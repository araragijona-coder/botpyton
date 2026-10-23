import contextlib
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from bot.architect_spool import ArchitectSpoolError, _response_path, run_order_spool
from bot.architect_order_contract import validate_architect_order
from bot.architect_order_response import ArchitectResponseWriter, build_architect_response
from bot.memory import JsonMemory
from bot.queue import TaskQueue
from bot.main import main


def packet(task_id="SPOOL-TASK-001", *, actions=None, status="ready", questions=None):
    chosen_actions = actions or ["validate_task", "prepare_report"]
    conditions = ["task_validated", "report_created"]
    if "run_tests" in chosen_actions:
        conditions.append("tests_passed")
    return {
        "schema_version": 1,
        "order_id": "SPOOL-ORDER-001",
        "source": {"agent_id": "external-architect", "plan_id": "SPOOL-PLAN-001"},
        "summary": "Inspect one selected project and return verifiable evidence.",
        "status": status,
        "tasks": [{
            "id": task_id,
            "goal": "Validate a bounded task and prepare a report.",
            "depends_on": [],
            "allowed_actions": chosen_actions,
            "success_conditions": conditions,
            "max_attempts": 1,
            "verification_checks": [{"type": "python_syntax"}],
        }] if status == "ready" else [],
        "questions": questions or [],
    }


class ArchitectSpoolTests(unittest.TestCase):
    def invoke(self, inbox, outbox, *extra):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            code = main([
                "--architect-inbox", str(inbox),
                "--architect-outbox", str(outbox),
                "--project-root", str(inbox),
                "--memory", str(outbox / "queue.json"),
                *extra,
            ])
        return code, json.loads(output.getvalue())

    def test_processes_only_one_order_and_skips_its_immutable_response(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            inbox, outbox = root / "inbox", root / "outbox"
            inbox.mkdir()
            outbox.mkdir()
            (inbox / "01.json").write_text(json.dumps(packet("SPOOL-TASK-001")), encoding="utf-8")
            (inbox / "02.json").write_text(json.dumps(packet("SPOOL-TASK-002")), encoding="utf-8")

            code, first = self.invoke(inbox, outbox)
            self.assertEqual(code, 0, first)
            self.assertEqual(first["spool_status"], "processed")
            self.assertEqual(first["input_file"], "01.json")
            self.assertEqual(first["status"], "validated_for_review")
            self.assertTrue((outbox / first["response_file"]).is_file())
            self.assertFalse((outbox / "queue.json").exists())

            code, second = self.invoke(inbox, outbox)
            self.assertEqual(code, 0, second)
            self.assertEqual(second["spool_status"], "processed")
            self.assertEqual(second["input_file"], "02.json")

            response_files_before = sorted(p.name for p in outbox.glob("*.response.json"))
            code, third = self.invoke(inbox, outbox)
            self.assertEqual(code, 0, third)
            self.assertEqual(third["spool_status"], "idle")
            self.assertEqual(third["already_responded_skipped"], ["01.json", "02.json"])
            self.assertEqual(sorted(p.name for p in outbox.glob("*.response.json")), response_files_before)

    def test_invalid_json_is_returned_as_durable_rejection_not_executed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            inbox, outbox = root / "inbox", root / "outbox"
            inbox.mkdir()
            outbox.mkdir()
            (inbox / "broken.json").write_text('{"schema_version": 1, "shell": "whoami"', encoding="utf-8")
            code, result = self.invoke(inbox, outbox)
            self.assertEqual(code, 2, result)
            self.assertEqual(result["status"], "rejected_invalid_order")
            self.assertFalse(result["response"]["execution_started"])
            self.assertFalse(result["response"]["queue_mutated"])
            self.assertEqual(result["response"]["next_action"]["action"], "repair_order_packet")
            saved = json.loads((outbox / result["response_file"]).read_text(encoding="utf-8"))
            self.assertEqual(saved["request_sha256"], result["request_sha256"])
            self.assertEqual(saved["status"], "rejected_invalid_order")

    def test_required_authorization_creates_reviewable_blocker_then_new_profile_is_eligible(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            inbox, outbox = root / "inbox", root / "outbox"
            inbox.mkdir()
            outbox.mkdir()
            (inbox / "run-tests.json").write_text(json.dumps(packet(actions=["validate_task", "run_tests"])), encoding="utf-8")

            code, blocked = self.invoke(inbox, outbox, "--execute-architect-order")
            self.assertEqual(code, 2, blocked)
            self.assertEqual(blocked["status"], "authorization_required")
            self.assertFalse(blocked["response"]["execution_started"])
            self.assertFalse(blocked["response"]["queue_mutated"])
            self.assertIn("project_code_execution", blocked["response"]["missing_local_approvals"])

            # The profile now differs, so the previously blocked packet can be
            # deliberately re-considered after the operator grants this capability.
            def accepted_runner(snapshot, response_path, flags):
                raw = snapshot.read_bytes()
                order = validate_architect_order(json.loads(raw.decode("utf-8")))
                response = build_architect_response(
                    order, hashlib.sha256(raw).hexdigest(), status="succeeded",
                    execution_started=True, queue_mutated=True,
                    operator_flags=flags,
                    execution={
                        "status": "succeeded", "tasks": [{"id": "SPOOL-TASK-001", "status": "succeeded", "attempts": 1}],
                        "attempt_results": [], "attempts_executed": 1,
                    },
                )
                with ArchitectResponseWriter(response_path) as writer:
                    writer.write(response)
                return {"exit_code": 0, "stdout": "", "stderr": ""}

            permitted = run_order_spool(
                inbox_path=inbox, outbox_path=outbox,
                execute=True, authorize_local_writes=False, execute_project_code=True,
                project_root=str(inbox), memory_path=str(outbox / "queue.json"),
                check_timeout=10, run_order=accepted_runner,
            )
            self.assertEqual(permitted["spool_status"], "processed")
            self.assertNotEqual(blocked["response_file"], permitted["response_file"])
            self.assertEqual(permitted["status"], "succeeded")

    def test_interrupted_execution_creates_recovery_evidence_without_replaying(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            inbox, outbox = root / "inbox", root / "outbox"
            inbox.mkdir()
            outbox.mkdir()
            packet_path = inbox / "interrupted.json"
            raw = json.dumps(packet(), indent=2).encode("utf-8")
            packet_path.write_bytes(raw)
            request_hash = hashlib.sha256(raw).hexdigest()
            flags = {
                "execute_architect_order": True,
                "authorize_local_writes": False,
                "execute_project_code": False,
            }
            order = validate_architect_order(json.loads(raw.decode("utf-8")))
            existing_response_path = _response_path(outbox, request_hash, "execute-w0-c0")
            pending = build_architect_response(
                order, request_hash, status="execution_in_progress",
                execution_started=True, queue_mutated=None, operator_flags=flags,
            )
            pending["validation"] = {
                key: value for key, value in order.items() if key != "normalized_tasks"
            }
            pending["summary"] = order["summary"]
            with ArchitectResponseWriter(existing_response_path) as writer:
                writer.write(pending)

            queue_path = outbox / "queue.json"
            queue = TaskQueue(JsonMemory(queue_path))
            queue.enqueue_many(order["normalized_tasks"])
            queue.claim_next(allowed_task_ids=set(order["task_ids"]))

            def no_replay(*_args):
                self.fail("an interrupted execution must never be replayed automatically")

            result = run_order_spool(
                inbox_path=inbox, outbox_path=outbox,
                execute=True, authorize_local_writes=False, execute_project_code=False,
                project_root=str(inbox), memory_path=str(queue_path), check_timeout=10,
                run_order=no_replay,
            )
            self.assertEqual(result["spool_status"], "recovery_required")
            self.assertEqual(result["status"], "recovery_required")
            self.assertFalse(result["processed"])
            self.assertFalse(result["execution_replayed"])
            self.assertEqual(result["response"]["evidence"]["queue_snapshot"]["tasks"][0]["status"], "running")
            self.assertEqual(result["next_action"]["action"], "inspect_running_task")
            self.assertTrue((outbox / result["recovery_report_file"]).is_file())
            saved_original = json.loads(existing_response_path.read_text(encoding="utf-8"))
            self.assertEqual(saved_original["status"], "execution_in_progress")
            saved_recovery = json.loads((outbox / result["recovery_report_file"]).read_text(encoding="utf-8"))
            self.assertEqual(saved_recovery["status"], "recovery_required")
            self.assertEqual(saved_recovery["request_sha256"], request_hash)

            repeated = run_order_spool(
                inbox_path=inbox, outbox_path=outbox,
                execute=True, authorize_local_writes=False, execute_project_code=False,
                project_root=str(inbox), memory_path=str(queue_path), check_timeout=10,
                run_order=no_replay,
            )
            self.assertEqual(repeated["recovery_report_file"], result["recovery_report_file"])
            self.assertFalse(repeated["execution_replayed"])

    def test_interrupted_execution_with_completed_queue_tasks_still_requires_report_review(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            inbox, outbox = root / "inbox", root / "outbox"
            inbox.mkdir()
            outbox.mkdir()
            raw = json.dumps(packet(), indent=2).encode("utf-8")
            (inbox / "interrupted.json").write_bytes(raw)
            request_hash = hashlib.sha256(raw).hexdigest()
            flags = {
                "execute_architect_order": True,
                "authorize_local_writes": False,
                "execute_project_code": False,
            }
            order = validate_architect_order(json.loads(raw.decode("utf-8")))
            existing_response_path = _response_path(outbox, request_hash, "execute-w0-c0")
            pending = build_architect_response(
                order, request_hash, status="execution_in_progress",
                execution_started=True, queue_mutated=None, operator_flags=flags,
            )
            pending["validation"] = {
                key: value for key, value in order.items() if key != "normalized_tasks"
            }
            with ArchitectResponseWriter(existing_response_path) as writer:
                writer.write(pending)

            queue_path = outbox / "queue.json"
            queue = TaskQueue(JsonMemory(queue_path))
            queue.enqueue_many(order["normalized_tasks"])
            queue.claim_next(allowed_task_ids=set(order["task_ids"]))
            task_id = order["task_ids"][0]
            queue.complete(task_id, {"report_path": "verified-report.json", "verification": {"ok": True}})

            result = run_order_spool(
                inbox_path=inbox, outbox_path=outbox,
                execute=True, authorize_local_writes=False, execute_project_code=False,
                project_root=str(inbox), memory_path=str(queue_path), check_timeout=10,
                run_order=lambda *_: self.fail("completed queue work must not be replayed"),
            )
            self.assertEqual(result["status"], "recovery_required")
            self.assertEqual(
                result["response"]["next_action"]["action"],
                "verify_reports_and_close_interrupted_run",
            )
            record = result["response"]["evidence"]["queue_snapshot"]["tasks"][0]
            self.assertEqual(record["status"], "succeeded")
            self.assertEqual(record["report_path"], "verified-report.json")
    def test_direct_module_rejects_overlapping_directories(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            nested_outbox = root / "outbox"
            nested_outbox.mkdir()
            with self.assertRaisesRegex(ArchitectSpoolError, "must not contain one another"):
                run_order_spool(
                    inbox_path=root,
                    outbox_path=nested_outbox,
                    execute=False,
                    authorize_local_writes=False,
                    execute_project_code=False,
                    project_root=str(root),
                    memory_path=str(root / "state.json"),
                    check_timeout=10,
                    run_order=lambda *_: self.fail("runner must not be called"),
                )

    def test_only_json_inputs_are_considered(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            inbox, outbox = root / "inbox", root / "outbox"
            inbox.mkdir()
            outbox.mkdir()
            (inbox / "not-an-order.txt").write_text(json.dumps(packet()), encoding="utf-8")
            code, result = self.invoke(inbox, outbox)
            self.assertEqual(code, 0, result)
            self.assertEqual(result["spool_status"], "idle")
            self.assertEqual(list(outbox.glob("*.response.json")), [])

    def test_extra_permission_flag_is_not_silently_carried_into_review_mode(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            inbox, outbox = root / "inbox", root / "outbox"
            inbox.mkdir()
            outbox.mkdir()
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                code = main([
                    "--architect-inbox", str(inbox),
                    "--architect-outbox", str(outbox),
                    "--authorize-local-writes",
                ])
            self.assertEqual(code, 2)


    def test_recovery_uses_original_order_not_tampered_response_task_ids(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            inbox, outbox = root / "inbox", root / "outbox"
            inbox.mkdir()
            outbox.mkdir()
            raw = json.dumps(packet(), indent=2).encode("utf-8")
            (inbox / "interrupted.json").write_bytes(raw)
            request_hash = hashlib.sha256(raw).hexdigest()
            order = validate_architect_order(json.loads(raw.decode("utf-8")))
            queue_path = outbox / "queue.json"
            queue = TaskQueue(JsonMemory(queue_path))
            queue.enqueue_many(order["normalized_tasks"])

            flags = {
                "execute_architect_order": True,
                "authorize_local_writes": False,
                "execute_project_code": False,
            }
            response_path = _response_path(outbox, request_hash, "execute-w0-c0")
            pending = build_architect_response(
                order, request_hash, status="execution_in_progress",
                execution_started=True, queue_mutated=None, operator_flags=flags,
            )
            pending["validation"] = {
                key: value for key, value in order.items() if key != "normalized_tasks"
            }
            pending["validation"]["task_ids"] = ["FORGED-TASK-ID"]
            pending["queue_mutated"] = True
            pending["summary"] = order["summary"]
            with ArchitectResponseWriter(response_path) as writer:
                writer.write(pending)

            def no_replay(*_args):
                self.fail("response-integrity mismatch must not replay project tasks")

            result = run_order_spool(
                inbox_path=inbox, outbox_path=outbox,
                execute=True, authorize_local_writes=False, execute_project_code=False,
                project_root=str(inbox), memory_path=str(queue_path), check_timeout=10,
                run_order=no_replay,
            )
            self.assertEqual(result["status"], "recovery_required")
            self.assertFalse(result["execution_replayed"])
            response = result["response"]
            evidence = response["evidence"]
            self.assertEqual(evidence["response_binding_status"], "mismatch")
            self.assertIn("validation.task_ids", evidence["response_binding_mismatches"])
            self.assertIn("queue_mutated", evidence["response_binding_mismatches"])
            self.assertIsNone(response["queue_mutated"])
            self.assertEqual(evidence["task_ids_from_order"], ["SPOOL-TASK-001"])
            self.assertEqual(evidence["queue_snapshot"]["tasks"][0]["task_id"], "SPOOL-TASK-001")
            self.assertEqual(evidence["queue_snapshot"]["tasks"][0]["status"], "pending")
            self.assertEqual(response["next_action"]["action"], "inspect_response_integrity")
            saved_original = json.loads(response_path.read_text(encoding="utf-8"))
            self.assertEqual(saved_original["validation"]["task_ids"], ["FORGED-TASK-ID"])


if __name__ == "__main__":
    unittest.main()

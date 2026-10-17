"""Adversarial tests for the simulation-only I-01 Design A prototype."""
import copy
import unittest

from bot.i01_emitter_simulation import (
    EmitterPolicy,
    EmitterRequestError,
    SimulationOnlyEmitter,
    SimulationRecorder,
)


VALID_SHA = "a" * 40
POLICY = EmitterPolicy(
    repository="araragijona-coder/botpyton",
    authorized_actors=frozenset({"trusted-worker"}),
    allowed_base_refs=frozenset({"main"}),
    allowed_head_prefixes=("bot-bootstrap/",),
)


def request(**overrides):
    value = {
        "schema_version": 1,
        "request_id": "req-test-001",
        "task_id": "task-test-001",
        "repository": "araragijona-coder/botpyton",
        "base_ref": "main",
        "head_ref": "bot-bootstrap/example",
        "head_sha": VALID_SHA,
        "title": "Example candidate",
        "body": "Review-only simulation request.",
        "draft": True,
    }
    value.update(overrides)
    return value


class SimulationEmitterTests(unittest.TestCase):
    def setUp(self):
        self.recorder = SimulationRecorder()
        self.evidence_actor = "trusted-worker"
        self.evidence_sha = VALID_SHA
        self.emitter = SimulationOnlyEmitter(POLICY, self.resolve_evidence, self.recorder)

    def resolve_evidence(self, repository, head_ref):
        # Test fixture standing in for a separately trusted identity/ref resolver.
        # It deliberately ignores any actor/SHA claims inside the request payload.
        return self.evidence_actor, self.evidence_sha

    def assert_rejected_without_operations(self, payload, actor="trusted-worker", resolved_sha=VALID_SHA):
        before = list(self.recorder.operations)
        self.evidence_actor = actor
        self.evidence_sha = resolved_sha
        with self.assertRaises(EmitterRequestError):
            self.emitter.process(payload)
        self.assertEqual(self.recorder.operations, before)

    def test_valid_request_records_simulation_not_live_write(self):
        result = self.emitter.process(request())
        self.assertEqual(result["mode"], "simulation_only")
        self.assertFalse(result["live_write_performed"])
        self.assertEqual(len(self.recorder.operations), 1)

    def test_unauthorized_identity_is_rejected_before_recording(self):
        self.assert_rejected_without_operations(request(), actor="untrusted-branch")

    def test_non_object_is_rejected_before_recording(self):
        self.assert_rejected_without_operations(["not", "an", "object"])

    def test_unknown_field_is_rejected(self):
        payload = request()
        payload["execute"] = True
        self.assert_rejected_without_operations(payload)

    def test_missing_field_is_rejected(self):
        payload = request()
        del payload["head_sha"]
        self.assert_rejected_without_operations(payload)

    def test_wrong_repository_is_rejected(self):
        self.assert_rejected_without_operations(request(repository="attacker/other"))

    def test_unapproved_base_or_head_ref_is_rejected(self):
        self.assert_rejected_without_operations(request(base_ref="develop"))
        self.assert_rejected_without_operations(request(head_ref="main"))
        self.assert_rejected_without_operations(request(head_ref="attacker/branch"))

    def test_malformed_refs_are_rejected(self):
        for ref in ("bot-bootstrap/../main", "bot-bootstrap//evil", "bot-bootstrap/x.lock", "bot-bootstrap/a@{b}"):
            with self.subTest(ref=ref):
                self.assert_rejected_without_operations(request(head_ref=ref))

    def test_sha_mismatch_is_rejected(self):
        self.assert_rejected_without_operations(request(), resolved_sha="b" * 40)

    def test_short_or_uppercase_sha_is_rejected(self):
        self.assert_rejected_without_operations(request(head_sha="a" * 39))
        self.assert_rejected_without_operations(request(head_sha="A" * 40))

    def test_non_draft_is_rejected(self):
        self.assert_rejected_without_operations(request(draft=False))
        self.assert_rejected_without_operations(request(draft=1))

    def test_invalid_text_and_size_limits_are_rejected(self):
        self.assert_rejected_without_operations(request(title="  "))
        self.assert_rejected_without_operations(request(title="x" * 201))
        self.assert_rejected_without_operations(request(body="x" * 20_001))
        self.assert_rejected_without_operations(request(body={"command": "run"}))

    def test_wrong_schema_version_is_rejected(self):
        self.assert_rejected_without_operations(request(schema_version=True))
        self.assert_rejected_without_operations(request(schema_version=2))

    def test_request_object_is_not_mutated(self):
        payload = request()
        original = copy.deepcopy(payload)
        self.emitter.process(payload)
        self.assertEqual(payload, original)

    def test_duplicate_request_id_is_rejected_without_second_operation(self):
        self.emitter.process(request())
        self.assert_rejected_without_operations(request())

    def test_rejections_never_increment_simulated_operation_count(self):
        invalid_requests = [
            request(draft=False),
            request(repository="attacker/repo"),
            request(head_sha="b" * 40),
            request(extra="unexpected"),
        ]
        for payload in invalid_requests:
            with self.subTest(payload=payload):
                before = len(self.recorder.operations)
                self.assert_rejected_without_operations(payload)
                self.assertEqual(len(self.recorder.operations), before)

    def test_recorder_has_no_live_github_transport_or_token_surface(self):
        self.assertFalse(hasattr(self.recorder, "request"))
        self.assertFalse(hasattr(self.recorder, "session"))
        self.assertFalse(hasattr(self.recorder, "token"))
        self.assertFalse(hasattr(self.recorder, "submit_pull_request"))



    def test_invalid_actor_types_fail_closed_without_type_error(self):
        for actor in (None, 7, [], {}, "   "):
            with self.subTest(actor=actor):
                self.assert_rejected_without_operations(request(), actor=actor)

    def test_request_ids_reject_empty_whitespace_and_malformed_values(self):
        for request_id in ("", "   ", "../bad", "bad/id", "x" * 81):
            with self.subTest(request_id=request_id):
                self.assert_rejected_without_operations(request(request_id=request_id))

    def test_policy_rejects_non_integer_text_limits(self):
        for kwargs in (
            {"max_title_chars": True},
            {"max_body_chars": False},
            {"max_title_chars": 201},
            {"max_body_chars": 20_001},
            {"max_title_chars": 0},
            {"max_body_chars": -1},
        ):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(ValueError):
                    EmitterPolicy(
                        repository="araragijona-coder/botpyton",
                        authorized_actors=frozenset({"trusted-worker"}),
                        allowed_base_refs=frozenset({"main"}),
                        allowed_head_prefixes=("bot-bootstrap/",),
                        **kwargs,
                    )

    def test_ref_validation_rejects_control_characters_and_excessive_length(self):
        for ref in ("bot-bootstrap/bad\\nname", "bot-bootstrap/" + "x" * 201, "bot-bootstrap/name.", "bot-bootstrap/.hidden"):
            with self.subTest(ref=ref):
                self.assert_rejected_without_operations(request(head_ref=ref))

    def test_duplicate_request_id_with_different_payload_is_rejected(self):
        self.emitter.process(request())
        changed = request(title="Different payload using the same ID")
        self.assert_rejected_without_operations(changed)
        self.assertEqual(len(self.recorder.operations), 1)


    def test_policy_rejects_empty_or_malformed_allowlists(self):
        invalid_policies = [
            {"authorized_actors": frozenset()},
            {"authorized_actors": frozenset({"  "})},
            {"authorized_actors": frozenset({7})},
            {"allowed_base_refs": frozenset()},
            {"allowed_base_refs": frozenset({"bad//ref"})},
            {"allowed_base_refs": frozenset({"../main"})},
            {"allowed_head_prefixes": ()},
            {"allowed_head_prefixes": ("bot-bootstrap",)},
            {"allowed_head_prefixes": ("../",)},
        ]
        for overrides in invalid_policies:
            with self.subTest(overrides=overrides):
                kwargs = {
                    "repository": "araragijona-coder/botpyton",
                    "authorized_actors": frozenset({"trusted-worker"}),
                    "allowed_base_refs": frozenset({"main"}),
                    "allowed_head_prefixes": ("bot-bootstrap/",),
                }
                kwargs.update(overrides)
                with self.assertRaises(ValueError):
                    EmitterPolicy(**kwargs)

    def test_returned_operation_cannot_mutate_recorded_audit_entry(self):
        result = self.emitter.process(request())
        result["live_write_performed"] = True
        result["repository"] = "attacker/repo"
        self.assertFalse(self.recorder.operations[0]["live_write_performed"])
        self.assertEqual(
            self.recorder.operations[0]["repository"], "araragijona-coder/botpyton"
        )


    def test_worker_cannot_supply_identity_or_sha_as_per_request_trusted_facts(self):
        payload = request()
        before = list(self.recorder.operations)
        with self.assertRaises(TypeError):
            self.emitter.process(
                payload, authenticated_actor="trusted-worker", resolved_head_sha=VALID_SHA
            )
        self.assertEqual(self.recorder.operations, before)

    def test_identity_and_sha_claim_fields_in_payload_are_rejected(self):
        for claimed_field, claimed_value in (
            ("authenticated_actor", "trusted-worker"),
            ("verified_actor", "trusted-worker"),
            ("resolved_head_sha", VALID_SHA),
            ("verified_head_sha", VALID_SHA),
        ):
            with self.subTest(claimed_field=claimed_field):
                self.assert_rejected_without_operations(
                    request(**{claimed_field: claimed_value})
                )

    def test_request_sha_must_match_sha_from_independent_resolver(self):
        self.evidence_sha = "b" * 40
        self.assert_rejected_without_operations(request(), resolved_sha="b" * 40)

    def test_unavailable_independent_evidence_fails_closed(self):
        def unavailable(repository, head_ref):
            raise RuntimeError("no trusted identity/ref source")
        emitter = SimulationOnlyEmitter(POLICY, unavailable, self.recorder)
        before = list(self.recorder.operations)
        with self.assertRaisesRegex(EmitterRequestError, "evidence unavailable"):
            emitter.process(request())
        self.assertEqual(self.recorder.operations, before)

    def test_simulation_module_has_no_network_process_or_filesystem_imports(self):
        import ast
        from pathlib import Path

        source = Path("bot/i01_emitter_simulation.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        forbidden_roots = {
            "socket", "subprocess", "requests", "httpx", "urllib", "http",
            "pathlib", "os", "shutil", "ftplib", "telnetlib",
        }
        imported_roots = set()
        forbidden_calls = {"eval", "exec", "open", "compile", "__import__"}
        called_names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_roots.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_roots.add(node.module.split(".")[0])
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                called_names.add(node.func.id)
        self.assertFalse(imported_roots & forbidden_roots)
        self.assertFalse(called_names & forbidden_calls)

if __name__ == "__main__":
    unittest.main()

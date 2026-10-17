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
        self.emitter = SimulationOnlyEmitter(POLICY, self.recorder)

    def assert_rejected_without_operations(self, payload, actor="trusted-worker", resolved_sha=VALID_SHA):
        before = list(self.recorder.operations)
        with self.assertRaises(EmitterRequestError):
            self.emitter.process(
                payload, authenticated_actor=actor, resolved_head_sha=resolved_sha
            )
        self.assertEqual(self.recorder.operations, before)

    def test_valid_request_records_simulation_not_live_write(self):
        result = self.emitter.process(
            request(), authenticated_actor="trusted-worker", resolved_head_sha=VALID_SHA
        )
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
        self.emitter.process(payload, authenticated_actor="trusted-worker", resolved_head_sha=VALID_SHA)
        self.assertEqual(payload, original)

    def test_duplicate_request_id_is_rejected_without_second_operation(self):
        self.emitter.process(request(), authenticated_actor="trusted-worker", resolved_head_sha=VALID_SHA)
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


if __name__ == "__main__":
    unittest.main()

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from bot.checkpoint import (
    MAX_CHECKPOINT_MANIFEST_BYTES,
    MAX_CHECKPOINT_STATE_BYTES,
    CheckpointError,
    export_checkpoint,
    restore_checkpoint,
)
from bot.memory import JsonMemory
from bot.queue import TaskQueue


def task(task_id="A", deps=None):
    return {
        "id": task_id,
        "goal": f"Test checkpoint task {task_id}",
        "depends_on": deps or [],
        "allowed_actions": ["validate_task"],
        "success_conditions": ["validation_passes"],
        "max_attempts": 3,
    }


class CheckpointTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source_memory = self.root / "source" / "state.json"
        self.restore_memory = self.root / "restored" / "state.json"
        self.checkpoint_dir = self.root / "artifact"

    def tearDown(self):
        self.temp.cleanup()

    def test_round_trip_preserves_queue_and_dependency_state(self):
        queue = TaskQueue(JsonMemory(self.source_memory))
        queue.enqueue_many([task("B", ["A"]), task("A")])
        first = queue.claim_next()
        self.assertEqual(first["id"], "A")
        queue.complete("A", {"ok": True, "checkpoint": 1})
        queue.claim_next()
        before = JsonMemory(self.source_memory).load()

        manifest = export_checkpoint(self.source_memory, self.checkpoint_dir)
        restored = restore_checkpoint(self.checkpoint_dir, self.restore_memory)
        after = JsonMemory(self.restore_memory).load()

        self.assertEqual(manifest["task_count"], 2)
        self.assertEqual(restored["task_count"], 2)
        self.assertEqual(before, after)
        self.assertEqual(after["tasks"]["A"]["status"], "succeeded")
        self.assertEqual(after["tasks"]["B"]["status"], "running")

    def test_tampered_state_is_rejected_without_overwriting_existing_memory(self):
        JsonMemory(self.source_memory).save({
            "schema_version": 1, "tasks": {}, "incidents": [], "solutions": []
        })
        export_checkpoint(self.source_memory, self.checkpoint_dir)
        Path(self.restore_memory).parent.mkdir(parents=True, exist_ok=True)
        existing = {"schema_version": 1, "tasks": {}, "incidents": [{"keep": True}], "solutions": []}
        JsonMemory(self.restore_memory).save(existing)
        state_path = self.checkpoint_dir / "state.json"
        state_path.write_text(state_path.read_text(encoding="utf-8") + " ", encoding="utf-8")

        with self.assertRaisesRegex(CheckpointError, "SHA-256 mismatch"):
            restore_checkpoint(self.checkpoint_dir, self.restore_memory)
        self.assertEqual(JsonMemory(self.restore_memory).load(), existing)

    def test_rejects_wrong_manifest_task_count(self):
        queue = TaskQueue(JsonMemory(self.source_memory))
        queue.enqueue_many([task()])
        export_checkpoint(self.source_memory, self.checkpoint_dir)
        manifest_path = self.checkpoint_dir / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["task_count"] = 88
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaisesRegex(CheckpointError, "task count"):
            restore_checkpoint(self.checkpoint_dir, self.restore_memory)
        self.assertFalse(self.restore_memory.exists())

    def test_export_rejects_corrupt_idempotency_key(self):
        queue = TaskQueue(JsonMemory(self.source_memory))
        queue.enqueue_many([task()])
        state = JsonMemory(self.source_memory).load()
        state["tasks"]["A"]["idempotency_key"] = "0" * 64
        JsonMemory(self.source_memory).save(state)
        with self.assertRaisesRegex(CheckpointError, "Idempotency key mismatch"):
            export_checkpoint(self.source_memory, self.checkpoint_dir)

    def test_oversized_state_is_rejected_before_parsing_without_overwriting_memory(self):
        JsonMemory(self.source_memory).save({
            "schema_version": 1, "tasks": {}, "incidents": [], "solutions": []
        })
        export_checkpoint(self.source_memory, self.checkpoint_dir)
        existing = {
            "schema_version": 1, "tasks": {},
            "incidents": [{"keep": "existing state"}], "solutions": []
        }
        JsonMemory(self.restore_memory).save(existing)
        (self.checkpoint_dir / "state.json").write_bytes(b" " * (MAX_CHECKPOINT_STATE_BYTES + 1))

        with self.assertRaisesRegex(CheckpointError, "state.json exceeds the configured size limit"):
            restore_checkpoint(self.checkpoint_dir, self.restore_memory)
        self.assertEqual(JsonMemory(self.restore_memory).load(), existing)

    def test_oversized_manifest_is_rejected_before_parsing_without_overwriting_memory(self):
        JsonMemory(self.source_memory).save({
            "schema_version": 1, "tasks": {}, "incidents": [], "solutions": []
        })
        export_checkpoint(self.source_memory, self.checkpoint_dir)
        existing = {
            "schema_version": 1, "tasks": {},
            "incidents": [{"keep": "existing state"}], "solutions": []
        }
        JsonMemory(self.restore_memory).save(existing)
        (self.checkpoint_dir / "manifest.json").write_bytes(
            b" " * (MAX_CHECKPOINT_MANIFEST_BYTES + 1)
        )

        with self.assertRaisesRegex(CheckpointError, "manifest.json exceeds the configured size limit"):
            restore_checkpoint(self.checkpoint_dir, self.restore_memory)
        self.assertEqual(JsonMemory(self.restore_memory).load(), existing)

    def test_deeply_nested_state_json_is_rejected_without_overwriting_memory(self):
        JsonMemory(self.source_memory).save({
            "schema_version": 1, "tasks": {}, "incidents": [], "solutions": []
        })
        manifest = export_checkpoint(self.source_memory, self.checkpoint_dir)
        existing = {
            "schema_version": 1, "tasks": {},
            "incidents": [{"keep": "existing state"}], "solutions": []
        }
        JsonMemory(self.restore_memory).save(existing)

        nested = "[" * 1500 + "0" + "]" * 1500
        state_text = (
            '{"schema_version":1,"tasks":{},"incidents":['
            + nested
            + '],"solutions":[]}\n'
        )
        (self.checkpoint_dir / "state.json").write_text(state_text, encoding="utf-8")
        manifest["sha256"] = hashlib.sha256(state_text.encode("utf-8")).hexdigest()
        (self.checkpoint_dir / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        with self.assertRaisesRegex(CheckpointError, "too deeply nested JSON"):
            restore_checkpoint(self.checkpoint_dir, self.restore_memory)
        self.assertEqual(JsonMemory(self.restore_memory).load(), existing)

    def test_symlink_checkpoint_state_is_rejected(self):
        JsonMemory(self.source_memory).save({
            "schema_version": 1, "tasks": {}, "incidents": [], "solutions": []
        })
        export_checkpoint(self.source_memory, self.checkpoint_dir)
        external = self.root / "external-state.json"
        external.write_text(
            json.dumps({"schema_version": 1, "tasks": {}, "incidents": [], "solutions": []}),
            encoding="utf-8",
        )
        state_path = self.checkpoint_dir / "state.json"
        state_path.unlink()
        try:
            state_path.symlink_to(external)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"File symlinks unavailable on this runner: {exc}")

        with self.assertRaisesRegex(CheckpointError, "state.json must be a regular file"):
            restore_checkpoint(self.checkpoint_dir, self.restore_memory)
        self.assertFalse(self.restore_memory.exists())

    def test_missing_manifest_fails_closed(self):
        self.checkpoint_dir.mkdir(parents=True, exist_ok=True)
        (self.checkpoint_dir / "state.json").write_text(
            json.dumps({"schema_version": 1, "tasks": {}, "incidents": [], "solutions": []}),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(CheckpointError, "complete checkpoint"):
            restore_checkpoint(self.checkpoint_dir, self.restore_memory)


if __name__ == "__main__":
    unittest.main()

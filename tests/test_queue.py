import multiprocessing
import tempfile
import time
import unittest
from pathlib import Path

from bot.memory import JsonMemory
from bot.queue import QueueError, TaskQueue


class _SlowJsonMemory(JsonMemory):
    """Expose a deterministic read/claim race window for process-level regression tests."""

    def load(self):
        state = super().load()
        if self.path.exists():
            time.sleep(0.25)
        return state


def _claim_worker(memory_path, start_event, results):
    start_event.wait(10)
    record = TaskQueue(_SlowJsonMemory(memory_path)).claim_next()
    results.put(record["id"] if record else None)


def task(task_id, deps=None, attempts=2):
    return {
        "id": task_id,
        "goal": f"Execute {task_id}",
        "depends_on": deps or [],
        "allowed_actions": ["validate_task"],
        "success_conditions": ["validation_passes"],
        "max_attempts": attempts,
    }


class PersistentQueueTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.queue = TaskQueue(JsonMemory(Path(self.temp.name) / "state.json"))

    def tearDown(self):
        self.temp.cleanup()

    def test_enqueue_is_idempotent_across_reloads(self):
        first = self.queue.enqueue_many([task("A")])[0]
        second = TaskQueue(JsonMemory(self.queue.memory.path)).enqueue_many([task("A")])[0]
        self.assertEqual(first["idempotency_key"], second["idempotency_key"])
        self.assertEqual(len(self.queue.list_tasks()), 1)

    def test_two_processes_cannot_claim_the_same_task(self):
        self.queue.enqueue_many([task("A")])
        context = multiprocessing.get_context("spawn")
        start_event = context.Event()
        results = context.Queue()
        workers = [
            context.Process(
                target=_claim_worker,
                args=(str(self.queue.memory.path), start_event, results),
            )
            for _ in range(2)
        ]
        try:
            for worker in workers:
                worker.start()
            start_event.set()
            for worker in workers:
                worker.join(15)
            self.assertTrue(
                all(not worker.is_alive() for worker in workers),
                "claim worker timed out, possibly while waiting for a state lock",
            )
            self.assertEqual([worker.exitcode for worker in workers], [0, 0])
            outcomes = [results.get(timeout=3) for _ in workers]
            self.assertEqual(outcomes.count("A"), 1, outcomes)
            self.assertEqual(outcomes.count(None), 1, outcomes)
            record = self.queue.get("A")
            self.assertEqual(record["status"], "running")
            self.assertEqual(record["attempts"], 1)
        finally:
            for worker in workers:
                if worker.is_alive():
                    worker.terminate()
                    worker.join(2)
            results.close()
            results.join_thread()

    def test_same_id_different_payload_is_rejected_without_mutation(self):
        self.queue.enqueue_many([task("A")])
        with self.assertRaises(QueueError):
            self.queue.enqueue_many([task("A", attempts=1)])
        self.assertEqual(self.queue.get("A")["payload"]["max_attempts"], 2)

    def test_dependencies_gate_claim_and_unblock_on_success(self):
        self.queue.enqueue_many([task("B", ["A"]), task("A")])
        claimed = self.queue.claim_next()
        self.assertEqual(claimed["id"], "A")
        self.assertIsNone(self.queue.claim_next())
        self.assertEqual(self.queue.get("B")["status"], "blocked")
        self.queue.complete("A", {"ok": True})
        next_task = self.queue.claim_next()
        self.assertEqual(next_task["id"], "B")

    def test_failed_dependency_blocks_downstream(self):
        self.queue.enqueue_many([task("A"), task("B", ["A"])])
        self.queue.claim_next()
        self.queue.fail("A", "known test failure")
        self.assertEqual(self.queue.get("B")["status"], "blocked")
        self.assertIsNone(self.queue.claim_next())

    def test_retryable_failure_returns_task_to_pending(self):
        self.queue.enqueue_many([task("A", attempts=2)])
        self.queue.claim_next()
        self.assertEqual(self.queue.fail("A", "temporary", retryable=True)["status"], "pending")
        self.assertEqual(self.queue.claim_next()["attempts"], 2)

    def test_resume_interrupted_running_task(self):
        self.queue.enqueue_many([task("A", attempts=3)])
        self.queue.claim_next()
        resumed = TaskQueue(JsonMemory(self.queue.memory.path)).resume_interrupted()
        self.assertEqual(resumed, ["A"])
        self.assertEqual(self.queue.get("A")["status"], "pending")
        self.assertEqual(self.queue.get("A")["last_error"], "interrupted_previous_run")

    def test_interrupted_task_at_attempt_limit_fails_closed(self):
        self.queue.enqueue_many([task("A", attempts=1)])
        self.queue.claim_next()
        self.assertEqual(self.queue.resume_interrupted(), [])
        record = self.queue.get("A")
        self.assertEqual(record["status"], "failed")
        self.assertEqual(record["last_error"], "interrupted_retry_limit_reached")

    def test_completion_is_idempotent_only_for_same_result(self):
        self.queue.enqueue_many([task("A")])
        self.queue.claim_next()
        self.queue.complete("A", {"ok": True})
        self.queue.complete("A", {"ok": True})
        with self.assertRaises(QueueError):
            self.queue.complete("A", {"ok": False})

    def test_cannot_complete_task_that_was_not_claimed(self):
        self.queue.enqueue_many([task("A")])
        with self.assertRaises(QueueError):
            self.queue.complete("A")


if __name__ == "__main__":
    unittest.main()

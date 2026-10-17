"""Tests for fail-closed PC-care result classification and postcondition checks."""
import unittest

from bot.operation_guard import assess_operation_result


class OperationGuardTests(unittest.TestCase):
    def test_complete_inventory_is_reported_success(self):
        result = assess_operation_result({
            "status": "collected", "items": [{"name": "python.exe"}],
            "returned_count": 1,
        })
        self.assertEqual(result["outcome"], "reported_success")
        self.assertEqual(result["exit_code"], 0)
        self.assertIn("returned_count_matches_items:pass", result["checks"])

    def test_partial_scan_never_returns_success_exit_code(self):
        result = assess_operation_result({
            "status": "scan_complete", "scan_limited": True,
            "access_errors": 0, "items": [],
        })
        self.assertEqual(result["outcome"], "partial")
        self.assertEqual(result["exit_code"], 2)
        self.assertFalse(result["retry_recommended"])

    def test_benchmark_with_partial_submeasurements_is_partial_not_unknown(self):
        result = assess_operation_result({
            "status": "partial_measurement",
            "measurements": {"operations": [
                {"name": "app_inventory", "status": "partial_limit"},
                {"name": "cleanup_preview", "status": "preview_partial"},
            ]},
        })
        self.assertEqual(result["outcome"], "partial")
        self.assertEqual(result["exit_code"], 2)
        self.assertFalse(result["retry_recommended"])

    def test_provider_failure_never_returns_success(self):
        result = assess_operation_result({"status": "completed", "return_code": 1})
        self.assertEqual(result["outcome"], "failed")
        self.assertEqual(result["exit_code"], 1)

    def test_count_mismatch_is_a_postcondition_failure(self):
        result = assess_operation_result({
            "status": "collected", "items": [{"name": "one"}],
            "returned_count": 2,
        })
        self.assertEqual(result["outcome"], "failed")
        self.assertEqual(result["exit_code"], 1)
        self.assertIn("does not match", result["next_action"])

    def test_integrity_error_is_not_success_even_if_status_says_collected(self):
        result = assess_operation_result({
            "status": "collected", "integrity_status": "error", "items": [],
        })
        self.assertEqual(result["outcome"], "failed")
        self.assertEqual(result["exit_code"], 1)

    def test_unknown_status_fails_closed_instead_of_assuming_success(self):
        result = assess_operation_result({"status": "new_status_from_future"})
        self.assertEqual(result["outcome"], "unknown")
        self.assertEqual(result["exit_code"], 3)

    def test_malformed_report_fails_closed(self):
        result = assess_operation_result(["not", "an", "object"])
        self.assertEqual(result["outcome"], "unknown")
        self.assertEqual(result["exit_code"], 3)


if __name__ == "__main__":
    unittest.main()

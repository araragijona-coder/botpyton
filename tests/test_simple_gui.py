import unittest
from types import SimpleNamespace
from unittest.mock import patch

from bot.simple_gui import ANALYSIS_STEPS, SimpleBotPytonApp, format_bytes, report_status, summarize_report


class SimpleDashboardTests(unittest.TestCase):
    def test_full_scan_is_sequential_and_contains_eight_plain_language_stages(self):
        self.assertEqual(len(ANALYSIS_STEPS), 8)
        self.assertTrue(all(label and argv for label, argv in ANALYSIS_STEPS))
        labels = " ".join(label for label, _argv in ANALYSIS_STEPS).casefold()
        self.assertIn("memoria", labels)
        self.assertIn("discos", labels)
        self.assertIn("temporales", labels)

    def test_format_bytes_uses_readable_units(self):
        self.assertEqual(format_bytes(0), "0 B")
        self.assertEqual(format_bytes(1536), "1.5 KB")
        self.assertEqual(format_bytes(None), "No disponible")

    def test_partial_report_is_flagged_as_not_complete(self):
        state, category = report_status({"status": "scan_partial"}, 2)
        self.assertEqual(state, "Revisión parcial")
        self.assertEqual(category, "warning")

    def test_summary_uses_plain_labels_for_common_metrics(self):
        output = summarize_report("Memoria y procesos", {
            "status": "collected",
            "available_memory_mb": 1536,
            "items_count": 4,
        })
        self.assertIn("RAM disponible: 1,536 MB", output)
        self.assertIn("Elementos detectados: 4", output)
        self.assertNotIn("available_memory_mb", output)

    def test_finding_summary_translates_internal_ids_and_does_not_leak_english_detail(self):
        output = summarize_report("Estado general", {
            "status": "diagnostic_only",
            "system": {"available_memory_mb": 800, "disk": {"free_gib": 8.0, "free_percent": 8.0}},
            "findings": [{
                "id": "available_memory_low",
                "title": "Low available physical memory",
                "detail": "An internal technical explanation",
                "recommendation": "Inspect current heavy processes",
                "severity": "warning",
            }],
        })
        self.assertIn("Hay poca memoria disponible en este momento", output)
        self.assertNotIn("Low available physical memory", output)
        self.assertNotIn("Inspect current heavy processes", output)
        self.assertIn("RAM disponible: 800 MB", output)

    def test_critical_findings_override_a_nominal_report_status(self):
        state, category = report_status({
            "status": "collected",
            "findings": [{"id": "disk_issue", "severity": "critical"}],
        })
        self.assertEqual(state, "Necesita revisión")
        self.assertEqual(category, "error")

    def test_warning_findings_are_not_hidden_by_a_nominal_report_status(self):
        state, category = report_status({
            "status": "collected",
            "findings": [{"id": "memory_issue", "severity": "warning"}],
        })
        self.assertEqual(state, "Hay avisos para revisar")
        self.assertEqual(category, "warning")

    def test_nonzero_exit_code_overrides_a_nominal_report_status(self):
        state, category = report_status({"status": "collected"}, 1)
        self.assertEqual(state, "Necesita revisión")
        self.assertEqual(category, "error")

    def test_full_scan_records_an_exception_and_continues_remaining_checks(self):
        app = SimpleBotPytonApp.__new__(SimpleBotPytonApp)
        app.stage_label = SimpleNamespace(configure=lambda **kwargs: None)
        app._post = lambda callback: callback()
        captured = []
        app._finish_reports = lambda title, reports: captured.append((title, reports))
        calls = []

        def fake_call(argv):
            calls.append(argv)
            if len(calls) == 1:
                raise OSError("simulated diagnostic failure")
            return 0, '{"status":"collected"}'

        with patch("bot.simple_gui._call_main", side_effect=fake_call):
            app._full_scan_worker()

        self.assertEqual(len(calls), len(ANALYSIS_STEPS))
        self.assertEqual(len(captured), 1)
        title, reports = captured[0]
        self.assertEqual(title, "Análisis amplio del PC")
        self.assertEqual(len(reports), len(ANALYSIS_STEPS))
        self.assertEqual(reports[0][1], 1)
        self.assertEqual(reports[0][3]["status"], "error")
        self.assertEqual(reports[1][1], 0)


if __name__ == "__main__":
    unittest.main()

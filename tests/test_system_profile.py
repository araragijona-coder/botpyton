import unittest

from bot.system_profile import get_system_profile, recommended_child_memory_limit_mb


class SystemProfileTests(unittest.TestCase):
    def test_memory_budget_is_conservative_and_bounded(self):
        self.assertEqual(recommended_child_memory_limit_mb(2 * 1024 * 1024 * 1024), 768)
        self.assertEqual(recommended_child_memory_limit_mb(1024 * 1024 * 1024), 512)
        self.assertEqual(recommended_child_memory_limit_mb(64 * 1024 * 1024), 64)
        detected_budget = recommended_child_memory_limit_mb(None)
        self.assertGreaterEqual(detected_budget, 64)
        self.assertLessEqual(detected_budget, 768)

    def test_profile_reports_resources_without_network_or_ai(self):
        result = get_system_profile(".")
        self.assertTrue(result["python_executable"])
        self.assertGreaterEqual(result["recommended_child_memory_limit_mb"], 64)
        self.assertLessEqual(result["recommended_child_memory_limit_mb"], 768)
        self.assertFalse(result["ai_enabled"])
        self.assertFalse(result["network_used"])


if __name__ == "__main__":
    unittest.main()

import unittest

from bot.tool_registry import discover_tools


class ToolRegistryTests(unittest.TestCase):
    def test_empty_path_returns_no_tools_without_running_probes(self):
        result = discover_tools(path_value="", specs=())
        self.assertEqual(result["status"], "discovery_complete")
        self.assertEqual(result["tools_found"], 0)
        self.assertEqual(result["tools"], [])
        self.assertFalse(result["network_used"])
        self.assertFalse(result["ai_enabled"])

    def test_discovery_does_not_execute_probes(self):
        result = discover_tools()
        self.assertEqual(result["tools_found"], len(result["tools"]))
        self.assertTrue(all(item["probe_executed"] is False for item in result["tools"]))
        self.assertTrue(all(isinstance(item["resolved_path"], str) for item in result["tools"]))


if __name__ == "__main__":
    unittest.main()

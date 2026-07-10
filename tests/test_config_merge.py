"""MCP server merge into empty/existing configs + idempotency. Stdlib unittest."""
import copy
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from installer import (  # noqa: E402
    merge_mcp_servers,
    remove_mcp_servers,
    merge_env,
    remove_env,
)

XYLEM_ENTRIES = {
    "context-keeper": {"type": "stdio", "command": "python3", "args": ["ck.py"], "env": {}},
    "agentsync": {"type": "stdio", "command": "python3", "args": ["as.py"], "env": {}},
}


class ConfigMergeTest(unittest.TestCase):
    def test_merge_into_empty_config(self):
        settings = {}
        merge_mcp_servers(settings, XYLEM_ENTRIES)
        self.assertIn("mcpServers", settings)
        self.assertEqual(set(settings["mcpServers"]), {"context-keeper", "agentsync"})

    def test_merge_preserves_foreign_servers(self):
        settings = {"mcpServers": {"other": {"type": "stdio", "command": "node"}}}
        merge_mcp_servers(settings, XYLEM_ENTRIES)
        self.assertIn("other", settings["mcpServers"])
        self.assertEqual(settings["mcpServers"]["other"]["command"], "node")
        self.assertIn("context-keeper", settings["mcpServers"])

    def test_merge_preserves_unrelated_top_level_keys(self):
        settings = {"theme": "dark", "permissions": {"allow": ["x"]}}
        merge_mcp_servers(settings, XYLEM_ENTRIES)
        self.assertEqual(settings["theme"], "dark")
        self.assertEqual(settings["permissions"], {"allow": ["x"]})

    def test_merge_is_idempotent(self):
        settings = {"mcpServers": {"other": {"command": "node"}}}
        merge_mcp_servers(settings, XYLEM_ENTRIES)
        first = copy.deepcopy(settings)
        merge_mcp_servers(settings, XYLEM_ENTRIES)
        self.assertEqual(first, settings)

    def test_remove_only_touches_named_servers(self):
        settings = {"mcpServers": {"other": {"command": "node"}}}
        merge_mcp_servers(settings, XYLEM_ENTRIES)
        remove_mcp_servers(settings, ["context-keeper", "agentsync", "cambium"])
        self.assertEqual(set(settings["mcpServers"]), {"other"})

    def test_remove_drops_empty_mcp_section(self):
        settings = {}
        merge_mcp_servers(settings, XYLEM_ENTRIES)
        remove_mcp_servers(settings, ["context-keeper", "agentsync"])
        self.assertNotIn("mcpServers", settings)

    def test_env_merge_and_remove(self):
        settings = {"env": {"USER_VAR": "1"}}
        merge_env(settings, "XYLEM_CONTEXT_KEEPER_PATH", "/x/ck.py")
        self.assertEqual(settings["env"]["XYLEM_CONTEXT_KEEPER_PATH"], "/x/ck.py")
        self.assertEqual(settings["env"]["USER_VAR"], "1")
        remove_env(settings, "XYLEM_CONTEXT_KEEPER_PATH")
        self.assertNotIn("XYLEM_CONTEXT_KEEPER_PATH", settings["env"])
        self.assertIn("USER_VAR", settings["env"])

    def test_env_remove_drops_empty_section(self):
        settings = {}
        merge_env(settings, "XYLEM_CONTEXT_KEEPER_PATH", "/x/ck.py")
        remove_env(settings, "XYLEM_CONTEXT_KEEPER_PATH")
        self.assertNotIn("env", settings)


if __name__ == "__main__":
    unittest.main()

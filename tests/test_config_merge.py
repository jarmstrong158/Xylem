"""MCP server merge into empty/existing configs + idempotency. Stdlib unittest."""
import copy
import json
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
    build_settings_install,
    detect_json_indent,
    dump_json_text,
    redact,
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


HTTP_MANIFEST = {
    "version": 3,
    "servers": [
        {"name": "agentsync-remote", "transport": "http",
         "url_env_key": "XYLEM_TEST_REMOTE_URL"},
    ],
}


class StaleHttpEntryTest(unittest.TestCase):
    """An http entry holds the token in its URL. When the env var goes away
    (rotated/revoked), the previously-written entry must go with it."""

    def _install(self, settings):
        build_settings_install(
            settings, HTTP_MANIFEST, mapping={}, ck_server_path="/x/ck.py",
            cambium_server_path="/x/cambium.py", hook_command="h",
            version_check_command="v", distill_command="d",
            warn=lambda m: None)
        return settings

    def setUp(self):
        os.environ.pop("XYLEM_TEST_REMOTE_URL", None)
        self.addCleanup(os.environ.pop, "XYLEM_TEST_REMOTE_URL", None)

    def test_entry_written_when_url_is_set(self):
        os.environ["XYLEM_TEST_REMOTE_URL"] = "https://w.example/mcp/tok"
        settings = self._install({})
        self.assertEqual(settings["mcpServers"]["agentsync-remote"]["url"],
                         "https://w.example/mcp/tok")

    def test_stale_entry_is_removed_when_url_is_unset(self):
        os.environ["XYLEM_TEST_REMOTE_URL"] = "https://w.example/mcp/old"
        settings = self._install({})
        del os.environ["XYLEM_TEST_REMOTE_URL"]
        self._install(settings)
        self.assertNotIn("agentsync-remote", settings.get("mcpServers", {}))

    def test_removal_warns(self):
        os.environ["XYLEM_TEST_REMOTE_URL"] = "https://w.example/mcp/old"
        settings = self._install({})
        del os.environ["XYLEM_TEST_REMOTE_URL"]
        warnings = []
        build_settings_install(
            settings, HTTP_MANIFEST, mapping={}, ck_server_path="/x/ck.py",
            cambium_server_path="/x/cambium.py", hook_command="h",
            version_check_command="v", distill_command="d",
            warn=warnings.append)
        self.assertTrue(any("stale" in w for w in warnings))

    def test_foreign_server_survives_the_stale_sweep(self):
        settings = {"mcpServers": {"other": {"command": "node"}}}
        self._install(settings)
        self.assertIn("other", settings["mcpServers"])


class RedactionTest(unittest.TestCase):
    """The whole URL is the credential -- nothing URL-shaped reaches stdout."""

    def test_path_token_is_masked(self):
        out = redact("url: https://w.example.com/mcp/SECRETTOKEN")
        self.assertNotIn("SECRETTOKEN", out)
        self.assertIn("https://w.example.com/mcp/<redacted>", out)

    def test_any_url_shape_is_covered_not_just_mcp(self):
        out = redact('"url": "https://host.dev/some/other/path?k=v"')
        self.assertNotIn("other", out)
        self.assertNotIn("k=v", out)
        self.assertIn("<redacted>", out)

    def test_single_segment_url_is_fully_masked(self):
        self.assertEqual(redact("https://host/TOKEN"), "https://host/<redacted>")

    def test_bare_host_is_left_alone(self):
        self.assertEqual(redact("https://example.com"), "https://example.com")

    def test_non_url_text_untouched(self):
        self.assertEqual(redact("no secrets here"), "no secrets here")

    def test_redaction_applies_inside_a_json_diff_line(self):
        line = '+    "url": "https://w.example.com/mcp/abc123def",'
        self.assertNotIn("abc123def", redact(line))


class JsonStyleTest(unittest.TestCase):
    """Existing formatting is sniffed and preserved, not normalized to 2."""

    def test_detects_four_space_indent(self):
        text = json.dumps({"a": {"b": 1}}, indent=4)
        self.assertEqual(detect_json_indent(text), 4)

    def test_detects_two_space_indent(self):
        text = json.dumps({"a": {"b": 1}}, indent=2)
        self.assertEqual(detect_json_indent(text), 2)

    def test_detects_tab_indent(self):
        text = json.dumps({"a": {"b": 1}}, indent="\t")
        self.assertEqual(detect_json_indent(text), "\t")

    def test_missing_or_flat_defaults_to_two(self):
        self.assertEqual(detect_json_indent(""), 2)
        self.assertEqual(detect_json_indent('{"a": 1}'), 2)

    def test_round_trip_is_byte_stable(self):
        obj = {"theme": "dark", "mcpServers": {"x": {"command": "node"}}}
        original = json.dumps(obj, indent=4) + "\n"
        indent = detect_json_indent(original)
        self.assertEqual(dump_json_text(obj, indent), original)


if __name__ == "__main__":
    unittest.main()

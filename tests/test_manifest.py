"""Validate manifest.json structure and completeness. Stdlib unittest only."""
import json
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MANIFEST_PATH = os.path.join(ROOT, "manifest.json")

EXPECTED_SERVERS = {
    "context-keeper",
    "agentsync",
    "agentsync-remote",
    "cambium",
    "context-keeper-remote",
}


class ManifestTest(unittest.TestCase):
    def setUp(self):
        with open(MANIFEST_PATH, "r", encoding="utf-8") as fh:
            self.manifest = json.load(fh)
        self.servers = self.manifest["servers"]
        self.by_name = {s["name"]: s for s in self.servers}

    def test_parses_as_json(self):
        self.assertIsInstance(self.manifest, dict)
        self.assertIsInstance(self.servers, list)

    def test_all_five_servers_present(self):
        self.assertEqual(set(self.by_name), EXPECTED_SERVERS)
        self.assertEqual(len(self.servers), 5)

    def test_every_server_has_required_fields(self):
        for name, server in self.by_name.items():
            self.assertIn("name", server, name)
            self.assertIn("transport", server, name)
            self.assertIn(server["transport"], ("stdio", "http"), name)
            self.assertIn("available", server, name)
            self.assertIsInstance(server["available"], bool, name)

    def test_stdio_servers_have_command_and_args(self):
        for name, server in self.by_name.items():
            if server["transport"] != "stdio":
                continue
            self.assertEqual(server["command"], "python3", name)
            self.assertIsInstance(server["args"], list, name)
            self.assertTrue(server["args"], name)
            # Path is a placeholder the installer resolves, not a real path yet.
            # Sibling server repos live under $XYLEM_PARENT, not inside this repo.
            self.assertIn("$XYLEM_PARENT", server["args"][0], name)
            self.assertIsInstance(server.get("env", {}), dict, name)

    def test_http_servers_declare_url_env_key_not_command(self):
        for name, server in self.by_name.items():
            if server["transport"] != "http":
                continue
            self.assertIn("url_env_key", server, name)
            self.assertNotIn("command", server, name)
            self.assertNotIn("args", server, name)

    def test_no_hardcoded_urls_or_secrets(self):
        # Every http server must reference env keys, never literal URLs/tokens.
        blob = json.dumps(self.manifest)
        self.assertNotIn("http://", blob)
        self.assertNotIn("https://", blob.replace("https://xylem.local/manifest.schema.json", ""))

    def test_context_keeper_declares_hook_artifacts(self):
        ck = self.by_name["context-keeper"]
        self.assertIn("hooks/session_start", ck["artifacts"])
        self.assertIn("hooks/scope_guard", ck["artifacts"])

    def test_context_keeper_remote_available(self):
        # CK-remote is built and deployed (Cloudflare Worker + D1). It must be
        # available so the installer registers it whenever its URL env var is
        # set; the http layer still skips it gracefully if the env var is unset.
        self.assertTrue(self.by_name["context-keeper-remote"]["available"])

    def test_agentsync_branch_defaults_to_agentsync(self):
        self.assertEqual(self.by_name["agentsync"]["env"]["AGENTSYNC_BRANCH"], "agentsync")


if __name__ == "__main__":
    unittest.main()

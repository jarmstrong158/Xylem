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
            # $PYTHON, not a bare "python3": on Windows `python3` routinely
            # resolves to the Microsoft Store shim while the interpreter that
            # actually has `mcp` installed is `python`, so the servers got
            # registered into a config where they could never start. The
            # installer resolves this to sys.executable -- if you could run the
            # installer, the servers can run.
            self.assertEqual(server["command"], "$PYTHON", name)
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

    def test_http_servers_carry_no_auth_header(self):
        # Both remote Workers authenticate on the URL path token (/mcp/<token>)
        # only; they never read an Authorization header. A header block would
        # send a bearer the Worker ignores, so no http server declares one.
        for name, server in self.by_name.items():
            if server["transport"] != "http":
                continue
            self.assertNotIn("headers", server, name)
        # No Bearer-format token wiring anywhere in the manifest.
        self.assertNotIn("Bearer {value}", json.dumps(self.manifest))

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

    def test_stdio_servers_declare_a_fetchable_source(self):
        # Every stdio server must carry a `source` so a clean machine can clone
        # it. The repo is stored as an owner/name slug (never a literal URL) so
        # the no-hardcoded-URLs rule above still holds; the clone URL is built in
        # code from that slug.
        for name, server in self.by_name.items():
            if server["transport"] != "stdio":
                continue
            source = server.get("source")
            self.assertIsInstance(source, dict, name)
            self.assertTrue(source.get("repo"), name)
            self.assertTrue(source.get("dir"), name)
            self.assertNotIn("://", source["repo"], name)
            self.assertIn("ref", source, name)  # present as a pin hook

    def test_stdio_servers_are_pinned_to_a_release_tag(self):
        # Fresh installs must resolve a reproducible, tested-together snapshot
        # rather than whatever each server's main happens to be mid-refactor.
        # Bump these refs when the servers cut new releases.
        for name, server in self.by_name.items():
            if server["transport"] != "stdio":
                continue
            ref = server["source"]["ref"]
            self.assertIsInstance(ref, str, name)
            self.assertRegex(ref, r"^v\d+\.\d+", name)

    def test_source_dir_matches_the_registered_script_path(self):
        # Fetch clones into $XYLEM_PARENT/<source.dir>; registration resolves
        # $XYLEM_PARENT/<segment>/... from args[0]. If these disagree the
        # installer clones to one place and registers another. Guard the drift.
        for name, server in self.by_name.items():
            if server["transport"] != "stdio":
                continue
            arg = server["args"][0]
            segment = arg.split("$XYLEM_PARENT/", 1)[1].split("/", 1)[0]
            self.assertEqual(server["source"]["dir"], segment, name)


if __name__ == "__main__":
    unittest.main()

"""The generated artifacts must match their sources.

Three hand-maintained copies of the habit prose drifted in production: the
plugin primer silently lost the update_status cadence, the mailbox escalation
rule, and the "done means pushed to origin" rule. Separately plugin/.mcp.json
drifted from manifest.json on both the server name (`agent-sync-remote` vs
`agentsync-remote`, which broke every documented tool reference) and the auth
scheme (a Bearer header these Workers never read).

Both classes of bug are now structurally impossible -- the files are generated.
This module is what keeps them that way.
"""

import json
import os
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import render_discipline  # noqa: E402


class GeneratedFilesAreCurrent(unittest.TestCase):
    def test_every_generated_file_matches_its_source(self):
        stale = []
        for path, want in render_discipline.outputs():
            with open(path, "r", encoding="utf-8", newline="") as fh:
                if fh.read() != want:
                    stale.append(os.path.relpath(path, ROOT).replace(os.sep, "/"))
        self.assertEqual(
            stale,
            [],
            "stale generated files: %s\nfix: python scripts/render_discipline.py --write"
            % ", ".join(stale),
        )

    def test_check_mode_exits_zero_when_current(self):
        proc = subprocess.run(
            [sys.executable, os.path.join(ROOT, "scripts", "render_discipline.py")],
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_render_is_idempotent(self):
        first = {p: t for p, t in render_discipline.outputs()}
        second = {p: t for p, t in render_discipline.outputs()}
        self.assertEqual(first, second)


class PluginMcpMatchesManifest(unittest.TestCase):
    """The two drift bugs that actually shipped, asserted directly."""

    def setUp(self):
        with open(os.path.join(ROOT, "manifest.json"), encoding="utf-8") as fh:
            self.manifest = json.load(fh)
        with open(os.path.join(ROOT, "plugin", ".mcp.json"), encoding="utf-8") as fh:
            self.mcp = json.load(fh)

    def test_every_plugin_server_name_exists_in_the_manifest(self):
        known = {s["name"] for s in self.manifest["servers"]}
        for name in self.mcp["mcpServers"]:
            self.assertIn(
                name,
                known,
                "plugin/.mcp.json registers '%s', which manifest.json does not declare. "
                "Tool names are prefixed by server name, so this breaks every "
                "`mcp__<server>__<tool>` reference in the habit prose." % name,
            )

    def test_plugin_registers_exactly_the_available_http_servers(self):
        want = {
            s["name"]
            for s in self.manifest["servers"]
            if s.get("transport") == "http" and s.get("available", True)
        }
        self.assertEqual(set(self.mcp["mcpServers"]), want)

    def test_plugin_sends_no_authorization_header(self):
        # These Workers authenticate on the URL path (/mcp/<token>) and never
        # read an Authorization header -- manifest.json and
        # docs/design-principles.md both say so. A Bearer header here is inert,
        # and documenting a *_TOKEN env var for it misleads the user into
        # thinking they have configured auth when they have not.
        for name, entry in self.mcp["mcpServers"].items():
            self.assertNotIn(
                "headers",
                entry,
                "plugin server '%s' declares headers; these Workers use path-token "
                "auth only." % name,
            )

    def test_plugin_config_carries_no_literal_url_or_secret(self):
        raw = json.dumps(self.mcp)
        self.assertNotIn("http://", raw)
        # Only the ${ENV_VAR} placeholder form is allowed.
        for entry in self.mcp["mcpServers"].values():
            self.assertTrue(
                entry["url"].startswith("${") and entry["url"].endswith("}"),
                "url must be an env placeholder, got %r" % entry["url"],
            )


class DisciplineSourceCoversTheLoadBearingRules(unittest.TestCase):
    """Guard the specific rules the hand-copied plugin primer used to drop."""

    def setUp(self):
        self.rendered = {
            os.path.basename(p): t for p, t in render_discipline.outputs()
        }

    def test_every_rendered_prose_output_carries_every_rule(self):
        required = [
            ("update_status", "the milestone-cadence rule"),
            ("mailbox", "the raise-judgment-calls escalation rule"),
            ("PUSHED TO ORIGIN", "the definition-of-done rule"),
            ("recall()", "the compound-knowledge rule"),
        ]
        for name in ("claude_md_block.md", "xylem_discipline.md", "discipline.md"):
            for needle, why in required:
                self.assertIn(
                    needle,
                    self.rendered[name],
                    "%s is missing %s (%r)" % (name, why, needle),
                )

    def test_plugin_primer_is_ascii_only(self):
        # It is cat'd straight to a console that may be cp1252.
        text = self.rendered["discipline.md"]
        self.assertTrue(all(ord(c) <= 127 for c in text))

    def test_claude_md_block_is_fenced_and_version_stamped(self):
        block = self.rendered["claude_md_block.md"]
        with open(os.path.join(ROOT, "manifest.json"), encoding="utf-8") as fh:
            version = json.load(fh)["version"]
        self.assertTrue(block.startswith("<!-- XYLEM:BEGIN v%d -->" % version))
        self.assertTrue(block.rstrip().endswith("<!-- XYLEM:END -->"))


if __name__ == "__main__":
    unittest.main()

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


class InstallServersJsonMatchesManifest(unittest.TestCase):
    """install/servers.json was the drift dec-016 missed.

    It sat OUTSIDE the four generated outputs as a third hand-maintained copy of
    the server declarations, and it drifted exactly the way hand-copies do: long
    after both were fixed everywhere else it still declared `"command":
    "python3"` (the Microsoft Store shim on Windows -- dec-013) and still pinned
    CONTEXT_KEEPER_PROJECT / AGENTSYNC_REPO / CAMBIUM_REPO, which freezes each
    server to the install-time project.

    It is generated now. These are the specific regressions, asserted directly,
    so a hand-edit that reintroduces either one fails here as well as in the
    is-it-stale check above.
    """

    # The keys whose whole point is that they are NOT pinned. Each manifest
    # server carries a `note` explaining why.
    FORBIDDEN_PINS = ("CONTEXT_KEEPER_PROJECT", "AGENTSYNC_REPO", "CAMBIUM_REPO")

    def setUp(self):
        with open(os.path.join(ROOT, "manifest.json"), encoding="utf-8") as fh:
            self.manifest = json.load(fh)
        with open(os.path.join(ROOT, "install", "servers.json"), encoding="utf-8") as fh:
            self.servers = json.load(fh)
        self.by_name = {s["name"]: s for s in self.servers["servers"]}

    def test_it_is_one_of_the_generated_outputs(self):
        # The guard only guards what it renders. If someone drops servers.json
        # back out of outputs(), the drift is silently legal again.
        rendered = {os.path.abspath(p) for p, _ in render_discipline.outputs()}
        self.assertIn(os.path.join(ROOT, "install", "servers.json"), rendered)

    def test_declares_exactly_the_manifests_available_servers(self):
        want = {
            s["name"] for s in self.manifest["servers"] if s.get("available", True)
        }
        self.assertEqual(set(self.by_name), want)

    def test_no_stdio_server_launches_with_a_bare_python(self):
        # dec-013: `python3` on Windows is the Store shim, and the interpreter
        # that actually has `mcp` is `python`. A bare name here registers servers
        # into a config where they can never start, with no diagnostic.
        for name, server in self.by_name.items():
            if server["transport"] != "stdio":
                continue
            self.assertEqual(
                server["command"],
                "$PYTHON",
                "server '%s' launches with %r; it must carry the $PYTHON "
                "placeholder so xylem_interpreter resolves a real interpreter."
                % (name, server["command"]),
            )

    def test_no_server_pins_a_project_or_repo(self):
        for name, server in self.by_name.items():
            for key in server.get("env", {}):
                self.assertNotIn(
                    key,
                    self.FORBIDDEN_PINS,
                    "server '%s' pins %s. That freezes it to the install-time "
                    "project; every one of these servers resolves its project "
                    "per call instead (see the manifest note)." % (name, key),
                )
            for key in server.get("required", []):
                self.assertNotIn(key, self.FORBIDDEN_PINS, name)

    def test_every_env_value_is_a_placeholder_not_a_literal(self):
        for name, server in self.by_name.items():
            for key, value in server.get("env", {}).items():
                self.assertEqual(
                    value,
                    "${%s}" % key,
                    "server '%s' env key %s carries a literal (%r). Baking a "
                    "value in here is how the pins got in." % (name, key, value),
                )

    def test_carries_no_literal_path_url_or_secret(self):
        raw = json.dumps(self.servers["servers"])
        for needle in ("http://", "https://", "/Users/", "C:\\", "/home/"):
            self.assertNotIn(needle, raw, needle)

    def test_required_keys_are_actually_referenced(self):
        for name, server in self.by_name.items():
            body = json.dumps(server)
            for key in server["required"]:
                self.assertIn(
                    "${%s}" % key,
                    body,
                    "server '%s' requires %s but never uses it." % (name, key),
                )

    def test_config_env_knobs_reach_the_generated_file(self):
        for decl in self.manifest["servers"]:
            if not decl.get("available", True):
                continue
            for key in decl.get("config_env", []):
                self.assertIn(key, self.by_name[decl["name"]]["env"], key)


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

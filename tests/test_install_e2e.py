"""End-to-end install/uninstall against a throwaway Claude config dir.

These run the REAL installer as a subprocess (CLAUDE_CONFIG_DIR points it at a
temp dir) and assert the safety properties the README promises: dry-run writes
nothing, install/uninstall is byte-exact, foreign entries survive, re-runs are
no-ops, and no credential reaches stdout. Stdlib unittest only.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

INSTALLER = os.path.join(ROOT, "installer.py")

SECRET_URL = "https://worker.example.com/mcp/SUPERSECRETTOKEN0123456789"

FOREIGN_HOOK_COMMAND = "python /opt/othertool/session_start_hook.py"
FOREIGN_SERVER = {"type": "stdio", "command": "node", "args": ["other.js"]}

CLAUDE_MD_BODY = "# My notes\r\n\r\nkeep me\r\n"


def _snapshot(directory):
    """{relative path: bytes} for every file under `directory`."""
    out = {}
    for base, _dirs, files in os.walk(directory):
        for name in files:
            full = os.path.join(base, name)
            with open(full, "rb") as fh:
                out[os.path.relpath(full, directory)] = fh.read()
    return out


class InstallE2ETest(unittest.TestCase):
    maxDiff = None

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="xylem-e2e-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.claude_dir = os.path.join(self.tmp, "claude")
        os.makedirs(self.claude_dir)
        self.settings_path = os.path.join(self.claude_dir, "settings.json")
        self.claude_md = os.path.join(self.claude_dir, "CLAUDE.md")
        self.commands_path = os.path.join(
            self.claude_dir, "commands", "xylem-discipline.md")

        # A hand-maintained settings.json: 4-space indent, a foreign MCP server,
        # and a foreign SessionStart hook whose script happens to share our
        # generic filename.
        self.original_settings = {
            "theme": "dark",
            "mcpServers": {"other-tool": dict(FOREIGN_SERVER)},
            "hooks": {
                "SessionStart": [
                    {"matcher": "",
                     "hooks": [{"type": "command",
                                "command": FOREIGN_HOOK_COMMAND}]}
                ]
            },
        }
        self._write_bytes(
            self.settings_path,
            (json.dumps(self.original_settings, indent=4) + "\n").encode("utf-8"))
        # CRLF, as a repo-committed file on Windows would be.
        self._write_bytes(self.claude_md, CLAUDE_MD_BODY.encode("utf-8"))

    # -- helpers -----------------------------------------------------------

    @staticmethod
    def _write_bytes(path, data):
        parent = os.path.dirname(path)
        if parent and not os.path.isdir(parent):
            os.makedirs(parent)
        with open(path, "wb") as fh:
            fh.write(data)

    def _env(self, **extra):
        env = {k: v for k, v in os.environ.items()
               if not k.endswith("_REMOTE_URL")}
        env["CLAUDE_CONFIG_DIR"] = self.claude_dir
        env.update(extra)
        return env

    def _run(self, *args, **kwargs):
        """Invoke the installer, defaulting to a real write.

        The installer previews unless given --apply (both installers in this
        repo now do; they used to ship opposite defaults under the same
        filename). These tests are about what gets *written*, so unless a case
        explicitly asks for a preview we add --apply here rather than
        threading it through every call site. test_bare_invocation_previews
        below is what pins the default itself.
        """
        args = list(args)
        if "--dry-run" not in args and "--apply" not in args:
            args.append("--apply")
        proc = subprocess.run(
            [sys.executable, INSTALLER] + args,
            cwd=ROOT, env=self._env(**kwargs.pop("env", {})),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            universal_newlines=True)
        self.assertEqual(proc.returncode, 0,
                         "installer failed: %s%s" % (proc.stdout, proc.stderr))
        return proc.stdout + proc.stderr

    def _read_bytes(self, path):
        with open(path, "rb") as fh:
            return fh.read()

    def _settings(self):
        return json.loads(self._read_bytes(self.settings_path).decode("utf-8"))

    # -- (a) dry-run writes nothing ---------------------------------------

    def test_bare_invocation_previews_and_writes_nothing(self):
        """No flags means PREVIEW.

        The root installer used to apply immediately while install/install.sh
        dry-ran, under the same filename. Aligning them is only worth anything
        if it stays aligned, so the default is pinned here.
        """
        before = _snapshot(self.claude_dir)
        proc = subprocess.run(
            [sys.executable, INSTALLER],
            cwd=ROOT, env=self._env(),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            universal_newlines=True)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(_snapshot(self.claude_dir), before,
                         "a bare installer run must not write anything")
        self.assertIn("PREVIEW", proc.stdout + proc.stderr)

    def test_dry_run_writes_nothing(self):
        before = _snapshot(self.claude_dir)
        out = self._run("--dry-run")
        self.assertEqual(_snapshot(self.claude_dir), before)
        self.assertIn("dry run", out)

    def test_uninstall_dry_run_writes_nothing(self):
        self._run()
        before = _snapshot(self.claude_dir)
        self._run("--uninstall", "--dry-run")
        self.assertEqual(_snapshot(self.claude_dir), before)

    # -- (b) install/uninstall round-trips byte-for-byte -------------------

    def test_round_trip_restores_original_bytes(self):
        settings_before = self._read_bytes(self.settings_path)
        md_before = self._read_bytes(self.claude_md)

        self._run()
        self.assertNotEqual(self._read_bytes(self.settings_path), settings_before)
        self._run("--uninstall")

        self.assertEqual(self._read_bytes(self.settings_path), settings_before)
        self.assertEqual(self._read_bytes(self.claude_md), md_before)
        self.assertFalse(os.path.exists(self.commands_path))
        # Only the backups the installer promises are left behind.
        leftovers = [n for n in _snapshot(self.claude_dir)
                     if ".xylem-backup" not in n]
        self.assertEqual(sorted(leftovers), ["CLAUDE.md", "settings.json"])

    def test_install_preserves_json_indent_and_crlf(self):
        self._run()
        raw = self._read_bytes(self.settings_path).decode("utf-8")
        # 4-space indent preserved, not reformatted to the hardcoded 2.
        self.assertIn('\n    "theme"', raw)
        self.assertNotIn('\n  "theme"', raw)
        # CLAUDE.md stays CRLF end to end.
        md = self._read_bytes(self.claude_md)
        self.assertNotIn(b"\n", md.replace(b"\r\n", b""))
        self.assertIn(b"XYLEM:BEGIN", md)

    def test_install_preserves_utf8_bom(self):
        self._write_bytes(self.claude_md,
                          b"\xef\xbb\xbf" + CLAUDE_MD_BODY.encode("utf-8"))
        self._run()
        self.assertTrue(self._read_bytes(self.claude_md).startswith(b"\xef\xbb\xbf"))

    # -- (c) foreign entries survive --------------------------------------

    def test_foreign_server_and_hook_survive_install(self):
        self._run()
        settings = self._settings()
        self.assertEqual(settings["mcpServers"]["other-tool"], FOREIGN_SERVER)
        self.assertEqual(settings["theme"], "dark")
        commands = [h["command"]
                    for g in settings["hooks"]["SessionStart"]
                    for h in g["hooks"]]
        self.assertIn(FOREIGN_HOOK_COMMAND, commands)

    def test_foreign_server_and_hook_survive_uninstall(self):
        self._run()
        self._run("--uninstall")
        settings = self._settings()
        self.assertEqual(settings["mcpServers"]["other-tool"], FOREIGN_SERVER)
        commands = [h["command"]
                    for g in settings["hooks"]["SessionStart"]
                    for h in g["hooks"]]
        self.assertEqual(commands, [FOREIGN_HOOK_COMMAND])

    def test_foreign_hook_group_keeps_its_matcher(self):
        self._run()
        self._run()
        group = self._settings()["hooks"]["SessionStart"][0]
        self.assertEqual(group["matcher"], "")

    # -- (d) idempotency ---------------------------------------------------

    def test_second_install_writes_nothing(self):
        self._run()
        before = _snapshot(self.claude_dir)
        out = self._run()
        self.assertIn("already up to date", out)
        self.assertEqual(_snapshot(self.claude_dir), before)

    def test_hooks_carry_a_timeout(self):
        self._run()
        hooks = self._settings()["hooks"]
        xylem = [h for g in hooks["SessionStart"] for h in g["hooks"]
                 if "othertool" not in h["command"]]
        self.assertTrue(xylem)
        for hook in xylem + [h for g in hooks["SessionEnd"] for h in g["hooks"]]:
            self.assertIsInstance(hook.get("timeout"), int)

    # -- (e) no secret in dry-run output ----------------------------------

    def test_dry_run_does_not_print_the_token(self):
        out = self._run("--dry-run", env={"AGENTSYNC_REMOTE_URL": SECRET_URL})
        self.assertNotIn("SUPERSECRETTOKEN0123456789", out)
        self.assertIn("<redacted>", out)

    def test_installed_settings_still_contain_the_real_url(self):
        # Redaction is a display concern only -- the written config must work.
        self._run(env={"AGENTSYNC_REMOTE_URL": SECRET_URL})
        self.assertEqual(
            self._settings()["mcpServers"]["agentsync-remote"]["url"], SECRET_URL)

    def test_rotated_url_removes_the_stale_entry(self):
        self._run(env={"AGENTSYNC_REMOTE_URL": SECRET_URL})
        self.assertIn("agentsync-remote", self._settings()["mcpServers"])
        self._run()  # env var no longer set
        self.assertNotIn("agentsync-remote", self._settings()["mcpServers"])

    # -- non-strict JSON does not abort the run ---------------------------

    def test_commented_settings_json_is_left_alone_not_crashed(self):
        broken = b'{\n  // a comment\n  "theme": "dark"\n}\n'
        self._write_bytes(self.settings_path, broken)
        out = self._run()
        self.assertEqual(self._read_bytes(self.settings_path), broken)
        self.assertIn("not strict JSON", out)
        # The other legs still ran.
        self.assertIn(b"XYLEM:BEGIN", self._read_bytes(self.claude_md))
        self.assertTrue(os.path.exists(self.commands_path))

    # -- fresh machine / --project ----------------------------------------

    def test_fresh_machine_install(self):
        os.remove(self.settings_path)
        os.remove(self.claude_md)
        self._run()
        self.assertIn("context-keeper", self._settings()["mcpServers"])
        self.assertIn(b"XYLEM:BEGIN", self._read_bytes(self.claude_md))

    def test_project_install_preserves_repo_crlf(self):
        project = os.path.join(self.tmp, "repo")
        project_md = os.path.join(project, "CLAUDE.md")
        self._write_bytes(project_md, CLAUDE_MD_BODY.encode("utf-8"))
        before = self._read_bytes(project_md)
        self._run("--project", project)
        after = self._read_bytes(project_md)
        # The pre-existing lines are byte-identical; only the block is added.
        self.assertTrue(after.startswith(before.rstrip(b"\r\n")))
        self.assertNotIn(b"\n", after.replace(b"\r\n", b""))
        self._run("--project", project, "--uninstall")
        self.assertEqual(self._read_bytes(project_md), before)

    # -- backups ------------------------------------------------------------

    def test_hand_edit_to_slash_command_is_backed_up(self):
        self._run()
        self._write_bytes(self.commands_path, b"my own notes\n")
        self._run("--uninstall")
        backups = [n for n in os.listdir(os.path.dirname(self.commands_path))
                   if n.startswith("xylem-discipline.md.xylem-backup")]
        recovered = []
        for name in backups:
            recovered.append(self._read_bytes(
                os.path.join(os.path.dirname(self.commands_path), name)))
        self.assertIn(b"my own notes\n", recovered)


if __name__ == "__main__":
    unittest.main()

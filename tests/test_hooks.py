"""SessionStart hook merge: valid JSON, no duplication, clean removal. Stdlib."""
import copy
import json
import os
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from installer import (  # noqa: E402
    merge_hooks, remove_hooks, HOOK_MARKER, VERSION_CHECK_MARKER,
    DISTILL_HOOK_MARKER, build_settings_install, build_settings_uninstall,
    CAMBIUM_ENV_KEY,
)

COMMAND = '"python" "/x/artifacts/session_start_hook.py"'
VERSION_CHECK_COMMAND = '"python" "/x/artifacts/version_check.py"'
DISTILL_COMMAND = '"python" "/x/artifacts/session_end_hook.py"'


class HooksTest(unittest.TestCase):
    def test_generated_hooks_are_valid_json(self):
        settings = {}
        merge_hooks(settings, COMMAND)
        # Must survive a JSON round-trip unchanged.
        round_tripped = json.loads(json.dumps(settings))
        self.assertEqual(round_tripped, settings)

    def test_hook_registered_under_session_start(self):
        settings = {}
        merge_hooks(settings, COMMAND)
        groups = settings["hooks"]["SessionStart"]
        self.assertEqual(len(groups), 1)
        inner = groups[0]["hooks"][0]
        self.assertEqual(inner["type"], "command")
        self.assertEqual(inner["command"], COMMAND)
        self.assertIn(HOOK_MARKER, inner["command"])

    def test_does_not_duplicate_on_rerun(self):
        settings = {}
        merge_hooks(settings, COMMAND)
        merge_hooks(settings, COMMAND)
        merge_hooks(settings, COMMAND)
        groups = settings["hooks"]["SessionStart"]
        marker_groups = [
            g for g in groups
            if any(HOOK_MARKER in h.get("command", "") for h in g["hooks"])
        ]
        self.assertEqual(len(marker_groups), 1)

    def test_is_idempotent(self):
        settings = {}
        merge_hooks(settings, COMMAND)
        once = copy.deepcopy(settings)
        merge_hooks(settings, COMMAND)
        self.assertEqual(once, settings)

    def test_preserves_foreign_session_start_hooks(self):
        settings = {
            "hooks": {
                "SessionStart": [
                    {"hooks": [{"type": "command", "command": "echo hi"}]}
                ]
            }
        }
        merge_hooks(settings, COMMAND)
        groups = settings["hooks"]["SessionStart"]
        self.assertEqual(len(groups), 2)
        commands = [h["command"] for g in groups for h in g["hooks"]]
        self.assertIn("echo hi", commands)
        self.assertIn(COMMAND, commands)

    def test_preserves_other_hook_events(self):
        settings = {
            "hooks": {
                "PreToolUse": [
                    {"matcher": "Bash", "hooks": [{"type": "command", "command": "guard"}]}
                ]
            }
        }
        merge_hooks(settings, COMMAND)
        self.assertIn("PreToolUse", settings["hooks"])
        self.assertIn("SessionStart", settings["hooks"])

    def test_remove_strips_only_xylem_hook(self):
        settings = {
            "hooks": {
                "SessionStart": [
                    {"hooks": [{"type": "command", "command": "echo hi"}]}
                ]
            }
        }
        merge_hooks(settings, COMMAND)
        remove_hooks(settings)
        groups = settings["hooks"]["SessionStart"]
        commands = [h["command"] for g in groups for h in g["hooks"]]
        self.assertIn("echo hi", commands)
        self.assertNotIn(COMMAND, commands)

    def test_remove_prunes_empty_containers(self):
        settings = {}
        merge_hooks(settings, COMMAND)
        remove_hooks(settings)
        self.assertNotIn("hooks", settings)


class TwoHookTest(unittest.TestCase):
    """The version_check hook coexists with the session_start hook."""

    def _install_both(self, settings):
        merge_hooks(settings, COMMAND)
        merge_hooks(settings, VERSION_CHECK_COMMAND, marker=VERSION_CHECK_MARKER)

    def test_both_hooks_register_under_session_start(self):
        settings = {}
        self._install_both(settings)
        groups = settings["hooks"]["SessionStart"]
        commands = [h["command"] for g in groups for h in g["hooks"]]
        self.assertIn(COMMAND, commands)
        self.assertIn(VERSION_CHECK_COMMAND, commands)
        self.assertEqual(len(groups), 2)

    def test_neither_hook_duplicates_on_rerun(self):
        settings = {}
        self._install_both(settings)
        self._install_both(settings)
        self._install_both(settings)
        groups = settings["hooks"]["SessionStart"]
        session_groups = [
            g for g in groups
            if any(HOOK_MARKER in h.get("command", "") for h in g["hooks"])
        ]
        version_groups = [
            g for g in groups
            if any(VERSION_CHECK_MARKER in h.get("command", "") for h in g["hooks"])
        ]
        self.assertEqual(len(session_groups), 1)
        self.assertEqual(len(version_groups), 1)
        self.assertEqual(len(groups), 2)

    def test_uninstall_removes_both_hooks(self):
        settings = {}
        self._install_both(settings)
        remove_hooks(settings)
        remove_hooks(settings, marker=VERSION_CHECK_MARKER)
        self.assertNotIn("hooks", settings)

    def test_removing_one_hook_leaves_the_other(self):
        settings = {}
        self._install_both(settings)
        remove_hooks(settings, marker=VERSION_CHECK_MARKER)
        groups = settings["hooks"]["SessionStart"]
        commands = [h["command"] for g in groups for h in g["hooks"]]
        self.assertIn(COMMAND, commands)
        self.assertNotIn(VERSION_CHECK_COMMAND, commands)


class DistillHookTest(unittest.TestCase):
    """The SessionEnd distill hook registers under SessionEnd, independently."""

    def test_registers_under_session_end(self):
        settings = {}
        merge_hooks(settings, DISTILL_COMMAND, marker=DISTILL_HOOK_MARKER,
                    event="SessionEnd")
        groups = settings["hooks"]["SessionEnd"]
        self.assertEqual(len(groups), 1)
        inner = groups[0]["hooks"][0]
        self.assertEqual(inner["command"], DISTILL_COMMAND)
        self.assertIn(DISTILL_HOOK_MARKER, inner["command"])
        # It lives under SessionEnd, not SessionStart.
        self.assertNotIn("SessionStart", settings["hooks"])

    def test_does_not_duplicate_on_rerun(self):
        settings = {}
        for _ in range(3):
            merge_hooks(settings, DISTILL_COMMAND, marker=DISTILL_HOOK_MARKER,
                        event="SessionEnd")
        self.assertEqual(len(settings["hooks"]["SessionEnd"]), 1)

    def test_remove_prunes_session_end(self):
        settings = {}
        merge_hooks(settings, DISTILL_COMMAND, marker=DISTILL_HOOK_MARKER,
                    event="SessionEnd")
        remove_hooks(settings, marker=DISTILL_HOOK_MARKER, event="SessionEnd")
        self.assertNotIn("hooks", settings)


class DistillHookScriptTest(unittest.TestCase):
    """The session_end_hook.py script fails soft and prints ASCII-only."""

    SCRIPT = os.path.join(ROOT, "artifacts", "session_end_hook.py")

    def _run(self, env):
        full = dict(os.environ)
        full.pop("XYLEM_CAMBIUM_PATH", None)
        full.update(env)
        return subprocess.run(
            [sys.executable, self.SCRIPT], env=full,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def test_unset_path_exits_zero_and_skips(self):
        proc = self._run({"XYLEM_CAMBIUM_PATH": ""})
        self.assertEqual(proc.returncode, 0)
        # stderr is strictly ASCII-decodable (Windows cp1252 console safety).
        proc.stderr.decode("ascii")
        self.assertIn(b"cambium not configured", proc.stderr)

    def test_bad_path_exits_zero_and_skips(self):
        proc = self._run({"XYLEM_CAMBIUM_PATH": "/no/such/cambium_server.py"})
        self.assertEqual(proc.returncode, 0)
        proc.stderr.decode("ascii")


class BuildSettingsDistillTest(unittest.TestCase):
    """The full install transform registers the distill hook + cambium env."""

    MANIFEST = {"version": 3, "servers": []}

    def _install(self):
        settings = {}
        build_settings_install(
            settings, self.MANIFEST, mapping={}, ck_server_path="/x/ck.py",
            cambium_server_path="/x/cambium/cambium_server.py",
            hook_command=COMMAND, version_check_command=VERSION_CHECK_COMMAND,
            distill_command=DISTILL_COMMAND, warn=lambda m: None)
        return settings

    def test_install_registers_distill_hook_under_session_end(self):
        settings = self._install()
        groups = settings["hooks"]["SessionEnd"]
        commands = [h["command"] for g in groups for h in g["hooks"]]
        self.assertIn(DISTILL_COMMAND, commands)
        # SessionStart still carries the two original hooks.
        ss_commands = [h["command"]
                       for g in settings["hooks"]["SessionStart"]
                       for h in g["hooks"]]
        self.assertIn(COMMAND, ss_commands)
        self.assertIn(VERSION_CHECK_COMMAND, ss_commands)

    def test_install_sets_cambium_env(self):
        settings = self._install()
        self.assertEqual(settings["env"][CAMBIUM_ENV_KEY],
                         "/x/cambium/cambium_server.py")

    def test_uninstall_removes_distill_hook_and_env(self):
        settings = self._install()
        build_settings_uninstall(settings, self.MANIFEST)
        # Every Xylem hook and env key gone.
        self.assertNotIn("hooks", settings)
        self.assertNotIn("env", settings)


if __name__ == "__main__":
    unittest.main()

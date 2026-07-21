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
    CAMBIUM_ENV_KEY, OWNER_KEY, HOOK_TIMEOUT, DISTILL_HOOK_TIMEOUT,
    _is_xylem_hook_group, to_fwd,
)

COMMAND = '"python" "/x/artifacts/session_start_hook.py"'
VERSION_CHECK_COMMAND = '"python" "/x/artifacts/version_check.py"'
DISTILL_COMMAND = '"python" "/x/artifacts/session_end_hook.py"'
PRIMER_COMMAND = '"python" "/x/artifacts/session_primer_hook.py"'


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


class HookOwnershipTest(unittest.TestCase):
    """Ownership is explicit. A foreign hook whose script merely shares our
    (very generic) filename must never be touched, let alone deleted."""

    FOREIGN = "python /opt/othertool/session_start_hook.py"

    def _foreign_settings(self):
        return {
            "hooks": {
                "SessionStart": [
                    {"matcher": "",
                     "hooks": [{"type": "command", "command": self.FOREIGN}]}
                ]
            }
        }

    def test_foreign_group_is_not_recognized_as_ours(self):
        group = self._foreign_settings()["hooks"]["SessionStart"][0]
        self.assertFalse(_is_xylem_hook_group(group, HOOK_MARKER))

    def test_foreign_hook_survives_install(self):
        settings = self._foreign_settings()
        merge_hooks(settings, COMMAND)
        commands = [h["command"] for g in settings["hooks"]["SessionStart"]
                    for h in g["hooks"]]
        self.assertIn(self.FOREIGN, commands)
        self.assertIn(COMMAND, commands)

    def test_foreign_hook_survives_uninstall(self):
        settings = self._foreign_settings()
        merge_hooks(settings, COMMAND)
        remove_hooks(settings)
        groups = settings["hooks"]["SessionStart"]
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["hooks"][0]["command"], self.FOREIGN)
        self.assertEqual(groups[0]["matcher"], "")

    def test_uninstall_without_installing_leaves_foreign_hook(self):
        settings = self._foreign_settings()
        remove_hooks(settings)
        self.assertIn("SessionStart", settings["hooks"])

    def test_our_groups_carry_the_sentinel(self):
        settings = {}
        merge_hooks(settings, COMMAND)
        self.assertIs(settings["hooks"]["SessionStart"][0][OWNER_KEY], True)

    def test_legacy_group_under_xylem_root_is_still_ours(self):
        # Written by an older version: no sentinel, but the script resolves
        # under this checkout. It must still be recognized and cleaned up.
        legacy_command = '"python" "%s"' % to_fwd(
            os.path.join(ROOT, "artifacts", "session_start_hook.py"))
        settings = {"hooks": {"SessionStart": [
            {"hooks": [{"type": "command", "command": legacy_command}]}]}}
        self.assertTrue(
            _is_xylem_hook_group(settings["hooks"]["SessionStart"][0],
                                 HOOK_MARKER))
        remove_hooks(settings)
        self.assertNotIn("hooks", settings)


class HookShapeTest(unittest.TestCase):
    """Registered hooks carry a timeout, and re-runs update in place."""

    def test_hook_has_a_timeout(self):
        settings = {}
        merge_hooks(settings, COMMAND)
        hook = settings["hooks"]["SessionStart"][0]["hooks"][0]
        self.assertEqual(hook["timeout"], HOOK_TIMEOUT)

    def test_distill_hook_has_the_longer_timeout(self):
        settings = {}
        merge_hooks(settings, DISTILL_COMMAND, marker=DISTILL_HOOK_MARKER,
                    event="SessionEnd", timeout=DISTILL_HOOK_TIMEOUT)
        hook = settings["hooks"]["SessionEnd"][0]["hooks"][0]
        self.assertEqual(hook["timeout"], DISTILL_HOOK_TIMEOUT)

    def test_rerun_preserves_user_added_matcher_and_position(self):
        settings = {}
        merge_hooks(settings, COMMAND)
        merge_hooks(settings, VERSION_CHECK_COMMAND,
                    marker=VERSION_CHECK_MARKER)
        # The user hand-adds a matcher to our group and it must survive.
        settings["hooks"]["SessionStart"][0]["matcher"] = "startup"
        before = copy.deepcopy(settings)
        merge_hooks(settings, COMMAND)
        self.assertEqual(settings, before)

    def test_changed_command_is_updated_in_place(self):
        settings = {}
        merge_hooks(settings, COMMAND)
        merge_hooks(settings, VERSION_CHECK_COMMAND,
                    marker=VERSION_CHECK_MARKER)
        new_command = '"python3.12" "/x/artifacts/session_start_hook.py"'
        merge_hooks(settings, new_command)
        groups = settings["hooks"]["SessionStart"]
        self.assertEqual(len(groups), 2)
        # Still first in the list -- no churn from remove-then-append.
        self.assertEqual(groups[0]["hooks"][0]["command"], new_command)


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
            stdin=subprocess.DEVNULL,  # hook reads a SessionEnd payload from stdin
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


class SessionRepoTest(unittest.TestCase):
    """_session_repo() resolves the session's project (git root) from the
    SessionEnd payload cwd, so a global hook distills whichever repo you were in."""

    def _load_hook(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "session_end_hook",
            os.path.join(ROOT, "artifacts", "session_end_hook.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_resolves_git_root_from_payload_cwd(self):
        import io, json, tempfile, shutil
        mod = self._load_hook()
        d = tempfile.mkdtemp()
        try:
            sub = os.path.join(d, "proj", "src")
            os.makedirs(sub)
            os.makedirs(os.path.join(d, "proj", ".git"))
            old = sys.stdin
            sys.stdin = io.StringIO(json.dumps({"cwd": sub}))
            try:
                repo = mod._session_repo()
            finally:
                sys.stdin = old
            self.assertTrue(os.path.samefile(repo, os.path.join(d, "proj")))
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_bad_or_empty_payload_yields_empty(self):
        import io, json
        mod = self._load_hook()
        for raw in ("", "   ", "not json", json.dumps({"no_cwd": 1}),
                    json.dumps({"cwd": "/path/with/no/git/anywhere/xyzzy"})):
            old = sys.stdin
            sys.stdin = io.StringIO(raw)
            try:
                self.assertEqual(mod._session_repo(), "")
            finally:
                sys.stdin = old


class BuildSettingsDistillTest(unittest.TestCase):
    """The full install transform registers the distill hook + cambium env."""

    MANIFEST = {"version": 3, "servers": []}

    def _install(self):
        settings = {}
        build_settings_install(
            settings, self.MANIFEST, mapping={}, ck_server_path="/x/ck.py",
            cambium_server_path="/x/cambium/cambium_server.py",
            hook_command=COMMAND, version_check_command=VERSION_CHECK_COMMAND,
            distill_command=DISTILL_COMMAND, primer_command=PRIMER_COMMAND,
            warn=lambda m: None)
        return settings

    def test_install_registers_distill_hook_under_session_end(self):
        settings = self._install()
        groups = settings["hooks"]["SessionEnd"]
        commands = [h["command"] for g in groups for h in g["hooks"]]
        self.assertIn(DISTILL_COMMAND, commands)
        # SessionStart carries the summary + version-check + the primer hook.
        ss_commands = [h["command"]
                       for g in settings["hooks"]["SessionStart"]
                       for h in g["hooks"]]
        self.assertIn(COMMAND, ss_commands)
        self.assertIn(VERSION_CHECK_COMMAND, ss_commands)
        self.assertIn(PRIMER_COMMAND, ss_commands)

    def test_uninstall_removes_the_primer_hook(self):
        settings = self._install()
        # sanity: present after install
        ss = [h["command"] for g in settings["hooks"]["SessionStart"]
              for h in g["hooks"]]
        self.assertIn(PRIMER_COMMAND, ss)
        build_settings_uninstall(settings, self.MANIFEST)
        self.assertNotIn("hooks", settings)   # primer gone with the rest

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


class PrimerHookTest(unittest.TestCase):
    """The SessionStart primer hook renders a digest and fails soft."""

    @staticmethod
    def _load():
        import importlib.util
        path = os.path.join(ROOT, "artifacts", "session_primer_hook.py")
        spec = importlib.util.spec_from_file_location("xylem_primer_hook", path)
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        return m

    def test_format_renders_items_and_assumptions(self):
        h = self._load()
        parsed = {
            "project": "proj", "known_items": 2,
            "known": [{"scope": "local", "content": "a fact", "recalls": 3}],
            "check_assumptions": [{"content": "billing on NetSuite",
                                   "valid_while": "while on NetSuite"}],
        }
        out = h._format(parsed)
        self.assertIn("cambium knows 2 item(s) for proj", out)
        self.assertIn("a fact", out)
        self.assertIn("recalls=3", out)
        self.assertIn("NetSuite", out)
        self.assertIn("recall(", out)

    def test_output_is_ascii(self):
        # The digest goes to a possibly-cp1252 console; the renderer must not
        # introduce a non-encodable byte even from unicode content.
        h = self._load()
        out = h._format({"project": "p", "known_items": 1,
                         "known": [{"scope": "local",
                                    "content": "em—dash", "recalls": 0}],
                         "check_assumptions": []})
        h._ascii(out).encode("ascii")  # must not raise


if __name__ == "__main__":
    unittest.main()

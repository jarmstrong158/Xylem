"""The plugin path: a distill hook that actually distills, and hooks that launch.

Two bugs this module exists for.

1. plugin/scripts/distill.py was a PERMANENT no-op. It shelled
   `subprocess.run(["cambium", "distill"])` behind a `shutil.which("cambium")`
   guard, and no `cambium` executable has ever existed -- cambium ships one
   console script, `cambium-mcp`, whose main() is `mcp.run()` with no argparse.
   The guard never passed, so every session printed a tidy "cambium not found"
   line and the capture leg of the compound-growth loop never ran. Three shipped
   skills documented the same imaginary CLI.

2. plugin/hooks/hooks.json launched with a bare `python` -- the exact bug
   dec-013 documents, reintroduced on the plugin path, where it is silent: a
   SessionStart hook that never launches produces no primer and no error.
"""

import ast
import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGIN = os.path.join(ROOT, "plugin")
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(PLUGIN, "scripts"))

import distill  # noqa: E402  (plugin/scripts/distill.py)
import xylem_interpreter  # noqa: E402

SKILLS = os.path.join(PLUGIN, "skills")


def _run_hook(payload, env=None):
    """Run distill.py as the hook runner does: real process, JSON on stdin."""
    child_env = dict(os.environ)
    child_env.pop("XYLEM_CAMBIUM_PATH", None)
    child_env.pop("CAMBIUM_SERVER", None)
    child_env.update(env or {})
    return subprocess.run(
        [sys.executable, os.path.join(PLUGIN, "scripts", "distill.py")],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=child_env,
    )


class DistillCallsTheModuleNotAMissingCli(unittest.TestCase):
    def test_no_cambium_console_script_is_invoked(self):
        """No executable named `cambium` may appear anywhere in the live code.

        Checked against the AST with docstrings stripped, so the module can go
        on explaining the bug in prose without the guard tripping over the
        explanation.
        """
        with open(os.path.join(PLUGIN, "scripts", "distill.py"), encoding="utf-8") as fh:
            tree = ast.parse(fh.read())

        # An argv is a list literal; its first element is the program to run.
        for node in ast.walk(tree):
            if not isinstance(node, ast.List) or not node.elts:
                continue
            first = node.elts[0]
            if not (isinstance(first, ast.Constant) and isinstance(first.value, str)):
                continue
            self.assertFalse(
                first.value.startswith("cambium"),
                "distill.py builds an argv starting with %r. cambium ships only "
                "`cambium-mcp` (an MCP stdio server with no subcommands); this "
                "hook is a no-op the moment it depends on a console script that "
                "does not exist." % first.value,
            )
        # The old guard. shutil.which on a name that is never installed is what
        # made the no-op look like a clean, deliberate skip.
        self.assertNotIn("shutil", [n.name for n in ast.walk(tree) if isinstance(n, ast.alias)])

    def test_it_launches_with_a_resolved_interpreter(self):
        # dec-013: never a bare name for the child process either.
        self.assertEqual(distill.interpreter(), sys.executable)

    def test_finds_a_cambium_checkout_via_the_env_pointer(self):
        with tempfile.TemporaryDirectory() as tmp:
            server = os.path.join(tmp, "cambium_server.py")
            open(server, "w").close()
            for key in distill.PATH_ENV_KEYS:
                with self.subTest(key=key):
                    os.environ[key] = server
                    self.addCleanup(os.environ.pop, key, None)
                    self.assertEqual(distill.find_cambium_server(), os.path.abspath(server))
                    os.environ.pop(key, None)

    def test_a_directory_pointer_is_accepted_too(self):
        with tempfile.TemporaryDirectory() as tmp:
            server = os.path.join(tmp, "cambium_server.py")
            open(server, "w").close()
            os.environ["XYLEM_CAMBIUM_PATH"] = tmp
            self.addCleanup(os.environ.pop, "XYLEM_CAMBIUM_PATH", None)
            self.assertEqual(distill.find_cambium_server(), os.path.abspath(server))

    def test_a_pointer_at_nothing_resolves_to_nothing(self):
        os.environ["XYLEM_CAMBIUM_PATH"] = os.path.join(ROOT, "does", "not", "exist.py")
        self.addCleanup(os.environ.pop, "XYLEM_CAMBIUM_PATH", None)
        # Falls through to sibling discovery, which must not invent a path.
        found = distill.find_cambium_server()
        if found is not None:
            self.assertTrue(os.path.isfile(found))


class DistillResultIsNotTakenOnFaith(unittest.TestCase):
    """cambium's unconfigured guidance must not be reported as a capture."""

    def test_only_a_distilled_status_counts(self):
        ok, count = distill._new_items(json.dumps({"status": "distilled", "new_items": 7}))
        self.assertTrue(ok)
        self.assertEqual(count, 7)

    def test_unconfigured_guidance_is_not_a_capture(self):
        for raw in (
            json.dumps({"status": "needs_setup", "gaps": []}),
            json.dumps({"status": "error"}),
            "not json at all",
            "",
            json.dumps(["a", "list"]),
        ):
            with self.subTest(raw=raw[:40]):
                self.assertEqual(distill._new_items(raw), (False, 0))

    def test_a_non_integer_count_does_not_crash_the_report(self):
        self.assertEqual(distill._new_items(json.dumps({"status": "distilled", "new_items": None})), (True, 0))


class DistillEndToEnd(unittest.TestCase):
    """Run the real hook against a stand-in cambium_server.py."""

    def _fake_cambium(self, tmp, body):
        server = os.path.join(tmp, "cambium_server.py")
        with open(server, "w", encoding="utf-8") as fh:
            fh.write(body)
        return server

    def test_a_real_distill_is_reported_with_its_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = os.path.join(tmp, "repo")
            os.makedirs(os.path.join(repo, ".git"))
            server = self._fake_cambium(
                tmp,
                "import json, os\n"
                "def distill():\n"
                "    return json.dumps({'status': 'distilled', 'new_items': 3,\n"
                "                       'repo': os.environ.get('CAMBIUM_REPO')})\n",
            )
            proc = _run_hook({"cwd": repo}, {"XYLEM_CAMBIUM_PATH": server})
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("distilled 3 new item(s)", proc.stdout)

    def test_it_distills_the_session_project_not_the_inherited_cwd(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = os.path.join(tmp, "the-session-repo")
            os.makedirs(os.path.join(repo, ".git"))
            server = self._fake_cambium(
                tmp,
                "import json, os\n"
                "def distill():\n"
                "    return json.dumps({'status': 'distilled',\n"
                "                       'new_items': len(os.environ['CAMBIUM_REPO'])})\n",
            )
            proc = _run_hook({"cwd": repo}, {"XYLEM_CAMBIUM_PATH": server})
            self.assertIn("distilled %d new item(s)" % len(repo), proc.stdout)

    def test_an_unconfigured_cambium_reports_nothing_distilled(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = os.path.join(tmp, "repo")
            os.makedirs(os.path.join(repo, ".git"))
            server = self._fake_cambium(
                tmp,
                "import json\n"
                "def distill():\n"
                "    return json.dumps({'status': 'needs_setup'})\n",
            )
            proc = _run_hook({"cwd": repo}, {"XYLEM_CAMBIUM_PATH": server})
            self.assertEqual(proc.returncode, 0)
            self.assertIn("not configured yet - nothing distilled", proc.stdout)
            self.assertNotIn("into the local knowledge store", proc.stdout)

    def test_a_raising_cambium_never_fails_the_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = os.path.join(tmp, "repo")
            os.makedirs(os.path.join(repo, ".git"))
            server = self._fake_cambium(
                tmp, "def distill():\n    raise RuntimeError('boom')\n"
            )
            proc = _run_hook({"cwd": repo}, {"XYLEM_CAMBIUM_PATH": server})
            self.assertEqual(proc.returncode, 0)
            self.assertNotIn("Traceback", proc.stdout)

    def test_a_non_git_session_distills_nothing_rather_than_the_wrong_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            plain = os.path.join(tmp, "not-a-repo")
            os.makedirs(plain)
            server = self._fake_cambium(
                tmp,
                "import json\n"
                "def distill():\n"
                "    return json.dumps({'status': 'distilled', 'new_items': 99})\n",
            )
            proc = _run_hook({"cwd": plain}, {"XYLEM_CAMBIUM_PATH": server})
            self.assertEqual(proc.returncode, 0)
            self.assertIn("could not resolve a git root", proc.stdout)

    def test_a_missing_cambium_is_a_clean_skip(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = os.path.join(tmp, "repo")
            os.makedirs(os.path.join(repo, ".git"))
            proc = _run_hook(
                {"cwd": repo},
                {"XYLEM_CAMBIUM_PATH": os.path.join(tmp, "nope", "cambium_server.py")},
            )
            self.assertEqual(proc.returncode, 0)


class PluginHooksLaunchAnInterpreterThatExists(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(PLUGIN, "hooks", "hooks.json"), encoding="utf-8") as fh:
            self.hooks = json.load(fh)
        self.commands = [
            inner["command"]
            for groups in self.hooks["hooks"].values()
            for group in groups
            for inner in group["hooks"]
        ]

    def test_both_session_hooks_are_registered(self):
        self.assertEqual(set(self.hooks["hooks"]), {"SessionStart", "SessionEnd"})
        self.assertEqual(len(self.commands), 2)

    def test_no_command_is_a_bare_single_interpreter_launch(self):
        # dec-013 on the plugin path. `python` alone is absent on most Linux and
        # macOS boxes; `python3` alone is the Microsoft Store shim on Windows.
        # Neither name is safe on its own, so every command must offer a fallback.
        for command in self.commands:
            for name in xylem_interpreter.LAUNCH_CHAIN:
                self.assertIn(
                    name + " ",
                    command,
                    "hook command %r does not try %r. A single bare interpreter "
                    "name fails silently on some supported platform." % (command, name),
                )
            self.assertIn("||", command, command)

    def test_the_script_path_is_quoted(self):
        # Plugin roots contain spaces on Windows more often than not.
        for command in self.commands:
            self.assertIn('"${CLAUDE_PLUGIN_ROOT}/', command)

    def test_every_referenced_script_exists(self):
        for command in self.commands:
            for token in command.split('"'):
                if token.startswith("${CLAUDE_PLUGIN_ROOT}/"):
                    rel = token[len("${CLAUDE_PLUGIN_ROOT}/"):]
                    self.assertTrue(
                        os.path.isfile(os.path.join(PLUGIN, rel)),
                        "hooks.json launches %s, which does not exist" % rel,
                    )

    def test_the_hook_scripts_exit_zero_even_when_they_fail(self):
        # The fallback chain is only safe because of this: a script that exited
        # non-zero on its own errors would be run a second time by `||`.
        for script in ("primer.py", "distill.py"):
            with self.subTest(script=script):
                proc = subprocess.run(
                    [sys.executable, os.path.join(PLUGIN, "scripts", script)],
                    input="{ this is not json",
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(proc.returncode, 0, proc.stderr)


class SkillsDocumentToolsThatExist(unittest.TestCase):
    """Three of the seven shipped skills documented a CLI that never existed."""

    CAMBIUM_SKILLS = ("distill-session", "recall-knowledge", "promote-to-org")

    def _read(self, name):
        with open(os.path.join(SKILLS, name, "SKILL.md"), encoding="utf-8") as fh:
            return fh.read()

    def test_no_skill_instructs_running_a_cambium_command(self):
        for name in self.CAMBIUM_SKILLS:
            text = self._read(name)
            for phantom in ("`cambium distill`", "`cambium recall", "`cambium review_promotions`"):
                self.assertNotIn(
                    phantom,
                    text,
                    "skill %s tells the agent to run %s. cambium has no CLI; the "
                    "agent will report a failure that is really a documentation bug."
                    % (name, phantom),
                )

    def test_no_skill_gates_on_cambium_being_on_path(self):
        for name in self.CAMBIUM_SKILLS:
            text = self._read(name)
            self.assertNotIn(
                "`cambium` is not on PATH",
                text,
                "skill %s gates on a PATH check that can never pass." % name,
            )

    def test_the_cambium_skills_say_it_is_an_mcp_server(self):
        for name in self.CAMBIUM_SKILLS:
            self.assertIn("MCP server", self._read(name), name)

    def test_plugin_readme_does_not_advertise_a_cambium_cli(self):
        with open(os.path.join(PLUGIN, "README.md"), encoding="utf-8") as fh:
            text = fh.read()
        self.assertNotIn("`cambium` CLI", text)

    def test_every_skill_still_has_a_name_and_description(self):
        for name in sorted(os.listdir(SKILLS)):
            path = os.path.join(SKILLS, name, "SKILL.md")
            if not os.path.isfile(path):
                continue
            with open(path, encoding="utf-8") as fh:
                head = fh.read(1200)
            self.assertIn("name: %s" % name, head, name)
            self.assertIn("description:", head, name)


if __name__ == "__main__":
    unittest.main()

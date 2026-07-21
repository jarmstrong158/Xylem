"""Fetch-a-clean-machine tests: URL building, fetch planning, and cloning.

No network and no real git: run_fetch takes an injected runner, and plan_fetch
is driven by pointing $XYLEM_PARENT at a temp dir. Stdlib unittest only.
"""
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import installer  # noqa: E402


def _stdio(name, dirname, ref=None):
    return {
        "name": name,
        "transport": "stdio",
        "available": True,
        "command": "$PYTHON",
        "args": ["$XYLEM_PARENT/%s/server.py" % dirname],
        "source": {"repo": "owner/%s" % dirname, "dir": dirname, "ref": ref},
    }


class CloneUrlTest(unittest.TestCase):
    def test_slug_becomes_github_https_dot_git(self):
        self.assertEqual(
            installer.clone_url("jarmstrong158/context-keeper"),
            "https://github.com/jarmstrong158/context-keeper.git")

    def test_slug_already_ending_in_git_is_not_doubled(self):
        self.assertEqual(
            installer.clone_url("owner/repo.git"),
            "https://github.com/owner/repo.git")

    def test_full_url_is_passed_through_untouched(self):
        url = "https://example.com/mirror/repo.git"
        self.assertEqual(installer.clone_url(url), url)

    def test_scp_style_git_url_passes_through(self):
        url = "git@github.com:owner/repo.git"
        self.assertEqual(installer.clone_url(url), url)

    def test_base_override(self):
        self.assertEqual(
            installer.clone_url("owner/repo", base="https://gitlab.com"),
            "https://gitlab.com/owner/repo.git")

    def test_source_base_env_override(self):
        old = os.environ.get("XYLEM_SOURCE_BASE")
        os.environ["XYLEM_SOURCE_BASE"] = "https://git.internal/"
        try:
            self.assertEqual(
                installer.clone_url("owner/repo"),
                "https://git.internal/owner/repo.git")
        finally:
            if old is None:
                del os.environ["XYLEM_SOURCE_BASE"]
            else:
                os.environ["XYLEM_SOURCE_BASE"] = old


class PlanFetchTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.mapping = {"$XYLEM_PARENT": installer.to_fwd(self.tmp)}
        self.manifest = {"servers": [
            _stdio("context-keeper", "context-keeper"),
            _stdio("agentsync", "agentsync"),
            {"name": "agentsync-remote", "transport": "http",
             "available": True, "url_env_key": "AGENTSYNC_REMOTE_URL"},
        ]}

    def _make_server(self, dirname):
        d = os.path.join(self.tmp, dirname)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "server.py"), "w") as fh:
            fh.write("# present\n")

    def test_missing_servers_are_needed(self):
        actions = installer.plan_fetch(self.manifest, self.mapping)
        by = {a["name"]: a for a in actions}
        self.assertTrue(by["context-keeper"]["needed"])
        self.assertTrue(by["agentsync"]["needed"])

    def test_http_servers_are_not_in_the_fetch_plan(self):
        names = [a["name"] for a in
                 installer.plan_fetch(self.manifest, self.mapping)]
        self.assertNotIn("agentsync-remote", names)

    def test_present_server_is_not_needed(self):
        self._make_server("context-keeper")
        by = {a["name"]: a for a in
              installer.plan_fetch(self.manifest, self.mapping)}
        self.assertFalse(by["context-keeper"]["needed"])
        self.assertTrue(by["agentsync"]["needed"])

    def test_dest_is_under_the_mapped_parent(self):
        # Fetch destination must share $XYLEM_PARENT with the registered script
        # path, or the installer would clone one place and register another.
        by = {a["name"]: a for a in
              installer.plan_fetch(self.manifest, self.mapping)}
        dest = by["context-keeper"]["dest"]
        script = by["context-keeper"]["script"]
        self.assertTrue(script.startswith(dest))
        self.assertTrue(dest.startswith(installer.to_fwd(self.tmp)))

    def test_server_without_source_is_skipped(self):
        manifest = {"servers": [{
            "name": "no-source", "transport": "stdio", "available": True,
            "command": "$PYTHON", "args": ["$XYLEM_PARENT/x/server.py"],
        }]}
        self.assertEqual(installer.plan_fetch(manifest, self.mapping), [])


class RunFetchTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.mapping = {"$XYLEM_PARENT": installer.to_fwd(self.tmp)}
        self.manifest = {"servers": [_stdio("cambium", "cambium")]}
        self.actions = installer.plan_fetch(self.manifest, self.mapping)

    def test_dry_run_reports_would_clone_and_does_not_call_git(self):
        calls = []

        def runner(args, cwd):
            calls.append(args)
            return True, ""

        msgs = installer.run_fetch(self.actions, apply=False, runner=runner)
        self.assertEqual(calls, [])
        self.assertTrue(any("would clone" in m for m in msgs))

    def test_apply_invokes_git_clone_with_url_and_dest(self):
        calls = []

        def runner(args, cwd):
            calls.append(args)
            return True, ""

        installer.run_fetch(self.actions, apply=True, runner=runner)
        self.assertEqual(len(calls), 1)
        args = calls[0]
        self.assertEqual(args[0], "clone")
        self.assertIn("https://github.com/owner/cambium.git", args)
        self.assertIn(self.actions[0]["dest"], args)

    def test_apply_with_ref_passes_branch(self):
        manifest = {"servers": [_stdio("cambium", "cambium", ref="v0.9.0")]}
        actions = installer.plan_fetch(manifest, self.mapping)
        calls = []
        installer.run_fetch(actions, apply=True,
                            runner=lambda a, c: calls.append(a) or (True, ""))
        self.assertIn("--branch", calls[0])
        self.assertIn("v0.9.0", calls[0])

    def test_clone_failure_warns_but_does_not_raise(self):
        warnings = []
        installer.run_fetch(
            self.actions, apply=True,
            runner=lambda a, c: (False, "fatal: repository not found"),
            warn=warnings.append)
        self.assertEqual(len(warnings), 1)
        self.assertIn("could not clone", warnings[0])

    def test_nothing_needed_is_a_noop(self):
        os.makedirs(os.path.join(self.tmp, "cambium"))
        with open(os.path.join(self.tmp, "cambium", "server.py"), "w") as fh:
            fh.write("# here\n")
        actions = installer.plan_fetch(self.manifest, self.mapping)
        calls = []
        installer.run_fetch(actions, apply=True,
                            runner=lambda a, c: calls.append(a) or (True, ""))
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()

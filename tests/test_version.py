"""Version signals: stamp parse/compare, nudge injection vs silence, offline
fetch fail-soft, and the installer `update` verb round-trip on a synthetic
config. Stdlib unittest only -- no network, no touching the real ~/.claude."""
import argparse
import contextlib
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import installer  # noqa: E402
from installer import (  # noqa: E402
    parse_fence_version,
    manifest_version,
    apply_fence,
    fence_begin,
    FENCE_BEGIN,
    FENCE_END,
)


def _load_version_check():
    """Import artifacts/version_check.py as an isolated module."""
    path = os.path.join(ROOT, "artifacts", "version_check.py")
    spec = importlib.util.spec_from_file_location("xylem_version_check", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


vc = _load_version_check()

BLOCK_BODY = "\n## Xylem discipline\n\nbody\n"
V1_BLOCK = FENCE_BEGIN + BLOCK_BODY + FENCE_END
V2_BLOCK = fence_begin(2) + BLOCK_BODY + FENCE_END


# --------------------------------------------------------------------------
# Stamp parse + compare (installer helpers)
# --------------------------------------------------------------------------

class StampParseTest(unittest.TestCase):
    def test_parse_versioned_marker(self):
        self.assertEqual(parse_fence_version(V2_BLOCK), 2)

    def test_parse_unstamped_block_is_v1(self):
        self.assertEqual(parse_fence_version(V1_BLOCK), 1)

    def test_parse_no_fence_is_none(self):
        self.assertIsNone(parse_fence_version("# just notes\n"))

    def test_parse_high_version(self):
        text = fence_begin(42) + "\nx\n" + FENCE_END
        self.assertEqual(parse_fence_version(text), 42)

    def test_manifest_version_int(self):
        self.assertEqual(manifest_version({"version": 3}), 3)

    def test_manifest_version_numeric_string(self):
        self.assertEqual(manifest_version({"version": "5"}), 5)

    def test_manifest_version_missing_defaults_to_1(self):
        self.assertEqual(manifest_version({}), 1)

    def test_manifest_version_garbage_defaults_to_1(self):
        self.assertEqual(manifest_version({"version": "nope"}), 1)

    def test_real_manifest_is_v2_or_higher(self):
        with open(os.path.join(ROOT, "manifest.json"), encoding="utf-8") as fh:
            manifest = json.load(fh)
        self.assertIsInstance(manifest["version"], int)
        self.assertGreaterEqual(manifest_version(manifest), 2)

    def test_shipped_block_carries_v2_stamp(self):
        with open(os.path.join(ROOT, "artifacts", "claude_md_block.md"),
                  encoding="utf-8") as fh:
            block = fh.read()
        self.assertEqual(parse_fence_version(block), 2)


# --------------------------------------------------------------------------
# Hook-side compare (version_check.py) -- nudge vs silence, fail-soft
# --------------------------------------------------------------------------

class VersionCheckTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="xylem-vc-")
        self.clone = os.path.join(self.tmp, "clone")
        os.makedirs(self.clone)
        self.claude_md = os.path.join(self.tmp, "CLAUDE.md")
        self._saved_env = {}
        # Isolate from any real config: only inspect our synthetic target.
        self._set_env("XYLEM_CHECK_TARGETS", self.claude_md)
        self._set_env("XYLEM_ROOT", self.clone)
        self._set_env("XYLEM_FETCH_ON_CHECK", "0")  # no network by default

    def tearDown(self):
        for key, old in self._saved_env.items():
            if old is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old

    def _set_env(self, key, value):
        if key not in self._saved_env:
            self._saved_env[key] = os.environ.get(key)
        os.environ[key] = value

    def _write_template(self, version):
        with open(os.path.join(self.clone, "manifest.json"), "w",
                  encoding="utf-8") as fh:
            json.dump({"version": version, "servers": []}, fh)

    def _write_block(self, text):
        with open(self.claude_md, "w", encoding="utf-8") as fh:
            fh.write("# User notes\n\n" + text + "\n")

    def _run(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = vc.main()
        return rc, buf.getvalue()

    def test_mismatch_emits_single_nudge_line(self):
        self._write_template(2)
        self._write_block(V1_BLOCK)
        rc, out = self._run()
        self.assertEqual(rc, 0)
        lines = [ln for ln in out.splitlines() if ln.strip()]
        self.assertEqual(len(lines), 1)
        self.assertIn("v2 available", lines[0])
        self.assertIn("installed v1", lines[0])
        self.assertIn("xylem update", lines[0])

    def test_match_is_silent(self):
        self._write_template(2)
        self._write_block(V2_BLOCK)
        rc, out = self._run()
        self.assertEqual(rc, 0)
        self.assertEqual(out, "")

    def test_installed_newer_than_template_is_silent(self):
        self._write_template(2)
        self._write_block(fence_begin(3) + "\nx\n" + FENCE_END)
        rc, out = self._run()
        self.assertEqual(out, "")

    def test_no_block_present_is_silent(self):
        self._write_template(2)
        self._write_block("no fence here")
        rc, out = self._run()
        self.assertEqual(out, "")

    def test_unstamped_block_counts_as_stale(self):
        # A deployed block with no stamp is v1 -> stale against a v2 template.
        self._write_template(2)
        self._write_block(V1_BLOCK)
        _, out = self._run()
        self.assertIn("installed v1", out)

    def test_missing_clone_is_silent(self):
        # Template unreadable -> stay quiet rather than guess.
        self._write_block(V1_BLOCK)  # no manifest written to clone
        rc, out = self._run()
        self.assertEqual(rc, 0)
        self.assertEqual(out, "")

    def test_offline_fetch_fails_soft_and_still_compares(self):
        # Fetch enabled but the clone is not a git repo: fetch fails, and we
        # fall back to the working-tree manifest and still emit the nudge.
        self._set_env("XYLEM_FETCH_ON_CHECK", "1")
        self._write_template(2)
        self._write_block(V1_BLOCK)
        rc, out = self._run()
        self.assertEqual(rc, 0)
        self.assertIn("v2 available", out)

    def test_lowest_version_among_targets_is_reported(self):
        # Two loaded blocks: a current global and a stale repo copy. The stale
        # one must win so the nudge still fires.
        other = os.path.join(self.tmp, "repo_CLAUDE.md")
        with open(other, "w", encoding="utf-8") as fh:
            fh.write(V1_BLOCK)
        self._set_env("XYLEM_CHECK_TARGETS", os.pathsep.join([self.claude_md, other]))
        self._write_template(2)
        self._write_block(V2_BLOCK)  # this one is current
        _, out = self._run()
        self.assertIn("installed v1", out)  # stale repo copy caught


# --------------------------------------------------------------------------
# Installer `update` verb round-trip on a synthetic config
# --------------------------------------------------------------------------

class UpdateVerbTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="xylem-update-")
        self.claude_dir = os.path.join(self.tmp, "claude")
        os.makedirs(self.claude_dir)
        self.claude_md = os.path.join(self.claude_dir, "CLAUDE.md")

        # Redirect the installer at our synthetic Claude config dir and stub the
        # git pull so the round-trip touches nothing real and needs no network.
        self._orig_detect = installer.detect_claude_dir
        self._orig_git = installer._run_git
        installer.detect_claude_dir = lambda: self.claude_dir
        installer._run_git = lambda *a, **k: (True, "Already up to date.")

    def tearDown(self):
        installer.detect_claude_dir = self._orig_detect
        installer._run_git = self._orig_git

    def _args(self):
        return argparse.Namespace(
            command="update", dry_run=False, uninstall=False, project=None)

    def _run_update(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = installer.run_update(self._args())
        return rc, buf.getvalue()

    def test_update_stamps_stale_block_to_current_version(self):
        expected = manifest_version(installer.load_manifest())
        # Pre-existing legacy (unstamped) block plus user content.
        with open(self.claude_md, "w", encoding="utf-8") as fh:
            fh.write("# My CLAUDE.md\n\nkeep me\n\n" + V1_BLOCK + "\n")

        rc, out = self._run_update()
        self.assertEqual(rc, 0)

        with open(self.claude_md, encoding="utf-8") as fh:
            result = fh.read()
        self.assertEqual(parse_fence_version(result), expected)
        self.assertIn("keep me", result)            # user content preserved
        self.assertEqual(result.count(FENCE_END), 1)  # no duplicate fences
        self.assertIn("update v1 -> v%d" % expected, out)

    def test_update_is_idempotent(self):
        with open(self.claude_md, "w", encoding="utf-8") as fh:
            fh.write("# My CLAUDE.md\n\nkeep me\n\n" + V1_BLOCK + "\n")
        self._run_update()          # v1 -> current
        _, out = self._run_update()  # current -> current
        self.assertIn("already up to date", out)

    def test_update_on_fresh_machine_creates_stamped_block(self):
        expected = manifest_version(installer.load_manifest())
        # No CLAUDE.md at all yet.
        rc, out = self._run_update()
        self.assertEqual(rc, 0)
        with open(self.claude_md, encoding="utf-8") as fh:
            result = fh.read()
        self.assertEqual(parse_fence_version(result), expected)

    def test_update_survives_git_pull_failure(self):
        # git pull fails (offline) -> still re-applies from local checkout.
        installer._run_git = lambda *a, **k: (False, "could not resolve host")
        with open(self.claude_md, "w", encoding="utf-8") as fh:
            fh.write(V1_BLOCK + "\n")
        rc, out = self._run_update()
        self.assertEqual(rc, 0)
        with open(self.claude_md, encoding="utf-8") as fh:
            self.assertIsNotNone(parse_fence_version(fh.read()))


if __name__ == "__main__":
    unittest.main()

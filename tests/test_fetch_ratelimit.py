"""The version-check fetch rate limiter (docs/versioning.md).

docs/versioning.md described a rate limiter that did not exist. It claimed the
upstream `git fetch` was off by default, needed XYLEM_FETCH_ON_CHECK=1, and was
capped at "once every 24 hours, 3-second timeout". In fact the fetch defaulted
ON, ran with a 10-second timeout, and kept no cache file at all -- so every
single SessionStart paid an unbounded network round-trip inside a 10-second hook
budget, on whatever network the user happened to be on.

The doc was the design and the code was the bug, so the code moved. These tests
hold it there, and the last class holds the doc to the code so the two cannot
drift apart again.

Stdlib unittest only. No network: `_run_git` is replaced throughout.
"""

import contextlib
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import installer  # noqa: E402
from installer import FENCE_BEGIN, FENCE_END  # noqa: E402


def _load_version_check():
    path = os.path.join(ROOT, "artifacts", "version_check.py")
    spec = importlib.util.spec_from_file_location("xylem_fetch_version_check", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


vc = _load_version_check()

V1_BLOCK = FENCE_BEGIN + "\n## Xylem discipline\n\nbody\n" + FENCE_END

FETCH_KEYS = (
    "XYLEM_FETCH_ON_CHECK", "XYLEM_FETCH_INTERVAL", "XYLEM_FETCH_TIMEOUT",
    "XYLEM_FETCH_STAMP", "XYLEM_FETCH_REF", "XYLEM_CHECK_TARGETS", "XYLEM_ROOT",
)


class FetchBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="xylem-fetch-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.clone = os.path.join(self.tmp, "clone")
        os.makedirs(os.path.join(self.clone, ".git"))
        with open(os.path.join(self.clone, "manifest.json"), "w", encoding="utf-8") as fh:
            json.dump({"version": 4, "servers": []}, fh)
        for key in FETCH_KEYS:
            old = os.environ.pop(key, None)
            if old is not None:
                self.addCleanup(os.environ.__setitem__, key, old)

    def set_env(self, key, value):
        os.environ[key] = value
        self.addCleanup(os.environ.pop, key, None)

    def fake_git(self, ok=False, out=""):
        """Replace _run_git; return the list it records calls into."""
        calls = []
        real = vc._run_git

        def fake(args, cwd, timeout=10):
            calls.append({"args": list(args), "cwd": cwd, "timeout": timeout})
            return ok, out

        vc._run_git = fake
        self.addCleanup(setattr, vc, "_run_git", real)
        return calls


class FetchIsOffByDefault(FetchBase):
    def test_the_default_is_off(self):
        self.assertIs(vc.FETCH_DEFAULT_ON, False)
        self.assertFalse(vc.fetch_enabled({}))

    def test_only_an_explicit_opt_in_turns_it_on(self):
        for raw in ("1", "true", "TRUE", "yes", "on"):
            self.assertTrue(vc.fetch_enabled({"XYLEM_FETCH_ON_CHECK": raw}), raw)
        for raw in ("0", "", "no", "false", "off", "   "):
            self.assertFalse(vc.fetch_enabled({"XYLEM_FETCH_ON_CHECK": raw}), raw)

    def test_a_default_session_start_does_no_network_io_at_all(self):
        # The whole point of the change.
        calls = self.fake_git()
        self.assertEqual(vc._template_version(self.clone), 4)
        self.assertEqual(calls, [])

    def test_the_stale_block_comparison_still_runs_with_the_fetch_off(self):
        # The load-bearing promise of turning it off: a stale block is still
        # caught on every session, from the local clone.
        claude_md = os.path.join(self.tmp, "CLAUDE.md")
        with open(claude_md, "w", encoding="utf-8") as fh:
            fh.write(V1_BLOCK + "\n")
        self.set_env("XYLEM_CHECK_TARGETS", claude_md)
        self.set_env("XYLEM_ROOT", self.clone)
        self.fake_git()
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(vc.main(), 0)
        self.assertIn("xylem habit layer v4 available", buf.getvalue())


class FetchHappensAtMostOncePerInterval(FetchBase):
    def test_the_default_interval_is_24_hours(self):
        self.assertEqual(vc.FETCH_INTERVAL_DEFAULT, 24 * 60 * 60)

    def test_due_when_no_fetch_has_ever_happened(self):
        self.assertTrue(vc.fetch_is_due(self.clone))

    def test_not_due_again_within_the_interval(self):
        now = 1000000.0
        vc._record_fetch(vc.stamp_path(self.clone), now)
        self.assertFalse(vc.fetch_is_due(self.clone, now=now + 60))
        self.assertFalse(vc.fetch_is_due(self.clone, now=now + 23 * 3600))

    def test_due_again_once_the_interval_has_passed(self):
        now = 1000000.0
        vc._record_fetch(vc.stamp_path(self.clone), now)
        self.assertTrue(vc.fetch_is_due(self.clone, now=now + 24 * 3600 + 1))

    def test_a_zero_interval_means_every_session(self):
        vc._record_fetch(vc.stamp_path(self.clone), 1000000.0)
        self.assertTrue(vc.fetch_is_due(self.clone, now=1000001.0, interval=0))

    def test_a_stamp_from_the_future_does_not_wedge_it_forever(self):
        # A clock that jumped back, or a restored backup.
        vc._record_fetch(vc.stamp_path(self.clone), 9000000000.0)
        self.assertTrue(vc.fetch_is_due(self.clone, now=1000000.0))

    def test_a_corrupt_stamp_is_treated_as_no_stamp(self):
        with open(vc.stamp_path(self.clone), "w", encoding="utf-8") as fh:
            fh.write("not a number")
        self.assertTrue(vc.fetch_is_due(self.clone))

    def test_the_second_session_of_the_day_does_not_fetch(self):
        calls = self.fake_git()  # offline: falls back to the working tree
        self.set_env("XYLEM_FETCH_ON_CHECK", "1")

        self.assertEqual(vc._template_version(self.clone), 4)
        self.assertEqual([c["args"][0] for c in calls], ["fetch"])

        self.assertEqual(vc._template_version(self.clone), 4)
        self.assertEqual(
            [c["args"][0] for c in calls], ["fetch"],
            "the second session fetched again; the rate limit is not holding")

    def test_a_failed_or_hung_fetch_is_still_rate_limited(self):
        # The stamp is written BEFORE the attempt. An attempt that hangs to its
        # timeout is exactly the one worth suppressing, and stamping only on
        # success would retry the worst case on every single session.
        self.fake_git(ok=False)
        self.set_env("XYLEM_FETCH_ON_CHECK", "1")
        vc._template_version(self.clone)
        self.assertFalse(vc.fetch_is_due(self.clone))


class FetchIsTimeBounded(FetchBase):
    def test_the_default_timeout_is_three_seconds(self):
        self.assertEqual(vc.FETCH_TIMEOUT_DEFAULT, 3)

    def test_the_fetch_is_actually_invoked_with_that_timeout(self):
        calls = self.fake_git()
        self.set_env("XYLEM_FETCH_ON_CHECK", "1")
        vc._template_version(self.clone)
        fetches = [c for c in calls if c["args"][0] == "fetch"]
        self.assertEqual(len(fetches), 1)
        self.assertEqual(fetches[0]["timeout"], vc.FETCH_TIMEOUT_DEFAULT)

    def test_the_timeout_fits_well_inside_the_session_start_hook_budget(self):
        # This check runs as a SessionStart hook; blowing the budget is the
        # failure mode the limiter exists to prevent.
        self.assertLess(vc.FETCH_TIMEOUT_DEFAULT, installer.HOOK_TIMEOUT)

    def test_a_custom_timeout_is_honoured(self):
        calls = self.fake_git()
        self.set_env("XYLEM_FETCH_ON_CHECK", "1")
        self.set_env("XYLEM_FETCH_TIMEOUT", "7")
        vc._template_version(self.clone)
        self.assertEqual(calls[0]["timeout"], 7)

    def test_garbage_knob_values_fall_back_to_the_default(self):
        for raw in ("banana", "-5", "", "3.5"):
            self.set_env("XYLEM_FETCH_INTERVAL", raw)
            self.assertEqual(
                vc._env_int("XYLEM_FETCH_INTERVAL", vc.FETCH_INTERVAL_DEFAULT),
                vc.FETCH_INTERVAL_DEFAULT, raw)


class TheStampFile(FetchBase):
    def test_it_lives_inside_dot_git_so_it_is_never_committed(self):
        path = vc.stamp_path(self.clone)
        self.assertEqual(os.path.dirname(path), os.path.join(self.clone, ".git"))
        self.assertEqual(os.path.basename(path), vc.STAMP_FILENAME)

    def test_two_clones_rate_limit_independently(self):
        other = os.path.join(self.tmp, "other-clone")
        os.makedirs(os.path.join(other, ".git"))
        self.assertNotEqual(vc.stamp_path(self.clone), vc.stamp_path(other))

    def test_a_checkout_with_no_git_directory_still_gets_a_stamp_path(self):
        # A tarball export, or a worktree (whose .git is a FILE).
        export = os.path.join(self.tmp, "tarball-export")
        os.makedirs(export)
        path = vc.stamp_path(export)
        self.assertTrue(path)
        self.assertFalse(path.startswith(export))

    def test_an_explicit_override_wins(self):
        want = os.path.join(self.tmp, "elsewhere", "stamp")
        self.set_env("XYLEM_FETCH_STAMP", want)
        self.assertEqual(vc.stamp_path(self.clone), want)

    def test_it_creates_a_missing_parent_directory(self):
        want = os.path.join(self.tmp, "made", "up", "stamp")
        vc._record_fetch(want, 1234.0)
        self.assertTrue(os.path.isfile(want))

    def test_an_unwritable_location_degrades_but_never_raises(self):
        # Losing the cache means "no rate limiting", never "failed session".
        blocked = os.path.join(self.clone, "manifest.json", "nope", "stamp")
        vc._record_fetch(blocked, 1.0)  # must not raise
        self.assertIsNone(vc._last_fetch(blocked))


class TheDocMatchesTheCode(unittest.TestCase):
    """The doc is why this feature exists; keep the two honest together."""

    def setUp(self):
        with open(os.path.join(ROOT, "docs", "versioning.md"), encoding="utf-8") as fh:
            self.doc = fh.read()

    def test_the_doc_still_says_off_by_default_and_the_code_agrees(self):
        self.assertIn("off by default", self.doc)
        self.assertFalse(vc.fetch_enabled({}))

    def test_every_knob_the_code_reads_is_documented_in_both_places(self):
        for key in ("XYLEM_FETCH_ON_CHECK", "XYLEM_FETCH_INTERVAL",
                    "XYLEM_FETCH_TIMEOUT", "XYLEM_FETCH_STAMP"):
            self.assertIn(key, self.doc, "%s undocumented in docs/versioning.md" % key)
            self.assertIn(key, vc.__doc__, "%s undocumented in version_check.py" % key)

    def test_the_documented_numbers_are_the_real_numbers(self):
        self.assertIn("once every 24 hours", self.doc)
        self.assertEqual(vc.FETCH_INTERVAL_DEFAULT, 24 * 60 * 60)
        self.assertIn("3-second timeout", self.doc)
        self.assertEqual(vc.FETCH_TIMEOUT_DEFAULT, 3)

    def test_the_documented_stamp_filename_is_the_real_one(self):
        self.assertIn(vc.STAMP_FILENAME, self.doc)


if __name__ == "__main__":
    unittest.main()

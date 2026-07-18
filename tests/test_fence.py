"""Fence insert/replace/remove + round-trip idempotency. Stdlib unittest only."""
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from installer import (  # noqa: E402
    apply_fence,
    remove_fence,
    detect_style,
    fence_begin,
    FENCE_BEGIN,
    FENCE_END,
)

BLOCK = FENCE_BEGIN + "\nXylem content here\n" + FENCE_END
V2_BLOCK = fence_begin(2) + "\nXylem content here\n" + FENCE_END


class FenceTest(unittest.TestCase):
    def test_insert_into_empty_file(self):
        out = apply_fence("", BLOCK)
        self.assertIn(FENCE_BEGIN, out)
        self.assertIn(FENCE_END, out)
        self.assertIn("Xylem content here", out)

    def test_append_preserves_existing_content(self):
        original = "# My notes\n\nSome existing text.\n"
        out = apply_fence(original, BLOCK)
        self.assertTrue(out.startswith("# My notes"))
        self.assertIn("Some existing text.", out)
        self.assertIn(FENCE_BEGIN, out)

    def test_replace_existing_fence_updates_content(self):
        original = apply_fence("# Notes\n", FENCE_BEGIN + "\nOLD\n" + FENCE_END)
        updated = apply_fence(original, FENCE_BEGIN + "\nNEW\n" + FENCE_END)
        self.assertIn("NEW", updated)
        self.assertNotIn("OLD", updated)
        # Exactly one fence pair remains.
        self.assertEqual(updated.count(FENCE_BEGIN), 1)
        self.assertEqual(updated.count(FENCE_END), 1)

    def test_replace_does_not_duplicate(self):
        text = ""
        for _ in range(5):
            text = apply_fence(text, BLOCK)
        self.assertEqual(text.count(FENCE_BEGIN), 1)
        self.assertEqual(text.count(FENCE_END), 1)

    def test_apply_is_idempotent(self):
        once = apply_fence("# Notes\n\nbody\n", BLOCK)
        twice = apply_fence(once, BLOCK)
        self.assertEqual(once, twice)

    def test_uninstall_removes_fence(self):
        original = "# Notes\n\nbody\n"
        installed = apply_fence(original, BLOCK)
        removed = remove_fence(installed)
        self.assertNotIn(FENCE_BEGIN, removed)
        self.assertNotIn("Xylem content here", removed)
        self.assertIn("body", removed)

    def test_round_trip_restores_original_with_prior_content(self):
        original = "# Notes\n\nbody\n"
        self.assertEqual(remove_fence(apply_fence(original, BLOCK)), original)

    def test_round_trip_empty_file(self):
        self.assertEqual(remove_fence(apply_fence("", BLOCK)), "")

    def test_remove_on_file_without_fence_is_noop(self):
        text = "# Just notes, no fence\n"
        self.assertEqual(remove_fence(text), text)

    # -- versioned fence markers -------------------------------------------

    def test_version_arg_stamps_begin_marker(self):
        out = apply_fence("", BLOCK, version=2)
        self.assertIn("<!-- XYLEM:BEGIN v2 -->", out)
        self.assertNotIn(FENCE_BEGIN, out)  # unstamped marker gone
        self.assertIn(FENCE_END, out)

    def test_replace_unstamped_block_with_versioned(self):
        original = apply_fence("# Notes\n", BLOCK)  # legacy, no stamp
        updated = apply_fence(original, BLOCK, version=2)
        self.assertIn("<!-- XYLEM:BEGIN v2 -->", updated)
        self.assertEqual(updated.count(FENCE_END), 1)
        # Exactly one begin marker of any form remains.
        self.assertEqual(updated.count("<!-- XYLEM:BEGIN"), 1)

    def test_replace_versioned_block_with_newer_version(self):
        original = apply_fence("# Notes\n", V2_BLOCK, version=2)
        updated = apply_fence(original, V2_BLOCK, version=3)
        self.assertIn("<!-- XYLEM:BEGIN v3 -->", updated)
        self.assertNotIn("<!-- XYLEM:BEGIN v2 -->", updated)
        self.assertEqual(updated.count("<!-- XYLEM:BEGIN"), 1)

    def test_remove_strips_versioned_fence(self):
        installed = apply_fence("# Notes\n\nbody\n", BLOCK, version=2)
        removed = remove_fence(installed)
        self.assertNotIn("XYLEM:BEGIN", removed)
        self.assertIn("body", removed)


class MultipleFenceGuardTest(unittest.TestCase):
    """Every fence operation works on the FIRST block only. A duplicate block
    would sit orphaned forever, so it must be reported loudly."""

    TWO_BLOCKS = BLOCK + "\n\nnotes\n\n" + BLOCK

    def test_apply_warns_on_two_blocks(self):
        warnings = []
        apply_fence(self.TWO_BLOCKS, BLOCK, warn=warnings.append)
        self.assertEqual(len(warnings), 1)
        self.assertIn("only the first block is managed", warnings[0])

    def test_remove_warns_on_two_blocks(self):
        warnings = []
        remove_fence(self.TWO_BLOCKS, warn=warnings.append)
        self.assertEqual(len(warnings), 1)

    def test_single_block_is_silent(self):
        warnings = []
        apply_fence("# notes\n\n" + BLOCK + "\n", BLOCK, warn=warnings.append)
        remove_fence(BLOCK, warn=warnings.append)
        self.assertEqual(warnings, [])

    def test_warning_is_optional(self):
        # No warn callback -> still behaves, just silently.
        self.assertIn(FENCE_END, apply_fence(self.TWO_BLOCKS, BLOCK))


class FileStyleTest(unittest.TestCase):
    """detect_style() recovers what read_text()'s universal newlines discard."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="xylem-style-")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def _write(self, data):
        path = os.path.join(self.tmp, "f.md")
        with open(path, "wb") as fh:
            fh.write(data)
        return path

    def test_crlf_detected(self):
        self.assertEqual(detect_style(self._write(b"a\r\nb\r\n")),
                         ("\r\n", False))

    def test_lf_detected(self):
        self.assertEqual(detect_style(self._write(b"a\nb\n")), ("\n", False))

    def test_bom_detected(self):
        newline, bom = detect_style(self._write(b"\xef\xbb\xbfa\n"))
        self.assertTrue(bom)
        self.assertEqual(newline, "\n")

    def test_mixed_newlines_take_the_dominant_one(self):
        self.assertEqual(detect_style(self._write(b"a\r\nb\r\nc\n"))[0], "\r\n")

    def test_missing_file_defaults_to_lf(self):
        self.assertEqual(detect_style(os.path.join(self.tmp, "nope")),
                         ("\n", False))



if __name__ == "__main__":
    unittest.main()

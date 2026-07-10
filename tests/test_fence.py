"""Fence insert/replace/remove + round-trip idempotency. Stdlib unittest only."""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from installer import (  # noqa: E402
    apply_fence,
    remove_fence,
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


if __name__ == "__main__":
    unittest.main()

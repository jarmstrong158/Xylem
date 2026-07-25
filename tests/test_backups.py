"""installer.py backup hygiene: prune the old ones, lock down the rest.

install/xylem_install.py already pruned its backups and chmod'd them to 0600,
on the grounds that a backup of a config is a working copy of that config's
credentials. installer.py -- the path most people actually run -- did neither.
A real ~/.claude accumulated four-plus `.xylem-backup*` files, world-readable,
each one holding the Worker connector URLs, which for these Workers ARE the
token (they authenticate on the URL path, /mcp/<token>).

The pruning policy is deliberately two-condition: beyond the newest N AND older
than the age limit. Age alone deletes the only copy of a hand edit on a machine
that has not been touched in a month; count alone deletes this morning's edits
after three re-runs of the installer.

Stdlib unittest only.
"""

import os
import shutil
import stat
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import installer  # noqa: E402

DAY = 86400


class BackupBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="xylem-bak-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.path = os.path.join(self.tmp, "settings.json")
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write('{"mcpServers": {}}\n')

    def make_snapshot(self, age_days, body="{}"):
        """Create a timestamped snapshot `age_days` old. Returns its path."""
        stamp = int(time.time() - age_days * DAY)
        p = "%s%s.%d" % (self.path, installer.BACKUP_SUFFIX, stamp)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(body)
        return p

    def pristine(self):
        p = self.path + installer.BACKUP_SUFFIX
        with open(p, "w", encoding="utf-8") as fh:
            fh.write("{}")
        return p


class DiscoveringBackups(BackupBase):
    def test_finds_only_this_installers_snapshots(self):
        mine = self.make_snapshot(1)
        for foreign in ("settings.json.bak", "settings.json.orig",
                        "settings.json.xylem-backup.notanumber",
                        "other.json.xylem-backup.1700000000"):
            open(os.path.join(self.tmp, foreign), "w").close()
        self.assertEqual(installer.xylem_backups(self.path), [mine])

    def test_the_pristine_backup_is_never_listed(self):
        # It is the only copy of what existed before Xylem touched the file,
        # there is exactly one of it, and it is not what grows without bound.
        self.pristine()
        self.assertEqual(installer.xylem_backups(self.path), [])

    def test_snapshots_come_back_newest_first(self):
        old = self.make_snapshot(30)
        mid = self.make_snapshot(10)
        new = self.make_snapshot(1)
        self.assertEqual(installer.xylem_backups(self.path), [new, mid, old])

    def test_a_missing_directory_is_empty_not_an_error(self):
        self.assertEqual(
            installer.xylem_backups(os.path.join(self.tmp, "gone", "x.json")), [])


class PruningBackups(BackupBase):
    def test_recent_snapshots_survive_however_many_there_are(self):
        recent = [self.make_snapshot(i) for i in range(10)]
        self.assertEqual(installer.prune_backups(self.path), 0)
        for p in recent:
            self.assertTrue(os.path.exists(p), p)

    def test_old_snapshots_beyond_the_keep_count_are_removed(self):
        keep = [self.make_snapshot(1), self.make_snapshot(2), self.make_snapshot(3)]
        stale = [self.make_snapshot(60), self.make_snapshot(90)]
        self.assertEqual(installer.prune_backups(self.path), 2)
        for p in keep:
            self.assertTrue(os.path.exists(p), p)
        for p in stale:
            self.assertFalse(os.path.exists(p), p)

    def test_the_newest_are_kept_even_when_all_are_ancient(self):
        # Count alone is not sufficient grounds to delete; neither is age.
        ancient = [self.make_snapshot(100 + i) for i in range(6)]
        installer.prune_backups(self.path)
        survivors = installer.xylem_backups(self.path)
        self.assertEqual(len(survivors), installer.BACKUP_KEEP)
        # And they are the newest ones.
        self.assertEqual(survivors, sorted(ancient, reverse=True)[:installer.BACKUP_KEEP])

    def test_the_pristine_backup_is_never_pruned(self):
        pristine = self.pristine()
        for i in range(8):
            self.make_snapshot(100 + i)
        installer.prune_backups(self.path)
        self.assertTrue(os.path.exists(pristine))

    def test_foreign_files_are_never_touched(self):
        foreign = os.path.join(self.tmp, "settings.json.bak")
        open(foreign, "w").close()
        for i in range(8):
            self.make_snapshot(100 + i)
        installer.prune_backups(self.path)
        self.assertTrue(os.path.exists(foreign))

    def test_age_is_read_from_the_filename_not_the_mtime(self):
        # shutil.copy2 PRESERVES the source file's mtime onto the copy, so a
        # backup's mtime is the config's last-edit time, not the time the backup
        # was taken. Pruning on mtime would therefore delete a snapshot taken
        # seconds ago of a config last edited a year ago.
        fresh = self.make_snapshot(0)
        for i in range(5):
            self.make_snapshot(1 + i)
        os.utime(fresh, (0, 0))  # mtime says 1970
        installer.prune_backups(self.path)
        self.assertTrue(os.path.exists(fresh))

    def test_pruning_is_idempotent(self):
        for i in range(8):
            self.make_snapshot(100 + i)
        first = installer.prune_backups(self.path)
        self.assertGreater(first, 0)
        self.assertEqual(installer.prune_backups(self.path), 0)

    def test_nothing_to_prune_is_not_an_error(self):
        self.assertEqual(installer.prune_backups(self.path), 0)


class Permissions(BackupBase):
    """0600 is best-effort by design; where modes exist, they must be right."""

    def _mode(self, path):
        return stat.S_IMODE(os.stat(path).st_mode)

    def test_secure_chmod_locks_a_file_to_its_owner(self):
        if os.name == "nt":
            self.skipTest("POSIX modes are not meaningful against Windows ACLs")
        os.chmod(self.path, 0o644)
        installer.secure_chmod(self.path)
        self.assertEqual(self._mode(self.path), installer.SECRET_MODE)

    def test_the_mode_is_actually_owner_only(self):
        self.assertEqual(installer.SECRET_MODE, 0o600)
        self.assertFalse(installer.SECRET_MODE & stat.S_IRGRP)
        self.assertFalse(installer.SECRET_MODE & stat.S_IROTH)

    def test_chmod_on_a_missing_file_never_raises(self):
        # The alternative to a best-effort chmod is no chmod at all.
        installer.secure_chmod(os.path.join(self.tmp, "does-not-exist"))

    def test_chmod_on_a_directory_path_never_raises(self):
        installer.secure_chmod(os.path.join(self.path, "not", "a", "dir"))


class WriterAppliesThePolicy(BackupBase):
    """End to end through the real writer, not the helpers in isolation."""

    def _planner(self, dry_run=False):
        return installer.Planner(dry_run, state_path=os.path.join(self.tmp, "state.json"))

    def test_the_pristine_backup_is_created_and_locked_down(self):
        plan = self._planner()
        plan.set_text(self.path, '{"mcpServers": {"a": 1}}\n')
        plan.apply()
        pristine = self.path + installer.BACKUP_SUFFIX
        self.assertTrue(os.path.exists(pristine))
        if os.name != "nt":
            self.assertEqual(stat.S_IMODE(os.stat(pristine).st_mode),
                             installer.SECRET_MODE)

    def test_a_written_json_config_is_locked_down(self):
        if os.name == "nt":
            self.skipTest("POSIX modes are not meaningful against Windows ACLs")
        plan = self._planner()
        plan.set_text(self.path, '{"mcpServers": {"a": 1}}\n')
        plan.apply()
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode),
                         installer.SECRET_MODE)

    def test_markdown_permissions_are_left_alone(self):
        # CLAUDE.md and the slash-command file are documentation, not secrets;
        # silently making a user's notes owner-only would be a surprise.
        if os.name == "nt":
            self.skipTest("POSIX modes are not meaningful against Windows ACLs")
        md = os.path.join(self.tmp, "CLAUDE.md")
        with open(md, "w", encoding="utf-8") as fh:
            fh.write("# notes\n")
        os.chmod(md, 0o644)
        plan = self._planner()
        plan.set_text(md, "# notes\n\nmore\n")
        plan.apply()
        self.assertEqual(stat.S_IMODE(os.stat(md).st_mode), 0o644)

    def test_a_dry_run_creates_no_backup_at_all(self):
        plan = self._planner(dry_run=True)
        plan.set_text(self.path, '{"mcpServers": {"a": 1}}\n')
        plan.render()
        self.assertEqual(installer.xylem_backups(self.path), [])
        self.assertFalse(os.path.exists(self.path + installer.BACKUP_SUFFIX))

    def test_repeated_hand_edits_do_not_accumulate_without_bound(self):
        # The unbounded-growth branch: an on-disk state that is neither our last
        # write nor the new text takes a timestamped snapshot. It now prunes.
        self.pristine()
        for i in range(8):
            self.make_snapshot(100 + i)
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write('{"hand": "edited"}\n')
        plan = self._planner()
        plan.set_text(self.path, '{"mcpServers": {"a": 1}}\n')
        plan.apply()
        self.assertLessEqual(len(installer.xylem_backups(self.path)),
                             installer.BACKUP_KEEP + 1)


if __name__ == "__main__":
    unittest.main()

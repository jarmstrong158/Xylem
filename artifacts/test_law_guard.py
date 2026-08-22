"""Open-rate test for law_guard.

A guardrail nobody measures is a guardrail nobody has. The first version of
this hook was assumed to work and measured 4/8 with the composition inverted:
it missed the sweep bug shipped the same day it was written, and it fired on a
Vanguard sprite generator because "vanguard" contains "guard" -- a substring
match on a project's NAME, which is exactly the failure law k-signal describes.

Every DAMAGE case below is a real edit from this repo's history that caused
real loss. Every ORDINARY case is a real edit that must stay quiet, because a
hook that fires on everything gets ignored and then protects nothing.
"""
import json
import os
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
HOOK = os.path.join(HERE, "law_guard.py")
DASH = "C:/Users/jarms/repos/xylem-dashboard/"

DAMAGE = [
    ("blind overwrite of the decision store", DASH + "tools/apply_queue.py",
     "def save_decisions(d):\n    with open(DEC,'w') as f:\n        json.dump(d,f)"),
    ("a sweep that rules work finished", DASH + "tools/apply_queue.py",
     "def sweep_finished_requests(dec):\n    if entries:\n"
     "        done = all(e.get('id') not in still for e in entries)"),
    ("a cache-first service worker document", DASH + "sw.js",
     "e.respondWith(caches.match(e.request).then(r=>r||fetch(e.request)))"),
    ("a detector matching a file against its own name", DASH + "tools/repair.py",
     "if 'train.py' in open(path).read():\n    flag_drift(entry)"),
    ("a billed CLI call", DASH + "tools/eval_runner.py",
     "subprocess.run(['claude','-p',prompt])"),
]

ORDINARY = [
    ("a sprite generator in vanguard",
     "C:/Users/jarms/repos/vanguard/scripts/gen_sprite.gd",
     "func draw_body(img: Image) -> void:\n\timg.set_pixel(4, 8, PALETTE.skin)"),
    ("a README paragraph", "C:/Users/jarms/repos/cambium/README.md",
     "Cambium distills context-keeper entries into knowledge."),
    ("a CSS rule", DASH + "app.css", ".gnode rect { fill: var(--surface-sunk); }"),
    ("a test assertion", "C:/Users/jarms/repos/cambium/test_cambium.py",
     "assert g['unsynthesized'] == ['dec-003']"),
    ("a story document",
     "C:/Users/jarms/repos/vanguard/docs/story/story_bible.md",
     "Maren is a Conduit who amplifies allies rather than casting directly."),
]


def fire(path, body):
    """Returns the laws the hook surfaced, or [] if it stayed quiet."""
    p = subprocess.run(
        [sys.executable, HOOK],
        input=json.dumps({"tool_name": "Edit",
                          "tool_input": {"file_path": path, "new_string": body}}),
        capture_output=True, text=True, timeout=20)
    out = p.stdout.strip()
    if not out:
        return []
    ctx = json.loads(out)["hookSpecificOutput"]["additionalContext"]
    return [l.strip("- ").strip() for l in ctx.splitlines()[1:]]


class TestOpenRate(unittest.TestCase):
    def test_every_known_damage_case_surfaces_a_law(self):
        missed = [name for name, path, body in DAMAGE if not fire(path, body)]
        self.assertEqual([], missed,
                         "these edits caused real loss and the hook stayed quiet")

    def test_ordinary_edits_stay_quiet(self):
        noisy = [name for name, path, body in ORDINARY if fire(path, body)]
        self.assertEqual([], noisy,
                         "a hook that fires on ordinary edits gets ignored")

    def test_a_project_name_is_not_a_signal(self):
        """'vanguard' contains 'guard'. It is not a detector."""
        self.assertEqual([], fire(
            "C:/Users/jarms/repos/vanguard/scripts/anything.gd",
            "func ready() -> void:\n\tpass"))

    def test_hook_never_blocks_an_edit(self):
        """Fails silent by contract: a memory layer must not stop work."""
        for payload in ('{"tool_name":"Edit"}', "not json at all", "{}"):
            p = subprocess.run([sys.executable, HOOK], input=payload,
                               capture_output=True, text=True, timeout=20)
            self.assertEqual(0, p.returncode, "hook must exit 0 on bad input")


if __name__ == "__main__":
    unittest.main(verbosity=2)


BASH_WRITES = [
    ("a heredoc rewriting the decision saver",
     "python - <<'PY'\nio.open('tools/apply_queue.py','w').write(s)\nPY"),
    ("sed -i on the service worker", "sed -i 's/v25/v26/' sw.js"),
]
BASH_QUIET = [
    ("git status", "git status --porcelain"),
    ("running the tests", "python -m pytest test_cambium.py -q"),
    ("listing a directory", "ls -la tools/"),
    ("reading a file", "cat README.md"),
]


def fire_bash(cmd):
    p = subprocess.run(
        [sys.executable, HOOK],
        input=json.dumps({"tool_name": "Bash", "tool_input": {"command": cmd}}),
        capture_output=True, text=True, timeout=20)
    return p.stdout.strip()


class TestBashCoverage(unittest.TestCase):
    """The hook was measured and found to be watching a door nobody used: six
    fires in a day, all from its own tests, because real edits go through
    heredocs and sed. These pin the wider coverage AND its quiet half."""

    def test_bash_edits_that_touch_guarded_code_fire(self):
        missed = [name for name, cmd in BASH_WRITES if not fire_bash(cmd)]
        self.assertEqual([], missed)

    def test_read_only_commands_never_fire(self):
        noisy = [name for name, cmd in BASH_QUIET if fire_bash(cmd)]
        self.assertEqual([], noisy,
                         "a guard that fires on git status gets ignored")


PROSE_ONLY = [
    ("a commit message mentioning sed -i and Claude",
     "git add -A && git commit -q -m \"$(cat <<'EOF'\n"
     "Coverage now includes Bash heredocs and sed -i rather than Edit.\n"
     "Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>\nEOF\n)\""),
    ("a commit message naming a store",
     "git commit -m 'fix decisions.json save path'"),
    ("echoing a sentence about deleting", "echo 'we should rm the old cache'"),
]


class TestProseIsNotCode(unittest.TestCase):
    """The hook fired on its own commit: the message said the hook had been
    missing "sed -i" and was signed "Co-Authored-By: Claude", so the scan found
    those strings in English and surfaced two irrelevant laws. Heredoc bodies
    and -m messages are prose and must never be read as commands."""

    def test_a_commit_message_is_not_a_write(self):
        noisy = [name for name, cmd in PROSE_ONLY if fire_bash(cmd)]
        self.assertEqual([], noisy)

    def test_a_real_write_alongside_a_message_still_fires(self):
        """Stripping prose must not blind the hook to the actual command."""
        self.assertTrue(fire_bash(
            "sed -i 's/v1/v2/' sw.js && git commit -m 'bump the shell'"))


class TestBreakageIsVisible(unittest.TestCase):
    def test_a_crash_is_recorded_not_swallowed(self):
        """A rename left the Bash branch calling a deleted function and the
        hook went quiet for every shell write, indistinguishable from having
        nothing to say. Crashes must still exit 0, but must leave a trace."""
        import tempfile
        env = dict(os.environ, CAMBIUM_ORG_REPO=tempfile.mkdtemp())
        p = subprocess.run([sys.executable, HOOK], input="{}",
                           capture_output=True, text=True, timeout=20, env=env)
        self.assertEqual(0, p.returncode)

    def test_the_ledger_records_a_fire(self):
        ledger = os.path.expanduser("~/.xylem/law-fires.jsonl")
        before = os.path.getsize(ledger) if os.path.exists(ledger) else 0
        fire_bash("sed -i 's/a/b/' sw.js")
        after = os.path.getsize(ledger) if os.path.exists(ledger) else 0
        self.assertGreater(after, before, "a fire must be measurable")

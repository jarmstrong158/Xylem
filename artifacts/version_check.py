#!/usr/bin/env python3
"""Xylem habit-layer version check (SessionStart hook).

Compares the version stamped into the installed CLAUDE.md discipline block
against the xylem template's manifest version, and prints a single one-line
nudge when the installed block is stale. On a match it prints nothing, so a
current machine spends zero model tokens.

Detection only -- this script NEVER rewrites a block. The nudge points at
`xylem update`, which is the sole path that re-applies a block.

Where it looks:
  - Installed block(s): $XYLEM_CHECK_TARGETS (os.pathsep-separated) if set,
    else the project CLAUDE.md ($PWD/CLAUDE.md) and the global CLAUDE.md. This
    covers both a globally-installed block and a copy committed into a repo.
    The lowest version among the blocks the session loaded is the one compared,
    so any stale copy is caught.
  - Template version: read from the local xylem clone. With a successful
    `git fetch` (see below), the origin copy of manifest.json is preferred so
    the nudge fires as soon as a new version is published upstream -- before the
    local pull -- which is exactly what `xylem update` then resolves. Falls back
    to the working-tree manifest.json.

The upstream fetch is OPT-IN and RATE-LIMITED
---------------------------------------------
This runs on every SessionStart, inside a 10-second hook budget. An eager
`git fetch origin` there costs up to the whole budget of latency on every
single session -- on a slow, captive, or offline network -- for a cosmetic
notice. docs/versioning.md has described the fetch as off by default, capped at
once per 24 hours, and bounded by a 3-second timeout; none of that existed, so
the doc was the design and the code was the bug. It exists now:

  * off unless XYLEM_FETCH_ON_CHECK=1;
  * at most one fetch per XYLEM_FETCH_INTERVAL seconds (default 86400), tracked
    by a timestamp cache file;
  * a 3-second timeout on the fetch itself, so even the once-a-day fetch cannot
    eat the hook budget.

The comparison against the local clone still happens on EVERY session, so a
stale block is still caught immediately; you just learn about a brand-new
upstream version on your next pull rather than instantly.

Config (environment):
  - XYLEM_ROOT            : path to the xylem clone (default: this script's repo)
  - XYLEM_FETCH_ON_CHECK  : "1" to `git fetch` first; off by default
  - XYLEM_FETCH_INTERVAL  : seconds between fetches (default 86400 = 24h; 0 = always)
  - XYLEM_FETCH_TIMEOUT   : seconds before the fetch is abandoned (default 3)
  - XYLEM_FETCH_STAMP     : override path to the timestamp cache file
  - XYLEM_FETCH_REF       : ref to read the template from (default origin/main)
  - XYLEM_CHECK_TARGETS   : override list of CLAUDE.md paths to inspect

Fail-soft on everything: if the clone is missing, git is absent, a fetch fails
offline, or no block is found, it outputs nothing and exits 0. Stdlib only.
ASCII-only output (Windows cp1252 console constraint).
"""
import json
import os
import re
import subprocess
import sys
import time

# Defaults for the fetch rate limiter. See the module docstring.
FETCH_DEFAULT_ON = False
FETCH_INTERVAL_DEFAULT = 24 * 60 * 60  # once a day
FETCH_TIMEOUT_DEFAULT = 3  # seconds; the hook budget is 10 in total
STAMP_FILENAME = "xylem-fetch-stamp"

# Same grammar the installer stamps with: optional ` vN` after BEGIN.
FENCE_BEGIN_RE = re.compile(r"<!-- XYLEM:BEGIN(?: v(\d+))? -->")

# This script ships in artifacts/; the clone root is its grandparent dir.
DEFAULT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read_text(path):
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            return fh.read()
    except (OSError, ValueError):
        return ""


def _parse_fence_version(text):
    """Installed version: int from `vN`, 1 for an unstamped block, else None."""
    match = FENCE_BEGIN_RE.search(text or "")
    if match is None:
        return None
    return int(match.group(1)) if match.group(1) else 1


def _manifest_version(obj):
    """Integer 'version' from a parsed manifest dict; default 1 if absent/bad."""
    try:
        return int(obj.get("version", 1))
    except (AttributeError, TypeError, ValueError):
        return 1


def _run_git(git_args, cwd, timeout=10):
    """Run a git command; return (ok, stdout). Never raises, never hangs."""
    try:
        proc = subprocess.run(
            ["git"] + list(git_args), cwd=cwd,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            universal_newlines=True, timeout=timeout)
    except Exception:  # git missing, timeout, bad cwd -- all fail soft
        return False, ""
    return proc.returncode == 0, (proc.stdout or "")


def _env_int(key, default):
    """A non-negative integer from the environment, or `default`. Never raises."""
    raw = os.environ.get(key, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value >= 0 else default


def fetch_enabled(env=None):
    """Is the upstream fetch turned on? Off by default -- see the docstring."""
    env = os.environ if env is None else env
    raw = env.get("XYLEM_FETCH_ON_CHECK", "").strip().lower()
    if not raw:
        return FETCH_DEFAULT_ON
    return raw in ("1", "true", "yes", "on")


def stamp_path(root):
    """Where the last-fetch timestamp lives.

    Inside .git by preference: it is per-clone (two checkouts rate-limit
    independently, which is what you want), it is never committed, and it is
    already a directory this tool may write to. A worktree/submodule has a .git
    FILE rather than a directory, and a tarball export has neither, so both fall
    back to the user cache dir.
    """
    override = os.environ.get("XYLEM_FETCH_STAMP", "").strip()
    if override:
        return override

    git_dir = os.path.join(root, ".git")
    if os.path.isdir(git_dir):
        return os.path.join(git_dir, STAMP_FILENAME)

    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.path.join(
            os.path.expanduser("~"), "AppData", "Local")
    else:
        base = os.environ.get("XDG_CACHE_HOME") or os.path.join(
            os.path.expanduser("~"), ".cache")
    return os.path.join(base, "xylem", STAMP_FILENAME)


def _last_fetch(path):
    """Epoch seconds of the last fetch attempt, or None. Never raises."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return float(fh.read().strip())
    except (OSError, ValueError, TypeError):
        return None


def _record_fetch(path, now):
    """Stamp the fetch time. A read-only or missing directory is not an error."""
    try:
        parent = os.path.dirname(path)
        if parent and not os.path.isdir(parent):
            os.makedirs(parent)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("%d\n" % int(now))
    except OSError:
        # Cannot persist the stamp: the check still works, it just cannot
        # rate-limit. Better than failing the session over a cache file.
        pass


def fetch_is_due(root, now=None, interval=None):
    """True when the last fetch is older than the interval (or never happened).

    A stamp in the FUTURE (a clock that jumped back, or a restored backup)
    counts as due rather than blocking fetches until the clock catches up.
    """
    now = time.time() if now is None else now
    if interval is None:
        interval = _env_int("XYLEM_FETCH_INTERVAL", FETCH_INTERVAL_DEFAULT)
    if interval <= 0:
        return True
    last = _last_fetch(stamp_path(root))
    if last is None:
        return True
    if last > now:
        return True
    return (now - last) >= interval


def _template_version(root):
    """Template version from the clone: origin (after fetch) then working tree."""
    ref = os.environ.get("XYLEM_FETCH_REF", "origin/main").strip() or "origin/main"

    if fetch_enabled() and fetch_is_due(root):
        remote = ref.split("/", 1)[0] if "/" in ref else "origin"
        # Stamp BEFORE the fetch, not after: an attempt that hangs to its
        # timeout is exactly the attempt we most want rate-limited, and
        # stamping only on success would retry it on every single session.
        _record_fetch(stamp_path(root), time.time())
        fetched, _ = _run_git(
            ["fetch", "--quiet", remote], root,
            timeout=_env_int("XYLEM_FETCH_TIMEOUT", FETCH_TIMEOUT_DEFAULT))
        if fetched:
            ok, out = _run_git(["show", "%s:manifest.json" % ref], root)
            if ok and out.strip():
                try:
                    return _manifest_version(json.loads(out))
                except ValueError:
                    pass  # fall through to the working-tree copy

    text = _read_text(os.path.join(root, "manifest.json"))
    if not text.strip():
        return None
    try:
        return _manifest_version(json.loads(text))
    except ValueError:
        return None


def _global_claude_md():
    """Best-guess path to the global CLAUDE.md across platforms."""
    if os.name == "nt":
        appdata = os.environ.get("APPDATA")
        if appdata:
            cand = os.path.join(appdata, "Claude", "CLAUDE.md")
            if os.path.isfile(cand):
                return cand
    return os.path.join(os.path.expanduser("~"), ".claude", "CLAUDE.md")


def _candidate_targets():
    """CLAUDE.md paths to inspect (explicit override or sane defaults)."""
    override = os.environ.get("XYLEM_CHECK_TARGETS", "").strip()
    if override:
        return [p for p in override.split(os.pathsep) if p]
    return [
        os.path.join(os.getcwd(), "CLAUDE.md"),  # repo-committed block
        _global_claude_md(),                      # globally-installed block
    ]


def _installed_version(targets):
    """Lowest stamped version among blocks that are actually present."""
    versions = []
    seen = set()
    for path in targets:
        key = os.path.normcase(os.path.abspath(path))
        if key in seen:
            continue
        seen.add(key)
        version = _parse_fence_version(_read_text(path))
        if version is not None:
            versions.append(version)
    return min(versions) if versions else None


def main():
    root = os.environ.get("XYLEM_ROOT", "").strip() or DEFAULT_ROOT

    installed = _installed_version(_candidate_targets())
    if installed is None:
        return 0  # no xylem block loaded -- nothing to compare

    template = _template_version(root)
    if template is None:
        return 0  # can't determine the template -- stay silent

    if installed < template:
        # Exactly one line, ASCII-only, on stdout.
        line = ("xylem habit layer v%d available (installed v%d); "
                "run `xylem update` to apply." % (template, installed))
        sys.stdout.write(line.encode("ascii", "replace").decode("ascii") + "\n")
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        # Absolute last-resort soft failure: a version check never blocks a session.
        sys.exit(0)

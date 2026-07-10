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

Config (environment):
  - XYLEM_ROOT            : path to the xylem clone (default: this script's repo)
  - XYLEM_FETCH_ON_CHECK  : "1" (default) to `git fetch` first; "0" to skip
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


def _template_version(root):
    """Template version from the clone: origin (after fetch) then working tree."""
    fetch_on = os.environ.get("XYLEM_FETCH_ON_CHECK", "1").strip() != "0"
    ref = os.environ.get("XYLEM_FETCH_REF", "origin/main").strip() or "origin/main"

    if fetch_on:
        remote = ref.split("/", 1)[0] if "/" in ref else "origin"
        fetched, _ = _run_git(["fetch", "--quiet", remote], root)
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

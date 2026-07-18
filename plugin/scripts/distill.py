#!/usr/bin/env python3
"""Xylem SessionEnd hook: distill this session's decisions into LOCAL cambium scope.

Replaces the previous distill.sh. That script required `bash` and `git` on PATH,
which is not a given on a stock Windows box -- the rest of the suite bends over
backwards for Windows (install.ps1, ASCII-only console output), so a bash-only
hook was an inconsistency, not a platform choice. Python is already a hard
dependency of the stack, so this runs everywhere the servers do.

This must NEVER fail a session. Every path exits 0.

Two behaviours worth knowing:
  * If `cambium` is not on PATH we print one line and leave. The plugin is fully
    usable without cambium; only the knowledge skills need it.
  * We distill the project the SESSION ran in, taken from the hook payload's
    `cwd`, not whatever directory the hook process happened to inherit. The old
    script used `git rev-parse` from the inherited cwd and swallowed a failed
    `cd`, so a bad resolve silently distilled the WRONG project rather than
    distilling nothing.
"""

import json
import os
import shutil
import subprocess
import sys

TIMEOUT = 45


def _payload():
    """Read the hook payload from stdin without ever blocking the session."""
    if sys.stdin is None or sys.stdin.isatty():
        return {}
    try:
        raw = sys.stdin.read()
    except Exception:
        return {}
    try:
        return json.loads(raw) if raw.strip() else {}
    except Exception:
        return {}


def _git_root(start):
    """Walk up from `start` looking for a .git entry. No subprocess, no PATH git."""
    try:
        cur = os.path.abspath(start)
    except Exception:
        return None
    last = None
    while cur and cur != last:
        if os.path.exists(os.path.join(cur, ".git")):
            return cur
        last, cur = cur, os.path.dirname(cur)
    return None


def main():
    if shutil.which("cambium") is None:
        print(
            "xylem: cambium not found on PATH - skipping session distillation "
            "(install cambium to enable the knowledge loop)."
        )
        return 0

    session_cwd = _payload().get("cwd") or os.getcwd()
    root = _git_root(session_cwd)
    if root is None:
        print(
            "xylem: could not resolve a git root for this session - skipping "
            "distillation rather than capturing into the wrong project."
        )
        return 0

    try:
        proc = subprocess.run(
            ["cambium", "distill"],
            cwd=root,
            timeout=TIMEOUT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
    except subprocess.TimeoutExpired:
        print("xylem: cambium distill timed out - skipping (session left untouched).")
        return 0
    except Exception:
        print("xylem: cambium distill could not run - skipping (session left untouched).")
        return 0

    if proc.returncode == 0:
        print("xylem: session distilled into the local knowledge store.")
    else:
        print(
            "xylem: cambium distill did not complete cleanly - skipping "
            "(session left untouched)."
        )
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        # A SessionEnd hook must never surface a traceback into the user's session.
        sys.exit(0)

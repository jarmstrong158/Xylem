#!/usr/bin/env python3
"""Xylem SessionEnd hook: distill this session's decisions into LOCAL cambium scope.

Why this does not shell out to a `cambium` command
--------------------------------------------------
It used to. It ran `subprocess.run(["cambium", "distill"])` behind a
`shutil.which("cambium")` guard -- and no such executable has ever existed.
cambium ships ONE console script, `cambium-mcp`, and its `main()` is
`mcp.run()`: an MCP stdio server with no argparse and no subcommands. So the
guard never passed, the hook printed "cambium not found on PATH" on every single
session, and the capture leg of the compound-growth loop was a permanent no-op
that looked like a clean, well-handled skip.

`distill` is an `@mcp.tool()`-decorated plain function in cambium_server.py, so
the way to call it is to call it. That is what artifacts/session_end_hook.py
(the full-install path) already did; this file was the parallel implementation
that drifted onto an imaginary CLI. Both paths now import the module.

The call happens in a CHILD interpreter rather than in-process, because a
SessionEnd hook has a hard timeout budget and importing a third-party server
module gives an unbounded amount of someone else's import-time code the chance
to hang. A child process can be killed; an import cannot.

It also replaces the earlier distill.sh, which required `bash` and `git` on
PATH -- not a given on a stock Windows box, and an inconsistency with the rest
of the suite (install.ps1, ASCII-only console output) rather than a platform
choice. Python is already a hard dependency of the stack.

This must NEVER fail a session. Every path exits 0.

Two behaviours worth knowing:
  * If cambium cannot be located we print one line and leave. The plugin is
    fully usable without cambium; only the knowledge skills need it.
  * We distill the project the SESSION ran in, taken from the hook payload's
    `cwd`, not whatever directory the hook process happened to inherit. The
    original script used `git rev-parse` from the inherited cwd and swallowed a
    failed `cd`, so a bad resolve silently distilled the WRONG project rather
    than distilling nothing.
"""

import json
import os
import subprocess
import sys

TIMEOUT = 45

SERVER_FILENAME = "cambium_server.py"

# Env vars that may point at cambium, in precedence order. Either at
# cambium_server.py directly or at the directory containing it -- both accepted.
#   XYLEM_CAMBIUM_PATH : what artifacts/session_end_hook.py (full install) reads
#   CAMBIUM_SERVER     : the install/servers.json config key for the same file
PATH_ENV_KEYS = ("XYLEM_CAMBIUM_PATH", "CAMBIUM_SERVER")

# Run distill in a child interpreter and print its JSON result on stdout.
# Kept as a single -c string so the hook stays one self-contained file.
CHILD = (
    "import importlib.util,os,sys;"
    "p=sys.argv[1];d=os.path.dirname(p);"
    "sys.path.insert(0,d);"
    "s=importlib.util.spec_from_file_location('xylem_cambium_server',p);"
    "m=importlib.util.module_from_spec(s);s.loader.exec_module(m);"
    "sys.stdout.write(str(m.distill()))"
)


def _payload():
    """Read the hook payload from stdin without ever blocking the session."""
    if sys.stdin is None or sys.stdin.isatty():
        return {}
    try:
        raw = sys.stdin.read()
    except Exception:
        return {}
    try:
        data = json.loads(raw) if raw.strip() else {}
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


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


def _as_server_file(raw):
    """Normalise a path that may name the server file or its directory."""
    raw = (raw or "").strip()
    if not raw:
        return None
    if os.path.isdir(raw):
        candidate = os.path.join(raw, SERVER_FILENAME)
        return candidate if os.path.isfile(candidate) else None
    return raw if os.path.isfile(raw) else None


def find_cambium_server():
    """Absolute path to cambium_server.py, or None.

    Order: an explicit env pointer, then the sibling checkout. The plugin lives
    at <xylem>/plugin/scripts/, and the installer clones the server repos as
    siblings of the xylem checkout, so both <xylem>/cambium and
    <xylem>/../cambium are worth a look before giving up.
    """
    for key in PATH_ENV_KEYS:
        found = _as_server_file(os.environ.get(key))
        if found:
            return os.path.abspath(found)

    here = os.path.dirname(os.path.abspath(__file__))
    plugin_root = os.path.dirname(here)          # <xylem>/plugin
    xylem_root = os.path.dirname(plugin_root)    # <xylem>
    for base in (xylem_root, os.path.dirname(xylem_root)):
        found = _as_server_file(os.path.join(base, "cambium"))
        if found:
            return os.path.abspath(found)
    return None


def interpreter():
    """The interpreter to run the child with.

    sys.executable, for the dec-013 reason: a bare `python`/`python3` name is
    resolved by PATH, and on a very common Windows box `python3` is the
    Microsoft Store shim rather than the interpreter that has cambium's
    dependencies installed.
    """
    return sys.executable or "python"


def _new_items(raw):
    """(was_a_real_distill, count). Anything else is a clean no-op.

    distill() returns a JSON string: {"status": "distilled", ...} when cambium
    is set up, and its config-state guidance when it is not. Reporting the
    latter as a successful capture would be the same "looks like it worked"
    failure this hook already shipped once.
    """
    try:
        parsed = json.loads(raw)
    except Exception:
        return False, 0
    if not isinstance(parsed, dict) or parsed.get("status") != "distilled":
        return False, 0
    count = parsed.get("new_items", 0)
    return True, count if isinstance(count, int) else 0


def main():
    server = find_cambium_server()
    if server is None:
        print(
            "xylem: cambium not found - skipping session distillation "
            "(set XYLEM_CAMBIUM_PATH to cambium_server.py to enable the "
            "knowledge loop)."
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

    env = dict(os.environ)
    # Point cambium at the project this SESSION ran in, unless the user has
    # deliberately pinned one.
    env.setdefault("CAMBIUM_REPO", root)

    try:
        proc = subprocess.run(
            [interpreter(), "-c", CHILD, server],
            cwd=root,
            env=env,
            timeout=TIMEOUT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except subprocess.TimeoutExpired:
        print("xylem: cambium distill timed out - skipping (session left untouched).")
        return 0
    except Exception:
        print("xylem: cambium distill could not run - skipping (session left untouched).")
        return 0

    if proc.returncode != 0:
        print(
            "xylem: cambium distill did not complete cleanly - skipping "
            "(session left untouched)."
        )
        return 0

    out = (proc.stdout or b"").decode("utf-8", "replace")
    ok, count = _new_items(out)
    if not ok:
        # cambium ran but is not configured yet. Not an error, not a capture.
        print("xylem: cambium is not configured yet - nothing distilled.")
        return 0

    print("xylem: distilled %d new item(s) into the local knowledge store." % count)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        # A SessionEnd hook must never surface a traceback into the user's session.
        sys.exit(0)

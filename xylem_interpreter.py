#!/usr/bin/env python3
"""The single interpreter-resolution policy for the whole Xylem suite.

Why this module exists
----------------------
Two independent installers ship in this repo -- `installer.py` (Claude Code
path) and `install/xylem_install.py` (multi-agent path) -- and they resolved the
Python interpreter for the stdio servers by OPPOSITE strategies:

  * installer.py used ``sys.executable``
  * install/xylem_install.py used ``shutil.which("python3")`` first

dec-013 records why the second one is wrong on a very common Windows setup:
``python3`` resolves to the Microsoft Store shim while the interpreter that
actually has ``mcp`` installed is ``python``. The servers got registered into a
config where they could never start, with no diagnostic. Two implementations of
one policy means the policy can only ever be half-fixed, so it lives here now
and both installers call it.

The policy, in order:

  1. ``XYLEM_PYTHON`` -- an explicit override always wins (a venv, a pinned
     build). Read from the caller's value source, or the environment.
  2. ``sys.executable`` -- the interpreter running the installer. If you could
     run the install, the servers can run; and installing from a virtualenv
     registers that virtualenv, which is almost always what you want.
  3. ``python3`` then ``python`` on PATH -- last resort, for the exotic case of
     an embedded/frozen interpreter where ``sys.executable`` is empty or is not
     a Python at all.

Stdlib only, Python 3.8+, like the rest of the suite.
"""

import os
import shutil
import sys

OVERRIDE_KEY = "XYLEM_PYTHON"

# Launch-command spellings that mean "whatever Python this machine has" rather
# than a real, resolved path. Anything in this set gets replaced by
# resolve_python(); anything else is an explicit choice and is left alone.
UNRESOLVED_COMMANDS = ("$PYTHON", "${PYTHON}", "python", "python3")


def _frozen():
    """True when sys.executable is not a usable Python (frozen/embedded host)."""
    exe = sys.executable or ""
    if not exe:
        return True
    # A frozen app (PyInstaller et al.) reports its own binary here; launching
    # the servers with it would run the app, not Python.
    return bool(getattr(sys, "frozen", False))


def resolve_python(get=None):
    """Return the interpreter path to launch the stdio servers with.

    `get` is an optional callable taking a key name, used by
    install/xylem_install.py so the override can also come from the untracked
    xylem.config.json rather than only from the environment.
    """
    override = None
    if get is not None:
        try:
            override = get(OVERRIDE_KEY)
        except Exception:
            override = None
    if not override:
        override = os.environ.get(OVERRIDE_KEY)
    if override:
        return str(override)

    if not _frozen():
        return sys.executable

    # sys.executable is unusable: fall back to PATH. python3 first because on a
    # frozen host we are almost certainly not on Windows, and even there a
    # failed launch is no worse than the nothing we would otherwise return.
    for name in ("python3", "python"):
        found = shutil.which(name)
        if found:
            return found
    return "python3"


def needs_resolution(command):
    """True if `command` is a placeholder/bare name rather than a real path."""
    return str(command or "").strip() in UNRESOLVED_COMMANDS


# --------------------------------------------------------------------------- #
# static launch commands (the plugin path, where nothing runs at install time)
# --------------------------------------------------------------------------- #
# installer.py can resolve a real interpreter path because it is a program that
# runs on the target machine. The PLUGIN has no install step at all -- Claude
# Code reads plugin/hooks/hooks.json verbatim -- so the launch command must be
# static, and a static command cannot contain a resolved path.
#
# It must therefore not contain a bare name either, which is what dec-013 is
# about. There is no single bare name that is right everywhere:
#
#   * on a very common Windows box, `python3` is the Microsoft Store shim: it
#     prints "Python was not found" and exits NON-ZERO, while `python` is the
#     interpreter that actually works;
#   * on most Linux/macOS boxes it is the exact reverse -- only `python3`
#     exists and `python` is absent.
#
# A fallback chain is the one form that survives both, and it works precisely
# because the failure modes above are non-zero exits. `A || B` is valid in
# cmd.exe and in POSIX sh with the same semantics, so one string covers every
# platform Claude Code runs a hook on.
#
# The chain is only safe for a script that exits 0 on every path (both plugin
# hook scripts do, deliberately) -- otherwise a script that merely returned
# non-zero would be run a second time.
LAUNCH_CHAIN = ("python3", "python")


def launch_command(script):
    """A static, platform-agnostic shell command that runs `script` with Python.

    `script` is inserted verbatim (it is a build-time constant such as
    "${CLAUDE_PLUGIN_ROOT}/scripts/primer.py") and is quoted, because plugin
    roots contain spaces on Windows more often than not.
    """
    return " || ".join('%s "%s"' % (name, script) for name in LAUNCH_CHAIN)

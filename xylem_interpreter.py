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

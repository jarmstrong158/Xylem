#!/usr/bin/env python3
"""Xylem SessionEnd hook.

Fires cambium's distill() at session end so the capture leg of the
compound-growth loop runs automatically: agentsync done-claims and
context-keeper decisions/constraints become cambium memory with zero per-note
effort. Mirrors the pattern used by session_start_hook.py -- it imports the
cambium server module and calls distill() directly (distill is idempotent and
built to run from a session-end / post-commit hook).

Fails soft in every degenerate case (cambium not installed, path not
configured, import error, handler error, or cambium simply not set up yet) by
writing a single ASCII-safe line to stderr and exiting 0, so a broken or absent
memory layer never blocks session teardown.

Stdlib only. ASCII-only output (Windows cp1252 console constraint).
"""
import importlib.util
import json
import os
import sys

SKIP_MSG = "xylem: cambium not configured, skipping distill"


def _ascii(text):
    """Coerce arbitrary text to something a cp1252 console can always print."""
    if not isinstance(text, str):
        text = str(text)
    return text.encode("ascii", "replace").decode("ascii")


def _emit(text):
    # Session teardown consumes no hook stdout, so log to stderr and never let
    # the write itself raise on a narrow console encoding.
    sys.stderr.write(_ascii(text))
    if not text.endswith("\n"):
        sys.stderr.write("\n")
    sys.stderr.flush()


def _resolve_server_path():
    """Return the absolute path to cambium's cambium_server.py, or None.

    XYLEM_CAMBIUM_PATH may point either at cambium_server.py directly or at the
    cambium directory containing it. Both are accepted.
    """
    raw = os.environ.get("XYLEM_CAMBIUM_PATH", "").strip()
    if not raw:
        return None
    if os.path.isdir(raw):
        candidate = os.path.join(raw, "cambium_server.py")
        return candidate if os.path.isfile(candidate) else None
    if os.path.isfile(raw):
        return raw
    return None


def _load_server(server_path):
    """Import cambium_server.py as an isolated module and return it, or None."""
    server_dir = os.path.dirname(os.path.abspath(server_path))
    # Let the module resolve its own sibling imports.
    if server_dir not in sys.path:
        sys.path.insert(0, server_dir)
    try:
        spec = importlib.util.spec_from_file_location(
            "xylem_cambium_server", server_path)
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    except Exception:
        return None


def _configured_result(result):
    """Was this a real distill (True) or cambium's unconfigured guidance (False)?

    distill() returns a JSON string. When cambium is set up it carries
    {"status": "distilled", ...}; when it is not, distill returns cambium's
    config-state guidance instead. Treat anything that is not a successful
    distill as a clean no-op.
    """
    if not isinstance(result, str):
        return False, None
    try:
        parsed = json.loads(result)
    except (ValueError, TypeError):
        return False, None
    if isinstance(parsed, dict) and parsed.get("status") == "distilled":
        return True, parsed
    return False, None


def main():
    server_path = _resolve_server_path()
    if not server_path:
        _emit(SKIP_MSG)
        return 0

    module = _load_server(server_path)
    if module is None:
        _emit(SKIP_MSG)
        return 0

    handler = getattr(module, "distill", None)
    if not callable(handler):
        _emit(SKIP_MSG)
        return 0

    try:
        result = handler()
    except Exception:
        _emit(SKIP_MSG)
        return 0

    ok, parsed = _configured_result(result)
    if not ok:
        _emit(SKIP_MSG)
        return 0

    _emit("xylem: distilled %d new item(s) into cambium"
          % parsed.get("new_items", 0))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        # Absolute last-resort soft failure: never let a hook block teardown.
        _emit(SKIP_MSG)
        sys.exit(0)

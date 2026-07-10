#!/usr/bin/env python3
"""Xylem SessionStart hook.

Injects the context-keeper project summary into the session at start. Mirrors
the pattern used by context-keeper's own hooks/session_start.py: it imports the
server module and calls handle_get_project_summary({}).

Fails soft in every degenerate case (context-keeper not installed, path not
configured, import error, handler error) by printing a single ASCII-safe line
and exiting 0, so a broken memory layer never blocks a session from starting.

Stdlib only. ASCII-only output (Windows cp1252 console constraint).
"""
import importlib.util
import os
import sys

SKIP_MSG = "xylem: context-keeper not configured, skipping memory injection"


def _ascii(text):
    """Coerce arbitrary text to something a cp1252 console can always print."""
    if not isinstance(text, str):
        text = str(text)
    return text.encode("ascii", "replace").decode("ascii")


def _emit(text):
    # Write bytes directly so we never trip over the console's default encoding.
    sys.stdout.write(_ascii(text))
    if not text.endswith("\n"):
        sys.stdout.write("\n")
    sys.stdout.flush()


def _resolve_server_path():
    """Return the absolute path to context-keeper's server.py, or None.

    XYLEM_CONTEXT_KEEPER_PATH may point either at server.py directly or at the
    context-keeper directory containing it. Both are accepted.
    """
    raw = os.environ.get("XYLEM_CONTEXT_KEEPER_PATH", "").strip()
    if not raw:
        return None
    if os.path.isdir(raw):
        candidate = os.path.join(raw, "server.py")
        return candidate if os.path.isfile(candidate) else None
    if os.path.isfile(raw):
        return raw
    return None


def _load_server(server_path):
    """Import server.py as an isolated module and return it, or None on failure."""
    server_dir = os.path.dirname(os.path.abspath(server_path))
    # Let the module resolve its own sibling imports.
    if server_dir not in sys.path:
        sys.path.insert(0, server_dir)
    try:
        spec = importlib.util.spec_from_file_location("xylem_context_keeper_server", server_path)
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    except Exception:
        return None


def _extract_text(result):
    """Normalize whatever the handler returns into printable text."""
    if result is None:
        return ""
    if isinstance(result, str):
        return result
    if isinstance(result, dict):
        # Common shapes: {"summary": "..."} or MCP-style {"content": [{"text": ...}]}
        if isinstance(result.get("summary"), str):
            return result["summary"]
        content = result.get("content")
        if isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, dict) and isinstance(block.get("text"), str):
                    parts.append(block["text"])
            if parts:
                return "\n".join(parts)
        if isinstance(result.get("text"), str):
            return result["text"]
    return str(result)


def main():
    server_path = _resolve_server_path()
    if not server_path:
        _emit(SKIP_MSG)
        return 0

    module = _load_server(server_path)
    if module is None:
        _emit(SKIP_MSG)
        return 0

    handler = getattr(module, "handle_get_project_summary", None)
    if not callable(handler):
        _emit(SKIP_MSG)
        return 0

    try:
        result = handler({})
    except Exception:
        _emit(SKIP_MSG)
        return 0

    text = _extract_text(result).strip()
    if not text:
        _emit(SKIP_MSG)
        return 0

    _emit(text)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        # Absolute last-resort soft failure: never let a hook crash a session.
        _emit(SKIP_MSG)
        sys.exit(0)

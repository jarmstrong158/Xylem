#!/usr/bin/env python3
"""Xylem SessionStart hook: inject a cambium knowledge primer.

The recall side of the compound loop. distill() (SessionEnd) captures work into
cambium automatically; without this, recall was advisory prose an agent had to
remember. This calls cambium's read-only session_primer() and injects a digest
of what's already known for the project, so recall is passive too.

Two jobs, both keyed on the session's real project (the SessionStart payload's
`cwd`, NOT this hook's own cwd, which is wherever `claude` was launched):

  1. Point cambium at that project. cambium resolves its repo per call as
     env > config-file > cwd, and the persistent MCP server has no per-session
     cwd, so we write CAMBIUM_REPO into cambium's config file. The running server
     then follows the session's project for interactive recall()/capture() too —
     not just this primer. (User-set CAMBIUM_REPO in the env still overrides.)
  2. Emit the primer digest to stdout so it lands in the session context.

Fails soft in every degenerate case (cambium absent/unconfigured, not a git
project, import/handler error) by exiting 0 with no output, so a missing memory
layer never blocks a session from starting.

Stdlib only. ASCII-only output (Windows cp1252 console constraint).
"""
import importlib.util
import json
import os
import sys


def _ascii(text):
    if not isinstance(text, str):
        text = str(text)
    return text.encode("ascii", "replace").decode("ascii")


def _emit(text):
    sys.stdout.write(_ascii(text))
    if not text.endswith("\n"):
        sys.stdout.write("\n")
    sys.stdout.flush()


def _resolve_server_path():
    """Absolute path to cambium_server.py from XYLEM_CAMBIUM_PATH (a file or its
    directory), or None."""
    raw = os.environ.get("XYLEM_CAMBIUM_PATH", "").strip()
    if not raw:
        return None
    if os.path.isdir(raw):
        candidate = os.path.join(raw, "cambium_server.py")
        return candidate if os.path.isfile(candidate) else None
    return raw if os.path.isfile(raw) else None


def _session_repo():
    """Git root of the session's project, from the SessionStart payload's `cwd`
    on stdin. Empty if unavailable. Mirrors session_end_hook so start and end
    resolve the SAME project."""
    try:
        if sys.stdin is None or sys.stdin.isatty():
            return ""
        raw = sys.stdin.read()
    except Exception:
        return ""
    if not raw or not raw.strip():
        return ""
    try:
        payload = json.loads(raw)
    except (ValueError, TypeError):
        return ""
    cwd = payload.get("cwd") if isinstance(payload, dict) else None
    if not isinstance(cwd, str) or not cwd.strip():
        return ""
    d = os.path.abspath(cwd.strip())
    while d:
        if os.path.isdir(os.path.join(d, ".git")):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            return ""
        d = parent
    return ""


def _active_project_file():
    """Path to the shared Xylem session pointer (overridable for tests)."""
    override = os.environ.get("XYLEM_ACTIVE_PROJECT_FILE")
    if override:
        return os.path.abspath(os.path.expanduser(override))
    return os.path.join(os.path.expanduser("~"), ".xylem", "active_project.json")


def _write_active_project(repo):
    """Record the session's project in the shared Xylem pointer that
    context-keeper and agentsync read per call, so the whole stack follows the
    session's project — not just cambium. Best-effort; never raises."""
    try:
        path = _active_project_file()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"project": repo}, f)
    except OSError:
        pass


def _load_server(server_path):
    server_dir = os.path.dirname(os.path.abspath(server_path))
    if server_dir not in sys.path:
        sys.path.insert(0, server_dir)
    try:
        spec = importlib.util.spec_from_file_location(
            "xylem_cambium_primer_server", server_path)
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    except Exception:
        return None


def _point_cambium_at(module, repo):
    """Persist CAMBIUM_REPO=repo into cambium's config file (preserving the
    other keys) so the running MCP server follows this session's project, and set
    it in THIS process's env so the primer call below resolves the same. Only
    ever updates the one key; never invents an agent id or org repo. Best-effort.
    """
    try:
        conf = module._load_config_file()
        if not isinstance(conf, dict):
            conf = {}
        if conf.get("CAMBIUM_REPO") != repo:
            conf["CAMBIUM_REPO"] = repo
            module._write_config_file(conf)
    except Exception:
        pass
    os.environ["CAMBIUM_REPO"] = repo


def _format(parsed):
    """Render the primer digest as a short ASCII block for the session context."""
    lines = ["xylem: cambium knows %d item(s) for %s"
             % (parsed.get("known_items", 0), parsed.get("project") or "this project")]
    for k in parsed.get("known", [])[:8]:
        lines.append("  - [%s] %s (recalls=%s)"
                     % (k.get("scope", "?"), k.get("content", ""),
                        k.get("recalls", 0)))
    checks = parsed.get("check_assumptions", [])
    if checks:
        lines.append("  assumptions to re-check:")
        for c in checks:
            lines.append("    - %s (valid while: %s)"
                         % (c.get("content", ""), c.get("valid_while", "")))
    lines.append("  recall(<query>) to search deeper.")
    return "\n".join(lines)


def main():
    # Record the session's project FIRST, independent of cambium: context-keeper
    # and agentsync read this shared pointer per call to follow the session too.
    repo = _session_repo()
    if repo:
        _write_active_project(repo)

    server_path = _resolve_server_path()
    if not server_path:
        return 0  # cambium not wired — pointer still recorded above

    module = _load_server(server_path)
    if module is None:
        return 0
    if repo:
        _point_cambium_at(module, repo)

    primer = getattr(module, "session_primer", None)
    if not callable(primer):
        return 0
    try:
        result = primer()
    except Exception:
        return 0

    try:
        parsed = json.loads(result) if isinstance(result, str) else None
    except (ValueError, TypeError):
        parsed = None
    # Only emit for a configured project with something worth surfacing. An
    # unconfigured cambium returns config-state guidance (no "known" key); a
    # configured-but-empty project returns known == [] — stay silent in both.
    if not isinstance(parsed, dict) or not parsed.get("known"):
        return 0

    _emit(_format(parsed))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)

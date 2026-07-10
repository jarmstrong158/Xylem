#!/usr/bin/env python3
"""Xylem installer.

Installs the Xylem suite into Claude Code:
  - registers enabled MCP servers in settings.json (stdio + http)
  - injects a fenced discipline block into CLAUDE.md
  - registers the SessionStart memory-injection hook
  - installs the /xylem-discipline slash command

Design rules (non-negotiable):
  - stdlib only, Python 3.8+
  - never clobber existing config: back up before first write, merge additively,
    idempotent re-runs, and uninstall touches ONLY Xylem-owned entries
  - never hardcode Worker URLs or secrets: http server url/token come from env
  - cross-platform: macOS, Linux, Windows (JSON paths use forward slashes)

Usage:
  python3 installer.py [--dry-run] [--uninstall] [--project PATH]
"""
import argparse
import difflib
import json
import os
import shutil
import sys

# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------

FENCE_BEGIN = "<!-- XYLEM:BEGIN -->"
FENCE_END = "<!-- XYLEM:END -->"
BACKUP_SUFFIX = ".xylem-backup"

# Marks the SessionStart hook we own, so uninstall can find exactly it.
HOOK_MARKER = "session_start_hook.py"
# settings.json env key we own (points the hook at context-keeper's server.py).
ENV_KEY = "XYLEM_CONTEXT_KEEPER_PATH"

ROOT = os.path.dirname(os.path.abspath(__file__))
# The sibling-repos directory: context-keeper, agentsync, cambium live here,
# NOT inside this repo. Manifest paths use $XYLEM_PARENT to reach them.
PARENT = os.path.dirname(ROOT)


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------

def to_fwd(path):
    """Forward-slash a path so it is safe inside JSON on every platform."""
    return path.replace("\\", "/")


def load_json_text(text):
    """Parse JSON text, tolerating an empty/whitespace file as {}."""
    text = (text or "").strip()
    if not text:
        return {}
    return json.loads(text)


def dump_json_text(obj):
    """Serialize deterministically so re-runs produce identical bytes."""
    return json.dumps(obj, indent=2, ensure_ascii=False) + "\n"


def read_text(path):
    # utf-8-sig transparently strips a leading BOM if some editor/tool wrote one
    # (common on Windows), while reading plain UTF-8 unchanged.
    if os.path.isfile(path):
        with open(path, "r", encoding="utf-8-sig") as fh:
            return fh.read()
    return ""


def resolve_placeholders(value, mapping):
    """Recursively replace $PLACEHOLDER tokens inside strings/lists/dicts."""
    if isinstance(value, str):
        for token, replacement in mapping.items():
            value = value.replace(token, replacement)
        return value
    if isinstance(value, list):
        return [resolve_placeholders(v, mapping) for v in value]
    if isinstance(value, dict):
        return {k: resolve_placeholders(v, mapping) for k, v in value.items()}
    return value


# --------------------------------------------------------------------------
# Pure transforms (imported by the test suite)
# --------------------------------------------------------------------------

def apply_fence(text, block):
    """Insert/replace the Xylem fenced block in CLAUDE.md text. Idempotent."""
    block = block.strip("\n")
    begin = text.find(FENCE_BEGIN)
    end = text.find(FENCE_END)
    if begin != -1 and end != -1 and end > begin:
        end_close = end + len(FENCE_END)
        return text[:begin] + block + text[end_close:]
    # Append at end, separated by a blank line.
    if text and not text.endswith("\n"):
        text += "\n"
    if text and not text.endswith("\n\n"):
        text += "\n"
    return text + block + "\n"


def remove_fence(text):
    """Remove the Xylem fenced block, leaving surrounding text intact."""
    begin = text.find(FENCE_BEGIN)
    end = text.find(FENCE_END)
    if begin == -1 or end == -1 or end < begin:
        return text
    end_close = end + len(FENCE_END)
    before = text[:begin].rstrip("\n")
    after = text[end_close:].lstrip("\n")
    if before and after:
        return before + "\n\n" + after
    tail = before + after
    return tail + "\n" if tail else ""


def build_stdio_entry(server, mapping):
    """Build a Claude Code stdio MCP server entry from a manifest server."""
    return {
        "type": "stdio",
        "command": server["command"],
        "args": resolve_placeholders(server.get("args", []), mapping),
        "env": resolve_placeholders(server.get("env", {}), mapping),
    }


def build_http_entry(server, env_lookup, warn):
    """Build an http MCP server entry, or None if its URL env var is unset.

    URL and token are read from the environment only. Nothing is hardcoded.
    """
    url_key = server.get("url_env_key")
    url = env_lookup(url_key) if url_key else None
    if not url:
        warn("server '%s' skipped: env var %s is not set" % (server["name"], url_key))
        return None
    entry = {"type": "http", "url": url}
    headers = {}
    for header_name, spec in (server.get("headers") or {}).items():
        env_key = spec.get("env_key")
        token = env_lookup(env_key) if env_key else None
        if not token:
            warn("server '%s': header '%s' omitted (env var %s not set)"
                 % (server["name"], header_name, env_key))
            continue
        fmt = spec.get("format", "{value}")
        headers[header_name] = fmt.replace("{value}", token)
    if headers:
        entry["headers"] = headers
    return entry


def merge_mcp_servers(settings, entries):
    """Add/overwrite Xylem server entries without touching foreign servers."""
    mcp = settings.setdefault("mcpServers", {})
    for name, entry in entries.items():
        mcp[name] = entry
    return settings


def remove_mcp_servers(settings, names):
    """Remove named Xylem servers; drop the section if it becomes empty."""
    mcp = settings.get("mcpServers")
    if isinstance(mcp, dict):
        for name in names:
            mcp.pop(name, None)
        if not mcp:
            settings.pop("mcpServers", None)
    return settings


def _is_xylem_hook_group(group, marker):
    for hook in (group.get("hooks") or []):
        if marker in (hook.get("command") or ""):
            return True
    return False


def merge_hooks(settings, command, marker=HOOK_MARKER):
    """Register the SessionStart hook once. Idempotent: replaces any prior one."""
    hooks = settings.setdefault("hooks", {})
    session_start = hooks.setdefault("SessionStart", [])
    session_start[:] = [g for g in session_start if not _is_xylem_hook_group(g, marker)]
    session_start.append({"hooks": [{"type": "command", "command": command}]})
    return settings


def remove_hooks(settings, marker=HOOK_MARKER):
    """Remove the Xylem SessionStart hook; prune empty containers."""
    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        return settings
    session_start = hooks.get("SessionStart")
    if isinstance(session_start, list):
        session_start[:] = [g for g in session_start if not _is_xylem_hook_group(g, marker)]
        if not session_start:
            hooks.pop("SessionStart", None)
    if not hooks:
        settings.pop("hooks", None)
    return settings


def merge_env(settings, key, value):
    settings.setdefault("env", {})[key] = value
    return settings


def remove_env(settings, key):
    env = settings.get("env")
    if isinstance(env, dict):
        env.pop(key, None)
        if not env:
            settings.pop("env", None)
    return settings


# --------------------------------------------------------------------------
# Settings transform assembly
# --------------------------------------------------------------------------

def enabled_servers(manifest):
    return [s for s in manifest.get("servers", []) if s.get("available", True)]

def all_server_names(manifest):
    return [s["name"] for s in manifest.get("servers", [])]


def build_settings_install(settings, manifest, mapping, ck_server_path,
                           hook_command, warn):
    """Apply every Xylem install transform to a settings dict (in place)."""
    entries = {}
    for server in enabled_servers(manifest):
        transport = server.get("transport")
        if transport == "stdio":
            entries[server["name"]] = build_stdio_entry(server, mapping)
        elif transport == "http":
            entry = build_http_entry(server, os.environ.get, warn)
            if entry is not None:
                entries[server["name"]] = entry
        else:
            warn("server '%s' skipped: unknown transport '%s'"
                 % (server.get("name"), transport))
    merge_mcp_servers(settings, entries)
    merge_env(settings, ENV_KEY, ck_server_path)
    merge_hooks(settings, hook_command)
    return settings


def build_settings_uninstall(settings, manifest):
    remove_mcp_servers(settings, all_server_names(manifest))
    remove_hooks(settings)
    remove_env(settings, ENV_KEY)
    return settings


# --------------------------------------------------------------------------
# Filesystem plan / apply
# --------------------------------------------------------------------------

class Planner:
    """Collects intended file writes/removes; renders diffs or applies them."""

    def __init__(self, dry_run):
        self.dry_run = dry_run
        self.changes = []  # (path, old_text, new_text_or_None)
        self.warnings = []

    def warn(self, msg):
        self.warnings.append(msg)

    def set_text(self, path, new_text):
        self.changes.append((path, read_text(path), new_text))

    def remove(self, path):
        if os.path.exists(path):
            self.changes.append((path, read_text(path), None))

    def render(self):
        out = []
        for path, old, new in self.changes:
            if new is None:
                out.append("DELETE %s" % path)
                continue
            if old == new:
                out.append("UNCHANGED %s" % path)
                continue
            verb = "MODIFY" if old else "CREATE"
            out.append("%s %s" % (verb, path))
            diff = difflib.unified_diff(
                old.splitlines(True), new.splitlines(True),
                fromfile=path + " (before)", tofile=path + " (after)")
            out.extend(line.rstrip("\n") for line in diff)
        return "\n".join(out)

    def apply(self):
        applied = []
        for path, old, new in self.changes:
            if new is None:
                if os.path.exists(path):
                    self._backup(path)
                    os.remove(path)
                    applied.append("removed %s" % path)
                continue
            if old == new:
                continue
            parent = os.path.dirname(path)
            if parent and not os.path.isdir(parent):
                os.makedirs(parent, exist_ok=True)
            if os.path.exists(path):
                self._backup(path)
            with open(path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(new)
            applied.append("wrote %s" % path)
        return applied

    @staticmethod
    def _backup(path):
        backup = path + BACKUP_SUFFIX
        # Preserve the pristine original: back up only on the first write.
        if not os.path.exists(backup):
            shutil.copy2(path, backup)


# --------------------------------------------------------------------------
# Environment detection
# --------------------------------------------------------------------------

def detect_claude_dir():
    """Locate the Claude Code config directory across platforms."""
    candidates = []
    if os.name == "nt":
        appdata = os.environ.get("APPDATA")
        if appdata:
            candidates.append(os.path.join(appdata, "Claude"))
    home = os.path.expanduser("~")
    candidates.append(os.path.join(home, ".claude"))

    for cand in candidates:
        if os.path.isfile(os.path.join(cand, "settings.json")):
            return cand
    # Fall back to the conventional location so a fresh machine still installs.
    return candidates[-1]


# --------------------------------------------------------------------------
# CLI driver
# --------------------------------------------------------------------------

def load_manifest():
    with open(os.path.join(ROOT, "manifest.json"), "r", encoding="utf-8") as fh:
        return json.load(fh)


def build_mapping(project_dir):
    return {
        "$XYLEM_PARENT": to_fwd(PARENT),
        "$XYLEM_ROOT": to_fwd(ROOT),
        "$PROJECT_DIR": to_fwd(project_dir),
        "$AGENT_ID": os.environ.get("XYLEM_AGENT_ID", "claude-code"),
    }


def plan(args):
    manifest = load_manifest()
    claude_dir = detect_claude_dir()
    settings_path = os.path.join(claude_dir, "settings.json")

    if args.project:
        claude_md_path = os.path.join(os.path.abspath(args.project), "CLAUDE.md")
        project_dir = os.path.abspath(args.project)
    else:
        claude_md_path = os.path.join(claude_dir, "CLAUDE.md")
        project_dir = os.getcwd()

    commands_path = os.path.join(claude_dir, "commands", "xylem-discipline.md")

    planner = Planner(args.dry_run)

    settings = load_json_text(read_text(settings_path))

    if args.uninstall:
        build_settings_uninstall(settings, manifest)
        planner.set_text(settings_path, dump_json_text(settings))
        # CLAUDE.md: strip the fence only.
        planner.set_text(claude_md_path, remove_fence(read_text(claude_md_path)))
        # Remove the slash command file entirely.
        planner.remove(commands_path)
        return planner

    # Install.
    mapping = build_mapping(project_dir)
    ck_server_path = to_fwd(os.path.join(PARENT, "context-keeper", "server.py"))
    hook_script = to_fwd(os.path.join(ROOT, "artifacts", "session_start_hook.py"))
    hook_command = '"%s" "%s"' % (to_fwd(sys.executable), hook_script)

    build_settings_install(settings, manifest, mapping, ck_server_path,
                           hook_command, planner.warn)
    planner.set_text(settings_path, dump_json_text(settings))

    block = read_text(os.path.join(ROOT, "artifacts", "claude_md_block.md"))
    planner.set_text(claude_md_path, apply_fence(read_text(claude_md_path), block))

    discipline = read_text(os.path.join(ROOT, "artifacts", "xylem_discipline.md"))
    planner.set_text(commands_path, discipline)

    return planner


def main(argv=None):
    parser = argparse.ArgumentParser(description="Install the Xylem suite into Claude Code.")
    parser.add_argument("--dry-run", action="store_true",
                        help="print exact diffs and write nothing")
    parser.add_argument("--uninstall", action="store_true",
                        help="remove only Xylem-owned entries")
    parser.add_argument("--project", metavar="PATH",
                        help="target the project's CLAUDE.md instead of the global one")
    args = parser.parse_args(argv)

    try:
        planner = plan(args)
    except FileNotFoundError as exc:
        print("xylem: %s" % exc, file=sys.stderr)
        return 1

    for msg in planner.warnings:
        print("xylem: warning: %s" % msg, file=sys.stderr)

    if args.dry_run:
        print("xylem: dry run -- no files will be written\n")
        rendered = planner.render()
        print(rendered if rendered else "xylem: nothing to do")
        return 0

    applied = planner.apply()
    if applied:
        for line in applied:
            print("xylem: %s" % line)
        action = "uninstalled" if args.uninstall else "installed"
        print("xylem: %s (backups written as *%s)" % (action, BACKUP_SUFFIX))
    else:
        print("xylem: already up to date -- no changes")
    return 0


if __name__ == "__main__":
    sys.exit(main())

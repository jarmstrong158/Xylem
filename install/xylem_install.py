#!/usr/bin/env python3
"""
Xylem suite MCP installer.

Stdlib Python 3 only — zero dependencies, cross-platform (macOS/Linux/Windows).

Detects installed coding agents, locates each one's MCP config file, and merges
in the Xylem suite servers declared in servers.json. Handles BOTH transports:
local stdio processes and remote HTTP (Cloudflare Worker) connectors, per the
per-tool declaration in the manifest.

Safety posture:
  * Dry-run is the DEFAULT. Nothing is written unless you pass --apply.
  * Every file is backed up before it is changed.
  * Merges are additive: a server key that already exists and was NOT added by
    this installer is left untouched (never clobbered).
  * Re-running is idempotent: a second run computes identical content and writes
    nothing.
  * If a config file cannot be parsed as strict JSON (e.g. a settings file with
    comments), it is NOT rewritten — the snippet to add manually is printed.
  * Uninstall removes only the entries this installer recorded in its state file.

Usage:
    python3 xylem_install.py install            # dry-run: show the diff
    python3 xylem_install.py install --apply     # actually write
    python3 xylem_install.py uninstall           # dry-run
    python3 xylem_install.py uninstall --apply    # actually remove
    python3 xylem_install.py install --only cursor,vscode
    python3 xylem_install.py install --config /path/to/xylem.config.json
"""

import argparse
import difflib
import json
import os
import platform
import re
import shutil
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_MANIFEST = HERE / "servers.json"
PLACEHOLDER = re.compile(r"\$\{([A-Z0-9_]+)\}")
IS_WINDOWS = platform.system() == "Windows"
IS_MAC = platform.system() == "Darwin"


# --------------------------------------------------------------------------- #
# small output helpers
# --------------------------------------------------------------------------- #
def out(msg=""):
    print(msg)


def warn(msg):
    print("  ! " + msg)


def info(msg):
    print("  - " + msg)


# --------------------------------------------------------------------------- #
# interpreter resolution (for stdio 'python3' launch commands)
# --------------------------------------------------------------------------- #
def resolve_python(get):
    override = get("XYLEM_PYTHON")
    if override:
        return override
    for name in ("python3", "python"):
        found = shutil.which(name)
        if found:
            return found
    return sys.executable or "python3"


# --------------------------------------------------------------------------- #
# config / manifest loading + placeholder resolution
# --------------------------------------------------------------------------- #
def read_json_strict(path):
    """Return (data, error). Missing/empty file -> ({}, None). Parse failure -> (None, msg)."""
    if not path.exists():
        return {}, None
    text = path.read_text(encoding="utf-8")
    if text.strip() == "":
        return {}, None
    try:
        return json.loads(text), None
    except json.JSONDecodeError as exc:
        return None, "not strict JSON (%s) at line %d" % (exc.msg, exc.lineno)


def load_user_config(explicit):
    """Locate the untracked xylem.config.json. Returns a flat dict (may be empty)."""
    candidates = []
    if explicit:
        candidates.append(Path(explicit))
    if os.environ.get("XYLEM_CONFIG"):
        candidates.append(Path(os.environ["XYLEM_CONFIG"]))
    candidates.append(HERE / "xylem.config.json")
    candidates.append(config_home() / "xylem" / "config.json")
    for c in candidates:
        data, err = read_json_strict(c)
        if err:
            warn("config file %s is %s — ignoring it" % (c, err))
            continue
        if data:
            info("using config: %s" % c)
            return {k: v for k, v in data.items() if not k.startswith("_")}
    return {}


def make_getter(cfg):
    """Env vars win over the config file; both are treated as sources of ${NAME}."""
    def get(key):
        val = os.environ.get(key)
        if val is not None and val != "":
            return val
        return cfg.get(key)
    return get


def resolve_str(s, get):
    missing = set()

    def repl(m):
        key = m.group(1)
        val = get(key)
        if val is None or val == "":
            missing.add(key)
            return m.group(0)
        return str(val)

    return PLACEHOLDER.sub(repl, s), missing


def build_server(decl, get):
    """Resolve one manifest entry into a concrete server, or (None, missing_required)."""
    required = set(decl.get("required", []))
    name = decl["name"]
    transport = decl["transport"]
    missing_required = set()

    if transport == "http":
        url, miss = resolve_str(decl["url"], get)
        missing_required |= miss & required
        if missing_required:
            return None, sorted(missing_required)
        return {"name": name, "transport": "http", "url": url}, []

    # stdio
    command = decl["command"]
    if command in ("python", "python3"):
        command = resolve_python(get)

    args = []
    for a in decl.get("args", []):
        r, miss = resolve_str(a, get)
        missing_required |= miss & required
        args.append(r)

    env = {}
    for k, v in decl.get("env", {}).items():
        r, miss = resolve_str(v, get)
        if miss:
            # A missing REQUIRED placeholder blocks the whole server; a missing
            # OPTIONAL one just drops that single env key.
            missing_required |= miss & required
            if not (miss & required):
                continue
        else:
            env[k] = r

    if missing_required:
        return None, sorted(missing_required)
    return {"name": name, "transport": "stdio", "command": command, "args": args, "env": env}, []


# --------------------------------------------------------------------------- #
# OS-specific base directories
# --------------------------------------------------------------------------- #
def config_home():
    if IS_WINDOWS:
        base = os.environ.get("APPDATA")
        return Path(base) if base else Path.home() / "AppData" / "Roaming"
    return Path.home() / ".config"


def appdata():
    base = os.environ.get("APPDATA")
    return Path(base) if base else Path.home() / "AppData" / "Roaming"


def app_support():
    return Path.home() / "Library" / "Application Support"


# --------------------------------------------------------------------------- #
# Agent adapters
#
# Each adapter knows: how to detect the agent, where its MCP config file lives on
# this OS, and how to render an entry in that agent's OWN schema. Three schema
# families cover the field:
#   mcpServers : {"mcpServers": {name: {...}}}          (Claude Code/Desktop, Cursor, Windsurf)
#   vscode     : {"servers":    {name: {"type": ...}}}  (VS Code mcp.json)
#   zed        : {"context_servers": {name: {...}}}     (Zed settings.json)
# http_style is how a remote URL entry is rendered, or None if the agent's config
# cannot express a remote server (then http servers are skipped with a note).
# --------------------------------------------------------------------------- #
class Adapter:
    def __init__(self, aid, name, schema, http_style, path_fn, detect_fn):
        self.id = aid
        self.name = name
        self.schema = schema
        self.http_style = http_style
        self._path_fn = path_fn
        self._detect_fn = detect_fn

    @property
    def container_key(self):
        return {"mcpServers": "mcpServers", "vscode": "servers", "zed": "context_servers"}[self.schema]

    def path(self):
        return self._path_fn()

    def detected(self):
        try:
            return self._detect_fn()
        except Exception:
            return False

    def render(self, server):
        """Return the agent-schema entry dict, or None if unsupported (http on a stdio-only agent)."""
        if server["transport"] == "stdio":
            if self.schema == "vscode":
                entry = {"type": "stdio", "command": server["command"], "args": server["args"]}
            elif self.schema == "zed":
                entry = {"source": "custom", "command": server["command"], "args": server["args"]}
            else:
                entry = {"command": server["command"], "args": server["args"]}
            if server["env"]:
                entry["env"] = server["env"]
            return entry
        # http
        if self.http_style is None:
            return None
        if self.http_style == "type-url":
            return {"type": "http", "url": server["url"]}
        if self.http_style == "url":
            return {"url": server["url"]}
        if self.http_style == "serverUrl":
            return {"serverUrl": server["url"]}
        return None


def _vscode_user_dir():
    if IS_WINDOWS:
        return appdata() / "Code" / "User"
    if IS_MAC:
        return app_support() / "Code" / "User"
    return Path.home() / ".config" / "Code" / "User"


def _claude_desktop_cfg():
    if IS_WINDOWS:
        return appdata() / "Claude" / "claude_desktop_config.json"
    if IS_MAC:
        return app_support() / "Claude" / "claude_desktop_config.json"
    return Path.home() / ".config" / "Claude" / "claude_desktop_config.json"


def _zed_settings():
    if IS_WINDOWS:
        return appdata() / "Zed" / "settings.json"
    return Path.home() / ".config" / "zed" / "settings.json"


def build_adapters():
    home = Path.home()
    return [
        Adapter(
            "claude-code", "Claude Code", "mcpServers", "type-url",
            lambda: home / ".claude.json",
            lambda: (home / ".claude.json").exists() or (home / ".claude").is_dir() or bool(shutil.which("claude")),
        ),
        Adapter(
            "cursor", "Cursor", "mcpServers", "url",
            lambda: home / ".cursor" / "mcp.json",
            lambda: (home / ".cursor").is_dir(),
        ),
        Adapter(
            "windsurf", "Windsurf", "mcpServers", "serverUrl",
            lambda: home / ".codeium" / "windsurf" / "mcp_config.json",
            lambda: (home / ".codeium" / "windsurf").is_dir(),
        ),
        Adapter(
            "vscode", "VS Code", "vscode", "type-url",
            lambda: _vscode_user_dir() / "mcp.json",
            lambda: _vscode_user_dir().is_dir(),
        ),
        Adapter(
            "claude-desktop", "Claude Desktop", "mcpServers", None,
            _claude_desktop_cfg,
            lambda: _claude_desktop_cfg().parent.is_dir(),
        ),
        Adapter(
            "zed", "Zed", "zed", None,
            _zed_settings,
            lambda: _zed_settings().parent.is_dir(),
        ),
    ]


# --------------------------------------------------------------------------- #
# state file (records what we added, so uninstall is surgical)
# --------------------------------------------------------------------------- #
def state_path():
    return config_home() / "xylem" / "installer-state.json"


def load_state():
    data, err = read_json_strict(state_path())
    if err or not isinstance(data, dict):
        return {"version": 1, "entries": []}
    data.setdefault("entries", [])
    return data


def save_state(state):
    p = state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


def state_owns(state, path, server_name):
    sp = str(path)
    return any(e["path"] == sp and e["server"] == server_name for e in state["entries"])


def state_add(state, adapter, path, server_name):
    if state_owns(state, path, server_name):
        return
    state["entries"].append(
        {"agent": adapter.id, "path": str(path), "container_key": adapter.container_key, "server": server_name}
    )


# --------------------------------------------------------------------------- #
# JSON write with backup + diff
# --------------------------------------------------------------------------- #
def dumps(data):
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def show_diff(path, old_text, new_text):
    diff = difflib.unified_diff(
        old_text.splitlines(keepends=True),
        new_text.splitlines(keepends=True),
        fromfile=str(path) + " (current)",
        tofile=str(path) + " (proposed)",
    )
    printed = False
    for line in diff:
        sys.stdout.write("    " + line if not line.endswith("\n") else "    " + line)
        printed = True
    if printed and not new_text.endswith("\n"):
        out()


def write_with_backup(path, new_text, apply):
    old_text = path.read_text(encoding="utf-8") if path.exists() else ""
    if old_text == new_text:
        return "unchanged", old_text
    if apply:
        if path.exists():
            backup = path.with_name(path.name + ".bak-" + time.strftime("%Y%m%d%H%M%S"))
            shutil.copy2(path, backup)
            info("backed up -> %s" % backup.name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(new_text, encoding="utf-8")
    return ("created" if old_text == "" else "updated"), old_text


# --------------------------------------------------------------------------- #
# install
# --------------------------------------------------------------------------- #
def cmd_install(args):
    manifest, err = read_json_strict(Path(args.manifest))
    if err or not manifest:
        out("Cannot read manifest %s: %s" % (args.manifest, err or "empty"))
        return 1
    cfg = load_user_config(args.config)
    get = make_getter(cfg)

    # resolve every declared server; report which are configured vs skipped
    servers, unconfigured = [], []
    for decl in manifest.get("servers", []):
        srv, missing = build_server(decl, get)
        if srv:
            servers.append(srv)
        else:
            unconfigured.append((decl["name"], missing))

    out("Xylem installer — %s" % ("APPLY" if args.apply else "DRY-RUN (no files written)"))
    out("Configured servers: %s" % (", ".join(s["name"] for s in servers) or "(none)"))
    for name, missing in unconfigured:
        info("skipping %s — not configured (missing: %s)" % (name, ", ".join(missing)))
    if not servers:
        out("\nNothing to install. Fill in install/xylem.config.json (see xylem.config.example.json).")
        return 0

    adapters = build_adapters()
    if args.only:
        wanted = {a.strip() for a in args.only.split(",") if a.strip()}
        adapters = [a for a in adapters if a.id in wanted]

    state = load_state()
    any_change = False

    for adapter in adapters:
        if not adapter.detected():
            continue
        path = adapter.path()
        out("\n== %s  (%s)" % (adapter.name, path))
        data, perr = read_json_strict(path)
        if perr:
            warn("%s — will NOT rewrite it. Add these manually:" % perr)
            for srv in servers:
                entry = adapter.render(srv)
                if entry is None:
                    info("(%s is remote/http; %s cannot express that in config — add via its UI)" % (srv["name"], adapter.name))
                    continue
                info('"%s": %s' % (srv["name"], json.dumps(entry)))
            continue

        if not isinstance(data.get(adapter.container_key, {}), dict):
            warn('"%s" in this file is not an object — skipping to avoid clobbering it.' % adapter.container_key)
            continue

        container = data.setdefault(adapter.container_key, {})
        planned = []  # (server_name, kind)
        for srv in servers:
            entry = adapter.render(srv)
            if entry is None:
                info("%s is remote/http — %s config can't express it (add via UI); skipping." % (srv["name"], adapter.name))
                continue
            name = srv["name"]
            if name in container:
                if state_owns(state, path, name):
                    if container[name] != entry:
                        container[name] = entry
                        planned.append((name, "update"))
                    # else identical -> idempotent no-op
                else:
                    info('"%s" already present (not added by Xylem) — leaving as-is.' % name)
                continue
            container[name] = entry
            planned.append((name, "add"))

        new_text = dumps(data)
        status, old_text = write_with_backup(path, new_text, args.apply)
        if status == "unchanged":
            info("no changes needed.")
            continue
        any_change = True
        verb = {"created": "would create", "updated": "would update"}[status] if not args.apply else status.upper()
        out("  %s: %s" % (verb, ", ".join("%s (%s)" % (n, k) for n, k in planned) or "(formatting)"))
        show_diff(path, old_text, new_text)
        if args.apply:
            for name, _kind in planned:
                state_add(state, adapter, path, name)

    if args.apply:
        save_state(state)
        out("\nDone. State recorded at %s" % state_path())
    else:
        out("\nDry-run complete. Re-run with --apply to write the changes above.")
    if not any_change:
        out("(Everything already up to date.)" if args.apply else "(No changes would be made.)")
    return 0


# --------------------------------------------------------------------------- #
# uninstall
# --------------------------------------------------------------------------- #
def cmd_uninstall(args):
    state = load_state()
    if not state["entries"]:
        out("No Xylem installer state found — nothing recorded to remove.")
        return 0

    out("Xylem uninstaller — %s" % ("APPLY" if args.apply else "DRY-RUN (no files written)"))
    by_path = {}
    for e in state["entries"]:
        by_path.setdefault(e["path"], []).append(e)

    remaining = []
    for path_str, entries in by_path.items():
        path = Path(path_str)
        out("\n== %s" % path)
        data, perr = read_json_strict(path)
        if perr:
            warn("%s — will NOT rewrite it. Remove these keys manually: %s"
                 % (perr, ", ".join(e["server"] for e in entries)))
            remaining.extend(entries)
            continue
        if data is None:
            data = {}
        removed = []
        for e in entries:
            container = data.get(e["container_key"])
            if isinstance(container, dict) and e["server"] in container:
                del container[e["server"]]
                removed.append(e["server"])
            # if it's gone already, treat as removed (idempotent)
        new_text = dumps(data)
        status, old_text = write_with_backup(path, new_text, args.apply)
        if removed:
            out("  %s: %s" % ("removed" if args.apply else "would remove", ", ".join(removed)))
        if status != "unchanged":
            show_diff(path, old_text, new_text)
        if not args.apply:
            remaining.extend(entries)  # dry-run keeps state intact

    if args.apply:
        state["entries"] = remaining
        save_state(state)
        out("\nDone. Removed Xylem entries; every other server and setting left untouched.")
    else:
        out("\nDry-run complete. Re-run with --apply to remove the entries above.")
    return 0


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="xylem_install.py",
        description="Merge the Xylem suite's MCP servers into your coding agents (stdio + http).",
    )
    sub = parser.add_subparsers(dest="command")

    def add_common(sp):
        sp.add_argument("--apply", action="store_true", help="actually write changes (default is dry-run)")
        sp.add_argument("--only", help="comma-separated agent ids to target (e.g. cursor,vscode)")

    pi = sub.add_parser("install", help="add Xylem servers to detected agents")
    add_common(pi)
    pi.add_argument("--config", help="path to xylem.config.json")
    pi.add_argument("--manifest", default=str(DEFAULT_MANIFEST), help="path to servers.json")

    pu = sub.add_parser("uninstall", help="remove only the Xylem servers this installer added")
    add_common(pu)

    sub.add_parser("list-agents", help="show which supported agents are detected on this machine")

    args = parser.parse_args(argv)
    if args.command == "install":
        return cmd_install(args)
    if args.command == "uninstall":
        return cmd_uninstall(args)
    if args.command == "list-agents":
        out("Detected agents:")
        for a in build_adapters():
            mark = "yes" if a.detected() else "no "
            out("  [%s] %-16s %s" % (mark, a.id, a.path()))
        return 0
    parser.print_help()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BrokenPipeError:
        # Output was piped to a reader that closed early (e.g. `| head`). Exit quietly.
        try:
            sys.stdout.close()
        except Exception:
            pass
        os._exit(0)
    except KeyboardInterrupt:
        os._exit(130)

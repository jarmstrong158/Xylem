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
    (The repo-ROOT ./install.sh is a different script that applies immediately.)
  * Every file is backed up before it is changed. Configs and backups are
    chmod 0600 because the remote connector URL is itself the credential, and
    the same URL is redacted out of all diff/warning output.
  * `uninstall --apply` deletes the backups it made, so removing the suite does
    not leave a working token on disk.
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

# Backups this installer creates: "<config>.bak-YYYYMMDDHHMMSS".
BACKUP_TS_FMT = "%Y%m%d%H%M%S"
BACKUP_RE = re.compile(r"\.bak-(\d{14})$")
BACKUP_MAX_AGE_DAYS = 30   # older Xylem backups are pruned on every --apply
BACKUP_KEEP = 3            # ...but always keep this many most-recent ones
SECRET_MODE = 0o600        # configs and backups carry connector tokens

# The remote Worker URLs authenticate on the path token alone, so the whole
# ".../mcp/<token>" URL is a live credential. Never print it.
_URL_PATH_TOKEN = re.compile(r"https?://[^\s\"'\\]+?/mcp/[^\s\"'\\?#]+")
_URL_QUERY = re.compile(r"(https?://[^\s\"'\\?#]+)\?[^\s\"'\\]*")


def redact(text):
    """Mask connector tokens in anything we are about to print.

    Dry-run output is the thing people paste into bug reports; it must never
    carry a working credential.
    """
    if not text:
        return text
    text = _URL_PATH_TOKEN.sub(".../mcp/<redacted>", text)
    text = _URL_QUERY.sub(r"\1?<redacted>", text)
    return text


# --------------------------------------------------------------------------- #
# small output helpers
# --------------------------------------------------------------------------- #
def out(msg=""):
    print(redact(msg))


def warn(msg):
    print("  ! " + redact(msg))


def info(msg):
    print("  - " + redact(msg))


# --------------------------------------------------------------------------- #
# interpreter resolution (for stdio '$PYTHON' launch commands)
# --------------------------------------------------------------------------- #
# The policy lives in the repo-root xylem_interpreter module, shared with
# installer.py. These two installers used to resolve the interpreter by OPPOSITE
# strategies -- this one tried shutil.which("python3") first, which dec-013
# records as the cause of broken Windows installs (python3 is the Microsoft
# Store shim; the interpreter that has `mcp` is `python`). One policy, one file.
sys.path.insert(0, str(HERE.parent))
try:
    import xylem_interpreter
except ImportError as _exc:  # pragma: no cover - broken checkout
    raise SystemExit(
        "xylem_install: cannot import xylem_interpreter from %s (%s).\n"
        "This script resolves the server interpreter through the shared policy "
        "module at the repo root; copying xylem_install.py out of the repo on "
        "its own leaves it without one. Run it from a full xylem checkout."
        % (HERE.parent, _exc)
    )


def resolve_python(get):
    """The interpreter to launch the stdio servers with (shared policy)."""
    return xylem_interpreter.resolve_python(get)


# --------------------------------------------------------------------------- #
# config / manifest loading + placeholder resolution
# --------------------------------------------------------------------------- #
def read_text_raw(path):
    """Read text WITHOUT newline translation, so \\r\\n survives a round-trip."""
    return path.read_bytes().decode("utf-8")


def read_json_strict(path):
    """Return (data, error). Missing/empty file -> ({}, None). Parse failure -> (None, msg).

    A JSON document whose top level is not an object (e.g. a bare ``[...]``) is
    reported as an error rather than returned: every caller here indexes the
    result like a mapping, and returning a list turns into an AttributeError
    that kills the whole run.
    """
    if not path.exists():
        return {}, None
    text = read_text_raw(path)
    if text.strip() == "":
        return {}, None
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        return None, "not strict JSON (%s) at line %d" % (exc.msg, exc.lineno)
    if not isinstance(data, dict):
        return None, "top-level JSON is a %s, not an object" % type(data).__name__
    return data, None


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
    if xylem_interpreter.needs_resolution(command):
        # "$PYTHON" (what the generated servers.json now carries) and the legacy
        # bare "python"/"python3" spellings all mean "resolve an interpreter".
        command = resolve_python(get)

    args = []
    for a in decl.get("args", []):
        r, miss = resolve_str(a, get)
        missing_required |= miss & required
        optional_miss = miss - required
        if optional_miss:
            # Mirror the env handling below: an unresolved OPTIONAL placeholder
            # drops the item. Otherwise resolve_str's fallback (m.group(0))
            # writes a literal "${FOO}" straight into the user's config, and the
            # server is launched with a nonsense argument.
            # ASCII only: this goes to a console that may be cp1252, where an
            # em-dash renders as a replacement glyph.
            warn("%s: dropping arg %r - unset optional placeholder(s): %s"
                 % (name, a, ", ".join(sorted(optional_miss))))
            continue
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
        return {
            "mcpServers": "mcpServers",
            "vscode": "servers",
            "zed": "context_servers",
            "copilot": "mcpServers",
        }[self.schema]

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
                # Zed's ContextServerSettingsContent is an *untagged* enum: the
                # presence of "command" selects the stdio variant, and the inner
                # ContextServerCommand is #[serde(flatten)] with its `path` field
                # renamed to "command" — i.e. a bare string with sibling
                # args/env, NOT a nested {path, args} object (that was the
                # pre-2025-06-27 form). The "source" key was introduced in
                # 2025-06 and removed again by the 2025-11-25 migrator, so it is
                # obsolete; we no longer emit it.
                entry = {"command": server["command"], "args": server["args"]}
            elif self.schema == "copilot":
                entry = {"type": "local", "command": server["command"], "args": server["args"]}
            else:
                entry = {"command": server["command"], "args": server["args"]}
            if server["env"]:
                entry["env"] = server["env"]
            if self.schema == "copilot":
                entry["tools"] = ["*"]  # Copilot CLI gates tools per server; allow all.
            return entry
        # http
        if self.schema == "copilot":
            return {"type": "http", "url": server["url"], "tools": ["*"]}
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


def _copilot_home():
    # COPILOT_HOME replaces the entire ~/.copilot path when set.
    override = os.environ.get("COPILOT_HOME")
    return Path(override) if override else Path.home() / ".copilot"


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
            # Zed's http variant of context_servers is selected by a "url" key
            # (optionally with "headers"); confirmed in zed's settings_content
            # deserializer and documented at zed.dev/docs/ai/mcp.
            "zed", "Zed", "zed", "url",
            _zed_settings,
            lambda: _zed_settings().parent.is_dir(),
        ),
        Adapter(
            "copilot-cli", "GitHub Copilot CLI", "copilot", "type-url",
            lambda: _copilot_home() / "mcp-config.json",
            lambda: _copilot_home().is_dir() or bool(shutil.which("copilot")),
        ),
    ]


# --------------------------------------------------------------------------- #
# state file (records what we added, so uninstall is surgical)
# --------------------------------------------------------------------------- #
def state_path():
    return config_home() / "xylem" / "installer-state.json"


def norm_path(path):
    """Canonical key for a config path.

    Compared as raw strings, the *same* file can render differently between runs
    (drive-letter case on Windows, a junction/symlink vs the real path, a
    trailing separator). When that happens the installer stops recognising its
    own entry and silently stops both updating and uninstalling it. Resolve
    links and normalise case so the key is stable.
    """
    s = str(path)
    if not s:
        return ""
    try:
        resolved = os.path.realpath(s)
    except OSError:
        resolved = os.path.abspath(s)
    return os.path.normcase(os.path.normpath(resolved))


def load_state():
    data, err = read_json_strict(state_path())
    if err or not isinstance(data, dict):
        return {"version": 1, "entries": []}
    entries = data.get("entries")
    if not isinstance(entries, list):
        entries = []
    # Migrate entries written before paths were normalised.
    migrated = []
    for e in entries:
        if not isinstance(e, dict) or "path" not in e:
            continue
        e = dict(e)
        e["path"] = norm_path(e["path"])
        migrated.append(e)
    data["entries"] = migrated
    return data


def save_state(state):
    p = state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes((json.dumps(state, indent=2) + "\n").encode("utf-8"))
    secure_chmod(p)  # the state file names every config we touched


def state_owns(state, path, server_name):
    np = norm_path(path)
    return any(
        norm_path(e.get("path", "")) == np and e.get("server") == server_name
        for e in state["entries"]
    )


def state_add(state, adapter, path, server_name):
    if state_owns(state, path, server_name):
        return
    state["entries"].append(
        {
            "agent": adapter.id,
            "path": norm_path(path),
            "container_key": adapter.container_key,
            "server": server_name,
        }
    )


# --------------------------------------------------------------------------- #
# JSON write with backup + diff
# --------------------------------------------------------------------------- #
def sniff_indent(text, default=2):
    """Return the file's existing indent unit (int spaces, or '\\t'), else default.

    Reformatting a user's 4-space config to 2 spaces rewrites every line, which
    both trashes their formatting and makes the shown "diff" the entire file.
    """
    for line in text.splitlines():
        m = re.match(r"^([ \t]+)\S", line)
        if not m:
            continue
        lead = m.group(1)
        if lead[0] == "\t":
            return "\t"
        return len(lead)
    return default


def sniff_newline(text, default="\n"):
    """Return the file's dominant newline convention."""
    if not text:
        return default
    crlf = text.count("\r\n")
    lf = text.count("\n") - crlf
    if crlf and crlf >= lf:
        return "\r\n"
    if lf:
        return "\n"
    return default


def dumps(data, indent=2, newline="\n"):
    text = json.dumps(data, indent=indent, ensure_ascii=False) + "\n"
    if newline != "\n":
        text = text.replace("\n", newline)
    return text


def dumps_like(data, old_text):
    """Serialise `data` using the indentation and newlines already in `old_text`."""
    return dumps(data, indent=sniff_indent(old_text), newline=sniff_newline(old_text))


def secure_chmod(path):
    """Restrict a secret-bearing file to the owner. Best-effort (no-op on Windows ACLs)."""
    try:
        os.chmod(str(path), SECRET_MODE)
    except OSError:
        pass


def xylem_backups(path):
    """Every backup THIS installer created for `path`, newest first."""
    found = []
    try:
        siblings = list(path.parent.iterdir())
    except OSError:
        return found
    prefix = path.name + ".bak-"
    for p in siblings:
        if p.name.startswith(prefix) and BACKUP_RE.search(p.name):
            found.append(p)
    found.sort(key=lambda p: p.name, reverse=True)
    return found


def prune_backups(path, max_age_days=BACKUP_MAX_AGE_DAYS, keep=BACKUP_KEEP):
    """Delete stale Xylem backups. They hold live connector tokens; don't hoard them."""
    cutoff = time.time() - max_age_days * 86400
    removed = 0
    for old in xylem_backups(path)[keep:]:
        try:
            if old.stat().st_mtime < cutoff:
                old.unlink()
                removed += 1
        except OSError:
            pass
    if removed:
        info("pruned %d backup(s) older than %d days" % (removed, max_age_days))
    return removed


def purge_backups(path):
    """Delete ALL Xylem backups of `path` — used by `uninstall --apply`.

    An uninstall that leaves a `.bak-*` file behind leaves a working credential
    behind, which is exactly what the user believed they were removing.
    """
    removed = 0
    for b in xylem_backups(path):
        try:
            b.unlink()
            removed += 1
        except OSError:
            pass
    return removed


def show_diff(path, old_text, new_text):
    diff = difflib.unified_diff(
        old_text.splitlines(keepends=True),
        new_text.splitlines(keepends=True),
        fromfile=str(path) + " (current)",
        tofile=str(path) + " (proposed)",
    )
    printed = False
    for line in diff:
        sys.stdout.write("    " + redact(line))
        printed = True
    if printed and not new_text.endswith("\n"):
        out()


def write_with_backup(path, new_text, apply):
    old_text = read_text_raw(path) if path.exists() else ""
    if old_text == new_text:
        return "unchanged", old_text
    if apply:
        if path.exists():
            backup = path.with_name(path.name + ".bak-" + time.strftime(BACKUP_TS_FMT))
            shutil.copy2(path, backup)
            secure_chmod(backup)  # the backup carries the same tokens as the config
            info("backed up -> %s" % backup.name)
            prune_backups(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(new_text.encode("utf-8"))
        secure_chmod(path)
    return ("created" if old_text == "" else "updated"), old_text


# --------------------------------------------------------------------------- #
# cross-server consistency checks
# --------------------------------------------------------------------------- #
def _same_tree(a, b):
    """True if two path strings resolve to the same working tree."""
    na = os.path.normcase(os.path.normpath(os.path.abspath(a)))
    nb = os.path.normcase(os.path.normpath(os.path.abspath(b)))
    return na == nb


def _warn_divergent_trees(get):
    """Warn when context-keeper and cambium are pointed at different clones.

    cambium reads context-keeper's `.context/` from `CAMBIUM_REPO/.context/`, so
    a `CONTEXT_KEEPER_PROJECT` that differs from `CAMBIUM_REPO` means distill()
    silently captures nothing from context-keeper. Non-fatal: install proceeds.
    Also flags a coordination-branch mismatch between agentsync and cambium.
    """
    ck = get("CONTEXT_KEEPER_PROJECT")
    cam = get("CAMBIUM_REPO")
    if ck and cam and not _same_tree(ck, cam):
        warn("CONTEXT_KEEPER_PROJECT (%s) and CAMBIUM_REPO (%s) are different "
             "trees — cambium reads context-keeper's .context/ from "
             "CAMBIUM_REPO/.context/, so distill() will capture nothing from "
             "context-keeper. Point both at the same clone." % (ck, cam))

    as_branch = get("AGENTSYNC_BRANCH")
    cam_branch = get("CAMBIUM_AGENTSYNC_BRANCH")
    if as_branch and cam_branch and as_branch != cam_branch:
        warn("AGENTSYNC_BRANCH (%s) and CAMBIUM_AGENTSYNC_BRANCH (%s) differ — "
             "distill reads the wrong coordination branch and captures no "
             "agentsync events. They must name the same branch." % (as_branch, cam_branch))


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
    _warn_divergent_trees(get)

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

        if not isinstance(data, dict):
            warn("top-level JSON is not an object — skipping this file.")
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

        # Preserve the file's own indentation/newlines instead of reformatting
        # it end to end (which would make the diff below the entire file).
        existing_text = read_text_raw(path) if path.exists() else ""
        new_text = dumps_like(data, existing_text)
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

    # --only was previously registered but never read, so `uninstall --apply
    # --only cursor` silently wiped Xylem entries from EVERY recorded agent.
    targeted = state["entries"]
    untouched = []
    if getattr(args, "only", None):
        wanted = {a.strip() for a in args.only.split(",") if a.strip()}
        known = {a.id for a in build_adapters()}
        unknown = wanted - known
        if unknown:
            warn("unknown agent id(s) in --only: %s (known: %s)"
                 % (", ".join(sorted(unknown)), ", ".join(sorted(known))))
        targeted = [e for e in state["entries"] if e.get("agent") in wanted]
        untouched = [e for e in state["entries"] if e.get("agent") not in wanted]
        out("Limited to: %s" % ", ".join(sorted(wanted)))
        if not targeted:
            out("\nNothing recorded for those agents — nothing to remove.")
            return 0

    by_path = {}
    for e in targeted:
        by_path.setdefault(e["path"], []).append(e)

    remaining = list(untouched)
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
        existing_text = read_text_raw(path) if path.exists() else ""
        new_text = dumps_like(data, existing_text)
        status, old_text = write_with_backup(path, new_text, args.apply)
        if removed:
            out("  %s: %s" % ("removed" if args.apply else "would remove", ", ".join(removed)))
        if status != "unchanged":
            show_diff(path, old_text, new_text)
        if not args.apply:
            remaining.extend(entries)  # dry-run keeps state intact
        else:
            # Every .bak-* of this file still contains the connector token the
            # user just asked us to remove. Uninstall must not leave a live
            # credential on disk.
            n = purge_backups(path)
            if n:
                info("deleted %d Xylem backup(s) of this file (they contained connector tokens)" % n)

    if args.apply:
        state["entries"] = remaining
        save_state(state)
        out("\nDone. Removed Xylem entries; every other server and setting left untouched.")
    else:
        out("\nDry-run complete. Re-run with --apply to remove the entries above.")
        out("(--apply also DELETES this installer's .bak-* backups of those files —")
        out(" they contain the connector token, so an uninstall must not leave them.)")
    return 0


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="xylem_install.py",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Merge the Xylem suite's MCP servers into your coding agents (stdio + http).\n"
            "\n"
            "*** THIS INSTALLER DRY-RUNS BY DEFAULT. Nothing is written without --apply. ***\n"
            "\n"
            "NOTE: the repo-root ./install.sh is a DIFFERENT bootstrap that APPLIES\n"
            "immediately. install/install.sh and install/install.ps1 (which call this\n"
            "script) dry-run. Same filename, opposite defaults — check your path."
        ),
        epilog=(
            "examples:\n"
            "  xylem_install.py install                 # dry-run: print the diff\n"
            "  xylem_install.py install --apply         # write it\n"
            "  xylem_install.py uninstall --apply --only cursor\n"
        ),
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

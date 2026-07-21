#!/usr/bin/env python3
"""Xylem installer.

Installs the Xylem suite into Claude Code:
  - registers enabled MCP servers in settings.json (stdio + http)
  - injects a fenced discipline block into CLAUDE.md
  - registers the SessionStart memory-injection and version-check hooks
  - registers the SessionEnd distill hook (cambium capture leg)
  - installs the /xylem-discipline slash command

Design rules (non-negotiable):
  - stdlib only, Python 3.8+
  - never clobber existing config: back up before first write, merge additively,
    idempotent re-runs, and uninstall touches ONLY Xylem-owned entries
  - never hardcode Worker URLs or secrets: http server url/token come from env
  - cross-platform: macOS, Linux, Windows (JSON paths use forward slashes)

Usage:
  python3 installer.py [--dry-run] [--uninstall] [--project PATH]
  python3 installer.py update [--dry-run] [--project PATH]

The `update` verb git-pulls this repo, then re-applies the block so the deployed
CLAUDE.md carries the current manifest version stamp. It is the one-word "yes"
that the version_check nudge points at.
"""
import argparse
import difflib
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time

# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------

FENCE_BEGIN = "<!-- XYLEM:BEGIN -->"  # legacy/unstamped begin marker (== v1)
FENCE_END = "<!-- XYLEM:END -->"
# Matches both the legacy unstamped begin marker and the versioned form
# `<!-- XYLEM:BEGIN vN -->`. Group 1 is the integer version, or None if absent.
FENCE_BEGIN_RE = re.compile(r"<!-- XYLEM:BEGIN(?: v(\d+))? -->")
BACKUP_SUFFIX = ".xylem-backup"

# Marks the SessionStart hooks we own, so uninstall can find exactly them.
# Each hook is identified by its script filename appearing in the command.
HOOK_MARKER = "session_start_hook.py"
VERSION_CHECK_MARKER = "version_check.py"
# Sentinel written into every hook group we own. Ownership must be explicit:
# the script filenames above are generic enough that a bare substring test would
# happily match (and then delete) an unrelated tool's hook.
OWNER_KEY = "_xylem"
# Registered hook timeouts (seconds), matching the plugin path's hooks.json.
HOOK_TIMEOUT = 10
DISTILL_HOOK_TIMEOUT = 60
# The SessionEnd hook that fires cambium's distill() -- the capture leg of the
# compound-growth loop. Keyed by its own script-name marker like the others.
DISTILL_HOOK_MARKER = "session_end_hook.py"
# SessionStart hook that injects cambium's session_primer() (the recall leg).
PRIMER_HOOK_MARKER = "session_primer_hook.py"
# Primer reads local + team + org (a couple of git fetches), so give it headroom.
PRIMER_HOOK_TIMEOUT = 30
# settings.json env keys we own. The first points the SessionStart hook at
# context-keeper's server.py; the second points the SessionEnd hook at cambium.
ENV_KEY = "XYLEM_CONTEXT_KEEPER_PATH"
CAMBIUM_ENV_KEY = "XYLEM_CAMBIUM_PATH"

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


def dump_json_text(obj, indent=2):
    """Serialize deterministically so re-runs produce identical bytes.

    `indent` mirrors whatever the existing file used (see detect_json_indent) so
    reformatting a hand-maintained settings.json is never a side effect.
    """
    return json.dumps(obj, indent=indent, ensure_ascii=False) + "\n"


def detect_json_indent(text, default=2):
    """Sniff the indent unit of an existing JSON document.

    Returns an int (spaces) or "\\t", both of which json.dumps accepts.
    """
    match = re.search(r"[\{\[][^\S\n]*\r?\n([ \t]+)\S", text or "")
    if match is None:
        return default
    whitespace = match.group(1)
    if "\t" in whitespace:
        return "\t"
    return len(whitespace)


def detect_style(path):
    """(newline, has_bom) of an existing file; defaults for a missing one.

    The installer reads with universal newlines, so without this the write leg
    would silently rewrite every line of a CRLF file (and drop a UTF-8 BOM),
    turning a surgical edit into a whole-file git diff.
    """
    if not os.path.isfile(path):
        return "\n", False
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError:
        return "\n", False
    has_bom = raw.startswith(b"\xef\xbb\xbf")
    crlf = raw.count(b"\r\n")
    lf = raw.count(b"\n") - crlf
    newline = "\r\n" if crlf > lf else "\n"
    return newline, has_bom


def read_text(path):
    # utf-8-sig transparently strips a leading BOM if some editor/tool wrote one
    # (common on Windows), while reading plain UTF-8 unchanged. The BOM and the
    # newline convention are recovered separately by detect_style().
    if os.path.isfile(path):
        with open(path, "r", encoding="utf-8-sig") as fh:
            return fh.read()
    return ""


# A URL's path can be the whole credential (the Workers authenticate on
# /mcp/<token> alone), so anything URL-shaped is redacted before it reaches a
# terminal, a --dry-run diff, or a pasted CI log.
URL_RE = re.compile(r"https?://[^\s\"'<>\\]+")


def _redact_url(match):
    url = match.group(0)
    scheme, _, rest = url.partition("://")
    host, slash, path = rest.partition("/")
    if not slash or not path:
        return url
    # Keep the first path segment (structural, e.g. "mcp"); redact the rest,
    # along with any query string or fragment.
    head = path.split("?")[0].split("#")[0]
    segments = [s for s in head.split("/") if s]
    if not segments:
        return url
    kept = segments[0] if len(segments) > 1 else ""
    prefix = "%s://%s/%s" % (scheme, host, kept + "/" if kept else "")
    return prefix + "<redacted>"


def redact(text):
    """Mask credential-bearing URL paths anywhere in human-facing output."""
    return URL_RE.sub(_redact_url, text or "")


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

def fence_begin(version=None):
    """Render the begin marker, stamped with an integer version when given."""
    if version is None:
        return FENCE_BEGIN
    return "<!-- XYLEM:BEGIN v%d -->" % int(version)


def parse_fence_version(text):
    """Version of the Xylem block present in `text`.

    Returns the integer from a `<!-- XYLEM:BEGIN vN -->` marker, 1 for a legacy
    unstamped block (fence present but no version), or None if no fence is found.
    A deployed block with no stamp therefore counts as v1/stale.
    """
    match = FENCE_BEGIN_RE.search(text)
    if match is None:
        return None
    return int(match.group(1)) if match.group(1) else 1


def _warn_multiple_fences(text, warn):
    """Warn when more than one Xylem block is present.

    Every fence operation works on the FIRST block only, so a duplicate block
    would otherwise sit there orphaned and never be updated or removed again.
    """
    if warn is None:
        return
    begins = len(FENCE_BEGIN_RE.findall(text or ""))
    ends = (text or "").count(FENCE_END)
    if begins > 1 or ends > 1:
        warn("CLAUDE.md contains %d Xylem begin and %d end markers; only the "
             "first block is managed. Delete the extra block(s) by hand -- the "
             "installer will not touch them." % (begins, ends))


def apply_fence(text, block, version=None, warn=None):
    """Insert/replace the Xylem fenced block in CLAUDE.md text. Idempotent.

    When `version` is given, the block's own begin marker is (re)stamped to
    `<!-- XYLEM:BEGIN vN -->` so manifest.json stays the single source of truth
    for the deployed stamp. An existing block is detected and replaced whether it
    carries the old unstamped marker or any versioned one.
    """
    _warn_multiple_fences(text, warn)
    block = block.strip("\n")
    if version is not None:
        block = FENCE_BEGIN_RE.sub(fence_begin(version), block, count=1)
    match = FENCE_BEGIN_RE.search(text)
    end = text.find(FENCE_END)
    if match is not None and end != -1 and end > match.start():
        end_close = end + len(FENCE_END)
        return text[:match.start()] + block + text[end_close:]
    # Append at end, separated by a blank line.
    if text and not text.endswith("\n"):
        text += "\n"
    if text and not text.endswith("\n\n"):
        text += "\n"
    return text + block + "\n"


def remove_fence(text, warn=None):
    """Remove the Xylem fenced block, leaving surrounding text intact.

    Detects both the legacy unstamped and the versioned begin marker.
    """
    _warn_multiple_fences(text, warn)
    match = FENCE_BEGIN_RE.search(text)
    end = text.find(FENCE_END)
    if match is None or end == -1 or end < match.start():
        return text
    begin = match.start()
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


def _under_xylem_root(command):
    """True if `command` invokes a script inside this xylem checkout."""
    root = to_fwd(ROOT).rstrip("/")
    if not root:
        return False
    haystack = to_fwd(command or "")
    if os.name == "nt":  # Windows paths are case-insensitive
        haystack = haystack.lower()
        root = root.lower()
    return (root + "/") in haystack


def _is_xylem_hook_group(group, marker):
    """True only for hook groups this installer owns.

    Ownership is deliberately narrow. The script filenames we key on
    (session_start_hook.py, version_check.py, session_end_hook.py) are generic,
    so a bare substring test would also match -- and uninstall would then delete
    -- an unrelated tool's hook. A group qualifies when it carries our sentinel
    key, or (for legacy groups written before the sentinel existed) when the
    matching command resolves to a script under this xylem root.
    """
    if not isinstance(group, dict):
        return False
    owned = group.get(OWNER_KEY) is True
    for hook in (group.get("hooks") or []):
        if not isinstance(hook, dict):
            continue
        command = hook.get("command") or ""
        if marker not in command:
            continue
        if owned or _under_xylem_root(command):
            return True
    return False


def merge_hooks(settings, command, marker=HOOK_MARKER, event="SessionStart",
                timeout=HOOK_TIMEOUT):
    """Register a hook under `event` once. Idempotent: updates any prior one.

    `event` defaults to SessionStart (the memory-injection and version-check
    hooks); the SessionEnd distill hook passes event="SessionEnd".

    The existing group is updated IN PLACE: a remove-then-append would drop any
    key the user added to our group (e.g. `matcher`) and shuffle it to the end
    of the list, producing a spurious settings.json write on every run.
    """
    hooks = settings.setdefault("hooks", {})
    groups = hooks.setdefault(event, [])
    entry = {"type": "command", "command": command, "timeout": timeout}
    for group in groups:
        if not _is_xylem_hook_group(group, marker):
            continue
        group[OWNER_KEY] = True
        inner = group.get("hooks") or []
        for index, hook in enumerate(inner):
            if isinstance(hook, dict) and marker in (hook.get("command") or ""):
                hook.update(entry)
                inner[index] = hook
                break
        else:
            inner.append(entry)
        group["hooks"] = inner
        return settings
    groups.append({OWNER_KEY: True, "hooks": [entry]})
    return settings


def remove_hooks(settings, marker=HOOK_MARKER, event="SessionStart"):
    """Remove the Xylem hook from `event`; prune empty containers.

    Foreign hooks that merely mention the same script filename are left alone.
    """
    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        return settings
    groups = hooks.get(event)
    if isinstance(groups, list):
        groups[:] = [g for g in groups if not _is_xylem_hook_group(g, marker)]
        if not groups:
            hooks.pop(event, None)
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

def manifest_version(manifest):
    """Integer template version from the manifest; defaults to 1 if absent/bad."""
    raw = manifest.get("version", 1)
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 1


def enabled_servers(manifest):
    return [s for s in manifest.get("servers", []) if s.get("available", True)]

def all_server_names(manifest):
    return [s["name"] for s in manifest.get("servers", [])]


def build_settings_install(settings, manifest, mapping, ck_server_path,
                           cambium_server_path, hook_command,
                           version_check_command, distill_command,
                           primer_command, warn):
    """Apply every Xylem install transform to a settings dict (in place)."""
    entries = {}
    stale = []
    for server in enabled_servers(manifest):
        transport = server.get("transport")
        if transport == "stdio":
            entries[server["name"]] = build_stdio_entry(server, mapping)
        elif transport == "http":
            entry = build_http_entry(server, os.environ.get, warn)
            if entry is not None:
                entries[server["name"]] = entry
            else:
                # The URL is the credential. If it is now unset (rotated or
                # revoked), an entry we wrote earlier still holds the OLD token,
                # so leaving it in place would be worse than removing it.
                stale.append(server["name"])
        else:
            warn("server '%s' skipped: unknown transport '%s'"
                 % (server.get("name"), transport))
    existing = settings.get("mcpServers")
    dropped = [n for n in stale
               if isinstance(existing, dict) and n in existing]
    if dropped:
        remove_mcp_servers(settings, dropped)
        for name in dropped:
            warn("removed stale entry for skipped server '%s' (its URL env var "
                 "is no longer set)" % name)
    merge_mcp_servers(settings, entries)
    merge_env(settings, ENV_KEY, ck_server_path)
    merge_env(settings, CAMBIUM_ENV_KEY, cambium_server_path)
    # Two SessionStart hooks, each keyed by its own script-name marker so they
    # register independently and neither clobbers the other on re-run.
    merge_hooks(settings, hook_command)
    merge_hooks(settings, version_check_command, marker=VERSION_CHECK_MARKER)
    # SessionStart hook: inject cambium's session_primer() so RECALL is passive,
    # the mirror of the SessionEnd distill capture leg. Its own marker.
    merge_hooks(settings, primer_command, marker=PRIMER_HOOK_MARKER,
                timeout=PRIMER_HOOK_TIMEOUT)
    # SessionEnd hook: fire cambium's distill() so capture is passive. Keyed by
    # its own marker under the SessionEnd event.
    merge_hooks(settings, distill_command, marker=DISTILL_HOOK_MARKER,
                event="SessionEnd", timeout=DISTILL_HOOK_TIMEOUT)
    return settings


def build_settings_uninstall(settings, manifest):
    remove_mcp_servers(settings, all_server_names(manifest))
    remove_hooks(settings)
    remove_hooks(settings, marker=VERSION_CHECK_MARKER)
    remove_hooks(settings, marker=PRIMER_HOOK_MARKER)
    remove_hooks(settings, marker=DISTILL_HOOK_MARKER, event="SessionEnd")
    remove_env(settings, ENV_KEY)
    remove_env(settings, CAMBIUM_ENV_KEY)
    return settings


# --------------------------------------------------------------------------
# Filesystem plan / apply
# --------------------------------------------------------------------------

class Planner:
    """Collects intended file writes/removes; renders diffs or applies them."""

    def __init__(self, dry_run, state_path=None):
        self.dry_run = dry_run
        self.changes = []  # (path, old_text, new_text_or_None)
        self.warnings = []
        self.state_path = state_path
        self._state = None
        # Set on uninstall: keep the bookkeeping for the backup decisions in
        # this run, then drop the file so uninstall leaves nothing behind.
        self.discard_state = False

    def warn(self, msg):
        self.warnings.append(msg)

    def set_text(self, path, new_text):
        self.changes.append((path, read_text(path), new_text))

    def remove(self, path):
        if os.path.exists(path):
            self.changes.append((path, read_text(path), None))

    def render(self):
        """Human-facing preview. Credential-bearing URLs are masked: --dry-run
        output is the thing people paste into issues and CI logs."""
        return redact(self._render_raw())

    def _render_raw(self):
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
                    self._backup(path, old, None)
                    os.remove(path)
                    self._forget(path)
                    applied.append("removed %s" % path)
                continue
            if old == new:
                continue
            parent = os.path.dirname(path)
            if parent and not os.path.isdir(parent):
                os.makedirs(parent, exist_ok=True)
            newline, has_bom = detect_style(path)
            if os.path.exists(path):
                self._backup(path, old, new)
            encoding = "utf-8-sig" if has_bom else "utf-8"
            # newline=<nl> re-expands our "\n" text to the file's own convention
            # so a CRLF file does not come back as an all-lines git diff.
            with open(path, "w", encoding=encoding, newline=newline) as fh:
                fh.write(new)
            self._remember(path, new)
            applied.append("wrote %s" % path)
        if self.discard_state:
            self._state = {}
        self._save_state()
        return applied

    # -- last-written bookkeeping -------------------------------------------
    # Backing up only on first write means a later hand edit (e.g. to the
    # slash-command file, which we overwrite wholesale every run) is destroyed
    # with no recoverable copy. We therefore record a hash of what we last wrote
    # and back up whenever the on-disk content is neither that nor the new text.

    def _load_state(self):
        if self._state is None:
            self._state = {}
            if self.state_path and os.path.isfile(self.state_path):
                try:
                    self._state = json.loads(read_text(self.state_path)) or {}
                except ValueError:
                    self._state = {}
        return self._state

    @staticmethod
    def _digest(text):
        return hashlib.sha256((text or "").encode("utf-8")).hexdigest()

    def _remember(self, path, text):
        self._load_state()[to_fwd(os.path.abspath(path))] = self._digest(text)

    def _forget(self, path):
        self._load_state().pop(to_fwd(os.path.abspath(path)), None)

    def _save_state(self):
        if not self.state_path or self._state is None:
            return
        if not self._state:
            # Nothing left to track (e.g. after uninstall) -- leave no litter.
            try:
                if os.path.isfile(self.state_path):
                    os.remove(self.state_path)
            except OSError:
                pass
            return
        parent = os.path.dirname(self.state_path)
        try:
            if parent and not os.path.isdir(parent):
                os.makedirs(parent, exist_ok=True)
            with open(self.state_path, "w", encoding="utf-8") as fh:
                fh.write(json.dumps(self._state, indent=2) + "\n")
        except OSError:
            pass  # bookkeeping is best-effort; never fail an install over it

    def _backup(self, path, old, new):
        backup = path + BACKUP_SUFFIX
        if not os.path.exists(backup):
            # Preserve the pristine original.
            shutil.copy2(path, backup)
            return
        if old == new:
            return
        last = self._load_state().get(to_fwd(os.path.abspath(path)))
        if last is not None and self._digest(old) == last:
            return  # exactly what we wrote last time -- nothing of the user's
        # The on-disk content is neither our last write nor the new text, so it
        # holds hand edits. Keep them alongside the pristine backup.
        shutil.copy2(path, "%s.%d" % (backup, int(time.time())))


# --------------------------------------------------------------------------
# Environment detection
# --------------------------------------------------------------------------

def detect_claude_dir():
    """Locate the Claude Code config directory across platforms.

    Claude Code uses ~/.claude on every platform, overridable via
    CLAUDE_CONFIG_DIR. %APPDATA%\\Claude is Claude *Desktop's* config home and is
    deliberately NOT probed -- writing Claude Code settings there configures
    nothing and edits an unrelated app's file.
    """
    override = os.environ.get("CLAUDE_CONFIG_DIR")
    if override:
        return os.path.abspath(os.path.expanduser(override))
    home = os.path.expanduser("~")
    candidates = [os.path.join(home, ".claude")]

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


def resolve_python():
    """The interpreter to launch the stdio servers with.

    The manifest used to hardcode "python3". That is wrong on a very common
    Windows setup: `python3` resolves to the Microsoft Store shim while the
    interpreter that actually has `mcp` installed is `python`. The servers got
    registered into a config where they could never start, with no diagnostic.

    sys.executable is the interpreter running this installer, so if you could
    run the install, the servers can run -- and installing from a virtualenv
    registers that virtualenv, which is almost always what you want.
    """
    return to_fwd(sys.executable or "python3")


def build_mapping(project_dir):
    return {
        "$PYTHON": resolve_python(),
        "$XYLEM_PARENT": to_fwd(PARENT),
        "$XYLEM_ROOT": to_fwd(ROOT),
        "$PROJECT_DIR": to_fwd(project_dir),
        "$AGENT_ID": os.environ.get("XYLEM_AGENT_ID", "claude-code"),
    }


def resolve_targets(args, claude_dir):
    """Resolve the settings.json, CLAUDE.md, project dir, and command paths.

    With --project, the block targets that project's CLAUDE.md; otherwise the
    global CLAUDE.md under the Claude config dir.
    """
    settings_path = os.path.join(claude_dir, "settings.json")
    if getattr(args, "project", None):
        project_dir = os.path.abspath(args.project)
        claude_md_path = os.path.join(project_dir, "CLAUDE.md")
    else:
        project_dir = os.getcwd()
        claude_md_path = os.path.join(claude_dir, "CLAUDE.md")
    commands_path = os.path.join(claude_dir, "commands", "xylem-discipline.md")
    return settings_path, claude_md_path, project_dir, commands_path


def plan(args):
    manifest = load_manifest()
    claude_dir = detect_claude_dir()
    settings_path, claude_md_path, project_dir, commands_path = resolve_targets(
        args, claude_dir)

    planner = Planner(args.dry_run,
                      state_path=os.path.join(claude_dir, ".xylem-state.json"))

    settings_text = read_text(settings_path)
    indent = detect_json_indent(settings_text)
    try:
        settings = load_json_text(settings_text)
    except ValueError as exc:
        # A settings.json with // comments or a trailing comma is not strict
        # JSON. Rewriting it would mean guessing at the user's intent, and
        # crashing the whole run over it is worse than doing the other legs.
        planner.warn(
            "%s is not strict JSON (%s) -- leaving it untouched. Register the "
            "Xylem servers and hooks by hand, or fix the file and re-run."
            % (settings_path, exc))
        settings = None

    if args.uninstall:
        planner.discard_state = True
        if settings is not None:
            build_settings_uninstall(settings, manifest)
            planner.set_text(settings_path, dump_json_text(settings, indent))
        # CLAUDE.md: strip the fence only.
        planner.set_text(
            claude_md_path,
            remove_fence(read_text(claude_md_path), planner.warn))
        # Remove the slash command file entirely.
        planner.remove(commands_path)
        return planner

    # Install.
    mapping = build_mapping(project_dir)
    ck_server_path = to_fwd(os.path.join(PARENT, "context-keeper", "server.py"))
    cambium_server_path = to_fwd(
        os.path.join(PARENT, "cambium", "cambium_server.py"))
    hook_script = to_fwd(os.path.join(ROOT, "artifacts", "session_start_hook.py"))
    hook_command = '"%s" "%s"' % (to_fwd(sys.executable), hook_script)
    # Same $XYLEM_ROOT-relative resolution and interpreter as the hook above.
    version_check_script = to_fwd(
        os.path.join(ROOT, "artifacts", "version_check.py"))
    version_check_command = '"%s" "%s"' % (
        to_fwd(sys.executable), version_check_script)
    # SessionEnd distill hook -- same interpreter/resolution pattern.
    distill_script = to_fwd(
        os.path.join(ROOT, "artifacts", "session_end_hook.py"))
    distill_command = '"%s" "%s"' % (to_fwd(sys.executable), distill_script)
    # SessionStart primer hook -- the recall leg, same pattern as the others.
    primer_script = to_fwd(
        os.path.join(ROOT, "artifacts", "session_primer_hook.py"))
    primer_command = '"%s" "%s"' % (to_fwd(sys.executable), primer_script)

    if settings is not None:
        build_settings_install(settings, manifest, mapping, ck_server_path,
                               cambium_server_path, hook_command,
                               version_check_command, distill_command,
                               primer_command, planner.warn)
        planner.set_text(settings_path, dump_json_text(settings, indent))

    block = read_text(os.path.join(ROOT, "artifacts", "claude_md_block.md"))
    version = manifest_version(manifest)
    planner.set_text(
        claude_md_path,
        apply_fence(read_text(claude_md_path), block, version, planner.warn))

    discipline = read_text(os.path.join(ROOT, "artifacts", "xylem_discipline.md"))
    planner.set_text(commands_path, discipline)

    return planner


def _run_git(git_args, cwd):
    """Run a git command, returning (ok, last_output_line). Never raises."""
    try:
        proc = subprocess.run(
            ["git"] + list(git_args), cwd=cwd,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            universal_newlines=True)
    except Exception as exc:  # git missing, cwd gone, etc. -- fail soft
        return False, str(exc)
    out = (proc.stdout or "").strip()
    last = out.splitlines()[-1] if out else ""
    return proc.returncode == 0, last


# --------------------------------------------------------------------------
# Server fetch: make a clean machine work
# --------------------------------------------------------------------------
# The stdio servers live in sibling repos ($XYLEM_PARENT/<dir>), not inside
# this one. On the author's machine they are already checked out; on anyone
# else's they are absent, so the installer would register three servers whose
# script paths do not exist and which therefore die on launch with no
# diagnostic. Fetch clones the missing ones to the exact path the manifest
# resolves to, so what we clone and what we register can never drift.

# Public source host. Overridable for forks/mirrors, but never carries a
# secret (these are public repos), so unlike the Worker URLs it may be a literal
# default. Kept out of manifest.json so the no-hardcoded-URLs rule there holds.
DEFAULT_SOURCE_BASE = "https://github.com/"


def clone_url(repo, base=None):
    """Full git URL for a manifest `source.repo`.

    A bare `owner/name` slug becomes `<base><owner/name>.git`; anything already
    carrying a scheme or scp-style `git@` prefix is passed through untouched, so
    a fork can pin a full URL if it wants. `base` defaults to $XYLEM_SOURCE_BASE
    or the public GitHub host.
    """
    repo = (repo or "").strip()
    if "://" in repo or repo.startswith("git@"):
        return repo
    if base is None:
        base = os.environ.get("XYLEM_SOURCE_BASE") or DEFAULT_SOURCE_BASE
    base = base.rstrip("/") + "/"
    suffix = "" if repo.endswith(".git") else ".git"
    return base + repo + suffix


def server_script_path(server, mapping):
    """Resolved path of a stdio server's entry script, or None if it has none."""
    args = server.get("args") or []
    if not args:
        return None
    return resolve_placeholders(args[0], mapping)


def plan_fetch(manifest, mapping):
    """Fetch plan: one entry per stdio server that declares a `source`.

    Pure -- no network, no filesystem writes, only an existence probe -- so the
    test suite can drive `needed` by pointing $XYLEM_PARENT at a temp dir. The
    clone destination is derived from the SAME $XYLEM_PARENT as the registered
    script path, so fetch cannot target a different directory than the manifest
    resolves.
    """
    parent = mapping["$XYLEM_PARENT"]
    actions = []
    for server in enabled_servers(manifest):
        if server.get("transport") != "stdio":
            continue
        source = server.get("source")
        if not source or not source.get("repo") or not source.get("dir"):
            continue
        script = server_script_path(server, mapping)
        actions.append({
            "name": server["name"],
            "repo": source["repo"],
            "ref": source.get("ref"),
            "dir": source["dir"],
            "dest": to_fwd(os.path.join(parent, source["dir"])),
            "script": script,
            "needed": bool(script) and not os.path.isfile(script),
        })
    return actions


def run_fetch(actions, apply, runner=_run_git, out=None, warn=None):
    """Clone the servers marked `needed`. Fail-soft: a clone that fails warns
    and lets the run continue (doctor and the launch itself will surface it),
    exactly as the servers behaved before -- only now with a diagnostic.

    Returns the list of human-facing lines emitted, so callers/tests can assert.
    """
    out = out or (lambda m: None)
    warn = warn or (lambda m: None)
    messages = []

    def emit(msg):
        messages.append(msg)
        out(msg)

    needed = [a for a in actions if a["needed"]]
    if not needed:
        return messages
    for action in needed:
        url = clone_url(action["repo"])
        ref = action["ref"]
        ref_note = (" @ %s" % ref) if ref else ""
        if not apply:
            emit("would clone %s%s -> %s" % (url, ref_note, action["dest"]))
            continue
        emit("cloning %s%s -> %s" % (url, ref_note, action["dest"]))
        git_args = ["clone", "--depth", "1"]
        if ref:
            git_args += ["--branch", ref]
        git_args += [url, action["dest"]]
        ok, last = runner(git_args, None)
        if ok:
            emit("cloned %s" % action["name"])
        else:
            warn("could not clone %s (%s): %s -- install git or clone it "
                 "manually into %s, then re-run" % (
                     action["name"], url, last or "unknown error",
                     action["dest"]))
    return messages


def fetch_servers(args, apply, out, warn):
    """Resolve the mapping and clone any missing stdio server repos.

    Shared by the install and update paths. A no-op when --no-fetch is passed.
    """
    if getattr(args, "no_fetch", False):
        return
    claude_dir = detect_claude_dir()
    _, _, project_dir, _ = resolve_targets(args, claude_dir)
    mapping = build_mapping(project_dir)
    actions = plan_fetch(load_manifest(), mapping)
    run_fetch(actions, apply=apply, out=out, warn=warn)


# --------------------------------------------------------------------------
# doctor: verify each registered server can actually start
# --------------------------------------------------------------------------

def _interpreter_has_mcp(python, runner=None):
    """True if `python` can import the `mcp` SDK. Never raises, never hangs."""
    if runner is not None:
        return runner([python, "-c", "import mcp"])
    try:
        proc = subprocess.run(
            [python, "-c", "import mcp"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            universal_newlines=True, timeout=30)
    except Exception:
        return False
    return proc.returncode == 0


def _script_parses(path):
    """True if `path` is syntactically valid Python. Compiles, never executes,
    so it cannot hang on a server's stdio loop."""
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            compile(fh.read(), path, "exec")
        return True
    except (OSError, SyntaxError, ValueError):
        return False


def diagnose(manifest, mapping, python, has_mcp):
    """Per-server health rows. Pure given its inputs: no launch, no network.

    Each row is (name, ok, symbol, detail). stdio servers are healthy when their
    script exists, parses, and the interpreter has `mcp`; http servers report
    whether their URL env var is set (unset is a warning, not a failure -- the
    remotes are optional).
    """
    rows = []
    for server in enabled_servers(manifest):
        name = server["name"]
        transport = server.get("transport")
        if transport == "stdio":
            path = server_script_path(server, mapping)
            if not path or not os.path.isfile(path):
                rows.append((name, False, "FAIL",
                             "server script not found: %s" % path))
            elif not _script_parses(path):
                rows.append((name, False, "FAIL",
                             "server script has a syntax error: %s" % path))
            elif not has_mcp:
                rows.append((name, False, "FAIL",
                             "interpreter %s cannot import 'mcp' (pip install "
                             "mcp)" % python))
            else:
                rows.append((name, True, "OK", path))
        elif transport == "http":
            url_key = server.get("url_env_key")
            if url_key and os.environ.get(url_key):
                rows.append((name, True, "OK",
                             "%s set (optional remote)" % url_key))
            else:
                rows.append((name, True, "WARN",
                             "%s unset -- optional remote not registered"
                             % url_key))
        else:
            rows.append((name, False, "FAIL",
                         "unknown transport '%s'" % transport))
    return rows


def run_doctor(args):
    """`installer.py doctor`: report whether each server can actually start.

    Exit 0 only when every stdio (required) server is healthy; non-zero if any
    is broken, so it is usable as a CI/post-install gate.
    """
    manifest = load_manifest()
    claude_dir = detect_claude_dir()
    _, _, project_dir, _ = resolve_targets(args, claude_dir)
    mapping = build_mapping(project_dir)
    python = resolve_python()
    has_mcp = _interpreter_has_mcp(python)

    rows = diagnose(manifest, mapping, python, has_mcp)
    print("xylem doctor -- interpreter: %s (mcp: %s)\n"
          % (python, "yes" if has_mcp else "NO"))
    broken = 0
    for name, ok, symbol, detail in rows:
        print("  [%-4s] %-22s %s" % (symbol, name, redact(detail)))
        if not ok:
            broken += 1
    print()
    if broken:
        print("xylem: %d server(s) will not start. Run the installer with "
              "--apply to fetch missing servers, or fix the notes above."
              % broken)
        return 1
    print("xylem: all servers healthy.")
    return 0


def run_update(args):
    """`installer.py update`: git pull the xylem repo, then re-apply the block.

    Idempotent: re-runs the install plan, which replaces the content inside the
    fences with the current manifest version stamp. Reports old -> new version
    and which files changed. Never rewrites a block except through this verb.
    """
    claude_dir = detect_claude_dir()
    _, claude_md_path, _, _ = resolve_targets(args, claude_dir)

    # Version currently deployed on this machine, read before we touch anything.
    old_version = parse_fence_version(read_text(claude_md_path))

    ok, last = _run_git(["pull", "--ff-only"], ROOT)
    if ok:
        print("xylem: git pull -- %s" % (last or "ok"))
    else:
        # Fail-soft: proceed with whatever is already checked out locally.
        print("xylem: warning: git pull failed, using local checkout",
              file=sys.stderr)
        if last:
            print("xylem: %s" % last, file=sys.stderr)

    new_version = manifest_version(load_manifest())

    # A machine that installed before this repo carried more servers -- or that
    # only ever cloned Xylem -- gets the missing ones cloned now, so `update`
    # heals a partial checkout instead of leaving dead server entries.
    fetch_servers(
        args, apply=not args.dry_run,
        out=lambda m: print("xylem: %s" % redact(m)),
        warn=lambda m: print("xylem: warning: %s" % redact(m), file=sys.stderr))

    planner = plan(args)
    for msg in planner.warnings:
        print("xylem: warning: %s" % redact(msg), file=sys.stderr)

    if args.dry_run:
        print("xylem: dry run -- no files will be written\n")
        rendered = planner.render()
        print(rendered if rendered else "xylem: nothing to do")
        return 0

    applied = planner.apply()

    old_label = ("v%d" % old_version) if old_version else "none (unstamped/stale)"
    print("xylem: update %s -> v%d" % (old_label, new_version))
    if applied:
        for line in applied:
            print("xylem: %s" % line)
    else:
        print("xylem: already up to date -- no files changed")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Install the Xylem suite into Claude Code. "
                    "Previews by default; pass --apply to write.")
    # Dry-run is the DEFAULT, matching install/xylem_install.py.
    #
    # These two installers used to ship opposite destructive defaults under the
    # same filename -- `./install.sh` wrote immediately while
    # `install/install.sh` only previewed. That is the kind of inconsistency
    # that eventually costs someone their config. Both now preview by default
    # and both need an explicit --apply.
    #
    # --dry-run is kept as an accepted no-op so every previously-documented
    # command line still does exactly what it used to.
    parser.add_argument("--apply", action="store_true",
                        help="actually write the changes (default: preview only)")
    parser.add_argument("--dry-run", action="store_true",
                        help="preview only -- now the default; accepted for compatibility")
    parser.add_argument("--uninstall", action="store_true",
                        help="remove only Xylem-owned entries")
    parser.add_argument("--project", metavar="PATH",
                        help="target the project's CLAUDE.md instead of the global one")
    parser.add_argument("--no-fetch", action="store_true",
                        help="do not clone missing stdio server repos "
                             "(context-keeper, agentsync, cambium)")
    parser.add_argument("command", nargs="?", choices=["update", "doctor"],
                        help="'update': git pull the xylem repo, then re-apply the "
                             "block with the current version stamp. "
                             "'doctor': report whether each server can start.")
    args = parser.parse_args(argv)

    # doctor is read-only: it reports health and never writes, so it ignores the
    # preview/--apply dance entirely and runs the same way every time.
    if args.command == "doctor":
        if args.uninstall:
            parser.error("'doctor' cannot be combined with --uninstall")
        return run_doctor(args)

    # Preview unless --apply. `--dry-run` is now redundant but still honored, so
    # every command line printed in older docs keeps working unchanged.
    args.dry_run = not args.apply
    if not args.apply:
        print("xylem: PREVIEW -- nothing will be written. Re-run with --apply to "
              "make these changes.\n")

    if args.command == "update":
        if args.uninstall:
            parser.error("'update' cannot be combined with --uninstall")
        try:
            return run_update(args)
        except FileNotFoundError as exc:
            print("xylem: %s" % exc, file=sys.stderr)
            return 1

    # Clone any missing stdio server repos before registering them, so a clean
    # machine ends up with servers that actually start. Skipped on uninstall
    # (nothing to fetch) and under --no-fetch. Previews as "would clone".
    if not args.uninstall:
        fetch_servers(
            args, apply=args.apply,
            out=lambda m: print("xylem: %s" % redact(m)),
            warn=lambda m: print("xylem: warning: %s" % redact(m),
                                 file=sys.stderr))

    try:
        planner = plan(args)
    except FileNotFoundError as exc:
        print("xylem: %s" % exc, file=sys.stderr)
        return 1

    for msg in planner.warnings:
        print("xylem: warning: %s" % redact(msg), file=sys.stderr)

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

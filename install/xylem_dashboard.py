#!/usr/bin/env python3
"""
Xylem dashboard generator.

Stdlib Python 3 only — zero dependencies, cross-platform. Reads your org's
coordination + memory activity and bakes a single self-contained
dashboard.html from docs/dashboard.template.html.

TWO ROUTES (pick with --remote; local is the default):

  LOCAL  (no token, default): reads your machine directly —
           * agentsync board  <- the coordination branch of a local clone (git)
           * context-keeper    <- the .context/ JSON in each project clone
           * cambium funnel    <- .cambium/knowledge.json (optional)
         Sees every project cloned on this machine. No secrets, no network.

  REMOTE (--remote): reads your Cloudflare Workers over HTTPS using the
         connector URLs already in your local (gitignored) xylem.config.json —
           * context-keeper-remote  <- list_projects + get_project_summary
           * agentsync-remote       <- survey (+ history if available)
         Sees the full central mirror (incl. work from other machines/mobile).
         The token stays in local config; only counts/summaries land in the HTML.

WHAT LANDS IN THE OUTPUT — read this before publishing the HTML anywhere public.
No token or credential is ever written. But free text *is*: claim titles,
completion notes (up to 600 chars) and decision summaries are copied into the
page close to verbatim. The only transformation applied is scrub_text(), which
redacts home-directory paths (C:\\Users\\<name>, /home/<name>, /Users/<name> ->
"<user>"). Anything else you typed into a note — internal hostnames, customer
names, unreleased project names — will appear as written. If you are publishing
this dashboard publicly, pass --no-notes to drop note/description bodies and keep
only titles, names and counts.

Every collector fails soft: a source that's missing or unreachable degrades that
panel to empty with a warning; the dashboard still renders.

Usage:
    python3 xylem_dashboard.py                       # local, writes ./dashboard.html
    python3 xylem_dashboard.py --output ~/xylem.html
    python3 xylem_dashboard.py --projects /a /b /c   # extra context-keeper clones
    python3 xylem_dashboard.py --remote              # use the Workers instead
    python3 xylem_dashboard.py --no-notes            # omit note bodies (public publishing)
    python3 xylem_dashboard.py --dry-run             # print a data summary, write nothing
"""

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# Nothing is excluded by default — this is your org's data, not ours. Set the
# XYLEM_DASHBOARD_EXCLUDE config key / env var (a list, or a comma-separated
# string) to substring-match throwaway or test projects you don't want on the
# board, e.g. "smoketest,e2e-live,scratch". Matching is case-insensitive.
_DEFAULT_EXCLUDE = ()

HERE = Path(__file__).resolve().parent
DEFAULT_TEMPLATE = HERE.parent / "docs" / "dashboard.template.html"
PLACEHOLDER = "__XYLEM_DATA__"

# The four stack components are constant for everyone; pills fill from your data.
STACK_REPOS = {"xylem", "cambium", "context-keeper", "agentsync"}


def warn(msg):
    print("  ! " + msg, file=sys.stderr)


def info(msg):
    print("  - " + msg)


# --------------------------------------------------------------------------- #
# text hygiene — every free-text field written to the output goes through
# clean_text() before it is serialised into the HTML.
# --------------------------------------------------------------------------- #
# Home directories leak the machine account name (and often the real person's
# name) into a page that is frequently published to GitHub Pages. Redact the
# user component, keep the shape of the path so the note still reads sensibly.
# Windows paths arrive with either one backslash or two (JSON-escaped notes).
_HOME_PATTERNS = (
    # C:\Users\alice  /  C:\\Users\\alice
    (re.compile(r"([A-Za-z]:\\{1,2}Users\\{1,2})([^\\/\s\"';:,)\]}]+)"), r"\1<user>"),
    # /home/alice
    (re.compile(r"(/home/)([^/\s\"';:,)\]}]+)"), r"\1<user>"),
    # /Users/alice
    (re.compile(r"(/Users/)([^/\s\"';:,)\]}]+)"), r"\1<user>"),
)

# Markers of UTF-8 bytes that were decoded as cp1252 somewhere upstream
# ("→" -> "â†'", "'" -> "â€™").
_MOJI_MARKERS = ("Ã", "â", "Â", "Ë", "Ð", "Ñ")


def _moji_score(s):
    return sum(s.count(m) for m in _MOJI_MARKERS)


def _demojibake(s):
    """Undo one round of UTF-8-decoded-as-cp1252, but only if it actually helps."""
    if not s or _moji_score(s) == 0:
        return s
    try:
        fixed = s.encode("cp1252").decode("utf-8")
    except Exception:
        return s
    return fixed if _moji_score(fixed) < _moji_score(s) else s


def scrub_text(s):
    """Redact home-directory paths from any free text bound for the output."""
    if not s:
        return s
    out = str(s)
    for pat, repl in _HOME_PATTERNS:
        out = pat.sub(repl, out)
    return out


def clean_text(s, limit=None):
    """The single funnel for free text: de-mojibake, scrub, then truncate."""
    out = scrub_text(_demojibake(str(s or "")))
    return out[:limit] if limit else out


# --------------------------------------------------------------------------- #
# config (reuse the installer's file/env convention)
# --------------------------------------------------------------------------- #
def _config_home():
    if os.name == "nt":
        base = os.environ.get("APPDATA")
        return Path(base) if base else Path.home() / "AppData" / "Roaming"
    return Path.home() / ".config"


def load_config(explicit):
    candidates = []
    if explicit:
        candidates.append(Path(explicit))
    if os.environ.get("XYLEM_CONFIG"):
        candidates.append(Path(os.environ["XYLEM_CONFIG"]))
    candidates.append(HERE / "xylem.config.json")
    candidates.append(_config_home() / "xylem" / "config.json")
    for c in candidates:
        try:
            if c.exists() and c.stat().st_size:
                data = json.loads(c.read_text(encoding="utf-8"))
                info("using config: %s" % c)
                return {k: v for k, v in data.items() if not k.startswith("_")}
        except Exception as exc:
            warn("ignoring config %s (%s)" % (c, exc))
    return {}


def cfg_get(cfg, key, default=None):
    v = os.environ.get(key)
    if v not in (None, ""):
        return v
    v = cfg.get(key)
    return v if v not in (None, "") else default


# --------------------------------------------------------------------------- #
# shared helpers
# --------------------------------------------------------------------------- #
def mmdd_from_epoch(sec):
    return datetime.fromtimestamp(int(sec), timezone.utc).strftime("%m-%d")


def mmdd_from_iso(iso):
    if not iso:
        return ""
    try:
        # tolerate trailing Z and fractional seconds
        s = iso.replace("Z", "+00:00")
        return datetime.fromisoformat(s).strftime("%m-%d")
    except Exception:
        return str(iso)[5:10]  # 'YYYY-MM-DD...' -> 'MM-DD'


def ymd_from_iso(iso):
    """Full 'YYYY-MM-DD' for the contributor detail view (falls back gracefully)."""
    if not iso:
        return ""
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).strftime("%Y-%m-%d")
    except Exception:
        return str(iso)[:10]


def ymd_from_epoch(ct):
    try:
        return datetime.fromtimestamp(int(ct), tz=timezone.utc).strftime("%Y-%m-%d")
    except Exception:
        return ""


def attribute_repo(text, branch, known, note=""):
    """Best-effort: which repo a claim is about, matched from its task, branch, or
    completion note. Falls back to 'unassigned' when nothing names a known repo —
    a plain catch-all that also flags work that wasn't tagged to any repo (never a
    made-up repo name).

    Sources are checked in priority order — task text, then branch, then the
    completion note — and the first hit wins. Concatenating them into one
    haystack let a long note that happens to mention another repo outvote the
    claim's own title, which mislabelled real events."""
    for hay in (text or "", branch or "", note or ""):
        # longest known name first so 'context-keeper-remote' wins over 'context-keeper'
        for name in sorted(known, key=len, reverse=True):
            if name.lower() in hay.lower():
                return name
    return "unassigned"


def active_count(entries):
    """Count entries whose status is active (absent status == active)."""
    n = 0
    for e in entries:
        if isinstance(e, dict) and e.get("status", "active") == "active":
            n += 1
    return n


def read_json_list(path):
    try:
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
    except Exception:
        pass
    return []


# --------------------------------------------------------------------------- #
# LOCAL collectors (no token)
# --------------------------------------------------------------------------- #
def git(repo, *args, timeout=30):
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, timeout=timeout,
    )


def resolve_ref(repo, branch):
    for cand in ("origin/%s" % branch, branch, "refs/remotes/origin/%s" % branch):
        r = git(repo, "rev-parse", "--verify", "--quiet", cand)
        if r.returncode == 0:
            return cand
    return None


def read_board_local(repo, branch, known, no_notes=False):
    """Reconstruct the coordination event list from claims.json git history."""
    if not repo or not Path(repo).exists():
        warn("agentsync: no local clone (set AGENTSYNC_REPO) — skipping coordination")
        return [], None
    ref = resolve_ref(repo, branch)
    if not ref:
        warn("agentsync: branch '%s' not found in %s — skipping coordination" % (branch, repo))
        return [], None
    log = git(repo, "log", "--reverse", "--format=%H %ct", ref, "--", "claims.json")
    if log.returncode != 0:
        warn("agentsync: no claims.json history on %s — skipping" % ref)
        return [], None
    # (agent, task) -> aggregated event
    agg = {}
    for line in log.stdout.splitlines():
        parts = line.split()
        if len(parts) != 2:
            continue
        sha, ct = parts
        blob = git(repo, "show", "%s:claims.json" % sha)
        if blob.returncode != 0:
            continue
        try:
            doc = json.loads(blob.stdout)
        except Exception:
            continue
        claims = doc.get("claims", doc if isinstance(doc, dict) else {})
        for agent, c in claims.items():
            if not isinstance(c, dict) or not c.get("task"):
                continue
            key = (agent, c.get("task"))
            rec = agg.setdefault(key, {"first": ct, "last": ct, "status": "", "note": "", "branch": ""})
            rec["last"] = ct
            rec["status"] = c.get("status", "") or rec["status"]
            rec["note"] = c.get("note") or rec["note"]
            rec["branch"] = c.get("branch") or rec["branch"]
    events = []
    for (agent, task), rec in agg.items():
        status = "done" if rec["status"] in ("done", "released") else "live"
        note = "" if no_notes else clean_text(rec["note"])
        events.append({
            "t": clean_text(task),
            "repo": attribute_repo(task, rec["branch"], known, rec["note"]),
            "who": agent,
            "when": mmdd_from_epoch(rec["last"]),
            "on": ymd_from_epoch(rec["last"]),
            "status": status,
            "desc": note[:150],
            "note": note[:600],
            "_sort": int(rec["last"]),
        })
    events.sort(key=lambda e: e["_sort"], reverse=True)
    for e in events:
        e.pop("_sort", None)
    return events, None


def read_context_local(project_paths, exclude=()):
    """Read every project clone's .context/ store into (stores, recent decisions)."""
    stores, decisions = [], []
    for path in project_paths:
        base = Path(path)
        ctx = base / ".context"
        if not ctx.is_dir() or excluded_project(base.name, exclude):
            continue
        decs = read_json_list(ctx / "decisions.json")
        cons = read_json_list(ctx / "constraints.json")
        name = base.name
        stores.append({"p": name, "dec": active_count(decs), "con": active_count(cons), "cls": name})
        active = [d for d in decs if isinstance(d, dict) and d.get("status", "active") == "active"]
        active.sort(key=lambda d: d.get("updated_at") or d.get("created_at") or "", reverse=True)
        for d in active[:2]:
            decisions.append({"id": "%s/%s" % (name, d.get("id", "?")),
                              "repo": name, "t": clean_text(d.get("summary"), 160)})
    stores.sort(key=lambda s: s["dec"], reverse=True)
    return stores, decisions


def read_cambium_local(repo):
    """Optional knowledge funnel from .cambium/knowledge.json (by scope)."""
    if not repo:
        return None
    kf = Path(repo) / ".cambium" / "knowledge.json"
    items = read_json_list(kf)
    if not items:
        return None
    by = {"local": 0, "team": 0, "org": 0}
    for it in items:
        if isinstance(it, dict):
            by[it.get("scope", "local")] = by.get(it.get("scope", "local"), 0) + 1
    total = sum(by.values())
    if not total:
        return None
    return {"distilled": total, "teamed": by.get("team", 0) + by.get("org", 0), "org": by.get("org", 0)}


def discover_projects(cfg, extra):
    paths, seen = [], set()

    def add(p):
        if not p:
            return
        rp = str(Path(p).expanduser())
        if rp not in seen and (Path(rp) / ".context").is_dir():
            seen.add(rp)
            paths.append(rp)

    for p in extra or []:
        add(p)
    listed = cfg_get(cfg, "XYLEM_DASHBOARD_PROJECTS")
    if listed:
        for p in (listed if isinstance(listed, list) else str(listed).split(os.pathsep)):
            add(p)
    for key in ("CONTEXT_KEEPER_PROJECT", "CAMBIUM_REPO", "AGENTSYNC_REPO"):
        add(cfg_get(cfg, key))
    root = cfg_get(cfg, "XYLEM_PROJECTS_ROOT")
    if root and Path(root).is_dir():
        for child in sorted(Path(root).iterdir()):
            add(str(child))
    return paths


# --------------------------------------------------------------------------- #
# REMOTE collectors (token, from local config)
# --------------------------------------------------------------------------- #
# A normal browser-like User-Agent. Cloudflare's edge (bot protections) blocks
# urllib's default "Python-urllib/x.y" UA with a 403 before the request ever
# reaches the Worker — which returns only 404/405, never 403. Sending a real UA
# gets past that. (The token still authenticates at the Worker as usual.)
_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) xylem-dashboard/1.0 Safari/537.36"


def rpc_call_tool(url, name, arguments):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                       "params": {"name": name, "arguments": arguments}}).encode("utf-8")
    req = urllib.request.Request(url, data=body,
                                 headers={"content-type": "application/json",
                                          "accept": "application/json",
                                          "user-agent": _UA})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if data.get("error"):
        raise RuntimeError(data["error"].get("message", "rpc error"))
    result = data.get("result", {})
    if result.get("isError"):
        raise RuntimeError((result.get("content") or [{}])[0].get("text", "tool error"))
    # Prefer structuredContent, but fall back to the JSON in content[].text —
    # not every MCP server (e.g. agentsync-remote) emits structuredContent, and
    # without this fallback its survey/history come back empty (silently).
    sc = result.get("structuredContent")
    if isinstance(sc, dict) and sc:
        return sc
    for item in (result.get("content") or []):
        if isinstance(item, dict) and item.get("type") == "text" and item.get("text"):
            try:
                return json.loads(item["text"])
            except Exception:
                pass
    return sc or {}


def excluded_project(name, exclude):
    n = str(name).lower()
    return any(p in n for p in exclude)


def read_context_remote(url, exclude=()):
    reg = rpc_call_tool(url, "list_projects", {})
    stores, decisions = [], []
    for p in reg.get("projects", []):
        name = p.get("project", "?")
        if excluded_project(name, exclude):
            continue
        stores.append({"p": name, "dec": p.get("decisions", 0),
                       "con": p.get("constraints", 0), "cls": name})
        try:
            s = rpc_call_tool(url, "get_project_summary", {"project": name})
            for d in (s.get("recent_decisions") or [])[:2]:
                decisions.append({"id": "%s/%s" % (name, d.get("id", "?")),
                                  "repo": name, "t": clean_text(d.get("summary"), 160)})
        except Exception as exc:
            warn("context-keeper-remote: summary for %s failed (%s)" % (name, exc))
    stores.sort(key=lambda s: s["dec"], reverse=True)
    return stores, decisions


# agentsync history commit messages look like:
#   "agentsync: <agent> claims '<task>'"
#   "agentsync: <agent> releases '<task>'"
#   "agentsync: <agent> releases '<task>' (freeform completion note...)"
# The task itself may contain apostrophes and parentheses, and a release often
# appends a " (note)" after the closing quote, so we match the task lazily up to
# a closing quote that is followed by either end-of-line or " (" — never anchor
# the quote to end-of-string, or every release-with-a-note is silently dropped.
_HIST_RE = re.compile(r"^agentsync:\s+(\S+)\s+(claims|releases)\s+'(.*?)'(?:\s+\(|\s*$)")
# A release often appends a " (freeform completion note...)" after the quoted
# task. This second, stricter pattern pulls that note out for the per-contributor
# detail view. It is best-effort only — if it doesn't match, task parsing above is
# unaffected and the note is simply absent.
_HIST_NOTE_RE = re.compile(r"^agentsync:\s+\S+\s+(?:claims|releases)\s+'.*?'\s+\((.*)\)\s*$")


def read_board_remote(url, known, no_notes=False):
    # Current claims from survey (rich: status/note/branch per active claim).
    current = {}
    try:
        surv = rpc_call_tool(url, "survey", {})
        for agent, c in (surv.get("claims") or {}).items():
            if isinstance(c, dict) and c.get("task"):
                current[(agent, c["task"])] = c
    except Exception as exc:
        warn("agentsync-remote survey failed (%s)" % exc)

    # Full timeline from the history commit log ({commits:[{message,date,...}]}).
    agg = {}
    try:
        hist = rpc_call_tool(url, "history", {"limit": 100})
        for cm in hist.get("commits", []):
            m = _HIST_RE.match(cm.get("message", ""))
            if not m:
                continue
            agent, action, task = m.group(1), m.group(2), m.group(3)
            date = cm.get("date", "") or ""
            rec = agg.setdefault((agent, task),
                                 {"first": date, "last": date, "released": False,
                                  "note": "", "note_date": ""})
            if date > rec["last"]:
                rec["last"] = date
            if date and date < rec["first"]:
                rec["first"] = date
            if action == "releases":
                rec["released"] = True
                # keep the newest release's completion note for the detail view
                nm = _HIST_NOTE_RE.match(cm.get("message", ""))
                if nm and date >= rec["note_date"]:
                    rec["note"], rec["note_date"] = nm.group(1).strip(), date
    except Exception as exc:
        warn("agentsync-remote history failed (%s)" % exc)

    # Always fold in survey's current claims. An active (often in-progress)
    # claim may not have a matching commit in the fetched history window, and
    # that live work is exactly what the coordination feed should surface — so
    # add any claim the history didn't already cover (and if history gave
    # nothing at all, this is what populates the board).
    for (agent, task), c in current.items():
        if (agent, task) not in agg:
            ts = c.get("updated_at", "")
            agg[(agent, task)] = {"first": ts, "last": ts,
                                  "released": c.get("status") in ("done", "released"),
                                  "note": "", "note_date": ""}

    events = []
    for (agent, task), rec in agg.items():
        cur = current.get((agent, task))
        live = cur is not None and cur.get("status") not in ("done", "released")
        # detail note: the live claim's own note, else the newest release note
        raw_note = (cur or {}).get("note") or rec.get("note") or ""
        note = "" if no_notes else clean_text(raw_note, 600)
        # `cur` is None for every completed claim, so the live claim's note alone
        # left 43 of 44 rows with a blank description — fall back to the release
        # note that the history parser already recovered.
        desc = "" if no_notes else clean_text((cur or {}).get("note") or raw_note, 150)
        events.append({
            "t": clean_text(task),
            "repo": attribute_repo(task, (cur or {}).get("branch"), known, raw_note),
            "who": agent,
            "when": mmdd_from_iso(rec["last"]),
            "on": ymd_from_iso(rec["last"]),
            "status": "live" if live else "done",
            "desc": desc,
            "note": note,
            "_sort": rec["last"] or "",
        })
    events.sort(key=lambda e: e["_sort"], reverse=True)
    for e in events:
        e.pop("_sort", None)
    return events, None


# --------------------------------------------------------------------------- #
# assemble + render
# --------------------------------------------------------------------------- #
COMPONENT_DEFS = [
    ("xylem", "xylem", "habit layer",
     "SessionStart/SessionEnd hooks, installer, replay player and version nudges. Makes the memory discipline automatic in every Claude session."),
    ("cambium", "cambium", "knowledge distillation",
     "Distills session outcomes into knowledge and promotes it team→org through endorsement + generalization gates."),
    ("ck", "context-keeper", "decision memory",
     "Records decisions, constraints and pipelines per project, with a Cloudflare Worker remote mirror. The canonical store."),
    ("agentsync", "agentsync", "coordination mesh",
     "Claim/release board with overlap CAS and a human-in-the-loop mailbox. Makes the phone a first-class peer."),
]


def build_components(stores, events, peers):
    by = {s["p"]: s for s in stores}
    comps = []
    for key, nm, role, desc in COMPONENT_DEFS:
        if nm == "agentsync":
            pills = [["events", len(events)], ["peers", len(peers)]]
        else:
            s = by.get(nm)
            if s:
                pills = [["decisions", s["dec"]]] + ([["constraints", s["con"]]] if s["con"] else [["repo", nm]])
            else:
                pills = [["repo", nm]]
        comps.append({"key": key, "nm": nm, "role": role, "desc": desc, "pills": pills})
    return comps


def assemble(route, stores, decisions, events, funnel, coordination_repo):
    peers = []
    for e in events:
        if e.get("who") and e["who"] not in peers:
            peers.append(e["who"])
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    n_proj = len(stores)
    sub = ("%d project%s, %d peer%s, one shared brain. Every claim, release, and recorded "
           "decision flowing through the memory + coordination mesh." %
           (n_proj, "" if n_proj == 1 else "s", len(peers), "" if len(peers) == 1 else "s"))
    meta = {
        "generated_at": generated_at,
        "headline_sub": sub,
        "source": "source: %s" % ("local git board + .context/ stores" if route == "local"
                                   else "context-keeper-remote + agentsync-remote"),
        "coordination_repo": clean_text(coordination_repo) or ("(local board)" if route == "local" else "(remote)"),
        "mesh_events": len(events),
    }
    if funnel:
        funnel = dict(funnel)
        funnel.setdefault("d1", "session outcomes distilled into candidate knowledge")
        funnel.setdefault("d2", "promoted to team scope")
        funnel.setdefault("d3", "endorsed to org scope")
        meta["funnel"] = funnel
    return {
        "meta": meta,
        "components": build_components(stores, events, peers),
        "events": events,
        "stores": stores,
        "decisions": decisions,
    }


def render(template_path, data, out_path):
    tpl_path = Path(template_path)
    if not tpl_path.is_file():
        raise RuntimeError(
            "template not found: %s\n"
            "The default template lives next to this script at ../docs/dashboard.template.html, "
            "so copying xylem_dashboard.py on its own leaves it with nothing to render.\n"
            "Fix: pass --template /path/to/dashboard.template.html, or copy the docs/ template "
            "from the xylem repo (https://github.com/jarmstrong158/Xylem) alongside the script."
            % tpl_path
        )
    tpl = tpl_path.read_text(encoding="utf-8")
    if PLACEHOLDER not in tpl:
        raise RuntimeError("template %s is missing the %s placeholder" % (template_path, PLACEHOLDER))
    html = tpl.replace(PLACEHOLDER, json.dumps(data, ensure_ascii=False))
    Path(out_path).write_text(html, encoding="utf-8")


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def main(argv=None):
    ap = argparse.ArgumentParser(description="Generate a self-contained Xylem observability dashboard.")
    ap.add_argument("--remote", action="store_true",
                    help="read the Cloudflare Workers instead of local files (uses connector URLs from config)")
    ap.add_argument("--output", default="dashboard.html", help="output HTML path (default ./dashboard.html)")
    ap.add_argument("--template", default=str(DEFAULT_TEMPLATE), help="path to dashboard.template.html")
    ap.add_argument("--config", help="path to xylem.config.json")
    ap.add_argument("--projects", nargs="*", help="extra context-keeper project clone paths (local route)")
    ap.add_argument("--coordination-repo",
                    help="label for the coordination board shown in the footer "
                         "(default: the local agentsync clone's name, or '(remote)')")
    ap.add_argument("--no-notes", action="store_true",
                    help="omit claim note/description bodies from the output — keep only "
                         "titles, names and counts (recommended when publishing publicly)")
    ap.add_argument("--dry-run", action="store_true", help="print a data summary, write nothing")
    args = ap.parse_args(argv)

    cfg = load_config(args.config)
    route = "remote" if args.remote else "local"
    print("Xylem dashboard — %s route" % route.upper())

    known_repos = set(STACK_REPOS)
    # Footer label for the board. Explicit flag wins; otherwise the local route
    # names the agentsync clone it actually read and the remote route stays
    # generic (assemble() supplies the fallback).
    coord_repo = args.coordination_repo or cfg_get(cfg, "XYLEM_COORDINATION_REPO")

    exclude = list(_DEFAULT_EXCLUDE)
    extra_excl = cfg_get(cfg, "XYLEM_DASHBOARD_EXCLUDE")
    if extra_excl:
        parts = extra_excl if isinstance(extra_excl, list) else str(extra_excl).split(",")
        exclude += [x.strip().lower() for x in parts if x.strip()]
    exclude = tuple(exclude)

    if route == "local":
        projects = discover_projects(cfg, args.projects)
        known_repos |= {Path(p).name for p in projects}
        info("context-keeper: %d project store(s)" % len(projects))
        stores, decisions = read_context_local(projects, exclude)
        repo = cfg_get(cfg, "AGENTSYNC_REPO")
        branch = cfg_get(cfg, "AGENTSYNC_BRANCH", "agentsync")
        if not coord_repo and repo:
            coord_repo = "%s (%s)" % (Path(repo).name, branch)
        events, _ = read_board_local(repo, branch, known_repos, args.no_notes)
        funnel = read_cambium_local(cfg_get(cfg, "CAMBIUM_REPO"))
    else:
        ck_url = cfg_get(cfg, "CONTEXT_KEEPER_REMOTE_URL")
        as_url = cfg_get(cfg, "AGENTSYNC_REMOTE_URL")
        if not ck_url and not as_url:
            print("No remote URLs in config (CONTEXT_KEEPER_REMOTE_URL / AGENTSYNC_REMOTE_URL). "
                  "Fill them in xylem.config.json or drop --remote to use local files.", file=sys.stderr)
            return 1
        stores, decisions = ([], [])
        if ck_url:
            try:
                stores, decisions = read_context_remote(ck_url, exclude)
            except Exception as exc:
                warn("context-keeper-remote unreachable (%s)" % exc)
        known_repos |= {s["p"] for s in stores}
        events = []
        if as_url:
            try:
                events, _ = read_board_remote(as_url, known_repos, args.no_notes)
            except Exception as exc:
                warn("agentsync-remote unreachable (%s)" % exc)
        funnel = None

    data = assemble(route, stores, decisions, events, funnel, coord_repo)

    totals = "%d project stores · %d decisions · %d constraints · %d coordination events · %d peers" % (
        len(data["stores"]),
        sum(s["dec"] for s in data["stores"]),
        sum(s["con"] for s in data["stores"]),
        len(data["events"]),
        len({e["who"] for e in data["events"] if e.get("who")}),
    )
    print("Collected: " + totals)

    # Applies to both routes: an empty collection means something is misconfigured
    # (Workers unreachable, or no .context/ clones and no agentsync board found),
    # and overwriting a good dashboard with an empty one is worse than failing.
    if not data["stores"] and not data["events"]:
        print("Refusing to write: the %s route collected no projects and no "
              "coordination events (%s). Keeping any existing output rather than "
              "overwriting it with an empty dashboard." % (
                  route,
                  "Workers unreachable or misconfigured" if route == "remote"
                  else "no .context/ project clones and no agentsync board found — "
                       "check AGENTSYNC_REPO / XYLEM_PROJECTS_ROOT, or pass --projects"),
              file=sys.stderr)
        return 2

    if args.dry_run:
        print("\nDry-run — no file written. Data summary above; rerun without --dry-run to write %s." % args.output)
        return 0

    try:
        render(args.template, data, args.output)
    except Exception as exc:
        print("Failed to render: %s" % exc, file=sys.stderr)
        return 1
    print("Wrote %s (self-contained; no credentials%s)." % (
        args.output, "; note bodies omitted" if args.no_notes
        else "; note/summary text included verbatim apart from home-path scrubbing — "
             "use --no-notes before publishing publicly"))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        os._exit(130)

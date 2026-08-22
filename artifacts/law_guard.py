#!/usr/bin/env python3
"""PreToolUse hook: put the relevant org law in front of the model AT THE EDIT.

Why this exists. Session-start injection is necessary and not sufficient. In one
long session the primer showed three laws -- atomic writes, measure the gate,
be loud when you skip -- and the same session then shipped a blind overwrite
that destroyed 24 records, thirteen deploys that never reached the client, and a
save path that discarded data silently. The laws were AVAILABLE at hour zero and
absent at hour six, which is when the code got written.

context-keeper already solved this shape for constraints: scope_guard fires on
PreToolUse so a scoped rule arrives immediately before a covered file is
written. Cambium's laws -- the most general knowledge in the system -- had no
equivalent. This is it.

Selection is by ACTION, not by similarity to prose: the file being written is
matched against a small table of action classes (persistence, deploy/cache,
detectors, spend, hooks), and only a law tied to that class is surfaced. A law
that fires on every edit is noise, and noise is how a guardrail gets ignored.

HOT PATH. This runs before every Edit/Write in every project, so it imports json
and os and nothing else -- con-010-acde. It reads one file, never the cambium
server, and fails silent: a memory layer must never block an edit.
"""
import json
import os
import sys

ORG = os.environ.get("CAMBIUM_ORG_REPO", r"C:\Users\jarms\repos\knowledge")
STORE = os.path.join(ORG, "knowledge.json")
MAX_LAWS = 2

# path/content signals -> words that must appear in a law for it to be relevant.
# Deliberately narrow. Each was chosen because a real failure went the other way.
CLASSES = (
    ("persistence", ("save", "store", "decisions", "write", "persist", "db",
                     "cache", ".json"),
     ("atomic", "data-loss", "corrupt", "overwrite")),
    ("delivery", ("sw.js", "service-worker", "deploy", "publish", "worker",
                  "manifest", "index.html"),
     ("gate", "open-rate", "deploy", "verified")),
    ("detector", ("check", "verify", "detect", "audit", "quality", "lint",
                  "monitor", "guard"),
     ("signal that fires", "fires on", "cannot tell", "metric", "instrument")),
    ("spend", ("agent", "model", "claude", "api", "runner", "eval"),
     ("money", "metered", "billed")),
    ("skip", ("except", "try", "fallback", "default", "skip"),
     ("proceeding", "loud when it skips", "silently")),
)


def _laws():
    try:
        with open(STORE, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return []
    items = data.get("items") if isinstance(data, dict) else data
    return [i for i in (items or []) if isinstance(i, dict)
            and i.get("status", "active") == "active"]


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0
    tool = payload.get("tool_name") or ""
    if tool not in ("Edit", "Write", "NotebookEdit"):
        return 0
    ti = payload.get("tool_input") or {}
    target = str(ti.get("file_path") or "").replace("\\", "/").lower()
    if not target:
        return 0
    body = (str(ti.get("new_string") or "") + str(ti.get("content") or "")).lower()[:4000]
    hay = target + " " + body

    wanted = set()
    for _name, signals, keys in CLASSES:
        if any(s in hay for s in signals):
            wanted.update(keys)
    if not wanted:
        return 0

    hits = []
    for law in _laws():
        text = str(law.get("content") or "")
        low = text.lower()
        if any(k in low for k in wanted):
            hits.append(text)
        if len(hits) >= MAX_LAWS:
            break
    if not hits:
        return 0

    lines = ["[xylem law] Relevant to what you are about to write:"]
    lines += ["  - " + h.strip().replace("\n", " ")[:300] for h in hits]
    out = {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                  "additionalContext": "\n".join(lines)}}
    sys.stdout.write(json.dumps(out))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)

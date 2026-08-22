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
FIRES = os.path.join(os.path.expanduser("~"), ".xylem", "law-fires.jsonl")
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
                  "monitor", "guard", "drift", "flag", "grep", "scan",
                  "stale", "match"),
     ("signal that fires", "fires on", "cannot tell", "metric", "instrument")),
    ("spend", ("agent", "model", "claude", "api", "runner", "eval"),
     ("money", "metered", "billed")),
    ("skip", ("except", "try", "fallback", "default", "skip"),
     ("proceeding", "loud when it skips", "silently")),
    # Deciding that work is FINISHED is its own action class. The sweep that
    # would have closed every synthesis request on its first pass was written
    # under this hook and got nothing: it saves no file, checks no metric, and
    # spends no money -- it just quietly ruled that something was done.
    ("lifecycle", ("done", "finish", "complete", "resolve", "close", "sweep",
                   "prune", "retire", "dismiss", "archive"),
     ("proceeding", "loud when it skips", "silently", "skipped step",
      "cannot tell", "empty success")),
)


def _has_word(hay, needle):
    """Substring matching put 'guard' inside 'vanguard' and served a metrics law
    to a sprite generator. A signal that fires on the project's NAME is the
    same bug the laws warn about, so signals match on word boundaries."""
    n, start = len(needle), 0
    while True:
        i = hay.find(needle, start)
        if i < 0:
            return False
        before = hay[i - 1] if i else " "
        after = hay[i + n] if i + n < len(hay) else " "
        # Only letters and digits continue a word. In code the separators ARE
        # underscores, dots and slashes -- treating "_" as a word character
        # meant save_decisions did not match "decisions", and that function is
        # the one that destroyed 24 records.
        if not before.isalnum() and not after.isalnum():
            return True
        start = i + 1


def _laws():
    try:
        with open(STORE, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return []
    items = data.get("items") if isinstance(data, dict) else data
    return [i for i in (items or []) if isinstance(i, dict)
            and i.get("status", "active") == "active"]


WRITE_SIGNS = (">", ">>", "sed -i", "tee ", "truncate", "mv ", "cp ",
               "open(", "write_text", "dump(", "writelines", "os.replace",
               "shutil.", "rm ", "del ")


def _scan_text(cmd):
    """The part of a shell command that is CODE, with prose removed.

    This hook fired on its own commit: the message said it had been missing
    "sed -i" and was signed "Co-Authored-By: Claude", so the scan found those
    strings in English and surfaced two irrelevant laws. But a heredoc is not
    always prose -- one feeding python IS the write. So the rule is by
    DESTINATION: a heredoc handed to an interpreter is code and is kept, and
    everything else quoted is prose and is dropped.
    """
    keep, i, n = [], 0, len(cmd)
    INTERP = ("python", "python3", "py ", "sh ", "bash", "node", "perl", "ruby")
    while i < n:
        c = cmd[i]
        if cmd.startswith("<<", i):
            j = i + 2
            while j < n and cmd[j] in "-~":
                j += 1
            q = cmd[j] if j < n and cmd[j] in "'\"" else ""
            j += 1 if q else 0
            k = j
            while k < n and (cmd[k].isalnum() or cmd[k] == "_"):
                k += 1
            tag = cmd[k - (k - j):k]
            k += 1 if (q and k < n and cmd[k] == q) else 0
            end_ = cmd.find(chr(10) + tag, k) if tag else -1
            body = cmd[k:end_] if end_ >= 0 else cmd[k:]
            # Kept only if an interpreter is reading it.
            before = cmd[:i].lower()
            if any(x in before for x in INTERP):
                keep.append(body)
            i = (end_ + 1 + len(tag)) if end_ >= 0 else n
            continue
        if c in "'\"":
            close = cmd.find(c, i + 1)
            i = (close + 1) if close > 0 else n      # quoted text is prose
            keep.append(" ")
            continue
        keep.append(c)
        i += 1
    return "".join(keep)


def _writes_a_file(cmd):
    """Only a Bash command that could MODIFY something is worth a law.

    Every shell call would otherwise pay for this hook, and a guard that fires
    on `git status` is noise -- which is how a guardrail gets ignored, and then
    it protects nothing.
    """
    low = _scan_text(cmd).lower()
    if any(op in low for op in (">", ">>")):
        return True
    words = low.replace("&&", " ").replace("|", " ").replace(";", " ").split()
    if any(w in ("tee", "mv", "cp", "rm", "truncate", "install") for w in words):
        return True
    if "sed" in words and "-i" in words:
        return True
    return any(sign in low for sign in
               ("open(", "write_text", "writelines", "os.replace", "json.dump",
                "shutil.", "'w'", '"w"'))


def _log(target, laws, signals):
    """Append one line per fire, so "does this help" becomes a number.

    Nothing else records that a law was ever put in front of anyone. The only
    other evidence available is recall counts, and those measure retrieval, not
    influence -- by that measure a law read at every session start scores zero.
    Appended, never rewritten: a ledger a process can rewrite is a ledger that
    can lose the inconvenient half.

    Fails silent like the rest of the hook. A memory layer must never be the
    reason an edit did not happen.
    """
    try:
        from datetime import datetime, timezone
        os.makedirs(os.path.dirname(FIRES), exist_ok=True)
        rec = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "file": target, "laws": laws, "signals": signals}
        with open(FIRES, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + chr(10))
    except Exception:
        pass


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0
    tool = payload.get("tool_name") or ""
    ti = payload.get("tool_input") or {}
    if tool in ("Edit", "Write", "NotebookEdit"):
        target = str(ti.get("file_path") or "").replace("\\", "/").lower()
        body = (str(ti.get("new_string") or "")
                + str(ti.get("content") or "")).lower()[:4000]
    elif tool == "Bash":
        # Measuring the hook revealed it was watching a door nobody uses: six
        # fires in a day, every one from its own test harness, because most
        # file modification here happens through heredocs and sed -i rather
        # than the Edit tool. A guard on the Edit path only is a gate that is
        # never open.
        cmd = str(ti.get("command") or "")
        if not _writes_a_file(cmd):
            return 0
        target = _scan_text(cmd).replace("\\", "/").lower()[:4000]
        body = target
    else:
        return 0
    if not target:
        return 0
    hay = target + " " + body

    wanted = set()
    for _name, signals, keys in CLASSES:
        if any(_has_word(hay, s) for s in signals):
            wanted.update(keys)
    if not wanted:
        return 0

    # RANK, never first-match-wins. The old loop broke on the first two laws it
    # met in file order, which served a metrics-regime law ahead of "be loud
    # when you skip" for a broken detector -- the right law was present and
    # second, which is the same as absent when only two are shown.
    scored = []
    for law in _laws():
        text = str(law.get("content") or "")
        low = text.lower()
        n = sum(1 for k in wanted if k in low)
        if n:
            scored.append((n, -len(text), text))
    if not scored:
        return 0
    scored.sort(reverse=True)
    hits = [t for _n, _l, t in scored[:MAX_LAWS]]

    _log(target, [h[:120] for h in hits], sorted(wanted))

    lines = ["[xylem law] Relevant to what you are about to write:"]
    lines += ["  - " + h.strip().replace("\n", " ")[:300] for h in hits]
    out = {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                  "additionalContext": "\n".join(lines)}}
    sys.stdout.write(json.dumps(out))
    return 0


if __name__ == "__main__":
    # Fails OPEN by contract -- a memory layer must never be the reason an edit
    # did not happen. But a crash was indistinguishable from having nothing to
    # say: a rename left the Bash branch calling a deleted function, and the
    # hook simply went quiet for every shell write. A skipped step must not
    # look like a completed one, so the ledger records the breakage.
    try:
        sys.exit(main())
    except Exception as exc:
        try:
            from datetime import datetime, timezone
            os.makedirs(os.path.dirname(FIRES), exist_ok=True)
            with open(FIRES, "a", encoding="utf-8") as f:
                f.write(json.dumps({
                    "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "error": "%s: %s" % (type(exc).__name__, exc)}) + chr(10))
        except Exception:
            pass
        sys.exit(0)

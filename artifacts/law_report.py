"""Did putting a law in front of the model change anything?

Recall counts cannot answer this. They measure retrieval, so a law injected at
every session start scores zero while being read every time -- and the laws
this system is proudest of sit at zero while the mistakes they describe keep
getting made.

The honest measure uses evidence already being produced. Every time a rule is
broken badly enough to matter, a constraint gets recorded naming the file it
happened in. So:

    a MISS is a file law_guard fired on, which later shows up in the triggering
    incident or scope of a NEWLY RECORDED constraint

That is the failure the fire was supposed to prevent, happening after the
warning, in writing, dated. It cannot be argued with and nobody has to remember
to score it.

A HELD file is one that fired and has no later incident. That is weaker
evidence -- absence of a recorded incident is not proof of a prevented one --
and it is reported as "no incident since" rather than as a save, because
counting silence as success is how a detector starts lying.
"""
import json
import os
import sys
from collections import Counter, defaultdict

FIRES = os.path.expanduser("~/.xylem/law-fires.jsonl")
REPOS = r"C:\Users\jarms\repos"


def fires():
    if not os.path.exists(FIRES):
        return []
    out = []
    with open(FIRES, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue
    return out


def incidents():
    """Every constraint/decision, with the text that would name a file."""
    rows = []
    for proj in sorted(os.listdir(REPOS)):
        ctx = os.path.join(REPOS, proj, ".context")
        if not os.path.isdir(ctx):
            continue
        for fn in ("constraints.json", "decisions.json"):
            fp = os.path.join(ctx, fn)
            if not os.path.exists(fp):
                continue
            try:
                with open(fp, encoding="utf-8") as f:
                    entries = json.load(f)
            except (OSError, ValueError):
                continue
            for e in entries:
                rows.append((proj, e.get("id"), e.get("created_at") or "",
                             " ".join(str(e.get(k) or "") for k in
                                      ("scope", "triggering_incident", "problem",
                                       "reason", "enforced_by"))))
    return rows


def main():
    fs = fires()
    if not fs:
        print("No fires recorded yet. law_guard writes to %s on every match;\n"
              "this report is meaningful after a few days of real editing." % FIRES)
        return 0

    inc = incidents()
    seen = defaultdict(list)          # basename -> [fire, ...]
    for f in fs:
        seen[os.path.basename(f["file"])].append(f)

    misses = []
    for base, group in seen.items():
        first = min(g["at"] for g in group)
        for proj, eid, created, text in inc:
            # Recorded AFTER the warning, and naming the same file.
            if created > first and base and base in text.lower():
                misses.append((base, proj, eid, first[:10], created[:10]))
                break

    held = len(seen) - len({m[0] for m in misses})
    print("law_guard ledger")
    print("  fires             %d" % len(fs))
    print("  distinct files    %d" % len(seen))
    print("  first fire        %s" % min(f["at"] for f in fs)[:16])
    print()
    print("  MISSED  %d  (fired, then an incident was recorded naming that file)"
          % len({m[0] for m in misses}))
    for base, proj, eid, warned, broke in sorted(misses):
        print("      %-26s warned %s -> %s %s (%s)" % (base, warned, proj, eid, broke))
    print()
    print("  no incident since  %d  -- weak evidence, not a claimed save" % held)
    print()
    top = Counter(l for f in fs for l in f.get("laws") or [])
    print("  most-surfaced laws")
    for law, n in top.most_common(5):
        print("      %3dx  %s" % (n, law[:88]))
    return 0


if __name__ == "__main__":
    sys.exit(main())

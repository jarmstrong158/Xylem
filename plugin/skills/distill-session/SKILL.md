---
name: distill-session
description: "used for 'distill this session', 'capture what we learned', 'harvest this session'. LOCAL capture only — runs `cambium distill` and lets the recall gate decide what earns team scope; it never promotes on request (use promote-to-org to elevate something deliberately). If cambium is missing, says so."
metadata:
  version: "0.1.0"
---

# Distill the session

Turn what this session learned into durable knowledge. Local capture is cheap and safe;
promotion to the team is earned.

## Steps

1. Check for cambium. If `cambium` is not on PATH, say so plainly and stop — the local
   distillation hook and this skill both require it. Point the user at the cambium repo
   to install it. Do not fabricate a result.

2. Run local capture. From the project's git root, run `cambium distill`. This mines the
   session's outcomes (decisions, constraints, notable changes) into the LOCAL knowledge
   store. This is the same capture the SessionEnd hook performs automatically — running
   it by hand is useful mid-session or when the hook was skipped.

3. Let the recall gate work. cambium promotes an item to TEAM scope only once it has
   been recalled enough times to prove it is durable (not a one-off). Do not force-promote
   to team just because it feels important; let the gate decide, and report what crossed.

4. Report. Summarize what was distilled locally and anything that reached team scope.

## Notes

- Local distillation never touches org scope. Promotion to the org brain is a separate,
  deliberate act — use the promote-to-org skill for that.
- If nothing durable came out of the session, that is fine — say so rather than inventing
  knowledge.

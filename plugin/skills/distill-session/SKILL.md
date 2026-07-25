---
name: distill-session
description: "used for 'distill this session', 'capture what we learned', 'harvest this session'. LOCAL capture only — calls cambium's `distill` MCP tool and lets the recall gate decide what earns team scope; it never promotes on request (use promote-to-org to elevate something deliberately). If the cambium server is not connected, says so."
metadata:
  version: "0.1.0"
---

# Distill the session

Turn what this session learned into durable knowledge. Local capture is cheap and safe;
promotion to the team is earned.

## Steps

1. Check for cambium. cambium is an **MCP server**, not a command-line program — there
   is no `cambium` executable to look for on PATH. If the `distill` tool is not available
   in this session, the cambium server is not connected: say so plainly and stop, and
   point the user at the local installer (the plugin alone does not ship the cambium
   server). Do not fabricate a result.

2. Run local capture. Call the `distill` tool. It mines the session's outcomes
   (decisions, constraints, notable changes) into the LOCAL knowledge store, reading the
   agentsync and context-keeper substrates directly. This is the same capture the
   SessionEnd hook performs automatically — calling it by hand is useful mid-session or
   when the hook was skipped. It is idempotent, so a re-run is safe.

3. Let the recall gate work. cambium promotes an item to TEAM scope only once it has
   been recalled enough times to prove it is durable (not a one-off). Do not force-promote
   to team just because it feels important; let the gate decide, and report what crossed.

4. Report. Summarize what was distilled locally and anything that reached team scope.

## Notes

- Local distillation never touches org scope. Promotion to the org brain is a separate,
  deliberate act — use the promote-to-org skill for that.
- If nothing durable came out of the session, that is fine — say so rather than inventing
  knowledge.

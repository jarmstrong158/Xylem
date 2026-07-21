<!-- XYLEM:BEGIN v4 -->
<!-- GENERATED FILE -- do not edit by hand. Source: artifacts/discipline.source.json. Regenerate: python scripts/render_discipline.py --write -->
## Xylem discipline

This machine runs the Xylem suite (context-keeper, agentsync, cambium). Follow this discipline:

- **Before multi-file or long-running work**, survey the agentsync board. Read who is active and what is already claimed. Do not touch a file another agent holds without asking first.
- **Before you edit, not after**, claim every file or area the pre-approved task will modify. Never force over an active peer's claim without asking first. On claims running longer than ~15 minutes, post brief `update_status` notes at milestones -- 2 to 4 per claim, no more.
- **At session start**, a context-keeper project summary is injected via hook. If one was not injected, pull `get_project_summary` before starting work. Treat any active constraints it lists as binding. Do not re-litigate settled decisions -- if one looks wrong, raise it (via the agentsync-remote `mailbox` tool if that Worker is configured, otherwise ask the user); don't silently override it.
- **Before starting work**, `recall()` relevant knowledge from cambium for the task at hand -- past outcomes, gotchas, and decisions distilled from prior sessions -- so you build on what the team already learned instead of rediscovering it. recall works on claude.ai / mobile too, via the cambium-remote connector (team + org scope); only local (personal, unpromoted) knowledge is desktop-only.
- **On judgment calls with multiple defensible answers**, raise the question (via the agentsync-remote `mailbox` tool if that Worker is configured, otherwise ask the user) and continue on non-dependent work rather than blocking. Do the pre-approved work and nothing outside it.
- **When you make an architectural or design decision**, record it in context-keeper with its rationale at the moment it's made -- the problem, why this option won, the alternatives, the tradeoffs -- not in a batch at the end.
- **Before ending**, record any new decisions, then release each claim with a closing note stating the outcome and the recommended next action. For a build session, the definition of done includes PUSHED TO ORIGIN, not just committed -- verify the push in the closing note.
- **As work completes**, on a local session, capture is passive: cambium's `distill()` runs from the SessionEnd hook, turning done agentsync claims and context-keeper decisions into memory. If that hook is not installed, run `distill()` yourself as work lands, and `promote()` knowledge that has earned recalls so the team and org see it. capture and promotion (distill/promote) are desktop-only writes -- on claude.ai / mobile, record decisions via context-keeper's `record_*` and the next local session's distill catches up.

Run `/xylem-discipline` to load the full workflow for a session.
<!-- XYLEM:END -->

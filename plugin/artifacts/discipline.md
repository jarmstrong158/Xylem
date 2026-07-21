# Xylem discipline

This session is running with the Xylem stack: a memory that persists, coordination across agents, and a loop that compounds what you learn. Work the habit, not just the task.

## The loop

1. SURVEY THE BOARD -- Before multi-file or long-running work, survey the agentsync board. Read who is active and what is already claimed. Do not touch a file another agent holds without asking first.

2. CLAIM YOUR TOUCHES -- Before you edit, not after, claim every file or area the pre-approved task will modify. Never force over an active peer's claim without asking first. On claims running longer than ~15 minutes, post brief `update_status` notes at milestones -- 2 to 4 per claim, no more.

3. LOAD MEMORY -- At session start, a context-keeper project summary is injected via hook. If one was not injected, pull `get_project_summary` before starting work. Treat any active constraints it lists as binding. Do not re-litigate settled decisions -- if one looks wrong, raise it (via the agentsync-remote `mailbox` tool if that Worker is configured, otherwise ask the user); don't silently override it.

4. RECALL WHAT THE TEAM ALREADY LEARNED -- Before starting work, on a local session, `recall()` relevant knowledge from cambium for the task at hand -- past outcomes, gotchas, and decisions distilled from prior sessions -- so you build on what the team already learned instead of rediscovering it. cambium is local-only (it has no remote Worker), so on claude.ai / mobile it is unavailable: lean on the injected context-keeper summary and `get_context` there instead.

5. WORK, AND RAISE JUDGMENT CALLS -- On judgment calls with multiple defensible answers, raise the question (via the agentsync-remote `mailbox` tool if that Worker is configured, otherwise ask the user) and continue on non-dependent work rather than blocking. Do the pre-approved work and nothing outside it.

6. RECORD DECISIONS AS YOU MAKE THEM -- When you make an architectural or design decision, record it in context-keeper with its rationale at the moment it's made -- the problem, why this option won, the alternatives, the tradeoffs -- not in a batch at the end.

7. RELEASE WITH A CLOSING NOTE -- Before ending, record any new decisions, then release each claim with a closing note stating the outcome and the recommended next action. For a build session, the definition of done includes PUSHED TO ORIGIN, not just committed -- verify the push in the closing note.

8. CAPTURE (MOSTLY AUTOMATIC) -- As work completes, on a local session, capture is passive: cambium's `distill()` runs from the SessionEnd hook, turning done agentsync claims and context-keeper decisions into memory. If that hook is not installed, run `distill()` yourself as work lands, and `promote()` knowledge that has earned recalls so the team and org see it. cambium is local-only, so on claude.ai / mobile there is no distill/promote -- record decisions via context-keeper's `record_*` and the next local session's distill catches up.

Recall, claim, record, release. Small, honest, compounding. The point is that the next session - yours or a teammate's - starts smarter than this one did.

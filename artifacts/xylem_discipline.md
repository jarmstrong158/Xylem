---
description: Load the Xylem coordination + memory workflow for this session
argument-hint: [describe the pre-approved task]
---

# Xylem discipline

Follow this workflow for the whole session. The loop:

```
Survey board -> claim touches -> load memory (recall) -> work (raise judgment calls) -> record decisions -> release with closing note -> distill captures
```

**Pre-approved work for this session:** $ARGUMENTS

If the slot above is empty, ask the user what the pre-approved task is before claiming anything.

## 1. Survey the board
Call the agentsync `survey` tool. Read who is active and what is already claimed. Do not touch a file another agent holds without asking first.

## 2. Claim your touches
Use agentsync `claim` for every file/area the pre-approved task will modify. Claim before you edit, not after. If a claim conflicts with an active peer, stop and ask the user -- never force over an active claim. On claims running longer than ~15 minutes, post brief `update_status` notes at milestones -- 2 to 4 per claim, no more.

## 3. Load memory
Read the context-keeper project summary (it is also injected at session start via hook). If a project summary was not injected at session start, pull `get_project_summary` before starting work. Then `recall()` relevant knowledge from cambium for the task at hand -- prior outcomes, gotchas, and decisions distilled from earlier sessions -- so you build on what the team already learned. Treat every active constraint as binding. Do not re-litigate settled decisions; if one looks wrong, raise it (via the agentsync-remote `mailbox` tool if that Worker is configured, otherwise ask the user).

## 4. Work
Do the pre-approved work and nothing outside it. On any judgment call with multiple defensible answers, raise the question (via the agentsync-remote `mailbox` tool if that Worker is configured, otherwise ask the user) and continue on non-dependent work rather than blocking.

## 5. Record decisions
The moment you make an architectural or design decision, record it in context-keeper with its rationale -- inline, not batched at the end.

## 6. Release with a closing note
Before ending, confirm all new decisions are recorded, then `release` each claim with a closing note stating the outcome and the recommended next action for whoever picks it up. For a build session, the definition of done includes PUSHED TO ORIGIN, not just committed -- verify the push in the closing note.

## 7. Capture (mostly automatic)
cambium's `distill()` fires from the SessionEnd hook, turning your done agentsync claims and recorded context-keeper decisions into memory with zero extra effort. If that hook is not installed on this machine, run `distill()` yourself as work lands, and `promote()` knowledge that has earned recalls so the team and org benefit.

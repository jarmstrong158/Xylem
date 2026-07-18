---
name: claim-work
description: "used before starting work that touches files, and when finishing: 'claim this task', 'start work on X', 'release that'. survey -> check_conflicts on the paths -> claim(task,touches,branch); update_status for milestones; release with a rich outcome note (what shipped, tests, commit/PR, next action)."
metadata:
  version: "0.1.0"
---

# Claim and release work

Stake a claim before you edit files so parallel agents (and your other devices) do not
collide, and release with a real outcome so the board tells the truth.

## Starting work

1. `survey` the board to see every peer's active claim and any pending mailbox notes.

2. `check_conflicts` on the exact paths you intend to modify. If an active peer overlaps
   your paths, do not barge in: narrow your `touches`, wait, coordinate via the mailbox,
   or claim with `force` only when you are sure it is safe. A claim marked done never
   blocks.

3. `claim` the work with:
   - **task** — a short, specific description.
   - **touches** — the paths/globs you will modify (keep them tight).
   - **branch** — the branch this work lands on.

## During work

- Use `update_status` at meaningful milestones (e.g. in-progress, blocked) so peers can
  see movement without asking.

## Finishing

4. `release` with a rich outcome note — not just "done". Include:
   - what actually shipped,
   - what was tested (and the result),
   - the commit SHA / PR number,
   - the recommended next action.
   This note becomes the coordination history other sessions read, so make it worth
   reading.

## Notes

- Claim before the first edit, not after — the point is to prevent collisions, not
  document them.
- If you finish only part of the claim, say so in the release note and open a follow-up
  rather than leaving a stale in-progress claim on the board.

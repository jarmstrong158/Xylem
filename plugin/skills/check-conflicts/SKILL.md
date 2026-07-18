---
name: check-conflicts
description: "READ-ONLY board inspection; it does NOT claim or release — use claim-work to start or finish work. Used for 'who's working on what', 'any conflicts', 'show the board', 'is anyone on this file'. Surveys the agentsync board, runs check_conflicts on named paths, reads the mailbox; reports active peers, overlaps, and pending notes. A done claim never blocks."
metadata:
  version: "0.1.0"
---

# Check the coordination board

Answer "who is working on what" without changing anything. This skill is strictly
read-only — it never claims, releases, or edits.

## Steps

1. `survey` the agentsync board. Report each peer's active claim: who, the task, the
   touched paths, the branch, and the status.

2. If the user named specific paths, run `check_conflicts` on them and report whether any
   active peer overlaps. Remember: a claim whose status is done never blocks — only
   active (in-progress) claims count as conflicts.

3. Read the mailbox and surface any pending human-in-the-loop notes (from/to, message,
   time).

4. Summarize for the human:
   - active peers and what each is doing,
   - any overlaps on the paths in question,
   - pending notes that need attention.

## Notes

- Do not stake or release a claim here. If the user then wants to start or finish work,
  hand off to the claim-work skill.
- If the board is empty (no active claims), say so plainly — an empty board is a valid,
  clean state.

---
name: record-decision
description: "used when the user wants to capture an engineering or design decision so it persists across sessions: 'record this decision', 'log why we chose X', 'remember this', 'note this constraint', or after a non-obvious choice. Writes it into context-keeper (problem, why_chosen, alternatives, tradeoffs, tags) and records any standing rule via record_constraint; mirror to DECISIONS.md if present."
metadata:
  version: "0.1.0"
---

# Record a decision

Capture the decision the moment it is made, while the reasoning is fresh, so future
sessions inherit it instead of re-deriving it.

## Steps

1. Pick the project. Default to the current repo's context-keeper project. If it is
   ambiguous, ask which project this belongs to.

2. Write the decision with `record_decision`. Fill every field that carries signal:
   - **summary** — one line stating what was decided.
   - **problem / context** — what forced the choice.
   - **why_chosen** — why this option won, concretely.
   - **alternatives** — the options considered and why they lost.
   - **tradeoffs** — what this costs; what you gave up.
   - **tags** — the subsystems/topics it touches, for later recall.
   Reference commits, PRs, files, and other decision ids where relevant.

3. Record standing rules separately. If the decision establishes a rule that future
   work must not violate ("never do X", "always route through Y"), record it with
   `record_constraint` — with the rule, the reason, and the triggering incident.
   Constraints are surfaced first during recall, so they act as guardrails.

4. Mirror to DECISIONS.md. If the repo keeps a DECISIONS.md, append a matching entry
   (id, date, summary, rationale) so the human-readable log stays in sync. If there is
   no DECISIONS.md, do not create one unless asked.

5. Confirm. Report the id(s) written and where (context-keeper project, and
   DECISIONS.md if mirrored).

## Notes

- Prefer one crisp decision over a vague blob. Split unrelated choices.
- Do not record secrets, tokens, or credentials — only the decision and its reasoning.
- If the choice supersedes an earlier decision, note the superseded id so the record
  stays coherent.

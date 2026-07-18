---
name: recall-context
description: "used at the start of a task or when asked 'what did we decide about X', 'what are the constraints here', 'recall context'. Calls get_project_summary to orient (read active constraints), then get_context / query_entries; cites ids; flags if a planned change would violate a decision."
metadata:
  version: "0.1.0"
---

# Recall project context

Before doing project work, load what has already been decided so you build on it instead
of contradicting it.

## Steps

1. Orient with `get_project_summary`. This is the single best first call: it returns
   the counts by kind, the ids present, the **active constraints**, and the most recent
   decisions. Read the active constraints first — they are the hard rules.

2. Go deeper as needed:
   - `get_context` with a free-text query for relevance-ranked entries on the topic.
   - `query_entries` when you want a precise filter (by kind, tags, status, or id).
   Pull the decisions and constraints that touch the files or subsystem you are about to
   change.

3. Cite ids. When you summarize what was decided, reference the entry ids (e.g.
   dec-012, con-006) so the human can verify and so later records can link back.

4. Flag conflicts. If the change you are about to make would violate a recorded decision
   or constraint, STOP and surface it: name the id, quote the rule, and ask whether to
   proceed (and supersede it) or change course. Do not silently break a settled decision.

## Notes

- Deprecated entries are excluded by default; include them only when tracing history.
- If the project has no entries yet, say so plainly rather than guessing.
- Recall is project-local. For cross-project or org-wide knowledge, use recall-knowledge.

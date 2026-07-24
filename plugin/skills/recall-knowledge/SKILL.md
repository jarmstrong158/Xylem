---
name: recall-knowledge
description: "used for cross-project questions: 'what does the org know about X', 'any universal practice for Y'. Calls cambium's `recall` MCP tool against the federated org brain; distinct from recall-context (project-local). Surfaces scope + endorsed_as."
metadata:
  version: "0.1.0"
---

# Recall org knowledge

Answer cross-project questions from the federated org brain — the knowledge that has been
distilled and promoted across every project, not just this repo.

## Steps

1. Check for cambium. cambium is an **MCP server**, not a command-line program — there
   is no `cambium` executable on PATH. If the `recall` tool is not available in this
   session, say so and stop; point the user at the local installer, which registers the
   cambium server (the plugin alone does not ship it).

2. Call `recall` with a focused query for the topic. This searches the federated
   knowledge that has been promoted to team and org scope across projects. On claude.ai
   and mobile the same call goes through the cambium-remote connector (team + org scope
   only; local scope stays desktop-only).

3. Report each hit with its provenance:
   - the knowledge itself,
   - its **scope** (local / team / org),
   - **endorsed_as** — the generalized, universal statement if it has been endorsed at
     org scope,
   - the originating project, so the human can trace it.

4. Distinguish from project recall. If the question is really about THIS project's
   decisions and constraints, use recall-context instead — that reads context-keeper for
   the local project. recall-knowledge is for "what does the org know", spanning projects.

## Notes

- Prefer org- and team-scope hits for universal questions; call out when an answer is
  only local to one project and may not generalize.
- If recall returns nothing, say so rather than guessing — an empty result is a real
  answer.

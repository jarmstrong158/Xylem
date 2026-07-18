---
name: recall-knowledge
description: "used for cross-project questions: 'what does the org know about X', 'any universal practice for Y'. Runs `cambium recall \"<query>\"` against the federated org brain; distinct from recall-context (project-local). Surfaces scope + endorsed_as."
metadata:
  version: "0.1.0"
---

# Recall org knowledge

Answer cross-project questions from the federated org brain — the knowledge that has been
distilled and promoted across every project, not just this repo.

## Steps

1. Check for cambium. If `cambium` is not on PATH, say so and stop — this skill needs it.
   Point the user at the cambium repo.

2. Run `cambium recall "<query>"` with a focused query for the topic. This searches the
   federated knowledge that has been promoted to team and org scope across projects.

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

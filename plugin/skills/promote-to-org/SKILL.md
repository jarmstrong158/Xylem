---
name: promote-to-org
description: "used for 'promote this to the org', 'make this a universal practice', 'review promotions'. Calls cambium's `review_promotions` MCP tool, then promotes ONE AT A TIME through the generalization gate (restate project-specific bodies via org-content) and endorsement gate; serialize PRs."
metadata:
  version: "0.1.0"
---

# Promote knowledge to the org

Elevate a team-scope learning into universal, org-wide practice. This is deliberate and
careful — org knowledge is read by every project, so it must be general and endorsed.

## Steps

1. Check for cambium. cambium is an **MCP server**, not a command-line program — there
   is no `cambium` executable on PATH. If its tools are not available in this session,
   say so and stop.

2. Call `review_promotions` to list team-scope candidates ready for org promotion.
   Note anything flagged `org_needs_generalization` — those carry project-specific detail
   that must be rewritten before they go org-wide.

3. Promote ONE AT A TIME. For each candidate, in a separate step:
   - **Generalization gate** — if the body is project-specific, restate it as a universal
     practice via the org-content path, keeping the concrete case as an example. Do not
     ship a project's local body to org readership.
   - **Endorsement gate** — promote only what genuinely holds across projects; endorse it
     as universal practice and surface `endorsed_as`.

4. Serialize the PRs. Org promotion writes to the shared knowledge repo, and parallel
   promotion PRs conflict on the knowledge file. Open and merge them one at a time — never
   a batch of concurrent org PRs.

5. Report each promoted item, its `endorsed_as` statement, and the PR.

## Notes

- When unsure whether something is universal, leave it at team scope. Over-promoting
  pollutes the org brain with local specifics.
- Never hand-edit the org knowledge file to work around the gates — always go through the
  tool so the generalization and endorsement checks run.

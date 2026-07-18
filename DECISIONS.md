# Decisions

Human-readable mirror of the `xylem` project's decision memory in context-keeper.
Newest first. Each entry mirrors a context-keeper decision id; the canonical record
(with alternatives, tradeoffs, and tags) lives in context-keeper.

---

## dec-009 — Plugin failed to LOAD despite passing validation: `manifest.hooks` double-loads the auto-loaded standard hooks file
*2026-07-18*

`claude plugin validate` passed for both manifests, but a real test-install surfaced a
runtime load failure it missed: *"Duplicate hooks file detected: ./hooks/hooks.json
resolves to already-loaded file ... The standard hooks/hooks.json is loaded automatically,
so manifest.hooks should only reference additional hook files."* Claude Code auto-loads
`plugin/hooks/hooks.json` by convention, so the explicit `"hooks": "./hooks/hooks.json"`
in `plugin.json` loaded it a second time and the plugin errored (Status: failed to load).
**Fix:** remove the `hooks` field from `plugin.json`; the standard hooks file still loads.
After the fix, reinstall showed **Status: enabled** and `claude plugin details xylem`
enumerated all components — Skills (7), Hooks (2), MCP servers (2). **Lesson:** validation
checks manifest shape, not the auto-load/duplicate-load rules — always test-install
(marketplace add + install + details) to confirm a plugin actually loads.

## dec-008 — Packaged the Xylem stack as a Claude Code plugin + turned the repo into its marketplace
*2026-07-18*

The repo root now carries a marketplace catalog (`.claude-plugin/marketplace.json`) and a
self-contained `plugin/`, so the stack installs in two commands. Memory + coordination
ship as HTTP MCP servers (`context-keeper-remote`, `agent-sync-remote`) configured in
`plugin/.mcp.json` with URL + Bearer-token env vars (the header convention from dec-004).
Knowledge ships via the optional `cambium` CLI: `scripts/distill.sh` (SessionEnd) always
exits 0 and no-ops if cambium is absent, and the knowledge skills degrade gracefully.
SessionStart cats an ASCII-only discipline primer. Seven skills encode the habit. Gotcha:
the hooks file must wrap its events in a top-level `"hooks"` key or `claude plugin
validate` fails. Shipped via PR #30 (`b5ab7a6`).

## dec-007 — Dashboard remote coordination was empty: agentsync-remote returns `content[].text`, not `structuredContent`, and the history regex dropped releases-with-notes
*2026-07-17*

The remote dashboard rendered its coordination panels empty with no error. Two causes:
(1) `rpc_call_tool` only read `structuredContent`, but agentsync-remote returns
survey/history only as `content[].text` — fixed with a JSON fallback; (2) the history
regex anchored the closing quote to end-of-line, dropping every `releases '<task>' (note)`
commit — fixed by matching the task lazily up to a quote followed by EOL or ` (`. Also
always fold survey's current claims into the timeline. Verified live: 43 events / 2 peers
(was 0/0). PR #25 (`21b3922`), refresh `32c6c4e`.

## dec-006 — The Pages dashboard auto-refreshes via a scheduled GitHub Action
*2026-07-17*

A scheduled Action runs the generator's `--remote` route and commits
`docs/dashboard.html`, keeping the public snapshot current with no human in the loop.
Connector URLs are Actions secrets (never committed, never written into the HTML — only
counts/summaries). A guard makes the generator exit non-zero when it collects zero
projects and zero events, so a transient Worker outage fails the step and leaves the last
good dashboard intact instead of overwriting it with an empty one. The job skips (with a
notice) until the secrets exist.

## dec-005 — The observability dashboard is a generic template + zero-dep stdlib generator with a local-first default and an opt-in `--remote` route
*2026-07-17*

`xylem_dashboard.py` renders a generic template from a JSON data island. The local-first
route reads the agentsync branch via git history + each clone's `.context/` store (+
optional `.cambium` funnel) and needs no secret. The opt-in `--remote` route reads the
token only from the gitignored config and writes only counts/summaries to the
self-contained HTML — never a token. (A view-time browser fetch was rejected because it
would embed the token in the page.)

## dec-004 — Flipped context-keeper-remote to `available:true` and gave it a Bearer Authorization header
*2026-07-11*

context-keeper-remote was marked `available:false` in `manifest.json`, so `enabled_servers()`
filtered it out and the installer never registered the deployed remote mirror — a live
bug. Flipping it `available:true` puts it into `enabled_servers` (still skipped gracefully
when `CONTEXT_KEEPER_REMOTE_URL` is unset, preserving local-first). Added the
`Authorization: Bearer` header (env key `CONTEXT_KEEPER_REMOTE_TOKEN`, by analogy with
agentsync-remote) because the suite uses fail-closed auth. Verified via `installer.py
--dry-run`.

## dec-003 — Landing page repositioned around "one install → habit by default"
*2026-07-11*

`docs/index.html` led with "agents that CAN remember/coordinate/ask", underselling the
differentiator: the installer's habit layer that makes those behaviors happen by default.
New hero + install command up top, a "week of real use" section with four clickable
commit/PR-linked items (kept falsifiable), and two new honest-labeled numbers groups.
Shipped to origin/main (`44f13a5`).

## dec-002 — Version signals shipped; `.context/` is now gitignored
*2026-07-10*

Version-signals build landed on main (PR #10, `565c5b2`); the deployed template version is
v2. `.context/` was added to `.gitignore` because it is machine-local session memory, is
not tracked upstream, and committing it would leak local decisions into the public repo —
future `git add -A` runs must not resurrect it.

## dec-001 — Version signals: `manifest.json` "version" is the single source of truth for the deployed fence stamp
*2026-07-10*

The installer stamps the CLAUDE.md begin fence as `<!-- XYLEM:BEGIN vN -->` from the
manifest int, and `version_check.py` (SessionStart hook) compares the installed block's
stamp against the template, fetching origin first so the "vN available" nudge fires on
upstream release. Making the manifest the single source of truth keeps stamp and template
version from drifting; detection informs, and only the explicit `xylem update` verb
rewrites a block. All git dependencies are fail-soft (exit 0, output nothing on error).

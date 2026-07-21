# Decisions

Human-readable mirror of the `xylem` project's decision memory in context-keeper.
Newest first. Each entry mirrors a context-keeper decision id; the canonical record
(with alternatives, tradeoffs, and tags) lives in context-keeper.

---

## dec-019 — 1b: a shared session pointer so context-keeper and agentsync follow the session too
*2026-07-21*

Fix 1 made only cambium follow the session (via its own config file);
context-keeper and agentsync stayed pinned to the frozen install-time
`$PROJECT_DIR`, so they targeted the wrong project for every non-xylem session.
**Fix:** the SessionStart primer hook now writes a shared pointer
`~/.xylem/active_project.json` recording the session's project (written first,
before the cambium logic, so it happens even when cambium is absent), and the
manifest unpins `CONTEXT_KEEPER_PROJECT` and `AGENTSYNC_REPO`. context-keeper
resolves the project per call (`env > pointer > cwd/.context`) and agentsync's
`_cfg` falls back to the pointer when `AGENTSYNC_REPO` is unset — so all three
servers now follow whichever project the session is in. Each keeps an
explicit-env override to force a fixed project. **Tradeoff:** the pointer is
global, so concurrent sessions in different projects race on it (fine for a solo
operator); cambium keeps its own config-file mechanism rather than migrating.

## dec-018 — Automatic recall: a SessionStart primer hook + unpinning cambium from the frozen install project
*2026-07-21*

A stack audit found the compound loop was half-open: `distill()` (capture) ran from the
SessionEnd hook, but recall was advisory prose, so cambium got ignored at session start.
Separately, the installer baked `$PROJECT_DIR` into `CAMBIUM_REPO`, freezing cambium to the
install-time project — so recall/capture hit the wrong store for every other project.
**Fix:** `artifacts/session_primer_hook.py` (SessionStart) injects cambium's new read-only
`session_primer()` digest and resolves the session's real project from the payload `cwd`
(not the hook's own cwd, which is the launch dir), writing `CAMBIUM_REPO` into cambium's
config file so the per-call-resolving server follows the session — the only reliable way,
since context-keeper/agentsync resolve their project once at startup. The manifest no longer
pins `CAMBIUM_REPO`, letting that config value win. Installer-path only (the plugin is
remote-only and cambium has no remote). **Deferred:** context-keeper/agentsync are still
startup-pinned; making them follow the session needs a per-call resolution change.

## dec-017 — Adoption: the installer bootstraps its own servers, pins them, and both install paths self-diagnose
*2026-07-21*

The stdio servers resolved to `$XYLEM_PARENT/<repo>` sibling dirs that only existed on
the author's machine, so a fresh clone of just Xylem registered three MCP servers pointing
at non-existent paths that died silently on launch — adoption was structurally impossible.
**Fix, three parts:** (1) each stdio server gains a `source {repo, dir, ref}` block and the
installer clones any missing server into the exact path the manifest resolves to — preview
by default, `--apply` clones, `--no-fetch` skips, `XYLEM_SOURCE_BASE` retargets forks,
fail-soft without git. (2) A `doctor` subcommand reports per-server startability without
launching anything (script present + parses, interpreter imports `mcp`; remotes optional),
non-zero exit if any required server is broken. (3) `ref` is pinned to each server's latest
release tag (`v0.15.0`/`v0.1.0`/`v0.1.0`; main is only 1–3 commits ahead) so fresh installs
get a reproducible, tested-together snapshot — bump on server releases. **Plugin path:** a
plugin structurally can't clone Python stdio servers, and its `.mcp.json` is already
correct, so the honest fix there was self-diagnosis, not local install — `primer.py` now
prints a one-line notice when neither Worker URL is set (quiet the moment either is), the
plugin-side equivalent of `doctor`. **Lesson:** "clone my six repos exactly like I have
them" is not an install; the gap between an impressive repo and an adopted one is the
~clean-machine bootstrap, not more features.

## dec-016 — Generate, don't copy: every duplicated artifact now has one source and a drift test
*2026-07-18*

An external audit found three bugs that were all the same bug: hand-maintained copies
that drifted. `plugin/.mcp.json` was a hand-copy of `manifest.json`'s http servers and
had drifted on **both** the server name (`agent-sync-remote` vs `agentsync-remote`,
which silently broke every `mcp__agentsync-remote__*` reference in the habit prose on a
plugin install) and the auth scheme (dec-014). The habit prose existed in three copies,
and the plugin's had quietly lost the `update_status` cadence, the mailbox escalation
rule, and "done means pushed to origin" — so plugin users got a strictly weaker
discipline with no signal anything was missing. **Fix:** one source each
(`artifacts/discipline.source.json`, `manifest.json`), rendered by
`scripts/render_discipline.py`, with `tests/test_generated_sync.py` failing if any
committed output drifts. Negative-tested: reintroducing both original bugs fails CI.
**Lesson:** when the same fact lives in three files, the question isn't whether they'll
drift, it's which one you'll ship wrong.

## dec-015 — Both installers preview by default; `--apply` is required to write
*2026-07-18*

`./install.sh` at the repo root applied immediately while `install/install.sh`
dry-ran by default. Two scripts, same filename, same repo, **opposite destructive
defaults** — a footgun that was going to cost someone their config eventually. **Fix:**
the root installer now previews by default and takes `--apply`, matching the suite
installer. `--dry-run` is kept as an accepted no-op so every previously published
command line still behaves identically. **Tradeoff:** a bare `./install.sh` no longer
installs, which breaks muscle memory — accepted, because the failure mode is now a
harmless preview instead of an unintended write.

## dec-014 — Path-token auth is canonical; the plugin's Bearer headers were inert (supersedes dec-004)
*2026-07-18*

dec-004 added an `Authorization: Bearer` header to the remote servers, and dec-008
carried "the header convention from dec-004" into `plugin/.mcp.json`. But the Workers
had since moved to authenticating on the URL path alone — `manifest.json`,
`install/README.md`, `docs/design-principles.md`, and an assertion in
`tests/test_manifest.py` all said so. **That reversal was never recorded**, so the
plugin kept shipping a header nobody reads and documented two `*_TOKEN` env vars that
did nothing, misleading users into thinking they had configured auth. **Fix:** headers
removed (now impossible to reintroduce — `.mcp.json` is generated, see dec-016), README
corrected to two env vars, and the auth model documented in `docs/manifest.md`.
**Lesson:** an unrecorded reversal is worse than an unrecorded decision — the stale
record actively propagates.

## dec-013 — `manifest.json` launches servers with `$PYTHON`, not `python3`
*2026-07-18*

The manifest hardcoded `python3` for all three stdio servers. On a very common Windows
setup that resolves to the Microsoft Store shim while the interpreter that actually has
`mcp` installed is `python` — reproduced on the author's own machine, where
`python3 -c "import mcp"` fails and `python -c "import mcp"` succeeds. The installer
was registering three servers into a config where they could never start, with no
diagnostic. **Fix:** `$PYTHON` resolves to `sys.executable` — if you could run the
installer, the servers can run, and installing from a virtualenv registers that
virtualenv. The irony worth noting: `install.sh` already did correct `python3`→`python`
discovery *with* a version check; it just never applied that care to the manifest.

## dec-012 — The dashboard generator scrubs home directories from everything it publishes
*2026-07-18*

`xylem_dashboard.py` claimed in its own docstring that "nothing secret is ever written
to the output," while emitting up to 600 characters of arbitrary release notes verbatim
onto a public GitHub Pages site. The live page contained
`C:\Users\<user>\repos\ollama` — a real username, published. **Fix:** a `scrub_text()`
pass over every free-text field, a `--no-notes` flag, an honest docstring, and the
committed `docs/dashboard.html` scrubbed in place. Also deleted
`docs/dashboard.data.json`, a hand-written file that no code read and that the
generator provably could not have produced — it looked like the dashboard's source and
contradicted it. **Lesson:** "we only publish counts and summaries" stops being true
the moment a summary is free text a human typed.

## dec-011 — Hook ownership is explicit, not a filename substring match
*2026-07-18*

Uninstall identified Xylem's hooks by substring-matching generic filenames like
`session_start_hook.py`. An unrelated tool's `/opt/othertool/session_start_hook.py` was
silently deleted by both install and uninstall — directly violating the headline
"additive, never clobbers foreign entries" claim. **Fix:** every hook group Xylem
writes carries a `"_xylem": true` sentinel; legacy groups are still recognized by
resolving under the xylem root, so nothing is orphaned. Now covered by an end-to-end
test asserting a foreign hook survives install *and* uninstall.

## dec-010 — The test suite runs in CI, on three platforms
*2026-07-18*

75 tests existed and nothing ran them: `.github/workflows/` contained only the
dashboard refresh job. The README cited `tests/test_manifest.py` as a credibility
signal, so a reviewer who went looking for a green check found none and reasonably
concluded the tests were decorative. **Fix:** `tests.yml` runs `unittest discover` on
ubuntu/macos/windows against Python 3.8 and 3.12 — the floor the installer claims and a
current release — with no `pip install` step, so the stdlib-only constraint is enforced
by the job itself rather than by good intentions.

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

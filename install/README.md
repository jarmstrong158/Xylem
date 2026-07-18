# Xylem suite installer

One command wires the Xylem servers into whatever coding agents you already have.
The suite is **mixed-transport** — some servers are local processes (stdio), some
are remote Cloudflare Workers (http) — and the installer handles both from a
single declaration.

Zero dependencies: stdlib Python 3 only. macOS, Linux, Windows.

## The pieces (each reviewable on its own)

| File | What it is |
|---|---|
| `servers.json` | **Manifest — data, not code.** Declares each suite server: name, `transport` (`stdio`/`http`), and either its launch command/args or the config key its URL comes from. Flipping a tool's transport or adding a server is an edit here, nothing else. |
| `xylem_install.py` | **The installer.** All real logic: agent detection, per-agent config schemas, additive merge, dry-run diff, uninstall. |
| `install.sh` / `install.ps1` | **Thin bootstraps.** Locate Python 3 and hand off. No logic. |
| `xylem.config.example.json` | Template for your machine-specific values. Copy to `xylem.config.json` (gitignored) and fill in. |

## Setup

1. Copy the template and fill in only the servers you run:
   ```sh
   cp install/xylem.config.example.json install/xylem.config.json
   # edit install/xylem.config.json
   ```
   Every key can instead be an environment variable of the same name (env wins).
   The remote Worker URLs embed a secret token — that is exactly why they live in
   this **untracked** file (or the environment), never in `servers.json`. The
   Workers authenticate on that URL path token alone; they never read an
   Authorization/Bearer header, so the whole `…/mcp/<token>` URL is the credential.

   > **Point context-keeper and cambium at the same clone.** `CONTEXT_KEEPER_PROJECT`
   > and `CAMBIUM_REPO` must be the **same working tree** (and so should
   > `AGENTSYNC_REPO`): cambium reads context-keeper's `.context/` from
   > `CAMBIUM_REPO/.context/` and agentsync's coordination branch from
   > `CAMBIUM_REPO`. If they diverge, `distill()` silently captures nothing from
   > context-keeper. Likewise `AGENTSYNC_BRANCH` (what agentsync writes) and
   > `CAMBIUM_AGENTSYNC_BRANCH` (what distill reads) must name the **same** branch;
   > both default to `agentsync`.

2. See what would happen (writes nothing):
   ```sh
   install/install.sh                 # macOS/Linux  (dry-run install)
   install\install.ps1                # Windows
   ```

3. Apply it:
   ```sh
   install/install.sh install --apply
   install\install.ps1 install --apply
   ```

Other commands:
```sh
install/install.sh list-agents             # which agents are detected here
install/install.sh install --only cursor,vscode
install/install.sh uninstall               # dry-run removal
install/install.sh uninstall --apply       # remove only what Xylem added
install/install.sh uninstall --apply --only cursor   # ...from one agent only
```

> ### Two scripts named `install.sh` — same defaults, different jobs
>
> | Script | Default with no `--apply` | What it does |
> |---|---|---|
> | **`install/install.sh`** (this directory) | **PREVIEW** — writes nothing | Registers the servers across 7 editors |
> | **`./install.sh`** at the repo root | **PREVIEW** — writes nothing | Claude Code only, plus the habit layer |
>
> Both preview by default and both need an explicit `--apply`. They used to ship
> *opposite* destructive defaults under the same filename, which is exactly the
> kind of thing that eventually costs someone their config; that is fixed.
> `--dry-run` is still accepted by both, so older command lines are unchanged.
>
> The scripts in *this* directory also print a `=== Xylem installer: DRY-RUN … ===`
> banner on startup so you can always see which mode you are in.

## What it touches

Detected agents and their MCP config files:

| Agent | Config file | Remote (http) support |
|---|---|---|
| Claude Code | `~/.claude.json` | yes (`type: http`) |
| Cursor | `~/.cursor/mcp.json` | yes (`url`) |
| Windsurf | `~/.codeium/windsurf/mcp_config.json` | yes (`serverUrl`) |
| VS Code | `…/Code/User/mcp.json` | yes (`type: http`) |
| Claude Desktop | `…/Claude/claude_desktop_config.json` | no — add remote connectors via its UI; stdio servers are merged |
| Zed | `~/.config/zed/settings.json` | yes (`url`) |
| GitHub Copilot CLI | `~/.copilot/mcp-config.json` (or `$COPILOT_HOME`) | yes (`type: http`) |

Zed's `context_servers` entries are written in Zed's **current** shape — a flat
`{"command": "<path>", "args": [...], "env": {...}}` for stdio (Zed's
`ContextServerCommand` is `#[serde(flatten)]` with its `path` field renamed to
`command`, so it is a string with sibling `args`, *not* a nested `{path, args}`
object — that was the pre-2025-06-27 form), and `{"url": ...}` for remote HTTP.
The `"source": "custom"` key that Zed used between 2025-06 and 2025-11 is
obsolete — Zed's own migrator strips it — so the installer no longer emits it.
Verified against `crates/settings_content/src/project.rs` and
[zed.dev/docs/ai/mcp](https://zed.dev/docs/ai/mcp).

## Safety

- **Dry-run is the default.** Nothing is written without `--apply`. (The
  repo-root `./install.sh` is a *different* script, but it now defaults to
  preview too.)
- **Backup before every change** (`<file>.bak-<timestamp>`).
- **Never clobbers.** A server key that already exists and wasn't added by this
  installer is left untouched.
- **Idempotent.** A second run computes identical content and writes nothing.
- **Won't corrupt commented configs.** If a file isn't strict JSON (e.g. a Zed
  `settings.json` with comments), it is not rewritten — the exact snippet to paste
  is printed instead.
- **Preserves your formatting.** The file's existing indent (2/4-space or tab)
  and newline convention are detected and reused, so the installer only ever
  touches the lines it actually changes.
- **Surgical uninstall.** Removes only the entries recorded in the installer's
  state file (`<config-home>/xylem/installer-state.json`), and honours `--only`
  so you can detach one agent without touching the rest.

### Secret handling

The remote Worker URL is itself the credential (the Workers authenticate on the
`…/mcp/<token>` path alone), which makes the config files and their backups
secret-bearing. Therefore:

- Every config and backup the installer writes is `chmod 0600`. *(This is a
  no-op on Windows, where `os.chmod` only toggles the read-only bit — Windows
  users should rely on their profile-directory ACLs.)*
- **All diff and warning output is redacted** to `.../mcp/<redacted>`. Dry-run
  output is safe to paste into an issue; the real token still goes to disk.
- **Backups are not hoarded.** Each `--apply` prunes this installer's own
  `.bak-*` files once they are older than 30 days (always keeping the 3 most
  recent), and `uninstall --apply` **deletes them outright** — an uninstall that
  left a backup behind would leave a working credential behind.
- `*.bak-*` is **not** currently covered by the repo `.gitignore` (only
  `*.xylem-backup` is). These files are normally written outside the repo, but
  if you point the installer at an in-repo config, add `*.bak-*` first.

Adding a new agent is a new `Adapter(...)` entry in `xylem_install.py`; adding or
re-transporting a server is a one-entry edit in `servers.json`.

## Observability dashboard

`xylem_dashboard.py` bakes a single self-contained `dashboard.html` from your own
org's activity — coordination traffic, decision memory per project, and (if you
use cambium) the knowledge funnel. It reads `dashboard.template.html` and injects
your data; only names, counts, and short summaries land in the HTML — never a
token.

Two routes:

```sh
# LOCAL (default, no token): reads the agentsync branch of AGENTSYNC_REPO via git
# and scans each project clone's .context/ store. Sees every project on this machine.
python3 install/xylem_dashboard.py --output ~/xylem-dashboard.html
python3 install/xylem_dashboard.py --projects /path/to/repoA /path/to/repoB   # add more clones
python3 install/xylem_dashboard.py --dry-run                                   # summarize, write nothing

# REMOTE (opt-in): reads the Cloudflare Workers via the *_REMOTE_URL values in your
# gitignored xylem.config.json — the full central mirror, incl. work from other
# machines/mobile. The token stays in local config.
python3 install/xylem_dashboard.py --remote --output ~/xylem-dashboard.html
```

Which projects the **local** route includes: it auto-uses `CONTEXT_KEEPER_PROJECT`,
`CAMBIUM_REPO`, and `AGENTSYNC_REPO`, plus anything in `XYLEM_DASHBOARD_PROJECTS`
(or every `.context/`-bearing clone under `XYLEM_PROJECTS_ROOT`). The **remote**
route enumerates the whole org automatically via context-keeper-remote's
`list_projects` (which reports exact, case-sensitive project names — no guessing).

Every collector fails soft: a missing clone, absent branch, or unreachable Worker
degrades that panel to empty with a warning; the dashboard still renders. Re-run it
(or wire it into a hook / cron) to refresh. Nothing secret is ever written to the
output.

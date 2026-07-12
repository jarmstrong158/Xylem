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
   ./install.sh                       # macOS/Linux  (dry-run install)
   .\install.ps1                      # Windows
   ```

3. Apply it:
   ```sh
   ./install.sh install --apply
   .\install.ps1 install --apply
   ```

Other commands:
```sh
./install.sh list-agents             # which agents are detected here
./install.sh install --only cursor,vscode
./install.sh uninstall               # dry-run removal
./install.sh uninstall --apply       # remove only what Xylem added
```

## What it touches

Detected agents and their MCP config files:

| Agent | Config file | Remote (http) support |
|---|---|---|
| Claude Code | `~/.claude.json` | yes (`type: http`) |
| Cursor | `~/.cursor/mcp.json` | yes (`url`) |
| Windsurf | `~/.codeium/windsurf/mcp_config.json` | yes (`serverUrl`) |
| VS Code | `…/Code/User/mcp.json` | yes (`type: http`) |
| Claude Desktop | `…/Claude/claude_desktop_config.json` | no — add remote connectors via its UI; stdio servers are merged |
| Zed | `~/.config/zed/settings.json` | no — stdio servers only |
| GitHub Copilot CLI | `~/.copilot/mcp-config.json` (or `$COPILOT_HOME`) | yes (`type: http`) |

## Safety

- **Dry-run is the default.** Nothing is written without `--apply`.
- **Backup before every change** (`<file>.bak-<timestamp>`).
- **Never clobbers.** A server key that already exists and wasn't added by this
  installer is left untouched.
- **Idempotent.** A second run computes identical content and writes nothing.
- **Won't corrupt commented configs.** If a file isn't strict JSON (e.g. a Zed
  `settings.json` with comments), it is not rewritten — the exact snippet to paste
  is printed instead.
- **Surgical uninstall.** Removes only the entries recorded in the installer's
  state file (`<config-home>/xylem/installer-state.json`).

Adding a new agent is a new `Adapter(...)` entry in `xylem_install.py`; adding or
re-transporting a server is a one-entry edit in `servers.json`.

# Manifest design

`manifest.json` is **data, not code** — the whole install is a declaration the installer
interprets, so changing what ships never means touching install logic.

- **One entry per server** declares its `name`, `transport` (`stdio`/`http`), launch
  command/args or the env key its URL comes from, and any hook `artifacts` it owns.
  Adding a server, or flipping one between local and remote, is an edit here and
  nowhere else.
- **`available` gates each server** — flip it to `false` to ship a placeholder the
  installer skips, no code change.
- **`version` is the deployed-stamp source of truth** — the one integer the fence stamp
  and the version check both read.
- **Placeholders, not paths.** `$PYTHON`, `$XYLEM_ROOT` (this repo), `$XYLEM_PARENT`
  (its parent, where the sibling server repos live), `$PROJECT_DIR`, and `$AGENT_ID`
  are resolved at install time, so the manifest is machine-agnostic.
- **No URLs, no secrets, ever.** Remote servers reference an env key (`url_env_key`);
  the value is read from the environment at install time and never written to the file.
  A test asserts the manifest contains no literal `http(s)://` URL.

## Why `$PYTHON` and not `python3`

The manifest used to hardcode `python3` as the launch command for all three stdio
servers. That is wrong on a common Windows setup: `python3` frequently resolves to the
Microsoft Store shim while the real interpreter — the one with `mcp` installed — is
`python`. The result was three MCP servers registered into a config that could never
start, with no diagnostic.

`$PYTHON` resolves to `sys.executable`: the exact interpreter running the installer.
If you could run the installer, the servers can run. This also means installing from a
virtualenv registers that virtualenv's interpreter, which is almost always what you
want.

## Generated from the manifest

`plugin/.mcp.json` is **generated** from this file by `scripts/render_discipline.py`,
not hand-maintained. It used to be a hand-copy, and it drifted on two things that both
shipped:

- the server name (`agent-sync-remote` vs `agentsync-remote`), which silently broke
  every `mcp__agentsync-remote__*` tool reference in the habit prose on a plugin
  install; and
- the auth scheme — the copy sent an `Authorization: Bearer` header that these Workers
  never read, so the documented `*_TOKEN` env vars did nothing at all.

`tests/test_generated_sync.py` fails if the committed file drifts from what the
manifest implies. Run `python scripts/render_discipline.py --write` after editing the
manifest.

## Auth

The remote Workers authenticate on the **URL path only** (`.../mcp/<token>`). They
never read an `Authorization` header — claude.ai connectors don't reliably send bearer
headers, so path-token auth is what actually works across both transports.

The practical consequence: **the whole URL is the credential.** Treat it like a
password. Rotating the token invalidates every prior URL. `tests/test_manifest.py`
asserts no http server declares an auth header, so this can't quietly regress.

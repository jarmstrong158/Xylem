# Degraded mode

Every moving part fails soft, because a coordination stack that halts your session when
a piece is missing is a worse deal than no stack at all.

- **Missing remote transport?** An http server whose URL env var is unset is skipped
  with a warning, and any previously-written entry for it is removed rather than left
  behind with a stale token. The local stdio servers install and work regardless. The
  suite is local-first — the Workers are an addition, never a dependency.
- **`available: false` servers** (e.g. a future analytics server that isn't built yet)
  are quietly skipped by the installer.
- **Offline, no git, or no xylem clone?** The version check finds nothing to compare and
  exits silently. It never blocks a session, and any uncaught exception is swallowed to
  a clean exit 0.
- **Unparseable `settings.json`?** If your settings file isn't strict JSON (comments,
  trailing commas), the installer warns, prints the block for you to paste, and skips
  writing that file — instead of aborting the run with a traceback.
- **No semantic-search backend?** context-keeper's optional embedding retrieval (Ollama
  or an OpenAI-compatible endpoint) is strictly additive and falls back to lexical
  search if unreachable.
- **Uncertain recall?** cambium abstains below a relevance threshold — `recall()`
  returns `no_confident_match` rather than confabulating.

## What is *not* degraded-mode-safe

Naming the gaps is part of the deal:

- **The plugin install path has no local memory server.** It ships the two remote
  Workers only. If you install the plugin without deploying the Workers, the discipline
  primer will tell the agent to recall from a memory that isn't there. See the
  comparison table in [plugin/README.md](../plugin/README.md).
- **cambium's `distill()` can lose work.** Claims completed *and* re-claimed between
  two distill runs are not captured — a deliberate trade of completeness for
  simplicity.
- **agentsync is textual, not semantic.** It tells you two files won't merge. It does
  not tell you an API contract broke.

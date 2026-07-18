# Xylem

**Agents that remember, coordinate, and compound.**

Xylem gives your AI coding sessions three things they normally lack: a memory that
persists across sessions, coordination across parallel agents and devices, and a
knowledge loop that compounds what each session learns. Session hooks make the discipline
automatic — recall before acting, claim before editing, record as you decide, and distill
at session end.

## The stack

| Layer | Component | What it does |
| --- | --- | --- |
| **Memory** | [context-keeper](https://github.com/jarmstrong158/context-keeper) | Persistent, per-project decision memory: decisions, constraints, and pipelines that survive across sessions. Available locally and as a remote HTTP MCP server. |
| **Coordination** | [agentsync](https://github.com/jarmstrong158/agentsync) | A claim/release board with overlap detection and a human-in-the-loop mailbox, so parallel agents (and your phone) don't collide. Available locally and as a remote HTTP MCP server. |
| **Knowledge** | [cambium](https://github.com/jarmstrong158/cambium) | Distills session outcomes into knowledge and promotes it team -> org through recall, generalization, and endorsement gates. |

Memory and coordination run over MCP and are configured below. Knowledge runs through the
`cambium` CLI and is optional — the plugin is fully usable without it; only the knowledge
skills (`distill-session`, `recall-knowledge`, `promote-to-org`) and the automatic
session-end distillation need it.

## Setup

### 1. Environment variables

The MCP servers read four environment variables. Set them in your shell (or your Claude
Code environment) before starting a session:

| Variable | What it is |
| --- | --- |
| `CONTEXT_KEEPER_REMOTE_URL` | HTTPS URL of your context-keeper-remote MCP endpoint |
| `CONTEXT_KEEPER_REMOTE_TOKEN` | Bearer token for context-keeper-remote |
| `AGENTSYNC_REMOTE_URL` | HTTPS URL of your agent-sync-remote MCP endpoint |
| `AGENTSYNC_REMOTE_TOKEN` | Bearer token for agent-sync-remote |

The token is sent as an `Authorization: Bearer ...` header and is never written to disk by
the plugin. Deploy your own context-keeper-remote and agentsync-remote workers (see their
repos) to get the URLs and tokens.

### 2. cambium (optional, for the knowledge skills)

Install [cambium](https://github.com/jarmstrong158/cambium) and put it on your `PATH`.
Without it, the session-end distillation hook prints a one-line skip note and exits
cleanly, and the three knowledge skills tell you it is missing instead of failing.

### 3. Hooks

Two hooks make the discipline automatic:

- **SessionStart** prints the Xylem discipline primer (`artifacts/discipline.md`).
- **SessionEnd** runs `scripts/distill.sh`, which distills the session into your local
  knowledge store (and no-ops gracefully if cambium is absent). It always exits 0 and
  never fails your session.

## Skills

Each skill triggers on natural phrasing — you don't call them by name.

### record-decision
Capture an engineering or design decision so it persists.
> "Record this decision: we're using the Bearer header for both workers because the edge
> strips custom headers." -> writes problem / why_chosen / alternatives / tradeoffs / tags
> into context-keeper, records any standing rule as a constraint, and mirrors to
> DECISIONS.md if the repo keeps one.

### recall-context
Load what's already been decided in this project before you act.
> "What did we decide about the token header?" / "What are the constraints here?" ->
> orients with `get_project_summary`, reads active constraints, cites ids, and flags if a
> planned change would violate a decision.

### claim-work
Stake a claim before editing; release with an outcome when done.
> "Start work on the dashboard generator, touches install/." -> survey ->
> check_conflicts -> claim(task, touches, branch). Later: "release that" -> release with a
> note covering what shipped, tests, the PR, and the next action.

### check-conflicts
See the board without changing it.
> "Who's working on what?" / "Any conflicts on docs/?" -> read-only survey +
> check_conflicts + mailbox; reports active peers, overlaps, and pending notes.

### distill-session
Harvest what this session learned into the local knowledge store.
> "Distill this session." -> runs `cambium distill`; durable items cross to team scope via
> the recall gate; says so if cambium is missing.

### recall-knowledge
Ask the federated org brain a cross-project question.
> "What does the org know about token headers at the edge?" -> `cambium recall "..."`
> across projects; surfaces scope and `endorsed_as`. (Use recall-context for
> project-local questions.)

### promote-to-org
Elevate a team learning into universal practice.
> "Promote this to the org." -> `cambium review_promotions`, then promote one at a time
> through the generalization and endorsement gates; PRs are serialized.

## The loop

Recall before acting. Claim before editing. Record as you decide. Release with an outcome.
Distill at session end; promote deliberately. The next session — yours or a teammate's —
starts smarter than this one did.

## License

MIT

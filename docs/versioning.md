# Version signals

The habit block gets sharper over time, and an install that silently goes stale is
worse than no signal. So the version travels with the block.

- **`manifest.json` `"version"` (an integer) is the single source of truth.** The
  installer stamps the begin fence as `<!-- XYLEM:BEGIN vN -->` from that value. The
  current template is **v4** (v3 wired the `SessionEnd` distill hook into the habit
  layer; v4 moved the block to a generated artifact and resolved the interpreter at
  install time). A legacy unstamped block counts as v1.
- **A `SessionStart` hook (`version_check.py`) compares** the version stamped into your
  installed block against the template. If several `CLAUDE.md` copies carry a block —
  say a global one and one committed into a repo — the lowest wins, so any stale copy
  is caught.
- **On a match it prints nothing.** A current machine spends zero model tokens on the
  check. When you're behind, it prints one ASCII line pointing at the fix.
- **`xylem update` is that fix.** `installer.py update` git-pulls this repo and
  re-applies the block with the current stamp, reporting `old → new` and which files
  changed. It is the *only* path that rewrites a block — the check only ever detects.

## The upstream fetch is opt-in

Earlier versions ran `git fetch origin` on every `SessionStart` so the nudge fired the
moment a new version was published upstream, before you'd pulled. That cost up to ten
seconds of latency on every single session, on a slow or captive network, for a
cosmetic notice.

The fetch is now rate-limited and off by default:

- Set `XYLEM_FETCH_ON_CHECK=1` to re-enable the eager upstream check.
- When enabled, it fetches at most once every 24 hours, with a 3-second timeout.
- Either way the comparison against your local clone still happens on every session,
  so a stale block is still caught — you just learn about brand-new upstream versions
  on your next pull instead of instantly.

## Trust boundary

`xylem update` git-pulls this repo and the pulled `artifacts/*.py` then execute as
`SessionStart` / `SessionEnd` hooks on every subsequent session. That is the intended
design, but it is worth naming plainly: **`xylem update` is a code-execution trust
boundary.** The pull is `--ff-only`, which is what stands between a compromised or
rewritten remote and every future session on your machine. If you don't control the
remote, read the diff before running it.

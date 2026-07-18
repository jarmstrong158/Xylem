#!/usr/bin/env bash
# Xylem SessionEnd hook: distill this session's decisions into the LOCAL cambium
# knowledge store. This must NEVER fail the session, so it always exits 0.
#
# - If `cambium` is not on PATH, print a one-line note and exit 0 (the plugin is
#   fully usable without cambium; only the knowledge skills need it).
# - Otherwise cd to the git root (so distillation targets the right project) and
#   run `cambium distill`, capturing the session outcome into local scope. Org
#   promotion stays deliberate (see the promote-to-org skill) and never happens
#   automatically here.
set -u

if ! command -v cambium >/dev/null 2>&1; then
  echo "xylem: cambium not found on PATH - skipping session distillation (install cambium to enable the knowledge loop)."
  exit 0
fi

# Resolve the git root; fall back to the current directory if we are not in a repo.
root="$(git rev-parse --show-toplevel 2>/dev/null || true)"
if [ -n "${root}" ]; then
  cd "${root}" || true
fi

if cambium distill 2>&1; then
  echo "xylem: session distilled into the local knowledge store."
else
  echo "xylem: cambium distill did not complete cleanly - skipping (session left untouched)."
fi

exit 0

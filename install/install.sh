#!/usr/bin/env sh
# Thin bootstrap for macOS/Linux: find a Python 3 interpreter and hand off to the
# installer. All real logic lives in xylem_install.py — keep this dumb.
#
# THIS SCRIPT (install/install.sh) DRY-RUNS BY DEFAULT.
# Nothing is written unless you pass --apply.
#
# WARNING: the repo-root ./install.sh is a DIFFERENT script with the OPPOSITE
# default — it APPLIES immediately. Same filename, same repo. Check which path
# you are actually invoking.
#
#   install/install.sh                  # DRY-RUN install: show what would change
#   install/install.sh install --apply  # actually write the changes
#   install/install.sh uninstall        # DRY-RUN removal
#   install/install.sh uninstall --apply # actually remove
#   install/install.sh list-agents      # what's detected here

set -eu

DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

PY=""
for cand in python3 python; do
  if command -v "$cand" >/dev/null 2>&1; then
    if "$cand" -c 'import sys; sys.exit(0 if sys.version_info[0] >= 3 else 1)' >/dev/null 2>&1; then
      PY="$cand"
      break
    fi
  fi
done

if [ -z "$PY" ]; then
  echo "error: Python 3 is required but was not found on PATH." >&2
  echo "Install Python 3 (https://www.python.org/downloads/) and re-run." >&2
  exit 1
fi

# Loud, unmistakable mode banner — this script DRY-RUNS by default.
MODE="DRY-RUN (no files written; add --apply to write)"
for a in "$@"; do
  if [ "$a" = "--apply" ]; then MODE="APPLY (files WILL be written)"; fi
done
echo "=== Xylem installer: $MODE ==="

if [ "$#" -eq 0 ]; then
  exec "$PY" "$DIR/xylem_install.py" install
fi
exec "$PY" "$DIR/xylem_install.py" "$@"

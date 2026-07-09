#!/usr/bin/env sh
# Thin bootstrap for macOS/Linux: find a Python 3 interpreter and hand off to the
# installer. All real logic lives in xylem_install.py — keep this dumb.
#
#   ./install.sh                 # dry-run: show what would change
#   ./install.sh install --apply  # write the changes
#   ./install.sh uninstall        # dry-run removal
#   ./install.sh list-agents      # what's detected here
#
# With no arguments it defaults to a dry-run `install` so a curious run is safe.

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

if [ "$#" -eq 0 ]; then
  exec "$PY" "$DIR/xylem_install.py" install
fi
exec "$PY" "$DIR/xylem_install.py" "$@"

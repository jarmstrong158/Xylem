#!/usr/bin/env bash
# Xylem bootstrap for macOS / Linux.
# Locates a Python 3.8+ interpreter and hands off to installer.py.
# All real logic lives in installer.py; this only finds Python and forwards args.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

find_python() {
  for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then
      if "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)' >/dev/null 2>&1; then
        echo "$candidate"
        return 0
      fi
    fi
  done
  return 1
}

PY="$(find_python || true)"
if [ -z "${PY:-}" ]; then
  echo "xylem: could not find Python 3.8+ on PATH (looked for python3, python)." >&2
  echo "xylem: install Python 3.8 or newer and re-run." >&2
  exit 1
fi

exec "$PY" "$SCRIPT_DIR/installer.py" "$@"

#!/usr/bin/env python3
"""Xylem SessionStart hook: print the discipline primer.

Replaces `cat ${CLAUDE_PLUGIN_ROOT}/artifacts/discipline.md`. Two reasons:
`cat` does not exist on a stock Windows shell, and the unquoted expansion broke
for anyone whose plugin root contains a space (e.g. C:\\Users\\Jon Armstrong\\).

The primer is ASCII-only by construction -- scripts/render_discipline.py refuses
to emit anything else, because this text goes straight to a console that may be
cp1252. Never fails a session: every path exits 0.
"""

import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PRIMER = os.path.join(os.path.dirname(HERE), "artifacts", "discipline.md")


def main():
    try:
        with io.open(PRIMER, encoding="utf-8") as fh:
            text = fh.read()
    except Exception:
        # No primer, no problem -- the session continues without it.
        return 0

    # Encode defensively: a cp1252 console must not raise on write.
    try:
        sys.stdout.write(text)
    except UnicodeEncodeError:
        sys.stdout.write(text.encode("ascii", "replace").decode("ascii"))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)

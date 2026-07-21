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

# The two Worker URLs the plugin's .mcp.json substitutes. The plugin ships only
# the *remote* servers, so with neither URL set its memory/coordination tools
# resolve to an empty URL and silently never connect. This is the plugin path's
# equivalent of the installer's "servers registered but not present" trap.
REMOTE_URL_KEYS = ("CONTEXT_KEEPER_REMOTE_URL", "AGENTSYNC_REMOTE_URL")


def backend_status(env=None):
    """One-line ASCII notice when the plugin has no reachable backend, else "".

    Quiet the moment *either* Worker URL is set: a half-configured user is
    mid-setup and knows it, and a local-installer user (whose stdio servers are
    a different backend entirely) should not be nagged. The line only fires when
    nothing at all is wired, which is exactly the silent-no-op we are fixing.
    """
    env = os.environ if env is None else env
    if any(env.get(key) for key in REMOTE_URL_KEYS):
        return ""
    return (
        "\n[xylem] No remote backend configured "
        "(CONTEXT_KEEPER_REMOTE_URL / AGENTSYNC_REMOTE_URL unset). This plugin's "
        "memory + coordination tools stay inert until you deploy the Workers "
        "(see the Xylem README) or run the local installer.\n")


def main():
    try:
        with io.open(PRIMER, encoding="utf-8") as fh:
            text = fh.read()
    except Exception:
        # No primer file, but the backend notice below is still worth printing.
        text = ""

    text += backend_status()

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

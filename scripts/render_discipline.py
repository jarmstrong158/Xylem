#!/usr/bin/env python3
"""Render every generated artifact from its single source of truth.

Three copies of the habit-layer prose used to be maintained by hand
(artifacts/claude_md_block.md, artifacts/xylem_discipline.md,
plugin/artifacts/discipline.md) and they drifted: the plugin copy silently
dropped the update_status cadence, the mailbox rule, and the
"done means pushed" rule. Likewise plugin/.mcp.json was a hand-copy of
manifest.json's http servers and drifted on both the server NAME and the auth
scheme.

So: generate, don't copy. Sources are artifacts/discipline.source.json and
manifest.json. tests/test_generated_sync.py fails if any output is stale, so a
PR that edits one and forgets the others cannot merge.

    python scripts/render_discipline.py            # check only, exit 1 if stale
    python scripts/render_discipline.py --write    # regenerate in place

Stdlib only, like the rest of the suite.
"""

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

SOURCE = os.path.join(ROOT, "artifacts", "discipline.source.json")
MANIFEST = os.path.join(ROOT, "manifest.json")

OUT_BLOCK = os.path.join(ROOT, "artifacts", "claude_md_block.md")
OUT_COMMAND = os.path.join(ROOT, "artifacts", "xylem_discipline.md")
OUT_PLUGIN = os.path.join(ROOT, "plugin", "artifacts", "discipline.md")
OUT_MCP = os.path.join(ROOT, "plugin", ".mcp.json")

GENERATED_NOTE = (
    "GENERATED FILE -- do not edit by hand. "
    "Source: artifacts/discipline.source.json. "
    "Regenerate: python scripts/render_discipline.py --write"
)


def load_json(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


# --------------------------------------------------------------------------
# renderers
# --------------------------------------------------------------------------


def render_block(src, version):
    """The fenced CLAUDE.md block. Bullet form, version-stamped."""
    lines = ["<!-- XYLEM:BEGIN v%d -->" % version]
    lines.append("<!-- %s -->" % GENERATED_NOTE)
    lines.append("## Xylem discipline")
    lines.append("")
    lines.append(src["preamble_block"])
    lines.append("")
    for rule in src["rules"]:
        lines.append("- **%s**, %s" % (rule["lead"], rule["body"]))
    lines.append("")
    lines.append(src["block_footer"])
    lines.append("<!-- XYLEM:END -->")
    return "\n".join(lines) + "\n"


def render_command(src):
    """The /xylem-discipline slash command. Numbered sections, takes $ARGUMENTS."""
    fm = src["command_frontmatter"]
    lines = ["---"]
    lines.append("description: %s" % fm["description"])
    lines.append("argument-hint: %s" % fm["argument-hint"])
    lines.append("---")
    lines.append("")
    lines.append("<!-- %s -->" % GENERATED_NOTE)
    lines.append("")
    lines.append("# Xylem discipline")
    lines.append("")
    lines.append(src["preamble_command"])
    lines.append("")
    lines.append("```")
    lines.append(src["loop"])
    lines.append("```")
    lines.append("")
    lines.append(src["command_args_note"])
    lines.append("")
    for i, rule in enumerate(src["rules"], start=1):
        lines.append("## %d. %s" % (i, rule["title"]))
        lines.append("%s, %s" % (rule["lead"], rule["body"]))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_plugin(src):
    """The plugin SessionStart primer.

    Emitted by `cat` straight into a terminal, so it must be ASCII-only:
    a Windows console on cp1252 mangles anything else.
    """
    # No generated-file banner here, unlike the other two outputs: this file is
    # printed verbatim into the user's session at SessionStart, and build-system
    # chatter in that slot is noise the reader can't act on.
    lines = ["# Xylem discipline"]
    lines.append("")
    lines.append(src["preamble_plugin"])
    lines.append("")
    lines.append("## The loop")
    lines.append("")
    for i, rule in enumerate(src["rules"], start=1):
        lines.append("%d. %s -- %s, %s" % (i, rule["title"].upper(), rule["lead"], rule["body"]))
        lines.append("")
    lines.append(src["plugin_footer"])
    text = "\n".join(lines).rstrip() + "\n"

    non_ascii = sorted({ch for ch in text if ord(ch) > 127})
    if non_ascii:
        raise SystemExit(
            "render_discipline: plugin primer must be ASCII-only (it is cat'd to a "
            "cp1252 console); offending characters: %r" % non_ascii
        )
    return text


def render_mcp(manifest):
    """plugin/.mcp.json, derived from manifest.json's http servers.

    Two bugs this kills permanently:
      * the plugin registered `agent-sync-remote` while every other file in the
        repo (and all the habit prose) says `agentsync-remote`, so every tool
        reference broke on a plugin install;
      * the plugin sent `Authorization: Bearer`, but these Workers authenticate
        on the URL path only -- manifest.json says so, docs/design-principles.md
        says so, and tests/test_manifest.py asserts it. The header was inert and
        the documented *_TOKEN env vars did nothing.
    """
    servers = {}
    for server in manifest["servers"]:
        if server.get("transport") != "http" or not server.get("available", True):
            continue
        servers[server["name"]] = {
            "type": "http",
            "url": "${%s}" % server["url_env_key"],
        }
    payload = {
        "$comment": GENERATED_NOTE.replace(
            "artifacts/discipline.source.json", "manifest.json"
        ),
        "mcpServers": servers,
    }
    return json.dumps(payload, indent=2) + "\n"


# --------------------------------------------------------------------------


def outputs():
    src = load_json(SOURCE)
    manifest = load_json(MANIFEST)
    version = manifest["version"]
    return [
        (OUT_BLOCK, render_block(src, version)),
        (OUT_COMMAND, render_command(src)),
        (OUT_PLUGIN, render_plugin(src)),
        (OUT_MCP, render_mcp(manifest)),
    ]


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--write",
        action="store_true",
        help="regenerate the outputs in place (default: check only)",
    )
    args = ap.parse_args()

    stale = []
    for path, want in outputs():
        current = None
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8", newline="") as fh:
                current = fh.read()
        if current == want:
            continue
        stale.append(path)
        if args.write:
            with open(path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(want)

    rel = [os.path.relpath(p, ROOT).replace(os.sep, "/") for p in stale]
    if args.write:
        print("regenerated: %s" % (", ".join(rel) if rel else "nothing (already current)"))
        return 0

    if stale:
        sys.stderr.write(
            "render_discipline: these generated files are stale:\n"
            + "".join("  %s\n" % p for p in rel)
            + "fix: python scripts/render_discipline.py --write\n"
        )
        return 1
    print("generated files are current")
    return 0


if __name__ == "__main__":
    sys.exit(main())

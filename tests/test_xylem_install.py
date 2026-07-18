"""Unit tests for install/xylem_install.py. Stdlib unittest only, no deps.

Covers the regressions that shipped unnoticed because this module had zero
tests: the placeholder resolver's optional-miss path, state-path normalisation,
the JSON top-level type guard, formatting preservation, secret redaction, and
the per-adapter entry shapes.
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "install"))

import xylem_install as xi  # noqa: E402


def getter(mapping):
    return lambda key: mapping.get(key)


# --------------------------------------------------------------------------- #
# placeholder resolution
# --------------------------------------------------------------------------- #
class ResolveStrTest(unittest.TestCase):
    def test_resolves_known_placeholder(self):
        s, miss = xi.resolve_str("${FOO}/bar", getter({"FOO": "/opt"}))
        self.assertEqual(s, "/opt/bar")
        self.assertEqual(miss, set())

    def test_reports_missing_placeholder(self):
        s, miss = xi.resolve_str("${FOO}/bar", getter({}))
        self.assertEqual(miss, {"FOO"})

    def test_empty_value_counts_as_missing(self):
        _, miss = xi.resolve_str("${FOO}", getter({"FOO": ""}))
        self.assertEqual(miss, {"FOO"})

    def test_multiple_placeholders(self):
        s, miss = xi.resolve_str("${A}-${B}", getter({"A": "1"}))
        self.assertEqual(miss, {"B"})
        self.assertIn("1-", s)

    def test_no_placeholder_is_passthrough(self):
        s, miss = xi.resolve_str("plain", getter({}))
        self.assertEqual((s, miss), ("plain", set()))


class BuildServerPlaceholderTest(unittest.TestCase):
    """Regression: a missing OPTIONAL placeholder must never reach the config."""

    def test_missing_optional_arg_is_dropped_not_literal(self):
        decl = {
            "name": "t", "transport": "stdio", "command": "/bin/true",
            "args": ["--flag", "${NOPE}"], "required": [],
        }
        srv, missing = xi.build_server(decl, getter({}))
        self.assertEqual(missing, [])
        self.assertNotIn("${NOPE}", srv["args"])
        self.assertEqual(srv["args"], ["--flag"])

    def test_missing_required_arg_blocks_server(self):
        decl = {
            "name": "t", "transport": "stdio", "command": "/bin/true",
            "args": ["${NEEDED}"], "required": ["NEEDED"],
        }
        srv, missing = xi.build_server(decl, getter({}))
        self.assertIsNone(srv)
        self.assertEqual(missing, ["NEEDED"])

    def test_resolved_arg_is_kept(self):
        decl = {
            "name": "t", "transport": "stdio", "command": "/bin/true",
            "args": ["${P}"], "required": ["P"],
        }
        srv, _ = xi.build_server(decl, getter({"P": "/srv.py"}))
        self.assertEqual(srv["args"], ["/srv.py"])

    def test_missing_optional_env_key_is_dropped(self):
        decl = {
            "name": "t", "transport": "stdio", "command": "/bin/true",
            "env": {"A": "${SET}", "B": "${UNSET}"}, "required": [],
        }
        srv, _ = xi.build_server(decl, getter({"SET": "v"}))
        self.assertEqual(srv["env"], {"A": "v"})

    def test_http_server_requires_url(self):
        decl = {"name": "r", "transport": "http", "url": "${U}", "required": ["U"]}
        srv, missing = xi.build_server(decl, getter({}))
        self.assertIsNone(srv)
        self.assertEqual(missing, ["U"])
        srv, _ = xi.build_server(decl, getter({"U": "https://x/mcp/tok"}))
        self.assertEqual(srv["transport"], "http")
        self.assertEqual(srv["url"], "https://x/mcp/tok")


# --------------------------------------------------------------------------- #
# state ownership / path normalisation
# --------------------------------------------------------------------------- #
class StateOwnsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.cfg = self.tmp / "mcp.json"
        self.cfg.write_text("{}", encoding="utf-8")
        self.adapter = xi.build_adapters()[0]

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_add_then_owns(self):
        state = {"version": 1, "entries": []}
        xi.state_add(state, self.adapter, self.cfg, "context-keeper")
        self.assertTrue(xi.state_owns(state, self.cfg, "context-keeper"))
        self.assertFalse(xi.state_owns(state, self.cfg, "other"))

    def test_add_is_idempotent(self):
        state = {"version": 1, "entries": []}
        xi.state_add(state, self.adapter, self.cfg, "s")
        xi.state_add(state, self.adapter, self.cfg, "s")
        self.assertEqual(len(state["entries"]), 1)

    def test_owns_survives_case_difference(self):
        """Regression: raw string compare lost ownership on drive-letter case."""
        state = {"version": 1, "entries": []}
        xi.state_add(state, self.adapter, self.cfg, "s")
        weird = Path(str(self.cfg).upper() if xi.IS_WINDOWS else str(self.cfg))
        if xi.IS_WINDOWS:
            self.assertNotEqual(str(weird), str(self.cfg))
        self.assertTrue(xi.state_owns(state, weird, "s"))

    def test_owns_survives_redundant_path_segments(self):
        state = {"version": 1, "entries": []}
        xi.state_add(state, self.adapter, self.cfg, "s")
        noisy = Path(os.path.join(str(self.tmp), ".", "sub", "..", "mcp.json"))
        self.assertTrue(xi.state_owns(state, noisy, "s"))

    def test_stored_path_is_normalised(self):
        state = {"version": 1, "entries": []}
        xi.state_add(state, self.adapter, self.cfg, "s")
        self.assertEqual(state["entries"][0]["path"], xi.norm_path(self.cfg))

    def test_norm_path_of_empty_is_empty(self):
        self.assertEqual(xi.norm_path(""), "")


class StateMigrationTest(unittest.TestCase):
    """load_state must normalise paths written by an older version."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self._orig = xi.state_path
        xi.state_path = lambda: self.tmp / "installer-state.json"

    def tearDown(self):
        xi.state_path = self._orig
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_legacy_unnormalised_entry_is_migrated(self):
        legacy = os.path.join(str(self.tmp), ".", "cfg.json")
        xi.state_path().write_text(
            json.dumps({"version": 1, "entries": [
                {"agent": "cursor", "path": legacy, "container_key": "mcpServers", "server": "s"}
            ]}), encoding="utf-8")
        state = xi.load_state()
        self.assertEqual(state["entries"][0]["path"], xi.norm_path(legacy))
        self.assertTrue(xi.state_owns(state, Path(self.tmp / "cfg.json"), "s"))

    def test_malformed_state_degrades_to_empty(self):
        xi.state_path().write_text("[1,2,3]", encoding="utf-8")
        self.assertEqual(xi.load_state()["entries"], [])

    def test_entries_not_a_list_degrades(self):
        xi.state_path().write_text('{"entries": "nope"}', encoding="utf-8")
        self.assertEqual(xi.load_state()["entries"], [])


# --------------------------------------------------------------------------- #
# JSON reading: top-level type guard
# --------------------------------------------------------------------------- #
class ReadJsonStrictTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, text):
        p = self.tmp / "f.json"
        p.write_bytes(text.encode("utf-8"))
        return p

    def test_missing_file(self):
        self.assertEqual(xi.read_json_strict(self.tmp / "absent.json"), ({}, None))

    def test_empty_file(self):
        self.assertEqual(xi.read_json_strict(self._write("   \n")), ({}, None))

    def test_object_ok(self):
        data, err = xi.read_json_strict(self._write('{"a": 1}'))
        self.assertIsNone(err)
        self.assertEqual(data, {"a": 1})

    def test_toplevel_list_is_an_error_not_a_list(self):
        """Regression: returning [] made data.get(...) raise AttributeError."""
        data, err = xi.read_json_strict(self._write('[1, 2]'))
        self.assertIsNone(data)
        self.assertIn("not an object", err)

    def test_toplevel_scalar_is_an_error(self):
        for raw in ('"str"', "42", "true", "null"):
            data, err = xi.read_json_strict(self._write(raw))
            self.assertIsNone(data, raw)
            self.assertIn("not an object", err)

    def test_invalid_json_reports_line(self):
        data, err = xi.read_json_strict(self._write('{"a": }'))
        self.assertIsNone(data)
        self.assertIn("not strict JSON", err)


# --------------------------------------------------------------------------- #
# formatting preservation
# --------------------------------------------------------------------------- #
class IndentSniffTest(unittest.TestCase):
    def test_detects_four_spaces(self):
        self.assertEqual(xi.sniff_indent('{\n    "a": 1\n}\n'), 4)

    def test_detects_two_spaces(self):
        self.assertEqual(xi.sniff_indent('{\n  "a": 1\n}\n'), 2)

    def test_detects_tabs(self):
        self.assertEqual(xi.sniff_indent('{\n\t"a": 1\n}\n'), "\t")

    def test_defaults_when_flat(self):
        self.assertEqual(xi.sniff_indent("{}"), 2)
        self.assertEqual(xi.sniff_indent(""), 2)


class NewlineSniffTest(unittest.TestCase):
    def test_detects_crlf(self):
        self.assertEqual(xi.sniff_newline('{\r\n  "a": 1\r\n}\r\n'), "\r\n")

    def test_detects_lf(self):
        self.assertEqual(xi.sniff_newline('{\n  "a": 1\n}\n'), "\n")

    def test_defaults_for_empty(self):
        self.assertEqual(xi.sniff_newline(""), "\n")


class DumpsLikeTest(unittest.TestCase):
    def test_preserves_four_space_indent(self):
        old = '{\n    "x": {\n        "y": 1\n    }\n}\n'
        new = xi.dumps_like(json.loads(old), old)
        self.assertIn('\n    "x"', new)
        self.assertNotIn('\n  "x"', new)

    def test_roundtrip_is_byte_identical_for_four_space_file(self):
        """The real bug: a 4-space config was rewritten end to end every run."""
        old = '{\n    "mcpServers": {\n        "a": {\n            "command": "x"\n        }\n    }\n}\n'
        self.assertEqual(xi.dumps_like(json.loads(old), old), old)

    def test_roundtrip_is_byte_identical_for_crlf_file(self):
        old = '{\r\n  "a": 1\r\n}\r\n'
        self.assertEqual(xi.dumps_like(json.loads(old), old), old)

    def test_preserves_tab_indent(self):
        old = '{\n\t"a": 1\n}\n'
        self.assertEqual(xi.dumps_like(json.loads(old), old), old)

    def test_default_is_two_space_lf(self):
        self.assertEqual(xi.dumps_like({"a": 1}, ""), '{\n  "a": 1\n}\n')


class WriteWithBackupTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.p = self.tmp / "cfg.json"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_dry_run_writes_nothing(self):
        self.p.write_bytes(b'{"a": 1}\n')
        status, _ = xi.write_with_backup(self.p, '{"a": 2}\n', apply=False)
        self.assertEqual(status, "updated")
        self.assertEqual(self.p.read_bytes(), b'{"a": 1}\n')

    def test_identical_content_is_unchanged(self):
        self.p.write_bytes(b'{"a": 1}\n')
        status, _ = xi.write_with_backup(self.p, '{"a": 1}\n', apply=True)
        self.assertEqual(status, "unchanged")
        self.assertEqual(xi.xylem_backups(self.p), [])

    def test_crlf_file_is_not_falsely_dirty(self):
        """read_text() newline translation made a CRLF file look changed forever."""
        text = '{\r\n  "a": 1\r\n}\r\n'
        self.p.write_bytes(text.encode("utf-8"))
        status, _ = xi.write_with_backup(self.p, text, apply=True)
        self.assertEqual(status, "unchanged")

    def test_apply_creates_backup_and_writes(self):
        self.p.write_bytes(b'{"a": 1}\n')
        status, _ = xi.write_with_backup(self.p, '{"a": 2}\n', apply=True)
        self.assertEqual(status, "updated")
        self.assertEqual(self.p.read_bytes(), b'{"a": 2}\n')
        self.assertEqual(len(xi.xylem_backups(self.p)), 1)

    def test_purge_backups_removes_all(self):
        self.p.write_bytes(b'{"a": 1}\n')
        for ts in ("20200101000000", "20200102000000"):
            (self.tmp / ("cfg.json.bak-" + ts)).write_bytes(b"{}")
        self.assertEqual(len(xi.xylem_backups(self.p)), 2)
        self.assertEqual(xi.purge_backups(self.p), 2)
        self.assertEqual(xi.xylem_backups(self.p), [])

    def test_purge_ignores_foreign_files(self):
        self.p.write_bytes(b"{}")
        (self.tmp / "cfg.json.bak-notatimestamp").write_bytes(b"{}")
        (self.tmp / "cfg.json.orig").write_bytes(b"{}")
        xi.purge_backups(self.p)
        self.assertTrue((self.tmp / "cfg.json.bak-notatimestamp").exists())
        self.assertTrue((self.tmp / "cfg.json.orig").exists())

    def test_prune_keeps_recent_and_drops_stale(self):
        self.p.write_bytes(b"{}")
        stale = []
        for i in range(6):
            b = self.tmp / ("cfg.json.bak-2020010%d000000" % i)
            b.write_bytes(b"{}")
            os.utime(str(b), (0, 0))  # epoch => far older than the cutoff
            stale.append(b)
        xi.prune_backups(self.p, max_age_days=1, keep=xi.BACKUP_KEEP)
        self.assertEqual(len(xi.xylem_backups(self.p)), xi.BACKUP_KEEP)


# --------------------------------------------------------------------------- #
# secret redaction
# --------------------------------------------------------------------------- #
class RedactTest(unittest.TestCase):
    def test_masks_worker_path_token(self):
        red = xi.redact("https://xylem.workers.dev/mcp/s3cr3t-token-value")
        self.assertNotIn("s3cr3t", red)
        self.assertIn("<redacted>", red)

    def test_masks_token_inside_json_line(self):
        line = '+      "url": "https://a.workers.dev/mcp/abc123def",\n'
        red = xi.redact(line)
        self.assertNotIn("abc123def", red)
        self.assertTrue(red.endswith("\n"))

    def test_masks_query_string(self):
        red = xi.redact("https://host/endpoint?token=abc123")
        self.assertNotIn("abc123", red)

    def test_leaves_ordinary_text_alone(self):
        for s in ("no urls here", "/home/u/.cursor/mcp.json", ""):
            self.assertEqual(xi.redact(s), s)

    def test_does_not_span_lines(self):
        red = xi.redact("https://h/mcp/tok1\nhttps://h/mcp/tok2\n")
        self.assertNotIn("tok1", red)
        self.assertNotIn("tok2", red)
        self.assertEqual(red.count("\n"), 2)

    def test_show_diff_output_is_redacted(self):
        import io
        old = '{\n  "a": 1\n}\n'
        new = '{\n  "url": "https://h.workers.dev/mcp/SUPERSECRET"\n}\n'
        buf, orig = io.StringIO(), sys.stdout
        sys.stdout = buf
        try:
            xi.show_diff(Path("cfg.json"), old, new)
        finally:
            sys.stdout = orig
        self.assertNotIn("SUPERSECRET", buf.getvalue())
        self.assertIn("<redacted>", buf.getvalue())

    def test_info_and_warn_are_redacted(self):
        import io
        for fn in (xi.info, xi.warn, xi.out):
            buf, orig = io.StringIO(), sys.stdout
            sys.stdout = buf
            try:
                fn('"r": {"url": "https://h/mcp/LEAK"}')
            finally:
                sys.stdout = orig
            self.assertNotIn("LEAK", buf.getvalue())


# --------------------------------------------------------------------------- #
# adapter matrix
# --------------------------------------------------------------------------- #
STDIO = {"name": "ck", "transport": "stdio", "command": "/usr/bin/python3",
         "args": ["/srv/ck.py"], "env": {"K": "v"}}
HTTP = {"name": "ckr", "transport": "http", "url": "https://h.workers.dev/mcp/tok"}


class AdapterMatrixTest(unittest.TestCase):
    def setUp(self):
        self.by_id = {a.id: a for a in xi.build_adapters()}

    def test_all_seven_adapters_present(self):
        self.assertEqual(
            set(self.by_id),
            {"claude-code", "cursor", "windsurf", "vscode",
             "claude-desktop", "zed", "copilot-cli"},
        )

    def test_container_keys(self):
        expected = {
            "claude-code": "mcpServers", "cursor": "mcpServers",
            "windsurf": "mcpServers", "claude-desktop": "mcpServers",
            "vscode": "servers", "zed": "context_servers",
            "copilot-cli": "mcpServers",
        }
        for aid, key in expected.items():
            self.assertEqual(self.by_id[aid].container_key, key, aid)

    def test_every_adapter_renders_stdio(self):
        for aid, ad in self.by_id.items():
            entry = ad.render(STDIO)
            self.assertIsNotNone(entry, aid)
            self.assertEqual(entry["command"], "/usr/bin/python3", aid)
            self.assertEqual(entry["args"], ["/srv/ck.py"], aid)
            self.assertEqual(entry["env"], {"K": "v"}, aid)

    def test_stdio_shapes_per_schema(self):
        self.assertEqual(
            self.by_id["cursor"].render(STDIO),
            {"command": "/usr/bin/python3", "args": ["/srv/ck.py"], "env": {"K": "v"}},
        )
        self.assertEqual(
            self.by_id["vscode"].render(STDIO),
            {"type": "stdio", "command": "/usr/bin/python3",
             "args": ["/srv/ck.py"], "env": {"K": "v"}},
        )
        self.assertEqual(
            self.by_id["copilot-cli"].render(STDIO),
            {"type": "local", "command": "/usr/bin/python3",
             "args": ["/srv/ck.py"], "env": {"K": "v"}, "tools": ["*"]},
        )

    def test_zed_stdio_is_flat_with_no_source_key(self):
        """Zed's ContextServerCommand flattens `path`->"command"; `source` is obsolete."""
        entry = self.by_id["zed"].render(STDIO)
        self.assertNotIn("source", entry)
        self.assertIsInstance(entry["command"], str)
        self.assertEqual(
            entry, {"command": "/usr/bin/python3", "args": ["/srv/ck.py"], "env": {"K": "v"}})

    def test_empty_env_is_omitted(self):
        srv = dict(STDIO, env={})
        self.assertNotIn("env", self.by_id["cursor"].render(srv))

    def test_http_shapes_per_style(self):
        expected = {
            "claude-code": {"type": "http", "url": HTTP["url"]},
            "cursor": {"url": HTTP["url"]},
            "windsurf": {"serverUrl": HTTP["url"]},
            "vscode": {"type": "http", "url": HTTP["url"]},
            "zed": {"url": HTTP["url"]},
            "copilot-cli": {"type": "http", "url": HTTP["url"], "tools": ["*"]},
        }
        for aid, want in expected.items():
            self.assertEqual(self.by_id[aid].render(HTTP), want, aid)

    def test_claude_desktop_cannot_express_http(self):
        self.assertIsNone(self.by_id["claude-desktop"].render(HTTP))

    def test_render_output_is_json_serialisable(self):
        for ad in self.by_id.values():
            for srv in (STDIO, HTTP):
                entry = ad.render(srv)
                if entry is not None:
                    json.dumps(entry)


# --------------------------------------------------------------------------- #
# manifest sanity — every declared server must build given its required keys
# --------------------------------------------------------------------------- #
class ManifestBuildTest(unittest.TestCase):
    def test_every_manifest_server_builds(self):
        manifest, err = xi.read_json_strict(xi.DEFAULT_MANIFEST)
        self.assertIsNone(err)
        self.assertTrue(manifest["servers"])
        for decl in manifest["servers"]:
            supplied = {k: "https://h/mcp/tok" if "URL" in k else "/val/" + k
                        for k in decl.get("required", [])}
            srv, missing = xi.build_server(decl, getter(supplied))
            self.assertIsNotNone(srv, "%s: missing %s" % (decl["name"], missing))
            self.assertNotIn("${", json.dumps(srv), decl["name"])


class UninstallOnlyFilterTest(unittest.TestCase):
    """Regression: --only was registered on `uninstall` but never read, so
    `uninstall --apply --only cursor` wiped Xylem entries from EVERY agent."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.cursor = self.tmp / "cursor.json"
        self.vscode = self.tmp / "vscode.json"
        self.cursor.write_bytes(json.dumps(
            {"mcpServers": {"ck": {"command": "x"}, "keepme": {"command": "y"}}},
            indent=2).encode("utf-8") + b"\n")
        self.vscode.write_bytes(json.dumps(
            {"servers": {"ck": {"type": "stdio"}}}, indent=2).encode("utf-8") + b"\n")

        self._orig = xi.state_path
        xi.state_path = lambda: self.tmp / "state.json"
        xi.state_path().write_text(json.dumps({"version": 1, "entries": [
            {"agent": "cursor", "path": xi.norm_path(self.cursor),
             "container_key": "mcpServers", "server": "ck"},
            {"agent": "vscode", "path": xi.norm_path(self.vscode),
             "container_key": "servers", "server": "ck"},
        ]}), encoding="utf-8")

        self._stdout = sys.stdout
        import io
        sys.stdout = io.StringIO()

    def tearDown(self):
        sys.stdout = self._stdout
        xi.state_path = self._orig
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _args(self, **kw):
        ns = type("A", (), {})()
        ns.apply = kw.get("apply", False)
        ns.only = kw.get("only", None)
        return ns

    def _load(self, p):
        return json.loads(p.read_bytes().decode("utf-8"))

    def test_only_removes_from_named_agent_alone(self):
        xi.cmd_uninstall(self._args(apply=True, only="cursor"))
        self.assertNotIn("ck", self._load(self.cursor)["mcpServers"])
        self.assertIn("keepme", self._load(self.cursor)["mcpServers"])
        # The untargeted agent must be left completely alone.
        self.assertIn("ck", self._load(self.vscode)["servers"])

    def test_only_keeps_untargeted_state_entries(self):
        xi.cmd_uninstall(self._args(apply=True, only="cursor"))
        state = xi.load_state()
        self.assertEqual([e["agent"] for e in state["entries"]], ["vscode"])

    def test_without_only_removes_from_all(self):
        xi.cmd_uninstall(self._args(apply=True))
        self.assertNotIn("ck", self._load(self.cursor)["mcpServers"])
        self.assertNotIn("ck", self._load(self.vscode)["servers"])
        self.assertEqual(xi.load_state()["entries"], [])

    def test_dry_run_with_only_writes_nothing(self):
        before = self.cursor.read_bytes()
        xi.cmd_uninstall(self._args(apply=False, only="cursor"))
        self.assertEqual(self.cursor.read_bytes(), before)
        self.assertEqual(len(xi.load_state()["entries"]), 2)

    def test_unknown_only_id_removes_nothing(self):
        before = self.cursor.read_bytes()
        xi.cmd_uninstall(self._args(apply=True, only="nosuchagent"))
        self.assertEqual(self.cursor.read_bytes(), before)
        self.assertEqual(len(xi.load_state()["entries"]), 2)

    def test_uninstall_apply_leaves_no_backup_behind(self):
        """An uninstall must not leave a .bak-* holding the connector token."""
        xi.cmd_uninstall(self._args(apply=True))
        self.assertEqual(xi.xylem_backups(self.cursor), [])
        self.assertEqual(xi.xylem_backups(self.vscode), [])


if __name__ == "__main__":
    unittest.main()

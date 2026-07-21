"""doctor tests: per-server health rows across every state, no server launch.

diagnose() is pure given (manifest, mapping, python, has_mcp), so we drive each
outcome directly. _script_parses compiles but never executes. Stdlib unittest.
"""
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import installer  # noqa: E402


def _stdio(name, dirname):
    return {
        "name": name, "transport": "stdio", "available": True,
        "command": "$PYTHON",
        "args": ["$XYLEM_PARENT/%s/server.py" % dirname],
        "source": {"repo": "owner/%s" % dirname, "dir": dirname, "ref": None},
    }


class ScriptParsesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def _write(self, body):
        p = os.path.join(self.tmp, "s.py")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(body)
        return p

    def test_valid_python_parses(self):
        self.assertTrue(installer._script_parses(self._write("x = 1\n")))

    def test_syntax_error_does_not_parse(self):
        self.assertFalse(installer._script_parses(self._write("def (:\n")))

    def test_missing_file_does_not_parse(self):
        self.assertFalse(
            installer._script_parses(os.path.join(self.tmp, "nope.py")))

    def test_parsing_never_executes_side_effects(self):
        # A module with import-time side effects must not run during a parse
        # check -- doctor must never launch or execute a server.
        marker = os.path.join(self.tmp, "ran.txt")
        body = "open(%r, 'w').write('x')\n" % marker
        installer._script_parses(self._write(body))
        self.assertFalse(os.path.exists(marker))


class DiagnoseTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.mapping = {"$XYLEM_PARENT": installer.to_fwd(self.tmp)}

    def _install_server(self, dirname, body="import mcp\n"):
        d = os.path.join(self.tmp, dirname)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "server.py"), "w", encoding="utf-8") as fh:
            fh.write(body)

    def _rows(self, manifest, has_mcp=True):
        rows = installer.diagnose(manifest, self.mapping, "python", has_mcp)
        return {r[0]: r for r in rows}

    def test_present_parsing_server_with_mcp_is_ok(self):
        self._install_server("context-keeper")
        rows = self._rows({"servers": [_stdio("context-keeper",
                                              "context-keeper")]})
        self.assertTrue(rows["context-keeper"][1])
        self.assertEqual(rows["context-keeper"][2], "OK")

    def test_missing_script_fails(self):
        rows = self._rows({"servers": [_stdio("agentsync", "agentsync")]})
        self.assertFalse(rows["agentsync"][1])
        self.assertIn("not found", rows["agentsync"][3])

    def test_syntax_error_fails(self):
        self._install_server("cambium", body="def (:\n")
        rows = self._rows({"servers": [_stdio("cambium", "cambium")]})
        self.assertFalse(rows["cambium"][1])
        self.assertIn("syntax error", rows["cambium"][3])

    def test_missing_mcp_fails_even_when_script_is_present(self):
        self._install_server("context-keeper")
        rows = self._rows(
            {"servers": [_stdio("context-keeper", "context-keeper")]},
            has_mcp=False)
        self.assertFalse(rows["context-keeper"][1])
        self.assertIn("mcp", rows["context-keeper"][3])

    def test_http_with_url_set_is_ok(self):
        os.environ["DOCTOR_TEST_URL"] = "https://example.com/mcp/tok"
        try:
            rows = self._rows({"servers": [{
                "name": "agentsync-remote", "transport": "http",
                "available": True, "url_env_key": "DOCTOR_TEST_URL"}]})
        finally:
            del os.environ["DOCTOR_TEST_URL"]
        self.assertTrue(rows["agentsync-remote"][1])
        self.assertEqual(rows["agentsync-remote"][2], "OK")

    def test_http_without_url_warns_but_is_not_a_failure(self):
        # Remotes are optional: an unset URL is a WARN, not a FAIL, so doctor
        # does not fail a healthy local-only install.
        os.environ.pop("DOCTOR_TEST_URL_UNSET", None)
        rows = self._rows({"servers": [{
            "name": "context-keeper-remote", "transport": "http",
            "available": True, "url_env_key": "DOCTOR_TEST_URL_UNSET"}]})
        self.assertTrue(rows["context-keeper-remote"][1])
        self.assertEqual(rows["context-keeper-remote"][2], "WARN")


if __name__ == "__main__":
    unittest.main()

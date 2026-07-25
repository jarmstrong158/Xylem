"""doctor tests: per-server health rows, and a handshake that actually happens.

doctor used to report "all servers healthy" for servers it had never started.
It checked that the file existed, that compile() accepted it, and that the
interpreter could `import mcp` -- none of which is the question a user asking
"are my servers working?" is asking. A server whose module raises at import,
whose required env var is unset, or which crashes before it can speak MCP
passes all three and is reported OK.

It now launches each stdio server and completes a real MCP `initialize`
handshake. diagnose() takes the handshake as an injected callable so the row
logic can be driven without spawning anything; MCPHandshakeTest exercises the
real one against genuine stdio servers built in a temp dir.
"""
import json
import os
import sys
import tempfile
import time
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

    def _rows(self, manifest, has_mcp=True, handshake=None):
        if handshake is None:
            def handshake(command, args, env=None, **kw):
                return True, "handshake OK -- fake v1 (MCP 2024-11-05)"
        rows = installer.diagnose(
            manifest, self.mapping, "python", has_mcp, handshake=handshake)
        return {r[0]: r for r in rows}

    def test_a_server_that_completes_the_handshake_is_ok(self):
        self._install_server("context-keeper")
        rows = self._rows({"servers": [_stdio("context-keeper",
                                              "context-keeper")]})
        self.assertTrue(rows["context-keeper"][1])
        self.assertEqual(rows["context-keeper"][2], "OK")
        self.assertIn("handshake OK", rows["context-keeper"][3])

    def test_a_present_parsing_server_that_will_not_start_is_NOT_ok(self):
        """The regression the whole change exists for.

        This server exists, compiles, and the interpreter has mcp -- every
        check the old doctor made. It just does not start. The old doctor
        reported it healthy.
        """
        self._install_server("context-keeper")

        def refuses(command, args, env=None, **kw):
            return False, "exited 1: ModuleNotFoundError: No module named 'httpx'"

        rows = self._rows(
            {"servers": [_stdio("context-keeper", "context-keeper")]},
            handshake=refuses)
        self.assertFalse(rows["context-keeper"][1])
        self.assertEqual(rows["context-keeper"][2], "FAIL")
        self.assertIn("httpx", rows["context-keeper"][3])

    def test_the_handshake_receives_the_resolved_args_and_env(self):
        # A server launched without its manifest env is a different server; the
        # handshake must see exactly what the installer would register.
        self._install_server("agentsync")
        server = _stdio("agentsync", "agentsync")
        server["env"] = {"AGENTSYNC_AGENT_ID": "$AGENT_ID"}
        self.mapping["$AGENT_ID"] = "tester"
        seen = {}

        def capture(command, args, env=None, **kw):
            seen.update(command=command, args=args, env=env)
            return True, "handshake OK -- x v1 (MCP 2024-11-05)"

        self._rows({"servers": [server]}, handshake=capture)
        self.assertEqual(seen["command"], "python")
        self.assertEqual(seen["env"], {"AGENTSYNC_AGENT_ID": "tester"})
        self.assertTrue(seen["args"][0].endswith("agentsync/server.py"),
                        seen["args"])
        self.assertNotIn("$XYLEM_PARENT", seen["args"][0])

    def test_a_broken_script_is_reported_as_itself_not_as_a_failed_handshake(self):
        # The cheap checks are kept as better error messages: "syntax error" is
        # more actionable than "no response to initialize".
        self._install_server("cambium", body="def (:\n")
        called = []
        self._rows({"servers": [_stdio("cambium", "cambium")]},
                   handshake=lambda *a, **k: called.append(1) or (True, ""))
        self.assertEqual(called, [], "doctor launched a script it knew was broken")

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

    def test_http_servers_are_never_launched(self):
        # An http server has nothing to spawn, and reaching out to the Worker
        # would make doctor's exit code depend on the network.
        called = []
        os.environ["DOCTOR_TEST_URL"] = "https://example.com/mcp/tok"
        try:
            self._rows({"servers": [{
                "name": "agentsync-remote", "transport": "http",
                "available": True, "url_env_key": "DOCTOR_TEST_URL"}]},
                handshake=lambda *a, **k: called.append(1) or (True, ""))
        finally:
            del os.environ["DOCTOR_TEST_URL"]
        self.assertEqual(called, [])

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

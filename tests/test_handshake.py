"""The MCP `initialize` handshake doctor now performs, against real processes.

doctor used to report "all servers healthy" for servers it had never started.
It checked that the file existed, that compile() accepted it, and that the
interpreter could `import mcp` -- and none of those is the question the user is
asking. A server whose module raises at import, whose required env var is unset,
whose own dependency is missing, or which crashes before it can speak MCP passes
all three and gets reported OK.

Every server in this module is a genuine child process: a real stdio JSON-RPC
responder, or a real failure. Nothing here is mocked, because a mocked
handshake would be the same kind of verification-that-doesn't-verify the
change exists to remove.

Stdlib unittest only.
"""

import json
import os
import shutil
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import installer  # noqa: E402

# A minimal but honest stdio MCP server: reads one JSON-RPC line, answers the
# initialize request, flushes. %r is the server name it reports.
RESPONDER = r"""
import json, sys
msg = json.loads(sys.stdin.readline())
sys.stdout.write(json.dumps({
    "jsonrpc": "2.0", "id": msg["id"],
    "result": {"protocolVersion": "2024-11-05",
               "serverInfo": {"name": %r, "version": "9.9"}}}) + "\n")
sys.stdout.flush()
"""


class ParseInitializeResponse(unittest.TestCase):
    """What counts as a completed handshake, and what does not."""

    def _reply(self, **result):
        return json.dumps({"jsonrpc": "2.0", "id": 1, "result": result})

    def test_a_well_formed_result_reports_the_server_identity(self):
        ok, detail = installer.parse_initialize_response(self._reply(
            protocolVersion="2024-11-05",
            serverInfo={"name": "cambium", "version": "1.27.0"}))
        self.assertTrue(ok)
        self.assertIn("cambium", detail)
        self.assertIn("1.27.0", detail)
        self.assertIn("2024-11-05", detail)

    def test_a_result_without_a_version_still_counts(self):
        ok, detail = installer.parse_initialize_response(
            self._reply(protocolVersion="2024-11-05", serverInfo={"name": "x"}))
        self.assertTrue(ok)
        self.assertIn("x", detail)

    def test_chatter_before_the_reply_is_skipped(self):
        # Servers log freely on stdout before the transport comes up.
        noisy = "starting up...\nloading config\n" + self._reply(
            protocolVersion="2024-11-05", serverInfo={"name": "noisy"})
        ok, detail = installer.parse_initialize_response(noisy)
        self.assertTrue(ok, detail)
        self.assertIn("noisy", detail)

    def test_a_jsonrpc_error_reply_carries_the_servers_own_message(self):
        # The server telling you what it needs is far more useful than
        # "unhealthy".
        raw = json.dumps({"jsonrpc": "2.0", "id": 1, "error": {
            "code": -32602, "message": "AGENTSYNC_REPO is not set"}})
        ok, detail = installer.parse_initialize_response(raw)
        self.assertFalse(ok)
        self.assertIn("AGENTSYNC_REPO is not set", detail)

    def test_a_reply_to_a_different_id_is_not_our_handshake(self):
        raw = json.dumps({"jsonrpc": "2.0", "id": 99,
                          "result": {"serverInfo": {"name": "other"}}})
        self.assertEqual(installer.parse_initialize_response(raw), (False, ""))

    def test_silence_and_garbage_are_failures(self):
        for raw in ("", "\n\n", "not json", "{", "[]", None, "12345"):
            with self.subTest(raw=raw):
                self.assertEqual(installer.parse_initialize_response(raw), (False, ""))


class TheHandshakePayload(unittest.TestCase):
    def test_it_is_a_valid_initialize_request(self):
        lines = [l for l in installer._handshake_payload().splitlines() if l.strip()]
        self.assertEqual(len(lines), 2)
        request = json.loads(lines[0])
        self.assertEqual(request["jsonrpc"], "2.0")
        self.assertEqual(request["method"], "initialize")
        self.assertEqual(request["id"], 1)
        self.assertIn("protocolVersion", request["params"])
        self.assertIn("capabilities", request["params"])
        self.assertIn("clientInfo", request["params"])

    def test_it_completes_the_mcp_lifecycle_with_the_initialized_notification(self):
        lines = [l for l in installer._handshake_payload().splitlines() if l.strip()]
        notification = json.loads(lines[1])
        self.assertEqual(notification["method"], "notifications/initialized")
        self.assertNotIn("id", notification)  # a notification has no id

    def test_it_is_newline_delimited_one_message_per_line(self):
        # MCP's stdio transport framing. One message per line, nothing else.
        for line in installer._handshake_payload().splitlines():
            if line.strip():
                json.loads(line)


class RealServerHandshake(unittest.TestCase):
    """Real child processes. These are the cases doctor used to call healthy."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="xylem-hs-")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def _server(self, body, name="server.py"):
        path = os.path.join(self.tmp, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(body)
        return path

    def test_a_real_stdio_server_completes_the_handshake(self):
        path = self._server(RESPONDER % "goodserver")
        ok, detail = installer.mcp_handshake(sys.executable, [path])
        self.assertTrue(ok, detail)
        self.assertIn("goodserver", detail)
        self.assertIn("9.9", detail)

    def test_a_server_that_raises_at_import_fails_with_its_own_error(self):
        # Exists, compiles, interpreter has mcp: the old doctor said healthy.
        path = self._server("import nonexistent_module_xyz\n")
        ok, detail = installer.mcp_handshake(sys.executable, [path])
        self.assertFalse(ok)
        self.assertIn("nonexistent_module_xyz", detail)

    def test_a_server_missing_a_required_env_var_fails_with_that_reason(self):
        path = self._server(
            "import os, sys\n"
            "if not os.environ.get('DOCTOR_REQUIRED'):\n"
            "    sys.stderr.write('DOCTOR_REQUIRED is not set\\n')\n"
            "    raise SystemExit(2)\n")
        ok, detail = installer.mcp_handshake(sys.executable, [path])
        self.assertFalse(ok)
        self.assertIn("DOCTOR_REQUIRED", detail)

    def test_the_declared_env_actually_reaches_the_server(self):
        # A server launched without its manifest env is a different server.
        path = self._server(
            "import os\n"
            "if not os.environ.get('DOCTOR_REQUIRED'):\n"
            "    raise SystemExit(2)\n"
            + RESPONDER % "configured")
        ok, detail = installer.mcp_handshake(
            sys.executable, [path], {"DOCTOR_REQUIRED": "yes"})
        self.assertTrue(ok, detail)
        self.assertIn("configured", detail)

    def test_a_server_that_exits_silently_is_a_failure(self):
        path = self._server("import sys\nsys.exit(0)\n")
        ok, detail = installer.mcp_handshake(sys.executable, [path])
        self.assertFalse(ok)
        self.assertIn("no initialize reply", detail)

    def test_a_server_that_answers_the_wrong_thing_is_a_failure(self):
        path = self._server(
            "import sys\n"
            "sys.stdin.readline()\n"
            "sys.stdout.write('OK\\n')\n")
        ok, detail = installer.mcp_handshake(sys.executable, [path])
        self.assertFalse(ok)

    def test_a_server_that_never_answers_is_killed_at_the_timeout(self):
        # The reason doctor cannot simply wait: a server that starts and hangs
        # would otherwise hang doctor, and hang it inside CI.
        path = self._server("import time\ntime.sleep(600)\n")
        started = time.time()
        ok, detail = installer.mcp_handshake(sys.executable, [path], timeout=2)
        elapsed = time.time() - started
        self.assertFalse(ok)
        self.assertIn("never spoke MCP", detail)
        self.assertLess(elapsed, 30, "the handshake did not honour its timeout")

    def test_a_missing_interpreter_is_a_failure_not_a_crash(self):
        ok, detail = installer.mcp_handshake(
            os.path.join(self.tmp, "no-such-python"), ["whatever.py"])
        self.assertFalse(ok)
        self.assertIn("could not launch", detail)

    def test_the_server_process_does_not_outlive_the_handshake(self):
        # stdin is closed after the payload, so a well-behaved server sees EOF
        # and exits; a badly-behaved one is killed at the timeout. Either way
        # doctor must not leave processes behind.
        path = self._server(RESPONDER % "tidy")
        ok, _ = installer.mcp_handshake(sys.executable, [path])
        self.assertTrue(ok)
        # If the child were still holding the file open, Windows would refuse
        # this; on POSIX it is simply a sanity check that we got here at all.
        os.remove(path)

    def test_the_default_timeout_is_bounded(self):
        self.assertGreater(installer.HANDSHAKE_TIMEOUT, 0)
        self.assertLessEqual(installer.HANDSHAKE_TIMEOUT, 60)


class LastMeaningfulLine(unittest.TestCase):
    """The server's own stderr is the diagnosis; report it, don't summarise it."""

    def test_returns_the_last_non_blank_line(self):
        self.assertEqual(
            installer._last_meaningful_line("Traceback...\nImportError: no x\n\n"),
            "ImportError: no x")

    def test_empty_input_is_empty_output(self):
        for raw in ("", "\n \n", None):
            self.assertEqual(installer._last_meaningful_line(raw), "")

    def test_a_very_long_line_is_truncated_for_one_line_reporting(self):
        got = installer._last_meaningful_line("x" * 500)
        self.assertLessEqual(len(got), 200)
        self.assertTrue(got.endswith("..."))


if __name__ == "__main__":
    unittest.main()

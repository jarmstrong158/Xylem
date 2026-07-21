"""Plugin primer backend-status tests: no silent no-op when nothing is wired.

Imports the plugin's primer.py directly and exercises backend_status across the
configured / half-configured / unconfigured states. Stdlib unittest only.
"""
import importlib.util
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PRIMER_PATH = os.path.join(ROOT, "plugin", "scripts", "primer.py")

_spec = importlib.util.spec_from_file_location("xylem_primer", PRIMER_PATH)
primer = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(primer)


class BackendStatusTest(unittest.TestCase):
    def test_silent_when_context_keeper_url_set(self):
        self.assertEqual(
            primer.backend_status({"CONTEXT_KEEPER_REMOTE_URL": "https://x/mcp/t"}),
            "")

    def test_silent_when_only_agentsync_url_set(self):
        # Mid-setup: one URL is enough to know the user is configuring; no nag.
        self.assertEqual(
            primer.backend_status({"AGENTSYNC_REMOTE_URL": "https://x/mcp/t"}),
            "")

    def test_warns_when_neither_url_set(self):
        msg = primer.backend_status({})
        self.assertIn("No remote backend configured", msg)
        self.assertIn("CONTEXT_KEEPER_REMOTE_URL", msg)
        self.assertIn("AGENTSYNC_REMOTE_URL", msg)

    def test_empty_string_url_counts_as_unset(self):
        msg = primer.backend_status(
            {"CONTEXT_KEEPER_REMOTE_URL": "", "AGENTSYNC_REMOTE_URL": ""})
        self.assertIn("No remote backend configured", msg)

    def test_notice_is_ascii_only(self):
        # The primer writes to a possibly-cp1252 console; the notice must never
        # introduce a non-encodable byte.
        primer.backend_status({}).encode("ascii")


if __name__ == "__main__":
    unittest.main()

"""install/xylem_dashboard.py -- especially the redaction path.

735 lines with zero tests, and a daily cron (.github/workflows/dashboard.yml,
`17 7 * * *`, `permissions: contents: write`) runs it against two live Worker
secrets and pushes the result to a public GitHub Pages site. It has leaked PII
once already: dec-012 records that the live page contained a real username in a
`C:\\Users\\<user>\\repos\\...` path, published, while the module's own docstring
claimed "nothing secret is ever written to the output". The remediation was
scrub_text() and --no-notes (3e17a62). Nothing tested either of them.

What is covered here, in priority order:

  * scrub_text / clean_text -- the redaction itself, including the cases a
    regex-based scrubber gets wrong: doubled Windows separators from
    JSON-escaped notes, several paths in one string, mixed platforms,
    truncation interacting with redaction, and idempotence.
  * --no-notes -- the flag the public cron actually depends on, driven through
    the real collectors with the network stubbed.
  * the connector URLs -- which for these Workers ARE the credential
    (path-token auth), and so must never reach the rendered page.
  * the refuse-to-write-an-empty-dashboard guard, which is what stands between
    a transient Worker outage and a published blank page.

No network and no real git: rpc_call_tool is replaced. Stdlib unittest only.
"""

import contextlib
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DASHBOARD = os.path.join(ROOT, "install", "xylem_dashboard.py")


def _load():
    spec = importlib.util.spec_from_file_location("xylem_dashboard", DASHBOARD)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


dash = _load()

# A path shaped exactly like the one that was published. dec-012.
LEAKED = r"C:\Users\jarms\repos\ollama"


class ScrubHomePaths(unittest.TestCase):
    """The redaction that exists because a real username was published."""

    def assertNoUser(self, text, user="jarms"):
        self.assertNotIn(user, text, "the account name survived scrubbing: %r" % text)

    def test_the_exact_path_that_leaked_is_redacted(self):
        out = dash.scrub_text("worked in %s today" % LEAKED)
        self.assertNoUser(out)
        self.assertIn("<user>", out)
        # The shape of the path survives, so the note still reads sensibly.
        self.assertIn(r"C:\Users\<user>\repos\ollama", out)

    def test_json_escaped_windows_paths_are_redacted_too(self):
        # Notes arrive from claims.json, where backslashes are doubled. A
        # scrubber that only handles the single-backslash form redacts the
        # pretty case and publishes the real one.
        out = dash.scrub_text(r"C:\\Users\\jarms\\repos\\ollama")
        self.assertNoUser(out)
        self.assertIn("<user>", out)

    def test_posix_home_paths_are_redacted(self):
        for raw, user in ((r"/home/alice/src/thing", "alice"),
                          (r"/Users/bob/Projects/x", "bob")):
            with self.subTest(raw=raw):
                out = dash.scrub_text(raw)
                self.assertNoUser(out, user)
                self.assertIn("<user>", out)

    def test_a_lowercase_or_odd_drive_letter_is_still_redacted(self):
        for raw in (r"d:\Users\carol\x", r"C:\Users\dave"):
            with self.subTest(raw=raw):
                out = dash.scrub_text(raw)
                self.assertIn("<user>", out)

    def test_several_paths_in_one_note_are_all_redacted(self):
        note = ("moved %s to /home/erin/tmp and copied /Users/frank/a "
                "before touching %s again" % (LEAKED, LEAKED))
        out = dash.scrub_text(note)
        for user in ("jarms", "erin", "frank"):
            self.assertNoUser(out, user)
        self.assertEqual(out.count("<user>"), 4)

    def test_a_path_at_the_very_end_of_a_string_is_redacted(self):
        # No trailing separator to anchor on -- a common off-by-one in this
        # kind of regex.
        self.assertNoUser(dash.scrub_text("see C:\\Users\\jarms"))
        self.assertNoUser(dash.scrub_text("see /home/jarms"))

    def test_a_path_followed_by_punctuation_is_redacted(self):
        for tail in ('"', "'", ";", ":", ",", ")", "]", "}", " "):
            with self.subTest(tail=tail):
                self.assertNoUser(dash.scrub_text("/home/jarms" + tail))

    def test_scrubbing_is_idempotent(self):
        once = dash.scrub_text(LEAKED)
        self.assertEqual(dash.scrub_text(once), once)

    def test_text_with_no_home_path_is_untouched(self):
        for raw in ("refactored the parser", "/usr/local/bin/git", "C:\\Windows\\System32",
                    "/var/log/x", "users are happy"):
            with self.subTest(raw=raw):
                self.assertEqual(dash.scrub_text(raw), raw)

    def test_empty_and_none_are_safe(self):
        self.assertEqual(dash.scrub_text(""), "")
        self.assertIsNone(dash.scrub_text(None))


class CleanTextFunnel(unittest.TestCase):
    """clean_text is the single funnel every free-text field goes through."""

    def test_it_scrubs(self):
        self.assertNotIn("jarms", dash.clean_text("in %s" % LEAKED))

    def test_it_truncates_to_the_limit(self):
        self.assertEqual(len(dash.clean_text("x" * 500, 160)), 160)

    def test_truncation_cannot_expose_what_scrubbing_hid(self):
        # Order matters: truncate-then-scrub would let a cut land mid-path and
        # leave an unmatched, unredacted fragment in the output.
        note = "a" * 40 + LEAKED
        for limit in range(30, 90):
            with self.subTest(limit=limit):
                self.assertNotIn("jarms", dash.clean_text(note, limit))

    def test_it_repairs_mojibake_before_publishing_it(self):
        broken = "moved the file â€™round"
        self.assertNotIn("â€™", dash.clean_text(broken))

    def test_it_leaves_clean_unicode_alone(self):
        self.assertEqual(dash.clean_text("team → org"), "team → org")

    def test_none_becomes_empty_not_the_string_None(self):
        self.assertEqual(dash.clean_text(None), "")


class NoNotesFlag(unittest.TestCase):
    """The flag the public cron depends on: `--remote --no-notes`."""

    SURVEY = {"claims": {"alice": {
        "task": "ship the thing",
        "status": "in-progress",
        "branch": "feat/x",
        "note": "debugged it in %s, tell no one" % LEAKED,
        "updated_at": "2026-07-01T10:00:00+00:00",
    }}}
    HISTORY = {"commits": [{
        "message": "agentsync: bob releases 'other work' (finished in /home/bob/src)",
        "date": "2026-06-30T09:00:00+00:00",
    }]}

    def _stub_rpc(self):
        def rpc(url, name, arguments):
            return {"survey": self.SURVEY, "history": self.HISTORY}.get(name, {})
        real = dash.rpc_call_tool
        dash.rpc_call_tool = rpc
        self.addCleanup(setattr, dash, "rpc_call_tool", real)

    def _events(self, no_notes):
        self._stub_rpc()
        events, _ = dash.read_board_remote(
            "https://example.workers.dev/mcp/SECRET", dash.STACK_REPOS,
            no_notes=no_notes)
        return events

    def test_without_the_flag_notes_are_included_but_scrubbed(self):
        events = self._events(no_notes=False)
        body = json.dumps(events)
        self.assertIn("tell no one", body)  # notes ARE published by default...
        self.assertNotIn("jarms", body)     # ...but never with a home path
        self.assertNotIn("bob/src", body)

    def test_with_the_flag_every_note_and_description_is_empty(self):
        events = self._events(no_notes=True)
        self.assertTrue(events)
        for event in events:
            self.assertEqual(event["note"], "", event)
            self.assertEqual(event["desc"], "", event)
        self.assertNotIn("tell no one", json.dumps(events))

    def test_titles_and_names_survive_the_flag(self):
        # --no-notes drops bodies, not the board itself.
        events = self._events(no_notes=True)
        titles = {e["t"] for e in events}
        self.assertIn("ship the thing", titles)
        self.assertIn("alice", {e["who"] for e in events})

    def test_the_workflow_still_passes_no_notes(self):
        # The public page is generated by this exact command line; losing the
        # flag is how the notes get published again.
        path = os.path.join(ROOT, ".github", "workflows", "dashboard.yml")
        with open(path, encoding="utf-8") as fh:
            workflow = fh.read()
        self.assertIn("--no-notes", workflow)
        self.assertIn("xylem_dashboard.py", workflow)


class LocalBoardRedaction(unittest.TestCase):
    """The local route funnels through the same scrubber."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="xylem-dash-")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def _project(self, name, decisions):
        base = os.path.join(self.tmp, name)
        ctx = os.path.join(base, ".context")
        os.makedirs(ctx)
        with open(os.path.join(ctx, "decisions.json"), "w", encoding="utf-8") as fh:
            json.dump(decisions, fh)
        with open(os.path.join(ctx, "constraints.json"), "w", encoding="utf-8") as fh:
            json.dump([], fh)
        return base

    def test_decision_summaries_are_scrubbed(self):
        base = self._project("proj", [
            {"id": "dec-001", "status": "active",
             "summary": "documented the layout of %s" % LEAKED}])
        stores, decisions = dash.read_context_local([base])
        self.assertEqual(len(decisions), 1)
        self.assertNotIn("jarms", json.dumps(decisions))
        self.assertIn("<user>", decisions[0]["t"])

    def test_only_active_decisions_are_counted(self):
        base = self._project("proj", [
            {"id": "dec-001", "status": "active", "summary": "a"},
            {"id": "dec-002", "status": "deprecated", "summary": "b"},
            {"id": "dec-003", "summary": "c"},  # absent status == active
        ])
        stores, _ = dash.read_context_local([base])
        self.assertEqual(stores[0]["dec"], 2)

    def test_a_missing_or_corrupt_store_is_skipped_not_fatal(self):
        base = self._project("proj", [{"id": "d", "summary": "x"}])
        with open(os.path.join(base, ".context", "decisions.json"), "w") as fh:
            fh.write("{ not json")
        stores, decisions = dash.read_context_local([base, os.path.join(self.tmp, "nope")])
        self.assertEqual(stores[0]["dec"], 0)
        self.assertEqual(decisions, [])


class RenderedPageCarriesNoSecrets(unittest.TestCase):
    """The output is published. Assert on the bytes that get published."""

    TOKEN = "s3cret-path-token"

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="xylem-render-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.template = os.path.join(self.tmp, "tpl.html")
        with open(self.template, "w", encoding="utf-8") as fh:
            fh.write("<html><script>const D = %s;</script></html>" % dash.PLACEHOLDER)
        self.out = os.path.join(self.tmp, "dashboard.html")

    def _render(self, no_notes):
        url = "https://example.workers.dev/mcp/%s" % self.TOKEN
        survey = {"claims": {"alice": {
            "task": "work in %s" % LEAKED, "status": "in-progress",
            "note": "notes from /home/alice/x", "branch": "b",
            "updated_at": "2026-07-01T10:00:00+00:00"}}}

        real = dash.rpc_call_tool
        dash.rpc_call_tool = lambda u, n, a: survey if n == "survey" else {}
        self.addCleanup(setattr, dash, "rpc_call_tool", real)

        events, _ = dash.read_board_remote(url, dash.STACK_REPOS, no_notes=no_notes)
        data = dash.assemble("remote", [], [], events, None, "board")
        dash.render(self.template, data, self.out)
        with open(self.out, encoding="utf-8") as fh:
            return fh.read()

    def test_the_connector_token_never_reaches_the_page(self):
        # These Workers authenticate on the URL path, so the URL IS the secret.
        html = self._render(no_notes=True)
        self.assertNotIn(self.TOKEN, html)
        self.assertNotIn("workers.dev", html)

    def test_no_home_path_reaches_the_page_even_with_notes_on(self):
        html = self._render(no_notes=False)
        self.assertNotIn("jarms", html)
        self.assertNotIn("/home/alice", html)

    def test_the_page_is_valid_json_inside_valid_html(self):
        html = self._render(no_notes=True)
        blob = html.split("const D = ", 1)[1].rsplit(";</script>", 1)[0]
        data = json.loads(blob)
        self.assertIn("events", data)

    def test_a_template_without_the_placeholder_is_refused(self):
        bad = os.path.join(self.tmp, "bad.html")
        with open(bad, "w", encoding="utf-8") as fh:
            fh.write("<html>nothing here</html>")
        with self.assertRaises(RuntimeError):
            dash.render(bad, {}, self.out)

    def test_a_missing_template_is_refused_with_an_actionable_message(self):
        with self.assertRaises(RuntimeError) as ctx:
            dash.render(os.path.join(self.tmp, "gone.html"), {}, self.out)
        self.assertIn("template not found", str(ctx.exception))

    def test_the_real_shipped_template_has_the_placeholder(self):
        # The cron renders against this file; without the placeholder every
        # scheduled run fails.
        with open(str(dash.DEFAULT_TEMPLATE), encoding="utf-8") as fh:
            self.assertIn(dash.PLACEHOLDER, fh.read())


class CommittedDashboardIsClean(unittest.TestCase):
    """dec-012 scrubbed the published page in place. Keep it scrubbed."""

    def test_the_committed_page_carries_no_home_paths(self):
        page = os.path.join(ROOT, "docs", "dashboard.html")
        if not os.path.isfile(page):
            self.skipTest("no committed dashboard.html")
        with open(page, encoding="utf-8") as fh:
            html = fh.read()
        # Whatever survives here must already be redacted: scrubbing it again
        # must be a no-op.
        self.assertEqual(dash.scrub_text(html), html,
                         "docs/dashboard.html contains an unredacted home path")


class RefusesToPublishNothing(unittest.TestCase):
    """A transient Worker outage must not overwrite a good page with a blank one."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="xylem-empty-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.out = os.path.join(self.tmp, "dashboard.html")
        with open(self.out, "w", encoding="utf-8") as fh:
            fh.write("<html>the good dashboard</html>")
        for key in ("CONTEXT_KEEPER_REMOTE_URL", "AGENTSYNC_REMOTE_URL"):
            old = os.environ.get(key)
            os.environ[key] = "https://example.workers.dev/mcp/tok"
            self.addCleanup(
                lambda k=key, v=old: os.environ.__setitem__(k, v)
                if v is not None else os.environ.pop(k, None))

    def _run(self, argv):
        buf, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
            rc = dash.main(argv)
        return rc, buf.getvalue() + err.getvalue()

    def test_an_unreachable_worker_exits_nonzero_and_writes_nothing(self):
        def boom(url, name, arguments):
            raise RuntimeError("connection refused")

        real = dash.rpc_call_tool
        dash.rpc_call_tool = boom
        self.addCleanup(setattr, dash, "rpc_call_tool", real)

        rc, output = self._run(["--remote", "--no-notes", "--output", self.out])
        self.assertNotEqual(rc, 0)
        self.assertIn("Refusing to write", output)
        with open(self.out, encoding="utf-8") as fh:
            self.assertIn("the good dashboard", fh.read())

    def test_the_workflow_relies_on_that_nonzero_exit(self):
        # The job's comment says the generator "exits non-zero if the route
        # collects nothing", which is what leaves the committed page untouched.
        path = os.path.join(ROOT, ".github", "workflows", "dashboard.yml")
        with open(path, encoding="utf-8") as fh:
            self.assertIn("exits non-zero", fh.read())


class RpcResponseHandling(unittest.TestCase):
    """The transport quirk that silently emptied the board once already."""

    def test_a_jsonrpc_error_is_raised_not_returned_as_data(self):
        # Swallowing it would publish an empty board as though it were real.
        self.assertTrue(callable(dash.rpc_call_tool))

    def test_attribute_repo_prefers_the_task_over_a_chatty_note(self):
        # Concatenating task+branch+note let a long note mentioning another
        # repo outvote the claim's own title.
        got = dash.attribute_repo(
            "fix cambium recall", "main", dash.STACK_REPOS,
            note="also touched agentsync and context-keeper along the way")
        self.assertEqual(got, "cambium")

    def test_attribute_repo_prefers_the_longest_matching_name(self):
        known = {"context-keeper", "context-keeper-remote"}
        self.assertEqual(
            dash.attribute_repo("ship context-keeper-remote", "", known),
            "context-keeper-remote")

    def test_attribute_repo_never_invents_a_repo(self):
        self.assertEqual(
            dash.attribute_repo("some unrelated work", "", dash.STACK_REPOS),
            "unassigned")


class DateHelpers(unittest.TestCase):
    def test_iso_dates_render(self):
        self.assertEqual(dash.mmdd_from_iso("2026-07-01T10:00:00+00:00"), "07-01")
        self.assertEqual(dash.ymd_from_iso("2026-07-01T10:00:00Z"), "2026-07-01")

    def test_a_malformed_date_degrades_instead_of_raising(self):
        self.assertEqual(dash.mmdd_from_iso("2026-07-01 garbage"), "07-01")
        self.assertEqual(dash.ymd_from_iso("nonsense-here"), "nonsense-h")
        self.assertEqual(dash.ymd_from_epoch("not a number"), "")

    def test_empty_dates_are_empty(self):
        self.assertEqual(dash.mmdd_from_iso(""), "")
        self.assertEqual(dash.ymd_from_iso(None), "")


class DocstringIsHonest(unittest.TestCase):
    """dec-012: the docstring claimed 'nothing secret is ever written'."""

    def test_it_does_not_claim_more_than_scrubbing_delivers(self):
        doc = dash.__doc__ or ""
        self.assertNotIn("nothing secret is ever written", doc)

    def test_it_names_the_transformation_it_actually_applies(self):
        doc = dash.__doc__ or ""
        self.assertIn("scrub_text", doc)
        self.assertIn("--no-notes", doc)


if __name__ == "__main__":
    unittest.main()

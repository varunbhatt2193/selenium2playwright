"""Step 10.3 — the playground's behaviour, without a browser or a backend.

Streamlit apps are notoriously untested, and the reason is structural: the file
*is* the program, so importing it runs it, and everything interesting is tangled
up with a widget. This project answers that by keeping `ui/app.py` a layout and
putting every decision in `playground.py` — so these tests are ordinary tests
over ordinary functions, and the thing they cannot cover (where the buttons sit)
is the thing that matters least.

Three of them are worth more than the rest:

* `test_payload_is_something_the_guard_accepts` runs step 10.4's real
  authorization handler over the request this module builds. The playground and
  the guard are the two halves of one contract, written a step apart, and this
  is the only test that would notice them drifting.
* `test_refusal_is_an_outcome_not_a_crash` — an unsupported file leaves the
  graph through `refuse` with no report at all. The page must say why, not throw.
* `test_playground_never_imports_streamlit` keeps the split honest. The moment
  it fails, none of these tests can be written any more.
"""

from __future__ import annotations

import asyncio
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import httpx

from selenium2playwright import guard, limits
from selenium2playwright import playground as pg


def run(coro):
    return asyncio.run(coro)


class FakeUser:
    def __init__(self, identity: str, permissions: list[str]):
        self.identity, self.permissions = identity, permissions


class FakeCtx:
    def __init__(self, identity: str, permissions: list[str]):
        self.user = FakeUser(identity, permissions)


VISITOR = FakeCtx("demo:someone", ["demo"])

SELENIUM = """import { By, WebDriver } from 'selenium-webdriver';

export class LoginPage {
  constructor(private driver: WebDriver) {}
  async login(user: string) {
    await this.driver.findElement(By.id('username')).sendKeys(user);
  }
}
"""

PLAYWRIGHT = """import { Page } from '@playwright/test';

export class LoginPage {
  constructor(private page: Page) {}
  async login(user: string) {
    await this.page.getByLabel('Username').fill(user);
  }
}
"""


def report_state(**overrides):
    """A final graph state of the shape `values` streaming actually delivers."""
    state = {
        "models": {"actor": "anthropic:claude-sonnet-5", "critic": "anthropic:claude-opus-5"},
        "report": {
            "status": "needs-review",
            "attempts": 2,
            "reason": "compiles, with one locator to confirm",
            "result": {"code": PLAYWRIGHT, "todos": ["TODO(review): confirm the username locator"],
                       "notes": ["explicit wait removed: Playwright auto-waits"]},
            "validation": [
                {"gate": "compile", "passed": True, "findings": []},
                {"gate": "residue", "passed": True, "findings": []},
                {"gate": "lint", "passed": True, "findings": []},
                {"gate": "parity", "passed": False, "findings": [{"code": "missing-test"}]},
            ],
            "critique": {"verdict": "pass", "fixes": []},
            "errors": [],
        },
    }
    state.update(overrides)
    return state


class Chunk:
    """One SSE part, the way `langgraph_sdk` hands it to a caller."""

    def __init__(self, event: str, data):
        self.event, self.data = event, data


class FakeRuns:
    def __init__(self, chunks, calls):
        self.chunks, self.calls = chunks, calls

    def stream(self, thread_id, assistant, input=None, stream_mode=None):  # noqa: A002
        self.calls.append({"thread_id": thread_id, "assistant": assistant,
                           "input": input, "stream_mode": stream_mode})
        yield from self.chunks


class FakeClient:
    def __init__(self, chunks):
        self.calls: list[dict] = []
        self.runs = FakeRuns(chunks, self.calls)


def fake_response(status_code: int, body=None, headers=None) -> httpx.Response:
    return httpx.Response(
        status_code,
        json=body if body is not None else {},
        headers=headers or {},
        request=httpx.Request("GET", "http://backend/limits"),
    )


class SampleTests(unittest.TestCase):
    """The one-click buttons, which are real files and not fixtures."""

    def test_every_sample_exists_and_is_selenium(self):
        found = pg.samples()
        self.assertEqual(len(found), 6, [s.name for s in found])
        for sample in found:
            text = sample.read()
            self.assertIn("selenium-webdriver", text, sample.name)
            self.assertTrue(sample.blurb.strip(), sample.name)

    def test_sample_names_are_names_the_demo_will_accept(self):
        # They travel to the server as `source_path` labels, and step 10.4
        # refuses anything path-shaped. A sample button that gets a 403 would be
        # the worst possible first impression.
        for sample in pg.samples():
            self.assertRegex(sample.name, pg.BARE_NAME)
            self.assertEqual(pg.check_input(sample.read(), sample.name), "")

    def test_the_spec_sample_carries_its_converted_page_object(self):
        """The one sample that cannot compile alone, and the fix for it.

        `login.spec.ts` imports `../pages/LoginPage`. Sent by itself, `tsc`
        fails on an import that cannot resolve — an honest finding about an
        incomplete request, and a terrible first impression. So the button
        sends the golden Playwright page object as `context_text`, exactly the
        way the suite graph feeds wave 2.
        """
        spec = next(s for s in pg.samples() if s.name == "login.spec.ts")
        context = spec.context()
        self.assertEqual(list(context), ["LoginPage.ts"])
        self.assertIn("@playwright/test", context["LoginPage.ts"])
        self.assertNotIn("selenium-webdriver", context["LoginPage.ts"])

    def test_page_objects_need_no_companion(self):
        page_object = next(s for s in pg.samples() if s.name == "LoginPage.ts")
        self.assertEqual(page_object.context(), {})

    def test_a_missing_companion_file_is_simply_no_companion(self):
        with TemporaryDirectory() as empty:
            with patch.object(pg, "_GOLDEN", Path(empty)):
                spec = next(s for s in pg.samples() if s.name == "login.spec.ts")
                self.assertEqual(spec.context(), {})

    def test_a_missing_samples_directory_costs_the_buttons_only(self):
        with TemporaryDirectory() as empty:
            with patch.object(pg, "_SUITE", Path(empty)):
                self.assertEqual(pg.samples(), [])


class InputCheckTests(unittest.TestCase):
    """What the page says before it spends anybody's budget."""

    def test_empty_paste_is_refused_with_an_instruction(self):
        self.assertIn("sample", pg.check_input("   \n", "x.ts"))

    def test_a_file_over_the_cap_is_refused_in_kilobytes(self):
        complaint = pg.check_input("x" * (pg.MAX_BYTES + 1), "Big.ts")
        self.assertIn("256 KB", complaint)

    def test_the_cap_counts_bytes_not_characters(self):
        # Same cap as graph.MAX_SOURCE_BYTES, same unit. One emoji is four
        # bytes, so a string well under the cap in characters can be over it.
        just_over = "🎭" * (pg.MAX_BYTES // 4 + 1)
        self.assertLess(len(just_over), pg.MAX_BYTES)
        self.assertIn("KB", pg.check_input(just_over, "Emoji.ts"))

    def test_a_path_shaped_name_is_refused_before_the_server_has_to(self):
        for name in ("../etc/passwd", "pages/LoginPage.ts", "/etc/passwd", ".env"):
            self.assertIn("plain name", pg.check_input(SELENIUM, name), name)

    def test_a_good_submission_has_nothing_to_say(self):
        self.assertEqual(pg.check_input(SELENIUM, "LoginPage.ts"), "")

    def test_a_companion_with_no_name_is_a_file_nothing_can_import(self):
        complaint = pg.check_input(SELENIUM, "login.spec.ts", companion_text=PLAYWRIGHT)
        self.assertIn("file name", complaint)

    def test_a_companion_may_not_be_a_path_either(self):
        complaint = pg.check_input(SELENIUM, "login.spec.ts",
                                   companion_name="../pages/LoginPage.ts",
                                   companion_text=PLAYWRIGHT)
        self.assertIn("plain name", complaint)

    def test_a_named_companion_is_accepted(self):
        self.assertEqual(
            pg.check_input(SELENIUM, "login.spec.ts",
                           companion_name="LoginPage.ts", companion_text=PLAYWRIGHT),
            "",
        )


class PayloadTests(unittest.TestCase):
    def setUp(self):
        limits._counter.reset()

    def test_text_goes_in_and_paths_never_do(self):
        request = pg.payload(SELENIUM, "LoginPage.ts")
        self.assertEqual(request["source_text"], SELENIUM)
        self.assertEqual(request["source_path"], "LoginPage.ts")
        self.assertFalse(request["ask_risks"])
        self.assertEqual(set(request) & set(guard.FORBIDDEN_INPUTS), set())

    def test_a_companion_travels_as_text_never_as_a_path(self):
        # context_paths would be a request to open a file on the server, and
        # step 10.4 refuses it. context_text is the same information with the
        # bytes attached.
        request = pg.payload(SELENIUM, "login.spec.ts",
                             context={"LoginPage.ts": PLAYWRIGHT})
        self.assertEqual(request["context_text"], {"LoginPage.ts": PLAYWRIGHT})
        self.assertNotIn("context_paths", request)

    def test_an_empty_companion_is_dropped_rather_than_sent_blank(self):
        request = pg.payload(SELENIUM, "a.ts", context={"": "", "B.ts": "   "})
        self.assertEqual(request["context_text"], {})

    def test_a_payload_with_a_companion_is_also_one_the_guard_accepts(self):
        body = {"assistant_id": "convert",
                "kwargs": {"input": pg.payload(SELENIUM, "login.spec.ts",
                                               context={"LoginPage.ts": PLAYWRIGHT})}}
        self.assertTrue(run(guard.guard_run(VISITOR, body)))

    def test_an_unnamed_paste_still_gets_a_typescript_name(self):
        # The extension is load-bearing: classify() and the playbook both read
        # it, so an unnamed paste must not arrive as an extensionless file.
        self.assertEqual(pg.payload(SELENIUM, "")["source_path"], "pasted.ts")

    def test_max_attempts_is_absent_unless_asked_for(self):
        self.assertNotIn("max_attempts", pg.payload(SELENIUM, "a.ts"))
        self.assertEqual(pg.payload(SELENIUM, "a.ts", max_attempts=2)["max_attempts"], 2)

    def test_payload_is_something_the_guard_accepts(self):
        """The two halves of one contract, checked against each other.

        `playground.payload` and `guard.guard_run` were written a step apart and
        have to agree about what a visitor may send. Nothing else in the test
        suite would notice if one of them changed.
        """
        body = {"assistant_id": "convert", "kwargs": {"input": pg.payload(SELENIUM, "LoginPage.ts")}}
        self.assertTrue(run(guard.guard_run(VISITOR, body)))

    def test_a_refinement_travels_as_a_sentence(self):
        request = pg.payload(SELENIUM, "LoginPage.ts", refinement="use getByRole")
        self.assertEqual(request["refinement"], "use getByRole")


class StreamTests(unittest.TestCase):
    """Turning a stream of SSE parts into three kinds of thing the page shows."""

    def test_run_id_nodes_and_final_state_all_come_out(self):
        client = FakeClient([
            Chunk("metadata", {"run_id": "run-1", "attempt": 1}),
            Chunk("updates", {"intake": {}}),
            Chunk("values", {"status": "converted"}),
            Chunk("updates", {"convert": {}}),
            Chunk("values", report_state()),
        ])
        updates = list(pg.stream(client, "thread-1", pg.payload(SELENIUM, "LoginPage.ts")))

        self.assertEqual([u.kind for u in updates],
                         ["run", "node", "state", "node", "state"])
        self.assertEqual(updates[0].run_id, "run-1")
        self.assertEqual([u.node for u in updates if u.kind == "node"], ["intake", "convert"])
        self.assertEqual(updates[-1].state["report"]["status"], "needs-review")

    def test_both_views_of_the_run_are_asked_for(self):
        # `updates` is the progress line and `values` is the answer; asking for
        # only one of them loses either the trail or the report.
        client = FakeClient([])
        list(pg.stream(client, "t", {}))
        self.assertEqual(client.calls[0]["stream_mode"], ["updates", "values"])
        self.assertEqual(client.calls[0]["assistant"], "convert")

    def test_events_it_does_not_understand_are_ignored(self):
        client = FakeClient([Chunk("events", {"whatever": 1}), Chunk("error", "boom")])
        self.assertEqual(list(pg.stream(client, "t", {})), [])

    def test_namespaced_event_names_still_count(self):
        # The server prefixes stream modes when a run is filtered
        # (`updates|convert`), and a startswith check is what keeps the progress
        # line working when it does.
        client = FakeClient([Chunk("updates|convert", {"convert": {}})])
        self.assertEqual([u.node for u in pg.stream(client, "t", {})], ["convert"])


class ProgressLabelTests(unittest.TestCase):
    def test_node_names_become_sentences(self):
        self.assertIn("four gates", pg.progress_label("validate", ["validate"]))

    def test_the_second_lap_says_so(self):
        seen = ["intake", "convert", "validate", "critic", "convert"]
        self.assertTrue(pg.progress_label("convert", seen).endswith("attempt 2"))

    def test_the_first_lap_does_not(self):
        self.assertNotIn("attempt", pg.progress_label("convert", ["intake", "convert"]))

    def test_an_unknown_node_is_shown_as_itself(self):
        self.assertEqual(pg.progress_label("mystery", ["mystery"]), "mystery")


class ScorecardTests(unittest.TestCase):
    def test_a_finished_run_is_flattened_into_what_the_page_shows(self):
        card = pg.scorecard(report_state())
        self.assertEqual(card.status, "needs-review")
        self.assertEqual(card.attempts, 2)
        self.assertEqual(card.gates,
                         [("compile", True), ("residue", True), ("lint", True), ("parity", False)])
        self.assertEqual(card.gates_line, "3/4 gates")
        self.assertEqual(card.critic, "pass")
        self.assertIn("username locator", card.todos[0])
        self.assertIn("auto-waits", card.notes[0])
        self.assertEqual(card.code, PLAYWRIGHT)
        self.assertFalse(card.passed)

    def test_a_passing_run_is_marked_as_one(self):
        state = report_state()
        state["report"]["status"] = "passed"
        self.assertTrue(pg.scorecard(state).passed)

    def test_refusal_is_an_outcome_not_a_crash(self):
        """An unsupported file has no report at all, and that is not an error.

        `refuse` is the path the agent is most confident about — a Java file, a
        README, something over the size cap. Rendering it as a failure would be
        a lie about the one answer we are sure of.
        """
        card = pg.scorecard({"status": "refused", "refusal": "This is a Java file, not TypeScript."})
        self.assertEqual(card.status, "refused")
        self.assertIn("Java", card.reason)
        self.assertEqual(card.code, "")
        self.assertEqual(card.gates, [])

    def test_a_state_with_neither_report_nor_reason_says_so(self):
        card = pg.scorecard({})
        self.assertEqual(card.status, "unavailable")
        self.assertIn("without a report", card.reason)

    def test_the_models_are_reported_from_the_state_not_guessed(self):
        self.assertEqual(pg.scorecard(report_state()).models["actor"],
                         "anthropic:claude-sonnet-5")


class DiffTests(unittest.TestCase):
    def test_the_diff_names_both_files_and_shows_both_sides(self):
        diff = pg.unified_diff(SELENIUM, PLAYWRIGHT, "LoginPage.ts", "LoginPage.ts")
        self.assertIn("--- LoginPage.ts", diff)
        self.assertIn("-import { By, WebDriver } from 'selenium-webdriver';", diff)
        self.assertIn("+import { Page } from '@playwright/test';", diff)

    def test_an_unchanged_file_diffs_to_nothing(self):
        self.assertEqual(pg.unified_diff(SELENIUM, SELENIUM, "a.ts", "a.ts"), "")

    def test_the_download_keeps_the_name_it_arrived_with(self):
        self.assertEqual(pg.download_name("login.spec.ts"), "login.spec.ts")
        self.assertEqual(pg.download_name(""), "pasted.ts")
        self.assertEqual(pg.download_name("LoginPage"), "LoginPage.ts")


class LimitsLineTests(unittest.TestCase):
    """The sentence in the sidebar, which is the only budget most people see."""

    SNAPSHOT = {
        "budget": {"used": 3, "limit": 41, "remaining": 38, "note": "…"},
        "per_visitor": {"daily": 10, "burst": 3, "window_s": 60},
        "budget_usd_per_day": 5.0,
    }

    def test_it_counts_conversions_and_names_the_money(self):
        self.assertEqual(pg.budget_line(self.SNAPSHOT), "38 of 41 conversions left today ($5/day).")

    def test_an_exhausted_budget_says_when_it_comes_back(self):
        spent = {**self.SNAPSHOT, "budget": {"used": 41, "limit": 41, "remaining": 0}}
        self.assertIn("midnight UTC", pg.budget_line(spent))

    def test_per_visitor_limits_are_stated_before_they_bite(self):
        self.assertEqual(pg.visitor_line(self.SNAPSHOT), "Per visitor: 10 a day, 3 per 60s.")

    def test_an_unreachable_backend_becomes_a_sentence_not_an_exception(self):
        with patch.object(pg.httpx, "get", side_effect=httpx.ConnectError("nope")):
            snapshot = pg.fetch_limits(url="http://backend", key="k", visitor="v")
        self.assertIn("did not answer", snapshot["error"])
        self.assertIn("did not answer", pg.budget_line(snapshot))

    def test_a_refused_key_is_explained_rather_than_shown_as_a_number(self):
        with patch.object(pg.httpx, "get", return_value=fake_response(401, {"error": "Invalid token."})):
            snapshot = pg.fetch_limits(url="http://backend", key="wrong", visitor="v")
        self.assertIn("S2P_DEMO_KEY", snapshot["error"])

    def test_the_visitor_id_is_sent_and_has_a_shape_the_guard_accepts(self):
        visitor = pg.new_visitor()
        self.assertRegex(visitor, guard._VISITOR_OK)
        self.assertNotEqual(visitor, pg.new_visitor())
        self.assertEqual(pg.headers("k", visitor)["X-S2P-Visitor"], visitor)
        self.assertEqual(pg.headers("k", visitor)["Authorization"], "Bearer k")


class FeedbackTests(unittest.TestCase):
    def test_a_thumbs_down_sends_the_file_back_with_it(self):
        with patch.object(pg.httpx, "post", return_value=fake_response(
                200, {"stored": True, "queued": True, "detail": "Recorded and queued."})) as post:
            answer = pg.send_feedback("run-1", 0.0, comment="wrong locator",
                                      source_text=SELENIUM, source_path="LoginPage.ts",
                                      url="http://backend", key="k", visitor="v")
        body = post.call_args.kwargs["json"]
        self.assertEqual(body["run_id"], "run-1")
        self.assertEqual(body["score"], 0.0)
        self.assertEqual(body["source_text"], SELENIUM)
        self.assertTrue(answer["queued"])

    def test_a_thumbs_up_does_not_ship_the_file_anywhere(self):
        # There is nothing to queue from a happy visitor, and sending their code
        # to LangSmith anyway would be collecting it for no reason.
        with patch.object(pg.httpx, "post", return_value=fake_response(200, {"stored": True})) as post:
            pg.send_feedback("run-1", 1.0, source_text=SELENIUM, url="http://backend", key="k")
        self.assertEqual(post.call_args.kwargs["json"]["source_text"], "")

    def test_a_failed_send_is_reported_honestly(self):
        with patch.object(pg.httpx, "post", side_effect=httpx.ConnectError("nope")):
            answer = pg.send_feedback("run-1", 0.0, url="http://backend", key="k")
        self.assertFalse(answer["stored"])
        self.assertIn("Could not reach", answer["detail"])


class ExplanationTests(unittest.TestCase):
    """Every guardrail eventually arrives here as a number. It must read as English."""

    def test_a_rate_limit_keeps_the_servers_own_sentence(self):
        detail = "Three runs a minute per visitor. Try again in 42s."
        self.assertEqual(pg.explain_status(429, detail), detail)

    def test_a_refusal_explains_which_rule_was_hit(self):
        detail = "`output_path` is not available on the public demo: it writes a file on the server."
        self.assertIn("output_path", pg.explain_status(403, detail))

    def test_a_bad_key_names_the_variable_to_fix(self):
        self.assertIn("S2P_DEMO_KEY", pg.explain_status(401))

    def test_an_http_error_carrying_a_response_is_read_from_it(self):
        error = httpx.HTTPStatusError(
            "429", request=httpx.Request("POST", "http://backend/threads/x/runs"),
            response=fake_response(429, {"detail": "Ten a day per visitor."},
                                   {"Retry-After": "3600"}),
        )
        self.assertIn("Ten a day", pg.explain(error))
        self.assertEqual(pg.retry_after(error), 3600)

    def test_a_timeout_says_a_conversion_takes_a_while(self):
        self.assertIn("minute", pg.explain(httpx.ReadTimeout("slow")))

    def test_an_unreachable_backend_names_the_url_it_tried(self):
        with patch.dict(os.environ, {"LANGGRAPH_DEPLOYMENT_URL": "http://127.0.0.1:9999"}):
            message = pg.explain(httpx.ConnectError("refused"))
        self.assertIn("127.0.0.1:9999", message)

    def test_retry_after_is_zero_when_nobody_said(self):
        self.assertEqual(pg.retry_after(ValueError("plain")), 0)


class SplitTests(unittest.TestCase):
    def test_playground_never_imports_streamlit(self):
        """The reason every test above can exist.

        Importing `ui/app.py` runs the app; importing `playground.py` must not
        need Streamlit installed at all. It is also what keeps `s2p convert`
        free of a web framework it has no use for.
        """
        source = Path(pg.__file__).read_text(encoding="utf-8")
        self.assertNotIn("import streamlit", source)

    def test_the_demo_key_is_preferred_over_the_owner_key(self):
        # An owner key here would bypass the meter entirely: every guardrail
        # step 10.4 built is enforced against the *demo* identity.
        with patch.dict(os.environ, {"S2P_DEMO_KEY": "demo", "S2P_API_KEY": "owner"}):
            self.assertEqual(pg.demo_key(), "demo")
        with patch.dict(os.environ, {"S2P_DEMO_KEY": "", "S2P_API_KEY": "owner"}):
            self.assertEqual(pg.demo_key(), "owner")

    def test_the_backend_url_comes_from_the_environment(self):
        with patch.dict(os.environ, {"LANGGRAPH_DEPLOYMENT_URL": "https://s2p.example/"}):
            self.assertEqual(pg.backend_url(), "https://s2p.example")


if __name__ == "__main__":
    unittest.main()

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
import io
import os
import unittest
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import httpx

from types import SimpleNamespace

from selenium2playwright import env, guard, limits, suite, suite_graph
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

    def stream(self, thread_id, assistant, input=None, stream_mode=None, **extra):  # noqa: A002
        self.calls.append({"thread_id": thread_id, "assistant": assistant,
                           "input": input, "stream_mode": stream_mode, **extra})
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


class SuiteAvailabilityTests(unittest.TestCase):
    """Whose filesystem is this? The one question suite mode turns on."""

    def test_a_backend_on_this_machine_is_the_only_one_that_qualifies(self):
        for url in ("http://127.0.0.1:2024", "http://localhost:8123",
                    "http://[::1]:2024", "http://0.0.0.0:2024/"):
            self.assertTrue(pg.is_local(url), url)
        for url in ("https://s2p.fly.dev", "http://10.0.0.69:8501",
                    "https://localhost.example.com"):
            self.assertFalse(pg.is_local(url), url)

    def test_a_remote_backend_can_still_run_suites_just_not_by_path(self):
        # The distinction the whole design turns on: what is local-only is
        # naming a folder, not converting one.
        blocked = pg.folder_blocker("https://s2p.fly.dev")
        self.assertIn("s2p.fly.dev", blocked)
        self.assertIn("Upload", blocked)
        self.assertEqual(pg.folder_blocker("http://127.0.0.1:2024"), "")

    def test_the_guard_really_would_refuse_a_folder_by_path(self):
        """The reason the folder input is local-only, asserted rather than described.

        `folder_blocker` says the deployment refuses `root` and `out_root`. This
        runs step 10.4's actual authorization handler over the actual payload
        this module builds and shows the refusal, so the sentence on screen
        cannot quietly become false while the guard changes underneath it.
        """
        body = {"assistant_id": "suite",
                "kwargs": {"input": pg.suite_payload("samples/selenium-suite", "out/demo")}}
        with self.assertRaises(Exception) as caught:
            run(guard.guard_run(VISITOR, body))
        self.assertIn("root", str(caught.exception))

    def test_an_uploaded_suite_is_something_the_guard_accepts(self):
        """The other half of the same contract, and the one that makes uploads work.

        `source_tree` carries the folder as text, so nothing in the request
        names a path on the server — which is the only property the guard was
        ever protecting.
        """
        body = {"assistant_id": "suite", "kwargs": {"input": pg.suite_payload(
            tree={"pages/LoginPage.ts": SELENIUM, "tests/login.spec.ts": SELENIUM})}}
        self.assertTrue(run(guard.guard_run(VISITOR, body)))

    def test_a_tree_with_a_path_that_escapes_is_refused_at_the_door(self):
        body = {"assistant_id": "suite",
                "kwargs": {"input": {"source_tree": {"../../etc/cron.d/x": "boom"}}}}
        with self.assertRaises(Exception) as caught:
            run(guard.guard_run(VISITOR, body))
        self.assertIn("relative path", str(caught.exception))


class SuitePlanTests(unittest.TestCase):
    """The wave plan on screen, and the four refusals that come before a model."""

    SAMPLE = str(env.REPO_ROOT / "samples" / "selenium-suite")

    def test_the_plan_is_the_scan_it_claims_to_be(self):
        # The page draws this before spending anything, so it has to be the
        # same plan the graph will follow, not an approximation of it.
        manifest = suite.scan(Path(self.SAMPLE))
        plan = pg.plan_suite(self.SAMPLE)
        self.assertEqual(plan.convert, [f.path for f in manifest.convertible])
        self.assertEqual(len(plan.waves), len(manifest.waves))
        self.assertEqual(plan.waves[0], list(manifest.waves[0]))
        self.assertIn(f"{len(manifest.convertible)} file(s)", plan.line)

    def test_only_narrows_the_plan_and_empties_the_waves_it_empties(self):
        plan = pg.plan_suite(self.SAMPLE, ["pages/*.ts"])
        self.assertTrue(plan.convert)
        self.assertTrue(all(p.startswith("pages/") for p in plan.convert))
        # A wave with nothing left in it is not shown as an empty wave.
        self.assertTrue(all(wave for wave in plan.waves))

    def test_a_bare_filename_is_a_pattern_too(self):
        self.assertEqual(pg.plan_suite(self.SAMPLE, ["LoginPage.ts"]).convert,
                         ["pages/LoginPage.ts"])

    def test_the_plan_says_what_it_found_not_just_how_many(self):
        plan = pg.plan_suite(self.SAMPLE)
        self.assertEqual(plan.found,
                         "Found 6 page objects and 6 Selenium test files (8 tests)")
        self.assertEqual(plan.counts["page_objects"], 6)
        self.assertEqual(plan.counts["tests"], 6)
        self.assertEqual(plan.counts["cases"], 8)

    def test_one_of_each_is_said_in_the_singular(self):
        plan = pg.plan_suite(self.SAMPLE, ["LoginPage.ts", "login.spec.ts"])
        self.assertEqual(plan.found,
                         "Found 1 page object and 1 Selenium test file (2 tests)")

    def test_a_filter_that_leaves_only_page_objects_says_only_that(self):
        # The counts are of what will be converted, not of what was scanned:
        # a plan that charges for six files must not claim to have found twelve.
        plan = pg.plan_suite(self.SAMPLE, ["pages/*.ts"])
        self.assertEqual(plan.found, "Found 6 page objects")
        self.assertEqual(plan.counts["tests"], 0)

    def test_each_wave_is_named_by_what_is_in_it(self):
        plan = pg.plan_suite(self.SAMPLE)
        self.assertEqual(plan.wave_lines, ["6 page objects", "6 test files (8 tests)"])
        self.assertEqual(pg.wave_label(plan, 1),
                         "Wave 1 of 2 · converting 6 page objects to Playwright")
        self.assertEqual(pg.wave_label(plan, 2),
                         "Wave 2 of 2 · converting 6 test files (8 tests) to Playwright")

    def test_the_lap_past_the_last_wave_says_nothing(self):
        # `next_wave` is a loop counter: the graph runs it once per wave and once
        # more, to find there is none left. The old fixed label announced that
        # non-existent wave right before the report appeared.
        plan = pg.plan_suite(self.SAMPLE)
        self.assertEqual(pg.wave_label(plan, 3), "")
        self.assertEqual(pg.wave_label(plan, 0), "")

    def test_a_single_wave_is_not_numbered(self):
        plan = pg.plan_suite(self.SAMPLE, ["pages/*.ts"])
        self.assertEqual(len(plan.waves), 1)
        self.assertEqual(pg.wave_label(plan, 1), "converting 6 page objects to Playwright")

    def test_the_labels_no_longer_carry_a_fixed_next_wave(self):
        self.assertNotIn("next_wave", pg.SUITE_NODE_LABELS)

    def test_the_output_folder_may_not_be_the_suite_or_inside_it(self):
        self.assertIn("different", pg.check_suite(self.SAMPLE, self.SAMPLE))
        self.assertIn("inside", pg.check_suite(self.SAMPLE, self.SAMPLE + "/out"))

    def test_a_folder_that_is_not_there_is_said_so_before_anything_is_sent(self):
        with TemporaryDirectory() as tmp:
            missing = str(Path(tmp) / "nope")
            self.assertIn("not a folder", pg.check_suite(missing, tmp + "/out"))

    def test_an_only_that_matches_nothing_is_a_complaint_not_an_empty_run(self):
        self.assertIn("Nothing", pg.check_suite(self.SAMPLE, "out/x", ["*.java"]))

    def test_an_empty_folder_says_what_it_found(self):
        with TemporaryDirectory() as tmp:
            (Path(tmp) / "suite").mkdir()
            complaint = pg.check_suite(str(Path(tmp) / "suite"), str(Path(tmp) / "out"))
            self.assertIn("no TypeScript Selenium files", complaint)

    def test_a_good_run_has_nothing_to_complain_about(self):
        self.assertEqual(pg.check_suite(self.SAMPLE, "out/playground-suite"), "")


class SuiteRequestTests(unittest.TestCase):
    """What goes on the wire: paths, a context and the two limits."""

    def test_paths_go_as_paths_because_this_backend_is_this_machine(self):
        request = pg.suite_payload("samples/selenium-suite", "out/demo", ["pages/*.ts"])
        self.assertTrue(request["root"].endswith("samples/selenium-suite"))
        self.assertEqual(request["only"], ["pages/*.ts"])
        self.assertEqual(request["report_path"], "")
        self.assertNotIn("source_tree", request)
        # The manifest stays here. Sending it would mean serialising a tree of
        # frozen dataclasses to save one directory walk.
        self.assertNotIn("manifest", request)

    def test_an_uploaded_tree_goes_as_text_and_names_no_path_at_all(self):
        # The property the guard checks: not "the paths are safe" but "there
        # are no server paths in this request".
        request = pg.suite_payload(tree={"pages/A.ts": "x"}, only=["*.ts"])
        self.assertEqual(request["source_tree"], {"pages/A.ts": "x"})
        self.assertNotIn("root", request)
        self.assertNotIn("out_root", request)
        self.assertEqual(set(request) & set(guard.FORBIDDEN_INPUTS), set())

    def test_blank_patterns_are_dropped_rather_than_sent(self):
        self.assertEqual(pg.suite_payload("a", "b", ["", "   ", "x.ts"])["only"], ["x.ts"])

    def test_an_unset_model_is_left_to_the_server(self):
        # "leave it alone" has to be expressible, or the form's defaults quietly
        # overrule .env on every run.
        self.assertEqual(pg.suite_context(), {"user_id": ""})
        context = pg.suite_context(model="openai:gpt-5.4", max_attempts=2, user_id="varun")
        self.assertEqual(context, {"model": "openai:gpt-5.4", "max_attempts": 2,
                                   "user_id": "varun"})

    def test_the_context_is_something_suite_settings_accepts(self):
        # It travels as JSON and is rebuilt on the far side; a key the dataclass
        # does not have is a TypeError in the graph, not here.
        settings = suite_graph.settings(SimpleNamespace(
            context=pg.suite_context(model="openai:gpt-5.4", max_attempts=2)))
        self.assertEqual(settings.model, "openai:gpt-5.4")
        self.assertEqual(settings.max_attempts, 2)

    def test_the_limits_are_the_same_two_the_cli_sends(self):
        """Pinned against `cli.suite_run_config`, which is where they were decided.

        Two copies of the recursion arithmetic is exactly the kind of thing that
        drifts silently and then fails a forty-file suite at wave nine.
        """
        from selenium2playwright import cli

        theirs = cli.suite_run_config({"actor": "m", "critic": "m"}, 3, waves=5, parallel=4)
        ours = pg.suite_config(waves=5, parallel=4)
        self.assertEqual(ours["max_concurrency"], theirs["max_concurrency"])
        self.assertEqual(ours["recursion_limit"], theirs["recursion_limit"])


def outcome_dict(path: str, wave: int = 1, status: str = "passed", **overrides) -> dict:
    """A `FileOutcome` as it actually arrives: JSON, with the tuples as lists."""
    row = {"path": path, "wave": wave, "status": status, "attempts": 1,
           "reason": "all gates passed", "gates": [["compile", True], ["residue", True]],
           "critic": "pass", "todos": [], "notes": [], "written": f"out/{path}",
           "seconds": 12.5, "errors": []}
    row.update(overrides)
    return row


class SuiteRowTests(unittest.TestCase):
    """Reading outcomes that are dataclasses locally and dicts over HTTP."""

    def test_a_dict_and_a_dataclass_read_the_same(self):
        posted = pg.file_row(outcome_dict("pages/LoginPage.ts"))
        direct = pg.file_row(suite_graph.FileOutcome(
            path="pages/LoginPage.ts", wave=1, status="passed", attempts=1,
            reason="all gates passed", gates=(("compile", True), ("residue", True)),
            critic="pass", written="out/pages/LoginPage.ts", seconds=12.5))
        self.assertEqual(posted, direct)
        self.assertTrue(posted.ok)
        self.assertEqual(posted.gates_line, "2/2")

    def test_files_tick_off_as_the_fan_out_finishes_them(self):
        """The live half: one `convert_file` update per file, in finish order."""
        client = FakeClient([
            Chunk("updates", {"plan": {"waves": [["a.ts"]]}}),
            Chunk("updates", {"convert_file": {"outcomes": [outcome_dict("pages/B.ts")]}}),
            Chunk("updates", {"convert_file": {"outcomes": [
                outcome_dict("pages/A.ts", status="needs-review")]}}),
        ])
        landed = [row
                  for update in pg.stream(client, "t", {}, assistant="suite")
                  if update.kind == "node"
                  for row in pg.rows_in(update.update)]
        self.assertEqual([r.path for r in landed], ["pages/B.ts", "pages/A.ts"])
        self.assertEqual([r.status for r in landed], ["passed", "needs-review"])

    def test_a_node_that_wrote_no_outcomes_produces_no_rows(self):
        self.assertEqual(pg.rows_in({"wave": 2}), [])

    def test_the_suite_assistant_is_the_one_asked_for(self):
        client = FakeClient([])
        list(pg.stream(client, "t", {}, assistant="suite"))
        self.assertEqual(client.calls[0]["assistant"], "suite")


def assembly_dict(**overrides) -> dict:
    """An `assemble.Assembly` as JSON, the way `values` streaming delivers it."""
    built = {
        "tree": {"gate": "compile", "passed": True, "findings": [], "tool_output": ""},
        "tree_error": "",
        "files": 12,
        "ledgers": [{"path": "pages/LoginPage.ts", "added": [], "note": "", "changes": [
            {"kind": "member", "name": "LoginPage.login", "verdict": "kept",
             "counterpart": "", "reason": ""},
            {"kind": "member", "name": "LoginPage.getFlashText", "verdict": "renamed",
             "counterpart": "flashMessage", "reason": ""},
        ]}],
        "todos": [{"text": "confirm the baseURL", "places": ["pages/LoginPage.ts:14",
                                                             "tests/login.spec.ts"]}],
        "scorecard": {}, "notes": [], "markdown": "# Conversion report\n",
        "report_path": "out/demo/conversion-report.md",
    }
    built.update(overrides)
    return built


class SuiteResultTests(unittest.TestCase):
    """The 9.3 assembly, which is the half the per-file rows cannot report."""

    def test_rows_come_back_in_plan_order_not_finish_order(self):
        state = {"outcomes": [outcome_dict("tests/z.spec.ts", wave=2),
                              outcome_dict("pages/B.ts"), outcome_dict("pages/A.ts")]}
        result = pg.suite_result(state)
        self.assertEqual([r.path for r in result.rows],
                         ["pages/A.ts", "pages/B.ts", "tests/z.spec.ts"])

    def test_a_run_that_never_assembled_still_shows_what_it_converted(self):
        # A suite that fell over in wave 2 has outcomes and no assembly. The
        # rows it did produce are the most useful thing on the screen.
        result = pg.suite_result({"outcomes": [outcome_dict("pages/A.ts")], "elapsed": 9.0})
        self.assertFalse(result.assembled)
        self.assertFalse(result.compiles)
        self.assertEqual(len(result.rows), 1)
        self.assertIn("tree not compiled", result.headline)

    def test_the_assembly_is_read_out_of_the_final_state(self):
        result = pg.suite_result({"outcomes": [outcome_dict("pages/LoginPage.ts")],
                                  "elapsed": 83.1, "assembly": assembly_dict()})
        self.assertTrue(result.assembled)
        self.assertTrue(result.compiles)
        self.assertEqual((result.kept, result.renamed, result.removed), (1, 1, 0))
        self.assertEqual(result.tree_files, 12)
        self.assertEqual(result.todos[0][0], "confirm the baseURL")
        self.assertEqual(len(result.todos[0][1]), 2)
        self.assertTrue(result.report_path.endswith("conversion-report.md"))
        self.assertIn("83.1s", result.headline)

    def test_twelve_green_rows_are_not_a_pass_if_the_tree_does_not_build(self):
        """The whole argument for step 9.3, as one assertion.

        Every per-file gate verdict is a local claim: this file compiled against
        the companions it happened to import. A page object whose method two
        specs call differently passes every row and still leaves a folder that
        does not build.
        """
        broken = assembly_dict(tree={
            "gate": "compile", "passed": False, "tool_output": "",
            "findings": [{"gate": "compile", "file": "tests/login.spec.ts", "line": 8,
                          "code": "TS2554", "message": "Expected 2 arguments, but got 1."}]})
        state = {"outcomes": [outcome_dict("pages/LoginPage.ts"),
                              outcome_dict("tests/login.spec.ts", wave=2)],
                 "assembly": broken}
        result = pg.suite_result(state)
        self.assertEqual(result.totals["passed"], 2)
        self.assertFalse(result.passed)
        self.assertIn("tests/login.spec.ts:8 TS2554", result.tree_findings[0])
        self.assertIn("tree does not compile", result.headline)

    def test_a_compile_that_could_not_run_is_not_green_either(self):
        # Unknown is never a pass — `assemble.Assembly.compiles` makes the same
        # call for the same reason.
        result = pg.suite_result({"outcomes": [outcome_dict("pages/A.ts")],
                                  "assembly": assembly_dict(tree=None,
                                                            tree_error="tsc not found")})
        self.assertFalse(result.passed)
        self.assertEqual(result.tree_error, "tsc not found")

    def test_a_removal_with_no_reason_is_counted_as_unexplained(self):
        # The report's loudest line: a public method disappeared and the model
        # never said why.
        ledgers = [{"path": "pages/LoginPage.ts", "added": [], "note": "", "changes": [
            {"kind": "member", "name": "LoginPage.dismiss", "verdict": "removed",
             "counterpart": "", "reason": ""},
            {"kind": "member", "name": "LoginPage.waitFor", "verdict": "removed",
             "counterpart": "", "reason": "Playwright auto-waits."}]}]
        result = pg.suite_result({"outcomes": [], "assembly": assembly_dict(ledgers=ledgers)})
        self.assertEqual((result.removed, result.unexplained), (2, 1))
        self.assertIn("no reason given", [loss[3] for loss in result.losses])

    def test_a_needs_review_file_keeps_the_suite_out_of_green(self):
        result = pg.suite_result({"outcomes": [
            outcome_dict("pages/A.ts"),
            outcome_dict("pages/B.ts", status="needs-review")], "assembly": assembly_dict()})
        self.assertTrue(result.compiles)
        self.assertFalse(result.passed)
        self.assertEqual(result.totals, {"passed": 1, "needs-review": 1,
                                         "refused": 0, "failed": 0})


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


class SuiteKeyTests(unittest.TestCase):
    """The one place the owner's key is used, and why it has to be."""

    def test_a_local_folder_run_calls_as_the_owner(self):
        # `guard.guard_run` refuses `root` for anyone without the owner
        # permission, so a folder run that authenticated as a demo visitor would
        # be offered on screen and then 403 — the worst of both.
        with patch.dict(os.environ, {"S2P_API_KEY": "owner-key",
                                     "S2P_DEMO_KEY": "demo-key"}, clear=False):
            self.assertEqual(pg.suite_key("http://127.0.0.1:2024"), "owner-key")
            self.assertEqual(pg.demo_key(), "demo-key")

    def test_a_remote_upload_never_calls_as_the_owner(self):
        """The one that would quietly cost money if it were wrong.

        An uploaded suite is metered per file, and the meter is only reached on
        the demo path — `guard_run` returns True immediately for an owner. A
        page that sent the owner key to a public deployment would spend the
        whole day's budget past every guardrail step 10.4 built.
        """
        with patch.dict(os.environ, {"S2P_API_KEY": "owner-key",
                                     "S2P_DEMO_KEY": "demo-key"}, clear=False):
            self.assertEqual(pg.suite_key("https://s2p.fly.dev"), "demo-key")

    def test_the_owner_is_the_one_the_guard_lets_through(self):
        owner = FakeCtx("owner", ["owner"])
        body = {"assistant_id": "suite",
                "kwargs": {"input": pg.suite_payload("samples/selenium-suite", "out/demo")}}
        self.assertTrue(run(guard.guard_run(owner, body)))

    def test_with_no_owner_key_it_falls_back_rather_than_sending_nothing(self):
        # A server run with S2P_AUTH=off ignores the token entirely; sending the
        # demo key there is harmless and means one fewer thing to configure.
        with patch.dict(os.environ, {"S2P_DEMO_KEY": "demo-key"}, clear=False):
            os.environ.pop("S2P_API_KEY", None)
            self.assertEqual(pg.suite_key("http://127.0.0.1:2024"), "demo-key")


class SuiteStreamTests(unittest.TestCase):
    """The two things a fan-out cannot run without, and the default that is left alone."""

    def test_a_single_conversion_still_sends_neither(self):
        # The server's own defaults are the right answer for one file, and
        # sending `config=None` is not the same as not sending it.
        client = FakeClient([])
        list(pg.stream(client, "t", {}))
        self.assertNotIn("config", client.calls[0])
        self.assertNotIn("context", client.calls[0])

    def test_a_suite_run_carries_its_limits_and_its_models(self):
        client = FakeClient([])
        list(pg.stream(client, "t", {}, assistant="suite",
                       config=pg.suite_config(waves=2, parallel=4),
                       context=pg.suite_context(model="openai:gpt-5.4")))
        call = client.calls[0]
        self.assertEqual(call["assistant"], "suite")
        self.assertEqual(call["config"]["max_concurrency"], 4)
        self.assertEqual(call["config"]["recursion_limit"], 10)
        self.assertEqual(call["context"]["model"], "openai:gpt-5.4")

    def test_the_committed_report_is_where_the_tab_looks_for_it(self):
        # The blocked tab renders this file. A rename would turn the one honest
        # answer to "does it scale past one file" into a missing expander.
        self.assertTrue(pg.SUITE_REPORT.exists(), pg.SUITE_REPORT)


def zipped(files: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, text in files.items():
            archive.writestr(name, text)
    return buffer.getvalue()


def upload(name: str, data) -> SimpleNamespace:
    """Anything with .name and .getvalue() — Streamlit's UploadedFile, or this."""
    raw = data if isinstance(data, bytes) else data.encode("utf-8")
    return SimpleNamespace(name=name, getvalue=lambda: raw)


class UploadTests(unittest.TestCase):
    """Bytes a browser hands over, turned into the two shapes the graph takes."""

    def test_one_file_comes_in_as_name_and_text(self):
        name, text = pg.read_upload(upload("LoginPage.ts", SELENIUM))
        self.assertEqual(name, "LoginPage.ts")
        self.assertEqual(text, SELENIUM)

    def test_a_byte_order_mark_is_stripped_rather_than_sent(self):
        # Invisible in an editor and a syntax error to tsc — it would come back
        # as a compile-gate finding about the visitor's own file.
        _, text = pg.read_upload(upload("A.ts", "﻿export class A {}"))
        self.assertEqual(text, "export class A {}")

    def test_something_that_is_not_text_is_refused_with_a_sentence(self):
        with self.assertRaises(ValueError) as caught:
            pg.read_upload(upload("logo.png", b"\x89PNG\r\n\x1a\n\xff\xfe"))
        self.assertIn("not UTF-8", str(caught.exception))

    def test_a_zip_of_a_folder_loses_the_wrapper_directory(self):
        # Zipping a folder gives you `selenium-suite/pages/LoginPage.ts`, and
        # that leading directory is not part of anybody's import paths.
        tree = pg.tree_from_zip(zipped({
            "selenium-suite/pages/LoginPage.ts": SELENIUM,
            "selenium-suite/tests/login.spec.ts": SELENIUM,
        }))
        self.assertEqual(sorted(tree), ["pages/LoginPage.ts", "tests/login.spec.ts"])

    def test_a_zip_with_no_common_wrapper_is_left_alone(self):
        tree = pg.tree_from_zip(zipped({"pages/A.ts": "a", "B.ts": "b"}))
        self.assertEqual(sorted(tree), ["B.ts", "pages/A.ts"])

    def test_a_single_file_zip_keeps_its_folder(self):
        # With one file there is no evidence of a wrapper, and stripping the
        # only directory would rewrite a real path.
        self.assertEqual(list(pg.tree_from_zip(zipped({"pages/A.ts": "a"}))),
                         ["pages/A.ts"])

    def test_junk_and_dependencies_are_dropped_not_refused(self):
        # node_modules is the one that matters: a suite zipped with its
        # dependencies is tens of thousands of files, every one of them charged.
        tree = pg.tree_from_zip(zipped({
            "s/pages/A.ts": "a", "s/pages/B.ts": "b",
            "s/node_modules/x/index.ts": "junk",
            "s/.git/config": "junk", "__MACOSX/._A.ts": "junk", "s/dist/A.ts": "junk",
        }))
        self.assertEqual(sorted(tree), ["pages/A.ts", "pages/B.ts"])

    def test_non_source_files_in_a_zip_are_ignored(self):
        tree = pg.tree_from_zip(zipped(
            {"a/A.ts": "a", "a/B.ts": "b", "a/README.md": "#", "a/x.png": "c"}))
        self.assertEqual(sorted(tree), ["A.ts", "B.ts"])

    def test_loose_files_and_a_zip_are_the_same_gesture(self):
        loose, complaint = pg.tree_from_uploads(
            [upload("LoginPage.ts", SELENIUM), upload("login.spec.ts", SELENIUM)])
        self.assertEqual(complaint, "")
        self.assertEqual(sorted(loose), ["LoginPage.ts", "login.spec.ts"])

    def test_a_loose_file_keeps_only_its_base_name(self):
        # A browser does not send the directory a file came from, which is why
        # the zip route exists and is the one to prefer for folders.
        tree, _ = pg.tree_from_uploads([upload("/Users/x/pages/A.ts", SELENIUM)])
        self.assertEqual(list(tree), ["A.ts"])

    def test_an_upload_that_cannot_be_read_answers_with_a_complaint(self):
        tree, complaint = pg.tree_from_uploads([upload("a.ts", b"\xff\xfe\x00")])
        self.assertEqual(tree, {})
        self.assertIn("not UTF-8", complaint)

    def test_nothing_uploaded_is_not_an_error_yet(self):
        # An empty uploader on first render is not somebody doing it wrong.
        self.assertEqual(pg.tree_from_uploads([]), ({}, ""))

    def test_a_tree_that_breaks_the_rules_says_which_rule(self):
        big = {f"f{i}.ts": "x" for i in range(suite.MAX_TREE_FILES + 1)}
        _, complaint = pg.tree_from_uploads([upload(n, t) for n, t in big.items()])
        self.assertIn(str(suite.MAX_TREE_FILES), complaint)

    def test_an_uploaded_tree_gets_the_same_plan_preview_as_a_folder(self):
        tree = {"pages/LoginPage.ts": SELENIUM,
                "tests/login.spec.ts":
                    "import { LoginPage } from '../pages/LoginPage';\n" + SELENIUM}
        plan = pg.plan_tree(tree)
        self.assertEqual(plan.files, 2)
        self.assertEqual(plan.billable, 2)
        self.assertIn("2 file(s)", plan.line)

    def test_the_price_is_the_conversions_not_the_files_sent(self):
        """A real repo is mostly helpers, and helpers are free.

        The page quotes what the guard will charge, and the guard charges what
        reaches a model — so twelve copied files and four Selenium files is a
        bill of four, and "Only these files" brings it down further. Before
        this, the number was the file count, and the advice to filter changed
        nothing.
        """
        tree = {f"lib/helper{i}.ts": f"export const H{i} = {i};\n" for i in range(12)}
        tree.update({f"pages/P{i}.ts": SELENIUM for i in range(4)})
        plan = pg.plan_tree(tree)
        self.assertEqual(len(plan.copied), 12)
        self.assertEqual(plan.billable, 4)
        self.assertEqual(pg.plan_tree(tree, ["pages/P1.ts"]).billable, 1)
        self.assertEqual(plan.billable, suite.conversions(tree))

    def test_unaffordable_talks_about_conversions(self):
        snapshot = {"per_visitor": {"daily": 15}, "budget": {"remaining": 41}}
        self.assertEqual(pg.affordable(snapshot, 4), "")
        self.assertIn("needs 16 conversions", pg.affordable(snapshot, 16))


class DownloadTests(unittest.TestCase):
    """Giving the suite back, which is the half an upload is useless without."""

    def test_the_converted_tree_comes_back_out_of_the_final_state(self):
        result = pg.suite_result({
            "outcomes": [outcome_dict("pages/A.ts")],
            "converted_tree": {"pages/A.ts": "converted"},
            "assembly": assembly_dict()})
        self.assertEqual(result.tree, {"pages/A.ts": "converted"})

    def test_a_folder_run_sends_nothing_back_because_it_is_already_there(self):
        result = pg.suite_result({"outcomes": [outcome_dict("pages/A.ts")],
                                  "assembly": assembly_dict()})
        self.assertEqual(result.tree, {})

    def test_the_zip_holds_the_code_and_the_report_together(self):
        # The report says which of these files still needs eyes. The two parting
        # company is how it gets ignored.
        data = pg.converted_zip({"pages/A.ts": "converted"}, "# Conversion report")
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            self.assertEqual(sorted(archive.namelist()),
                             ["conversion-report.md", "pages/A.ts"])
            self.assertEqual(archive.read("pages/A.ts").decode(), "converted")

    def test_a_zip_survives_a_round_trip_through_the_uploader(self):
        original = {"pages/A.ts": SELENIUM, "tests/a.spec.ts": SELENIUM}
        back = pg.tree_from_zip(pg.converted_zip(original))
        self.assertEqual(back, original)


class OldBackendTests(unittest.TestCase):
    def test_a_deployment_that_predates_uploads_says_so(self):
        # The one 403 that is not a rule the visitor broke: an old image has no
        # idea what source_tree is, falls through to the single-file check, and
        # asks for source_text.
        said = pg.explain_status(403, "Send the file as `source_text`. The public demo…")
        self.assertIn("Redeploy", said)

    def test_a_real_refusal_is_still_passed_through_unchanged(self):
        said = pg.explain_status(403, "`root` is not available on the public demo.")
        self.assertNotIn("Redeploy", said)
        self.assertIn("`root`", said)


class AffordabilityTests(unittest.TestCase):
    """Saying the price before the click, now that a suite has one."""

    SNAPSHOT = {"budget": {"used": 0, "limit": 41, "remaining": 41},
                "per_visitor": {"daily": 10, "burst": 3, "window_s": 60}}

    def test_a_suite_inside_both_ceilings_is_fine(self):
        self.assertEqual(pg.affordable(self.SNAPSHOT, 8), "")

    def test_a_suite_bigger_than_the_per_visitor_cap_can_never_run_here(self):
        # The advice has to be "convert fewer at a time", not "come back
        # tomorrow": this one is a shape, not a balance.
        said = pg.affordable(self.SNAPSHOT, 12)
        self.assertIn("12 conversions", said)
        self.assertIn("10 conversions per visitor", said)
        self.assertIn("Only these files", said)

    def test_a_suite_bigger_than_what_is_left_today_is_a_different_sentence(self):
        snapshot = {**self.SNAPSHOT, "budget": {"used": 38, "limit": 41, "remaining": 3}}
        said = pg.affordable(snapshot, 8)
        self.assertIn("3 are left", said)
        self.assertIn("midnight UTC", said)

    def test_a_backend_that_did_not_answer_is_not_treated_as_a_refusal(self):
        # `fetch_limits` returns {"error": …} when it cannot reach the meter.
        # Blocking the button on that would turn a sidebar warning into a dead
        # page; the guard is still there to say no.
        self.assertEqual(pg.affordable({"error": "The backend did not answer."}, 12), "")

    def test_nothing_to_convert_costs_nothing(self):
        self.assertEqual(pg.affordable(self.SNAPSHOT, 0), "")

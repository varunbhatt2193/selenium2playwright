"""Step 9.2 — the suite graph: fan-out with Send, and the reducer that joins it.

Two kinds of test live here. Most of them replace the per-file subgraph with a
scripted stand-in, because what is under test is the *orchestration* — how many
branches ran, when, in what order, with which companions, and what happened when
one of them fell over. One test at the bottom runs the real graph and the real
four gates on two real files, so the wiring is proved against the thing it
actually drives.

Nothing here reaches the network.
"""

import io
import json
import operator
import os
import threading
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Annotated, TypedDict, get_type_hints
from unittest.mock import Mock, patch

from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda
from langgraph.errors import InvalidUpdateError
from langgraph.graph import END, START, StateGraph

from selenium2playwright import cli, graph, suite, suite_graph
from selenium2playwright.schemas import ConversionReport, ConversionResult, Critique, ValidationReport

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "samples/selenium-suite"
GOLDEN = ROOT / "samples/playwright-golden"

# The 9.1 toy suite again: three convertible files in a two-deep chain, one
# support file to copy, one Cypress file to skip.
TOY = {
    "pages/BasePage.ts": (
        'import { WebDriver, By } from "selenium-webdriver";\n'
        "export class BasePage {\n"
        "  constructor(protected driver: WebDriver) {}\n"
        "}\n"),
    "pages/LoginPage.ts": (
        'import { WebDriver } from "selenium-webdriver";\n'
        'import { BasePage } from "./BasePage";\n'
        "export class LoginPage extends BasePage {\n"
        "  async open() { await this.driver.get('/login'); }\n"
        "}\n"),
    "tests/login.spec.ts": (
        'import { Builder } from "selenium-webdriver";\n'
        'import { LoginPage } from "../pages/LoginPage";\n'
        'import { USERS } from "../support/users";\n'
        'describe("Login", () => { it("works", async () => { new Builder(); }); });\n'),
    "support/users.ts": 'export const USERS = { tomsmith: "secret" };\n',
    "legacy/old.cy.ts": 'describe("old", () => { cy.visit("/login"); });\n',
}

PASSED = ValidationReport(gate="compile", passed=True)


def build(directory: Path, files: dict[str, str]) -> Path:
    for name, source in files.items():
        target = directory / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source, encoding="utf-8")
    return directory


def report(status="passed", code="export const converted = true;\n", todos=(), attempts=1):
    return ConversionReport(
        status=status, attempts=attempts, reason="scripted",
        result=ConversionResult(code=code, todos=list(todos)),
        validation=[ValidationReport(gate=gate, passed=status == "passed") for gate in suite_graph.GATES],
        critique=Critique(verdict="pass", fixes=[]) if status == "passed"
        else Critique(verdict="revise", fixes=["scripted fix"]))


class FakeChild:
    """A stand-in for the single-file graph that records how it was called."""

    def __init__(self, respond, log, delay=0.0):
        self.respond, self.log, self.delay = respond, log, delay

    def invoke(self, inputs, config=None, context=None):
        started = time.time()
        if self.delay:
            time.sleep(self.delay)
        self.log.append({"inputs": inputs, "context": context, "config": config,
                         "started": started, "finished": time.time(),
                         "thread": threading.current_thread().name})
        return self.respond(inputs)


class FanOutTests(unittest.TestCase):
    """The orchestration, with the per-file graph scripted."""

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = build(Path(self.tmp.name) / "src", TOY)
        self.out = Path(self.tmp.name) / "out"
        self.log = []

    def child(self, respond=None, delay=0.0):
        """Patch the subgraph builder; every branch gets its own FakeChild."""
        respond = respond or (lambda inputs: {"status": "converted", "report": report(),
                                              "iteration": 1})
        return patch.object(suite_graph.single, "build_graph",
                            side_effect=lambda *a, **k: FakeChild(respond, self.log, delay))

    def run_suite(self, respond=None, delay=0.0, **inputs):
        with self.child(respond, delay):
            return suite_graph.build_suite_graph().invoke(
                {"root": str(self.root), "out_root": str(self.out), **inputs},
                config={"max_concurrency": 8, "recursion_limit": 20})

    def test_every_convertible_file_gets_one_branch_and_the_reducer_keeps_them_all(self):
        final = self.run_suite()
        self.assertEqual([o.path for o in suite_graph.ordered(final)],
                         ["pages/BasePage.ts", "pages/LoginPage.ts", "tests/login.spec.ts"])
        self.assertEqual(suite_graph.totals(suite_graph.ordered(final))["passed"], 3)

    def test_a_wave_runs_at_the_same_time_and_the_next_one_waits_for_it(self):
        """The whole point of Send: wave 1 overlaps, wave 2 starts after it ends."""
        # Three page objects that import nothing — one wave, three branches —
        # and one spec that imports one of them, so it cannot be in that wave.
        page = ('import { WebDriver } from "selenium-webdriver";\n'
                "export class %sPage { constructor(d: WebDriver) {} }\n")
        self.root = build(Path(self.tmp.name) / "flat", {
            **{f"pages/{name}Page.ts": page % name for name in ("Login", "Cart", "Search")},
            "tests/login.spec.ts": ('import { Builder } from "selenium-webdriver";\n'
                                    'import { LoginPage } from "../pages/LoginPage";\n'
                                    'describe("Login", () => { it("works", () => new Builder()); });\n'),
        })
        started = time.time()
        final = self.run_suite(delay=0.4)
        elapsed = time.time() - started

        self.assertEqual([o.wave for o in suite_graph.ordered(final)], [1, 1, 1, 2])
        first = [c for c in self.log if "spec" not in c["inputs"]["source_path"]]
        self.assertEqual(len(first), 3)
        # They overlap: the last one started before the first one finished, and
        # they were not all on the same thread.
        self.assertLess(max(c["started"] for c in first), min(c["finished"] for c in first))
        self.assertGreater(len({c["thread"] for c in first}), 1)
        # The spec imports a page object, so its wave cannot start early.
        spec = next(c for c in self.log if "spec" in c["inputs"]["source_path"])
        self.assertGreaterEqual(spec["started"], max(c["finished"] for c in first) - 0.01)
        # Serial would be four sleeps; two waves is two.
        self.assertLess(elapsed, 4 * 0.4)

    def test_a_later_wave_is_given_the_converted_companions_from_the_output_tree(self):
        self.run_suite()
        calls = {Path(c["inputs"]["source_path"]).name: c for c in self.log}
        self.assertEqual(calls["BasePage.ts"]["inputs"]["context_paths"], [])
        self.assertEqual(calls["LoginPage.ts"]["inputs"]["context_paths"],
                         [str(self.out / "pages/BasePage.ts")])
        # The spec's companions are the *converted* page object and the copied
        # support file, both read from the output tree, never from the source.
        self.assertEqual(sorted(calls["login.spec.ts"]["inputs"]["context_paths"]),
                         sorted([str(self.out / "pages/LoginPage.ts"),
                                 str(self.out / "support/users.ts")]))

    def test_converted_files_are_written_support_is_copied_and_skipped_is_absent(self):
        final = self.run_suite()
        self.assertEqual((self.out / "pages/LoginPage.ts").read_text(),
                         "export const converted = true;\n")
        self.assertEqual((self.out / "support/users.ts").read_text(), TOY["support/users.ts"])
        self.assertEqual(final["copied"], ["support/users.ts"])
        self.assertFalse((self.out / "legacy/old.cy.ts").exists())

    def test_a_file_that_needs_review_is_still_written_because_the_next_wave_imports_it(self):
        def respond(inputs):
            if "BasePage" in inputs["source_path"]:
                return {"status": "converted", "iteration": 3,
                        "report": report("needs-review", todos=["TODO(review): check the locator"],
                                         attempts=3)}
            return {"status": "converted", "report": report(), "iteration": 1}

        final = self.run_suite(respond)
        base = next(o for o in suite_graph.ordered(final) if o.path == "pages/BasePage.ts")
        self.assertEqual(base.status, "needs-review")
        self.assertEqual(base.attempts, 3)
        self.assertEqual(base.todos, ("TODO(review): check the locator",))
        self.assertTrue((self.out / "pages/BasePage.ts").exists())
        # ... and the file that imports it still receives it as context.
        login = next(c for c in self.log if "LoginPage" in c["inputs"]["source_path"])
        self.assertEqual(login["inputs"]["context_paths"], [str(self.out / "pages/BasePage.ts")])

    def test_a_file_with_no_code_writes_nothing_and_leaves_its_dependents_without_it(self):
        def respond(inputs):
            if "BasePage" in inputs["source_path"]:
                return {"status": "failed", "iteration": 1,
                        "report": ConversionReport(status="needs-review", attempts=1,
                                                   reason="the model returned nothing",
                                                   result=None, validation=[], critique=None,
                                                   errors=["empty reply"])}
            return {"status": "converted", "report": report(), "iteration": 1}

        final = self.run_suite(respond)
        base = next(o for o in suite_graph.ordered(final) if o.path == "pages/BasePage.ts")
        self.assertEqual(base.status, "failed")
        self.assertEqual(base.written, "")
        self.assertEqual(base.errors, ("empty reply",))
        self.assertFalse((self.out / "pages/BasePage.ts").exists())
        login = next(c for c in self.log if "LoginPage" in c["inputs"]["source_path"])
        self.assertEqual(login["inputs"]["context_paths"], [])

    def test_one_branch_raising_does_not_take_the_other_files_down(self):
        def respond(inputs):
            if "BasePage" in inputs["source_path"]:
                raise RuntimeError("provider is on fire")
            return {"status": "converted", "report": report(), "iteration": 1}

        final = self.run_suite(respond)
        outcomes = {o.path: o for o in suite_graph.ordered(final)}
        self.assertEqual(outcomes["pages/BasePage.ts"].status, "failed")
        self.assertIn("provider is on fire", outcomes["pages/BasePage.ts"].reason)
        self.assertEqual(outcomes["tests/login.spec.ts"].status, "passed")

    def test_only_selects_a_subset_and_empty_waves_are_not_dispatched(self):
        final = self.run_suite(only=["pages/*.ts"])
        self.assertEqual([o.path for o in suite_graph.ordered(final)],
                         ["pages/BasePage.ts", "pages/LoginPage.ts"])
        self.assertEqual(final["waves"], [["pages/BasePage.ts"], ["pages/LoginPage.ts"]])

    def test_the_run_context_reaches_every_per_file_subgraph(self):
        with self.child():
            suite_graph.build_suite_graph().invoke(
                {"root": str(self.root), "out_root": str(self.out)},
                context=suite_graph.SuiteSettings(model="openai:gpt-5.4",
                                                  critic_model="anthropic:claude-sonnet-5",
                                                  max_attempts=1, user_id="varun"),
                config={"recursion_limit": 20})
        self.assertEqual(len(self.log), 3)
        for call in self.log:
            self.assertEqual(call["context"].model, "openai:gpt-5.4")
            self.assertEqual(call["context"].critic_model, "anthropic:claude-sonnet-5")
            self.assertEqual(call["context"].max_attempts, 1)
            self.assertEqual(call["inputs"]["user_id"], "varun")
            self.assertFalse(call["inputs"]["ask_risks"])  # a suite run has nobody to ask

    def test_each_file_is_its_own_named_traced_run(self):
        self.run_suite()
        names = {c["config"]["run_name"] for c in self.log}
        self.assertEqual(names, {"convert:pages/BasePage.ts", "convert:pages/LoginPage.ts",
                                 "convert:tests/login.spec.ts"})
        # BasePage → LoginPage → the spec is a three-deep chain, so three waves.
        self.assertIn("wave:3", next(c for c in self.log
                                     if "spec" in c["inputs"]["source_path"])["config"]["tags"])


class ReducerTests(unittest.TestCase):
    """Why `Annotated[list, operator.add]` is not decoration."""

    def graph_for(self, annotation):
        state = TypedDict("S", {"out": annotation}, total=False)

        def fan(_):
            return [suite_graph.Send("one", {"n": n}) for n in (1, 2, 3)]

        builder = StateGraph(state)
        builder.add_node("start", lambda s: {})
        builder.add_node("one", lambda job: {"out": [job["n"]]})
        builder.add_edge(START, "start")
        builder.add_conditional_edges("start", fan, ["one"])
        builder.add_edge("one", END)
        return builder.compile()

    def test_without_a_reducer_parallel_branches_collide(self):
        with self.assertRaises(InvalidUpdateError):
            self.graph_for(list).invoke({})

    def test_with_operator_add_they_are_appended(self):
        final = self.graph_for(Annotated[list, operator.add]).invoke({})
        self.assertEqual(sorted(final["out"]), [1, 2, 3])

    def test_the_suite_state_declares_that_reducer_on_outcomes(self):
        """A pin: drop the annotation and the graph stops running, not just this test."""
        # get_type_hints, not __annotations__: `from __future__ import
        # annotations` leaves every annotation in that module as a string.
        hints = get_type_hints(suite_graph.SuiteState, include_extras=True)
        self.assertIn(operator.add, hints["outcomes"].__metadata__)


class HelperTests(unittest.TestCase):
    def test_selected_matches_a_relative_path_a_glob_or_a_bare_name(self):
        for pattern in ("pages/*.ts", "pages/LoginPage.ts", "LoginPage.ts", "*Page.ts"):
            with self.subTest(pattern=pattern):
                self.assertTrue(suite_graph.selected("pages/LoginPage.ts", [pattern]))
        self.assertFalse(suite_graph.selected("tests/login.spec.ts", ["pages/*.ts"]))
        self.assertTrue(suite_graph.selected("anything.ts", []))

    def test_ordered_puts_completion_order_back_into_plan_order(self):
        late = suite_graph.FileOutcome(path="a.ts", wave=1, status="passed", attempts=1, reason="")
        early = suite_graph.FileOutcome(path="b.ts", wave=1, status="passed", attempts=1, reason="")
        second = suite_graph.FileOutcome(path="a.ts", wave=2, status="passed", attempts=1, reason="")
        got = suite_graph.ordered({"outcomes": [second, late, early]})
        self.assertEqual([(o.wave, o.path) for o in got], [(1, "a.ts"), (1, "b.ts"), (2, "a.ts")])

    def test_a_refusal_is_recorded_as_an_outcome_not_an_exception(self):
        outcome = suite_graph.record({"path": "x.ts", "wave": 1, "output_path": "/nope/x.ts"},
                                     {"status": "refused", "refusal": "webdriverio is not supported"}, 1.5)
        self.assertEqual(outcome.status, "refused")
        self.assertEqual(outcome.reason, "webdriverio is not supported")
        self.assertEqual(outcome.written, "")

    def test_token_counts_are_added_up_across_files(self):
        one = {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}
        outcomes = [suite_graph.FileOutcome(path=f"{n}.ts", wave=1, status="passed", attempts=1,
                                            reason="", usage=one) for n in range(3)]
        self.assertEqual(suite_graph.suite_usage(outcomes)["total_tokens"], 45)
        self.assertIsNone(suite_graph.suite_usage(outcomes, "critic_usage"))


class CommandTests(unittest.TestCase):
    """`s2p suite` — the surface, with the per-file graph scripted."""

    def setUp(self):
        keys = patch.dict(os.environ, {"LANGSMITH_TRACING": "false", "LANGCHAIN_TRACING_V2": "false",
                                       "ANTHROPIC_API_KEY": "sk-ant-offline-test",
                                       "S2P_EMBEDDINGS": "off"})
        keys.start()
        self.addCleanup(keys.stop)
        original = cli.console._width  # rich folds cells to the terminal; pin it (see 9.1)
        cli.console.width = 100
        self.addCleanup(setattr, cli.console, "_width", original)
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = build(Path(self.tmp.name) / "src", TOY)
        self.out = Path(self.tmp.name) / "out"
        self.log = []

    def run_cli(self, *argv, respond=None):
        respond = respond or (lambda inputs: {"status": "converted", "report": report(),
                                              "iteration": 1})
        out, err = io.StringIO(), io.StringIO()
        with patch.object(suite_graph.single, "build_graph",
                          side_effect=lambda *a, **k: FakeChild(respond, self.log)), \
                redirect_stdout(out), redirect_stderr(err):
            code = cli.run(["suite", *argv])
        return code, out.getvalue(), err.getvalue()

    def test_a_clean_run_prints_a_row_per_file_and_exits_zero(self):
        code, out, err = self.run_cli(str(self.root), "--out", str(self.out))
        self.assertEqual(code, 0, err)
        self.assertEqual(out, "")  # nothing machine-readable was asked for
        for path in ("pages/BasePage.ts", "pages/LoginPage.ts", "tests/login.spec.ts"):
            self.assertRegex(err, path.replace(".", r"\.") + r"[^\n]*PASS")
        self.assertIn("copied support/users.ts unchanged", err)
        self.assertIn("skipped legacy/old.cy.ts", err)
        self.assertIn("3 file(s): 3 passed", err)

    def test_one_file_needing_review_makes_the_whole_run_exit_one(self):
        def respond(inputs):
            if "LoginPage" in inputs["source_path"]:
                return {"status": "converted", "iteration": 2,
                        "report": report("needs-review", todos=["TODO(review): confirm the URL"],
                                         attempts=2)}
            return {"status": "converted", "report": report(), "iteration": 1}

        code, _, err = self.run_cli(str(self.root), "--out", str(self.out), respond=respond)
        self.assertEqual(code, 1)
        self.assertIn("2 passed", err)
        self.assertIn("1 needs-review", err)
        self.assertIn("pages/LoginPage.ts: TODO(review): confirm the URL", err)

    def test_json_puts_the_whole_run_on_stdout_and_nothing_else(self):
        code, out, err = self.run_cli(str(self.root), "--out", str(self.out), "--json")
        self.assertEqual(code, 0)
        document = json.loads(out)
        self.assertEqual(document["schema"], suite_graph.RUN_SCHEMA)
        self.assertEqual(document["counts"]["passed"], 3)
        self.assertEqual([f["path"] for f in document["files"]],
                         ["pages/BasePage.ts", "pages/LoginPage.ts", "tests/login.spec.ts"])
        self.assertEqual(document["files"][0]["gates"],
                         {gate: True for gate in suite_graph.GATES})
        self.assertEqual(document["copied"], ["support/users.ts"])
        self.assertEqual([s["path"] for s in document["skipped"]], ["legacy/old.cy.ts"])
        self.assertNotIn("PASS", out)  # the table stayed on stderr, as in `s2p convert`
        self.assertRegex(err, r"pages/LoginPage\.ts[^\n]*PASS")

    def test_only_narrows_the_run_and_a_pattern_that_matches_nothing_is_a_usage_error(self):
        code, _, err = self.run_cli(str(self.root), "--out", str(self.out), "--only", "pages/*.ts")
        self.assertEqual(code, 0)
        self.assertIn("2 file(s): 2 passed", err)
        code, _, err = self.run_cli(str(self.root), "--out", str(self.out), "--only", "nope/*.ts")
        self.assertEqual(code, 2)
        self.assertIn("--only matched none", err.replace("\n", " "))

    def test_writing_into_the_suite_being_read_is_refused(self):
        for destination in (self.root, self.root / "converted"):
            with self.subTest(out=destination):
                code, _, err = self.run_cli(str(self.root), "--out", str(destination))
                self.assertEqual(code, 2)
        self.assertEqual(self.log, [])  # refused before a single file was converted

    def test_a_folder_with_nothing_convertible_exits_one_and_says_so(self):
        empty = build(Path(self.tmp.name) / "empty", {"a.cy.ts": 'describe("x", () => cy.visit("/"));\n'})
        code, _, err = self.run_cli(str(empty), "--out", str(self.out))
        self.assertEqual(code, 1)
        self.assertIn("Nothing to convert", err)


class RealGraphTests(unittest.TestCase):
    """One end-to-end run: the real subgraph, the real gates, a scripted model.

    Two files in two waves, so the thing being proved is the one thing a
    stand-in cannot prove — that the test in wave 2 compiles against the
    Playwright page object wave 1 wrote, from the output tree, for real.
    """

    def setUp(self):
        keys = patch.dict(os.environ, {"LANGSMITH_TRACING": "false", "LANGCHAIN_TRACING_V2": "false",
                                       "ANTHROPIC_API_KEY": "sk-ant-offline-test"})
        keys.start()
        self.addCleanup(keys.stop)
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def golden_model(self):
        """Answer with the golden file whose source path the prompt names."""
        wanted = {str(SAMPLE / rel): (GOLDEN / rel).read_text()
                  for rel in ("pages/LoginPage.ts", "tests/login.spec.ts")}

        def structured(schema, **kwargs):
            def respond(prompt):
                text = str(prompt)
                if schema is Critique:
                    parsed = Critique(verdict="pass", fixes=[])
                else:
                    match = next(code for path, code in wanted.items() if path in text)
                    parsed = ConversionResult(code=match)
                return {"parsed": parsed, "raw": AIMessage(content=""), "parsing_error": None}
            return RunnableLambda(respond)

        model = Mock()
        model.with_structured_output.side_effect = structured
        return patch.object(graph, "make_model", return_value=model)

    def test_a_real_two_wave_run_compiles_the_spec_against_the_converted_page_object(self):
        out = Path(self.tmp.name) / "converted"
        with self.golden_model():
            final = suite_graph.build_suite_graph().invoke(
                {"root": str(SAMPLE), "out_root": str(out),
                 "only": ["pages/LoginPage.ts", "tests/login.spec.ts"]},
                context=suite_graph.SuiteSettings(max_attempts=1),
                config={"recursion_limit": 20})
        outcomes = suite_graph.ordered(final)
        self.assertEqual([(o.wave, o.path) for o in outcomes],
                         [(1, "pages/LoginPage.ts"), (2, "tests/login.spec.ts")])
        for outcome in outcomes:
            with self.subTest(file=outcome.path):
                self.assertEqual(dict(outcome.gates), {gate: True for gate in suite_graph.GATES},
                                 outcome.reason)
                self.assertEqual(outcome.critic, "pass")
                self.assertEqual(outcome.written, str(out / outcome.path))
        self.assertEqual((out / "tests/login.spec.ts").read_text(),
                         (GOLDEN / "tests/login.spec.ts").read_text())


if __name__ == "__main__":
    unittest.main()

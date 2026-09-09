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
from types import SimpleNamespace
from typing import Annotated, TypedDict, get_type_hints
from unittest.mock import Mock, patch

from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda
from langgraph.errors import InvalidUpdateError
from langgraph.graph import END, START, StateGraph

from console_env import pin_console
from selenium2playwright import assemble, cli, graph, suite, suite_graph
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
        final = self.run_suite(delay=0.4)

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
        # Serial would be four sleeps back to back; two waves is two. Compare the
        # span the children actually occupied against what they actually spent,
        # both measured inside this run — a wall-clock constant is a coin flip on
        # a slow shared runner, and it lost the first time CI ran this file.
        span = max(c["finished"] for c in self.log) - min(c["started"] for c in self.log)
        spent = sum(c["finished"] - c["started"] for c in self.log)
        self.assertLess(span, spent)

    def test_a_later_wave_is_given_the_converted_companions_from_the_output_tree(self):
        self.run_suite()
        calls = {Path(c["inputs"]["source_path"]).name: c for c in self.log}
        self.assertEqual(calls["BasePage.ts"]["inputs"]["context_paths"], [])
        self.assertEqual(calls["LoginPage.ts"]["inputs"]["context_paths"],
                         [str(self.out / "pages/BasePage.ts")])
        # The spec's companions are the *converted* page object and the copied
        # support file, both read from the output tree, never from the source —
        # plus `BasePage.ts`, which the spec does not import and cannot compile
        # without.
        #
        # This assertion used to name only the first two, and that was the bug
        # rather than the specification: this fixture is three deep
        # (BasePage -> LoginPage -> spec), so the spec's compile gate was handed
        # a page object whose own `./BasePage` import resolved to nothing. The
        # gate then failed the file for a reason that had nothing to do with its
        # conversion, and the repair loop spent its attempts on it. See
        # `TransitiveContextTests` and `suite_graph.needed_by`.
        self.assertEqual(sorted(calls["login.spec.ts"]["inputs"]["context_paths"]),
                         sorted([str(self.out / "pages/LoginPage.ts"),
                                 str(self.out / "pages/BasePage.ts"),
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
        pin_console(self)  # rich renders differently on CI; see tests/console_env.py
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
        # The per-file TODO line is gone; it is in 9.3's consolidated ledger now.
        self.assertIn("1. confirm the URL · pages/LoginPage.ts", err)

    def test_json_puts_the_whole_run_on_stdout_and_nothing_else(self):
        code, out, err = self.run_cli(str(self.root), "--out", str(self.out), "--json")
        self.assertEqual(code, 0)
        document = json.loads(out)
        # Still every key of s2p.suite-run/v1, under the assembled document's name.
        self.assertEqual(document["schema"], assemble.REPORT_SCHEMA)
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

    def golden_model(self, prefix: str = ""):
        """Answer with the golden file whose source path the prompt names.

        `prefix` is load-bearing, not decoration. A spec's prompt contains *two*
        of these paths — its own, and its already-converted companion — so
        matching on the bare relative path would answer the spec with the page
        object. The source tree and the output tree have different prefixes
        (`samples/selenium-suite/…` vs `…/out/…`, or `…/src/…` vs `…/out/…` for
        a text run), and keying on the source one is what keeps them apart.
        """
        wanted = {f"{prefix or SAMPLE}/{rel}": (GOLDEN / rel).read_text()
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


    def test_a_suite_sent_as_text_comes_back_as_text(self):
        """The whole of suite-in-the-browser, as one round trip.

        No `root`, no `out_root` — nothing in the input names a path on this
        machine. `plan` materializes the tree into a temp directory it chose,
        every node after it is the same code a folder run uses (two waves, the
        real four gates, a real whole-tree `tsc`), and `finish` hands the
        converted files back as text and deletes the workspace.

        The assertion that matters most is the last one: the directory is gone.
        A public host that kept every visitor's suite on disk would be a slow
        leak of both storage and other people's code.
        """
        tree = {rel: (SAMPLE / rel).read_text()
                for rel in ("pages/LoginPage.ts", "tests/login.spec.ts")}
        # The workspace path is chosen by the graph, so the stub keys on the
        # part of it that is fixed: sources live under `<workspace>/src`.
        with self.golden_model(prefix="/src"):
            final = suite_graph.build_suite_graph().invoke(
                {"source_tree": tree},
                context=suite_graph.SuiteSettings(max_attempts=1),
                config={"recursion_limit": 20})

        outcomes = suite_graph.ordered(final)
        self.assertEqual([(o.wave, o.path) for o in outcomes],
                         [(1, "pages/LoginPage.ts"), (2, "tests/login.spec.ts")])
        self.assertTrue(final["assembly"].compiles, final["assembly"].tree_error)

        back = final["converted_tree"]
        self.assertEqual(sorted(back), ["pages/LoginPage.ts", "tests/login.spec.ts"])
        self.assertEqual(back["tests/login.spec.ts"],
                         (GOLDEN / "tests/login.spec.ts").read_text())

        # The report is in the state, not on a disk the caller cannot reach.
        self.assertIn("Conversion report", final["assembly"].markdown)
        self.assertEqual(final["assembly"].report_path, "")
        self.assertFalse(Path(final["workspace"]).exists(), "the workspace outlived the run")

    def test_a_folder_run_is_not_handed_its_own_files_back(self):
        # They are already on the caller's disk. Sending them would be posting
        # somebody their own filesystem.
        out = Path(self.tmp.name) / "converted2"
        with self.golden_model():  # noqa: SIM117
            final = suite_graph.build_suite_graph().invoke(
                {"root": str(SAMPLE), "out_root": str(out), "only": ["pages/LoginPage.ts"]},
                context=suite_graph.SuiteSettings(max_attempts=1),
                config={"recursion_limit": 20})
        self.assertEqual(final.get("converted_tree", {}), {})
        self.assertTrue(final["assembly"].report_path.endswith(assemble.REPORT_NAME))

    def test_a_tree_that_could_escape_never_reaches_the_filesystem(self):
        # The guard refuses this first, but the graph is the last line of
        # defence and must not depend on having been called through the guard.
        with self.assertRaises(ValueError) as caught:
            suite_graph.plan({"source_tree": {"../escaped.ts": "boom"}})
        self.assertIn("relative path", str(caught.exception))


class CarriedCompanionDispatchTests(unittest.TestCase):
    """A copied file is in the output tree, but it was never converted.

    The compile gate needs it, so it stays in `context_paths`. The prompt must
    not call it converted, so its path also travels in `carried_paths`.
    """

    @staticmethod
    def tree(tmp, actions: dict) -> tuple:
        out = Path(tmp) / "out"
        files = []
        for path, action in actions.items():
            (out / path).parent.mkdir(parents=True, exist_ok=True)
            (out / path).write_text("whatever")
            files.append(SimpleNamespace(path=path, action=action, imports=()))
        return out, files

    def test_only_the_copied_companion_is_named_as_carried(self):
        with TemporaryDirectory() as tmp:
            out, files = self.tree(tmp, {"pages/Base.ts": "copy",
                                         "pages/Login.ts": "convert"})
            files.append(SimpleNamespace(path="tests/a.spec.ts", action="convert",
                                         imports=("pages/Base.ts", "pages/Login.ts")))
            sends = suite_graph.dispatch({
                "waves": [["tests/a.spec.ts"]], "wave": 1, "root": tmp,
                "out_root": str(out), "manifest": SimpleNamespace(files=files)})
            job = sends[0].arg
            self.assertEqual(sorted(Path(p).name for p in job["context_paths"]),
                             ["Base.ts", "Login.ts"])
            self.assertEqual([Path(p).name for p in job["carried_paths"]],
                             ["Base.ts"])

    def test_a_fully_converted_wave_carries_nothing(self):
        with TemporaryDirectory() as tmp:
            out, files = self.tree(tmp, {"pages/Login.ts": "convert"})
            files.append(SimpleNamespace(path="tests/a.spec.ts", action="convert",
                                         imports=("pages/Login.ts",)))
            sends = suite_graph.dispatch({
                "waves": [["tests/a.spec.ts"]], "wave": 1, "root": tmp,
                "out_root": str(out), "manifest": SimpleNamespace(files=files)})
            self.assertEqual([], sends[0].arg["carried_paths"])


class DataFixtureDispatchTests(unittest.TestCase):
    """The JSON a suite reads has to be in the output tree and in the gate.

    Nothing converts it, so nothing would put it there — and then the converted
    spec imports a file that is not on disk, the compile gate reports it, and
    the repair loop rewrites a correct import three times.
    """

    def test_the_plan_copies_every_imported_fixture_across(self):
        with TemporaryDirectory() as tmp:
            root, out = Path(tmp) / "src", Path(tmp) / "out"
            (root / "tests/testdata").mkdir(parents=True)
            (root / "tests/testdata/login.json").write_text('{"username": "u"}\n')
            (root / "tests/specs").mkdir(parents=True)
            (root / "tests/specs/login.spec.ts").write_text(
                'import { WebDriver } from "selenium-webdriver";\n'
                'import loginData from "tests/testdata/login.json";\n'
                "export const u = loginData.username;\n")
            state = suite_graph.plan({"root": str(root), "out_root": str(out)})
            self.assertIn("tests/testdata/login.json", state["copied"])
            self.assertTrue((out / "tests/testdata/login.json").exists())

    def test_a_fixture_reaches_the_file_that_reads_it_without_being_called_carried(self):
        with TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            (out / "tests/testdata").mkdir(parents=True)
            (out / "tests/testdata/login.json").write_text("{}\n")
            spec = SimpleNamespace(path="tests/a.spec.ts", action="convert", imports=(),
                                   data_imports=("tests/testdata/login.json",))
            sends = suite_graph.dispatch({
                "waves": [["tests/a.spec.ts"]], "wave": 1, "root": tmp,
                "out_root": str(out), "manifest": SimpleNamespace(files=[spec])})
            job = sends[0].arg
            self.assertEqual([Path(p).name for p in job["context_paths"]], ["login.json"])
            self.assertEqual([], job["carried_paths"])

    def test_a_fixture_a_page_object_reads_still_reaches_the_spec(self):
        """Transitive, for the same reason companions are: the spec never names it."""
        with TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            (out / "pages").mkdir(parents=True)
            (out / "pages/Login.ts").write_text("x")
            (out / "data").mkdir(parents=True)
            (out / "data/users.json").write_text("{}\n")
            page = SimpleNamespace(path="pages/Login.ts", action="convert", imports=(),
                                   data_imports=("data/users.json",))
            spec = SimpleNamespace(path="tests/a.spec.ts", action="convert",
                                   imports=("pages/Login.ts",), data_imports=())
            sends = suite_graph.dispatch({
                "waves": [["tests/a.spec.ts"]], "wave": 1, "root": tmp,
                "out_root": str(out), "manifest": SimpleNamespace(files=[page, spec])})
            self.assertEqual(sorted(Path(p).name for p in sends[0].arg["context_paths"]),
                             ["Login.ts", "users.json"])


if __name__ == "__main__":
    unittest.main()


class WorkspaceSweepTests(unittest.TestCase):
    """What happens to a text run's temp folder when the run does not finish.

    `finish` deletes its own workspace, and on the happy path that is the end of
    it. A run that raises in between never reaches `finish`, and on a long-lived
    public host every one of those leaves a copy of somebody's suite on disk.
    """

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patcher = patch("tempfile.gettempdir", return_value=self.tmp.name)
        patcher.start()
        self.addCleanup(patcher.stop)

    def make(self, name: str, age_seconds: float) -> Path:
        path = Path(self.tmp.name) / name
        (path / "src").mkdir(parents=True)
        (path / "src" / "A.ts").write_text("somebody's code")
        old = time.time() - age_seconds
        os.utime(path, (old, old))
        return path

    def test_an_abandoned_workspace_is_swept_on_the_next_run(self):
        stale = self.make("s2p-suite-old", suite_graph.WORKSPACE_TTL + 60)
        self.assertEqual(suite_graph.sweep_workspaces(), 1)
        self.assertFalse(stale.exists())

    def test_a_run_still_going_is_left_alone(self):
        # The sweep runs at the start of a new suite, and another one may be
        # halfway through its second wave.
        live = self.make("s2p-suite-live", 30)
        self.assertEqual(suite_graph.sweep_workspaces(), 0)
        self.assertTrue((live / "src" / "A.ts").exists())

    def test_it_only_touches_its_own_directories(self):
        someone_else = Path(self.tmp.name) / "important-thing"
        someone_else.mkdir()
        old = time.time() - 99999
        os.utime(someone_else, (old, old))
        suite_graph.sweep_workspaces()
        self.assertTrue(someone_else.exists())

    def test_a_sweep_that_cannot_run_is_not_an_error(self):
        # Another worker sweeping the same directory is the expected case, not
        # a failure, and a tidy-up that could fail a conversion would be worse
        # than the mess.
        with patch.object(Path, "glob", side_effect=OSError("gone")):
            self.assertEqual(suite_graph.sweep_workspaces(), 0)


class TransitiveContextTests(unittest.TestCase):
    """What a file needs to compile, which is not what it imports.

    `SuiteFile.imports` is direct imports. Handing only those to the compile
    gate is right for a two-deep suite and wrong for every deeper one: the
    spec's `../pages/BasePage` does not resolve, the gate reports a compile
    failure that has nothing to do with the conversion, and the repair loop
    spends all three attempts rewriting correct code to fix it.

    Latent since 9.2, because `samples/selenium-suite` is exactly two deep.
    """

    @staticmethod
    def graph(edges: dict) -> dict:
        return {path: SimpleNamespace(path=path, imports=tuple(deps))
                for path, deps in edges.items()}

    def test_a_three_deep_chain_carries_the_whole_chain(self):
        by = self.graph({"spec.ts": ["Page.ts"], "Page.ts": ["Base.ts"], "Base.ts": []})
        self.assertEqual(suite_graph.needed_by("spec.ts", by), ["Page.ts", "Base.ts"])

    def test_a_two_deep_suite_is_unchanged(self):
        # The old behaviour has to survive: this is what every prior suite run
        # and every published report was measured with.
        by = self.graph({"spec.ts": ["Page.ts"], "Page.ts": []})
        self.assertEqual(suite_graph.needed_by("spec.ts", by), ["Page.ts"])

    def test_a_diamond_names_each_file_once(self):
        by = self.graph({"spec.ts": ["A.ts", "B.ts"], "A.ts": ["Base.ts"],
                         "B.ts": ["Base.ts"], "Base.ts": []})
        self.assertEqual(suite_graph.needed_by("spec.ts", by),
                         ["A.ts", "B.ts", "Base.ts"])

    def test_a_cycle_terminates_rather_than_dying(self):
        # TypeScript allows circular imports, so a suite containing one is a
        # suite this still has to convert.
        by = self.graph({"A.ts": ["B.ts"], "B.ts": ["A.ts"]})
        self.assertEqual(suite_graph.needed_by("A.ts", by), ["B.ts"])

    def test_a_file_never_lists_itself(self):
        by = self.graph({"A.ts": ["A.ts", "B.ts"], "B.ts": []})
        self.assertEqual(suite_graph.needed_by("A.ts", by), ["B.ts"])

    def test_imports_of_files_outside_the_suite_are_ignored(self):
        # `@playwright/test` is not in the manifest and is not ours to provide.
        by = self.graph({"spec.ts": ["Page.ts", "node_modules/x.ts"], "Page.ts": []})
        self.assertEqual(suite_graph.needed_by("spec.ts", by), ["Page.ts"])

    def test_the_order_is_stable_across_calls(self):
        # A companion list that reshuffled between runs would make two
        # identical conversions produce two different prompts.
        by = self.graph({"spec.ts": ["A.ts", "B.ts"], "A.ts": ["Base.ts"],
                         "B.ts": [], "Base.ts": []})
        self.assertEqual(suite_graph.needed_by("spec.ts", by),
                         suite_graph.needed_by("spec.ts", by))

    def test_dispatch_hands_the_deep_companion_to_the_gate(self):
        """The bug itself, at the level it actually happened."""
        with TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            for rel in ("pages/BasePage.ts", "pages/DynamicControlsPage.ts"):
                (out / rel).parent.mkdir(parents=True, exist_ok=True)
                (out / rel).write_text("converted")
            manifest = SimpleNamespace(files=[
                SimpleNamespace(path="pages/BasePage.ts", imports=()),
                SimpleNamespace(path="pages/DynamicControlsPage.ts",
                                imports=("pages/BasePage.ts",)),
                SimpleNamespace(path="tests/dc.spec.ts",
                                imports=("pages/DynamicControlsPage.ts",)),
            ])
            sends = suite_graph.dispatch({
                "waves": [["pages/BasePage.ts"], ["pages/DynamicControlsPage.ts"],
                          ["tests/dc.spec.ts"]],
                "wave": 3, "root": tmp, "out_root": str(out), "manifest": manifest})
            context = [Path(p).name for p in sends[0].arg["context_paths"]]
            self.assertEqual(context, ["DynamicControlsPage.ts", "BasePage.ts"])

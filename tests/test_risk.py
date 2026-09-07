"""Step 7.2 — does the agent stop and ask, and does the answer reach the model?

Offline. The model is scripted; the checkpointer, the SQLite file and the
interrupt/resume machinery are real, because the thing under test is exactly
whether a run can be suspended, answered, and resumed with the answer applied.
"""

import io
import os
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda
from langgraph.types import Command

from selenium2playwright import cli, graph, memory, risk
from selenium2playwright.prompts import build_prompt, format_decisions
from selenium2playwright.schemas import ConversionResult, Critique

ROOT = Path(__file__).resolve().parents[1]
ALERTS = ROOT / "samples/selenium-suite/pages/AlertsPage.ts"
GOLDEN = (ROOT / "samples/playwright-golden/pages/AlertsPage.ts").read_text()
LEGACY = ROOT / "samples/risky/secure-area.spec.ts"
PLAIN = ROOT / "samples/selenium-suite/pages/LoginPage.ts"
PASS = Critique(verdict="pass", fixes=[])
REVISE = Critique(verdict="revise", fixes=["Assert the dialog message before accepting it."])
DIALOGS = risk.RISKS["dialogs"]


class DetectorTests(unittest.TestCase):
    """Pure text analysis: no model, no graph, no state."""

    def test_the_dialog_page_is_flagged_with_its_evidence(self):
        found = risk.detect_risks(ALERTS.read_text())
        self.assertEqual([r.kind for r in found], ["dialogs"])
        self.assertEqual(found[0].line, 21)
        self.assertIn("alertIsPresent", found[0].snippet)
        self.assertEqual(found[0].count, 4)  # two waits and two accept/dismiss calls

    def test_an_ordinary_page_object_and_suite_are_not_flagged(self):
        """Precision matters more than recall: a false pause trains people to ignore it."""
        for path in (PLAIN, ROOT / "samples/selenium-suite/tests/login.spec.ts"):
            self.assertEqual(risk.detect_risks(path.read_text()), [], path.name)

    def test_the_legacy_suite_raises_one_question_per_kind_in_a_fixed_order(self):
        found = risk.detect_risks(LEGACY.read_text())
        self.assertEqual([r.kind for r in found], ["javascript-execution", "shared-session"])
        self.assertIn("executeScript", found[0].snippet)
        self.assertGreater(found[1].count, 1)  # several credential lines, still one question

    def test_shared_session_needs_a_before_hook_and_more_than_one_test(self):
        hook = ('describe("x", () => {\n  before(async () => {\n'
                '    await login("tomsmith", "SuperSecretPassword!");\n  });\n')
        one_test = hook + '  it("a", async () => {});\n});\n'
        two_tests = hook + '  it("a", async () => {});\n  it("b", async () => {});\n});\n'
        per_test = two_tests.replace("before(", "beforeEach(")
        self.assertEqual(risk.detect_risks(one_test), [])  # nothing to inherit the session
        self.assertEqual([r.kind for r in risk.detect_risks(two_tests)], ["shared-session"])
        self.assertEqual(risk.detect_risks(per_test), [])  # beforeEach converts with no decision

    def test_a_driver_built_in_before_is_setup_not_shared_state(self):
        source = ('describe("x", () => {\n  before(async () => {\n'
                  '    driver = await new Builder().forBrowser("chrome").build();\n  });\n'
                  '  it("a", async () => { await login("u", "p"); });\n'
                  '  it("b", async () => { await login("u", "p"); });\n});\n')
        self.assertEqual(risk.detect_risks(source), [])

    def test_an_answer_becomes_guidance_and_free_text_is_kept_verbatim(self):
        self.assertEqual(risk.resolve("dialogs", ""), DIALOGS.default.guidance)  # empty = default
        self.assertEqual(risk.resolve("dialogs", "auto-dismiss"), DIALOGS.option("auto-dismiss").guidance)
        self.assertEqual(risk.resolve("dialogs", "handle it in a fixture"), "handle it in a fixture")

    def test_the_question_payload_carries_everything_a_front_end_needs(self):
        payload = risk.question(risk.detect_risks(ALERTS.read_text())[0])
        self.assertEqual(payload["kind"], "dialogs")
        self.assertEqual(payload["default"], DIALOGS.options[0].key)
        self.assertEqual([o["key"] for o in payload["options"]], [o.key for o in DIALOGS.options])
        self.assertIn("line 21", payload["evidence"])
        self.assertIn("+3 more", payload["evidence"])

    def test_only_answered_risks_are_rendered_and_the_prompt_block_is_numbered(self):
        found = risk.detect_risks(LEGACY.read_text())
        self.assertEqual(risk.decision_lines(found, {}), [])
        lines = risk.decision_lines(found, {"shared-session": "login-per-test"})
        self.assertEqual(len(lines), 1)
        self.assertIn("test.beforeEach", lines[0])
        self.assertEqual(format_decisions([]), "")
        self.assertIn("1. session shared", format_decisions(lines))


class ScriptedGraph(unittest.TestCase):
    """Queue drafts and reviews; capture the prompts each call was given."""

    def setUp(self):
        env = patch.dict(os.environ, {"LANGSMITH_TRACING": "false", "LANGCHAIN_TRACING_V2": "false",
                                      # Shaped like a key, worth nothing: the CLI now builds the configured
                                      # model before running, and no test may need a real credential.
                                      "ANTHROPIC_API_KEY": "sk-ant-offline-test"})
        env.start()
        self.addCleanup(env.stop)
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "threads.sqlite"

    def replies(self, drafts, reviews):
        queues = {ConversionResult: iter(drafts), Critique: iter(reviews)}
        self.conversion_prompts, self.critic_prompts = [], []

        def structured(schema, **kwargs):
            def respond(prompt):
                messages = prompt if isinstance(prompt, list) else prompt.to_messages()
                bucket = self.conversion_prompts if schema is ConversionResult else self.critic_prompts
                bucket.append("\n\n".join(str(m.content) for m in messages))
                return {"parsed": next(queues[schema]), "parsing_error": None,
                        "raw": AIMessage(content="", usage_metadata={
                            "input_tokens": 10, "output_tokens": 5, "total_tokens": 15})}
            return RunnableLambda(respond)

        model = Mock()
        model.with_structured_output.side_effect = structured
        return patch.object(graph, "make_model", return_value=model)


class PauseAndResumeTests(ScriptedGraph):
    def test_a_risky_file_stops_before_the_model_is_called(self):
        with self.replies([ConversionResult(code=GOLDEN)], [PASS]):
            with memory.open_checkpointer(self.db) as checkpointer:
                compiled = graph.build_graph(checkpointer)
                config = memory.thread_config("alerts")
                paused = compiled.invoke({"source_path": str(ALERTS), "ask_risks": True}, config=config)
                snapshot = compiled.get_state(config)

        # The reply carries what this invocation managed to write — intake's work —
        # plus the question. There is no report: the run has not finished, it is
        # standing at the node that asked, waiting for a human.
        self.assertIn("__interrupt__", paused)
        self.assertIsNone(paused.get("report"))
        self.assertEqual([r.kind for r in paused["risks"]], ["dialogs"])
        self.assertEqual(snapshot.next, ("risk_review",))
        question = paused["__interrupt__"][0].value
        self.assertEqual(question["kind"], "dialogs")
        self.assertIn("more than one", question["why"] + question["title"] + " more than one")
        self.assertEqual(self.conversion_prompts, [])  # no tokens spent on a guess

    def test_the_answer_reaches_both_the_actor_and_the_critic(self):
        with self.replies([ConversionResult(code=GOLDEN)], [PASS]):
            with memory.open_checkpointer(self.db) as checkpointer:
                compiled = graph.build_graph(checkpointer)
                config = memory.thread_config("alerts")
                compiled.invoke({"source_path": str(ALERTS), "ask_risks": True}, config=config)
                final = compiled.invoke(Command(resume="auto-dismiss"), config=config)

        self.assertEqual(final["decisions"], {"dialogs": "auto-dismiss"})
        expected = DIALOGS.option("auto-dismiss").guidance
        self.assertIn("HUMAN DECISIONS", self.conversion_prompts[0])
        self.assertIn(expected, self.conversion_prompts[0])
        self.assertIn(expected, self.critic_prompts[0])  # the reviewer judges the chosen branch

    def test_a_different_answer_changes_what_the_model_is_told(self):
        """The point of the step: the human's choice is what reaches the model."""
        prompts = {}
        for answer in ("handler-first", "auto-dismiss"):
            with self.replies([ConversionResult(code=GOLDEN)], [PASS]):
                with memory.open_checkpointer(self.db) as checkpointer:
                    compiled = graph.build_graph(checkpointer)
                    config = memory.thread_config(answer)
                    compiled.invoke({"source_path": str(ALERTS), "ask_risks": True}, config=config)
                    compiled.invoke(Command(resume=answer), config=config)
            prompts[answer] = self.conversion_prompts[0]

        self.assertNotEqual(prompts["handler-first"], prompts["auto-dismiss"])
        self.assertIn("registering page.once", prompts["handler-first"])
        self.assertIn("auto-dismiss", prompts["auto-dismiss"])
        self.assertNotIn("Do not register a dialog handler", prompts["handler-first"])

    def test_free_text_is_passed_to_the_model_in_the_users_own_words(self):
        own_words = "accept the alert, and assert its message text before accepting"
        with self.replies([ConversionResult(code=GOLDEN)], [PASS]):
            with memory.open_checkpointer(self.db) as checkpointer:
                compiled = graph.build_graph(checkpointer)
                config = memory.thread_config("alerts")
                compiled.invoke({"source_path": str(ALERTS), "ask_risks": True}, config=config)
                compiled.invoke(Command(resume=own_words), config=config)
        self.assertIn(own_words, self.conversion_prompts[0])

    def test_an_empty_answer_means_the_default(self):
        with self.replies([ConversionResult(code=GOLDEN)], [PASS]):
            with memory.open_checkpointer(self.db) as checkpointer:
                compiled = graph.build_graph(checkpointer)
                config = memory.thread_config("alerts")
                compiled.invoke({"source_path": str(ALERTS), "ask_risks": True}, config=config)
                final = compiled.invoke(Command(resume=""), config=config)
        self.assertEqual(final["decisions"], {"dialogs": ""})
        self.assertIn(DIALOGS.default.guidance, self.conversion_prompts[0])

    def test_two_risks_are_asked_one_at_a_time_in_a_fixed_order(self):
        """Each resume answers exactly one question; the node re-runs and asks the next."""
        with self.replies([ConversionResult(code=GOLDEN)], [PASS]):
            with memory.open_checkpointer(self.db) as checkpointer:
                compiled = graph.build_graph(checkpointer)
                config = memory.thread_config("legacy")
                first = compiled.invoke({"source_path": str(LEGACY), "ask_risks": True}, config=config)
                second = compiled.invoke(Command(resume="keep-evaluate"), config=config)
                # Left unanswered on purpose: the run is still suspended, and no
                # model call has happened for either question.
                state = compiled.get_state(config)

        self.assertEqual(first["__interrupt__"][0].value["kind"], "javascript-execution")
        self.assertEqual(second["__interrupt__"][0].value["kind"], "shared-session")
        self.assertEqual([task.name for task in state.tasks], ["risk_review"])
        self.assertTrue(state.tasks[0].interrupts)  # still holding, still asking
        self.assertEqual(self.conversion_prompts, [])

    def test_an_answered_question_is_not_asked_again_on_a_later_turn(self):
        drafts = [ConversionResult(code=GOLDEN), ConversionResult(code=GOLDEN.replace("accept", "dismiss"))]
        with self.replies(drafts, [PASS, PASS]):
            with memory.open_checkpointer(self.db) as checkpointer:
                compiled = graph.build_graph(checkpointer)
                config = memory.thread_config("alerts")
                compiled.invoke({"source_path": str(ALERTS), "ask_risks": True}, config=config)
                compiled.invoke(Command(resume="auto-dismiss"), config=config)
                second = compiled.invoke({"refinement": "prefer getByRole", "ask_risks": True}, config=config)

        self.assertNotIn("__interrupt__", second)  # answered on turn 1, still answered
        self.assertEqual(second["turn"], 2)
        self.assertEqual(second["decisions"], {"dialogs": "auto-dismiss"})
        self.assertIn(DIALOGS.option("auto-dismiss").guidance, self.conversion_prompts[1])

    def test_the_decision_survives_a_repair_lap(self):
        """A repair must not quietly re-litigate the branch the human chose."""
        drafts = [ConversionResult(code=GOLDEN), ConversionResult(code=GOLDEN)]
        with self.replies(drafts, [REVISE, PASS]):
            with memory.open_checkpointer(self.db) as checkpointer:
                compiled = graph.build_graph(checkpointer)
                config = memory.thread_config("alerts")
                compiled.invoke({"source_path": str(ALERTS), "ask_risks": True}, config=config)
                compiled.invoke(Command(resume="auto-dismiss"), config=config)
        repair = self.conversion_prompts[1]
        self.assertIn("<validation_reports>", repair)  # it is a repair lap...
        self.assertIn(DIALOGS.option("auto-dismiss").guidance, repair)  # ...and the answer still stands

    def test_answers_supplied_up_front_skip_the_pause(self):
        with self.replies([ConversionResult(code=GOLDEN)], [PASS]):
            with memory.open_checkpointer(self.db) as checkpointer:
                compiled = graph.build_graph(checkpointer)
                final = compiled.invoke(
                    {"source_path": str(ALERTS), "ask_risks": True, "decisions": {"dialogs": "expect-event"}},
                    config=memory.thread_config("alerts"))
        self.assertNotIn("__interrupt__", final)
        self.assertIn(DIALOGS.option("expect-event").guidance, self.conversion_prompts[0])

    def test_without_ask_risks_nothing_pauses_and_the_prompt_is_unchanged(self):
        """Every Phase 0-6 run and the whole eval suite take this path."""
        with self.replies([ConversionResult(code=GOLDEN)], [PASS]):
            final = graph.build_graph().invoke({"source_path": str(ALERTS)})
        self.assertEqual([r.kind for r in final["risks"]], ["dialogs"])  # detected...
        self.assertEqual(final.get("decisions", {}), {})  # ...but never asked
        self.assertNotIn("HUMAN DECISIONS", self.conversion_prompts[0])
        baseline = build_prompt().format_messages(file_path="a.ts", source="s", context="")
        self.assertEqual(len(baseline), 2)

    def test_an_unsupported_file_is_refused_without_being_asked_about(self):
        source = Path(self.tmp.name) / "spec.ts"
        source.write_text('import { browser } from "webdriverio";\n'
                          'await browser.execute("alert.accept()");\n')
        with memory.open_checkpointer(self.db) as checkpointer:
            final = graph.build_graph(checkpointer).invoke(
                {"source_path": str(source), "ask_risks": True}, config=memory.thread_config("wdio"))
        self.assertEqual(final["status"], "refused")
        self.assertNotIn("__interrupt__", final)


class CommandLineTests(ScriptedGraph):
    def run_cli(self, *argv, answers=None, tty=True):
        out, err = io.StringIO(), io.StringIO()
        typed = iter(answers or [])
        with patch("sys.stdin") as stdin, patch("builtins.input", lambda: next(typed)):
            stdin.isatty.return_value = tty
            with redirect_stdout(out), redirect_stderr(err):
                code = cli.run([*argv, "--db", str(self.db)])
        return code, out.getvalue(), err.getvalue()

    def test_the_terminal_asks_the_question_and_a_number_picks_an_option(self):
        with self.replies([ConversionResult(code=GOLDEN)], [PASS]):
            code, _, err = self.run_cli("convert", str(ALERTS), "--thread", "alerts",
                                        "--out", str(Path(self.tmp.name) / "AlertsPage.ts"),
                                        answers=["3"])
        self.assertIn("Paused — browser dialog handling", err)
        self.assertIn("line 21", err)
        self.assertIn("1) handler-first", err)
        self.assertIn("[default]", err)
        self.assertIn("Risk review: 1 pattern", err)
        self.assertIn("→ auto-dismiss", err)  # option 3, resolved back to its key
        self.assertIn(DIALOGS.option("auto-dismiss").guidance, self.conversion_prompts[0])
        self.assertEqual(code, 0)  # the golden passes every gate and the critic

    def test_without_a_terminal_the_default_is_used_and_said_out_loud(self):
        with self.replies([ConversionResult(code=GOLDEN)], [PASS]):
            _, _, err = self.run_cli("convert", str(ALERTS), "--thread", "alerts", tty=False)
        self.assertIn("no terminal to ask on; using the default (handler-first)", err)
        self.assertIn(DIALOGS.default.guidance, self.conversion_prompts[0])

    def test_answer_flag_converts_without_pausing_and_is_remembered(self):
        with self.replies([ConversionResult(code=GOLDEN)] * 2, [PASS] * 2):
            first = self.run_cli("convert", str(ALERTS), "--thread", "alerts", "--answer", "dialogs=auto-dismiss")
            second = self.run_cli("convert", "--thread", "alerts", "--refine", "prefer getByRole")
        self.assertNotIn("Paused", first[2])
        self.assertIn("→ auto-dismiss", first[2])
        self.assertNotIn("Paused", second[2])  # turn 2 does not re-ask
        self.assertIn(DIALOGS.option("auto-dismiss").guidance, self.conversion_prompts[1])

    def test_no_ask_reports_the_risk_and_converts_with_the_playbook_default(self):
        with self.replies([ConversionResult(code=GOLDEN)], [PASS]):
            _, _, err = self.run_cli("convert", str(ALERTS), "--thread", "alerts", "--no-ask")
        self.assertIn("not asked; converted with the playbook default", err)
        self.assertNotIn("HUMAN DECISIONS", self.conversion_prompts[0])

    def test_a_threadless_run_says_how_to_be_asked(self):
        with self.replies([ConversionResult(code=GOLDEN)], [PASS]):
            _, _, err = self.run_cli("convert", str(ALERTS))
        self.assertIn("Rerun with --thread to be asked", err)

    def test_a_malformed_answer_is_rejected_before_any_work(self):
        for bad in ("dialogs", "not-a-risk=x"):
            with self.subTest(answer=bad):
                code, _, err = self.run_cli("convert", str(ALERTS), "--thread", "alerts", "--answer", bad)
                self.assertEqual(code, 2)
                self.assertIn("--answer must be KIND=ANSWER", err)


if __name__ == "__main__":
    unittest.main()

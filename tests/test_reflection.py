"""Bounded reflection with scripted model replies and the real validation tools."""

import io
import os
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda

from selenium2playwright import graph
from selenium2playwright.reflection import MAX_ATTEMPTS
from selenium2playwright.schemas import ConversionResult, Critique

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "samples/selenium-suite/pages/LoginPage.ts"
GOLDEN = (ROOT / "samples/playwright-golden/pages/LoginPage.ts").read_text()
BROKEN = GOLDEN.replace("await this.usernameInput.fill", "this.usernameInput.fill")
PASS = Critique(verdict="pass", fixes=[])
REVISE = Critique(verdict="revise", fixes=["Await usernameInput.fill() to prevent a floating Promise."])


class ReflectionTests(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ, {"LANGSMITH_TRACING": "false", "LANGCHAIN_TRACING_V2": "false"})
        env.start()
        self.addCleanup(env.stop)

    def replies(self, drafts, reviews):
        queues = {ConversionResult: iter(drafts), Critique: iter(reviews)}
        self.calls = []
        self.conversion_prompts = []

        def structured(schema, **kwargs):
            def respond(prompt):
                self.calls.append(schema.__name__)
                if schema is ConversionResult:
                    self.conversion_prompts.append(prompt if isinstance(prompt, list) else prompt.to_messages())
                value = next(queues[schema])
                if isinstance(value, Exception):
                    raise value
                return {"parsed": value, "parsing_error": None, "raw": AIMessage(content="", usage_metadata={
                    "input_tokens": 10, "output_tokens": 5, "total_tokens": 15, "input_token_details": {"cache_read": 3},
                })}
            return RunnableLambda(respond)

        model = Mock()
        model.with_structured_output.side_effect = structured
        return patch.object(graph, "make_model", return_value=model)

    def test_broken_draft_repairs_and_revalidates_with_all_feedback(self):
        draft = ConversionResult(code=BROKEN + "\n// TODO(review): Await the fill operation.\n",
                                 todos=["TODO(review): Await the fill operation."])
        with self.replies([draft, ConversionResult(code=GOLDEN)], [REVISE, PASS]):
            updates = list(graph.build_graph().stream({"source_path": str(SOURCE)}, stream_mode="updates"))
        self.assertEqual([next(iter(u)) for u in updates],
                         ["intake", "convert", "validate", "critic", "convert", "validate", "critic", "assemble"])
        self.assertFalse(updates[2]["validate"]["validation"][2].passed)
        report = updates[-1]["assemble"]["report"]
        self.assertEqual((report.status, report.attempts), ("passed", 2))
        self.assertEqual(report.result.code, GOLDEN)
        self.assertEqual(report.result.todos, [])
        self.assertTrue(all(r.passed for r in report.validation))
        self.assertEqual(self.calls, ["ConversionResult", "Critique"] * 2)
        self.assertEqual(len(self.conversion_prompts[0]), 2)
        feedback = self.conversion_prompts[1][-1].content
        for evidence in ("previous_conversion", "usernameInput.fill", "no-floating-promises", REVISE.fixes[0]):
            self.assertIn(evidence, feedback)
        self.assertEqual(updates[4]["convert"]["usage"]["total_tokens"], 30)
        self.assertEqual(updates[6]["critic"]["critic_usage"]["input_token_details"]["cache_read"], 6)

    def test_persistent_failure_stops_at_three_attempts(self):
        with self.replies([ConversionResult(code=BROKEN)] * MAX_ATTEMPTS, [PASS] * MAX_ATTEMPTS):
            final = graph.build_graph().invoke({"source_path": str(SOURCE)})
        self.assertEqual((final["report"].status, final["iteration"]), ("needs-review", 3))
        self.assertEqual(self.calls, ["ConversionResult", "Critique"] * 3)
        self.assertEqual(final["report"].result.code, BROKEN)
        self.assertFalse(final["report"].validation[2].passed)
        self.assertIn("3 of 3", final["report"].reason)

    def test_semantic_review_can_request_repairs_and_pass_on_third_attempt(self):
        wrong_url = ConversionResult(code=GOLDEN.replace('"/login"', '"/wrong"'))
        review = Critique(verdict="revise", fixes=["Restore the original /login path; /wrong changes behavior."])
        with self.replies([wrong_url, wrong_url, ConversionResult(code=GOLDEN)], [review, review, PASS]):
            updates = list(graph.build_graph().stream({"source_path": str(SOURCE)}, stream_mode="updates"))
        for update in updates:
            if "validate" in update:
                self.assertTrue(all(r.passed for r in update["validate"]["validation"]))
        report = updates[-1]["assemble"]["report"]
        self.assertEqual((report.status, report.attempts), ("passed", 3))
        self.assertEqual(report.result.code, GOLDEN)

    def test_failed_repair_keeps_previous_code_and_its_scorecard(self):
        with self.replies([ConversionResult(code=BROKEN), RuntimeError("repair API unavailable")], [REVISE]):
            final = graph.build_graph().invoke({"source_path": str(SOURCE)})
        report = final["report"]
        self.assertEqual((report.status, report.attempts), ("needs-review", 2))
        self.assertEqual(report.result.code, BROKEN)
        self.assertFalse(report.validation[2].passed)
        self.assertIn("repair API unavailable", report.errors)
        self.assertEqual(self.calls, ["ConversionResult", "Critique", "ConversionResult"])

    def test_unavailable_critic_and_validator_do_not_trigger_repairs(self):
        with self.replies([ConversionResult(code=GOLDEN)], [RuntimeError("critic unavailable")]):
            final = graph.build_graph().invoke({"source_path": str(SOURCE)})
        self.assertEqual(final["report"].status, "needs-review")
        self.assertEqual(final["iteration"], 1)
        with self.replies([ConversionResult(code=GOLDEN)], [REVISE]), \
                patch.object(graph, "compile_check", side_effect=RuntimeError("tsc missing")):
            final = graph.build_graph().invoke({"source_path": str(SOURCE)})
        self.assertEqual(final["iteration"], 1)
        self.assertIn("validation tool failed", final["report"].reason)

    def test_initial_failure_preserves_existing_output_file(self):
        with TemporaryDirectory() as folder, self.replies([RuntimeError("conversion unavailable")], []), \
                redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()) as stderr:
            output = Path(folder) / "existing.ts"
            output.write_text("existing output")
            result = graph.main([str(SOURCE), "--out", str(output)])
            self.assertEqual(result, 1)
            self.assertEqual(output.read_text(), "existing output")
        self.assertIn("no output file was written", stderr.getvalue())
        self.assertIn("NOT RUN", stderr.getvalue())
        self.assertEqual(self.calls, ["ConversionResult"])

    def test_open_code_todos_are_collected_and_need_review_without_rewriting(self):
        code = GOLDEN + "\n// TODO(review): Verify the application's configured baseURL.\n"
        with self.replies([ConversionResult(code=code)], [PASS]):
            final = graph.build_graph().invoke({"source_path": str(SOURCE)})
        self.assertEqual(final["report"].status, "needs-review")
        self.assertEqual(final["report"].attempts, 1)
        self.assertEqual(len(final["report"].result.todos), 1)
        self.assertIn("TODO(review): Verify", final["report"].result.todos[0])


if __name__ == "__main__":
    unittest.main()

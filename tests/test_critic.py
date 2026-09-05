"""Critic contracts and graph behavior with fixed model replies; no paid API calls."""

import io
import os
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda
from pydantic import ValidationError

from selenium2playwright import graph, llm
from selenium2playwright.schemas import ConversionResult, Critique, Finding, ValidationReport

ROOT = Path(__file__).resolve().parents[1]


class CriticTests(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ, {"LANGSMITH_TRACING": "false", "LANGCHAIN_TRACING_V2": "false"})
        env.start()
        self.addCleanup(env.stop)
        self.state = {
            "source_path": str(ROOT / "samples/selenium-suite/pages/LoginPage.ts"),
            "source": "source evidence", "context": "companion evidence",
            "result": ConversionResult(code="converted evidence", notes=["decision evidence"], todos=["TODO(review): risk"]),
            "validation": [ValidationReport(gate=g, passed=True) for g in ("compile", "residue", "lint", "parity")],
        }

    def reply(self, critique=None, parsing_error=None):
        self.prompts = []
        def respond(prompt):
            self.prompts.append(prompt if isinstance(prompt, list) else prompt.to_messages())
            return {"parsed": critique, "parsing_error": parsing_error,
                    "raw": AIMessage(content="", usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15})}
        model = Mock()
        model.with_structured_output.return_value = RunnableLambda(respond)
        self.model = model
        return patch.object(graph, "make_model", return_value=model)

    def test_critic_receives_source_output_companions_and_findings(self):
        self.state["validation"][2] = ValidationReport(gate="lint", passed=False, findings=[Finding(
            gate="lint", file="LoginPage.ts", line=23, code="error/no-floating-promises", message="Add await")])
        review = Critique(verdict="revise", fixes=["LoginPage.ts:23: await the fill() call."])
        with self.reply(review) as make_model:
            update = graph.critic(self.state)
        make_model.assert_called_once_with(for_critic=True)
        self.model.with_structured_output.assert_called_once_with(Critique, method="json_schema", include_raw=True)
        human = self.prompts[0][-1].content
        for evidence in ("source evidence", "converted evidence", "companion evidence", "decision evidence",
                         "TODO(review): risk", "FAIL lint", "LoginPage.ts:23", "error/no-floating-promises"):
            self.assertIn(evidence, human)
        self.assertEqual(update["critique"], review)
        self.assertEqual(update["critic_usage"]["total_tokens"], 15)
        self.assertEqual(update["critique_error"], "")

    def test_model_pass_cannot_override_failed_validator(self):
        self.state["validation"][0] = ValidationReport(gate="compile", passed=False, tool_output="error TS18003: No inputs")
        with self.reply(Critique(verdict="pass", fixes=[])):
            update = graph.critic(self.state)
        self.assertEqual(update["critique"].verdict, "revise")
        self.assertIn("TS18003", update["critique"].fixes[0])
        self.assertIn("FAIL compile", self.prompts[0][-1].content)

    def test_clean_review_can_pass(self):
        with self.reply(Critique(verdict="pass", fixes=[])):
            update = graph.critic(self.state)
        self.assertEqual(update["critique"].verdict, "pass")
        self.assertEqual(update["critique"].fixes, [])

    def test_parse_failure_or_missing_reply_is_unavailable_not_pass(self):
        for error in (ValueError("malformed JSON"), None):
            with self.subTest(error=error), self.reply(parsing_error=error):
                update = graph.critic(self.state)
                self.assertIsNone(update["critique"])
                self.assertTrue(update["critique_error"])
                self.assertEqual(update["critic_usage"]["total_tokens"], 15)

    def test_model_error_is_explicit(self):
        with patch.object(graph, "make_model", side_effect=RuntimeError("provider unavailable")):
            update = graph.critic(self.state)
        self.assertIsNone(update["critique"])
        self.assertEqual(update["critique_error"], "provider unavailable")

    def test_schema_rejects_contradictions_unknown_verdicts_and_blank_fixes(self):
        for verdict, fixes in (("pass", ["fix this"]), ("revise", []), ("revise", [" "]), ("maybe", [])):
            with self.subTest(verdict=verdict, fixes=fixes), self.assertRaises(ValidationError):
                Critique(verdict=verdict, fixes=fixes)

    def test_critic_revision_or_failure_preserves_code_and_fails_cli(self):
        code = (ROOT / "samples/playwright-golden/pages/LoginPage.ts").read_text()
        for review in (Critique(verdict="revise", fixes=["Review the locator against the source."]), None):
            stdout, stderr = io.StringIO(), io.StringIO()
            with self.subTest(review=review), self.reply(review), redirect_stdout(stdout), redirect_stderr(stderr), \
                    patch.object(graph, "route_after_critic", return_value="assemble"), \
                    patch.object(graph, "convert", return_value={"status": "converted", "result": ConversionResult(code=code),
                                                                 "usage": None, "iteration": 1}) as convert:
                result = graph.main([self.state["source_path"]])
            self.assertEqual(result, 1)
            self.assertEqual(stdout.getvalue(), code)
            self.assertIn("PASS compile", stderr.getvalue())
            self.assertIn("Critic: REVISE" if review else "Critic: UNAVAILABLE", stderr.getvalue())
            convert.assert_called_once()
            self.assertEqual(len(self.prompts), 1)

    def test_effort_is_explicit_for_anthropic_critic_only(self):
        with patch.object(llm, "init_chat_model") as initialize:
            llm.make_model("anthropic:claude-sonnet-5", for_critic=True)
            self.assertEqual(initialize.call_args.kwargs["effort"], "medium")
            llm.make_model("anthropic:claude-sonnet-5")
            self.assertNotIn("effort", initialize.call_args.kwargs)
            llm.make_model("google_genai:example", for_critic=True)
            self.assertNotIn("effort", initialize.call_args.kwargs)


if __name__ == "__main__":
    unittest.main()

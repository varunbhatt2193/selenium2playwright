"""Step 6.3: the attempt cap is honored by the graph, recorded by the plan, and diffed fairly."""

import copy
import io
import os
import unittest
from contextlib import closing, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch
from uuid import uuid4

from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda

from selenium2playwright import eval_experiment, eval_target, graph
from selenium2playwright.eval_compare import compare_reports, render_comparison_markdown
from selenium2playwright.eval_plan import build_plan, digest
from selenium2playwright.eval_report import assemble_report
from selenium2playwright.reflection import MAX_ATTEMPTS, resolve_attempt_cap
from selenium2playwright.schemas import ConversionResult, Critique
from test_eval_experiment import OfflineClient, fixed_records

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "samples/selenium-suite/pages/LoginPage.ts"
GOLDEN = (ROOT / "samples/playwright-golden/pages/LoginPage.ts").read_text()
BROKEN = GOLDEN.replace("await this.usernameInput.fill", "this.usernameInput.fill")
REVISE = Critique(verdict="revise", fixes=["Await usernameInput.fill() to prevent a floating Promise."])


class AttemptCapGraphTests(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ, {"LANGSMITH_TRACING": "false", "LANGCHAIN_TRACING_V2": "false"})
        env.start()
        self.addCleanup(env.stop)

    def replies(self, drafts, reviews):
        queues = {ConversionResult: iter(drafts), Critique: iter(reviews)}
        self.calls = []

        def structured(schema, **kwargs):
            def respond(prompt):
                self.calls.append(schema.__name__)
                return {"parsed": next(queues[schema]), "parsing_error": None,
                        "raw": AIMessage(content="", usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15})}
            return RunnableLambda(respond)

        model = Mock()
        model.with_structured_output.side_effect = structured
        return patch.object(graph, "make_model", return_value=model)

    def test_cap_of_one_reviews_but_never_repairs(self):
        # The critic asks for a revision, and the code really fails lint; with a
        # budget of 1 the graph must still stop after a single conversion.
        with self.replies([ConversionResult(code=BROKEN)] * MAX_ATTEMPTS, [REVISE] * MAX_ATTEMPTS):
            final = graph.build_graph().invoke({"source_path": str(SOURCE), "max_attempts": 1})
        self.assertEqual(self.calls, ["ConversionResult", "Critique"])
        self.assertEqual((final["max_attempts"], final["iteration"], final["report"].status), (1, 1, "needs-review"))
        self.assertEqual(final["report"].critique.verdict, "revise")
        self.assertIn("1 of 1", final["report"].reason)

    def test_default_and_explicit_three_still_repair(self):
        for inputs in ({"source_path": str(SOURCE)}, {"source_path": str(SOURCE), "max_attempts": 3}):
            with self.subTest(inputs=inputs), self.replies([ConversionResult(code=BROKEN), ConversionResult(code=GOLDEN)],
                                                            [REVISE, Critique(verdict="pass", fixes=[])]):
                final = graph.build_graph().invoke(inputs)
            self.assertEqual((final["max_attempts"], final["report"].status, final["report"].attempts), (3, "passed", 2))

    def test_invalid_caps_are_rejected_before_any_model_call(self):
        for bad in (0, 4, -1, True, 2.0, "2"):
            with self.subTest(cap=bad):
                with self.assertRaises(ValueError):
                    resolve_attempt_cap(bad)
                with self.replies([], []), self.assertRaises(ValueError):
                    graph.build_graph().invoke({"source_path": str(SOURCE), "max_attempts": bad})
                self.assertEqual(self.calls, [])
        self.assertEqual(resolve_attempt_cap(None), MAX_ATTEMPTS)

    def test_cli_flag_reaches_the_graph(self):
        with self.replies([ConversionResult(code=BROKEN)], [REVISE]), redirect_stdout(io.StringIO()), \
                patch("sys.stderr", new_callable=io.StringIO) as stderr:
            code = graph.main([str(SOURCE), "--max-attempts", "1"])
        self.assertEqual((code, self.calls), (1, ["ConversionResult", "Critique"]))
        self.assertIn("(1/1 attempts)", stderr.getvalue())


class AttemptCapEvaluationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.single = build_plan(ROOT, max_attempts=1, phase="6.3")
        cls.reflective = build_plan(ROOT, max_attempts=3, phase="6.3")

    def test_target_forwards_the_cap_and_records_it(self):
        inputs = next(iter(self.single["examples"].values()))["inputs"]
        with patch.object(eval_target, "build_graph") as build:
            build.return_value.invoke.return_value = {"status": "refused", "refusal": "test"}
            output = eval_target.conversion_target(inputs, max_attempts=1)
        graph_inputs, config = build.return_value.invoke.call_args.args[0], build.return_value.invoke.call_args.kwargs["config"]
        self.assertEqual((graph_inputs["max_attempts"], output["max_attempts"]), (1, 1))
        self.assertIn("attempts:1", config["tags"])
        # A bad cap is a harness mistake that would affect every row, so it
        # raises immediately instead of becoming twelve adapter_error rows.
        with patch.object(eval_target, "build_graph") as build, self.assertRaises(ValueError):
            eval_target.conversion_target(inputs, max_attempts=9)
        build.assert_not_called()

    def test_plans_differ_only_in_the_cap_and_hash_differently(self):
        a, b = self.single["metadata"]["configuration"], self.reflective["metadata"]["configuration"]
        self.assertEqual((a["max_attempts"], b["max_attempts"]), (1, 3))
        self.assertEqual({k for k in a if a[k] != b[k]}, {"max_attempts"})
        self.assertNotEqual(digest(a), digest(b))
        self.assertEqual(self.single["metadata"]["phase"], "6.3")
        self.assertEqual(self.single["examples"], self.reflective["examples"])

    def test_live_runner_binds_the_plan_cap_to_the_real_target(self):
        class Empty:
            experiment_name = "s2p-6.3-local"
            experiment_id, url = uuid4(), "http://example.invalid"
            def __iter__(self):
                return iter(())
        with TemporaryDirectory() as temporary, closing(OfflineClient(self.single)) as client, \
                patch.object(eval_experiment, "evaluate", return_value=Empty()) as evaluate, \
                patch.object(eval_experiment.env, "model_name", return_value=self.single["metadata"]["configuration"]["model"]):
            eval_experiment.run_experiment(self.single, client, Path(temporary) / "run")
        target = evaluate.call_args.args[0]
        self.assertIs(target.func, eval_target.conversion_target)
        self.assertEqual(target.keywords, {"max_attempts": 1})
        self.assertTrue(evaluate.call_args.kwargs["experiment_prefix"].startswith("s2p-6.3-claude-opus-5-attempts1"))
        self.assertEqual(evaluate.call_args.kwargs["metadata"]["configuration"]["max_attempts"], 1)

    def _report(self, plan, *, break_case=None, attempts=1):
        records = fixed_records(plan)
        for record in records:
            record["run"]["outputs"]["report"]["attempts"] = attempts
            record["run"]["outputs"].update(usage={"total_tokens": 10 * attempts}, critic_usage={"total_tokens": 5},
                                            elapsed_seconds=2.0 * attempts)
        for record in records:
            if plan["examples"][record["example_id"]]["metadata"]["case_id"] == break_case:
                record["run"]["outputs"]["report"]["status"] = "needs-review"
                for item in record["feedback"]:
                    if item["key"] == "compiles":
                        item["score"], item["evaluator_info"]["status"] = 0, "failed"
                        item["evaluator_info"]["report"]["passed"] = False
                    if item["key"] == "compiles_status":
                        item["value"] = "failed"
        report = assemble_report(plan, records, {"id": str(uuid4()), "name": "x", "url": "u", "mode": "test"})
        report["cloud_verification"] = {"status": "verified"}
        return report

    def test_comparison_reports_the_delta_per_metric_and_per_case(self):
        comparison = compare_reports(self._report(self.single, break_case="login-page"),
                                     self._report(self.reflective, attempts=2))
        self.assertTrue(comparison["comparable"], comparison["issues"])
        self.assertEqual(comparison["delta"]["passes"], {"compiles": 1, "residue_free": 0, "typed_lint_pass": 0,
                                                         "parity_pass": 0, "all_static_passed": 1, "graph_report_passed": 1})
        self.assertEqual(comparison["delta"]["percent_points"]["all_static_passed"], 8.33)
        self.assertEqual(comparison["delta"]["target_seconds"], {"total": "24.0", "ratio": "2.000"})
        self.assertEqual(comparison["delta"]["actor_calls"]["total"], 12)
        self.assertEqual(comparison["case_changes"], {"improved": 1, "same": 11})
        self.assertIn("11/12 (91.67%) | 12/12 (100.0%) | +1 (+8.33 pts)", render_comparison_markdown(comparison))

    def test_comparison_refuses_arms_that_differ_in_more_than_the_cap(self):
        other = copy.deepcopy(self.reflective)
        other["metadata"]["configuration"]["model"] = "anthropic:claude-sonnet-5"
        other["dataset_version"] = "2000-01-01T00:00:00+00:00"
        comparison = compare_reports(self._report(self.single), self._report(other))
        self.assertFalse(comparison["comparable"])
        self.assertIn("configuration.model differs between arms", comparison["issues"])
        self.assertIn("dataset_version differs between arms", comparison["issues"])
        swapped = compare_reports(self._report(self.reflective), self._report(self.single))
        self.assertIn("expected arm A = 1 attempt and arm B > 1, got 3 and 1", swapped["issues"])
        unverified = self._report(self.reflective)
        unverified["cloud_verification"] = {"status": "unverified"}
        self.assertIn("arm B cloud readback is unverified", compare_reports(self._report(self.single), unverified)["issues"])

    def test_comparison_refuses_an_arm_with_provider_errors(self):
        # Seen live on 2026-09-06: the account ran out of credits during arm B.
        # A 400 from the SDK is not a model result, so the arm is refused.
        broken = self._report(self.reflective)
        for row in broken["rows"]:
            if row["case_id"] == "windows-test":
                row["outputs"]["report"]["errors"] = ["Error code: 400 - {'type': 'error', 'error': {'type': "
                                                     "'invalid_request_error', 'message': 'Your credit balance is too low'}}"]
        comparison = compare_reports(self._report(self.single), broken)
        self.assertFalse(comparison["comparable"])
        self.assertIn("arm B has provider errors (infrastructure, not model quality) on windows-test; rerun that arm",
                      comparison["issues"])
        self.assertEqual(comparison["arms"]["reflective"]["provider_error_rows"], ["windows-test"])


if __name__ == "__main__":
    unittest.main()

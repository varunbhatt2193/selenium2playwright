"""Judge evaluator, calibration mutations, and summaries — scripted model, no network."""

import json
import os
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from langchain_core.language_models import BaseChatModel
from langchain_core.runnables import RunnableLambda
from langsmith import tracing_context
from langsmith.evaluation import EvaluationResult

from selenium2playwright import eval_calibration as calibration
from selenium2playwright import eval_judge as judge_module
from selenium2playwright import eval_judge_pass as judge_pass
from selenium2playwright.eval_collection import build_collection
from selenium2playwright.eval_judge import CHOICES, FEEDBACK_KEY, IdiomaticJudge

ROOT = Path(__file__).resolve().parents[1]


class ScriptedJudgeModel(BaseChatModel):
    """Returns pre-written structured answers; records the prompts it received."""
    script: list[Any] = []
    seen: list[Any] = []

    @property
    def _llm_type(self) -> str:
        return "scripted-judge"

    def _generate(self, *args, **kwargs):  # pragma: no cover - the judge never calls plain generate
        raise AssertionError("openevals must use with_structured_output")

    def with_structured_output(self, schema, **kwargs):
        self.seen.append(schema)

        def answer(messages):
            self.seen.append(messages)
            item = self.script.pop(0)
            if isinstance(item, Exception):
                raise item
            return item
        return RunnableLambda(answer)


class JudgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        collection = build_collection(ROOT / "samples", ROOT / "docs/evaluation-fixture-evidence.json")
        cls.rows = {row["metadata"]["case_id"]: row for row in collection["examples"]}

    def setUp(self):
        self.enterContext(patch.dict(os.environ, {"LANGSMITH_TRACING": "false", "LANGCHAIN_TRACING_V2": "false"}))
        self.enterContext(tracing_context(enabled=False))

    def judge_with(self, *answers):
        model = ScriptedJudgeModel(script=list(answers), seen=[])
        return IdiomaticJudge(model=model, model_name="fake:judge"), model

    def test_golden_score_passes_through_with_prompt_containing_all_three_texts(self):
        row = self.rows["login-page"]
        judge, model = self.judge_with({"reasoning": "Clean locators.", "score": 5.0})
        score, status = judge(row["inputs"], {"code": row["outputs"]["code"]}, row["outputs"])
        self.assertEqual((score["key"], score["score"], status["value"]), (FEEDBACK_KEY, 5, "scored"))
        self.assertEqual(score["comment"], "Clean locators.")
        info = score["evaluator_info"]
        self.assertEqual((info["judge_model"], info["status"], info["error"]), ("fake:judge", "scored", None))
        self.assertEqual(info["rubric_sha256"], judge_module.RUBRIC_SHA256)
        EvaluationResult.model_validate(score)
        self.assertEqual(json.loads(json.dumps(score)), score)
        schema, messages = model.seen
        self.assertEqual(schema["properties"]["score"]["enum"], [float(c) for c in CHOICES])
        self.assertEqual(list(schema["properties"]), ["reasoning", "score"])  # explain first, then commit
        prompt = messages[0]["content"]
        for text in (row["inputs"]["source"], row["outputs"]["code"], "pages/LoginPage.ts", "({ page })"):
            self.assertIn(text, prompt)
        self.assertNotIn("{candidate}", prompt)

    def test_missing_output_is_reported_without_a_model_call(self):
        row = self.rows["login-test"]
        judge, model = self.judge_with()
        for outputs in (None, {}, {"code": "   "}, {"code": None}):
            score, status = judge(row["inputs"], outputs, row["outputs"])
            self.assertIsNone(score["score"])
            self.assertEqual(status["value"], "no_output")
            self.assertEqual(score["evaluator_info"]["error"]["type"], "MissingCode")
        self.assertEqual(model.seen, [])

    def test_model_failure_and_off_scale_score_become_judge_error_not_exceptions(self):
        row = self.rows["login-test"]
        judge, _ = self.judge_with(RuntimeError("provider down"), {"reasoning": "?", "score": 7.0})
        for expected in ("RuntimeError", "ValueError"):
            score, status = judge(row["inputs"], {"code": row["outputs"]["code"]}, row["outputs"])
            self.assertIsNone(score["score"])
            self.assertEqual(status["value"], "judge_error")
            self.assertEqual(score["evaluator_info"]["error"]["type"], expected)

    def test_truncated_reply_recovers_score_from_closing_sentence_or_reports_it(self):
        row = self.rows["alerts-page"]
        never = {"reasoning": "Nice work, no number here."}
        judge, _ = self.judge_with({"reasoning": "All clean.\n\nThus, the score should be: 4."}, never, never, never)
        score, status = judge(row["inputs"], {"code": row["outputs"]["code"]}, row["outputs"])
        self.assertEqual((score["score"], status["value"]), (4, "scored_from_reasoning"))
        self.assertEqual(score["comment"], "All clean.\n\nThus, the score should be: 4.")
        score, status = judge(row["inputs"], {"code": row["outputs"]["code"]}, row["outputs"])
        self.assertEqual((score["score"], status["value"]), (None, "judge_error"))
        self.assertIn("no closing sentence", score["evaluator_info"]["error"]["message"])
        for text in ("Thus, the score should be: **5**", "the score should be 3.0.", "SCORE SHOULD BE: 2"):
            self.assertEqual(IdiomaticJudge.verdict({"reasoning": text})[2], "scored_from_reasoning")

    def test_cut_short_replies_are_retried_up_to_the_cap(self):
        row = self.rows["windows-page"]
        cut = {"reasoning": "A. Locators look fine, B. ..."}
        judge, model = self.judge_with(cut, cut, {"reasoning": "Thus, the score should be: 3."}, cut, cut, cut)
        score, status = judge(row["inputs"], {"code": row["outputs"]["code"]}, row["outputs"])
        self.assertEqual((score["score"], status["value"], score["evaluator_info"]["attempts"]), (3, "scored_from_reasoning", 3))
        score, status = judge(row["inputs"], {"code": row["outputs"]["code"]}, row["outputs"])
        self.assertEqual((score["score"], status["value"], score["evaluator_info"]["attempts"]), (None, "judge_error", 3))
        self.assertEqual(model.script, [])  # exactly six model calls: 3 + 3

    def test_default_judge_resolves_model_from_env_lazily(self):
        with patch.dict(os.environ, {"S2P_MODEL": "anthropic:claude-haiku-4-5-20251001",
                                     "S2P_CRITIC_MODEL": "anthropic:claude-opus-5"}):
            os.environ.pop("S2P_JUDGE_MODEL", None)
            fresh = IdiomaticJudge()
            self.assertIsNone(fresh.model_name)  # Import/construction never touches the provider.
            with patch.object(judge_module, "make_model", return_value=ScriptedJudgeModel(
                    script=[{"reasoning": "ok", "score": 4.0}], seen=[])) as made:
                score, _ = fresh(self.rows["login-page"]["inputs"], {"code": "x"}, None)
            made.assert_called_once_with("anthropic:claude-opus-5")
            self.assertEqual((score["score"], fresh.model_name), (4, "anthropic:claude-opus-5"))
            self.assertIn("(no reference available)", made.return_value.seen[1][0]["content"])


class CalibrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.variants = calibration.build_variants(ROOT / "samples", ROOT / "docs/evaluation-fixture-evidence.json")
        cls.by_key = {(v["case_id"], v["variant"]): v for v in cls.variants}

    def test_every_golden_present_and_every_mutation_actually_changes_its_file(self):
        goldens = [v for v in self.variants if v["variant"] == "golden"]
        self.assertEqual(len(goldens), 12)
        for v in self.variants:
            if v["variant"] != "golden":
                self.assertNotEqual(v["candidate"], self.by_key[(v["case_id"], "golden")]["candidate"], v["case_id"])
        self.assertTrue(all(v["kind"] == "page-object" for v in self.variants if v["variant"] == "pom_assertions"))

    def test_mutations_hit_the_rubric_area_they_claim(self):
        xpath = self.by_key[("login-page", "xpath_locators")]["candidate"]
        self.assertIn('locator("#username")', xpath)
        self.assertIn("xpath=//button[contains(., 'Login')]", xpath)
        self.assertNotIn("getByLabel", xpath)
        values = self.by_key[("login-test", "value_assertions")]["candidate"]
        self.assertIn("expect(await loginPage.flashMessage.textContent()).toContain(", values)
        self.assertNotIn("await expect(", values)
        slept = self.by_key[("login-page", "sleeps")]["candidate"]
        self.assertEqual(slept.count("setTimeout(resolve, 2000)"), 4)  # goto, fill, fill, click
        pom = self.by_key[("login-page", "pom_assertions")]["candidate"]
        self.assertEqual(pom.count("await expect(this.page).toHaveURL(/.+/);"), 2)
        self.assertTrue(pom.startswith('import { expect, type Locator, type Page } from "@playwright/test";'))
        self.assertNotIn(("login-test", "pom_assertions"), self.by_key)

    def test_scoring_repeats_goldens_and_summary_answers_the_three_questions(self):
        subset = [v for v in self.variants if v["case_id"] in {"login-page", "login-test"}]
        golden_calls = {}

        def fake_judge(inputs, outputs, reference):
            # Goldens: 5 first time; login-page drops to 4 on its repeat. Broken files: 3.
            if outputs["code"] == reference["code"]:
                n = golden_calls[inputs["source_path"]] = golden_calls.get(inputs["source_path"], 0) + 1
                s = 4 if n == 2 and inputs["source_path"] == "pages/LoginPage.ts" else 5
            else:
                s = 3
            return ({"key": FEEDBACK_KEY, "score": s, "comment": f"r{s}", "evaluator_info": {}},
                    {"key": FEEDBACK_KEY + "_status", "value": "scored"})
        records = calibration.score_variants(subset, fake_judge, golden_repeats=2)
        self.assertEqual(len(records), len(subset) + 2)
        self.assertEqual([r["repeat"] for r in records if r["variant"] == "golden"], [1, 2, 1, 2])
        records.append({"case_id": "x", "kind": "test", "variant": "golden", "repeat": 1, "score": None,
                        "status": "judge_error", "reasoning": "", "evaluator_info": {}})
        records[0]["status"] = "scored_from_reasoning"  # Recovered verdicts still count as scored.
        summary = calibration.summarize(records, "fake:judge", "abc", "idiomatic-v1")
        self.assertEqual((summary["judge_calls"], summary["scored"], summary["unscored"]), (len(records), len(records) - 1, 1))
        self.assertEqual(summary["recovered_from_reasoning"], 1)
        self.assertEqual(summary["unscored_statuses"], {"judge_error": 1})
        self.assertEqual(summary["goldens"]["judged"], 4)
        self.assertEqual(summary["goldens"]["below_four"], [])
        agreement = summary["repeat_agreement"]
        self.assertEqual((agreement["pairs"], agreement["exact"], agreement["within_one"]), (2, 1, 2))
        self.assertEqual(agreement["disagreements"], {"login-page": [5, 4]})
        for name, v in summary["broken_goldens"].items():
            self.assertEqual(v["judged"], v["lower_than_golden"] + len(v["not_lower"]), name)
        markdown = calibration.render_markdown(summary)
        self.assertIn("| xpath_locators |", markdown)
        self.assertIn("Disagreements: {'login-page': [5, 4]}", markdown)


class JudgePassTests(unittest.TestCase):
    def test_arm_summary_disagreements_and_table(self):
        comparison = json.loads((ROOT / "docs/phase-6.5-sonnet-comparison.json").read_text())
        arm = comparison["arms"]["reflective"]
        cases = [c["case_id"] for c in comparison["per_case"]]
        records = [{"experiment": arm["experiment"]["name"], "case_id": c, "score": 5 if i % 3 else 3,
                    "status": "scored", "reasoning": ""} for i, c in enumerate(cases)]
        records[-1] |= {"score": None, "status": "judge_error"}
        records[0]["status"] = "scored_from_reasoning"
        summary = judge_pass.arm_summary(arm, records)
        self.assertEqual(summary["recovered_from_reasoning"], [cases[0]])
        self.assertEqual((summary["scheduled"], summary["judged"], summary["scored"]), (12, 12, 11))
        self.assertEqual(summary["unscored"], {cases[-1]: "judge_error"})
        self.assertEqual(sum(summary["distribution"].values()), 11)
        self.assertEqual(summary["all_static"], arm["passes"]["all_static_passed"])
        split = judge_pass.disagreements(comparison, "reflective", records)
        self.assertEqual(split["static_pass_but_judge_le3"], sorted(cases[i] for i in (0, 3, 6, 9)))
        table = judge_pass.render_table([summary])
        self.assertIn("| claude-sonnet-5 | 3 | 12/12 |", table)
        self.assertIn("| 1 |\n", table)  # one unscored row in the last column

    def test_cross_judge_agreement_counts_only_rows_both_judges_scored(self):
        def make(judge, scores):
            return {"judge_model": judge, "arms": [{"experiment": "exp-1", "model": "anthropic:claude-opus-5",
                                                    "max_attempts": 1, "mean": 4.0, "scores_by_case": scores}]}
        a = make("anthropic:claude-opus-5", {"c1": 5, "c2": 4, "c3": 3, "c4": None, "c5": 5})
        b = make("openai:gpt-5.4", {"c1": 5, "c2": 5, "c3": 1, "c4": 4, "c5": None})
        cross = judge_pass.cross_judge(a, b)
        arm = cross["arms"][0]
        self.assertEqual((arm["pairs"], arm["exact"], arm["within_one"]), (3, 1, 2))
        self.assertEqual(arm["disagreements"], {"c2": [4, 5], "c3": [3, 1]})
        self.assertEqual(cross["totals"], {"pairs": 3, "exact": 1, "within_one": 2, "b_higher": 1, "a_higher": 1})
        self.assertEqual(arm["b_minus_a"], round((0 + 1 - 2) / 3, 2))
        table = judge_pass.render_cross_table(cross)
        self.assertIn("| claude-opus-5 | 1 | 3 | 1 | 2 | 4.0 | 4.0 | -0.33 |", table)


if __name__ == "__main__":
    unittest.main()

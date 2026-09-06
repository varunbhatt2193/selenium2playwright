"""Protect denominators and distinguish conversion quality from experiment integrity."""

import copy
import unittest
from uuid import uuid4

from selenium2playwright.eval_plan import build_plan
from selenium2playwright.eval_report import assemble_report, measurement, render_markdown
from test_eval_experiment import ROOT, fixed_records


class ExperimentReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plan = build_plan(ROOT)

    def test_missing_rows_remain_in_the_denominator_and_their_scenario(self):
        records = fixed_records(self.plan)
        report = assemble_report(self.plan, records[2:], {})
        compile_score = report["aggregate"]["metrics"]["compiles"]
        self.assertEqual((compile_score["passed"], compile_score["scheduled"]), (10, 12))
        self.assertEqual(compile_score["statuses"], {"missing_result": 2, "passed": 10})
        self.assertEqual(report["by_scenario"]["alerts"]["scheduled"], 2)
        self.assertEqual(report["by_scenario"]["alerts"]["all_static_passed"], 0)
        self.assertFalse(report["local_integrity"]["complete"])
        self.assertIn("missing_result", render_markdown(report))

    def test_missing_duplicate_and_incoherent_feedback_cannot_count_as_a_pass(self):
        variants = ("missing", "duplicate", "incoherent", "lost-evidence", "lost-report", "contradictory-report")
        for variant in variants:
            with self.subTest(variant=variant):
                records = fixed_records(self.plan)
                feedback = records[0]["feedback"]
                if variant == "missing":
                    feedback.pop(0)
                elif variant == "duplicate":
                    feedback.append(copy.deepcopy(feedback[0]))
                elif variant == "incoherent":
                    feedback[1]["value"] = "failed"  # Contradicts score=1 and evidence.
                elif variant == "lost-evidence":
                    feedback[0].pop("evaluator_info")
                elif variant == "lost-report":
                    feedback[0]["evaluator_info"].pop("report")
                else:
                    feedback[0]["evaluator_info"]["report"]["passed"] = False
                report = assemble_report(self.plan, records, {})
                self.assertFalse(report["local_integrity"]["complete"])
                self.assertEqual(report["aggregate"]["metrics"]["compiles"]["passed"], 11)
                self.assertEqual(report["aggregate"]["metrics"]["residue_free"]["passed"], 12)

    def test_duplicate_and_foreign_results_do_not_increase_success_counts(self):
        records = fixed_records(self.plan)
        duplicate = copy.deepcopy(records[0])
        foreign = copy.deepcopy(records[1])
        foreign["example_id"] = str(uuid4())
        report = assemble_report(self.plan, records + [duplicate, foreign], {})
        self.assertEqual(report["local_integrity"]["received_results"], 14)
        self.assertFalse(report["local_integrity"]["complete"])
        self.assertEqual(report["aggregate"]["scheduled"], 12)
        self.assertEqual(report["aggregate"]["all_static_passed"], 11)

    def test_graph_needs_review_can_coexist_with_complete_static_passes(self):
        records = fixed_records(self.plan)
        records[0]["run"]["outputs"]["report"]["status"] = "needs-review"
        records[0]["run"]["outputs"]["report"]["result"]["todos"] = ["Verify locator"]
        report = assemble_report(self.plan, records, {})
        self.assertTrue(report["local_integrity"]["complete"])
        self.assertEqual(report["aggregate"]["all_static_passed"], 12)
        self.assertEqual(report["aggregate"]["graph_report_passed"], 11)
        self.assertIn("Verify locator", render_markdown(report))

    def test_unknown_cost_and_usage_are_not_silently_zero_filled(self):
        self.assertEqual(measurement([None, None]), {
            "total": None, "known_subtotal": None, "known_rows": 0, "missing_rows": 2})
        self.assertEqual(measurement([None, 0, "0.1"]), {
            "total": None, "known_subtotal": "0.1", "known_rows": 2, "missing_rows": 1})
        self.assertEqual(measurement([0, 0])["total"], "0")
        records = fixed_records(self.plan)
        records[0]["remote_cost_usd"] = "0.03"
        records[0]["run"]["outputs"]["usage"] = {"total_tokens": 10}
        report = assemble_report(self.plan, records, {})
        self.assertIsNone(report["aggregate"]["langsmith_root_cost_usd"]["total"])
        self.assertEqual(report["aggregate"]["langsmith_root_cost_usd"]["known_subtotal"], "0.03")
        self.assertEqual(report["aggregate"]["actor_total_tokens"]["missing_rows"], 11)

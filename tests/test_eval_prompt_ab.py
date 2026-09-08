"""Step 11.1b — a playbook A/B must refuse to be anything else.

The danger with "we changed the prompt and the score went up" is that something
else changed too: the code, the dataset, the attempt budget, the model. These
tests exercise the refusals rather than the happy path, because the refusals are
what make the published number mean something. No network, no model.
"""

import copy
import unittest

from selenium2playwright.eval_hardcases import CASES
from selenium2playwright.eval_plan import build_plan
from selenium2playwright.eval_prompt_ab import (
    ALLOWED_FILE_CHANGES, changed_files, compare_prompt_arms, render_prompt_ab_markdown,
)
from selenium2playwright.eval_report import assemble_report
from test_eval_experiment import ROOT, fixed_records

PLAYBOOK = next(iter(ALLOWED_FILE_CHANGES))


def finished(report: dict) -> dict:
    """Mark a locally assembled report as cloud-verified, the way a real run ends."""
    report["cloud_verification"] = {"status": "verified"}
    return report


class PromptAbTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plan = build_plan(ROOT, benchmark="hard")

    def arms(self):
        """Two identical reports whose only difference is the playbook's hash."""
        before = finished(assemble_report(self.plan, fixed_records(self.plan), {"name": "A"}))
        after = copy.deepcopy(before)
        after["experiment"] = {"name": "B"}
        after["plan"]["metadata"]["configuration"]["file_sha256"][PLAYBOOK] = "f" * 64
        after["plan"]["metadata"]["configuration"]["git_revision"] = "b" * 40
        return before, after

    def test_a_playbook_only_edit_is_comparable(self):
        before, after = self.arms()
        comparison = compare_prompt_arms(before, after)
        self.assertTrue(comparison["comparable"], comparison["issues"])
        self.assertEqual(comparison["edited_files"], [PLAYBOOK])

    def test_two_identical_runs_are_not_a_prompt_experiment(self):
        before = finished(assemble_report(self.plan, fixed_records(self.plan), {"name": "A"}))
        comparison = compare_prompt_arms(before, copy.deepcopy(before))
        self.assertFalse(comparison["comparable"])
        self.assertIn("no prompt edit to measure", " ".join(comparison["issues"]))

    def test_a_source_edit_disqualifies_the_comparison(self):
        before, after = self.arms()
        after["plan"]["metadata"]["configuration"]["file_sha256"][
            "src/selenium2playwright/graph.py"] = "e" * 64
        comparison = compare_prompt_arms(before, after)
        self.assertFalse(comparison["comparable"])
        self.assertIn("files other than the playbook changed", " ".join(comparison["issues"]))

    def test_a_different_attempt_budget_disqualifies_the_comparison(self):
        before, after = self.arms()
        after["plan"]["metadata"]["configuration"]["max_attempts"] = 1
        comparison = compare_prompt_arms(before, after)
        self.assertFalse(comparison["comparable"])
        self.assertIn("configuration.max_attempts differs", " ".join(comparison["issues"]))

    def test_a_different_model_disqualifies_the_comparison(self):
        before, after = self.arms()
        after["plan"]["metadata"]["configuration"]["model"] = "openai:gpt-4.1-mini"
        comparison = compare_prompt_arms(before, after)
        self.assertFalse(comparison["comparable"])
        self.assertIn("configuration.model differs", " ".join(comparison["issues"]))

    def test_unverified_cloud_readback_disqualifies_an_arm(self):
        before, after = self.arms()
        after["cloud_verification"] = {"status": "incomplete"}
        comparison = compare_prompt_arms(before, after)
        self.assertFalse(comparison["comparable"])
        self.assertIn("arm B cloud readback is incomplete", " ".join(comparison["issues"]))

    def test_changed_files_reports_additions_and_removals(self):
        a = {"file_sha256": {"x": "1", "y": "2"}}
        b = {"file_sha256": {"x": "1", "z": "3"}}
        self.assertEqual(changed_files(a, b), ["y", "z"])


class ReservedCaseTests(unittest.TestCase):
    """Reserving a hard case is a claim about honesty; it has to be visible."""

    @classmethod
    def setUpClass(cls):
        cls.plan = build_plan(ROOT, benchmark="hard")

    def comparison(self, reserved):
        before = finished(assemble_report(self.plan, fixed_records(self.plan), {"name": "A"}))
        after = copy.deepcopy(before)
        after["experiment"] = {"name": "B"}
        after["plan"]["metadata"]["configuration"]["file_sha256"][PLAYBOOK] = "f" * 64
        return compare_prompt_arms(before, after, reserved=reserved)

    def test_reserved_cases_are_excluded_from_the_tuned_list(self):
        comparison = self.comparison((10, 11))
        self.assertEqual(sorted(comparison["reserved"]), ["10", "11"])
        self.assertNotIn(10, comparison["tuned_for"])
        self.assertNotIn(11, comparison["tuned_for"])
        claimed = {number for case in CASES for number in case.covers}
        self.assertEqual(set(comparison["tuned_for"]), claimed - {10, 11})

    def test_every_pattern_appears_in_the_scorecard_with_its_words(self):
        markdown = render_prompt_ab_markdown(self.comparison((10, 11)))
        self.assertIn("## Per hard case", markdown)
        self.assertIn("reserved", markdown)
        self.assertIn("executeScript workarounds", markdown)


if __name__ == "__main__":
    unittest.main()

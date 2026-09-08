"""Step 11.1 — keep the hard-case benchmark honest about what it covers.

The value of this dataset is the claim "these twelve patterns are measured".
These tests exist so that claim cannot quietly stop being true: a retired
fixture, an edited golden, a hard case nobody wrote a row for, or a page object
whose browser evidence points at a test that no longer runs it. No network, no
model, no browser — the browser evidence itself is measured by
scripts/measure_hard_fixtures.py and read back here as data.
"""

import json
import re
import shutil
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from selenium2playwright.eval_collection import build_collection
from selenium2playwright.eval_plan import build_plan
from selenium2playwright.eval_report import assemble_report, render_markdown
from selenium2playwright.eval_hard_collection import (
    build_hard_collection, check_evidence, check_manifest,
)
from selenium2playwright.eval_hardcases import (
    CASES, COVERED_BY_BASE_DATASET, GOLDEN_DIR, HARD_CASES,
    PLANNED_BROWSER_TEST_COUNTS, SOURCE_DIR,
)
from test_eval_experiment import fixed_records

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "samples"
EVIDENCE = ROOT / "docs/evaluation-hard-fixture-evidence.json"
# The name the Phase 6.1 dataset was published under; see docs/phase-6.1-receipt.json.
PUBLISHED_BASE_DATASET = "selenium2playwright-v1-4920b5f319d8"


def evidence() -> dict:
    return json.loads(EVIDENCE.read_text(encoding="utf-8"))


class CoverageTests(unittest.TestCase):
    def test_every_hard_case_has_a_home(self):
        here = {number for case in CASES for number in case.covers}
        self.assertEqual(here | set(COVERED_BY_BASE_DATASET), set(HARD_CASES))

    def test_the_two_cross_referenced_cases_are_not_also_claimed_here(self):
        here = {number for case in CASES for number in case.covers}
        self.assertEqual(here & set(COVERED_BY_BASE_DATASET), set())

    def test_hard_case_numbers_are_the_twelve_from_plan_review(self):
        self.assertEqual(sorted(HARD_CASES), list(range(1, 13)))

    def test_every_row_names_at_least_one_hard_case(self):
        for case in CASES:
            with self.subTest(case.case_id):
                self.assertTrue(case.covers)

    def test_the_manifest_as_written_passes_its_own_checks(self):
        check_manifest(CASES)


class ManifestRejectionTests(unittest.TestCase):
    """Each of these is a way the benchmark could quietly shrink."""

    def reject(self, cases, fragment):
        with self.assertRaises(ValueError) as caught:
            check_manifest(cases)
        self.assertIn(fragment, str(caught.exception))

    def test_a_hard_case_with_no_fixture_is_refused(self):
        stripped = tuple(replace(case, covers=tuple(n for n in case.covers if n != 4))
                         for case in CASES)
        self.reject(stripped, "no fixture and no cross-reference")

    def test_claiming_a_case_the_base_dataset_owns_is_refused(self):
        doubled = tuple(replace(case, covers=case.covers + (2,)) if case.case_id == "hovers-page"
                        else case for case in CASES)
        self.reject(doubled, "claimed by both benchmarks")

    def test_an_unknown_hard_case_number_is_refused(self):
        invented = tuple(replace(case, covers=case.covers + (99,)) if case.case_id == "hovers-page"
                         else case for case in CASES)
        self.reject(invented, "do not exist")

    def test_a_page_object_must_say_whose_browser_run_covers_it(self):
        orphaned = tuple(replace(case, browser_evidence_from="") if case.case_id == "hovers-page"
                         else case for case in CASES)
        self.reject(orphaned, "borrow evidence from a test row")

    def test_a_test_row_may_not_borrow_evidence(self):
        borrowing = tuple(replace(case, browser_evidence_from="hovers-test")
                          if case.case_id == "hovers-test" else case for case in CASES)
        self.reject(borrowing, "is its own browser evidence")

    def test_a_companion_must_be_a_declared_page_object(self):
        invented = tuple(replace(case, companions=("pages/NotDeclared.ts",))
                         if case.case_id == "hovers-test" else case for case in CASES)
        self.reject(invented, "Companion must be a declared page object")

    def test_a_duplicate_case_id_is_refused(self):
        self.reject(CASES + (CASES[0],), "Duplicate case IDs")


class FixtureTests(unittest.TestCase):
    def test_both_suites_contain_every_declared_path(self):
        for case in CASES:
            with self.subTest(case.case_id):
                self.assertTrue((SAMPLES / SOURCE_DIR / case.path).is_file())
                self.assertTrue((SAMPLES / GOLDEN_DIR / case.path).is_file())

    def test_the_two_suites_hold_the_same_files_and_nothing_else(self):
        source = {str(p.relative_to(SAMPLES / SOURCE_DIR)) for p in (SAMPLES / SOURCE_DIR).rglob("*.ts")}
        golden = {str(p.relative_to(SAMPLES / GOLDEN_DIR)) for p in (SAMPLES / GOLDEN_DIR).rglob("*.ts")}
        self.assertEqual(source, golden)
        self.assertEqual(source, {case.path for case in CASES})

    def test_sources_are_selenium_and_goldens_are_not(self):
        for case in CASES:
            with self.subTest(case.case_id):
                source = (SAMPLES / SOURCE_DIR / case.path).read_text(encoding="utf-8")
                golden = (SAMPLES / GOLDEN_DIR / case.path).read_text(encoding="utf-8")
                self.assertIn("selenium-webdriver", source)
                self.assertNotIn("selenium-webdriver", golden)
                self.assertIn("@playwright/test", golden)

    def test_test_identities_are_preserved_between_the_pairs(self):
        """Same test names, same count — the parity gate's invariant, read statically."""
        for case in CASES:
            if case.kind != "test":
                continue
            with self.subTest(case.case_id):
                source = (SAMPLES / SOURCE_DIR / case.path).read_text(encoding="utf-8")
                golden = (SAMPLES / GOLDEN_DIR / case.path).read_text(encoding="utf-8")
                names = re.findall(r"""^\s*it\(["'](.+?)["'],""", source, re.MULTILINE)
                converted = re.findall(r"""^\s*test\(["'](.+?)["'],""", golden, re.MULTILINE)
                self.assertEqual(names, converted)
                self.assertEqual(len(names), PLANNED_BROWSER_TEST_COUNTS[case.case_id])

    def test_the_legacy_row_really_has_no_await(self):
        """Hard case 12 is only a hard case while the source stays promise-chained."""
        source = (SAMPLES / SOURCE_DIR / "tests/shared-session.spec.ts").read_text(encoding="utf-8")
        self.assertNotIn("await ", source)
        self.assertIn(".then(", source)


class EvidenceTests(unittest.TestCase):
    def test_the_recorded_evidence_matches_the_manifest(self):
        gates = check_evidence(evidence(), {case.case_id for case in CASES})
        self.assertEqual(set(gates), {"compile", "residue", "lint", "parity"})

    def test_a_failing_gate_is_refused(self):
        broken = evidence()
        broken["static_gates"]["lint"]["passed"] = False
        with self.assertRaises(ValueError):
            check_evidence(broken, {case.case_id for case in CASES})

    def test_evidence_for_a_different_set_of_cases_is_refused(self):
        broken = evidence()
        broken["cases"].pop("hovers-page")
        with self.assertRaises(ValueError):
            check_evidence(broken, {case.case_id for case in CASES})

    def test_both_frameworks_ran_the_same_named_tests(self):
        for case_id, checked in evidence()["cases"].items():
            with self.subTest(case_id):
                self.assertEqual([t["name"] for t in checked["source_browser"]],
                                 [t["name"] for t in checked["reference_browser"]])


class CollectionTests(unittest.TestCase):
    def setUp(self):
        self.collection = build_hard_collection(SAMPLES, EVIDENCE)

    def test_one_row_per_case_with_the_hard_cases_recorded(self):
        self.assertEqual(len(self.collection["examples"]), len(CASES))
        for row in self.collection["examples"]:
            with self.subTest(row["metadata"]["case_id"]):
                self.assertTrue(row["metadata"]["hard_cases"])
                self.assertEqual(len(row["metadata"]["hard_case_titles"]),
                                 len(row["metadata"]["hard_cases"]))
                self.assertIn("fixture_validation", row["metadata"])

    def test_inputs_never_carry_the_answer(self):
        for row in self.collection["examples"]:
            with self.subTest(row["metadata"]["case_id"]):
                self.assertEqual(set(row["inputs"]), {"source_path", "source", "context_files"})
                self.assertNotIn(row["outputs"]["code"], row["inputs"]["context_files"].values())
                self.assertNotIn("selenium-webdriver", row["outputs"]["code"])

    def test_companions_are_the_playwright_versions(self):
        row = next(r for r in self.collection["examples"]
                   if r["metadata"]["case_id"] == "shared-session-test")
        self.assertEqual(set(row["inputs"]["context_files"]),
                         {"pages/SecureAreaPage.ts", "pages/BasePage.ts"})
        for text in row["inputs"]["context_files"].values():
            self.assertIn("@playwright/test", text)

    def test_the_dataset_name_is_pinned_to_the_contents(self):
        self.assertTrue(self.collection["dataset_name"].startswith("selenium2playwright-hard-v1-"))
        self.assertIn(self.collection["collection_sha256"][:12], self.collection["dataset_name"])

    def test_an_edited_golden_invalidates_the_measured_evidence(self):
        with tempfile.TemporaryDirectory() as work:
            copy = Path(work) / "samples"
            shutil.copytree(SAMPLES / SOURCE_DIR, copy / SOURCE_DIR)
            shutil.copytree(SAMPLES / GOLDEN_DIR, copy / GOLDEN_DIR)
            target = copy / GOLDEN_DIR / "pages/HoversPage.ts"
            target.write_text(target.read_text(encoding="utf-8") + "\n// edited\n", encoding="utf-8")
            with self.assertRaises(ValueError) as caught:
                build_hard_collection(copy, EVIDENCE)
            self.assertIn("changed since browser/static verification", str(caught.exception))


class HardCaseScorecardTests(unittest.TestCase):
    """The per-hard-case view is the deliverable of 11.1b; it must not overcount.

    These use synthetic all-passing records: the question here is whether the
    grouping arithmetic is right, not how the converter scores.
    """

    @classmethod
    def setUpClass(cls):
        cls.plan = build_plan(ROOT, benchmark="hard")

    def report(self, records):
        return assemble_report(self.plan, records, {})

    def test_the_groups_are_exactly_the_hard_cases_the_fixtures_claim(self):
        report = self.report(fixed_records(self.plan))
        claimed = sorted({number for case in CASES for number in case.covers})
        self.assertEqual(sorted(int(k) for k in report["by_hard_case"]), claimed)

    def test_group_sizes_match_the_manifest(self):
        report = self.report(fixed_records(self.plan))
        for number, group in report["by_hard_case"].items():
            expected = sum(1 for case in CASES if int(number) in case.covers)
            with self.subTest(number):
                self.assertEqual(group["scheduled"], expected)

    def test_overlapping_groups_do_not_sum_to_the_experiment_total(self):
        """A row exercises several patterns; that is the point, and it must be visible."""
        report = self.report(fixed_records(self.plan))
        summed = sum(group["scheduled"] for group in report["by_hard_case"].values())
        self.assertGreater(summed, report["aggregate"]["scheduled"])

    def test_one_failing_row_fails_every_pattern_it_exercises(self):
        records = fixed_records(self.plan)
        target = next(r for r in records
                      if self.plan["examples"][r["example_id"]]["metadata"]["case_id"] == "base-page")
        for item in target["feedback"]:
            if item["key"] == "compiles":
                item["score"] = 0
            if item["key"] == "compiles_status":
                item["value"] = "failed"
            if item.get("evaluator_info", {}).get("gate") == "compile":
                item["evaluator_info"]["status"] = "failed"
                item["evaluator_info"]["report"]["passed"] = False
        report = self.report(records)
        # base-page claims 10 and 1; both groups lose exactly one all-static pass.
        for number in ("1", "10"):
            group = report["by_hard_case"][number]
            with self.subTest(number):
                self.assertEqual(group["all_static_passed"], group["scheduled"] - 1)
        self.assertEqual(report["by_hard_case"]["7"]["all_static_passed"],
                         report["by_hard_case"]["7"]["scheduled"])

    def test_the_scorecard_names_the_pattern_not_only_its_number(self):
        markdown = render_markdown(self.report(fixed_records(self.plan)))
        self.assertIn("## Per hard case", markdown)
        self.assertIn(HARD_CASES[7], markdown)

    def test_the_phase_6_1_benchmark_renders_no_hard_case_section(self):
        base_plan = build_plan(ROOT)
        report = assemble_report(base_plan, fixed_records(base_plan), {})
        self.assertEqual(report["by_hard_case"], {})
        self.assertNotIn("## Per hard case", render_markdown(report))


class BaseDatasetUnchangedTests(unittest.TestCase):
    """Step 11.1 made snapshot_example suite-agnostic; the published rows must not move."""

    def test_the_phase_6_1_dataset_still_fingerprints_the_same(self):
        base = build_collection(SAMPLES, ROOT / "docs/evaluation-fixture-evidence.json")
        self.assertEqual(base["dataset_name"], PUBLISHED_BASE_DATASET)


if __name__ == "__main__":
    unittest.main()

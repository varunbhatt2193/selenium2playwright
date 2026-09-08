"""Preflight the hard-case benchmark before any LangSmith client is created.

Same contract as eval_collection for the Phase 6.1 set, with the structure this
benchmark actually has: rows may depend on a shared base class, a page object
borrows its browser evidence from the test that exercises it, and every row
declares which of the twelve hard cases it is here to measure. The whole list
must be covered — by a fixture here or by an explicit cross-reference to the
Phase 6.1 set — or the collection does not build. Local reads only.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from selenium2playwright.eval_collection import sha256_text
from selenium2playwright.eval_dataset import snapshot_example
from selenium2playwright.eval_hardcases import (
    CASES, COVERED_BY_BASE_DATASET, GOLDEN_DIR, HARD_CASES,
    PLANNED_BROWSER_TEST_COUNTS, SOURCE_DIR, HardCase,
)


def check_manifest(cases: tuple[HardCase, ...]) -> None:
    """Refuse a manifest that has drifted from the twelve cases it claims to cover."""
    by_id = {case.case_id: case for case in cases}
    if len(by_id) != len(cases):
        raise ValueError("Duplicate case IDs in the hard-case manifest")
    if len({case.path for case in cases}) != len(cases):
        raise ValueError("Duplicate target paths in the hard-case manifest")
    if set(PLANNED_BROWSER_TEST_COUNTS) - set(by_id):
        raise ValueError("A planned browser-test row is missing from the manifest")

    covered = {number for case in cases for number in case.covers}
    if unknown := covered - set(HARD_CASES):
        raise ValueError(f"Manifest references hard cases that do not exist: {sorted(unknown)}")
    if overlap := covered & set(COVERED_BY_BASE_DATASET):
        raise ValueError(f"Hard cases claimed by both benchmarks: {sorted(overlap)}")
    if missing := set(HARD_CASES) - covered - set(COVERED_BY_BASE_DATASET):
        raise ValueError(f"Hard cases with no fixture and no cross-reference: {sorted(missing)}")

    for case in cases:
        is_test = case.case_id in PLANNED_BROWSER_TEST_COUNTS
        if case.kind != ("test" if is_test else "page-object"):
            raise ValueError(f"Case kind does not match its planned role: {case.case_id}")
        if not case.covers:
            raise ValueError(f"Every row must name the hard cases it exercises: {case.case_id}")
        # A page object runs no browser tests of its own, so it must say whose
        # run stands as its evidence. A test row is its own evidence.
        if is_test and case.browser_evidence_from:
            raise ValueError(f"A test row is its own browser evidence: {case.case_id}")
        if not is_test and case.browser_evidence_from not in PLANNED_BROWSER_TEST_COUNTS:
            raise ValueError(f"Page object must borrow evidence from a test row: {case.case_id}")
        for path in case.companions:
            companion = next((item for item in cases if item.path == path), None)
            if companion is None or companion.kind != "page-object":
                raise ValueError(f"Companion must be a declared page object: {case.case_id}")


def check_evidence(evidence: dict, case_ids: set[str]) -> dict:
    """The gates and browser runs must cover this exact manifest, all green."""
    if evidence.get("schema_version") != 1 or set(evidence["cases"]) != case_ids:
        raise ValueError("Fixture evidence must cover the same complete manifest")
    gates = evidence["static_gates"]
    if set(gates) != {"compile", "residue", "lint", "parity"} or any(
        gate["passed"] is not True or gate["findings"] for gate in gates.values()
    ):
        raise ValueError("All four golden fixture gates must have passing evidence")
    return gates


def build_hard_collection(samples_root: Path, evidence_path: Path,
                          cases: tuple[HardCase, ...] = CASES) -> dict:
    """Capture every hard-case row or fail; never silently shrink the benchmark."""
    check_manifest(cases)
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    gates = check_evidence(evidence, {case.case_id for case in cases})

    rows = []
    for case in sorted(cases, key=lambda item: item.case_id):
        if case.reference_review != "reviewed":
            raise ValueError(f"Reference review is pending: {case.case_id}")
        row = snapshot_example(case, samples_root, source_dir=SOURCE_DIR, golden_dir=GOLDEN_DIR)
        checked = evidence["cases"][case.case_id]
        if (checked["source_sha256"] != sha256_text(row["inputs"]["source"])
                or checked["reference_sha256"] != sha256_text(row["outputs"]["code"])):
            raise ValueError(f"Fixture changed since browser/static verification: {case.case_id}")
        test_id = case.case_id if case.kind == "test" else case.browser_evidence_from
        if checked["browser_test_case_id"] != test_id:
            raise ValueError(f"Wrong browser evidence association: {case.case_id}")
        for side in ("source_browser", "reference_browser"):
            tests = checked[side]
            if (len(tests) != PLANNED_BROWSER_TEST_COUNTS[test_id]
                    or any(test["status"] != "passed" for test in tests)):
                raise ValueError(f"Incomplete passing {side} evidence: {case.case_id}")
        if [t["name"] for t in checked["source_browser"]] != [t["name"] for t in checked["reference_browser"]]:
            raise ValueError(f"Browser test identities differ: {case.case_id}")
        row["metadata"] |= {
            "benchmark": "hard-cases",
            "suite": SOURCE_DIR,
            "hard_cases": list(case.covers),
            "hard_case_titles": [HARD_CASES[number] for number in case.covers],
            "fixture_validation": {
                "measured_at_utc": evidence["measured_at_utc"], "static_gates": gates,
                "browser_settings": evidence["browser_settings"], "tools": evidence["tools"],
                "report": "docs/evaluation-hard-fixture-evidence.json", **checked,
            },
        }
        rows.append(row)

    digest = sha256_text(json.dumps(rows, sort_keys=True, ensure_ascii=False))
    return {
        "schema_version": 1, "collection_sha256": digest,
        "dataset_name": f"selenium2playwright-hard-v1-{digest[:12]}", "examples": rows,
        "coverage": {
            "conversion_examples": len(rows),
            "scenarios": dict(sorted(Counter(c.scenario for c in cases).items())),
            "kinds": dict(sorted(Counter(c.kind for c in cases).items())),
            "browser_tests_per_framework": sum(PLANNED_BROWSER_TEST_COUNTS.values()),
            "hard_cases_total": len(HARD_CASES),
            "hard_cases_here": sorted({n for c in cases for n in c.covers}),
            "hard_cases_in_base_dataset": {str(k): v for k, v in sorted(COVERED_BY_BASE_DATASET.items())},
        },
    }

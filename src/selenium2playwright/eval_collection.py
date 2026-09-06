"""Preflight the whole curated benchmark before any LangSmith client is created.

One valid row is insufficient: a missing scenario would silently shrink the
denominator. Fixture evidence is bound to source/reference text hashes so an
edit cannot inherit an old browser pass. This module performs local reads only.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path

from selenium2playwright.eval_dataset import DatasetCase, snapshot_example
from selenium2playwright.eval_manifest import CASES, PLANNED_BROWSER_TEST_COUNTS


def sha256_text(text: str) -> str:
    """Identify the exact UTF-8 text that a fixture check or dataset row used."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def build_collection(samples_root: Path, evidence_path: Path,
                     cases: tuple[DatasetCase, ...] = CASES) -> dict:
    """Capture every planned row or fail; never silently skip missing/pending cases."""
    expected_ids = set(PLANNED_BROWSER_TEST_COUNTS)
    expected_ids |= {key.removesuffix("-test") + "-page" for key in expected_ids}
    if len(cases) != len(expected_ids) or {c.case_id for c in cases} != expected_ids:
        raise ValueError("Manifest must contain each of the 12 planned case IDs exactly once")
    by_path = {case.path: case for case in cases}
    if len(by_path) != len(cases):
        raise ValueError("Duplicate target paths in the manifest")
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    if evidence.get("schema_version") != 1 or set(evidence["cases"]) != expected_ids:
        raise ValueError("Fixture evidence must cover the same complete manifest")
    gates = evidence["static_gates"]
    if set(gates) != {"compile", "residue", "lint", "parity"} or any(
        gate["passed"] is not True or gate["findings"] for gate in gates.values()
    ):
        raise ValueError("All four golden fixture gates must have passing evidence")

    rows = []
    for case in sorted(cases, key=lambda item: item.case_id):
        if case.reference_review != "reviewed":
            raise ValueError(f"Reference review is pending: {case.case_id}")
        is_test = case.case_id in PLANNED_BROWSER_TEST_COUNTS
        expected_scenario = case.case_id.removesuffix("-test").removesuffix("-page")
        if case.scenario != expected_scenario:
            raise ValueError(f"Scenario does not match the planned case: {case.case_id}")
        if case.kind != ("test" if is_test else "page-object"):
            raise ValueError(f"Case kind does not match its planned role: {case.case_id}")
        if (is_test and len(case.companions) != 1) or (not is_test and case.companions):
            raise ValueError(f"Expected one POM companion per test and none per POM: {case.case_id}")
        for path in case.companions:
            companion = by_path.get(path)
            if companion is None or companion.kind != "page-object" or companion.scenario != case.scenario:
                raise ValueError(f"Companion must be the scenario's declared POM: {case.case_id}")
        row = snapshot_example(case, samples_root)
        checked = evidence["cases"][case.case_id]
        if (checked["source_sha256"] != sha256_text(row["inputs"]["source"])
                or checked["reference_sha256"] != sha256_text(row["outputs"]["code"])):
            raise ValueError(f"Fixture changed since browser/static verification: {case.case_id}")
        test_id = case.case_id if is_test else case.case_id.removesuffix("-page") + "-test"
        if checked["browser_test_case_id"] != test_id:
            raise ValueError(f"Wrong browser evidence association: {case.case_id}")
        for side in ("source_browser", "reference_browser"):
            tests = checked[side]
            if (len(tests) != PLANNED_BROWSER_TEST_COUNTS[test_id]
                    or any(test["status"] != "passed" for test in tests)):
                raise ValueError(f"Incomplete passing {side} evidence: {case.case_id}")
        if [t["name"] for t in checked["source_browser"]] != [t["name"] for t in checked["reference_browser"]]:
            raise ValueError(f"Browser test identities differ: {case.case_id}")
        # POMs point to their scenario's tests; these are not extra browser passes.
        row["metadata"]["fixture_validation"] = {
            "measured_at_utc": evidence["measured_at_utc"], "static_gates": gates,
            "browser_settings": evidence["browser_settings"], "tools": evidence["tools"],
            "report": "docs/evaluation-fixture-evidence.json", **checked,
        }
        rows.append(row)

    # Include review/evidence metadata in the collection identity. The existing
    # per-row content_sha256 deliberately identifies only the conversion task.
    digest = sha256_text(json.dumps(rows, sort_keys=True, ensure_ascii=False))
    return {
        "schema_version": 1, "collection_sha256": digest,
        "dataset_name": f"selenium2playwright-v1-{digest[:12]}", "examples": rows,
        "coverage": {"conversion_examples": len(rows),
                     "scenarios": dict(sorted(Counter(c.scenario for c in cases).items())),
                     "kinds": dict(sorted(Counter(c.kind for c in cases).items())),
                     "browser_tests_per_framework": sum(PLANNED_BROWSER_TEST_COUNTS.values())},
    }

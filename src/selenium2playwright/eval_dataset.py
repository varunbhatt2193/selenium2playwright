"""Step 6.1 — capture repeatable evaluation examples from curated sample pairs.

A dataset row is one FILE conversion, even if that file contains several tests.
This module reads local fixtures; it neither calls a model nor uploads anything.
The later evaluation adapter must materialize these snapshots before invoking
the graph: today's intake reads disk paths and cannot consume this row directly.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal


@dataclass(frozen=True)
class DatasetCase:
    """A curator's manifest entry, comparable to one data-driven test definition.

    Both suites use the same relative path, for example ``tests/login.spec.ts``.
    Companions name already-converted dependencies in the golden suite. They
    make this an isolated-file evaluation with supplied dependencies, rather
    than an evaluation of the converter generating an entire dependent suite.
    """

    case_id: str  # Stable human-readable identity, even when the source changes.
    scenario: str  # Reporting group, such as login, alerts, or iframe.
    kind: Literal["page-object", "test"]
    path: str
    expected_behaviors: tuple[str, ...]  # Acceptance criteria, not exact code matches.
    companions: tuple[str, ...] = ()
    # Compiling a reference does not make it human-reviewed. Record that separately.
    reference_review: Literal["pending", "reviewed"] = "pending"
    review_note: str = ""  # Evidence/location of the review; never inferred by this module.


def _read_typescript(root: Path, relative: str) -> str:
    """Read an explicit suite file, rejecting ambiguous paths and escaped symlinks."""
    path = PurePosixPath(relative)
    # Portable, canonical keys will later become paths in a temporary workspace.
    # Rejecting aliases also makes self-companion and duplicate checks meaningful.
    if (path.is_absolute() or ".." in path.parts or "\\" in relative
            or ":" in relative or path.as_posix() != relative or path.suffix != ".ts"):
        raise ValueError(f"Expected a canonical suite-relative .ts path: {relative!r}")
    resolved_root = root.resolve(strict=True)
    candidate = (resolved_root / relative).resolve(strict=True)
    if not candidate.is_relative_to(resolved_root):
        raise ValueError(f"Sample escapes its suite directory: {relative!r}")
    code = candidate.read_text(encoding="utf-8")
    if not code.strip():
        raise ValueError(f"Empty sample cannot define an evaluation case: {relative!r}")
    return code


def snapshot_example(case: DatasetCase, samples_root: Path) -> dict:
    """Return inputs/reference outputs/metadata in LangSmith's example format.

    Snapshot text now so later edits to the checkout cannot silently change an
    already-uploaded task. Reference code and acceptance criteria stay outside
    inputs; the future target must receive only inputs, never this entire dict.
    """
    if not case.case_id.strip() or not case.scenario.strip():
        raise ValueError("case_id and scenario must be non-blank")
    if case.kind not in ("page-object", "test"):
        raise ValueError("kind must be page-object or test")
    if not case.expected_behaviors or any(not item.strip() for item in case.expected_behaviors):
        raise ValueError("Each case needs non-blank expected behaviors")
    if case.reference_review not in ("pending", "reviewed"):
        raise ValueError("reference_review must be pending or reviewed")
    if case.reference_review == "reviewed" and not case.review_note.strip():
        raise ValueError("Reviewed references require a review note")
    if case.path in case.companions or len(set(case.companions)) != len(case.companions):
        raise ValueError("Companions must be unique and exclude the target's own reference")

    source_root = samples_root / "selenium-suite"
    golden_root = samples_root / "playwright-golden"
    source = _read_typescript(source_root, case.path)
    reference = _read_typescript(golden_root, case.path)
    companions = {}
    for relative in case.companions:
        code = _read_typescript(golden_root, relative)
        # A differently named symlink to the target would still leak its answer.
        if (golden_root / relative).resolve() == (golden_root / case.path).resolve():
            raise ValueError("A companion resolves to the target's own reference")
        companions[relative] = code

    inputs = {"source_path": case.path, "source": source, "context_files": companions}
    outputs = {"code": reference, "expected_behaviors": list(case.expected_behaviors)}
    # Sort dictionary keys so key insertion order does not change the fingerprint.
    # Include companions and acceptance criteria: changing either changes the task.
    # This identifies captured content, not review quality or the model configuration.
    captured = json.dumps({"inputs": inputs, "outputs": outputs}, sort_keys=True, ensure_ascii=False)
    fingerprint = hashlib.sha256(captured.encode("utf-8")).hexdigest()
    return {
        "inputs": inputs,
        "outputs": outputs,  # LangSmith calls these reference_outputs during evaluation.
        "metadata": {
            "schema_version": 1,
            "case_id": case.case_id,
            "scenario": case.scenario,
            "kind": case.kind,
            "context_policy": "provided-golden-companions" if companions else "standalone",
            "reference_review": case.reference_review,
            "review_note": case.review_note,
            "content_sha256": fingerprint,
        },
    }

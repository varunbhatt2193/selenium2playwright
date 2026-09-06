"""Phase 6.2 — grade final code with the existing deterministic validators.

These checks run again after graph assembly; copied graph pass flags are not
evaluation evidence. See docs/evaluation-evaluators.md for metric definitions.
"""

from __future__ import annotations

from time import perf_counter

from selenium2playwright.eval_target import validate_inputs
from selenium2playwright.schemas import Gate, ValidationReport
from selenium2playwright.validators.compile import compile_check
from selenium2playwright.validators.lint import lint_check
from selenium2playwright.validators.parity import parity_check
from selenium2playwright.validators.residue import residue_check

EVALUATOR_VERSION = "deterministic-v1"
GATE_KEYS = {"compile": "compiles", "residue": "residue_free",
             "lint": "typed_lint_pass", "parity": "parity_pass"}


def gate_feedback(gate: Gate, status: str, started: float,
                  report: ValidationReport | None = None, error: dict | None = None) -> list[dict]:
    """Package one verdict as a numeric score plus an explanatory status metric."""
    key = GATE_KEYS[gate]
    comment = status
    if report is not None:
        comment += "\n" + report.render()  # Includes warnings and source locations.
    if error is not None:
        comment += f"\n{error['type']}: {error['message']}"
    evidence = {"version": EVALUATOR_VERSION, "gate": gate, "status": status,
                "elapsed_seconds": perf_counter() - started,
                "report": report.model_dump(mode="json") if report is not None else None,
                "error": error}
    # Always emit a number: missing output/tools must not disappear from the
    # denominator. The separate status distinguishes a defect from no verdict.
    return [{"key": key, "score": int(status == "passed"), "comment": comment,
             "evaluator_info": evidence,
             "feedback_config": {"type": "continuous", "min": 0, "max": 1}},
            {"key": key + "_status", "value": status, "comment": comment}]


def evaluate_gate(inputs: dict, outputs: dict | None, gate: Gate) -> list[dict]:
    """Validate the snapshot, rerun one gate, and preserve failures as feedback."""
    started = perf_counter()
    try:
        # Validators write supplied paths into a sandbox; check paths BEFORE
        # any tool runs. This also rejects the target appearing as a companion.
        validate_inputs(inputs)
    except ValueError as exc:
        return gate_feedback(gate, "invalid_input", started,
                             error={"type": type(exc).__name__, "message": str(exc)})
    code = outputs.get("code") if isinstance(outputs, dict) else None
    if not isinstance(code, str) or not code.strip():
        return gate_feedback(gate, "no_output", started, error={
            "type": "MissingCode", "message": "Target did not return non-blank candidate text."})

    relative = inputs["source_path"]
    candidate = {relative: code}
    files = dict(inputs["context_files"]) | candidate  # Keep captured inputs unchanged.
    checks = {"compile": lambda: compile_check(files),
              "residue": lambda: residue_check(files),
              "lint": lambda: lint_check(files),
              # Only this file has a source pair; companions are already converted.
              "parity": lambda: parity_check({relative: inputs["source"]}, candidate)}
    report = None
    try:
        report = ValidationReport.model_validate(checks[gate]())
        if report.gate != gate:
            raise ValueError(f"Expected {gate} evidence, received {report.gate}")
        # A failed process without parsed diagnostics is not a usable verdict.
        # Keep its raw output instead of claiming a specific candidate defect.
        if (not report.passed and not report.findings
                or any(f.code == "validator-error" for f in report.findings)):
            return gate_feedback(gate, "tool_error", started, report, error={
                "type": "UnusableValidationReport",
                "message": "Validator could not establish a verdict; inspect findings and tool_output."})
        # Lint warnings can coexist with passed=True; honor the gate's policy.
        return gate_feedback(gate, "passed" if report.passed else "failed", started, report)
    except Exception as exc:
        # This boundary covers one evaluator, so a missing compiler cannot
        # prevent residue/lint/parity from producing their own feedback later.
        return gate_feedback(gate, "tool_error", started, report, error={
            "type": type(exc).__name__, "message": str(exc) or type(exc).__name__})


def compiles(inputs: dict, outputs: dict | None) -> list[dict]:
    """Check whether final TypeScript and its captured companions compile."""
    return evaluate_gate(inputs, outputs, "compile")


def residue_free(inputs: dict, outputs: dict | None) -> list[dict]:
    """Check whether configured forbidden Selenium/Mocha/chai patterns remain."""
    return evaluate_gate(inputs, outputs, "residue")


def typed_lint_pass(inputs: dict, outputs: dict | None) -> list[dict]:
    """Check typed ESLint errors, including promises created without awaiting."""
    return evaluate_gate(inputs, outputs, "lint")


def parity_pass(inputs: dict, outputs: dict | None) -> list[dict]:
    """Check whether tracked source tests or assertions were lost in conversion."""
    return evaluate_gate(inputs, outputs, "parity")


# The runner can pass this list directly to LangSmith's evaluate(evaluators=...).
# Argument names inputs/outputs are the SDK's binding contract, not arbitrary.
EVALUATORS = [compiles, residue_free, typed_lint_pass, parity_pass]

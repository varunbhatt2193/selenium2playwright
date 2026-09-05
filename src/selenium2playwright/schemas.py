"""The shape of a conversion result — a contract the model must fill in.

Step 2.1 got free text back and hoped it was code (one run in three came
wrapped in a markdown fence — see docs/gap-log.md #1). Step 2.2 replaces hope
with a schema: LangChain turns this class into a JSON schema, hands it to the
model as the *only* acceptable reply shape, and parses the reply back into a
ConversionResult object. Fields are typed, so `result.code` is always a str
and `result.todos` is always a list — no parsing, no fence-stripping.

The field descriptions are not decoration: they are sent to the model as part
of the schema, so they are prompt text. Write them for the model.
"""

from __future__ import annotations

from typing import Literal, Self

from pydantic import BaseModel, Field, model_validator


class ConversionResult(BaseModel):
    """One converted file plus everything the user must know about it."""

    code: str = Field(
        description=(
            "The complete converted Playwright TypeScript file, exactly as it "
            "should be written to disk. Raw source only: no markdown fences, no "
            "explanation, ends with a newline."
        )
    )
    notes: list[str] = Field(
        default_factory=list,
        description=(
            "Short notes on non-obvious decisions, one per item — e.g. which "
            "playbook rule drove a change, or why a wait was deleted. Empty if "
            "the conversion was entirely mechanical."
        ),
    )
    todos: list[str] = Field(
        default_factory=list,
        description=(
            "Every TODO(review) placed in the code, repeated here verbatim "
            "(playbook rule 25: the consolidated ledger). Empty if none."
        ),
    )


class Critique(BaseModel):
    """The reviewer's decision; step 5.2 will use it to choose whether to revise."""

    verdict: Literal["pass", "revise"] = Field(
        description="pass only when all gates pass and no concrete correctness/idiom fix is needed; otherwise revise."
    )
    fixes: list[str] = Field(
        description=(
            "Concrete, actionable fixes grounded in the source, converted code, or validator findings. "
            "Name the file/location and evidence when available; never invent a line number. "
            "Use a TODO(review) task for unresolved uncertainty. Empty only for pass."
        )
    )

    @model_validator(mode="after")
    def consistent_verdict(self) -> Self:
        """Contradictory or empty reviews are parse failures, not usable decisions."""
        if (self.verdict == "pass") != (not self.fixes):
            raise ValueError("pass requires no fixes; revise requires at least one fix")
        if any(not fix.strip() for fix in self.fixes):
            raise ValueError("fixes must contain non-blank instructions")
        return self


# --- validation --------------------------------------------------------------
# Every deterministic gate (compile, residue, lint, parity) reports in this one
# shape. The critic (Phase 5) reads it as text; evals (Phase 6) read it as data.

Gate = Literal["compile", "residue", "lint", "parity"]


class Finding(BaseModel):
    """One concrete problem at one place in one file."""

    gate: Gate
    file: str
    line: int | None = None  # None when the finding is about the whole file
    column: int | None = None
    code: str  # e.g. "TS2551", "selenium-import", "no-floating-promises", "missing-test"
    message: str

    def render(self) -> str:
        """`file:line:col code message` — the line the critic will quote back."""
        where = self.file + (f":{self.line}" if self.line else "") + (f":{self.column}" if self.column else "")
        return f"{where} {self.code} {self.message}"


class ValidationReport(BaseModel):
    """Verdict of one gate over one set of files."""

    gate: Gate
    passed: bool
    findings: list[Finding] = Field(default_factory=list)
    tool_output: str = ""  # raw stdout/stderr, kept for the trace and for debugging the parser

    @property
    def summary(self) -> str:
        n = len(self.findings)
        return f"{self.gate}: {'passed' if self.passed else f'{n} finding' + ('s' if n != 1 else '')}"

    def render(self) -> str:
        """Human/critic-readable block: summary line then one line per finding."""
        return "\n".join([self.summary, *(f.render() for f in self.findings)])


class ConversionReport(BaseModel):
    """Final artifact after success, the attempt limit, or an unavailable tool/model."""

    status: Literal["passed", "needs-review"]
    attempts: int
    reason: str
    result: ConversionResult | None  # None only when no conversion could be produced
    validation: list[ValidationReport]
    critique: Critique | None
    errors: list[str] = Field(default_factory=list)

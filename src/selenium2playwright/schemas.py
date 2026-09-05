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

from pydantic import BaseModel, Field


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

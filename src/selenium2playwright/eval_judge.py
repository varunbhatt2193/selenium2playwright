"""Phase 6.4 — LLM-as-judge for idiomatic Playwright, built on openevals.

The four static evaluators answer exact questions: compiles? residue-free?
lint clean? every test kept? This evaluator answers a style question no tool
can: does the converted file read like Playwright written by someone who knows
Playwright? A model scores 1-5 against the written rubric below.

The score is an opinion. docs/evaluation-judge.md explains the calibration
(goldens high, broken goldens lower, repeats agree) that must pass before the
opinion is trusted, and why the judge is kept independent of the graph's critic.
"""

from __future__ import annotations

import hashlib
import re
from time import perf_counter

from langchain_core.language_models import BaseChatModel
from openevals.llm import create_llm_as_judge

from selenium2playwright import env
from selenium2playwright.llm import make_model

JUDGE_VERSION = "idiomatic-v1"
FEEDBACK_KEY = "idiomatic_playwright"
CHOICES = [1, 2, 3, 4, 5]
# Both count as a verdict; the second says the structured field was missing and
# the number came from the model's own closing sentence instead.
SCORED_STATUSES = ("scored", "scored_from_reasoning")
# A provider can end a structured reply early (Anthropic stop_reason "refusal"
# did so on ~1 in 5 calibration calls, at random). A reply with neither the score
# field nor the closing sentence carries no verdict, so it is asked again, bounded.
MAX_JUDGE_ATTEMPTS = 3
CLOSING_SENTENCE = re.compile(r"score should be:?\s*\**\s*([1-5])(?:\.0)?\b", re.I)

# Reasoning first, score last: the model explains before it commits. The same
# shape openevals builds by default; declared here so we receive the raw reply.
SCORE_SCHEMA = {
    "title": "score", "type": "object", "additionalProperties": False,
    "description": "The rubric score with the reasoning that led to it.",
    "properties": {
        "reasoning": {"type": "string", "description": "Rubric-by-rubric reasoning. You MUST end with the "
                      "sentence: Thus, the score should be: SCORE_YOU_ASSIGN."},
        "score": {"type": "number", "enum": [float(c) for c in CHOICES],
                  "description": "The 1-5 score, matching the closing sentence."},
    },
    "required": ["reasoning", "score"],
}

# openevals fills this with str.format: our extra keyword arguments (file_path,
# source, candidate, golden) land in the named slots. Literal braces are doubled.
RUBRIC = """You are grading ONE TypeScript file that was converted from Selenium WebDriver to Playwright Test.
Score how idiomatic the Playwright is, on a 1-5 scale, using only the rubric below.

Do NOT grade correctness. Compilation, leftover Selenium residue, lint, and test/assertion parity are already checked by exact tools. Grade style only.

Rubric
A. Locators. Prefers getByRole / getByLabel / getByPlaceholder / getByText / getByTestId whenever the page offers them. Falls back to page.locator(css) only when no user-facing locator exists. XPath where a user-facing locator exists is a serious slip.
B. Web-first assertions and waiting. Assertions act on locators and retry: await expect(locator).toBeVisible() / toContainText() / toHaveValue(). Asserting on extracted values, such as expect(await locator.textContent()).toContain(...), is Selenium thinking. No waitForTimeout, no sleeps, no hand-rolled polling, no explicit waits that Playwright's auto-waiting already covers.
C. Shape. A page object has readonly Locator fields set in the constructor, actions as async methods, and no assertions. A test uses @playwright/test fixtures ({{ page }}), beforeEach for shared setup, and no manual browser lifecycle.

Score anchors
5 = all three areas clean; reads like the reference or better.
4 = one small slip (a CSS locator that had a role/label alternative, one redundant wait). Nothing a reviewer would send back.
3 = works but old habits show: value-based asserts, several CSS locators, or a wait hiding a missing web-first assertion.
2 = Selenium thinking in Playwright syntax: extraction-then-assert throughout, sleeps, assertions inside a page object.
1 = not idiomatic Playwright in any meaningful way, or not the requested file.

The reference below is ONE good answer, written by hand. A different structure is fine if it is equally idiomatic. Do not reward copying it; do not punish differing from it.

File under review: {file_path}

<selenium_source>
{source}
</selenium_source>

<converted_candidate>
{candidate}
</converted_candidate>

<reference_playwright>
{golden}
</reference_playwright>

Grade the converted candidate. Name the specific lines behind each deduction, then give the score.
"""

# A receipt must say which rubric produced a score; editing the text changes this.
RUBRIC_SHA256 = hashlib.sha256(RUBRIC.encode("utf-8")).hexdigest()


class IdiomaticJudge:
    """A LangSmith evaluator: (inputs, outputs, reference_outputs) -> feedback list.

    Wraps one openevals judge. The model is built lazily so importing this
    module never needs an API key; offline tests inject a fake through `model`.
    """

    def __init__(self, model: BaseChatModel | None = None, model_name: str | None = None):
        self._model = model
        self.model_name = model_name or ("injected" if model is not None else None)
        self._scorer = None

    def _ensure_scorer(self):
        if self._scorer is None:
            if self._model is None:
                self.model_name = self.model_name or env.judge_model_name()
                self._model = make_model(self.model_name)
            # output_schema makes openevals hand back the parsed reply itself instead
            # of indexing response["score"] for us: a provider can cut a tool call
            # short (Anthropic stop_reason "refusal" did, on the alerts files) and we
            # want to keep the reasoning and recover the verdict, not raise KeyError.
            self._scorer = create_llm_as_judge(prompt=RUBRIC, feedback_key=FEEDBACK_KEY, judge=self._model,
                                               output_schema=SCORE_SCHEMA)
        return self._scorer

    @staticmethod
    def complete(reply: dict) -> bool:
        """True when the reply carries a verdict: the score field or the closing sentence."""
        return reply.get("score") is not None or CLOSING_SENTENCE.search(str(reply.get("reasoning") or "")) is not None

    @staticmethod
    def verdict(reply: dict) -> tuple[int, str, str]:
        """(score, reasoning, status) from a structured reply, complete or cut short."""
        reasoning = str(reply.get("reasoning") or "")
        score = reply.get("score")
        if score is not None:
            if score not in CHOICES:
                raise ValueError(f"Judge returned {score!r}, not one of {CHOICES}")
            return int(score), reasoning, "scored"
        found = CLOSING_SENTENCE.search(reasoning)
        if found is None:
            raise ValueError("Structured reply had no score and no closing sentence to recover it from")
        return int(found.group(1)), reasoning, "scored_from_reasoning"

    def feedback(self, status: str, started: float, score: int | None = None, reasoning: str = "",
                 error: dict | None = None, attempts: int = 0) -> list[dict]:
        """Same two-metric shape as the static gates: a number and a status."""
        comment = reasoning or status
        if error is not None:
            comment += f"\n{error['type']}: {error['message']}"
        info = {"version": JUDGE_VERSION, "judge_model": self.model_name, "rubric_sha256": RUBRIC_SHA256,
                "status": status, "attempts": attempts, "elapsed_seconds": perf_counter() - started, "error": error}
        # No verdict is reported as score None, not 0: a 0 on a 1-5 scale would
        # drag averages and read as "terrible code" instead of "not judged".
        # Receipts count unscored rows explicitly so they never leave the denominator.
        return [{"key": FEEDBACK_KEY, "score": score, "comment": comment, "evaluator_info": info},
                {"key": FEEDBACK_KEY + "_status", "value": status, "comment": comment}]

    def __call__(self, inputs: dict, outputs: dict | None, reference_outputs: dict | None = None) -> list[dict]:
        started = perf_counter()
        code = outputs.get("code") if isinstance(outputs, dict) else None
        if not isinstance(code, str) or not code.strip():
            return self.feedback("no_output", started, error={
                "type": "MissingCode", "message": "Target did not return non-blank candidate text."})
        golden = reference_outputs.get("code") if isinstance(reference_outputs, dict) else None
        attempts = 0
        try:
            scorer = self._ensure_scorer()
            for attempts in range(1, MAX_JUDGE_ATTEMPTS + 1):
                reply = scorer(inputs=inputs, outputs=outputs, reference_outputs=reference_outputs,
                               file_path=inputs.get("source_path", "(unknown)"),
                               source=inputs.get("source", "(source not captured)"),
                               candidate=code, golden=golden or "(no reference available)")
                if not isinstance(reply, dict):
                    raise TypeError(f"Expected the structured reply as a dict, got {type(reply).__name__}")
                if self.complete(reply):
                    break
            score, reasoning, status = self.verdict(reply)  # Raises if every attempt was cut short.
            return self.feedback(status, started, score=score, reasoning=reasoning, attempts=attempts)
        except Exception as exc:
            # One bad row must not abort a 12-row pass; keep the failure as evidence.
            return self.feedback("judge_error", started, attempts=attempts, error={
                "type": type(exc).__name__, "message": str(exc) or type(exc).__name__})


DEFAULT_JUDGE = IdiomaticJudge()


def idiomatic_playwright(inputs: dict, outputs: dict | None, reference_outputs: dict | None = None) -> list[dict]:
    """Module-level evaluator for evaluate(evaluators=[...]); judge model from env."""
    return DEFAULT_JUDGE(inputs, outputs, reference_outputs)

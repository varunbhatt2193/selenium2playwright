"""Step 10.4 — the flywheel: a thumbs-down becomes tomorrow's test case.

The evals in Phase 6 run against a dataset somebody sat down and wrote. That
dataset is a guess about which files are hard. Real visitors converting real
files are a much better guess, and the moment they tell you one came out wrong
is the single most valuable signal this project can collect — it is a labelled
failure, for free, on an input nobody thought to try.

So a 👎 does two things here, and the second is the one that matters:

    always     record the judgement in LangSmith, attached to the run
    on 👎      queue the run's input as an example in a dataset

The first makes the deployment measurable: LangSmith can chart feedback over
time, and Phase 11's online evaluators can sample against it. The second makes
it *improvable*. The queued dataset is not the eval set — it is the inbox for
one. Rows land tagged and unreviewed, and a human decides which are genuine
failures worth a golden output and which are somebody pasting Java.

Deliberately kept out of the graph. A conversion should not slow down or fail
because feedback plumbing had a bad day, so this is a separate call, made after
the run is over, and every path through it swallows its own errors.

Docs: https://docs.langchain.com/langsmith/evaluation
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

QUEUE_DATASET = os.environ.get("S2P_FEEDBACK_DATASET") or "s2p-feedback-queue"
FEEDBACK_KEY = "user_score"

# The tag that separates "a visitor complained about this" from the curated
# rows. Phase 11 filters on it rather than trusting the dataset name, because
# names get reused and tags do not.
QUEUE_TAG = "from-feedback"


@dataclass(frozen=True)
class Recorded:
    """What actually happened, so the caller can say so honestly.

    `queued` is separate from `stored` because the two can differ: a 👍 is
    stored and never queued, and a 👎 with LangSmith unreachable is neither. A
    single boolean would force the playground to imply something it does not
    know.
    """

    stored: bool
    queued: bool
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"stored": self.stored, "queued": self.queued, "detail": self.detail}


def _client():
    """A LangSmith client, or None when there is no key to use one with.

    Returning None rather than raising: a deployment without tracing configured
    should serve conversions perfectly well, just without a flywheel.
    """
    if not os.environ.get("LANGSMITH_API_KEY"):
        return None
    from langsmith import Client

    return Client()


def record(
    run_id: str,
    score: float,
    *,
    comment: str = "",
    source_text: str = "",
    source_path: str = "",
    visitor: str = "",
) -> Recorded:
    """File one judgement about one run, and queue it if it was a complaint.

    `score` is 1 for 👍 and 0 for 👎, matching LangSmith's convention for a
    binary feedback key, which is what makes it chartable without any further
    configuration.
    """
    client = _client()
    if client is None:
        return Recorded(False, False, "LangSmith is not configured on this deployment.")

    stored, why_not = False, ""
    try:
        client.create_feedback(
            run_id=run_id,
            key=FEEDBACK_KEY,
            score=score,
            comment=comment or None,
            source_info={"visitor": visitor} if visitor else None,
        )
        stored = True
    except Exception as exc:  # noqa: BLE001 — feedback must never break the caller
        why_not = f"could not attach to the run ({type(exc).__name__})"

    if score >= 1 or not source_text.strip():
        # Nothing to queue: either they were happy, or we were not given the
        # input to queue. The second happens when the playground sends feedback
        # for a run it did not itself submit.
        return Recorded(stored, False, "Recorded." if stored else f"Not recorded: {why_not}.")

    # Queued even when the line above failed, deliberately. Attaching the score
    # to a run is bookkeeping; the *input somebody said we got wrong* is the
    # artifact worth having, and it does not stop being worth having because
    # LangSmith could not find the run id. Dropping both because one failed was
    # the first version, and it threw away the valuable half to protect the
    # cheap half.
    queued, detail = _queue(client, source_text, source_path, comment, run_id, visitor)
    if stored:
        return Recorded(True, queued, detail)
    return Recorded(False, queued, f"{detail} Score {why_not}.")


def _queue(
    client, source_text: str, source_path: str, comment: str, run_id: str, visitor: str
) -> tuple[bool, str]:
    """Put the disliked input in the queue dataset, creating it on first use.

    No expected output. That is the point of a *queue*: an example with a golden
    answer nobody has written yet would be worse than no example, because the
    eval would start scoring against a blank. A human supplies the output when
    they triage the row, and only then does it graduate into the eval set.
    """
    try:
        dataset = _ensure_dataset(client)
        client.create_example(
            dataset_id=dataset.id,
            inputs={
                "source_text": source_text,
                "source_path": source_path or "pasted.ts",
            },
            metadata={
                "run_id": run_id,
                "comment": comment,
                "visitor": visitor,
                "status": "unreviewed",
                "tag": QUEUE_TAG,
            },
        )
        return True, "Recorded, and queued for review."
    except Exception as exc:  # noqa: BLE001
        return False, f"Recorded, but could not queue: {type(exc).__name__}"


def _ensure_dataset(client):
    from langsmith.utils import LangSmithNotFoundError

    try:
        return client.read_dataset(dataset_name=QUEUE_DATASET)
    except LangSmithNotFoundError:
        return client.create_dataset(
            dataset_name=QUEUE_DATASET,
            description=(
                "Conversions a visitor marked wrong on the public demo. Unreviewed "
                "inputs with no expected output: triage, add a golden, then promote "
                "into the eval set."
            ),
        )


def pending() -> list[dict[str, Any]]:
    """The queue, for triage. Empty when LangSmith is not configured."""
    client = _client()
    if client is None:
        return []
    try:
        dataset = _ensure_dataset(client)
        rows = []
        for example in client.list_examples(dataset_id=dataset.id):
            metadata = example.metadata or {}
            if metadata.get("status") != "unreviewed":
                continue
            rows.append(
                {
                    "example_id": str(example.id),
                    "source_path": (example.inputs or {}).get("source_path", ""),
                    "comment": metadata.get("comment", ""),
                    "run_id": metadata.get("run_id", ""),
                }
            )
        return rows
    except Exception:  # noqa: BLE001
        return []

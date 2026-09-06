"""Read back experiment evidence without rerunning or rewriting any conversion."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import datetime, timezone
from time import sleep

from selenium2playwright.eval_plan import FEEDBACK_KEYS


async def readback_once(client, report: dict, records: list[dict]) -> dict:
    """Compare project metadata, root runs, and required feedback with local evidence."""
    experiment_id = report["experiment"]["id"]
    project = client.read_project(project_id=experiment_id)
    issues = []
    if str(project.id) != experiment_id or str(project.reference_dataset_id) != report["plan"]["dataset_id"]:
        issues.append("Experiment points to a different dataset")
    for key, value in report["plan"]["metadata"].items():
        if (project.metadata or {}).get(key) != value:
            issues.append(f"Experiment metadata differs: {key}")
    # The current runs.query API paginates asynchronously. Its default window is
    # one day, so use the saved start time even for a much later readback retry.
    runs = [run async for run in client.runs.query(
        project_ids=[experiment_id], is_root=True, min_start_time=report["experiment"]["started_at_utc"],
        selects=["ID", "REFERENCE_EXAMPLE_ID", "INPUTS", "OUTPUTS", "ERROR", "END_TIME", "TOTAL_COST", "TOTAL_TOKENS"])]
    by_id = {str(run.id): run for run in runs}
    expected_ids = {record["run"]["id"] for record in records}
    if len(by_id) != len(runs) or set(by_id) != expected_ids:
        issues.append("Root run IDs are missing, duplicated, or unexpected")
    feedback = list(client.list_feedback(run_ids=list(expected_ids), feedback_key=FEEDBACK_KEYS))
    by_feedback = defaultdict(list)
    for item in feedback:
        if str(item.run_id) not in expected_ids:
            issues.append("Feedback targets an unexpected run")
        by_feedback[(str(item.run_id), item.key)].append(item)
    costs, cost_coverage = {}, {}
    for record in records:
        run_id, local = record["run"]["id"], record["run"]
        remote = by_id.get(run_id)
        if remote is None:
            continue
        # A present root can still have missing child uploads. Reconcile its
        # token aggregate with BOTH locally recorded roles before using cost.
        # Missing role usage remains unknown, even when the cloud returns zero.
        output = local["outputs"] or {}
        usage = [(output.get(role) or {}).get("total_tokens") for role in ("usage", "critic_usage")]
        expected_tokens = sum(usage) if all(isinstance(n, int) and n >= 0 for n in usage) else None
        matched = expected_tokens is not None and remote.total_tokens == expected_tokens
        costs[run_id] = str(remote.total_cost) if matched and remote.total_cost is not None else None
        cost_coverage[run_id] = {"local_tokens": expected_tokens, "cloud_tokens": remote.total_tokens,
                                "tokens_match": matched, "reported_cost_usd": str(remote.total_cost)
                                if remote.total_cost is not None else None}
        if (str(remote.reference_example_id) != record["example_id"] or remote.inputs != local["inputs"]
                or remote.outputs != local["outputs"] or remote.error != local["error"] or remote.end_time is None):
            issues.append(f"Run content/completion differs: {run_id}")
        for expected in record["feedback"]:
            matches = by_feedback[(run_id, expected["key"])]
            if len(matches) != 1:
                issues.append(f"Expected one {expected['key']} feedback for {run_id}; got {len(matches)}")
                continue
            actual = matches[0]
            if any(getattr(actual, field) != expected.get(field) for field in ("score", "value", "comment")):
                issues.append(f"Feedback values differ: {run_id}/{expected['key']}")
            # SDK _log_evaluation_feedback maps evaluator_info to source_info;
            # readback exposes that under feedback_source.metadata. Server-added
            # fields may coexist, but every field we submitted must match.
            metadata = actual.feedback_source.metadata if actual.feedback_source else {}
            for key, value in (expected.get("evaluator_info") or {}).items():
                if (metadata or {}).get(key) != value:
                    issues.append(f"Feedback evidence differs: {run_id}/{expected['key']}/{key}")
    return {"status": "verified" if not issues else "unverified", "issues": issues,
            "root_runs_read": len(runs), "feedback_read": len(feedback), "costs_usd": costs,
            "cost_coverage": cost_coverage,
            "project": project.model_dump(mode="json"),
            "runs": [run.model_dump(mode="json", include={"id", "reference_example_id", "inputs", "outputs",
                     "error", "end_time", "total_cost", "total_tokens"}) for run in runs],
            "feedback": [item.model_dump(mode="json") for item in feedback]}


def verify_cloud(client, report: dict, records: list[dict], *, attempts: int = 6, delay: float = 2) -> dict:
    """Allow bounded upload/indexing delay; incomplete evidence never becomes verified."""
    if not report["local_integrity"]["complete"]:
        return {"status": "not_checked", "issues": ["Local integrity must be complete before cloud verification"]}
    if not report["experiment"].get("id"):
        return {"status": "not_checked", "issues": ["No uploaded experiment ID is available"]}
    result = {"status": "unverified", "issues": ["No readback attempted"]}
    # Keep one event loop across retries because the SDK reuses HTTP connections.
    with asyncio.Runner() as runner:
        for attempt in range(1, attempts + 1):
            try:
                if attempt == 1:
                    client.flush(timeout=30)
                result = runner.run(readback_once(client, report, records))
            except Exception as exc:
                result = {"status": "unverified", "issues": [f"{type(exc).__name__}: {str(exc) or type(exc).__name__}"]}
            result["attempts"] = attempt
            result["checked_at_utc"] = datetime.now(timezone.utc).isoformat()
            if result["status"] == "verified":
                break
            if attempt < attempts:
                sleep(delay)
    return result

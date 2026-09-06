"""Recover journaled roots/feedback after an upload failure, never model calls.

Planning is pure and checks the whole snapshot before the first cloud write.
The saved journal is authoritative only after the normal local integrity check.
Missing child traces cannot be reconstructed from final code and stay missing.
"""

from collections import defaultdict
from datetime import datetime, timezone
from uuid import UUID, uuid5

from selenium2playwright.eval_report import assemble_report


def recovery_actions(report: dict, records: list[dict], cloud: dict) -> list[dict]:
    """Plan missing evidence; reject conflicting or duplicate remote evidence."""
    checked = assemble_report(report["plan"], records, report["experiment"], report["execution_error"])
    if not checked["local_integrity"]["complete"]:
        raise ValueError("Cannot recover an incomplete or inconsistent local journal")
    project = cloud["project"]
    metadata = (project.get("extra") or {}).get("metadata") or {}
    if (project["id"] != report["experiment"]["id"]
            or project["reference_dataset_id"] != report["plan"]["dataset_id"]
            or any(metadata.get(k) != v for k, v in report["plan"]["metadata"].items())):
        raise ValueError("Project identity, dataset, or configuration differs")
    expected_ids = {r["run"]["id"] for r in records}
    runs = {r["id"]: r for r in cloud["runs"]}
    if len(runs) != len(cloud["runs"]) or not set(runs) <= expected_ids:
        raise ValueError("Unexpected or duplicate remote roots")
    feedback = defaultdict(list)
    for item in cloud["feedback"]:
        if item["run_id"] not in expected_ids:
            raise ValueError("Feedback targets an unexpected run")
        feedback[item["run_id"], item["key"]].append(item)
    actions = []
    for record in records:
        local = record["run"]
        run_id = local["id"]
        # A completed root needs real timestamps and its original trace identity.
        # These are stored by the runner, not invented during recovery.
        if not local.get("end_time") or local.get("trace_id") != run_id:
            raise ValueError(f"Journal lacks completed root identity: {run_id}")
        if datetime.fromisoformat(local["end_time"]) < datetime.fromisoformat(local["start_time"]):
            raise ValueError(f"Journal timestamps are reversed: {run_id}")
        remote = runs.get(run_id)
        if remote is None:
            actions.append({"operation": "create_root", "run_id": run_id})
        else:
            if any(remote[k] != local[k] for k in ("inputs", "reference_example_id")):
                raise ValueError(f"Remote root identity/input conflict: {run_id}")
            if remote["end_time"] is None and not remote["outputs"] and remote["error"] is None:
                actions.append({"operation": "finish_root", "run_id": run_id})
            elif any(remote[k] != local[k] for k in ("outputs", "error")) or remote["end_time"] is None:
                raise ValueError(f"Remote root output/completion conflict: {run_id}")
        for item in record["feedback"]:
            matches = feedback[run_id, item["key"]]
            if not matches:
                actions.append({"operation": "create_feedback", "run_id": run_id, "key": item["key"],
                    "feedback_id": str(uuid5(UUID(run_id), "journal-recovery-v1:" + item["key"]))})
                continue
            if len(matches) != 1:
                raise ValueError(f"Duplicate feedback: {run_id}/{item['key']}")
            actual = matches[0]
            evidence = (actual.get("feedback_source") or {}).get("metadata") or {}
            if (any(actual.get(k) != item.get(k) for k in ("score", "value", "comment"))
                    or any(evidence.get(k) != v for k, v in (item.get("evaluator_info") or {}).items())):
                raise ValueError(f"Conflicting feedback: {run_id}/{item['key']}")
    return actions


def apply_action(client, action: dict, report: dict, records: list[dict], journal_sha256: str) -> None:
    """Replay one preflighted item synchronously; keep provenance separate from results."""
    record = next(r for r in records if r["run"]["id"] == action["run_id"])
    local = record["run"]
    provenance = {"journal_sha256": journal_sha256, "method": "journal-recovery-v1",
                  "recovered_at_utc": datetime.now(timezone.utc).isoformat()}
    if action["operation"] == "create_feedback":
        item = next(f for f in record["feedback"] if f["key"] == action["key"])
        # Stable IDs make a retry safe after an ambiguous network response. Keep
        # original evaluator evidence; omit the conflicting workspace definition.
        client.create_feedback(run_id=local["id"], trace_id=local["trace_id"], key=item["key"],
            score=item.get("score"), value=item.get("value"), comment=item.get("comment"),
            source_info=(item.get("evaluator_info") or {}) | {"upload_recovery": provenance},
            source_run_id=item.get("source_run_id"), feedback_source_type="model",
            feedback_id=action["feedback_id"], session_id=report["experiment"]["id"],
            start_time=datetime.fromisoformat(local["start_time"]))
    elif action["operation"] == "finish_root":
        client.update_run(local["id"], outputs=local["outputs"], error=local["error"],
                          end_time=datetime.fromisoformat(local["end_time"]))
    elif action["operation"] == "create_root":
        started = datetime.fromisoformat(local["start_time"])
        # Root dotted_order is the SDK's UTC timestamp + original root UUID.
        # Descendants already use this identity, so preserve it when restoring.
        order = started.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + local["id"]
        client.create_run(name="conversion_target", run_type="chain", id=local["id"],
            trace_id=local["trace_id"], dotted_order=order, inputs=local["inputs"],
            outputs=local["outputs"], error=local["error"], start_time=started,
            end_time=datetime.fromisoformat(local["end_time"]), reference_example_id=record["example_id"],
            project_name=report["experiment"]["name"], session_id=report["experiment"]["id"],
            extra={"metadata": report["plan"]["metadata"] | {"upload_recovery": provenance}})
    else:
        raise ValueError(f"Unknown recovery operation: {action['operation']}")

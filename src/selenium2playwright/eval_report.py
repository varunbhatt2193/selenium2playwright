"""Reconcile scheduled examples and keep missing evidence in every denominator."""

from __future__ import annotations

from collections import Counter, defaultdict
from decimal import Decimal
import json
from math import isfinite

from selenium2playwright.eval_evaluators import EVALUATOR_VERSION, GATE_KEYS
from selenium2playwright.eval_plan import FEEDBACK_KEYS
from selenium2playwright.schemas import ValidationReport


def coherent_feedback(numeric: dict, category: dict, gate: str) -> bool:
    """Require a usable score, category, and complete evidence before counting a pass."""
    status, evidence = category.get("value"), numeric.get("evaluator_info") or {}
    if (not isinstance(status, str) or status not in {"passed", "failed", "no_output", "invalid_input", "tool_error"}
            or numeric.get("score") not in (0, 1) or numeric["score"] != int(status == "passed")
            or evidence.get("status") != status or evidence.get("gate") != gate
            or evidence.get("version") != EVALUATOR_VERSION or "report" not in evidence or "error" not in evidence):
        return False
    elapsed = evidence.get("elapsed_seconds")
    if not isinstance(elapsed, (int, float)) or not isfinite(elapsed) or elapsed < 0:
        return False
    if status in {"passed", "failed"}:
        raw = evidence["report"]
        if not isinstance(raw, dict) or not {"gate", "passed", "findings", "tool_output"} <= raw.keys():
            return False
        try:
            report = ValidationReport.model_validate(raw)
        except ValueError:
            return False
        return report.gate == gate and report.passed == (status == "passed") and evidence["error"] is None
    error = evidence["error"]
    return isinstance(error, dict) and all(isinstance(error.get(k), str) and error[k] for k in ("type", "message"))


def measurement(values: list) -> dict:
    """Report a complete total only when every row supplied the measurement."""
    known = [Decimal(str(value)) for value in values if value is not None]
    subtotal = str(sum(known)) if known else None
    return {"total": subtotal if len(known) == len(values) else None,
            "known_subtotal": subtotal, "known_rows": len(known), "missing_rows": len(values) - len(known)}


def aggregate(rows: list[dict]) -> dict:
    """Count verified passes across all scheduled rows, with status breakdowns."""
    n = len(rows)
    metrics = {}
    for key in GATE_KEYS.values():
        passed = sum(row["metrics"][key]["score"] for row in rows)
        metrics[key] = {"passed": passed, "scheduled": n, "pass_percent": round(100 * passed / n, 2) if n else None,
                        "statuses": dict(Counter(row["metrics"][key]["status"] for row in rows))}
    return {"scheduled": n, "metrics": metrics,
            "all_static_passed": sum(all(m["score"] == 1 for m in row["metrics"].values()) for row in rows),
            "graph_report_passed": sum((row["outputs"].get("report") or {}).get("status") == "passed" for row in rows),
            "target_seconds": measurement([row["outputs"].get("elapsed_seconds") for row in rows]),
            "actor_total_tokens": measurement([(row["outputs"].get("usage") or {}).get("total_tokens") for row in rows]),
            "critic_total_tokens": measurement([(row["outputs"].get("critic_usage") or {}).get("total_tokens") for row in rows]),
            "langsmith_root_cost_usd": measurement([row["cost_usd"] for row in rows])}


def assemble_report(plan: dict, records: list[dict], experiment: dict,
                    execution_error: dict | None = None) -> dict:
    """Produce one report row per scheduled example, even if execution stopped early."""
    issues, grouped, rows = [], defaultdict(list), []
    for record in records:
        identity = record["example_id"]
        if identity not in plan["examples"]:
            issues.append(f"Unexpected result example: {identity}")
        grouped[identity].append(record)
    run_ids = [record["run"]["id"] for record in records]
    if len(set(run_ids)) != len(run_ids):
        issues.append("Duplicate target run IDs")
    for identity, example in plan["examples"].items():
        case_id = example["metadata"]["case_id"]
        matches = grouped[identity]
        record = matches[0] if len(matches) == 1 else None
        run = record["run"] if record else {}
        outputs = run.get("outputs") or {}
        feedback = record["feedback"] if record else []
        by_key = defaultdict(list)
        for item in feedback:
            by_key[item["key"]].append(item)
        usable = record is not None
        if not usable:
            issues.append(f"{case_id}: expected one result, received {len(matches)}")
        elif run["reference_example_id"] != identity or run["inputs"] != example["inputs"]:
            issues.append(f"{case_id}: result input/example identity mismatch")
            usable = False
        if record and (set(by_key) != set(FEEDBACK_KEYS) or any(len(v) != 1 for v in by_key.values())):
            issues.append(f"{case_id}: expected each of the eight feedback keys exactly once")
        metrics = {}
        for gate, key in GATE_KEYS.items():
            score = 0
            status = "missing_result" if not matches else "invalid_feedback"
            if usable and len(by_key[key]) == len(by_key[key + "_status"]) == 1:
                numeric, category = by_key[key][0], by_key[key + "_status"][0]
                # An SDK evaluator exception can yield a key with no usable score.
                # Require coherent score/status/evidence before counting a pass.
                if coherent_feedback(numeric, category, gate):
                    score, status = int(numeric["score"]), category["value"]
                else:
                    issues.append(f"{case_id}: invalid {key} score/status/evidence")
            elif usable:
                status = "missing_feedback" if not by_key[key] or not by_key[key + "_status"] else "invalid_feedback"
            metrics[key] = {"score": score, "status": status}
        rows.append({"example_id": identity, "case_id": case_id, "scenario": example["metadata"]["scenario"],
                     "kind": example["metadata"]["kind"], "run_id": run.get("id"), "outputs": outputs,
                     "run_error": run.get("error"), "feedback": feedback, "metrics": metrics,
                     "cost_usd": record.get("remote_cost_usd") if record else None})
    if execution_error:
        issues.append(f"Experiment execution error: {execution_error['type']}: {execution_error['message']}")
    return {"schema_version": 1, "plan": plan, "experiment": experiment, "execution_error": execution_error,
            "local_integrity": {"complete": not issues, "issues": issues, "received_results": len(records),
                                "scheduled_examples": len(rows), "expected_feedback": len(rows) * len(FEEDBACK_KEYS)},
            "aggregate": aggregate(rows), "by_scenario": {
                name: aggregate([row for row in rows if row["scenario"] == name]) for name in sorted({r["scenario"] for r in rows})},
            "by_kind": {name: aggregate([row for row in rows if row["kind"] == name]) for name in sorted({r["kind"] for r in rows})},
            "rows": rows, "cloud_verification": {"status": "not_checked"}}


def render_markdown(report: dict) -> str:
    """Render a readable scorecard; full code, tool output, and feedback stay in JSON."""
    plan, totals = report["plan"], report["aggregate"]
    config = plan["metadata"]["configuration"]
    lines = ["# Phase 6.2 experiment report", "",
             f"Experiment: {report['experiment'].get('url') or 'not uploaded'}", "",
             f"Execution mode: **{report['experiment'].get('mode', 'test_fixture')}**. "
             "Offline SDK checks do not measure model conversion quality.", "",
             f"Dataset: `{plan['dataset_id']}` at `{plan['dataset_version']}`.",
             f"Model requested: `{config['model']}`; {config['max_attempts']} total attempts; one repetition.",
             f"Configuration SHA-256: `{plan['metadata']['configuration_sha256']}`.",
             f"Git: `{config['git_revision']}`; dirty: `{config['git_dirty']}`.", "",
             f"Local integrity complete: **{report['local_integrity']['complete']}**. "
             f"Cloud verification: **{report['cloud_verification']['status']}**.", "",
             "Static checking does not establish browser correctness. Test rows use supplied golden POMs.",
             "Full captured inputs/configuration are in plan.json; complete outputs and feedback are in report.json.", "",
             "## Scores across all scheduled examples", "",
             "| Metric | Verified passes / scheduled | Percent | Status counts |",
             "| --- | --- | --- | --- |"]
    for key, metric in totals["metrics"].items():
        lines.append(f"| {key} | {metric['passed']} / {metric['scheduled']} | {metric['pass_percent']}% | {metric['statuses']} |")
    lines += ["", f"All four static gates: {totals['all_static_passed']} / {totals['scheduled']}.",
              f"Graph reports passed: {totals['graph_report_passed']} / {totals['scheduled']}.", "",
              "## Scenario and kind breakdowns", "",
              "| Group | Scheduled | Compile | Residue | Lint | Parity | All static | Graph passed |",
              "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    for dimension in ("by_scenario", "by_kind"):
        for name, group in report[dimension].items():
            scores = " | ".join(str(group["metrics"][key]["passed"]) for key in GATE_KEYS.values())
            lines.append(f"| {name} | {group['scheduled']} | {scores} | {group['all_static_passed']} | {group['graph_report_passed']} |")
    lines += ["", "## Each scheduled conversion", "",
              "| Case | Graph report | Attempts | Compile | Residue | Lint | Parity | TODOs |",
              "| --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for row in report["rows"]:
        final = row["outputs"].get("report") or {}
        scores = " | ".join(row["metrics"][key]["status"] for key in GATE_KEYS.values())
        todos = len((final.get("result") or {}).get("todos", [])) if final.get("result") is not None else "unknown"
        lines.append(f"| {row['case_id']} | {final.get('status', row['outputs'].get('conversion_status', 'missing'))} | "
                     f"{final.get('attempts', 'unknown')} | {scores} | {todos} |")
    lines += ["", "## Time, tokens, and available cost", "",
              "Target time includes internal graph checks; evaluator time measures the additional checks.",
              "Missing totals remain unavailable; known subtotals do not include unreported usage/cost.", "",
              "```json", json.dumps({k: totals[k] for k in (
                  "target_seconds", "actor_total_tokens", "critic_total_tokens", "langsmith_root_cost_usd")}, indent=2), "```"]
    for row in report["rows"]:
        output = row["outputs"]
        final = output.get("report") or {}
        details = {"run_id": row["run_id"], "conversion_status": output.get("conversion_status"),
                   "reason": final.get("reason"), "notes": (final.get("result") or {}).get("notes"),
                   "todos": (final.get("result") or {}).get("todos"), "critique": final.get("critique"),
                   "errors": final.get("errors"), "refusal": output.get("refusal"),
                   "adapter_error": output.get("adapter_error"), "run_error": row["run_error"],
                   "target_seconds": output.get("elapsed_seconds"), "actor_usage": output.get("usage"),
                   "critic_usage": output.get("critic_usage"), "cost_usd": row["cost_usd"],
                   "evaluators": {f["key"]: {"status": (f.get("evaluator_info") or {}).get("status"),
                       "elapsed_seconds": (f.get("evaluator_info") or {}).get("elapsed_seconds"),
                       "comment": f.get("comment")} for f in row["feedback"] if f["key"] in GATE_KEYS.values()}}
        lines += ["", f"## {row['case_id']} evidence", "", "```json", json.dumps(details, indent=2), "```"]
    lines += ["", "## Integrity and cloud readback", "", "```json",
              json.dumps({"local": report["local_integrity"], "cloud": report["cloud_verification"]}, indent=2), "```", ""]
    return "\n".join(lines)

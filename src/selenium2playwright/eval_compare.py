"""Step 6.3 — put two experiment reports side by side and work out the delta.

One arm ran every example with a single conversion attempt (no repairs). The
other arm ran the same examples with up to three attempts (draft + two repairs).
Everything else — dataset version, model, prompts, evaluators, code revision —
must be identical, otherwise the difference cannot be blamed on reflection.
See docs/reflection-ab.md for the plain-English walkthrough.
"""

from __future__ import annotations

from collections import Counter
from decimal import Decimal

from selenium2playwright.eval_evaluators import GATE_KEYS

# Every configuration key except the one under test must match between arms.
VARIABLE = {"max_attempts"}
PASS_KEYS = [*GATE_KEYS.values(), "all_static_passed", "graph_report_passed"]
MEASURES = ("target_seconds", "actor_total_tokens", "critic_total_tokens", "langsmith_root_cost_usd")


def _passes(totals: dict) -> dict:
    """Flatten the four gate counts plus the two combined counts into one dict."""
    return {**{key: totals["metrics"][key]["passed"] for key in GATE_KEYS.values()},
            "all_static_passed": totals["all_static_passed"], "graph_report_passed": totals["graph_report_passed"]}


def arm_summary(report: dict) -> dict:
    """Reduce one full experiment report to the numbers the comparison needs."""
    config = report["plan"]["metadata"]["configuration"]
    totals = report["aggregate"]
    n = totals["scheduled"]
    rows = {}
    for row in report["rows"]:
        final = row["outputs"].get("report") or {}
        rows[row["case_id"]] = {
            "graph_status": final.get("status", row["outputs"].get("conversion_status", "missing")),
            # No draft = the actor's reply never parsed into code, so validation,
            # the critic, and any repair lap were all impossible for this row.
            "draft": bool(final.get("result")), "errors": [str(e)[:160] for e in final.get("errors") or []],
            "attempts": final.get("attempts"), "all_static": all(m["score"] == 1 for m in row["metrics"].values()),
            "todos": len((final.get("result") or {}).get("todos") or []) if final.get("result") else None,
            "target_seconds": row["outputs"].get("elapsed_seconds"),
            "actor_tokens": (row["outputs"].get("usage") or {}).get("total_tokens"),
            "critic_tokens": (row["outputs"].get("critic_usage") or {}).get("total_tokens"),
            "cost_usd": row["cost_usd"]}
    attempts = [r["attempts"] for r in rows.values() if isinstance(r["attempts"], int)]
    passes = _passes(totals)
    drafted = [r for r in rows.values() if r["draft"]]
    with_draft = {"rows": len(drafted), "no_draft_rows": n - len(drafted),
                  "all_static_passed": sum(r["all_static"] for r in drafted),
                  "graph_report_passed": sum(r["graph_status"] == "passed" for r in drafted),
                  "repaired_rows": sum(isinstance(r["attempts"], int) and r["attempts"] > 1 for r in drafted)}
    return {
        "experiment": {k: report["experiment"].get(k) for k in ("name", "id", "url", "mode", "elapsed_seconds")},
        "max_attempts": config["max_attempts"], "model": config["model"],
        "configuration_sha256": report["plan"]["metadata"]["configuration_sha256"],
        "git_revision": config["git_revision"], "git_dirty": config["git_dirty"],
        "local_complete": report["local_integrity"]["complete"],
        "cloud_status": report["cloud_verification"]["status"], "scheduled": n,
        "passes": passes, "percent": {k: round(100 * v / n, 2) if n else None for k, v in passes.items()},
        "attempt_counts": dict(sorted(Counter(attempts).items())), "with_draft": with_draft,
        "actor_calls": {"total": sum(attempts) if len(attempts) == n else None, "known_subtotal": sum(attempts),
                        "known_rows": len(attempts), "missing_rows": n - len(attempts)},
        **{key: totals[key] for key in MEASURES}, "rows": rows,
    }


def _delta(a: dict, b: dict) -> dict:
    """b minus a for a measurement dict; None when either side has no full total."""
    if a["total"] is None or b["total"] is None:
        return {"total": None, "known_subtotal": str(Decimal(b["known_subtotal"] or 0) - Decimal(a["known_subtotal"] or 0)),
                "note": "one or both arms have missing rows; only the known subtotal is shown"}
    diff = Decimal(b["total"]) - Decimal(a["total"])
    ratio = (Decimal(b["total"]) / Decimal(a["total"])) if Decimal(a["total"]) else None
    return {"total": str(diff), "ratio": str(round(ratio, 3)) if ratio is not None else None}


def compare_reports(single: dict, reflective: dict) -> dict:
    """Compare a one-attempt report (A) with a reflective report (B). B minus A is the delta."""
    a, b = arm_summary(single), arm_summary(reflective)
    plans = (single["plan"], reflective["plan"])
    issues = []
    for key in ("dataset_id", "dataset_version", "dataset_name"):
        if plans[0][key] != plans[1][key]:
            issues.append(f"{key} differs between arms")
    if plans[0]["metadata"]["collection_sha256"] != plans[1]["metadata"]["collection_sha256"]:
        issues.append("collection_sha256 differs between arms")
    configs = (plans[0]["metadata"]["configuration"], plans[1]["metadata"]["configuration"])
    for key in sorted(set(configs[0]) | set(configs[1])):
        if key not in VARIABLE and configs[0].get(key) != configs[1].get(key):
            issues.append(f"configuration.{key} differs between arms")
    if not (a["max_attempts"] == 1 < b["max_attempts"]):
        issues.append(f"expected arm A = 1 attempt and arm B > 1, got {a['max_attempts']} and {b['max_attempts']}")
    if set(a["rows"]) != set(b["rows"]):
        issues.append("the two arms did not score the same case IDs")
    for arm, name in ((a, "A"), (b, "B")):
        if not arm["local_complete"]:
            issues.append(f"arm {name} local evidence is incomplete")
        if arm["cloud_status"] != "verified":
            issues.append(f"arm {name} cloud readback is {arm['cloud_status']}")
    per_case = []
    for case_id in sorted(set(a["rows"]) & set(b["rows"])):
        ra, rb = a["rows"][case_id], b["rows"][case_id]
        repairs = (rb["attempts"] - 1) if isinstance(rb["attempts"], int) else None
        if ra["all_static"] == rb["all_static"] and ra["graph_status"] == rb["graph_status"]:
            change = "same"
        elif ra["draft"] != rb["draft"]:
            # One arm never got code out of the model. That is a first-attempt
            # parse failure, not evidence about the repair loop either way.
            change = "no draft in " + ("A" if not ra["draft"] else "B")
        elif (not ra["all_static"] and rb["all_static"]) or (ra["graph_status"] != "passed" and rb["graph_status"] == "passed"):
            # Only a row that actually used a repair lap can credit reflection.
            # A better result with zero repairs is run-to-run model variance.
            change = "improved" if repairs else "improved without repair (variance)"
        else:
            change = "regressed" if repairs else "regressed without repair (variance)"
        per_case.append({"case_id": case_id, "one_attempt": ra, "reflective": rb, "change": change, "repairs_used": repairs})
    n = a["scheduled"]
    delta = {"passes": {k: b["passes"][k] - a["passes"][k] for k in PASS_KEYS},
             "percent_points": {k: round(100 * (b["passes"][k] - a["passes"][k]) / n, 2) if n else None for k in PASS_KEYS},
             **{key: _delta(a[key], b[key]) for key in MEASURES},
             "actor_calls": {"total": (b["actor_calls"]["total"] - a["actor_calls"]["total"])
                             if None not in (a["actor_calls"]["total"], b["actor_calls"]["total"]) else None}}
    changes = Counter(row["change"] for row in per_case)
    return {"schema_version": 1, "comparable": not issues, "issues": issues,
            "held_fixed": {"dataset_id": plans[0]["dataset_id"], "dataset_version": plans[0]["dataset_version"],
                           "collection_sha256": plans[0]["metadata"]["collection_sha256"], "model": a["model"],
                           "git_revision": a["git_revision"], "evaluator_version": configs[0].get("evaluator_version")},
            "arms": {"one_attempt": a, "reflective": b}, "delta": delta, "per_case": per_case,
            "case_changes": dict(changes),
            "headline": (f"all-static pass {a['passes']['all_static_passed']}/{n} -> {b['passes']['all_static_passed']}/{n} "
                         f"({a['percent']['all_static_passed']}% -> {b['percent']['all_static_passed']}%); "
                         f"graph passed {a['passes']['graph_report_passed']}/{n} -> {b['passes']['graph_report_passed']}/{n}")}


def _fmt(value) -> str:
    return "unavailable" if value is None else str(value)


def render_comparison_markdown(comparison: dict) -> str:
    """A short human scorecard; comparison.json keeps every number."""
    a, b, d = comparison["arms"]["one_attempt"], comparison["arms"]["reflective"], comparison["delta"]
    lines = ["# Phase 6.3 — one attempt vs. reflection", "",
             f"Comparable: **{comparison['comparable']}**" + (f" — issues: {comparison['issues']}" if comparison["issues"] else ""),
             "", f"**{comparison['headline']}**", "",
             "| Held fixed | Value |", "| --- | --- |",
             *[f"| {k} | `{v}` |" for k, v in comparison["held_fixed"].items()], "",
             "| Arm | Experiment | Attempts allowed | Config SHA-256 | Local complete | Cloud |", "| --- | --- | --- | --- | --- | --- |",
             *[f"| {label} | [{arm['experiment']['name']}]({arm['experiment']['url']}) | {arm['max_attempts']} | "
               f"`{arm['configuration_sha256'][:12]}…` | {arm['local_complete']} | {arm['cloud_status']} |"
               for label, arm in (("A: one attempt", a), ("B: reflection", b))], "",
             "## Quality", "", "| Metric | A: one attempt | B: reflection | Delta (B − A) |", "| --- | --- | --- | --- |",
             *[f"| {k} | {a['passes'][k]}/{a['scheduled']} ({a['percent'][k]}%) | {b['passes'][k]}/{b['scheduled']} "
               f"({b['percent'][k]}%) | {d['passes'][k]:+d} ({d['percent_points'][k]:+} pts) |" for k in PASS_KEYS], "",
             "## Cost of reflection", "", "| Measure | A: one attempt | B: reflection | Delta (B − A) |", "| --- | --- | --- | --- |"]
    for key in MEASURES:
        lines.append(f"| {key} | {_fmt(a[key]['total'])} (known {a[key]['known_subtotal']}, {a[key]['missing_rows']} missing) | "
                     f"{_fmt(b[key]['total'])} (known {b[key]['known_subtotal']}, {b[key]['missing_rows']} missing) | "
                     f"{_fmt(d[key].get('total'))}" + (f" (×{d[key]['ratio']})" if d[key].get("ratio") else "") + " |")
    lines += [f"| actor model calls | {_fmt(a['actor_calls']['total'])} | {_fmt(b['actor_calls']['total'])} | {_fmt(d['actor_calls']['total'])} |",
              f"| attempts used per row | {a['attempt_counts']} | {b['attempt_counts']} | |", "",
              "## Only the rows where the model produced a draft", "",
              "A row with no draft failed before validation, so no repair lap was possible. "
              "These counts show what reflection did on the rows it could act on.", "",
              "| Measure | A: one attempt | B: reflection |", "| --- | --- | --- |",
              *[f"| {k} | {a['with_draft'][k]} | {b['with_draft'][k]} |" for k in a["with_draft"]], "",
              "## Each example", "", "| Case | A status (attempts) | A all-static | B status (attempts) | B all-static | Repairs used | Change |",
              "| --- | --- | --- | --- | --- | --- | --- |"]
    for row in comparison["per_case"]:
        ra, rb = row["one_attempt"], row["reflective"]
        lines.append(f"| {row['case_id']} | {ra['graph_status']} ({ra['attempts']}) | {ra['all_static']} | "
                     f"{rb['graph_status']} ({rb['attempts']}) | {rb['all_static']} | {_fmt(row['repairs_used'])} | {row['change']} |")
    lines += ["", f"Changes: {comparison['case_changes']}.", "",
              "Static gates do not establish browser correctness. Two arms of one model run each; "
              "model output varies between runs, so small deltas can be noise.", ""]
    return "\n".join(lines)

"""Phase 6.4 — add judge scores to experiments that already exist in LangSmith.

evaluate(<experiment name>, evaluators=[judge]) re-reads each saved run's
outputs and the dataset's reference outputs, calls the evaluator, and attaches
the new feedback to the old runs. The converter is not run again: judging the
six saved 6.3/6.5 arms costs judge calls only.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from statistics import mean

from langsmith import evaluate

from selenium2playwright.eval_judge import FEEDBACK_KEY, JUDGE_VERSION, RUBRIC_SHA256, SCORED_STATUSES


def judge_experiment(client, experiment: str, evaluator, journal: Path, metadata: dict | None = None) -> list[dict]:
    """Score one existing experiment row by row; append each row to the journal as it lands."""
    results = evaluate(experiment, evaluators=[evaluator], client=client, max_concurrency=1,
                       blocking=False, metadata=dict(metadata or {}))
    records = []
    with journal.open("a", encoding="utf-8") as out:
        for item in results:
            feedback = {f.key: f for f in item["evaluation_results"]["results"]}
            score, status = feedback.get(FEEDBACK_KEY), feedback.get(FEEDBACK_KEY + "_status")
            record = {"experiment": experiment, "example_id": str(item["example"].id),
                      "case_id": item["example"].metadata.get("case_id"), "run_id": str(item["run"].id),
                      "score": score.score if score is not None else None,
                      "status": status.value if status is not None else "missing",
                      "reasoning": score.comment if score is not None else ""}
            records.append(record)
            out.write(json.dumps(record, ensure_ascii=False) + "\n")
            out.flush()
            print(f"  judged {len(records)}: {record['case_id']} -> {record['score']} ({record['status']})", flush=True)
    return records


def arm_summary(arm: dict, records: list[dict]) -> dict:
    """One line per arm: static and graph passes from the receipt, judge scores from this pass."""
    scored = [r for r in records if r["status"] in SCORED_STATUSES]
    scores = [r["score"] for r in scored]
    per_case = {r["case_id"]: r for r in records}
    return {"experiment": arm["experiment"]["name"], "model": arm["model"], "critic_model": arm.get("critic_model"),
            "max_attempts": arm["max_attempts"], "phase": arm.get("phase"),
            "all_static": arm["passes"]["all_static_passed"], "graph_passed": arm["passes"].get("graph_report_passed"),
            "scheduled": arm["scheduled"], "judged": len(records), "scored": len(scored),
            "unscored": {c: r["status"] for c, r in per_case.items() if r["status"] not in SCORED_STATUSES},
            "recovered_from_reasoning": sorted(r["case_id"] for r in scored if r["status"] == "scored_from_reasoning"),
            "mean": round(mean(scores), 2) if scores else None,
            "distribution": dict(sorted(Counter(scores).items())),
            "scores_by_case": {c: r["score"] for c, r in sorted(per_case.items())}}


def disagreements(comparison: dict, arm_key: str, records: list[dict]) -> dict:
    """Where the judge and the exact tools point different ways; both lists are worth reading."""
    static_ok = {c["case_id"]: c[arm_key]["all_static"] for c in comparison["per_case"]}
    scored = {r["case_id"]: r["score"] for r in records if r["status"] in SCORED_STATUSES}
    return {"static_pass_but_judge_le3": sorted(c for c, s in scored.items() if static_ok.get(c) and s <= 3),
            "static_fail_but_judge_ge4": sorted(c for c, s in scored.items() if static_ok.get(c) is False and s >= 4)}


def render_table(summaries: list[dict]) -> str:
    """Markdown table: one row per arm, the judge next to the exact gates."""
    lines = [f"Judge `{JUDGE_VERSION}`, rubric `{RUBRIC_SHA256[:12]}…`. Scores 1–5; mean over scored rows.", "",
             "| actor | attempts | all-static | graph passed | judge mean | 5 | 4 | ≤3 | unscored |",
             "|---|---|---|---|---|---|---|---|---|"]
    for s in summaries:
        d = s["distribution"]
        low = sum(v for k, v in d.items() if k <= 3)
        lines.append(f"| {s['model'].split(':', 1)[-1]} | {s['max_attempts']} | {s['all_static']}/{s['scheduled']} | "
                     f"{s['graph_passed']}/{s['scheduled']} | {s['mean']} | {d.get(5, 0)} | {d.get(4, 0)} | {low} | "
                     f"{len(s['unscored'])} |")
    return "\n".join(lines) + "\n"


def cross_judge(pass_a: dict, pass_b: dict) -> dict:
    """How far two judges agree on the same rows. Judge is opinion; this measures how shared."""
    by_experiment = {a["experiment"]: a for a in pass_a["arms"]}
    arms, all_pairs = [], []
    for b in pass_b["arms"]:
        a = by_experiment.get(b["experiment"])
        if a is None:
            continue
        pairs = {c: (a["scores_by_case"][c], b["scores_by_case"].get(c)) for c in a["scores_by_case"]}
        pairs = {c: p for c, p in pairs.items() if p[0] is not None and p[1] is not None}
        diffs = [y - x for x, y in pairs.values()]
        all_pairs.extend(pairs.values())
        arms.append({"experiment": b["experiment"], "model": b["model"], "max_attempts": b["max_attempts"],
                     "pairs": len(pairs), "exact": sum(x == y for x, y in pairs.values()),
                     "within_one": sum(abs(x - y) <= 1 for x, y in pairs.values()),
                     "mean_a": a["mean"], "mean_b": b["mean"],
                     "b_minus_a": round(mean(diffs), 2) if diffs else None,
                     "disagreements": {c: list(p) for c, p in sorted(pairs.items()) if p[0] != p[1]}})
    return {"judge_a": pass_a["judge_model"], "judge_b": pass_b["judge_model"], "arms": arms,
            "totals": {"pairs": len(all_pairs), "exact": sum(x == y for x, y in all_pairs),
                       "within_one": sum(abs(x - y) <= 1 for x, y in all_pairs),
                       "b_higher": sum(y > x for x, y in all_pairs), "a_higher": sum(x > y for x, y in all_pairs)}}


def render_cross_table(cross: dict) -> str:
    a, b = cross["judge_a"].split(":", 1)[-1], cross["judge_b"].split(":", 1)[-1]
    t = cross["totals"]
    lines = [f"Two judges, same rows. A = `{a}`, B = `{b}`. "
             f"{t['pairs']} rows both scored: {t['exact']} exact, {t['within_one']} within one point; "
             f"B higher on {t['b_higher']}, A higher on {t['a_higher']}.", "",
             "| actor | attempts | rows | exact | within 1 | mean A | mean B | B − A |", "|---|---|---|---|---|---|---|---|"]
    for r in cross["arms"]:
        lines.append(f"| {r['model'].split(':', 1)[-1]} | {r['max_attempts']} | {r['pairs']} | {r['exact']} | "
                     f"{r['within_one']} | {r['mean_a']} | {r['mean_b']} | {r['b_minus_a']:+} |")
    return "\n".join(lines) + "\n"

"""Step 11.1b — compare two runs that differ only in the playbook.

`eval_compare` answers "what did the repair loop buy?", where the variable is
`max_attempts`. This answers a different question with the same rigour: "did
editing the rulebook help, and what did it cost?" The variable here is the
playbook file itself, so the integrity check is the mirror image — every
configuration key must match EXCEPT the file hashes, and among the file hashes
only `docs/playbook.md` may differ. A run whose source code also changed is not
a prompt experiment, and this module refuses to call it one.

Working-agreement rule 6: no prompt change without a green eval run. This is
the thing that makes that rule checkable rather than aspirational.
"""

from __future__ import annotations

from collections import Counter

from selenium2playwright.eval_compare import MEASURES, PASS_KEYS, _delta, arm_summary

# A playbook edit legitimately moves these and nothing else. git_revision and
# git_dirty follow from committing the edit; file_sha256 is checked file by file.
VARIABLE = {"file_sha256", "git_revision", "git_dirty"}
ALLOWED_FILE_CHANGES = {"docs/playbook.md"}


def changed_files(before: dict, after: dict) -> list[str]:
    """Which hashed files differ between two configurations, added/removed included."""
    a, b = before["file_sha256"], after["file_sha256"]
    return sorted({path for path in set(a) | set(b) if a.get(path) != b.get(path)})


def hard_case_delta(before: dict, after: dict) -> dict:
    """Per-pattern before/after, for the benchmark whose rows carry hard cases.

    These groups overlap — one row exercises several patterns — so the counts
    do not sum to the experiment total, and a single row moving shows up in
    every group it belongs to. That is the intended reading.
    """
    numbers = sorted(set(before.get("by_hard_case", {})) | set(after.get("by_hard_case", {})),
                     key=int)
    delta = {}
    for number in numbers:
        a = before.get("by_hard_case", {}).get(number)
        b = after.get("by_hard_case", {}).get(number)
        if a is None or b is None or a["scheduled"] != b["scheduled"]:
            delta[number] = {"comparable": False}
            continue
        delta[number] = {
            "comparable": True, "rows": a["scheduled"],
            "before": a["all_static_passed"], "after": b["all_static_passed"],
            "graph_before": a["graph_report_passed"], "graph_after": b["graph_report_passed"],
        }
    return delta


def compare_prompt_arms(before: dict, after: dict, *, reserved: tuple[int, ...] = ()) -> dict:
    """Compare a baseline report (A) with a post-edit report (B). B minus A is the delta.

    `reserved` names hard cases deliberately left out of the tuning loop. They
    are reported separately so a reader can see whether the edit generalized or
    the benchmark simply became what the prompt was fitted to.
    """
    a, b = arm_summary(before), arm_summary(after)
    plans = (before["plan"], after["plan"])
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
    edited = changed_files(configs[0], configs[1])
    if not edited:
        issues.append("no file changed between arms; there is no prompt edit to measure")
    if unexpected := sorted(set(edited) - ALLOWED_FILE_CHANGES):
        issues.append(f"files other than the playbook changed: {unexpected}")
    if set(a["rows"]) != set(b["rows"]):
        issues.append("the two arms did not score the same case IDs")
    for arm, name in ((a, "A"), (b, "B")):
        if not arm["local_complete"]:
            issues.append(f"arm {name} local evidence is incomplete")
        if arm["cloud_status"] != "verified":
            issues.append(f"arm {name} cloud readback is {arm['cloud_status']}")
        if arm["provider_error_rows"]:
            issues.append(f"arm {name} has provider errors (infrastructure, not model quality) on "
                          f"{', '.join(arm['provider_error_rows'])}; rerun that arm")

    per_case = []
    for case_id in sorted(set(a["rows"]) & set(b["rows"])):
        ra, rb = a["rows"][case_id], b["rows"][case_id]
        if ra["all_static"] == rb["all_static"] and ra["graph_status"] == rb["graph_status"]:
            change = "same"
        elif ra["draft"] != rb["draft"]:
            change = "no draft in " + ("A" if not ra["draft"] else "B")
        elif (not ra["all_static"] and rb["all_static"]) or (
                ra["graph_status"] != "passed" and rb["graph_status"] == "passed"):
            change = "improved"
        else:
            change = "regressed"
        per_case.append({"case_id": case_id, "before": ra, "after": rb, "change": change})

    n = a["scheduled"]
    delta = {"passes": {k: b["passes"][k] - a["passes"][k] for k in PASS_KEYS},
             "percent_points": {k: round(100 * (b["passes"][k] - a["passes"][k]) / n, 2) if n else None
                                for k in PASS_KEYS},
             **{key: _delta(a[key], b[key]) for key in MEASURES}}
    hard = hard_case_delta(before, after)
    return {
        "schema_version": 1, "comparable": not issues, "issues": issues,
        "edited_files": edited,
        "held_fixed": {"dataset_id": plans[0]["dataset_id"], "dataset_version": plans[0]["dataset_version"],
                       "collection_sha256": plans[0]["metadata"]["collection_sha256"],
                       "model": a["model"], "critic_model": a["critic_model"],
                       "max_attempts": a["max_attempts"],
                       "evaluator_version": configs[0].get("evaluator_version")},
        "arms": {"before": a, "after": b}, "delta": delta, "per_case": per_case,
        "case_changes": dict(Counter(row["change"] for row in per_case)),
        "by_hard_case": hard,
        "tuned_for": sorted(int(k) for k in hard if int(k) not in reserved),
        "reserved": {str(number): hard.get(str(number), {"comparable": False}) for number in sorted(reserved)},
        "headline": (f"all-static pass {a['passes']['all_static_passed']}/{n} -> "
                     f"{b['passes']['all_static_passed']}/{n}; graph passed "
                     f"{a['passes']['graph_report_passed']}/{n} -> {b['passes']['graph_report_passed']}/{n}"),
    }


def render_prompt_ab_markdown(comparison: dict) -> str:
    """A short human scorecard; the JSON keeps every number."""
    from selenium2playwright.eval_hardcases import HARD_CASES
    a, b, d = comparison["arms"]["before"], comparison["arms"]["after"], comparison["delta"]
    fmt = lambda v: "unavailable" if v is None else str(v)
    lines = [f"# Phase {a['phase']} — playbook A/B", "",
             f"Comparable: **{comparison['comparable']}**"
             + (f" — issues: {comparison['issues']}" if comparison["issues"] else ""), "",
             f"**{comparison['headline']}**", "",
             f"Edited between arms: {', '.join(f'`{p}`' for p in comparison['edited_files'])}.", "",
             "| Held fixed | Value |", "| --- | --- |",
             *[f"| {k} | `{v}` |" for k, v in comparison["held_fixed"].items()], "",
             "| Arm | Experiment | Git | Local complete | Cloud |", "| --- | --- | --- | --- | --- |",
             *[f"| {label} | [{arm['experiment']['name']}]({arm['experiment']['url']}) | "
               f"`{arm['git_revision'][:9]}` | {arm['local_complete']} | {arm['cloud_status']} |"
               for label, arm in (("A: before", a), ("B: after", b))], "",
             "## Quality", "", "| Metric | A: before | B: after | Delta (B − A) |", "| --- | --- | --- | --- |",
             *[f"| {k} | {a['passes'][k]}/{a['scheduled']} ({a['percent'][k]}%) | "
               f"{b['passes'][k]}/{b['scheduled']} ({b['percent'][k]}%) | "
               f"{d['passes'][k]:+d} ({d['percent_points'][k]:+} pts) |" for k in PASS_KEYS], "",
             "## Cost", "", "| Measure | A: before | B: after | Delta (B − A) |", "| --- | --- | --- | --- |"]
    for key in MEASURES:
        lines.append(f"| {key} | {fmt(a[key]['total'])} | {fmt(b[key]['total'])} | "
                     f"{fmt(d[key].get('total'))}"
                     + (f" (×{d[key]['ratio']})" if d[key].get("ratio") else "") + " |")
    lines += ["", "## Per hard case", "",
              "Groups overlap: one row exercises several patterns, so these counts do not sum to "
              "the experiment total and one row moving shows up in every group it belongs to.", "",
              "| # | Pattern | Rows | All static A → B | Tuned for? |", "| --- | --- | --- | --- | --- |"]
    for number, group in comparison["by_hard_case"].items():
        title = HARD_CASES.get(int(number), "")
        tuned = "reserved" if number in comparison["reserved"] else "yes"
        cell = (f"{group['before']} → {group['after']}" if group["comparable"] else "not comparable")
        lines.append(f"| {number} | {title} | {group.get('rows', '?')} | {cell} | {tuned} |")
    lines += ["", "## Each example", "",
              "| Case | A status (attempts) | A all-static | B status (attempts) | B all-static | Change |",
              "| --- | --- | --- | --- | --- | --- |"]
    for row in comparison["per_case"]:
        ra, rb = row["before"], row["after"]
        lines.append(f"| {row['case_id']} | {ra['graph_status']} ({ra['attempts']}) | {ra['all_static']} | "
                     f"{rb['graph_status']} ({rb['attempts']}) | {rb['all_static']} | {row['change']} |")
    lines += ["", f"Changes: {comparison['case_changes']}.", "",
              "One run per arm. These models are not deterministic and temperature is not set, so a "
              "small delta can be run-to-run variance rather than the edit. Static gates do not "
              "establish browser correctness.", ""]
    return "\n".join(lines)

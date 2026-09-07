"""Phase 6.4 — calibrate the judge before trusting it.

Three checks, all offline against samples/ (no LangSmith dataset needed):
  1. goldens should score high;
  2. goldens broken on purpose should score lower than their golden;
  3. the same file judged twice should get the same score.
A judge that fails any of these is measuring something other than our rubric.
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean

from selenium2playwright.eval_collection import build_collection
from selenium2playwright.eval_judge import SCORED_STATUSES

ROLE_TAGS = {"button": "button", "link": "a", "heading": "h1", "textbox": "input", "checkbox": "input"}
# One nesting level of parentheses, enough for expect(page.getByRole("x", { name: "y" })).
_LOCATOR = r"\(((?:[^()]|\([^()]*\))+)\)"


def xpath_locators(code: str) -> str:
    """Rubric A: swap user-facing locators for id/XPath ones a Selenium habit would reach for."""
    code = re.sub(r'getByLabel\("([^"]+)"\)',
                  lambda m: f'locator("#{m.group(1).lower().replace(" ", "-")}")', code)
    code = re.sub(r'getByRole\("(\w+)",\s*\{\s*name:\s*"([^"]+)"[^}]*\}\)',
                  lambda m: f"locator(\"xpath=//{ROLE_TAGS.get(m.group(1), '*')}[contains(., '{m.group(2)}')]\")", code)
    code = re.sub(r'getByText\("([^"]+)"[^)]*\)',
                  lambda m: f"locator(\"xpath=//*[contains(text(), '{m.group(1)}')]\")", code)
    return code


def value_assertions(code: str) -> str:
    """Rubric B: turn retrying web-first assertions into extract-then-assert."""
    code = re.sub(r"await expect" + _LOCATOR + r"\.toContainText\(", r"expect(await \1.textContent()).toContain(", code)
    code = re.sub(r"await expect" + _LOCATOR + r"\.toHaveText\(", r"expect(await \1.textContent()).toBe(", code)
    code = re.sub(r"await expect" + _LOCATOR + r"\.toHaveValue\(", r"expect(await \1.inputValue()).toBe(", code)
    code = re.sub(r"await expect" + _LOCATOR + r"\.toBeVisible\(\)", r"expect(await \1.isVisible()).toBe(true)", code)
    return code


def sleeps(code: str) -> str:
    """Rubric B: a fixed sleep after every action, the driver.sleep() reflex."""
    return re.sub(r"^(\s*)(await [^\n]*\.(?:click|goto|fill|selectOption|setInputFiles|check)\([^\n]*\);)$",
                  r"\1\2\n\1await new Promise((resolve) => setTimeout(resolve, 2000));", code, flags=re.M)


def pom_assertions(code: str) -> str:
    """Rubric C: an assertion at the end of every page-object method; tests are left alone."""
    if "export class" not in code or "async " not in code:
        return code
    changed = re.sub(r"(?ms)^(  async \w+\([^)]*\)[^{]*\{\n)(.*?)(^  \}$)",
                     r"\1\2    await expect(this.page).toHaveURL(/.+/);\n\3", code)
    if changed == code:
        return code
    return re.sub(r'^import \{([^}]*)\} from "@playwright/test";', r'import { expect,\1} from "@playwright/test";',
                  changed, count=1, flags=re.M)


MUTATIONS = {"xpath_locators": xpath_locators, "value_assertions": value_assertions,
             "sleeps": sleeps, "pom_assertions": pom_assertions}


def build_variants(samples_root: Path, evidence_path: Path, only: set[str] | None = None) -> list[dict]:
    """Every golden plus every broken golden that actually differs from it."""
    rows = build_collection(samples_root, evidence_path)["examples"]
    variants = []
    for row in rows:
        case_id = row["metadata"]["case_id"]
        if only and case_id not in only:
            continue
        golden = row["outputs"]["code"]
        base = {"case_id": case_id, "kind": row["metadata"]["kind"], "inputs": row["inputs"],
                "reference_outputs": row["outputs"]}
        variants.append(base | {"variant": "golden", "candidate": golden})
        for name, mutate in MUTATIONS.items():
            broken = mutate(golden)
            # A mutation that finds nothing to break is not evidence; skip it.
            if broken != golden:
                variants.append(base | {"variant": name, "candidate": broken})
    return variants


def score_variants(variants: list[dict], judge, golden_repeats: int = 2, on_record=None) -> list[dict]:
    """Judge each variant; goldens are judged golden_repeats times for the agreement check."""
    records = []
    for variant in variants:
        repeats = golden_repeats if variant["variant"] == "golden" else 1
        for repeat in range(1, repeats + 1):
            score, status = judge(variant["inputs"], {"code": variant["candidate"]}, variant["reference_outputs"])
            record = {"case_id": variant["case_id"], "kind": variant["kind"], "variant": variant["variant"],
                      "repeat": repeat, "score": score["score"], "status": status["value"],
                      "reasoning": score["comment"], "evaluator_info": score["evaluator_info"]}
            records.append(record)
            if on_record is not None:
                on_record(record)
    return records


def summarize(records: list[dict], judge_model: str, rubric_sha256: str, judge_version: str) -> dict:
    """The three calibration questions as numbers; every skipped row is counted, not dropped."""
    scored = [r for r in records if r["status"] in SCORED_STATUSES]
    golden_first = {r["case_id"]: r["score"] for r in scored if r["variant"] == "golden" and r["repeat"] == 1}
    golden_scores = [r["score"] for r in scored if r["variant"] == "golden"]
    by_variant = {}
    for name in MUTATIONS:
        rows = [r for r in scored if r["variant"] == name]
        if not rows:
            continue
        lower = [r for r in rows if r["case_id"] in golden_first and r["score"] < golden_first[r["case_id"]]]
        equal_or_higher = [r["case_id"] for r in rows if r["case_id"] in golden_first
                           and r["score"] >= golden_first[r["case_id"]]]
        by_variant[name] = {"judged": len(rows), "mean": round(mean(r["score"] for r in rows), 2),
                            "lower_than_golden": len(lower), "not_lower": sorted(equal_or_higher),
                            "distribution": dict(sorted(Counter(r["score"] for r in rows).items()))}
    pairs = {}
    for r in scored:
        if r["variant"] == "golden":
            pairs.setdefault(r["case_id"], {})[r["repeat"]] = r["score"]
    both = [p for p in pairs.values() if 1 in p and 2 in p]
    agreement = {"pairs": len(both), "exact": sum(p[1] == p[2] for p in both),
                 "within_one": sum(abs(p[1] - p[2]) <= 1 for p in both),
                 "disagreements": {c: [p[1], p[2]] for c, p in pairs.items() if 1 in p and 2 in p and p[1] != p[2]}}
    return {"schema_version": 1, "judge_version": judge_version, "judge_model": judge_model,
            "rubric_sha256": rubric_sha256, "measured_at_utc": datetime.now(timezone.utc).isoformat(),
            "judge_calls": len(records), "scored": len(scored), "unscored": len(records) - len(scored),
            "recovered_from_reasoning": sum(r["status"] == "scored_from_reasoning" for r in scored),
            "unscored_statuses": dict(Counter(r["status"] for r in records if r["status"] not in SCORED_STATUSES)),
            "goldens": {"judged": len(golden_scores), "mean": round(mean(golden_scores), 2) if golden_scores else None,
                        "min": min(golden_scores, default=None),
                        "distribution": dict(sorted(Counter(golden_scores).items())),
                        "below_four": sorted({r["case_id"] for r in scored if r["variant"] == "golden" and r["score"] < 4})},
            "broken_goldens": by_variant, "repeat_agreement": agreement}


def render_markdown(summary: dict) -> str:
    g, a = summary["goldens"], summary["repeat_agreement"]
    lines = [f"# Judge calibration — {summary['judge_version']} / {summary['judge_model']}", "",
             f"Rubric `{summary['rubric_sha256'][:12]}…`; {summary['judge_calls']} judge calls, "
             f"{summary['scored']} scored ({summary['recovered_from_reasoning']} recovered from the closing sentence), "
             f"{summary['unscored']} unscored {summary['unscored_statuses'] or ''}.", "",
             "## 1. Goldens should score high", "",
             f"{g['judged']} judgements, mean {g['mean']}, min {g['min']}, distribution {g['distribution']}.",
             f"Goldens below 4: {', '.join(g['below_four']) or 'none'}.", "",
             "## 2. Broken goldens should score lower than their golden", "",
             "| mutation | judged | mean | lower than golden | not lower |", "|---|---|---|---|---|"]
    for name, v in summary["broken_goldens"].items():
        lines.append(f"| {name} | {v['judged']} | {v['mean']} | {v['lower_than_golden']}/{v['judged']} | "
                     f"{', '.join(v['not_lower']) or '—'} |")
    lines += ["", "## 3. Same file, same score", "",
              f"{a['pairs']} golden pairs: {a['exact']} exact matches, {a['within_one']} within one point.",
              f"Disagreements: {a['disagreements'] or 'none'}."]
    return "\n".join(lines) + "\n"

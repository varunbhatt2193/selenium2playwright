"""Phase 6.4 — score existing LangSmith experiments with the idiomatic judge.

    caffeinate -i -s uv run python scripts/judge_experiment.py --judge-model anthropic:claude-opus-5 \
        --comparison docs/phase-6.3-comparison.json docs/phase-6.5-haiku-comparison.json \
                     docs/phase-6.5-sonnet-comparison.json

Each comparison receipt names two experiments (one attempt / reflective). For
each, evaluate(<experiment name>) re-reads the saved runs and attaches judge
feedback; the converter does not run. Writes out/6.4/judge-<stamp>/ with a
journal per arm, judge-pass.json, and judge-table.md.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from langsmith import Client

from selenium2playwright import env
from selenium2playwright.eval_judge import JUDGE_VERSION, RUBRIC_SHA256, IdiomaticJudge
from selenium2playwright.eval_judge_pass import arm_summary, disagreements, judge_experiment, render_table

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--comparison", nargs="+", required=True, help="comparison receipts whose arms to judge")
    parser.add_argument("--arms", nargs="*", default=["one_attempt", "reflective"])
    parser.add_argument("--judge-model", default=None)
    parser.add_argument("--out", default="out/6.4")
    args = parser.parse_args()
    if not env.check():
        return 1

    model_name = args.judge_model or env.judge_model_name()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    folder = ROOT / args.out / f"judge-{stamp}"
    folder.mkdir(parents=True, exist_ok=False)
    revision = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=ROOT).stdout.strip()
    client = Client()
    judge = IdiomaticJudge(model_name=model_name)
    metadata = {"judge_version": JUDGE_VERSION, "judge_model": model_name, "rubric_sha256": RUBRIC_SHA256,
                "git_revision": revision}
    summaries, receipt_arms = [], []
    for path in args.comparison:
        comparison = json.loads(Path(path).read_text(encoding="utf-8"))
        if not comparison.get("comparable"):
            print(f"skip {path}: receipt is not comparable")
            continue
        for key in args.arms:
            arm = comparison["arms"][key]
            name = arm["experiment"]["name"]
            print(f"judging {name}", flush=True)
            records = judge_experiment(client, name, judge, folder / f"{name}.jsonl", metadata)
            summary = arm_summary(arm, records) | {"arm": key, "receipt": path,
                                                   "disagreements": disagreements(comparison, key, records)}
            summaries.append(summary)
            receipt_arms.append(summary)
    order = {"claude-haiku-4-5-20251001": 0, "claude-sonnet-5": 1, "claude-opus-5": 2}
    summaries.sort(key=lambda s: (order.get(s["model"].split(":", 1)[-1], 9), s["max_attempts"]))
    receipt = {"schema_version": 1, "measured_at_utc": datetime.now(timezone.utc).isoformat(), **metadata,
               "arms": summaries, "folder": str(folder.relative_to(ROOT))}
    (folder / "judge-pass.json").write_text(json.dumps(receipt, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (folder / "judge-table.md").write_text(render_table(summaries), encoding="utf-8")
    print(render_table(summaries))
    for s in summaries:
        print(f"{s['experiment']}: {s['disagreements']}")
    print(f"evidence: {folder}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Phase 6.3: run the same pinned benchmark twice — one attempt vs. reflection — and diff them.

    .venv/bin/python scripts/run_reflection_ab.py                 # preview: write both plans, no model calls
    .venv/bin/python scripts/run_reflection_ab.py --run           # live: two experiments, then comparison
    .venv/bin/python scripts/run_reflection_ab.py --compare-only out/6.3/ab-.../attempts-1 out/6.3/ab-.../attempts-3

Arm A: max_attempts=1 (draft only, the critic still reviews but never repairs).
Arm B: max_attempts=3 (draft + up to two repairs; the production default).
Dataset version, model, prompts, evaluators, and code revision stay identical.

--critic-model lets a different (stronger) model review the drafts in BOTH
arms, e.g. a Haiku actor with an Opus critic (docs/reflection-haiku-ab.md):

    .venv/bin/python scripts/run_reflection_ab.py --run --phase 6.5 \
        --model anthropic:claude-haiku-4-5-20251001 --critic-model anthropic:claude-opus-5
"""

from __future__ import annotations

import argparse
import json
import os
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from langsmith import Client

from selenium2playwright import env
from selenium2playwright.eval_compare import compare_reports, render_comparison_markdown
from selenium2playwright.eval_experiment import run_experiment
from selenium2playwright.eval_plan import DEFAULT_EVAL_MODEL, build_plan, write_json
from selenium2playwright.reflection import MAX_ATTEMPTS

ROOT = Path(__file__).resolve().parents[1]
ARMS = (1, MAX_ATTEMPTS)  # A then B, in this order, so both see the same code revision


def save_comparison(folder: Path, single: dict, reflective: dict) -> dict:
    """Write comparison.json + comparison.md beside the two arm directories."""
    comparison = compare_reports(single, reflective)
    write_json(folder / "comparison.json", comparison)
    (folder / "comparison.md").write_text(render_comparison_markdown(comparison), encoding="utf-8")
    return comparison


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--run", action="store_true", help="make provider calls and upload two experiments")
    mode.add_argument("--compare-only", nargs=2, type=Path, metavar=("ARM_A_DIR", "ARM_B_DIR"),
                      help="recompute the comparison from two saved report.json files")
    parser.add_argument("--model", default=DEFAULT_EVAL_MODEL, help="the actor (writes the code) in both arms")
    parser.add_argument("--critic-model", help="the critic (reviews the code) in both arms; default: same as --model")
    parser.add_argument("--phase", default="6.3", help="roadmap phase label for names, titles, and out/<phase>")
    parser.add_argument("--output-dir", type=Path, help="fresh directory; default is a unique name under out/<phase>")
    args = parser.parse_args()

    if args.compare_only:
        reports = [json.loads((d / "report.json").read_text()) for d in args.compare_only]
        folder = args.output_dir or args.compare_only[0].parent
        comparison = save_comparison(folder, *reports)
    else:
        critic = args.critic_model or args.model
        # The graph reads these two variables; the plan records the same pair
        # and run_experiment refuses to start if they ever disagree.
        os.environ["S2P_MODEL"], os.environ["S2P_CRITIC_MODEL"] = args.model, critic
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        folder = args.output_dir or ROOT / "out" / args.phase / f"ab-{stamp}-{uuid4().hex[:8]}"
        plans = {cap: build_plan(ROOT, args.model, max_attempts=cap, phase=args.phase, critic_model=critic) for cap in ARMS}
        print(json.dumps({"mode": "live" if args.run else "preview", "artifact_dir": str(folder), "model": args.model,
                          "critic_model": critic,
                          "arms": {f"attempts-{cap}": plan["metadata"]["configuration_sha256"] for cap, plan in plans.items()},
                          "scheduled_examples_per_arm": len(plans[1]["examples"])}, indent=2))
        if plans[1]["metadata"]["configuration"]["git_dirty"]:
            print("Warning: worktree is dirty; commit first so both arms cite one reviewable revision.")
        if not args.run:
            for cap, plan in plans.items():
                (folder / f"attempts-{cap}").mkdir(parents=True, exist_ok=False)
                write_json(folder / f"attempts-{cap}" / "plan.json", plan)
            return 0
        if any(not os.environ.get(key) for key in env.required()):
            raise ValueError("LangSmith and selected-provider credentials are required for --run")
        reports = []
        with closing(Client()) as client:
            for cap in ARMS:
                print(f"=== Arm max_attempts={cap} ===", flush=True)
                reports.append(run_experiment(plans[cap], client, folder / f"attempts-{cap}"))
                print(f"Arm {cap}: cloud verification {reports[-1]['cloud_verification']['status']}", flush=True)
        comparison = save_comparison(folder, *reports)

    print(f"Comparison: {folder / 'comparison.md'}")
    print(comparison["headline"])
    if not comparison["comparable"]:
        print("Not comparable:", *comparison["issues"], sep="\n  ")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

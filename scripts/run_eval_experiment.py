"""Phase 6.2: preview an experiment plan, run it, or retry only its cloud readback.

    uv run python scripts/run_eval_experiment.py
    uv run python scripts/run_eval_experiment.py --run
    uv run python scripts/run_eval_experiment.py --benchmark hard --phase 11.1b --run
    uv run python scripts/run_eval_experiment.py --verify-only out/6.2/experiment-...

Default preview is local. --run makes provider calls and uploads an experiment;
--verify-only reads existing cloud evidence without repeating conversions.

--benchmark selects which pinned dataset to measure: `base` is the Phase 6.1
twelve-file set, `hard` is the Step 11.1a twelve-hard-case set. Both are pinned
to an upload receipt, so a locally edited fixture stops the run instead of
scoring the converter against rows that are no longer in the cloud.
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
from selenium2playwright.eval_experiment import run_experiment, verify_saved
from selenium2playwright.eval_plan import BENCHMARKS, DEFAULT_EVAL_MODEL, build_plan, write_json

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    """Prepare a reviewable plan by default; execute only the selected explicit mode."""
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--run", action="store_true")
    mode.add_argument("--verify-only", type=Path, metavar="ARTIFACT_DIR")
    parser.add_argument("--model", default=DEFAULT_EVAL_MODEL, help="Actor and critic model for this evaluation process")
    parser.add_argument("--benchmark", default="base", choices=sorted(BENCHMARKS),
                        help="Which pinned dataset to measure (default: the Phase 6.1 set)")
    parser.add_argument("--phase", default=None,
                        help="Label for the experiment name and report title; defaults per benchmark")
    parser.add_argument("--output-dir", type=Path, help="Fresh artifact directory; default uses a unique name under out/<phase>")
    args = parser.parse_args()
    if args.verify_only:
        if not os.environ.get("LANGSMITH_API_KEY"):
            raise ValueError("LANGSMITH_API_KEY is required for cloud readback")
        with closing(Client()) as client:
            report = verify_saved(client, args.verify_only)
        folder = args.verify_only
    else:
        # Use the graph's existing model mechanism. No edit to .env or graph prompts.
        os.environ["S2P_MODEL"] = args.model
        phase = args.phase or ("11.1b" if args.benchmark == "hard" else "6.2")
        plan = build_plan(ROOT, args.model, phase=phase, benchmark=args.benchmark)
        label = "experiment" if args.run else "preview"
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        folder = args.output_dir or ROOT / "out" / phase / f"{label}-{stamp}-{uuid4().hex[:8]}"
        print(json.dumps({"mode": label, "artifact_dir": str(folder), "model": args.model,
                          "benchmark": args.benchmark, "phase": phase, "dataset": plan["dataset_name"],
                          "scheduled_examples": len(plan["examples"]), "expected_feedback": 8 * len(plan["examples"]),
                          "configuration_sha256": plan["metadata"]["configuration_sha256"]}, indent=2))
        if not args.run:
            folder.mkdir(parents=True, exist_ok=False)
            write_json(folder / "plan.json", plan)
            return 0
        if any(not os.environ.get(key) for key in env.required()):
            raise ValueError("LangSmith and selected-provider credentials are required for --run")
        with closing(Client()) as client:
            report = run_experiment(plan, client, folder)
    print(f"Report: {folder / 'report.md'}")
    print(f"Cloud verification: {report['cloud_verification']['status']}")
    if not report["local_integrity"]["complete"] or report["cloud_verification"]["status"] != "verified":
        return 2  # Evidence incomplete; quality percentages cannot certify this run.
    totals = report["aggregate"]
    return 0 if totals["all_static_passed"] == totals["graph_report_passed"] == totals["scheduled"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

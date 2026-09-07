"""Phase 6.4 — run the judge calibration: goldens, broken goldens, repeats.

    uv run python scripts/calibrate_judge.py --dry-run
    caffeinate -i -s uv run python scripts/calibrate_judge.py --judge-model anthropic:claude-opus-5

Offline against samples/: no dataset read, no converter run. Every judge call
is traced to LangSmith (project from .env) so token cost can be read back.
Writes out/6.4/calibration-<stamp>/{results.jsonl,calibration.json,calibration.md}.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from selenium2playwright import env
from selenium2playwright.eval_calibration import build_variants, render_markdown, score_variants, summarize
from selenium2playwright.eval_judge import JUDGE_VERSION, RUBRIC_SHA256, IdiomaticJudge

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--judge-model", default=None, help="provider:model; default S2P_JUDGE_MODEL chain")
    parser.add_argument("--repeats", type=int, default=2, help="how many times each golden is judged")
    parser.add_argument("--only", nargs="*", default=None, help="case ids to include (default all 12)")
    parser.add_argument("--out", default="out/6.4", help="parent folder for this run's evidence")
    parser.add_argument("--dry-run", action="store_true", help="list variants, call no model")
    args = parser.parse_args()

    variants = build_variants(ROOT / "samples", ROOT / "docs/evaluation-fixture-evidence.json",
                              set(args.only) if args.only else None)
    calls = sum(args.repeats if v["variant"] == "golden" else 1 for v in variants)
    model_name = args.judge_model or env.judge_model_name()
    print(f"judge {model_name}, rubric {RUBRIC_SHA256[:12]}…, {len(variants)} variants, {calls} judge calls")
    for v in variants:
        print(f"  {v['case_id']:22} {v['variant']}")
    if args.dry_run:
        return 0
    if not env.check():
        return 1

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    folder = ROOT / args.out / f"calibration-{stamp}"
    folder.mkdir(parents=True, exist_ok=False)
    revision = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=ROOT).stdout.strip()
    judge = IdiomaticJudge(model_name=model_name)
    with (folder / "results.jsonl").open("x", encoding="utf-8") as journal:
        def keep(record):
            journal.write(json.dumps(record, ensure_ascii=False) + "\n")
            journal.flush()
            print(f"  {record['case_id']:22} {record['variant']:17} #{record['repeat']} -> "
                  f"{record['score']} ({record['status']})", flush=True)
        records = score_variants(variants, judge, golden_repeats=args.repeats, on_record=keep)
    summary = summarize(records, model_name, RUBRIC_SHA256, JUDGE_VERSION) | {
        "git_revision": revision, "golden_repeats": args.repeats, "folder": str(folder.relative_to(ROOT))}
    (folder / "calibration.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (folder / "calibration.md").write_text(render_markdown(summary), encoding="utf-8")
    print(render_markdown(summary))
    print(f"evidence: {folder}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

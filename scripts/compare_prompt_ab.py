"""Step 11.1b: put a before/after playbook run side by side.

    uv run python scripts/compare_prompt_ab.py out/11.1b/baseline-gpt54 out/11.1b/tuned-gpt54 \
        --reserved 10 --reserved 11 --out docs/phase-11.1b-comparison.json

Reads two finished experiment directories. No model calls, no uploads. The
comparison refuses to call itself comparable unless every configuration key
matches except the file hashes, and among those only docs/playbook.md moved.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from selenium2playwright.eval_prompt_ab import compare_prompt_arms, render_prompt_ab_markdown

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("before", type=Path, help="baseline experiment directory")
    parser.add_argument("after", type=Path, help="post-edit experiment directory")
    parser.add_argument("--reserved", type=int, action="append", default=[],
                        help="a hard case deliberately kept out of the tuning loop (repeatable)")
    parser.add_argument("--out", type=Path, default=ROOT / "docs/phase-11.1b-comparison.json")
    args = parser.parse_args()
    load = lambda folder: json.loads((folder / "report.json").read_text(encoding="utf-8"))
    comparison = compare_prompt_arms(load(args.before), load(args.after),
                                     reserved=tuple(args.reserved))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(comparison, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    args.out.with_suffix(".md").write_text(render_prompt_ab_markdown(comparison), encoding="utf-8")
    print(json.dumps({"comparable": comparison["comparable"], "issues": comparison["issues"],
                      "headline": comparison["headline"], "changes": comparison["case_changes"],
                      "edited_files": comparison["edited_files"],
                      "written": [str(args.out), str(args.out.with_suffix(".md"))]}, indent=2))
    return 0 if comparison["comparable"] else 2


if __name__ == "__main__":
    raise SystemExit(main())

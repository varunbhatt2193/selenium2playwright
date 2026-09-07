"""Phase 6.4 — agreement between two judge passes over the same experiments.

    uv run python scripts/compare_judges.py docs/phase-6.4-judge-pass-opus.json docs/phase-6.4-judge-pass-gpt54.json \
        --json docs/phase-6.4-judge-agreement.json --markdown docs/phase-6.4-judge-agreement.md
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from selenium2playwright.eval_judge_pass import cross_judge, render_cross_table


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("pass_a")
    parser.add_argument("pass_b")
    parser.add_argument("--json", default=None)
    parser.add_argument("--markdown", default=None)
    args = parser.parse_args()
    cross = cross_judge(json.loads(Path(args.pass_a).read_text()), json.loads(Path(args.pass_b).read_text()))
    table = render_cross_table(cross)
    if args.json:
        Path(args.json).write_text(json.dumps(cross, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if args.markdown:
        Path(args.markdown).write_text(table, encoding="utf-8")
    print(table)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

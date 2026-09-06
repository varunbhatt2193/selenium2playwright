"""Phase 6.5: one diagram across actors from the A/B receipts (no model calls).

    .venv/bin/python scripts/render_actor_shootout.py \
        Haiku=docs/phase-6.5-haiku-comparison.json \
        Opus=docs/phase-6.3-comparison.json \
        --svg docs/reflection-shootout.svg --md docs/reflection-shootout-table.md

Every receipt must be comparable and every arm must share one critic model.
"""

from selenium2playwright.eval_shootout import main

if __name__ == "__main__":
    raise SystemExit(main())

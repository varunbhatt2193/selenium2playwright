"""Preview missing LangSmith roots/feedback, or restore them with --apply.

Requires an existing runner artifact directory. It never calls a converter,
model, or evaluator, and never replaces completed conflicting cloud evidence.
"""

import argparse
import asyncio
import hashlib
import json
import shutil
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from langsmith import Client

from selenium2playwright import env
from selenium2playwright.eval_plan import write_json
from selenium2playwright.eval_readback import readback_once
from selenium2playwright.eval_recovery import apply_action, recovery_actions


def main() -> None:
    """Save a complete repair plan before writing; journal each acknowledged write."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact_dir", type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    folder = args.artifact_dir
    journal = (folder / "results.jsonl").read_bytes()
    journal_hash = hashlib.sha256(journal).hexdigest()
    records = [json.loads(line) for line in journal.splitlines()]
    report = json.loads((folder / "report.json").read_text())
    # Use synchronous uploads so a failed write is visible at its call site,
    # rather than disappearing inside an SDK background multipart batch.
    with closing(Client(auto_batch_tracing=False)) as client:
        cloud = asyncio.run(readback_once(client, report, records))
        actions = recovery_actions(report, records, cloud)
        attempt = folder / ("recovery-" + uuid4().hex[:8])
        attempt.mkdir()
        write_json(attempt / "cloud-before.json", cloud)
        plan = {"experiment_id": report["experiment"]["id"], "journal_sha256": journal_hash,
                "apply": args.apply, "actions": actions, "created_at_utc": datetime.now(timezone.utc).isoformat()}
        write_json(attempt / "plan.json", plan)
        print(json.dumps(plan, indent=2), flush=True)
        if not args.apply:
            return
        # Preserve the original scorecard/readback before verify-only replaces
        # their current views. Each attempt has its own directory and receipt.
        for name in ("report.json", "report.md", "cloud-readback.json"):
            if (folder / name).exists():
                shutil.copy2(folder / name, attempt / name)
        with (attempt / "applied.jsonl").open("x") as receipt:
            for action in actions:
                apply_action(client, action, report, records, journal_hash)
                receipt.write(json.dumps(action) + "\n")
                receipt.flush()
        print(f"Acknowledged {len(actions)} writes. Run --verify-only with run_eval_experiment.py next.")


if __name__ == "__main__":
    main()

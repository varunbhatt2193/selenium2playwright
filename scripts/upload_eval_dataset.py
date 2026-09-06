"""Phase 6.1: preview locally, or explicitly upload and verify the curated dataset.

    .venv/bin/python scripts/upload_eval_dataset.py
    .venv/bin/python scripts/upload_eval_dataset.py --upload

Default mode creates reviewable JSON without network access. --upload writes
the same snapshot to LangSmith and records a receipt only after exact readback.
Neither mode runs the converter, calls a model, or starts a scored experiment.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path
from time import perf_counter

from selenium2playwright.eval_collection import build_collection

ROOT = Path(__file__).resolve().parents[1]


def write_json(path: Path, value: dict) -> None:
    """Save a readable, complete local artifact with a terminating newline."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main() -> int:
    """Preflight, save the snapshot, optionally upload, then save verified provenance."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--upload", action="store_true", help="Create/resume the dataset in LangSmith")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "out/6.1/dataset")
    args = parser.parse_args()
    started = perf_counter()
    # Build every row before constructing a client: a missing fixture cannot cause
    # a half-planned cloud dataset. This also rejects pending reviews/stale evidence.
    collection = build_collection(ROOT / "samples", ROOT / "docs/evaluation-fixture-evidence.json")
    write_json(args.output_dir / "collection.json", collection)
    print(json.dumps({"mode": "upload" if args.upload else "local-preview",
                      "dataset_name": collection["dataset_name"], "coverage": collection["coverage"]}, indent=2))
    if not args.upload:
        return 0

    # Load only at the network boundary. Upload needs a LangSmith key, not a model key.
    from selenium2playwright import env  # Central .env loading; no credential values printed.
    from langsmith import Client
    from selenium2playwright.eval_upload import upload_collection

    if not os.environ.get("LANGSMITH_API_KEY"):
        raise ValueError("LANGSMITH_API_KEY is required for --upload")
    client = Client()
    try:
        receipt = upload_collection(client, collection)
    finally:
        client.close()
    receipt["recorded_at_utc"] = datetime.now(timezone.utc).isoformat()
    receipt["elapsed_seconds"] = round(perf_counter() - started, 3)
    receipt["langsmith_sdk_version"] = version("langsmith")
    receipt["git_revision"] = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    receipt["git_dirty"] = bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True).strip())
    receipt["model_calls"] = 0
    receipt["converter_experiment"] = "not_run; Phase 6.2"
    write_json(args.output_dir / "receipt.json", receipt)
    print(json.dumps(receipt, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

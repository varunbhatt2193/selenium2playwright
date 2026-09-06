"""Preview a detailed experiment description; --publish writes and reads it back.

Only the description changes. Model outputs, feedback, configuration, and the
experiment name are preserved. Requires a verified saved experiment and narrative.
"""

import argparse
import json
from contextlib import closing
from pathlib import Path

from langsmith import Client

from selenium2playwright import env
from selenium2playwright.eval_readback import verify_cloud


def main() -> None:
    """Make the exact narrative reviewable before publishing to its checked experiment."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact_dir", type=Path)
    parser.add_argument("narrative", type=Path)
    parser.add_argument("--publish", action="store_true")
    args = parser.parse_args()
    report = json.loads((args.artifact_dir / "report.json").read_text())
    body = args.narrative.read_text(encoding="utf-8")
    identity = report["experiment"]["id"]
    if report["cloud_verification"]["status"] != "verified" or identity not in body:
        raise ValueError("Narrative must identify this verified experiment")
    # Relative documentation links work in GitHub, but LangSmith would resolve
    # them under its own website. Use this project's repository links there.
    base = "https://github.com/varunbhatt2193/selenium2playwright/blob/main/docs/"
    for name in ("evaluation-recovery.md", "phase-6.2-receipt.json"):
        body = body.replace(f"]({name})", f"]({base}{name})")
    (args.artifact_dir / "published-description.md").write_text(body, encoding="utf-8")
    print(f"Description preview: {args.artifact_dir / 'published-description.md'} ({len(body)} characters)")
    if not args.publish:
        return
    records = [json.loads(line) for line in (args.artifact_dir / "results.jsonl").read_text().splitlines()]
    with closing(Client()) as client:
        checked = verify_cloud(client, report, records)
        if checked["status"] != "verified":
            raise ValueError("Current cloud evidence no longer matches this report")
        client.update_project(identity, description=body)
        if client.read_project(project_id=identity).description != body:
            raise ValueError("Published description did not read back exactly")
    print(f"Published and verified description for experiment {identity}")


if __name__ == "__main__":
    main()

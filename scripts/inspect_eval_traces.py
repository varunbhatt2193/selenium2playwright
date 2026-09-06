"""Read a recorded experiment's model traces and feedback configuration; no writes.

Use the saved artifact directory from run_eval_experiment.py. This inspection
does not invoke the converter. Full selected trace evidence stays in ignored out/.
"""

import argparse
import asyncio
import json
from contextlib import closing
from pathlib import Path

from langsmith import Client

from selenium2playwright import env
from selenium2playwright.eval_plan import FEEDBACK_KEYS, write_json


async def inspect(client, folder: Path) -> dict:
    """Capture model-call evidence and key definitions for this one experiment."""
    report = json.loads((folder / "report.json").read_text())
    experiment = report["experiment"]
    # Query all trace nodes so absent model/graph steps can be counted rather
    # than assumed present from a successfully uploaded root run.
    traces = [run.model_dump(mode="json") async for run in client.runs.query(
        project_ids=[experiment["id"]], min_start_time=experiment["started_at_utc"],
        selects=["ID", "NAME", "RUN_TYPE", "PARENT_RUN_IDS", "TRACE_ID", "START_TIME", "END_TIME",
                 "STATUS", "INPUTS", "OUTPUTS", "ERROR", "METADATA", "TOTAL_TOKENS",
                 "PROMPT_TOKENS", "COMPLETION_TOKENS", "TOTAL_COST"])]
    configs = [config.model_dump(mode="json") for config in client.list_feedback_configs(feedback_key=FEEDBACK_KEYS)]
    result = {"experiment_id": experiment["id"], "traces": traces, "feedback_configs": configs}
    write_json(folder / "trace-audit.json", result)
    print(json.dumps({"trace_nodes": len(traces), "model_nodes": sum(t["run_type"].lower() == "llm" for t in traces),
                      "feedback_configs": configs}, indent=2))
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact_dir", type=Path)
    args = parser.parse_args()
    with closing(Client()) as client:
        asyncio.run(inspect(client, args.artifact_dir))


if __name__ == "__main__":
    main()

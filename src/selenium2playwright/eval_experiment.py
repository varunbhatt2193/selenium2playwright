"""Execute a pinned experiment, journal completed rows, and verify uploaded evidence."""

from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from functools import partial
from pathlib import Path
from time import perf_counter

from langsmith import evaluate, tracing_context

from selenium2playwright import env
from selenium2playwright.eval_evaluators import EVALUATORS
from selenium2playwright.eval_plan import configuration, digest, verified_examples, write_json
from selenium2playwright.eval_readback import verify_cloud
from selenium2playwright.eval_report import assemble_report, render_markdown
from selenium2playwright.eval_target import conversion_target

ROOT = Path(__file__).resolve().parents[2]


def save_report(folder: Path, report: dict) -> None:
    """Keep machine-readable evidence and the human walkthrough beside the journal."""
    write_json(folder / "report.json", report)
    (folder / "report.md").write_text(render_markdown(report), encoding="utf-8")


def verify_saved(client, folder: Path) -> dict:
    """Retry readback using saved evidence; this never calls the target or a model."""
    report = json.loads((folder / "report.json").read_text())
    records = [json.loads(line) for line in (folder / "results.jsonl").read_text().splitlines()]
    readback = verify_cloud(client, report, records)
    write_json(folder / "cloud-readback.json", readback)
    if readback["status"] == "verified":
        for record in records:
            record["remote_cost_usd"] = readback["costs_usd"].get(record["run"]["id"])
    report = assemble_report(report["plan"], records, report["experiment"], report["execution_error"])
    # Full remote objects are retained separately; the scorecard needs the
    # verification result and available costs, not another copy of every trace.
    report["cloud_verification"] = {k: v for k, v in readback.items()
                                     if k not in {"project", "runs", "feedback", "costs_usd"}}
    save_report(folder, report)
    return report


def run_experiment(plan: dict, client, folder: Path, *, upload_results: bool = True,
                   target=conversion_target) -> dict:
    """Run all checked examples once; preserve partial results and integrity failures."""
    # A fresh directory prevents reruns from mixing evidence or overwriting a
    # previous experiment. Preview and live runs each receive their own folder.
    folder.mkdir(parents=True, exist_ok=False)
    write_json(folder / "plan.json", plan)
    started = perf_counter()
    experiment = {"id": None, "name": None, "url": None, "upload_results": upload_results,
                  "mode": "live_converter" if upload_results else "offline_sdk_check",
                  "started_at_utc": datetime.now(timezone.utc).isoformat()}
    records, error = [], None
    with (folder / "results.jsonl").open("x", encoding="utf-8") as journal:
        try:
            config = plan["metadata"]["configuration"]
            if upload_results and target is not conversion_target:
                raise ValueError("Live runs must use conversion_target; injected candidates are offline test helpers")
            if target is conversion_target:
                # The plan, not the caller, decides the lap budget, so the recorded
                # configuration hash and the executed graph can never disagree.
                target = partial(conversion_target, max_attempts=config["max_attempts"])
            if upload_results and env.model_name() != config["model"]:
                raise ValueError("S2P_MODEL differs from the model recorded in the plan")
            if upload_results and env.critic_model_name() != config["critic_model"]:
                raise ValueError("S2P_CRITIC_MODEL differs from the critic model recorded in the plan")
            if digest(configuration(ROOT, config["model"], config["max_attempts"], config["critic_model"])) != plan["metadata"]["configuration_sha256"]:
                raise ValueError("Configuration changed since the plan was prepared")
            examples = verified_examples(client, plan)
            # Local tests inject a fixed target and use SDK local tracing. The
            # production CLI always uses conversion_target with uploads enabled.
            with tracing_context(client=client, enabled=True if upload_results else "local"):
                phase = plan["metadata"].get("phase", "6.2")
                short = lambda name: name.split(":", 1)[-1]
                critic = "" if config["critic_model"] == config["model"] else f"-critic-{short(config['critic_model'])}"
                results = evaluate(
                    target, data=examples, evaluators=EVALUATORS, client=client,
                    experiment_prefix=f"s2p-{phase}-{short(config['model'])}{critic}-attempts{config['max_attempts']}",
                    description=(f"Pinned 12-file benchmark; {config['max_attempts']} total conversion attempt(s); "
                                 "independent static checks; golden POM context for tests."),
                    metadata=copy.deepcopy(plan["metadata"]), max_concurrency=1, num_repetitions=1,
                    blocking=False, upload_results=upload_results, error_handling="log",
                )
                experiment["name"] = results.experiment_name
                if upload_results:
                    experiment.update(id=str(results.experiment_id), url=results.url)
                write_json(folder / "experiment.json", experiment)
                for item in results:
                    # Whitelist target-run fields. Full model child traces stay
                    # in LangSmith; never dump a credential-bearing client object.
                    record = {"example_id": str(item["example"].id),
                              "run": item["run"].model_dump(mode="json", include={
                                  "id", "trace_id", "reference_example_id", "inputs", "outputs",
                                  "error", "start_time", "end_time"}),
                              "feedback": [f.model_dump(mode="json") for f in item["evaluation_results"]["results"]]}
                    records.append(record)
                    journal.write(json.dumps(record, ensure_ascii=False) + "\n")
                    journal.flush()  # Completed rows survive a later ordinary failure.
                    print(f"Recorded {len(records)}/{len(examples)}: {item['example'].metadata['case_id']}", flush=True)
            if digest(configuration(ROOT, config["model"], config["max_attempts"], config["critic_model"])) != plan["metadata"]["configuration_sha256"]:
                raise ValueError("Configuration changed during execution; this is not a single-configuration experiment")
        except Exception as exc:
            error = {"type": type(exc).__name__, "message": str(exc) or type(exc).__name__}
    experiment.update(finished_at_utc=datetime.now(timezone.utc).isoformat(), elapsed_seconds=perf_counter() - started)
    write_json(folder / "experiment.json", experiment)
    report = assemble_report(plan, records, experiment, error)
    save_report(folder, report)  # Local evidence is durable before remote verification.
    if upload_results and report["local_integrity"]["complete"]:
        report = verify_saved(client, folder)
    return report

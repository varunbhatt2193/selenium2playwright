"""A reproducible live demo: seed one missing await, then let the real model repair it.

    uv run python scripts/demo_reflection.py

The FIRST draft is explicitly injected from the golden fixture with one await
removed. All critic calls and subsequent conversions use the configured model.
The seed is recorded in trace metadata; this is not an unseeded quality evaluation.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from langchain_core.tracers.langchain import wait_for_all_tracers
from langsmith import Client, tracing_context

from selenium2playwright import graph
from selenium2playwright.schemas import ConversionResult

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    source = ROOT / "samples/selenium-suite/pages/LoginPage.ts"
    golden = (ROOT / "samples/playwright-golden/pages/LoginPage.ts").read_text(encoding="utf-8")
    broken = golden.replace("await this.usernameInput.fill", "this.usernameInput.fill", 1)
    if broken == golden:
        raise RuntimeError("The golden fixture changed; update the missing-await seed before running this demo")
    original_convert = graph.convert

    def seeded_convert(state):
        if state["iteration"] == 0:
            return {"status": "converted", "result": ConversionResult(code=broken),
                    "iteration": 1, "usage": None, "conversion_error": ""}
        return original_convert(state)

    run_id = uuid4()
    output = ROOT / "out/5.2"
    output.mkdir(parents=True, exist_ok=True)
    events = []
    final = {}
    print("Injected first draft: golden LoginPage with one await removed. Subsequent calls use the real model.", flush=True)
    print(f"LangSmith run ID: {run_id}", flush=True)
    with tracing_context(enabled=True), patch.object(graph, "convert", seeded_convert):
        for update in graph.build_graph().stream(
            {"source_path": str(source)}, stream_mode="updates",
            config={"run_id": run_id, "run_name": "reflection-demo-seeded-missing-await",
                    "tags": ["step:5.2", "demo:seeded-missing-await"],
                    "metadata": {"seed": "golden POM with first fill await removed; first model call bypassed"},
                    "recursion_limit": 14},
        ):
            for node, values in update.items():
                final.update(values)
                event = {"node": node, "attempt": final.get("iteration", 0)}
                if node == "validate":
                    event["gates"] = {r.gate: r.passed for r in values["validation"]}
                if node == "critic":
                    review = values["critique"]
                    event["critique"] = review.model_dump() if review else values["critique_error"]
                if node == "assemble":
                    event["status"] = values["report"].status
                events.append(event)
                print(json.dumps(event), flush=True)

    report = final["report"]
    (output / "report.json").write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
    (output / "trace.json").write_text(json.dumps({"run_id": str(run_id), "seeded": True, "events": events}, indent=2) + "\n")
    if report.result is not None:
        (output / "LoginPage.ts").write_text(report.result.code, encoding="utf-8")
    wait_for_all_tracers()
    try:
        client = Client()
        url = client.get_run_url(run=client.read_run(run_id))
        (output / "trace-url.txt").write_text(url + "\n", encoding="utf-8")
        print(f"LangSmith trace: {url}", flush=True)
    except Exception as exc:
        print(f"Trace URL lookup failed; use run ID {run_id}: {exc}", flush=True)
    print(f"{report.status}: {report.reason}", flush=True)
    print(f"Artifacts: {output}", flush=True)
    return 0 if report.status == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())

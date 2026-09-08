"""Step 11.2 — run converted code in a real browser against a pinned local app.

    docker compose -f deploy/the-internet/compose.yml up -d
    uv run python scripts/run_execution_eval.py --goldens --suite base
    uv run python scripts/run_execution_eval.py --experiment out/11.1b/arm-c-playbook2
    uv run python scripts/run_execution_eval.py --tree out/9.3

--goldens runs the untouched fixtures and is the CI gate: it proves the ruler
still measures. --experiment replays the code a finished experiment already
produced, so the execution number costs browser time and no tokens at all.
--tree runs a whole converted suite, whose page objects and tests were converted
together — the one arrangement in which nobody has to guess the other's names.

Exit codes: 0 everything green, 1 something ran red, 2 the app is not reachable
or the request was malformed. Nothing here calls a model or LangSmith.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from selenium2playwright.eval_execution import (execute_experiment, execute_goldens, execute_tree,
                                                render_experiment_markdown, render_goldens_markdown)
from selenium2playwright.execution import LOCAL_BASE_URL, SUITES, wait_for_app

ROOT = Path(__file__).resolve().parents[1]


def write(folder: Path, name: str, report: dict, markdown: str) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{name}.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (folder / f"{name}.md").write_text(markdown, encoding="utf-8")
    print(f"Report: {folder / (name + '.md')}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--goldens", action="store_true", help="run the untouched fixtures (the CI gate)")
    mode.add_argument("--experiment", type=Path, metavar="ARTIFACT_DIR",
                      help="a finished experiment folder containing report.json")
    mode.add_argument("--tree", type=Path, metavar="CONVERTED_DIR",
                      help="a whole converted suite (pages/ + tests/), such as a suite-mode run")
    parser.add_argument("--suite", default="base", choices=sorted(SUITES),
                        help="which benchmark to check against (--goldens/--tree; an experiment names its own)")
    parser.add_argument("--base-url", default=LOCAL_BASE_URL,
                        help="the app to run against; the public site is allowed but not hermetic")
    parser.add_argument("--only", nargs="*", default=None, help="limit an experiment run to these case IDs")
    parser.add_argument("--out", type=Path, default=ROOT / "out" / "11.2")
    parser.add_argument("--wait", type=float, default=60.0, help="seconds to wait for the app to answer")
    args = parser.parse_args()

    status = wait_for_app(args.base_url, args.wait)
    if not status["reachable"]:
        print(json.dumps({"app_unreachable": status, "hint":
                          "docker compose -f deploy/the-internet/compose.yml up -d"}, indent=2))
        return 2
    revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
                              capture_output=True, text=True).stdout.strip()
    if args.goldens:
        report = execute_goldens(ROOT, args.suite, args.base_url) | {"git_revision": revision}
        write(args.out, f"goldens-{args.suite}", report, render_goldens_markdown(report))
        print(json.dumps({"suite": args.suite, "passed": report["passed"],
                          "counts": report["counts"], "failures": report["failures"]}, indent=2))
        return 0 if report["passed"] else 1

    if args.tree:
        folder = args.tree if args.tree.is_absolute() else ROOT / args.tree
        report = execute_tree(ROOT, args.suite, folder, args.base_url) | {"git_revision": revision}
        write(args.out, f"tree-{folder.name}", report, render_goldens_markdown(report))
        print(json.dumps({"suite": args.suite, "source": report["source"], "passed": report["passed"],
                          "counts": report["counts"], "failures": report["failures"]}, indent=2))
        return 0 if report["passed"] else 1

    folder = args.experiment if args.experiment.is_absolute() else ROOT / args.experiment
    if not (folder / "report.json").exists():
        print(f"{folder}/report.json not found; --experiment wants a finished experiment folder")
        return 2
    report = execute_experiment(ROOT, folder, args.base_url, only=args.only) | {"git_revision": revision}
    write(args.out, f"experiment-{folder.name}", report, render_experiment_markdown(report))
    totals = report["aggregate"]
    print(json.dumps({"suite": report["suite"], "aggregate": totals,
                      "quadrants": {k: len(v) for k, v in report["quadrants"].items()}}, indent=2))
    return 0 if totals["passed"] == totals["scheduled"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

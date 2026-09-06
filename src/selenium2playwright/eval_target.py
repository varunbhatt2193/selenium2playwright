"""Phase 6.2 — adapt captured dataset inputs to the graph's file-based intake.

Only ``example.inputs`` belongs here: reference outputs stay with evaluators.
See docs/evaluation-target.md for the layout, failure contract, and walkthrough.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory
from time import perf_counter

from selenium2playwright.graph import build_graph
from selenium2playwright.reflection import MAX_ATTEMPTS, resolve_attempt_cap


def validate_inputs(inputs: dict) -> None:
    """Reject malformed snapshots before creating files or invoking the graph."""
    # Exact keys catch accidentally passing the whole example or its answer.
    if not isinstance(inputs, dict) or set(inputs) != {"source_path", "source", "context_files"}:
        raise ValueError("Target requires only source_path, source, and context_files")
    if not isinstance(inputs["context_files"], dict):
        raise ValueError("context_files must map relative TypeScript paths to captured text")
    files = [(inputs["source_path"], inputs["source"]), *inputs["context_files"].items()]
    seen = set()
    for relative, code in files:
        if not isinstance(relative, str) or "\x00" in relative:
            raise ValueError("Snapshot paths must be strings without NUL characters")
        path = PurePosixPath(relative)
        if (path.is_absolute() or ".." in path.parts or "\\" in relative or ":" in relative
                or path.as_posix() != relative or path.suffix != ".ts"):
            raise ValueError(f"Expected a canonical suite-relative .ts path: {relative!r}")
        if not isinstance(code, str) or not code.strip():
            raise ValueError(f"Snapshot text must be non-blank: {relative!r}")
        # Include the target in collision checks: its own answer cannot be a
        # companion. Case folding also catches aliases on macOS file systems.
        key = relative.casefold()
        if any(key == prior or key.startswith(prior + "/") or prior.startswith(key + "/")
               for prior in seen):
            raise ValueError(f"Snapshot paths collide: {relative!r}")
        seen.add(key)


def materialize_inputs(inputs: dict, workspace: Path) -> dict:
    """Write captured inputs into a fresh, caller-owned workspace for graph intake."""
    validate_inputs(inputs)
    source = workspace / "source" / inputs["source_path"]
    target = workspace / "converted" / inputs["source_path"]
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(inputs["source"], encoding="utf-8")
    companions = []
    for relative, code in sorted(inputs["context_files"].items()):
        companion = workspace / "converted" / relative
        companion.parent.mkdir(parents=True, exist_ok=True)
        companion.write_text(code, encoding="utf-8")
        companions.append(str(companion))
    # output_path is an import anchor, not a prewritten answer. The graph
    # returns generated code in memory; tests and POMs retain relative imports.
    return {"source_path": str(source), "context_paths": companions, "output_path": str(target)}


def conversion_target(inputs: dict, *, max_attempts: int | None = None) -> dict:
    """Run one isolated conversion and return JSON-compatible evidence for scoring.

    max_attempts is the lap budget handed to the graph (step 6.3 A/B): 1 means a
    single conversion with no repair; None means the graph default of 3.
    The recorded output includes the cap so a row can never be misread later.
    """
    started = perf_counter()
    cap = resolve_attempt_cap(max_attempts)
    output = {"code": None, "conversion_status": "error", "report": None, "refusal": "",
              "usage": None, "critic_usage": None, "adapter_error": None, "max_attempts": cap}
    try:
        # Every call owns its directory; no shared cwd change and no reads from
        # samples/ or playwright-golden/. Cleanup runs even when invoke raises.
        with TemporaryDirectory(prefix="s2p-eval-") as folder:
            graph_inputs = materialize_inputs(inputs, Path(folder)) | {"max_attempts": cap}
            final = build_graph().invoke(graph_inputs, config={
                "run_name": "evaluation-conversion", "tags": ["step:6.3", "target:v2", f"attempts:{cap}"],
                "recursion_limit": 3 * MAX_ATTEMPTS + 5,
            })
            # Assembly is authoritative: it includes the final TODO ledger and
            # preserves a prior draft when a later repair/provider call fails.
            report = None if final["status"] == "refused" else final["report"].model_dump(mode="json")
            result = report["result"] if report is not None else None
            output.update(code=result["code"] if result is not None else None,
                          conversion_status=final["status"], report=report,
                          refusal=final.get("refusal", ""), usage=final.get("usage"),
                          critic_usage=final.get("critic_usage"))
    except Exception as exc:
        # This is the evaluation row boundary. An escaped exception must remain
        # visible to future evaluators, not silently remove a difficult example.
        # Without returned state, partial attempts/usage are unknown, not zero.
        output["conversion_status"] = "error"
        output["adapter_error"] = {"type": type(exc).__name__, "message": str(exc) or type(exc).__name__}
    output["elapsed_seconds"] = perf_counter() - started
    return output

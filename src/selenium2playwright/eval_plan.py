"""Freeze experiment inputs and provenance before creating any cloud experiment."""

from __future__ import annotations

import hashlib
import json
import platform
import subprocess
from datetime import datetime
from importlib.metadata import version
from pathlib import Path
from uuid import UUID

from selenium2playwright.eval_collection import build_collection
from selenium2playwright.eval_evaluators import EVALUATOR_VERSION, GATE_KEYS
from selenium2playwright.eval_upload import expected_examples
from selenium2playwright.llm import MAX_OUTPUT_TOKENS
from selenium2playwright.reflection import MAX_ATTEMPTS

DEFAULT_EVAL_MODEL = "anthropic:claude-opus-5"  # Learning agreement: Opus for evals.
FEEDBACK_KEYS = [key for metric in GATE_KEYS.values() for key in (metric, metric + "_status")]


def digest(value: dict) -> str:
    """Hash canonical JSON so dictionary insertion order cannot change identity."""
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def write_json(path: Path, value: dict) -> None:
    """Replace one artifact atomically; a partial write cannot truncate its predecessor."""
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def configuration(root: Path, model: str) -> dict:
    """Capture explicit settings and relevant file/tool identities, never credentials."""
    paths = set((root / "src").rglob("*.py")) | set((root / "sandbox").glob("*.json"))
    paths |= set((root / "sandbox").glob("*.mjs")) | set((root / "sandbox").glob("*.cjs"))
    paths |= {root / p for p in ("docs/playbook.md", "pyproject.toml", "uv.lock",
                                "scripts/run_eval_experiment.py")}
    hashes = {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in sorted(paths)}
    packages = json.loads((root / "sandbox/package.json").read_text())["devDependencies"]
    installed = {name: json.loads((root / "sandbox/node_modules" / name / "package.json").read_text())["version"]
                 for name in packages}
    if installed != packages:
        raise ValueError("Installed sandbox package versions differ from the pinned manifest")
    git = lambda *args: subprocess.check_output(["git", *args], cwd=root, text=True).strip()
    return {
        "model": model, "max_output_tokens": MAX_OUTPUT_TOKENS, "max_attempts": MAX_ATTEMPTS,
        "critic_effort": "medium" if model.startswith("anthropic:") else None,
        "critic_structured_output": "json_schema",
        "actor_structured_output": "provider_default", "temperature": "not_set",
        "max_concurrency": 1, "num_repetitions": 1, "evaluator_version": EVALUATOR_VERSION,
        "git_revision": git("rev-parse", "HEAD"), "git_dirty": bool(git("status", "--porcelain")),
        "file_sha256": hashes, "python": platform.python_version(), "platform": platform.platform(),
        "python_packages": {name: version(name) for name in (
            "langsmith", "langchain", "langchain-core", "langchain-anthropic", "langgraph", "anthropic", "pydantic")},
        "node": subprocess.check_output(["node", "--version"], text=True).strip(), "sandbox_packages": installed,
        "path_policy": "fresh temporary absolute paths; stable suite-relative layout",
    }


def build_plan(root: Path, model: str = DEFAULT_EVAL_MODEL) -> dict:
    """Bind the complete local collection to its verified upload receipt and configuration."""
    collection = build_collection(root / "samples", root / "docs/evaluation-fixture-evidence.json")
    receipt = json.loads((root / "docs/phase-6.1-receipt.json").read_text())
    if (receipt["status"] != "verified" or receipt["collection_sha256"] != collection["collection_sha256"]
            or receipt["dataset_name"] != collection["dataset_name"]):
        raise ValueError("The upload receipt does not identify the current verified collection")
    timestamp = datetime.fromisoformat(receipt["dataset_version"])
    if timestamp.tzinfo is None:
        raise ValueError("The pinned dataset version must include its timezone")
    expected = expected_examples(UUID(receipt["dataset_id"]), collection["examples"])
    if (receipt["example_ids"] != {row["metadata"]["case_id"]: identity for identity, row in expected.items()}
            or receipt["examples_verified"] != len(expected)):
        raise ValueError("Receipt example identities/count differ from the planned collection")
    config = configuration(root, model)
    return {
        "schema_version": 1, "dataset_id": receipt["dataset_id"], "dataset_url": receipt["dataset_url"],
        "dataset_name": receipt["dataset_name"], "dataset_version": receipt["dataset_version"],
        "examples": expected, "coverage": collection["coverage"],
        "metadata": {"phase": "6.2", "collection_sha256": collection["collection_sha256"],
                     "pinned_dataset_version": receipt["dataset_version"], "models": [model],
                     "configuration": config, "configuration_sha256": digest(config),
                     "expected_examples": len(expected), "expected_feedback_keys": FEEDBACK_KEYS},
    }


def verified_examples(client, plan: dict) -> list:
    """Read one pinned snapshot and compare its full contents before any model call."""
    dataset = client.read_dataset(dataset_id=plan["dataset_id"])
    if (str(dataset.id) != plan["dataset_id"] or dataset.name != plan["dataset_name"]
            or (dataset.metadata or {}).get("collection_sha256") != plan["metadata"]["collection_sha256"]):
        raise ValueError("Remote dataset identity differs from the experiment plan")
    examples = list(client.list_examples(dataset_id=plan["dataset_id"], as_of=plan["dataset_version"]))
    found = {}
    for example in examples:
        identity = str(example.id)
        if identity not in plan["examples"] or identity in found:
            raise ValueError(f"Unexpected or duplicate example: {identity}")
        expected = plan["examples"][identity]
        metadata = expected["metadata"] | {"dataset_split": ["base"]}
        if (str(example.dataset_id) != plan["dataset_id"] or example.inputs != expected["inputs"]
                or example.outputs != expected["outputs"] or example.metadata != metadata):
            raise ValueError(f"Pinned remote contents differ for {expected['metadata']['case_id']}")
        found[identity] = example
    if set(found) != set(plan["examples"]):
        raise ValueError("Pinned remote dataset is missing scheduled examples")
    # Return the actual checked objects in stable case order; do not fetch again.
    return [found[identity] for identity in plan["examples"]]

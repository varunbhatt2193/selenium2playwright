"""Publish a preflighted collection and verify the server's versioned contents.

Reruns add only missing rows. Existing mismatches are errors, never implicit
updates/deletions: later experiments must retain a stable reference dataset.
"""

from __future__ import annotations

from uuid import UUID, uuid5

from langsmith import Client
from langsmith.utils import LangSmithConflictError, LangSmithNotFoundError


def expected_examples(dataset_id: UUID, rows: list[dict]) -> dict[str, dict]:
    """Give each case a repeatable UUID within this dataset, preventing retry duplicates."""
    return {str(uuid5(dataset_id, row["metadata"]["case_id"])): row for row in rows}


def missing_examples(client: Client, dataset_id: UUID, expected: dict[str, dict],
                     *, as_of=None) -> list[dict]:
    """Compare full server contents; return missing rows only after ruling out drift."""
    found = set()
    for remote in client.list_examples(dataset_id=dataset_id, as_of=as_of):
        identity = str(remote.id)
        if identity not in expected or identity in found:
            raise ValueError(f"Unexpected or duplicate remote example: {identity}")
        row = expected[identity]
        # Comparing the stored hash alone would miss edits that leave metadata intact.
        # LangSmith stores its split membership in metadata, adding ["base"] by
        # default. Check that exact value too; do not broadly ignore server fields.
        expected_metadata = {**row["metadata"], "dataset_split": ["base"]}
        if (remote.inputs != row["inputs"] or remote.outputs != row["outputs"]
                or remote.metadata != expected_metadata):
            raise ValueError(f"Remote contents differ for {row['metadata']['case_id']}; refusing to overwrite")
        found.add(identity)
    return [{"id": identity, **row, "split": ["base"]}
            for identity, row in expected.items() if identity not in found]


def upload_collection(client: Client, collection: dict) -> dict:
    """Create/resume an immutable-by-convention collection and return a verified receipt."""
    name = collection["dataset_name"]
    created = False
    try:
        dataset = client.read_dataset(dataset_name=name)
    except LangSmithNotFoundError:
        try:
            dataset = client.create_dataset(
                dataset_name=name,
                description=(
                    "Phase 6.1 curated development benchmark: 12 file conversions, six scenarios, "
                    "eight browser tests per framework. Test rows receive golden POM companions. "
                    "Inputs are source snapshots; outputs are independent references. "
                    "Fixture-validation metadata is measured baseline evidence, not converter scores. "
                    "Iframe typing remains uncovered. See repository docs/evaluation-dataset.md."
                ),
                metadata={"collection_sha256": collection["collection_sha256"],
                          "schema_version": 1, "coverage": collection["coverage"]},
            )
            created = True
        except LangSmithConflictError:
            # Another invocation may have created this same name between read/create.
            dataset = client.read_dataset(dataset_name=name)
    if (dataset.metadata or {}).get("collection_sha256") != collection["collection_sha256"]:
        raise ValueError("Dataset name exists with a different collection fingerprint")
    expected = expected_examples(dataset.id, collection["examples"])
    missing = missing_examples(client, dataset.id, expected)
    if missing:
        # An interrupted bulk request can leave a partial dataset. The next invocation
        # reads it first and resumes only absent, deterministically identified rows.
        client.create_examples(dataset_id=dataset.id, examples=missing)

    version = client.read_dataset_version(dataset_id=dataset.id, tag="latest")
    remaining = missing_examples(client, dataset.id, expected, as_of=version.as_of)
    if remaining:
        raise ValueError(f"Versioned readback is incomplete: {len(remaining)} examples missing")
    dataset = client.read_dataset(dataset_id=dataset.id)
    return {
        "status": "verified", "dataset_name": name, "dataset_id": str(dataset.id),
        "dataset_url": dataset.url, "dataset_version": version.as_of.isoformat(),
        "collection_sha256": collection["collection_sha256"],
        "dataset_created": created, "examples_created": len(missing),
        "examples_verified": len(expected), "coverage": collection["coverage"],
        "example_ids": {row["metadata"]["case_id"]: identity for identity, row in expected.items()},
        "verification": "Exact inputs, reference outputs and metadata at the recorded dataset version",
    }

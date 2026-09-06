"""Protect benchmark completeness, answer separation, and repeatable cloud writes.

The fake server deliberately supports partial writes and corrupt readback. These
tests exercise failure paths without credentials, provider calls, or network.
"""

import copy
import shutil
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

from langsmith.utils import LangSmithNotFoundError
from selenium2playwright.eval_collection import build_collection
from selenium2playwright.eval_dataset import snapshot_example
from selenium2playwright.eval_manifest import CASES
from selenium2playwright.eval_upload import upload_collection

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "docs/evaluation-fixture-evidence.json"


class FakeClient:
    def __init__(self):
        self.dataset = None
        self.examples = {}
        self.writes = 0
        self.interrupt_once = False
        self.corrupt_version = False

    def read_dataset(self, **kwargs):
        if self.dataset is None:
            raise LangSmithNotFoundError("No dataset")
        return self.dataset

    def create_dataset(self, dataset_name, metadata, **kwargs):
        self.writes += 1
        self.dataset = SimpleNamespace(id=uuid4(), metadata=metadata, url="https://example.test/dataset")
        return self.dataset

    def create_examples(self, *, examples, **kwargs):
        self.writes += 1
        for row in examples:
            payload = copy.deepcopy(row)
            payload["id"] = UUID(payload["id"])
            # Match the live service's explicit split -> metadata representation.
            payload["metadata"]["dataset_split"] = payload.pop("split", ["base"])
            self.examples[str(payload["id"])] = SimpleNamespace(**payload)
            if self.interrupt_once:
                self.interrupt_once = False
                raise RuntimeError("Connection dropped after storing one row")

    def list_examples(self, *, as_of=None, **kwargs):
        examples = copy.deepcopy(list(self.examples.values()))
        if as_of and self.corrupt_version:
            examples[0].outputs["code"] = "corrupted after upload"
        return iter(examples)

    def read_dataset_version(self, **kwargs):
        return SimpleNamespace(as_of=datetime(2026, 9, 6, tzinfo=timezone.utc))


class DatasetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.collection = build_collection(ROOT / "samples", EVIDENCE)

    def test_complete_manifest_has_separate_answers_and_six_companions(self):
        rows = self.collection["examples"]
        self.assertEqual(len(rows), 12)
        self.assertEqual(sum(bool(r["inputs"]["context_files"]) for r in rows), 6)
        for row in rows:
            self.assertNotIn("code", row["inputs"])
            self.assertNotIn("expected_behaviors", row["inputs"])
            self.assertIn("code", row["outputs"])

    def test_manifest_order_does_not_change_collection_identity(self):
        reversed_collection = build_collection(ROOT / "samples", EVIDENCE, tuple(reversed(CASES)))
        self.assertEqual(self.collection, reversed_collection)

    def test_missing_duplicate_and_pending_manifest_entries_fail(self):
        invalid = [CASES[:-1], CASES[:-1] + (CASES[0],),
                   (replace(CASES[0], reference_review="pending"),) + CASES[1:]]
        for cases in invalid:
            with self.subTest(cases=tuple(c.case_id for c in cases)), self.assertRaises(ValueError):
                build_collection(ROOT / "samples", EVIDENCE, cases)

    def test_missing_file_and_stale_browser_evidence_fail(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "samples"
            for suite in ("selenium-suite", "playwright-golden"):
                shutil.copytree(ROOT / "samples" / suite, root / suite)
            target = root / "selenium-suite" / CASES[0].path
            target.write_text(target.read_text() + "\n// changed\n")
            with self.assertRaisesRegex(ValueError, "changed since"):
                build_collection(root, EVIDENCE)
            target.unlink()
            with self.assertRaises(FileNotFoundError):
                build_collection(root, EVIDENCE)

    def test_own_answer_and_ambiguous_paths_are_rejected(self):
        invalid = [replace(CASES[0], companions=(CASES[0].path,))]
        invalid += [replace(CASES[0], path=path) for path in
                    ("../outside.ts", "/tmp/outside.ts", "pages/./LoginPage.ts", "pages\\LoginPage.ts")]
        for case in invalid:
            with self.subTest(path=case.path), self.assertRaises(ValueError):
                snapshot_example(case, ROOT / "samples")

    def test_upload_twice_keeps_exactly_twelve_rows_and_makes_no_second_write(self):
        client = FakeClient()
        first = upload_collection(client, self.collection)
        writes = client.writes
        second = upload_collection(client, self.collection)
        self.assertEqual(first["examples_created"], 12)
        self.assertEqual(second["examples_created"], 0)
        self.assertEqual(second["examples_verified"], 12)
        self.assertEqual(client.writes, writes)
        self.assertEqual(first["example_ids"], second["example_ids"])

    def test_interrupted_upload_resumes_only_missing_rows(self):
        client = FakeClient()
        client.interrupt_once = True
        with self.assertRaisesRegex(RuntimeError, "Connection dropped"):
            upload_collection(client, self.collection)
        self.assertEqual(len(client.examples), 1)
        result = upload_collection(client, self.collection)
        self.assertEqual(result["examples_created"], 11)
        self.assertEqual(result["examples_verified"], 12)

    def test_remote_content_edit_with_unchanged_hash_is_rejected_before_writing(self):
        client = FakeClient()
        upload_collection(client, self.collection)
        next(iter(client.examples.values())).outputs["code"] = "wrong code; metadata hash unchanged"
        # Even when another row is missing, validate all existing rows before adding it.
        client.examples.pop(next(reversed(client.examples)))
        writes = client.writes
        with self.assertRaisesRegex(ValueError, "refusing to overwrite"):
            upload_collection(client, self.collection)
        self.assertEqual(client.writes, writes)

    def test_foreign_dataset_and_unexpected_example_are_rejected(self):
        client = FakeClient()
        upload_collection(client, self.collection)
        client.dataset.metadata = {"collection_sha256": "someone-elses-dataset"}
        with self.assertRaisesRegex(ValueError, "different collection"):
            upload_collection(client, self.collection)
        client.dataset.metadata = {"collection_sha256": self.collection["collection_sha256"]}
        foreign = copy.deepcopy(next(iter(client.examples.values())))
        foreign.id = uuid4()
        client.examples[str(foreign.id)] = foreign
        with self.assertRaisesRegex(ValueError, "Unexpected"):
            upload_collection(client, self.collection)

    def test_bad_versioned_readback_cannot_produce_verified_receipt(self):
        client = FakeClient()
        client.corrupt_version = True
        with self.assertRaisesRegex(ValueError, "refusing to overwrite"):
            upload_collection(client, self.collection)

    def test_only_the_expected_server_split_metadata_is_accepted(self):
        client = FakeClient()
        upload_collection(client, self.collection)
        remote = next(iter(client.examples.values()))
        remote.metadata["dataset_split"] = ["other-split"]
        with self.assertRaisesRegex(ValueError, "refusing to overwrite"):
            upload_collection(client, self.collection)
        remote.metadata["dataset_split"] = ["base"]
        remote.metadata["unplanned_field"] = True
        with self.assertRaisesRegex(ValueError, "refusing to overwrite"):
            upload_collection(client, self.collection)

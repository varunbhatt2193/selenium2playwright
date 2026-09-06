"""Exercise real SDK orchestration locally and reject incomplete experiment evidence."""

import copy
import io
import json
import unittest
from contextlib import closing, redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import UUID, uuid4

from langsmith import Client
from langsmith.schemas import Example, Feedback, ModelFeedbackSource, TracerSession
from langsmith._openapi_client.types.run import Run as CloudRun

from selenium2playwright import eval_experiment
from selenium2playwright.eval_evaluators import GATE_KEYS, gate_feedback
from selenium2playwright.eval_plan import build_plan, digest, verified_examples
from selenium2playwright.eval_readback import verify_cloud
from selenium2playwright.eval_report import assemble_report, measurement, render_markdown
from selenium2playwright.schemas import ValidationReport
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]


class OfflineClient(Client):
    """Use SDK local evaluation machinery, supplying dataset reads from memory."""
    def __init__(self, plan):
        super().__init__(api_url="http://localhost:1", api_key="local-test", auto_batch_tracing=False)
        self.plan = plan
        self.reads = []
        self.examples = [Example(id=UUID(identity), dataset_id=UUID(plan["dataset_id"]),
            inputs=row["inputs"], outputs=row["outputs"], metadata=row["metadata"] | {"dataset_split": ["base"]},
            modified_at=datetime.fromisoformat(plan["dataset_version"])) for identity, row in plan["examples"].items()]

    def read_dataset(self, **kwargs):
        return SimpleNamespace(id=UUID(self.plan["dataset_id"]), name=self.plan["dataset_name"],
                               metadata={"collection_sha256": self.plan["metadata"]["collection_sha256"]})

    def list_examples(self, **kwargs):
        self.reads.append(kwargs)
        return iter(copy.deepcopy(self.examples))


def fixed_output(row):
    """A known candidate for plumbing tests; never used by the production CLI."""
    code = row["outputs"]["code"]
    return {"code": code, "conversion_status": "converted", "elapsed_seconds": 1.0,
            "usage": None, "critic_usage": None, "adapter_error": None, "refusal": "",
            "report": {"status": "passed", "attempts": 1, "reason": "Fixed candidate",
                       "result": {"code": code, "todos": [], "notes": []}, "errors": [], "critique": None}}


def fixed_records(plan):
    """Build coherent local records for report/readback corruption tests."""
    records = []
    for identity, row in plan["examples"].items():
        now = datetime.now(timezone.utc).isoformat()
        feedback = [item for gate in GATE_KEYS for item in gate_feedback(
            gate, "passed", perf_counter(), ValidationReport(gate=gate, passed=True))]
        records.append({"example_id": identity, "run": {"id": str(uuid4()), "reference_example_id": identity,
            "inputs": row["inputs"], "outputs": fixed_output(row), "error": None, "start_time": now, "end_time": now},
            "feedback": feedback})
    return records


class FakeReadback:
    """Mimic current asynchronous run queries and synchronous feedback reads."""
    def __init__(self, plan, records, experiment):
        self.project = TracerSession(id=UUID(experiment["id"]), tenant_id=uuid4(),
            reference_dataset_id=UUID(plan["dataset_id"]), extra={"metadata": copy.deepcopy(plan["metadata"])})
        self.root_runs = [CloudRun(**copy.deepcopy(record["run"]), total_cost=0.01, total_tokens=30) for record in records]
        self.feedback = [Feedback(id=uuid4(), run_id=UUID(record["run"]["id"]), trace_id=None,
            created_at=datetime.now(timezone.utc), modified_at=datetime.now(timezone.utc),
            key=item["key"], score=item.get("score"), value=item.get("value"),
            comment=item.get("comment"), feedback_source=ModelFeedbackSource(metadata=copy.deepcopy(item.get("evaluator_info", {}))))
            for record in records for item in record["feedback"]]
        self.queries = []
        self.runs = SimpleNamespace(query=self.query)
        self.flush = Mock()

    def query(self, **kwargs):
        self.queries.append(kwargs)
        async def iterate():
            for run in self.root_runs:
                yield run
        return iterate()

    def read_project(self, **kwargs):
        return self.project

    def list_feedback(self, **kwargs):
        return iter(self.feedback)


class ExperimentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plan = build_plan(ROOT)

    def test_plan_records_complete_identity_and_allowlisted_configuration(self):
        self.assertEqual(len(self.plan["examples"]), 12)
        config = self.plan["metadata"]["configuration"]
        self.assertEqual(config["model"], "anthropic:claude-opus-5")
        self.assertEqual(self.plan["metadata"]["configuration_sha256"], digest(config))
        self.assertNotEqual(digest(config), digest(config | {"model": "different-model"}))
        self.assertIn("src/selenium2playwright/eval_target.py", config["file_sha256"])
        self.assertIn("sandbox/eslint.config.mjs", config["file_sha256"])
        self.assertNotIn(".env", config["file_sha256"])
        self.assertNotIn("API_KEY", json.dumps(config))

    def test_pinned_read_rejects_missing_duplicate_and_changed_examples(self):
        mutations = [lambda rows: rows.pop(), lambda rows: rows.append(rows[0]),
                     lambda rows: rows[0].inputs.update(source="Changed source"),
                     lambda rows: rows[0].outputs.update(code="Changed reference"),
                     lambda rows: rows[0].metadata.update(scenario="wrong")]
        for mutate in mutations:
            with self.subTest(mutation=mutate), closing(OfflineClient(copy.deepcopy(self.plan))) as client:
                mutate(client.examples)
                with self.assertRaises(ValueError):
                    verified_examples(client, self.plan)
        with closing(OfflineClient(self.plan)) as client:
            rows = verified_examples(client, self.plan)
            self.assertEqual([str(row.id) for row in rows], list(self.plan["examples"]))
            self.assertEqual(client.reads, [{"dataset_id": self.plan["dataset_id"], "as_of": self.plan["dataset_version"]}])

    def test_real_sdk_runs_twelve_fixed_candidates_and_writes_complete_local_reports(self):
        by_path = {row["inputs"]["source_path"]: row for row in self.plan["examples"].values()}
        def target(inputs):
            return fixed_output(by_path[inputs["source_path"]])
        with TemporaryDirectory() as temporary, closing(OfflineClient(self.plan)) as client, redirect_stdout(io.StringIO()), \
                patch("requests.Session.request", side_effect=AssertionError("Offline test attempted network")) as network:
            folder = Path(temporary) / "experiment"
            report = eval_experiment.run_experiment(self.plan, client, folder, upload_results=False, target=target)
            network.assert_not_called()
            self.assertTrue(report["local_integrity"]["complete"], report["local_integrity"])
            self.assertEqual(report["aggregate"]["all_static_passed"], 12)
            self.assertEqual(report["cloud_verification"]["status"], "not_checked")
            self.assertEqual(len((folder / "results.jsonl").read_text().splitlines()), 12)
            saved = json.loads((folder / "report.json").read_text())
            self.assertEqual(saved, report)
            self.assertIn("alerts-test evidence", (folder / "report.md").read_text())
            self.assertIsNone(report["aggregate"]["langsmith_root_cost_usd"]["total"])
            self.assertEqual(len(client.reads), 1)
            with self.assertRaises(FileExistsError):
                eval_experiment.run_experiment(self.plan, client, folder, upload_results=False, target=target)

    def test_preflight_drift_prevents_sdk_execution_and_keeps_all_rows_missing(self):
        plan = copy.deepcopy(self.plan)
        plan["metadata"]["configuration_sha256"] = "stale"
        with TemporaryDirectory() as temporary, closing(OfflineClient(plan)) as client, patch.object(eval_experiment, "evaluate") as evaluate:
            report = eval_experiment.run_experiment(plan, client, Path(temporary) / "run", upload_results=False)
        evaluate.assert_not_called()
        self.assertFalse(report["local_integrity"]["complete"])
        self.assertEqual(report["aggregate"]["metrics"]["compiles"]["statuses"], {"missing_result": 12})

    def test_interrupted_sdk_iterator_preserves_the_completed_row_and_partial_report(self):
        from langsmith.evaluation import EvaluationResult
        record = fixed_records(self.plan)[0]
        class Interrupted:
            experiment_name = "interrupted-local-test"
            def __iter__(self):
                yield {"example": Example(id=UUID(record["example_id"]), metadata={"case_id": "alerts-page"}),
                       "run": SimpleNamespace(model_dump=lambda **kwargs: record["run"]),
                       "evaluation_results": {"results": [EvaluationResult.model_validate(f) for f in record["feedback"]]}}
                raise RuntimeError("Stopped after first evaluated row")
        with TemporaryDirectory() as temporary, closing(OfflineClient(self.plan)) as client, redirect_stdout(io.StringIO()), \
                patch.object(eval_experiment, "evaluate", return_value=Interrupted()):
            folder = Path(temporary) / "run"
            report = eval_experiment.run_experiment(self.plan, client, folder, upload_results=False)
            self.assertEqual(len((folder / "results.jsonl").read_text().splitlines()), 1)
            self.assertEqual(json.loads((folder / "report.json").read_text()), report)
        self.assertFalse(report["local_integrity"]["complete"])
        self.assertEqual(report["aggregate"]["metrics"]["compiles"]["passed"], 1)
        self.assertEqual(report["aggregate"]["metrics"]["compiles"]["scheduled"], 12)
        self.assertEqual(report["execution_error"]["type"], "RuntimeError")

    def test_readback_verifies_full_feedback_and_reads_older_runs_with_explicit_window(self):
        records = fixed_records(self.plan)
        for record in records:
            record["run"]["outputs"].update(usage={"total_tokens": 10}, critic_usage={"total_tokens": 20})
        experiment = {"id": str(uuid4()), "started_at_utc": "2026-08-01T00:00:00+00:00"}
        report = assemble_report(self.plan, records, experiment)
        client = FakeReadback(self.plan, records, experiment)
        result = verify_cloud(client, report, records, attempts=1)
        self.assertEqual(result["status"], "verified", result["issues"])
        self.assertEqual((result["root_runs_read"], result["feedback_read"]), (12, 96))
        self.assertEqual(client.queries[0]["min_start_time"], experiment["started_at_utc"])
        self.assertIn("INPUTS", client.queries[0]["selects"])
        self.assertEqual(measurement(list(result["costs_usd"].values()))["total"], "0.12")

    def test_cloud_missing_duplicate_or_changed_evidence_never_verifies(self):
        records = fixed_records(self.plan)
        experiment = {"id": str(uuid4()), "started_at_utc": "2026-08-01T00:00:00+00:00"}
        report = assemble_report(self.plan, records, experiment)
        mutations = [lambda c: c.root_runs.pop(), lambda c: c.feedback.pop(),
                     lambda c: c.feedback.append(c.feedback[0]),
                     lambda c: c.feedback[0].feedback_source.metadata.pop("report"),
                     lambda c: c.root_runs[0].outputs.update(code="Changed remotely"),
                     lambda c: c.project.metadata.update(pinned_dataset_version="wrong-version")]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                client = FakeReadback(self.plan, records, experiment)
                mutate(client)
                result = verify_cloud(client, report, records, attempts=1)
                self.assertEqual(result["status"], "unverified")
                self.assertTrue(result["issues"])

    def test_cloud_readback_retries_without_running_conversions(self):
        records = fixed_records(self.plan)
        experiment = {"id": str(uuid4()), "started_at_utc": "2026-08-01T00:00:00+00:00"}
        report = assemble_report(self.plan, records, experiment)
        client = FakeReadback(self.plan, records, experiment)
        with patch.object(client, "read_project", side_effect=[RuntimeError("Temporary read error"), client.project]), \
                patch.object(eval_experiment, "conversion_target") as target:
            result = verify_cloud(client, report, records, attempts=2, delay=0)
        target.assert_not_called()
        self.assertEqual((result["status"], result["attempts"]), ("verified", 2))

    def test_saved_readback_updates_costs_without_repeating_the_target(self):
        from selenium2playwright.eval_plan import write_json
        records = fixed_records(self.plan)
        for record in records:
            record["run"]["outputs"].update(usage={"total_tokens": 10}, critic_usage={"total_tokens": 20})
        experiment = {"id": str(uuid4()), "started_at_utc": "2026-08-01T00:00:00+00:00"}
        report = assemble_report(self.plan, records, experiment)
        client = FakeReadback(self.plan, records, experiment)
        with TemporaryDirectory() as temporary, patch.object(eval_experiment, "evaluate") as evaluate:
            folder = Path(temporary)
            write_json(folder / "report.json", report)
            (folder / "results.jsonl").write_text("\n".join(json.dumps(record) for record in records) + "\n")
            verified = eval_experiment.verify_saved(client, folder)
            self.assertEqual(verified["cloud_verification"]["status"], "verified")
            self.assertEqual(verified["aggregate"]["langsmith_root_cost_usd"]["total"], "0.12")
            self.assertEqual(len(json.loads((folder / "cloud-readback.json").read_text())["feedback"]), 96)
        evaluate.assert_not_called()

    def test_partial_cloud_tokens_or_unknown_local_usage_exclude_cost_without_changing_scores(self):
        records = fixed_records(self.plan)
        for record in records:
            record["run"]["outputs"].update(usage={"total_tokens": 10}, critic_usage={"total_tokens": 20})
        records[1]["run"]["outputs"]["critic_usage"] = None
        experiment = {"id": str(uuid4()), "started_at_utc": "2026-08-01T00:00:00+00:00"}
        report = assemble_report(self.plan, records, experiment)
        client = FakeReadback(self.plan, records, experiment)
        client.root_runs[0].total_tokens = 20  # One child upload was lost.
        result = verify_cloud(client, report, records, attempts=1)
        self.assertEqual(result["status"], "verified")
        self.assertEqual(measurement(list(result["costs_usd"].values())), {
            "total": None, "known_subtotal": "0.10", "known_rows": 10, "missing_rows": 2})
        self.assertEqual(report["aggregate"]["all_static_passed"], 12)

    def test_live_mode_rejects_injected_targets_before_any_dataset_or_model_call(self):
        with TemporaryDirectory() as temporary, closing(OfflineClient(self.plan)) as client, \
                patch.object(eval_experiment, "evaluate") as evaluate:
            report = eval_experiment.run_experiment(self.plan, client, Path(temporary) / "run", target=lambda inputs: {})
            self.assertEqual(client.reads, [])
        evaluate.assert_not_called()
        self.assertFalse(report["local_integrity"]["complete"])
        self.assertIn("Live runs must use conversion_target", report["execution_error"]["message"])

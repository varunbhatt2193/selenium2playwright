"""Verify snapshot isolation and real graph integration without provider calls.

Fixed replies exercise plumbing, not model quality; they are never uploaded as
converter scores. The actual TypeScript gates still run in the integration test.
"""

import copy
import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda

from selenium2playwright import eval_target, graph
from selenium2playwright.eval_collection import build_collection
from selenium2playwright.schemas import ConversionReport, ConversionResult, Critique

ROOT = Path(__file__).resolve().parents[1]


class EvalTargetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows = build_collection(ROOT / "samples", ROOT / "docs/evaluation-fixture-evidence.json")["examples"]

    def setUp(self):
        tracing = patch.dict(os.environ, {"LANGSMITH_TRACING": "false", "LANGCHAIN_TRACING_V2": "false"})
        tracing.start()
        self.addCleanup(tracing.stop)

    def test_every_snapshot_materializes_only_inputs_with_matching_import_layout(self):
        for row in self.rows:
            inputs = copy.deepcopy(row["inputs"])
            inputs["source"] += "\n// Captured edit that is absent from the checkout.\n"
            original = copy.deepcopy(inputs)
            with self.subTest(case=row["metadata"]["case_id"]), TemporaryDirectory() as folder:
                root = Path(folder)
                paths = eval_target.materialize_inputs(inputs, root)
                state = graph.intake(paths)
                self.assertEqual(state["source"], inputs["source"])
                self.assertEqual(Path(paths["source_path"]), root / "source" / inputs["source_path"])
                self.assertEqual(Path(paths["output_path"]), root / "converted" / inputs["source_path"])
                self.assertFalse(Path(paths["output_path"]).exists())  # No golden answer was copied.
                expected = {str((root / "converted" / p).resolve()): c for p, c in inputs["context_files"].items()}
                self.assertEqual(state["context_files"], expected)
                self.assertEqual(len([p for p in root.rglob("*") if p.is_file()]), 1 + len(expected))
                self.assertEqual(inputs, original)

    def test_invalid_snapshots_never_reach_the_graph(self):
        valid = self.rows[0]["inputs"]
        invalid = [self.rows[0], valid | {"reference_outputs": {}}, valid | {"source": " "},
                   valid | {"source": None}, valid | {"context_files": []}]
        for path in ("/tmp/a.ts", "../a.ts", "pages/../a.ts", "./a.ts", "a//b.ts", "a\\b.ts",
                     "C:a.ts", "a.js", "a\x00.ts", 7):
            invalid.append(valid | {"source_path": path})
        for path in (valid["source_path"], valid["source_path"].upper().replace(".TS", ".ts"),
                     valid["source_path"] + "/nested.ts", "../answer.ts"):
            invalid.append(valid | {"context_files": {path: "export {};"}})
        invalid.append(valid | {"context_files": {"pages/Blank.ts": ""}})
        with patch.object(eval_target, "build_graph") as build:
            for inputs in invalid:
                with self.subTest(inputs=inputs):
                    output = eval_target.conversion_target(inputs)
                    self.assertEqual(output["conversion_status"], "error")
                    self.assertEqual(output["adapter_error"]["type"], "ValueError")
                    self.assertIsNone(output["code"])
            build.assert_not_called()

    def test_real_graph_and_four_gates_accept_pom_and_test_with_fixed_replies(self):
        for row in self.rows:
            if row["metadata"]["case_id"] not in ("login-page", "login-test"):
                continue
            actor_prompts = []
            def structured(schema, **kwargs):
                def reply(prompt):
                    if schema is ConversionResult:
                        # Provider preparation can turn a PromptValue into messages.
                        messages = prompt.to_messages() if hasattr(prompt, "to_messages") else prompt
                        actor_prompts.append("\n".join(message.text for message in messages))
                    parsed = Critique(verdict="pass", fixes=[]) if schema is Critique else ConversionResult(code=row["outputs"]["code"])
                    return {"parsed": parsed, "raw": AIMessage(content=""), "parsing_error": None}
                return RunnableLambda(reply)
            model = Mock()
            model.with_structured_output.side_effect = structured
            with self.subTest(case=row["metadata"]["case_id"]), patch.object(graph, "make_model", return_value=model):
                output = eval_target.conversion_target(row["inputs"])
                self.assertIsNone(output["adapter_error"])
                self.assertEqual(output["report"]["status"], "passed", output["report"])
                self.assertEqual(output["report"]["attempts"], 1)
                self.assertEqual(output["code"], row["outputs"]["code"])
                self.assertEqual([r["gate"] for r in output["report"]["validation"]], ["compile", "residue", "lint", "parity"])
                self.assertTrue(all(r["passed"] for r in output["report"]["validation"]))
                self.assertIn(row["inputs"]["source"], actor_prompts[0])
                self.assertNotIn(row["outputs"]["code"], actor_prompts[0])
                json.dumps(output)  # LangSmith needs plain JSON-compatible values.

    def test_refusal_is_explicit_and_never_calls_a_model(self):
        inputs = {"source_path": "tests/unsupported.ts", "source": 'import { browser } from "webdriverio";', "context_files": {}}
        with patch.object(graph, "make_model") as model:
            output = eval_target.conversion_target(inputs)
        model.assert_not_called()
        self.assertEqual(output["conversion_status"], "refused")
        self.assertTrue(output["refusal"])
        self.assertIsNone(output["report"])
        self.assertIsNone(output["code"])
        self.assertIsNone(output["adapter_error"])

    def test_handled_provider_failure_keeps_the_graphs_failed_report(self):
        with patch.object(graph, "make_model", side_effect=RuntimeError("Provider unavailable")):
            output = eval_target.conversion_target(self.rows[0]["inputs"])
        self.assertEqual(output["conversion_status"], "failed")
        self.assertEqual(output["report"]["status"], "needs-review")
        self.assertEqual(output["report"]["attempts"], 1)
        self.assertEqual(output["report"]["errors"], ["Provider unavailable"])
        self.assertIsNone(output["code"])
        self.assertIsNone(output["adapter_error"])  # Already handled inside the graph.

    def test_retained_draft_findings_and_usage_survive_serialization(self):
        # Assembly already decided to retain this draft after a failed repair.
        # The adapter must carry that decision, not turn code existence into pass.
        report = ConversionReport(
            status="needs-review", attempts=2, reason="Repair failed; retaining the prior draft.",
            result=ConversionResult(code="export {}; // TODO(review): check behavior\n",
                                    notes=["Prior draft"], todos=["check behavior"]),
            validation=[{"gate": "compile", "passed": False, "findings": [{
                "gate": "compile", "file": "pages/AlertsPage.ts", "code": "validator-error",
                "message": "Compiler unavailable", "line": None}], "tool_output": "raw tool details"}],
            critique=Critique(verdict="revise", fixes=["Restore the compiler"]), errors=["Repair unavailable"],
        )
        usage = {"input_tokens": 30, "output_tokens": 10, "input_token_details": {"cache_read": 20}}
        final = {"status": "converted", "report": report, "usage": usage, "critic_usage": {"total_tokens": 9}}
        with patch.object(eval_target, "build_graph", return_value=Mock(invoke=Mock(return_value=final))):
            output = eval_target.conversion_target(self.rows[0]["inputs"])
        self.assertEqual(output["code"], report.result.code)
        self.assertEqual(output["conversion_status"], "converted")
        self.assertEqual(output["report"], report.model_dump(mode="json"))
        self.assertEqual(output["usage"], usage)
        self.assertEqual(output["critic_usage"], final["critic_usage"])
        self.assertEqual(json.loads(json.dumps(output)), output)

    def test_escaped_graph_error_is_reported_and_each_workspace_is_removed(self):
        folders = []
        def fail(inputs, config):
            folders.append(Path(inputs["source_path"]).parents[2])
            self.assertTrue(folders[-1].exists())
            raise RuntimeError("Graph interrupted before returning final state")
        with patch.object(eval_target, "build_graph", return_value=Mock(invoke=fail)):
            for _ in range(2):
                output = eval_target.conversion_target(self.rows[0]["inputs"])
                self.assertEqual(output["adapter_error"]["type"], "RuntimeError")
                self.assertIsNone(output["report"])
                self.assertIsNone(output["usage"])
                self.assertGreaterEqual(output["elapsed_seconds"], 0)
        self.assertNotEqual(*folders)
        self.assertTrue(all(not folder.exists() for folder in folders))


if __name__ == "__main__":
    unittest.main()

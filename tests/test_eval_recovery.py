"""Ensure recovery replays authentic evidence without duplicate or conflicting writes."""

import copy
import unittest
from unittest.mock import Mock

from selenium2playwright.eval_plan import build_plan
from selenium2playwright.eval_readback import verify_cloud
from selenium2playwright.eval_recovery import apply_action, recovery_actions
from selenium2playwright.eval_report import assemble_report
from test_eval_experiment import ROOT, FakeReadback, fixed_records
from uuid import uuid4


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.plan = build_plan(ROOT)
        self.records = fixed_records(self.plan)
        for r in self.records:
            r["run"]["trace_id"] = r["run"]["id"]
        self.experiment = {"id": str(uuid4()), "name": "recovery-test",
                           "started_at_utc": "2026-08-01T00:00:00+00:00"}
        self.report = assemble_report(self.plan, self.records, self.experiment)
        self.cloud = verify_cloud(FakeReadback(self.plan, self.records, self.experiment),
                                  self.report, self.records, attempts=1)

    def test_complete_evidence_needs_no_writes(self):
        self.assertEqual(recovery_actions(self.report, self.records, self.cloud), [])

    def test_missing_root_and_feedback_have_stable_actions_and_original_content(self):
        record = self.records[-1]
        self.cloud["runs"].pop()
        self.cloud["feedback"] = self.cloud["feedback"][:-8]
        actions = recovery_actions(self.report, self.records, self.cloud)
        self.assertEqual(len(actions), 9)
        self.assertEqual(actions, recovery_actions(self.report, self.records, self.cloud))
        client = Mock()
        for action in actions:
            apply_action(client, action, self.report, self.records, "saved-journal-hash")
        created = client.create_run.call_args.kwargs
        self.assertEqual(created["id"], record["run"]["id"])
        self.assertEqual(created["outputs"], record["run"]["outputs"])
        self.assertEqual(created["trace_id"], record["run"]["trace_id"])
        self.assertEqual(client.create_feedback.call_count, 8)
        for call, original in zip(client.create_feedback.call_args_list, record["feedback"]):
            self.assertNotIn("feedback_config", call.kwargs)
            for key in ("score", "value", "comment"):
                self.assertEqual(call.kwargs[key], original.get(key))
            self.assertEqual(call.kwargs["source_info"]["upload_recovery"]["journal_sha256"], "saved-journal-hash")
        client.update_run.assert_not_called()

    def test_unfinished_root_can_be_finished_but_completed_roots_are_not_overwritten(self):
        root = self.cloud["runs"][-1]
        root.update(end_time=None, outputs=None)
        actions = recovery_actions(self.report, self.records, self.cloud)
        self.assertEqual(actions, [{"operation": "finish_root", "run_id": root["id"]}])
        client = Mock()
        apply_action(client, actions[0], self.report, self.records, "hash")
        self.assertEqual(client.update_run.call_args.kwargs["outputs"], self.records[-1]["run"]["outputs"])
        client.create_run.assert_not_called()

    def test_any_conflict_blocks_the_entire_plan_even_after_missing_items(self):
        mutations = [lambda c: c["runs"][0]["outputs"].update(code="remote edit"),
                     lambda c: c["runs"][0]["inputs"].update(source="different input"),
                     lambda c: c["feedback"][0].update(score=0),
                     lambda c: c["feedback"][0]["feedback_source"]["metadata"].pop("report"),
                     lambda c: c["feedback"].append(c["feedback"][0]),
                     lambda c: c["runs"].append(c["runs"][0]),
                     lambda c: c["project"].update(reference_dataset_id=str(uuid4()))]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                cloud = copy.deepcopy(self.cloud)
                cloud["runs"].pop()  # There is recoverable work, but conflicts take priority.
                mutate(cloud)
                with self.assertRaises(ValueError):
                    recovery_actions(self.report, self.records, cloud)

    def test_local_corruption_blocks_recovery(self):
        self.records[-1]["feedback"].pop()
        with self.assertRaisesRegex(ValueError, "local journal"):
            recovery_actions(self.report, self.records, self.cloud)


if __name__ == "__main__":
    unittest.main()

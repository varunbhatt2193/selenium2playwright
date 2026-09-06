"""Actor and critic can be different models; the plan records and enforces both."""

import copy
import os
import unittest
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import uuid4

from langchain_core.runnables import RunnableLambda, RunnablePassthrough

from selenium2playwright import env, eval_experiment, llm
from selenium2playwright.eval_compare import compare_reports
from selenium2playwright.eval_plan import build_plan, configuration, digest
from test_eval_experiment import OfflineClient

ROOT = Path(__file__).resolve().parents[1]
HAIKU, OPUS = "anthropic:claude-haiku-4-5-20251001", "anthropic:claude-opus-5"


class EnvModelSplitTests(unittest.TestCase):
    def test_critic_defaults_to_the_actor_unless_set(self):
        with patch.dict(os.environ, {"S2P_MODEL": HAIKU}, clear=False), patch.dict(os.environ):
            os.environ.pop("S2P_CRITIC_MODEL", None)
            self.assertEqual(env.model_names(), {"actor": HAIKU, "critic": HAIKU})
            os.environ["S2P_CRITIC_MODEL"] = OPUS
            self.assertEqual(env.model_names(), {"actor": HAIKU, "critic": OPUS})

    def test_required_keys_cover_every_provider_in_use(self):
        with patch.dict(os.environ, {"S2P_MODEL": HAIKU, "S2P_CRITIC_MODEL": "openai:gpt-5"}):
            self.assertEqual(set(env.required()), {"LANGSMITH_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY"})
        with patch.dict(os.environ, {"S2P_MODEL": HAIKU, "S2P_CRITIC_MODEL": "mystery:model"}):
            with self.assertRaisesRegex(ValueError, "S2P_CRITIC_MODEL='mystery:model'"):
                env.required()

    def test_make_model_and_cache_marker_follow_the_role(self):
        with patch.dict(os.environ, {"S2P_MODEL": HAIKU, "S2P_CRITIC_MODEL": "openai:gpt-5"}), \
                patch.object(llm, "init_chat_model") as initialize:
            llm.make_model()
            self.assertEqual(initialize.call_args.args[0], HAIKU)
            llm.make_model(for_critic=True)
            self.assertEqual(initialize.call_args.args[0], "openai:gpt-5")
            self.assertNotIn("effort", initialize.call_args.kwargs)  # not Anthropic, no effort knob
            llm.make_model(OPUS, for_critic=True)
            self.assertEqual((initialize.call_args.args[0], initialize.call_args.kwargs["effort"]), (OPUS, "medium"))
            self.assertIsInstance(llm.prepare_messages(), RunnableLambda)          # Anthropic actor: cache marker
            self.assertIsInstance(llm.prepare_messages(for_critic=True), RunnablePassthrough)  # OpenAI critic: none


class PlanModelSplitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.same = build_plan(ROOT, HAIKU, max_attempts=1, phase="6.5")
        cls.split = build_plan(ROOT, HAIKU, max_attempts=1, phase="6.5", critic_model=OPUS)

    def test_critic_model_is_hashed_and_listed(self):
        same, split = self.same["metadata"]["configuration"], self.split["metadata"]["configuration"]
        self.assertEqual((same["critic_model"], split["critic_model"]), (HAIKU, OPUS))
        self.assertEqual({k for k in same if same[k] != split[k]}, {"critic_model"})
        self.assertNotEqual(digest(same), digest(split))
        self.assertEqual(self.split["metadata"]["models"], sorted({HAIKU, OPUS}))
        self.assertEqual(digest(configuration(ROOT, HAIKU, 1, OPUS)), self.split["metadata"]["configuration_sha256"])

    def test_live_runner_refuses_a_critic_env_that_disagrees_with_the_plan(self):
        with TemporaryDirectory() as temporary, closing(OfflineClient(self.split)) as client, \
                patch.dict(os.environ, {"S2P_MODEL": HAIKU, "S2P_CRITIC_MODEL": HAIKU}), \
                patch.object(eval_experiment, "evaluate") as evaluate:
            report = eval_experiment.run_experiment(self.split, client, Path(temporary) / "run")
        evaluate.assert_not_called()
        self.assertIn("S2P_CRITIC_MODEL differs", report["execution_error"]["message"])

    def test_live_runner_names_the_critic_only_when_it_differs(self):
        class Empty:
            experiment_name, experiment_id, url = "s2p-6.5-local", uuid4(), "http://example.invalid"
            def __iter__(self):
                return iter(())
        for plan, prefix in ((self.split, "s2p-6.5-claude-haiku-4-5-20251001-critic-claude-opus-5-attempts1"),
                             (self.same, "s2p-6.5-claude-haiku-4-5-20251001-attempts1")):
            config = plan["metadata"]["configuration"]
            with self.subTest(prefix=prefix), TemporaryDirectory() as temporary, closing(OfflineClient(plan)) as client, \
                    patch.dict(os.environ, {"S2P_MODEL": config["model"], "S2P_CRITIC_MODEL": config["critic_model"]}), \
                    patch.object(eval_experiment, "evaluate", return_value=Empty()) as evaluate:
                eval_experiment.run_experiment(plan, client, Path(temporary) / "run")
            self.assertEqual(evaluate.call_args.kwargs["experiment_prefix"], prefix)
            self.assertEqual(evaluate.call_args.kwargs["metadata"]["configuration"]["critic_model"], config["critic_model"])

    def test_comparison_refuses_arms_with_different_critics(self):
        from test_reflection_ab import AttemptCapEvaluationTests
        make = AttemptCapEvaluationTests("_report")._report
        reflective = copy.deepcopy(self.split)
        reflective["metadata"]["configuration"]["max_attempts"] = 3
        comparison = compare_reports(make(self.same), make(reflective))
        self.assertFalse(comparison["comparable"])
        self.assertIn("configuration.critic_model differs between arms", comparison["issues"])
        self.assertEqual(comparison["held_fixed"]["critic_model"], HAIKU)


if __name__ == "__main__":
    unittest.main()

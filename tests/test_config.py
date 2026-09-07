"""Step 8.2 — run-scoped configuration: --model, --max-attempts, --json.

Three layers, deliberately tested apart: env.py resolves a name before anything
is built, the context schema carries the resolved names into one run, and the
CLI turns flags into that context and into a JSON document. Everything is
offline — the model is scripted, the four validation gates are the real tools.

The harness comes from test_cli because this is the same surface; `-s tests`
puts this directory on sys.path, which is how the import resolves.
"""

import json
import os
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import Mock, patch

from test_cli import GOLDEN, PASS, REVISE, SOURCE, CliHarness

from selenium2playwright import cli, env, graph, llm
from selenium2playwright.schemas import ConversionResult

OPUS, HAIKU = "anthropic:claude-opus-5", "anthropic:claude-haiku-4-5-20251001"
SONNET = "anthropic:claude-sonnet-5"


class ModelNameTests(unittest.TestCase):
    """env.py decides which model a run means, before anything is built."""

    def test_aliases_expand_full_names_pass_through_and_a_bare_word_is_refused(self):
        self.assertEqual(env.resolve_model("opus"), OPUS)
        self.assertEqual(env.resolve_model("openai:gpt-4o"), "openai:gpt-4o")
        self.assertEqual(env.resolve_model(" sonnet "), SONNET)
        # No provider half and no alias: guessing a provider would surface much
        # later as an authentication error against the wrong company.
        with self.assertRaises(ValueError) as refused:
            env.resolve_model("gpt-4o")
        self.assertIn("provider:model", str(refused.exception))

    def test_a_flag_beats_the_environment_and_the_actor_carries_the_critic(self):
        with patch.dict(os.environ, {"S2P_MODEL": HAIKU}):
            os.environ.pop("S2P_CRITIC_MODEL", None)
            self.assertEqual(env.resolve_roles(), {"actor": HAIKU, "critic": HAIKU})
            # One flag moves both: one model for both is the default arrangement,
            # and nobody asked for a split here.
            self.assertEqual(env.resolve_roles("opus"), {"actor": OPUS, "critic": OPUS})
            self.assertEqual(env.resolve_roles("opus", "haiku"), {"actor": OPUS, "critic": HAIKU})

    def test_a_deliberate_critic_split_survives_a_new_actor(self):
        with patch.dict(os.environ, {"S2P_MODEL": HAIKU, "S2P_CRITIC_MODEL": OPUS}):
            self.assertEqual(env.resolve_roles("sonnet"), {"actor": SONNET, "critic": OPUS})

    def test_a_missing_key_is_reported_by_variable_name_and_never_by_value(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-realkeyshapedthing"}):
            self.assertEqual(env.key_missing("openai:gpt-4o"), "")
        with patch.dict(os.environ, {}, clear=True):
            self.assertIn("OPENAI_API_KEY", env.key_missing("openai:gpt-4o"))
        with patch.dict(os.environ, {"OPENAI_API_KEY": "wrong-paste-entirely"}):
            complaint = env.key_missing("openai:gpt-4o")
        self.assertIn("OPENAI_API_KEY", complaint)
        self.assertNotIn("wrong-paste-entirely", complaint)  # masked, per the secrets rule

    def test_a_provider_this_file_has_no_advice_for_is_not_judged_here(self):
        """Silence means "no advice", not "approved" — llm.check_model asks the provider."""
        self.assertEqual(env.key_missing("ibm:granite-3"), "")       # not in PROVIDER_KEYS
        self.assertEqual(env.key_missing("ollama:llama3.2"), "")     # local, needs no key
        with patch.dict(os.environ, {"S2P_MODEL": "ibm:granite-3"}):
            os.environ.pop("S2P_CRITIC_MODEL", None)
            # Not knowing Watsonx's key variable must not stop the run…
            self.assertNotIn("IBM", "".join(env.required()))
            # …but it is said out loud rather than passed over in silence.
            self.assertEqual(env.unverifiable()["actor"], "ibm:granite-3")


class ConfiguredHarness(CliHarness):
    """The scripted model of test_cli, plus a record of the names it was asked for."""

    #: Shaped like the real thing, worth nothing: the preflight builds a client
    #: (no network), so a machine with no keys must still run these tests.
    FAKE_KEYS = {"ANTHROPIC_API_KEY": "sk-ant-test", "OPENAI_API_KEY": "sk-test",
                 "GROQ_API_KEY": "gsk_test"}

    def setUp(self):
        super().setUp()
        # A critic split in the developer's own .env must not decide a test.
        split = patch.dict(os.environ, self.FAKE_KEYS)
        split.start()
        os.environ.pop("S2P_CRITIC_MODEL", None)
        self.addCleanup(split.stop)

    @contextmanager
    def recording(self, drafts=None, reviews=None):
        """Yield the list of model names make_model is asked for, in order."""
        asked: list[str | None] = []
        with self.replies(drafts or [ConversionResult(code=GOLDEN)], reviews or [PASS]) as scripted:
            def make_model(name=None, *, for_critic=False):
                asked.append(name)
                return scripted.return_value
            with patch.object(graph, "make_model", make_model):
                yield asked


class ContextSchemaTests(ConfiguredHarness):
    """The graph reads these settings from the run's context, not from its state."""

    def test_an_invoke_without_context_still_runs_on_the_environment(self):
        """LangGraph leaves runtime.context as None; settings() supplies the defaults."""
        self.assertEqual(graph.settings(None), graph.RunSettings())
        self.assertEqual(graph.settings(Mock(context=None)), graph.RunSettings())
        self.assertEqual(graph.settings(Mock(context={"model": "opus"})).model, "opus")

    def test_the_context_model_reaches_the_provider_and_is_recorded_in_the_state(self):
        with self.recording() as asked:
            final = graph.build_graph().invoke(
                {"source_path": str(SOURCE)},
                context=graph.RunSettings(model="opus", critic_model="haiku"))
        self.assertEqual(asked, [OPUS, HAIKU])  # the actor, then the critic
        self.assertEqual(final["models"], {"actor": OPUS, "critic": HAIKU})

    def test_a_plain_invoke_is_unchanged_and_still_records_what_it_used(self):
        with patch.dict(os.environ, {"S2P_MODEL": HAIKU}), self.recording() as asked:
            final = graph.build_graph().invoke({"source_path": str(SOURCE)})
        self.assertEqual(asked, [HAIKU, HAIKU])
        self.assertEqual(final["report"].status, "passed")

    def test_a_cap_in_the_context_beats_one_restored_from_the_thread(self):
        """Turn 2 obeys the flag typed on turn 2, not the number turn 1 recorded."""
        db = Path(self.tmp.name) / "threads.sqlite"
        broken = ConversionResult(code=GOLDEN.replace("await this.usernameInput.fill",
                                                      "this.usernameInput.fill"))
        with self.replies([ConversionResult(code=GOLDEN)], [PASS]):
            first, _, _ = self.run_cli("convert", str(SOURCE), "--thread", "t",
                                       "--db", str(db), "--no-diff")
        self.assertEqual(first, 0)  # turn 1 ran, and recorded, the default cap of 3
        with self.replies([broken] * 3, [REVISE] * 3):
            code, _, err = self.run_cli("convert", "--thread", "t", "--db", str(db),
                                        "--max-attempts", "1", "--no-diff")
        self.assertEqual(code, 1)
        self.assertIn("(1/1 attempts)", err)


class CliConfigTests(ConfiguredHarness):
    def test_the_model_flag_shows_on_screen_and_in_the_trace_configuration(self):
        with self.recording() as asked:
            code, _, err = self.run_cli("convert", str(SOURCE), "--model", "opus", "--no-diff")
        self.assertEqual((code, asked), (0, [OPUS, OPUS]))
        self.assertIn(f"Models: actor {OPUS}", err)
        # The same choice reaches the trace, where a run can be filtered on it.
        config = cli.run_config({"actor": OPUS, "critic": HAIKU}, 1)
        self.assertIn("model:claude-opus-5", config["tags"])
        self.assertIn("attempts:1", config["tags"])
        self.assertEqual(config["metadata"],
                         {"actor_model": OPUS, "critic_model": HAIKU, "max_attempts": 1})

    def test_an_unusable_model_is_refused_before_any_provider_call(self):
        for argv, expected in ((["--model", "gpt-4o"], "unknown model"),
                               (["--model", "bogus:thing"], "Invalid value: bogus:thing")):
            with self.subTest(argv=argv), patch.object(graph, "make_model") as never:
                code, out, err = self.run_cli("convert", str(SOURCE), *argv)
            self.assertEqual((code, out), (2, ""))
            # Click wraps its error box, so compare on collapsed whitespace.
            self.assertIn(expected, " ".join(err.split()))
            never.assert_not_called()

    def test_a_named_model_with_no_key_fails_fast_and_names_the_variable(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(graph, "make_model") as never:
            code, _, err = self.run_cli("convert", str(SOURCE), "--model", "openai:gpt-4o")
        self.assertEqual(code, 2)
        self.assertIn("OPENAI_API_KEY", err)
        never.assert_not_called()

    def test_json_is_one_document_on_stdout_with_the_converted_code_inside(self):
        code, out, err = self.convert("--json", "--no-diff")
        document = json.loads(out)  # the whole of stdout, or this raises
        self.assertEqual((code, document["exit_code"]), (0, 0))
        self.assertEqual(document["schema"], cli.REPORT_SCHEMA)
        self.assertEqual(document["status"], "passed")
        self.assertEqual(document["report"]["result"]["code"], GOLDEN)
        self.assertEqual(document["max_attempts"], 3)
        self.assertTrue(document["models"]["actor"])
        self.assertEqual([report["gate"] for report in document["report"]["validation"]],
                         ["compile", "residue", "lint", "parity"])
        self.assertIn("Conversion: passed", err)  # the human half is unchanged

    def test_json_names_the_file_it_wrote_instead_of_carrying_the_code_twice(self):
        target = Path(self.tmp.name) / "LoginPage.ts"
        code, out, _ = self.convert("--json", "--no-diff", "--out", str(target))
        document = json.loads(out)
        self.assertEqual((code, document["output"]), (0, str(target)))
        self.assertEqual(target.read_text(), GOLDEN)

    def test_a_refusal_is_still_a_json_document(self):
        """Machine-readable has to include the outcomes a caller likes least."""
        source = Path(self.tmp.name) / "wdio.ts"
        source.write_text('import { browser } from "webdriverio";')
        with patch.object(graph, "make_model") as never:
            code, out, _ = self.run_cli("convert", str(source), "--json")
        document = json.loads(out)
        self.assertEqual((code, document["exit_code"], document["status"]), (2, 2, "refused"))
        self.assertIsNone(document["report"])
        self.assertIn("webdriverio", document["refusal"])
        never.assert_not_called()


class OtherProviderTests(ConfiguredHarness):
    """OpenAI and the rest: nothing here may assume the provider is Anthropic."""

    def test_a_usable_model_passes_the_preflight_without_touching_the_network(self):
        for name in ("anthropic:claude-sonnet-5", "openai:gpt-5.4"):
            with self.subTest(name=name):
                self.assertEqual(llm.check_model(name), "")

    def test_a_missing_integration_package_says_which_one_to_add(self):
        """The common first failure on a new provider — and it is fixable in one line."""
        problem = llm.check_model("groq:llama-3.3-70b-versatile")
        self.assertIn("langchain-groq", problem)
        self.assertIn("uv add langchain-groq", problem)

    def test_a_provider_that_does_not_exist_says_what_a_model_string_looks_like(self):
        """LangChain's own list of 28 providers is accurate and unreadable in a box."""
        problem = llm.check_model("bogus:whatever")
        self.assertIn("provider:model", problem)
        self.assertNotIn("Supported providers", problem)
        self.assertLess(len(problem), 300)

    def test_the_critic_asks_for_json_schema_only_where_it_is_supported(self):
        self.assertEqual(llm.structured_kwargs("openai:gpt-5.4", for_critic=True),
                         {"include_raw": True, "method": "json_schema"})
        self.assertEqual(llm.structured_kwargs("anthropic:claude-sonnet-5", for_critic=True),
                         {"include_raw": True, "method": "json_schema"})
        # An unlisted provider gets the default path every integration implements.
        self.assertEqual(llm.structured_kwargs("groq:llama-3.3-70b-versatile", for_critic=True),
                         {"include_raw": True})
        # The actor never asks for it, on any provider.
        self.assertEqual(llm.structured_kwargs("openai:gpt-5.4"), {"include_raw": True})

    def methods_used(self, model_name):
        """Run one conversion on this model and report how each schema was requested."""
        with self.replies([ConversionResult(code=GOLDEN)], [PASS]) as scripted:
            graph.build_graph().invoke({"source_path": str(SOURCE)},
                                       context=graph.RunSettings(model=model_name))
            return {call.args[0].__name__: call.kwargs
                    for call in scripted.return_value.with_structured_output.call_args_list}

    def test_the_same_graph_runs_on_any_provider_and_adapts_how_it_asks(self):
        openai = self.methods_used("openai:gpt-5.4")
        self.assertEqual(openai["ConversionResult"], {"include_raw": True})
        self.assertEqual(openai["Critique"], {"include_raw": True, "method": "json_schema"})
        # Same nodes, same edges, one different word in the request.
        groq = self.methods_used("groq:llama-3.3-70b-versatile")
        self.assertEqual(groq["Critique"], {"include_raw": True})


if __name__ == "__main__":
    unittest.main()

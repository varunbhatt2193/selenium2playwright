"""Step 7.1 — threads: does turn 2 remember turn 1, and does the memory survive SQLite?

Offline. The model is scripted (same fake as tests/test_reflection.py) but the
checkpointer, the SQLite file, and every validator are real, because the thing
under test is exactly whether state written to disk comes back usable.
"""

import io
import os
import sqlite3
import unittest
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda

from selenium2playwright import graph, memory
from selenium2playwright.classify import Classification
from selenium2playwright.prompts import build_critic_prompt, build_prompt, format_conventions
from selenium2playwright.reflection import refinement_feedback
from selenium2playwright.schemas import (ConversionReport, ConversionResult, Critique, Finding,
                                         ValidationReport)

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "samples/selenium-suite/pages/LoginPage.ts"
GOLDEN = (ROOT / "samples/playwright-golden/pages/LoginPage.ts").read_text()
BROKEN = GOLDEN.replace("await this.usernameInput.fill", "this.usernameInput.fill")
TESTID = GOLDEN.replace('getByLabel("Username")', 'getByTestId("username")')
PASS = Critique(verdict="pass", fixes=[])
REVISE = Critique(verdict="revise", fixes=["Await usernameInput.fill() to prevent a floating Promise."])
DATA_TESTID = "use getByTestId locators everywhere"


@dataclass(frozen=True)
class NotInTheAllowlist:
    """Encodes like Classification does, but memory.CHECKPOINT_TYPES never names it."""

    value: str


class ScriptedModel(unittest.TestCase):
    """Shared harness: queue up drafts and reviews, capture the prompts sent."""

    def setUp(self):
        env = patch.dict(os.environ, {"LANGSMITH_TRACING": "false", "LANGCHAIN_TRACING_V2": "false"})
        env.start()
        self.addCleanup(env.stop)
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "nested" / "threads.sqlite"

    def replies(self, drafts, reviews):
        queues = {ConversionResult: iter(drafts), Critique: iter(reviews)}
        self.conversion_prompts, self.critic_prompts = [], []

        def structured(schema, **kwargs):
            def respond(prompt):
                messages = prompt if isinstance(prompt, list) else prompt.to_messages()
                bucket = self.conversion_prompts if schema is ConversionResult else self.critic_prompts
                bucket.append("\n\n".join(str(m.content) for m in messages))
                value = next(queues[schema])
                if isinstance(value, Exception):
                    raise value
                return {"parsed": value, "parsing_error": None,
                        "raw": AIMessage(content="", usage_metadata={
                            "input_tokens": 10, "output_tokens": 5, "total_tokens": 15})}
            return RunnableLambda(respond)

        model = Mock()
        model.with_structured_output.side_effect = structured
        return patch.object(graph, "make_model", return_value=model)


class CheckpointRoundTripTests(ScriptedModel):
    def test_every_state_type_is_allowlisted_and_rebuilds_itself(self):
        """The guard promised in memory.CHECKPOINT_TYPES: add a type, add it there.

        The serializer is locked to the strict set (see memory.strict_serializer),
        so an object of a type that is not listed there cannot come back — which
        the next test proves. Everything the state really holds must therefore
        survive this round trip, or a resumed thread will crash on read.
        """
        finding = Finding(gate="compile", file="a.ts", line=3, code="TS2551", message="typo")
        report = ConversionReport(
            status="needs-review", attempts=2, reason="findings remain",
            result=ConversionResult(code=GOLDEN, notes=["n"], todos=["TODO(review): check"]),
            validation=[ValidationReport(gate="compile", passed=False, findings=[finding], tool_output="raw")],
            critique=REVISE, errors=["boom"])
        stored = {"classification": Classification("selenium", "mocha", "typescript", True, "ok"),
                  "report": report, "baseline": report.result, "conventions": [DATA_TESTID]}

        with patch.dict(os.environ, {"LANGGRAPH_STRICT_MSGPACK": "true"}):
            with memory.open_checkpointer(self.db) as checkpointer:
                compiled = graph.build_graph(checkpointer)
                config = memory.thread_config("t1")
                compiled.update_state(config, stored)
                restored = memory.thread_state(compiled, "t1")

        self.assertEqual(restored["classification"], stored["classification"])
        self.assertIsInstance(restored["report"], ConversionReport)
        self.assertEqual(restored["report"].validation[0].findings[0].render(), finding.render())
        self.assertEqual(restored["report"].critique, REVISE)
        self.assertEqual(restored["baseline"].code, GOLDEN)
        self.assertEqual(restored["conventions"], [DATA_TESTID])

    def test_a_type_outside_the_allowlist_comes_back_as_plain_data(self):
        """The lockdown is real, not decorative: reading is data, not class loading.

        Note how it fails — quietly. An unlisted type is not an error on read;
        it degrades to the dict it was encoded from, and the crash arrives later
        wherever something says `.result` on it. That is the whole reason the
        test above enumerates every type the state holds.
        """
        with memory.open_checkpointer(self.db) as checkpointer:
            compiled = graph.build_graph(checkpointer)
            config = memory.thread_config("t1")
            compiled.update_state(config, {"classification": NotInTheAllowlist("x")})
            restored = memory.thread_state(compiled, "t1")
        self.assertEqual(restored["classification"], {"value": "x"})  # a dict, not the class

    def test_checkpointer_creates_the_directory_and_lists_threads(self):
        self.assertEqual(memory.list_threads(self.db), [])  # missing file, not an error
        with memory.open_checkpointer(self.db) as checkpointer:
            compiled = graph.build_graph(checkpointer)
            for thread_id in ("beta", "alpha"):
                compiled.update_state(memory.thread_config(thread_id), {"turn": 1})
        self.assertTrue(self.db.exists())
        self.assertEqual(memory.list_threads(self.db), ["alpha", "beta"])

    def test_unrelated_sqlite_file_is_not_mistaken_for_a_thread_database(self):
        sqlite3.connect(self.db.parent.mkdir(parents=True, exist_ok=True) or str(self.db)).close()
        self.assertEqual(memory.list_threads(self.db), [])

    def test_stateless_graph_keeps_no_memory(self):
        """The default is unchanged: no checkpointer, no thread, nothing written."""
        with self.replies([ConversionResult(code=GOLDEN)], [PASS]):
            final = graph.build_graph().invoke({"source_path": str(SOURCE)})
        self.assertEqual(final["turn"], 1)
        self.assertEqual(final["conventions"], [])
        self.assertIsNone(final["baseline"])
        self.assertFalse(self.db.exists())


class TwoTurnRefinementTests(ScriptedModel):
    def turns(self, compiled, *calls):
        """Invoke one thread repeatedly, the way two CLI runs would."""
        config = memory.thread_config("login")
        return [compiled.invoke(payload, config=config) for payload in calls]

    def test_second_turn_refines_the_first_without_being_given_the_file_again(self):
        with self.replies([ConversionResult(code=GOLDEN), ConversionResult(code=TESTID)], [PASS, PASS]):
            with memory.open_checkpointer(self.db) as checkpointer:
                compiled = graph.build_graph(checkpointer)
                first, second = self.turns(compiled,
                                           {"source_path": str(SOURCE)},
                                           {"refinement": DATA_TESTID})

        # Turn 1: an ordinary stateless-looking run.
        self.assertEqual((first["turn"], first["report"].status), (1, "passed"))
        self.assertEqual(first["conventions"], [])
        self.assertNotIn("STANDING INSTRUCTIONS", self.conversion_prompts[0])

        # Turn 2: source restored from the thread, instruction remembered, and the
        # previous conversion handed to the model as the starting point.
        self.assertEqual((second["turn"], second["report"].status), (2, "passed"))
        self.assertEqual(second["source"], SOURCE.read_text())
        self.assertEqual(second["conventions"], [DATA_TESTID])
        self.assertEqual(second["refinement"], "")  # folded into conventions, not left pending
        self.assertEqual(second["baseline"].code, GOLDEN)
        self.assertEqual(second["iteration"], 1)  # a new turn gets a fresh attempt budget
        prompt = self.conversion_prompts[1]
        self.assertIn(DATA_TESTID, prompt)
        self.assertIn("<previous_conversion>", prompt)
        # embedded as JSON, so match on a fragment that carries no quotes of its own
        self.assertIn("export class LoginPage", prompt)
        self.assertIn("getByLabel", prompt)
        self.assertIn(DATA_TESTID, self.critic_prompts[1])  # the reviewer sees it too
        self.assertEqual(second["report"].result.code, TESTID)

    def test_instructions_accumulate_and_survive_a_repair_lap(self):
        """Turn 3 keeps turn 2's rule, and the repair prompt still carries both."""
        naming = "suffix page object classes with Page"
        drafts = [ConversionResult(code=GOLDEN), ConversionResult(code=TESTID),
                  ConversionResult(code=BROKEN), ConversionResult(code=TESTID)]
        with self.replies(drafts, [PASS, PASS, REVISE, PASS]):
            with memory.open_checkpointer(self.db) as checkpointer:
                compiled = graph.build_graph(checkpointer)
                turns = self.turns(compiled, {"source_path": str(SOURCE)},
                                   {"refinement": DATA_TESTID}, {"refinement": naming})

        self.assertEqual([t["turn"] for t in turns], [1, 2, 3])
        self.assertEqual(turns[2]["conventions"], [DATA_TESTID, naming])
        self.assertEqual(turns[2]["iteration"], 2)  # turn 3 needed one repair
        repair = self.conversion_prompts[3]
        self.assertIn("<validation_reports>", repair)  # it is a repair, not a refinement
        self.assertNotIn("<previous_conversion>\n{", repair.split("<validation_reports>")[1])
        for instruction in (DATA_TESTID, naming):
            self.assertIn(instruction, repair)  # ...and both rules are still standing

    def test_repeated_instruction_is_not_duplicated(self):
        with self.replies([ConversionResult(code=GOLDEN)] * 3, [PASS] * 3):
            with memory.open_checkpointer(self.db) as checkpointer:
                compiled = graph.build_graph(checkpointer)
                turns = self.turns(compiled, {"source_path": str(SOURCE)},
                                   {"refinement": DATA_TESTID}, {"refinement": f"  {DATA_TESTID}  "})
        self.assertEqual(turns[2]["conventions"], [DATA_TESTID])

    def test_a_separate_thread_starts_clean(self):
        """Threads are isolated: 7.1 is short-term memory, not cross-thread (that is 7.3)."""
        with self.replies([ConversionResult(code=GOLDEN)] * 3, [PASS] * 3):
            with memory.open_checkpointer(self.db) as checkpointer:
                compiled = graph.build_graph(checkpointer)
                self.turns(compiled, {"source_path": str(SOURCE)}, {"refinement": DATA_TESTID})
                other = compiled.invoke({"source_path": str(SOURCE)},
                                        config=memory.thread_config("fresh"))
        self.assertEqual((other["turn"], other["conventions"]), (1, []))
        self.assertIsNone(other["baseline"])


class PromptShapeTests(unittest.TestCase):
    def test_conventions_render_numbered_after_the_cached_prefix(self):
        self.assertEqual(format_conventions([]), "")
        block = format_conventions(["first rule", "second rule"])
        self.assertIn("1. first rule\n2. second rule", block)
        messages = build_prompt(revision="REPAIR", conventions=block).format_messages(
            file_path="a.ts", source="src", context="")
        kinds = [m.type for m in messages]
        self.assertEqual(kinds, ["system", "human", "human", "human"])
        self.assertIn("playbook", messages[0].content.lower())  # still first, still cacheable
        self.assertEqual(messages[2].content, block)  # standing rules...
        self.assertEqual(messages[3].content, "REPAIR")  # ...then this attempt's feedback

    def test_prompts_are_byte_identical_to_phase_6_when_there_are_no_conventions(self):
        """Nothing about a thread-less run changed, so the eval baselines still hold."""
        args = dict(file_path="a.ts", source="src", context="")
        self.assertEqual([m.content for m in build_prompt().format_messages(**args)],
                         [m.content for m in build_prompt(conventions="").format_messages(**args)])
        self.assertEqual(len(build_critic_prompt().format_messages(conversion="c", validation="v", **args)), 2)
        self.assertEqual(len(build_critic_prompt("rules").format_messages(
            conversion="c", validation="v", **args)), 3)

    def test_refinement_feedback_carries_the_previous_file_and_forbids_a_patch(self):
        text = refinement_feedback(ConversionResult(code=GOLDEN, notes=["kept the flash locator"]))
        self.assertIn("<previous_conversion>", text)
        self.assertIn("kept the flash locator", text)
        self.assertIn("complete ConversionResult, not a patch", text)


class CommandLineTests(ScriptedModel):
    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = graph.main([*argv, "--db", str(self.db)])
        return code, out.getvalue(), err.getvalue()

    def test_two_cli_turns_reuse_the_thread_and_its_output_path(self):
        destination = Path(self.tmp.name) / "out" / "LoginPage.ts"
        with self.replies([ConversionResult(code=GOLDEN), ConversionResult(code=TESTID)], [PASS, PASS]):
            first = self.run_cli(str(SOURCE), "--thread", "login", "--out", str(destination))
            second = self.run_cli("--thread", "login", "--refine", DATA_TESTID)
        self.assertEqual((first[0], second[0]), (0, 0))
        self.assertIn("turn 1", first[2])
        self.assertIn("turn 2", second[2])
        self.assertIn(f"standing instruction 1: {DATA_TESTID}", second[2])
        self.assertIn("continuing from the previous turn's conversion", second[2])
        self.assertEqual(destination.read_text(), TESTID)  # remembered --out, rewritten in place

    def test_a_failed_refinement_turn_says_the_previous_one_still_stands(self):
        destination = Path(self.tmp.name) / "LoginPage.ts"
        with self.replies([ConversionResult(code=GOLDEN), RuntimeError("provider down")], [PASS]):
            self.run_cli(str(SOURCE), "--thread", "login", "--out", str(destination))
            code, _, err = self.run_cli("--thread", "login", "--refine", DATA_TESTID)
        self.assertEqual(code, 1)
        self.assertIn("provider down", err)
        self.assertIn("No converted code was produced", err)
        self.assertIn("previous turn's conversion on this thread is unchanged", err)
        self.assertEqual(destination.read_text(), GOLDEN)  # turn 1's file untouched

    def test_resuming_needs_a_thread_and_a_thread_needs_a_first_turn(self):
        with self.assertRaises(SystemExit):
            self.run_cli("--refine", DATA_TESTID)
        with self.assertRaises(SystemExit):
            self.run_cli("--thread", "never-run", "--refine", DATA_TESTID)

    def test_list_threads_prints_saved_ids(self):
        with self.replies([ConversionResult(code=GOLDEN)], [PASS]):
            self.run_cli(str(SOURCE), "--thread", "login")
        self.assertEqual(self.run_cli("--list-threads")[1].split(), ["login"])


if __name__ == "__main__":
    unittest.main()

"""Step 7.3 — long-term memory: is a preference taught once recalled by itself later?

Offline. The chat model is scripted (same fake as tests/test_memory.py) and the
embeddings model is a tiny deterministic one defined here, so nothing in this
file touches a network. The store, the SQLite file, its vector index and every
validator are real, because what is under test is exactly whether a preference
written in one conversation comes back, correctly ranked, in another.

The fake embeddings are a bag of hashed words: identical text always gives an
identical vector, and texts sharing words score higher than texts that do not.
That is all recall needs to be exercised. What the real model's scores actually
look like is a separate, live question, answered by scripts/calibrate_recall.py.
"""

import hashlib
import io
import math
import os
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from langchain_core.embeddings import Embeddings
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda

from selenium2playwright import cli, graph, memory, store
from selenium2playwright.classify import classify
from selenium2playwright.prompts import build_prompt, format_remembered
from selenium2playwright.schemas import ConversionResult, Critique

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "samples/selenium-suite/pages/LoginPage.ts"
SPEC = ROOT / "samples/selenium-suite/tests/upload.spec.ts"
UNSUPPORTED = ROOT / "samples/playwright.config.ts"  # already Playwright: refused, never converted
GOLDEN = (ROOT / "samples/playwright-golden/pages/LoginPage.ts").read_text()
PASS = Critique(verdict="pass", fixes=[])

TESTIDS = "Use getByTestId for form fields, the login username and password inputs included"
OFFTOPIC = "Our CI publishes the HTML report to S3 after every nightly run"


class HashEmbeddings(Embeddings):
    """Deterministic offline embeddings: one axis per hashed word, then normalised.

    Cosine similarity then reduces to "what fraction of words do these two share",
    which is enough to rank memories against a file profile without a network.
    """

    dims = 64

    def __init__(self):
        self.calls = 0

    def _vector(self, text: str) -> list[float]:
        weights = [0.0] * self.dims
        for word in text.lower().replace(",", " ").replace(":", " ").split():
            weights[int(hashlib.sha1(word.encode()).hexdigest(), 16) % self.dims] += 1.0
        length = math.sqrt(sum(w * w for w in weights)) or 1.0
        return [w / length for w in weights]

    def embed_documents(self, texts):
        self.calls += 1
        return [self._vector(t) for t in texts]

    def embed_query(self, text):
        self.calls += 1
        return self._vector(text)


class StoreHarness(unittest.TestCase):
    """A real SQLite store with fake embeddings, plus the scripted chat model."""

    def setUp(self):
        tracing = patch.dict(os.environ, {"LANGSMITH_TRACING": "false", "LANGCHAIN_TRACING_V2": "false"})
        tracing.start()
        self.addCleanup(tracing.stop)
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "nested" / "memories.sqlite"
        self.threads = Path(self.tmp.name) / "threads.sqlite"
        self.embeddings = HashEmbeddings()

    def open(self, embeddings="fake"):
        model = self.embeddings if embeddings == "fake" else embeddings
        return store.open_store(self.db, model, HashEmbeddings.dims if model else None)

    def replies(self, drafts, reviews):
        queues = {ConversionResult: iter(drafts), Critique: iter(reviews)}
        self.conversion_prompts, self.critic_prompts = [], []

        def structured(schema, **kwargs):
            def respond(prompt):
                messages = prompt if isinstance(prompt, list) else prompt.to_messages()
                bucket = self.conversion_prompts if schema is ConversionResult else self.critic_prompts
                bucket.append("\n\n".join(str(m.content) for m in messages))
                return {"parsed": next(queues[schema]), "parsing_error": None,
                        "raw": AIMessage(content="", usage_metadata={
                            "input_tokens": 10, "output_tokens": 5, "total_tokens": 15})}
            return RunnableLambda(respond)

        model = Mock()
        model.with_structured_output.side_effect = structured
        return patch.object(graph, "make_model", return_value=model)


class MemoryFileTests(StoreHarness):
    def test_writing_reading_and_forgetting_one_preference(self):
        with self.open() as memories:
            saved = store.remember(memories, TESTIDS, "varun")
            self.assertEqual([m.text for m in store.memories(memories, "varun")], [TESTIDS])
            self.assertTrue(store.forget(memories, saved.key, "varun"))
            self.assertEqual(store.memories(memories, "varun"), [])
            self.assertFalse(store.forget(memories, saved.key, "varun"))  # says so, never silent
        self.assertTrue(self.db.exists())  # created the nested directory too

    def test_the_same_rule_taught_twice_is_one_memory(self):
        """The key is a hash of the text, so repetition updates instead of piling up."""
        with self.open() as memories:
            first = store.remember(memories, TESTIDS, "varun")
            second = store.remember(memories, "  Use getByTestId FOR form fields, the login "
                                              "username and password inputs included ", "varun")
            self.assertEqual(first.key, second.key)
            self.assertEqual(len(store.memories(memories, "varun")), 1)

    def test_one_user_never_sees_another_users_memories(self):
        with self.open() as memories:
            store.remember(memories, TESTIDS, "varun")
            self.assertEqual(store.memories(memories, "someone-else"), [])

    def test_an_empty_memory_set_costs_no_embedding_call(self):
        """The common case — nobody has taught it anything — must not hit the API."""
        with self.open() as memories:
            before = self.embeddings.calls
            self.assertEqual(store.recall(memories, "any query at all", "varun"), [])
            self.assertEqual(self.embeddings.calls, before)

    def test_switching_embeddings_model_is_refused_not_silently_wrong(self):
        """Vectors from two models are not comparable, and nothing about it looks broken."""
        class OtherModel(HashEmbeddings):
            model = "other:embed-1"

        with self.open() as memories:
            store.remember(memories, TESTIDS, "varun")
        with self.assertRaises(ValueError) as caught:
            with self.open(OtherModel()):
                pass
        self.assertIn("not comparable", str(caught.exception))

    def test_only_the_text_is_embedded_not_where_it_was_written(self):
        """Provenance must not change a memory's vector — see open_store's index config.

        The same sentence, filed on two different days from two different files,
        has to rank identically. If anything but `text` reaches the embeddings
        model, it does not, and nothing about it looks broken.
        """
        scores = []
        for source in ("pages/LoginPage.ts", "an entirely different sentence about uploads"):
            self.db.unlink(missing_ok=True)
            with self.open() as memories:
                store.remember(memories, TESTIDS, "varun", source=source)
                found = store.recall(memories, "page object login username password", "varun",
                                     min_score=0.0)
                scores.append(found[0].score)
        self.assertEqual(scores[0], scores[1])

    def test_bookkeeping_is_never_recalled_into_a_prompt(self):
        with self.open() as memories:
            store.remember(memories, TESTIDS, "varun")
            found = store.recall(memories, "embeddings openai name model", "varun", min_score=0.0)
            self.assertEqual([m.text for m in found], [TESTIDS])


class RecallTests(StoreHarness):
    def profile(self, path):
        source = path.read_text()
        return store.recall_query(str(path), classify(source, str(path)), source)

    def test_the_memories_come_back_ranked_closest_first(self):
        """Ranking is the point; the threshold is a separate, measured decision."""
        with self.open() as memories:
            for text in (OFFTOPIC, TESTIDS):
                store.remember(memories, text, "varun")
            found = store.recall(memories, self.profile(SOURCE), "varun", min_score=0.0)
        self.assertEqual(found[0].text, TESTIDS)  # a login page object
        self.assertEqual(found[-1].text, OFFTOPIC)  # nothing to do with this file
        self.assertGreater(found[0].score, found[-1].score)

    def test_a_threshold_between_them_keeps_the_unrelated_one_out(self):
        with self.open() as memories:
            for text in (TESTIDS, OFFTOPIC):
                store.remember(memories, text, "varun")
            ranked = store.recall(memories, self.profile(SOURCE), "varun", min_score=0.0)
            gate = (ranked[0].score + ranked[-1].score) / 2
            found = store.recall(memories, self.profile(SOURCE), "varun", min_score=gate)
        self.assertEqual([m.text for m in found], [TESTIDS])

    def test_the_threshold_can_reject_everything(self):
        with self.open() as memories:
            store.remember(memories, OFFTOPIC, "varun")
            self.assertEqual(store.recall(memories, self.profile(SOURCE), "varun", min_score=0.9), [])

    def test_no_more_than_the_limit_ever_reaches_a_prompt(self):
        with self.open() as memories:
            for n in range(6):
                store.remember(memories, f"{TESTIDS} variation {n}", "varun")
            found = store.recall(memories, self.profile(SOURCE), "varun", min_score=0.0)
        self.assertEqual(len(found), store.RECALL_LIMIT)

    def test_a_preference_already_standing_on_the_thread_is_not_sent_twice(self):
        with self.open() as memories:
            store.remember(memories, TESTIDS, "varun")
            found = store.recall(memories, self.profile(SOURCE), "varun",
                                 min_score=0.0, exclude=["  use getbytestid FOR form fields, the "
                                                         "login username and password inputs included"])
        self.assertEqual(found, [])

    def test_without_embeddings_recall_lists_the_recent_ones_unranked(self):
        """The honest degraded mode: still useful, and visibly not ranked."""
        with self.open(embeddings=None) as memories:
            for text in (OFFTOPIC, TESTIDS):
                store.remember(memories, text, "varun")
            found = store.recall(memories, self.profile(SOURCE), "varun")
        self.assertEqual(len(found), 2)
        self.assertTrue(all(m.score is None for m in found))

    def test_the_store_parameter_keeps_the_spelling_langgraph_injects_on(self):
        """A silent trap: LangGraph matches the annotation text, not the type.

        "BaseStore | None" is not on its list, and `from __future__ import
        annotations` makes every annotation a string — so modernising this one
        line would leave the node running with store=None forever, with no
        error anywhere. See the comment above graph.recall.
        """
        import inspect
        annotation = inspect.signature(graph.recall).parameters["store"].annotation
        self.assertIn(annotation, ("BaseStore", "Optional[BaseStore]"))

    def test_the_query_is_a_profile_of_the_file_not_the_file(self):
        page, spec = self.profile(SOURCE), self.profile(SPEC)
        self.assertIn("page object LoginPage.ts", page)
        self.assertIn("classes: login page", page)  # camelCase split into words
        self.assertIn("username", page)
        self.assertNotIn("await this.driver", page)  # the code itself is not the query
        self.assertIn("mocha test spec upload.spec.ts", spec)
        self.assertIn("uploads a created file", spec)  # the test titles carry the meaning


class GraphRecallTests(StoreHarness):
    def run_graph(self, payload, thread=None, use_store=True):
        with (self.open() if use_store else _NoStore()) as memories:
            with memory.open_checkpointer(self.threads) as checkpointer:
                compiled = graph.build_graph(checkpointer if thread else None,
                                             memories if use_store else None)
                config = memory.thread_config(thread) if thread else {}
                return compiled.invoke(payload, config=config)

    def test_a_preference_taught_in_one_thread_applies_in_a_fresh_one(self):
        """The whole point of step 7.3, end to end and through SQLite.

        Thread 1 is told once, with --remember. Thread 2 is a different
        conversation about a different file and is told nothing at all.
        """
        with self.replies([ConversionResult(code=GOLDEN)] * 2, [PASS] * 2):
            first = self.run_graph({"source_path": str(SPEC), "remember": TESTIDS,
                                    "user_id": "varun"}, thread="one")
            self.assertEqual([m.text for m in first["recalled"]], [TESTIDS])
            second = self.run_graph({"source_path": str(SOURCE), "user_id": "varun"}, thread="two")
        self.assertEqual([m.text for m in second["recalled"]], [TESTIDS])
        self.assertIn("REMEMBERED PREFERENCES", self.conversion_prompts[1])
        self.assertIn(TESTIDS, self.conversion_prompts[1])
        self.assertEqual(second["memory_count"], 1)

    def test_the_recalled_preference_reaches_the_critic_as_well(self):
        """Or the reviewer would flag the user's own convention as a defect."""
        with self.replies([ConversionResult(code=GOLDEN)], [PASS]):
            self.run_graph({"source_path": str(SOURCE), "remember": TESTIDS, "user_id": "varun"})
        self.assertIn(TESTIDS, self.critic_prompts[0])

    def test_a_preference_given_now_is_applied_even_if_it_does_not_match_the_file(self):
        """The user just said it. It is not put to a similarity vote this run."""
        with self.replies([ConversionResult(code=GOLDEN)], [PASS]):
            final = self.run_graph({"source_path": str(SOURCE), "remember": OFFTOPIC,
                                    "user_id": "varun"})
        self.assertEqual([m.text for m in final["recalled"]], [OFFTOPIC])
        self.assertIsNone(final["recalled"][0].score)  # sent because it was given, not because it scored
        self.assertEqual(final["remember"], "")  # consumed, so a later turn cannot re-file it

    def test_a_recalled_preference_survives_a_repair_lap(self):
        broken = ConversionResult(code=GOLDEN.replace("await this.usernameInput.fill",
                                                      "this.usernameInput.fill"))
        with self.replies([broken, ConversionResult(code=GOLDEN)],
                          [Critique(verdict="revise", fixes=["Await the fill."]), PASS]):
            self.run_graph({"source_path": str(SOURCE), "remember": TESTIDS, "user_id": "varun"})
        self.assertEqual(len(self.conversion_prompts), 2)
        self.assertIn(TESTIDS, self.conversion_prompts[1])

    def test_no_store_means_the_prompt_is_byte_identical_to_phase_6(self):
        with self.replies([ConversionResult(code=GOLDEN)], [PASS]):
            final = self.run_graph({"source_path": str(SOURCE)}, use_store=False)
        self.assertEqual(final["recalled"], [])
        self.assertNotIn("REMEMBERED PREFERENCES", self.conversion_prompts[0])
        self.assertEqual(len(build_prompt().format_messages(file_path="x", source="y", context="")), 2)

    def test_an_unsupported_file_is_refused_without_touching_memory(self):
        with self.replies([], []):
            final = self.run_graph({"source_path": str(UNSUPPORTED), "user_id": "varun"})
        self.assertEqual(final["status"], "refused")
        self.assertEqual(final.get("recalled", []), [])

    def test_a_memory_round_trips_through_the_checkpointer(self):
        """Memory is a new state type, so memory.CHECKPOINT_TYPES has to name it."""
        with patch.dict(os.environ, {"LANGGRAPH_STRICT_MSGPACK": "true"}):
            with memory.open_checkpointer(self.threads) as checkpointer:
                compiled = graph.build_graph(checkpointer)
                config = memory.thread_config("t1")
                compiled.update_state(config, {"recalled": [store.Memory("k", TESTIDS, 0.42, "now")]})
                restored = memory.thread_state(compiled, "t1")
        self.assertIsInstance(restored["recalled"][0], store.Memory)
        self.assertEqual(restored["recalled"][0].score, 0.42)


class _NoStore:
    """A with-block that yields nothing, so run_graph reads the same either way."""

    def __enter__(self):
        return None

    def __exit__(self, *exc):
        return False


class CommandLineTests(StoreHarness):
    def cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.run([*argv, "--memory-db", str(self.db)])
        return code, out.getvalue(), err.getvalue()

    def fake_embeddings(self):
        return patch.multiple(cli, make_embeddings=Mock(return_value=self.embeddings),
                              embedding_dims=Mock(return_value=HashEmbeddings.dims))

    def test_teaching_listing_and_forgetting_need_no_file_to_convert(self):
        with self.fake_embeddings():
            code, _, err = self.cli("remember", TESTIDS, "--user", "varun")
            self.assertEqual(code, 0)
            self.assertIn("remembered", err)
            _, out, _ = self.cli("memories", "--user", "varun")
            self.assertIn(TESTIDS, out)
            key = out.split()[0]
            _, _, err = self.cli("forget", key, "--user", "varun")
            self.assertIn(f"forgot [{key}]", err)
            _, out, _ = self.cli("memories", "--user", "varun")
            self.assertEqual(out, "")

    def test_forgetting_something_that_is_not_there_says_so(self):
        _, _, err = self.cli("forget", "0123456789abcdef")
        self.assertIn("no memory", err)

    def test_a_run_reports_which_preferences_it_applied(self):
        with self.fake_embeddings(), self.replies([ConversionResult(code=GOLDEN)], [PASS]):
            self.cli("remember", TESTIDS, "--user", "varun")
            code, _, err = self.cli("convert", str(SOURCE), "--user", "varun", "--out",
                                    str(Path(self.tmp.name) / "LoginPage.ts"))
        self.assertEqual(code, 0)
        self.assertIn("applying 1 of 1 remembered preference(s)", err)
        self.assertIn("score", err)

    def test_a_preference_that_does_not_fit_is_reported_as_not_applied(self):
        """Never silent: "why didn't it use my rule?" is answered on screen."""
        with self.fake_embeddings(), self.replies([ConversionResult(code=GOLDEN)], [PASS]):
            self.cli("remember", OFFTOPIC, "--user", "varun")
            _, _, err = self.cli("convert", str(SOURCE), "--user", "varun", "--out",
                                 str(Path(self.tmp.name) / "LoginPage.ts"))
        self.assertIn("none close enough to this file", err)

    def test_no_recall_turns_the_whole_thing_off(self):
        with self.fake_embeddings(), self.replies([ConversionResult(code=GOLDEN)], [PASS]):
            self.cli("remember", TESTIDS, "--user", "varun")
            _, _, err = self.cli("convert", str(SOURCE), "--user", "varun", "--no-recall", "--out",
                                 str(Path(self.tmp.name) / "LoginPage.ts"))
        self.assertNotIn("Long-term memory", err)

    def test_no_recall_and_remember_together_is_a_usage_error(self):
        code, _, err = self.cli("convert", "--no-recall", "--remember", TESTIDS)
        self.assertEqual(code, 2)
        self.assertIn("cannot be combined", err)

    def test_writing_a_memory_with_broken_embeddings_is_refused_not_silent(self):
        """A memory stored without a vector could never be found again."""
        with patch.object(cli, "make_embeddings", side_effect=RuntimeError("no OPENAI_API_KEY")):
            code, _, err = self.cli("remember", TESTIDS)
        self.assertEqual(code, 2)
        self.assertIn("could never be recalled", err)

    def test_reading_with_broken_embeddings_degrades_out_loud(self):
        with patch.object(cli, "make_embeddings", side_effect=RuntimeError("no OPENAI_API_KEY")):
            with self.replies([ConversionResult(code=GOLDEN)], [PASS]):
                _, _, err = self.cli("convert", str(SOURCE), "--out",
                                     str(Path(self.tmp.name) / "LoginPage.ts"))
        self.assertIn("recall falls back to the most recent memories", err)


if __name__ == "__main__":
    unittest.main()

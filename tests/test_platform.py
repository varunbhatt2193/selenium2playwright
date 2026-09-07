"""Step 10.1 — the local platform: langgraph.json, and the entry points it names.

`langgraph dev` is a server that imports our graphs and serves them over HTTP.
Nothing here talks to it: starting a server is not a unit test. What is testable
— and what actually broke while building this step — is the *contract* between
langgraph.json and the code it points at, because every one of these mistakes
fails at server start (or, worse, does not fail at all):

  * a factory whose signature the loader cannot classify,
  * a factory that hands the platform a graph with its own checkpointer or
    store, when the platform's whole job is to provide those,
  * an embeddings entry that is a *factory* where the loader wants a
    text -> vectors function,
  * a vector index whose `dims` no longer matches the embeddings model, or
    whose `fields` name a key the memories do not have,
  * a store our own code cannot recognise as indexed, because the platform
    wraps it.
"""

import inspect
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.store.base import BaseStore

from selenium2playwright import env, graph, llm, server, store, suite_graph

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "langgraph.json"
CONFIG = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def target(spec: str) -> tuple[Path, str]:
    """Split a "./path/to/file.py:name" entry into the file and the name."""
    path, _, name = spec.rpartition(":")
    return ROOT / path, name


class ConfigTests(unittest.TestCase):
    """langgraph.json is a promise about this repository. Hold it to it."""

    def test_every_path_it_names_exists_and_exports_what_it_claims(self):
        specs = [g["path"] if isinstance(g, dict) else g for g in CONFIG["graphs"].values()]
        specs.append(CONFIG["store"]["index"]["embed"])
        for spec in specs:
            with self.subTest(spec=spec):
                path, name = target(spec)
                self.assertTrue(path.is_file(), f"{path} does not exist")
                self.assertTrue(hasattr(server, name), f"server.py exports no {name}")

    def test_it_publishes_both_front_ends_the_cli_has(self):
        # A graph missing here is a graph nobody can reach over HTTP, which is
        # the only thing 10.2 and 10.3 will have.
        self.assertEqual(set(CONFIG["graphs"]), {"convert", "suite"})
        for name, spec in CONFIG["graphs"].items():
            with self.subTest(graph=name):
                self.assertTrue(spec["description"].strip(), "Studio shows this to a human")

    def test_the_vector_index_matches_the_memories_it_will_index(self):
        index = CONFIG["store"]["index"]
        # store.remember writes {"text": ..., "source": ...}; the index must name
        # the key that holds the sentence, or it embeds the wrong half.
        self.assertEqual(index["fields"], ["text"])
        # dims cannot be computed in a JSON file, so it is written down — and it
        # is only right for as long as the default embeddings model is.
        self.assertEqual(index["dims"], llm.EMBEDDING_DIMS[env.DEFAULT_EMBEDDINGS])


class FactoryTests(unittest.TestCase):
    """The signature is the API. The loader reads it, and cannot ask."""

    def test_the_published_factories_take_no_arguments(self):
        for name in ("convert_graph", "suite_graph"):
            with self.subTest(factory=name):
                params = inspect.signature(getattr(server, name)).parameters
                self.assertEqual(list(params), [], "the platform passes nothing it was not asked for")

    def test_the_builders_they_wrap_could_not_be_published_directly(self):
        # This is the reason server.py exists, so it is asserted rather than
        # written in a comment. build_graph's two unannotated parameters are
        # rejected outright; build_suite_graph's single one is worse — the
        # loader silently decides it is the RunnableConfig and passes one in,
        # so a graph would run with a config dict where its store should be.
        self.assertEqual(len(inspect.signature(graph.build_graph).parameters), 2)
        self.assertEqual(len(inspect.signature(suite_graph.build_suite_graph).parameters), 1)

    def test_the_loader_itself_agrees(self):
        # Pinning our reading of the rule against the code that enforces it, so
        # a change in langgraph-api shows up here and not at a server start.
        try:
            from langgraph_api._factory_utils import _classify_factory
        except ImportError:  # pragma: no cover - langgraph-cli is a dev extra
            self.skipTest("langgraph-api is not installed")
        self.assertEqual(_classify_factory(server.convert_graph), {})
        self.assertEqual(_classify_factory(server.suite_graph), {})
        with self.assertRaises(ValueError):
            _classify_factory(graph.build_graph)
        # No exception: the silent one. It maps the config onto `store`.
        self.assertEqual(list(_classify_factory(suite_graph.build_suite_graph)(None, None)), ["store"])

    def test_the_graphs_bring_no_database_of_their_own(self):
        # The platform creates the checkpointer and the store and injects them
        # at run time. Compiling one in is ignored in the cloud and refused by
        # the local dev server, so the graph must arrive empty-handed.
        for name in ("convert_graph", "suite_graph"):
            with self.subTest(factory=name):
                compiled = getattr(server, name)()
                self.assertNotIsInstance(compiled.checkpointer, BaseCheckpointSaver)
                self.assertNotIsInstance(compiled.store, BaseStore)


class EmbeddingsTests(unittest.TestCase):
    """`embed_memories` is a function of texts, not a factory. That is the shape."""

    def setUp(self):
        server.memory_embeddings.cache_clear()
        self.addCleanup(server.memory_embeddings.cache_clear)

    def test_it_embeds_the_texts_with_whatever_model_the_env_chose(self):
        class Fake:
            def embed_documents(self, texts):
                return [[float(len(text))] for text in texts]

        with patch.object(llm, "make_embeddings", return_value=Fake()):
            self.assertEqual(server.embed_memories(["ab", "cde"]), [[2.0], [3.0]])

    def test_it_is_built_once_and_reused(self):
        with patch.object(llm, "make_embeddings") as make:
            make.return_value.embed_documents.return_value = [[0.0]]
            server.embed_memories(["one"])
            server.embed_memories(["two"])
        self.assertEqual(make.call_count, 1)

    def test_recall_switched_off_is_a_named_conflict_not_an_obscure_crash(self):
        # S2P_EMBEDDINGS=off is a supported answer everywhere else in the
        # project. It cannot be one here, because langgraph.json has already
        # asked for an index — so say which two settings disagree.
        with patch.object(llm, "make_embeddings", return_value=None):
            with self.assertRaises(RuntimeError) as refused:
                server.embed_memories(["anything"])
        self.assertIn("S2P_EMBEDDINGS", str(refused.exception))
        self.assertIn("langgraph.json", str(refused.exception))


class WrappedStoreTests(unittest.TestCase):
    """The store a node is handed is not always the store holding the vectors."""

    class Wrapper:
        """The shape langgraph-runtime's BatchedStore presents: no index_config."""

        def __init__(self, inner):
            self._store = inner

    class Indexed:
        index_config = {"dims": 3, "fields": ["text"]}

    class Plain:
        index_config = None

    def test_an_index_behind_a_wrapper_still_counts_as_an_index(self):
        self.assertTrue(store.indexed(self.Wrapper(self.Indexed())))

    def test_and_an_unindexed_one_behind_a_wrapper_still_does_not(self):
        # The degraded path must stay reachable, or "no embeddings" would start
        # claiming to rank by meaning.
        self.assertFalse(store.indexed(self.Wrapper(self.Plain())))
        self.assertFalse(store.indexed(self.Plain()))

    def test_the_unwrapping_is_one_layer_deep_and_asks_for_nothing_else(self):
        self.assertTrue(store.indexed(self.Indexed()))


if __name__ == "__main__":
    unittest.main()

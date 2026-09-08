"""Steps 10.1 and 10.2 — the platform contract: langgraph.json, its entry points,
and the inputs a server on another machine can actually accept.

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

import importlib.util
import inspect
import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.store.base import BaseStore

from selenium2playwright import env, graph, llm, server, store, suite_graph

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "langgraph.json"
CONFIG = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
# Where the image installs the pinned Node toolchain (step 10.2).
CONTAINER_SANDBOX = "/opt/s2p-sandbox"


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


class DeployConfigTests(unittest.TestCase):
    """Step 10.2 — what has to be true of the image before it is worth building."""

    def test_no_description_contains_an_apostrophe(self):
        # Not a style rule. The CLI writes the graph table into the Dockerfile as
        # ENV LANGSERVE_GRAPHS='{...}', in single quotes, unescaped. One
        # apostrophe in a description closes that string early and the build dies
        # with "Syntax error - can't find = in <the next word>", pointing at
        # nothing that looks like this file. Cost one build to find.
        for name, spec in CONFIG["graphs"].items():
            with self.subTest(graph=name):
                self.assertNotIn("'", spec["description"])

    def test_the_image_installs_the_toolchain_the_gates_shell_out_to(self):
        lines = "\n".join(CONFIG["dockerfile_lines"])
        # A deployment that cannot compile its own output can only *claim* the
        # conversion worked, so Node and the pinned toolchain are not optional.
        self.assertIn("node:22", lines)
        self.assertIn("npm ci", lines)
        # Installed to a fixed path, and the code told where to look — the
        # directory a repo is cloned into is not something an image can know.
        self.assertIn(f"ENV S2P_SANDBOX={CONTAINER_SANDBOX}", lines)

    def test_it_copies_the_lockfile_before_the_rest_of_the_sandbox(self):
        # npm ci needs both files, and copying them alone first is what keeps the
        # install layer cached until the pinned versions actually change.
        lines = CONFIG["dockerfile_lines"]
        install = next(i for i, line in enumerate(lines) if "npm ci" in line)
        lockfile = next(i for i, line in enumerate(lines) if "package-lock.json" in line)
        self.assertLess(lockfile, install)
        for required in ("sandbox/package.json", "sandbox/package-lock.json"):
            self.assertIn(required, lines[lockfile])

    def test_the_build_context_excludes_secrets_and_the_wrong_platforms_binaries(self):
        ignored = (ROOT / ".dockerignore").read_text(encoding="utf-8").split()
        # An image is copied, pulled and cached. A key baked into a layer is a
        # key you cannot rotate out of the layers that already exist.
        self.assertIn(".env", ignored)
        # node_modules is built INSIDE the image, on Linux. A macOS tree copied
        # in would shadow it with binaries for the wrong platform.
        self.assertIn("node_modules/", ignored)


class SandboxLocationTests(unittest.TestCase):
    """The toolchain lives in the repo, unless something says otherwise."""

    def probed(self, **environ) -> object:
        """A private copy of `env`, loaded under `environ`. Never the shared one.

        `importlib.reload(env)` was the obvious spelling, and it was a bug that
        outlived its own tearDown. Reload re-executes the module and binds
        `SANDBOX` to a **new** Path, while the four gates still hold the object
        they imported at start-up. The values matched, so it looked harmless —
        but `OneSandboxTests` asserts *identity*, deliberately, and identity was
        gone the moment any test in this class ran. Reloading again to clean up
        could not fix that: another reload is another new object, so the four
        assertions failed for whoever ran after this class, in a file they had
        not touched.

        Loading a separate module object answers the same question — what does
        this file compute under this environment? — and leaves `sys.modules`
        alone, so no other test can notice this one ran.
        """
        spec = importlib.util.spec_from_file_location("env_probe", env.__file__)
        module = importlib.util.module_from_spec(spec)
        with patch.dict(os.environ, environ, clear=False):
            spec.loader.exec_module(module)
        return module

    def test_it_defaults_to_the_folder_in_this_repository(self):
        fresh = self.probed(S2P_SANDBOX="")
        self.assertEqual(fresh.SANDBOX, fresh.REPO_ROOT / "sandbox")
        self.assertTrue((fresh.SANDBOX / "package.json").is_file())

    def test_and_an_absolute_override_wins(self):
        fresh = self.probed(S2P_SANDBOX=CONTAINER_SANDBOX)
        self.assertEqual(fresh.SANDBOX, Path(CONTAINER_SANDBOX))

    def test_probing_leaves_the_shared_module_alone(self):
        # The regression itself: the gates bind `SANDBOX` at import, so anything
        # that rebinds it here breaks an identity assertion in a class that
        # never ran this code. Cheap to assert, and it is the whole reason the
        # method above does not reload.
        before = env.SANDBOX
        self.probed(S2P_SANDBOX=CONTAINER_SANDBOX)
        self.assertIs(env.SANDBOX, before)



class OneSandboxTests(unittest.TestCase):
    """Four modules used to compute the toolchain path themselves. One does now.

    Deliberately not in SandboxLocationTests: reloading env rebinds the name,
    and identity is exactly what this asserts — an override that moved some of
    the toolchain and not the rest would be worse than one that moved none.
    """

    def test_every_gate_reads_the_same_object(self):
        from selenium2playwright import assemble
        from selenium2playwright.validators import compile as compile_gate
        from selenium2playwright.validators import lint, parity
        for module in (compile_gate, lint, parity, assemble):
            with self.subTest(module=module.__name__):
                self.assertIs(module.SANDBOX, env.SANDBOX)


class RemoteInputTests(unittest.TestCase):
    """Step 10.2 — a server has none of your files, so a path is not an input."""

    SOURCE = ROOT / "samples" / "selenium-suite" / "pages" / "LoginPage.ts"

    def setUp(self):
        self.text = self.SOURCE.read_text(encoding="utf-8")

    def test_a_path_is_still_read_from_disk(self):
        # Every phase before this one, and the eval runner, and most tests.
        state = graph.intake({"source_path": str(self.SOURCE)})
        self.assertEqual(state["source"], self.text)
        self.assertTrue(state["classification"].supported)

    def test_text_is_used_as_sent_and_the_path_becomes_only_a_name(self):
        state = graph.intake({"source_path": "LoginPage.ts", "source_text": self.text})
        self.assertEqual(state["source"], self.text)
        # The name is written back, because the recall query, the scorecard and
        # the report all ask the state what the file is called.
        self.assertEqual(state["source_path"], "LoginPage.ts")
        self.assertTrue(state["classification"].supported)

    def test_text_with_no_name_at_all_still_converts(self):
        state = graph.intake({"source_text": self.text})
        self.assertEqual(state["source_path"], graph.PASTED_NAME)
        self.assertTrue(state["classification"].supported)

    def test_the_name_travels_into_the_things_that_ask_for_it(self):
        # What a file *is* comes from its contents, not its name — but the name
        # is in the long-term-memory query, the scorecard title and the report,
        # so a paste box that sends "pasted.ts" for everything makes recall
        # blurrier than it needs to be.
        state = graph.intake({"source_path": "LoginPage.ts", "source_text": self.text})
        query = store.recall_query(state["source_path"], state["classification"], state["source"])
        self.assertIn("LoginPage.ts", query)

    def test_companions_can_be_sent_as_text_too(self):
        state = graph.intake({"source_path": "login.spec.ts", "source_text": self.text,
                              "context_text": {"pages/LoginPage.ts": "export class LoginPage {}"}})
        self.assertIn("export class LoginPage {}", state["context"])
        # Keyed the way validate() and format_context() have always looked
        # companions up — by resolved path, in both modes.
        self.assertEqual(list(state["context_files"]),
                         [str(Path("pages/LoginPage.ts").resolve())])

    def test_neither_input_is_consumed_so_turn_two_needs_only_a_sentence(self):
        # source_text belongs to the conversation the way source_path always
        # has. Consuming it would leave a refine turn with no file at all.
        first = {"source_path": "LoginPage.ts", "source_text": self.text}
        state = graph.intake(first)
        self.assertNotIn("source_text", state)
        second = graph.intake({**first, **state, "refinement": "use getByTestId"})
        self.assertEqual(second["source"], self.text)
        self.assertEqual(second["conventions"], ["use getByTestId"])
        self.assertEqual(second["turn"], 2)


class SourceSizeTests(unittest.TestCase):
    """One paste is one request, and a request has a size."""

    def test_a_file_within_the_cap_is_classified_normally(self):
        self.assertIsNone(graph.oversized("x" * graph.MAX_SOURCE_BYTES))

    def test_an_oversized_one_leaves_by_the_same_door_as_a_cypress_file(self):
        # A refusal, not an exception: one honest sentence, no model call, and
        # no half-converted output — the refuse node already does all of that.
        state = graph.intake({"source_text": "x" * (graph.MAX_SOURCE_BYTES + 1)})
        self.assertFalse(state["classification"].supported)
        self.assertEqual(graph.route_after_intake(state), "refuse")

    def test_the_size_it_reports_never_reads_as_equal_to_the_limit(self):
        refusal = graph.oversized("x" * (graph.MAX_SOURCE_BYTES + 1)).reason
        self.assertIn("257 KB", refusal)
        self.assertIn("256 KB", refusal)

    def test_the_cap_counts_bytes_not_characters(self):
        # A file of multi-byte characters is bigger than len() thinks it is.
        wide = "é" * ((graph.MAX_SOURCE_BYTES // 2) + 1)
        self.assertLess(len(wide), graph.MAX_SOURCE_BYTES)
        self.assertIsNotNone(graph.oversized(wide))


if __name__ == "__main__":
    unittest.main()

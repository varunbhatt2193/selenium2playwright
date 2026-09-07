"""Step 10.1 — the entry points LangGraph Platform loads, and nothing else.

Every phase so far has had exactly one front end: `cli.py`, a program you run.
This step adds a second one that is not a program at all. `langgraph dev` starts
a small HTTP server — the same server LangGraph Platform runs in the cloud —
reads `langgraph.json` to find out which graphs to publish, imports them, and
serves them as an API that LangGraph Studio (and, from 10.2, `langgraph-sdk`)
can call. Nothing in the graph changes. What changes is who calls it.

The server needs three things from us, and this module is two of them:

  a callable that returns a graph, taking no arguments
        The platform owns persistence. It creates the checkpointer (so threads,
        interrupts and resume work over HTTP) and the store (so long-term
        memory works), and it hands them to the graph at run time. Our
        `build_graph(checkpointer=None, store=None)` therefore must be compiled
        *without* either — which is exactly what its defaults already do.

        It cannot be named directly in `langgraph.json`, though, and the reason
        is worth knowing because the failure is loud in one case and silent in
        the other. The loader inspects the factory's signature and allows at
        most two parameters, a `RunnableConfig` and a `ServerRuntime`, matched
        by annotation (`langgraph_api/_factory_utils.py:_classify_factory`):

          build_graph(checkpointer, store)  -> two unannotated parameters,
              so the loader cannot tell which is which and raises at startup.
          build_suite_graph(store)          -> one parameter, unannotated, so
              the loader decides it must be the config and passes a
              RunnableConfig **as the store**. No error. The graph simply
              behaves as if it had a store that is really a dict.

        Zero-argument wrappers make the contract unambiguous, and they are the
        honest place to say "the platform brings the database, we bring the
        graph".

  an embeddings function for the store's vector index
        `langgraph.json` configures the platform store's index, and its `embed`
        field may name a module attribute. The loader passes whatever it finds
        to `ensure_embeddings`, which treats a plain callable as a
        *text -> vectors* function, not as a factory — so what belongs here is
        `embed_memories` below, not `llm.make_embeddings`. Going through our own
        function instead of hard-coding "openai:text-embedding-3-small" in the
        JSON is what keeps `S2P_EMBEDDINGS` the one place that chooses.

The third thing is `langgraph.json` itself, next to `pyproject.toml`.
"""

from __future__ import annotations

from collections.abc import Sequence
from functools import lru_cache

from langchain_core.embeddings import Embeddings

from selenium2playwright import env, llm
from selenium2playwright.graph import build_graph
from selenium2playwright.suite_graph import build_suite_graph


def convert_graph():
    """One Selenium file -> one Playwright file: the graph `s2p convert` runs.

    Two ways to hand it a file, and over HTTP only one of them is real:

        {"source_text": "import { By } ...", "source_path": "LoginPage.ts"}
        {"source_path": "samples/selenium-suite/pages/LoginPage.ts"}

    The first sends the bytes and uses `source_path` as a *name*; the second
    reads that path on the **server's** filesystem, which is your own machine
    under `langgraph dev` and a container with none of your files once this is
    deployed (step 10.2). Companions work the same way: `context_text`
    (name -> contents) or `context_paths`.

    Also optional: `output_path`, `refinement`, `ask_risks`, `user_id`,
    `remember`, `max_attempts`.
    """
    return build_graph()


def suite_graph():
    """A whole folder: the graph `s2p suite` runs, fan-out and all.

    Inputs (`SuiteState`): `root`, `out_root`, and optionally `only`. Both are
    server-side paths, with the same caveat as above.
    """
    return build_suite_graph()


@lru_cache(maxsize=1)
def memory_embeddings() -> Embeddings:
    """The embeddings model `S2P_EMBEDDINGS` chose, built once per process.

    Lazy on purpose: `langgraph.json` names this module, so anything done at
    import time would be done at server start, and an embeddings key is not
    needed to convert a file. Cached because the store asks for it on every
    write and every semantic search.
    """
    embeddings = llm.make_embeddings()
    if embeddings is None:
        raise RuntimeError(
            f"S2P_EMBEDDINGS is '{env.EMBEDDINGS_OFF}', but langgraph.json configures a "
            "vector index for the platform store, and an index cannot be built without "
            "an embeddings model. Either set S2P_EMBEDDINGS to a model, or delete the "
            '"store" block from langgraph.json to run the server without semantic recall.'
        )
    return embeddings


def embed_memories(texts: Sequence[str]) -> list[list[float]]:
    """Text in, vectors out — the shape `ensure_embeddings` expects of a callable."""
    return memory_embeddings().embed_documents(list(texts))

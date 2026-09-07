"""Step 7.1 — short-term memory: one SQLite file, one thread per conversation.

Until now every `graph.invoke(...)` started from nothing. The state dict was
built by `intake`, flowed through the nodes, and was thrown away when the
process exited. That is why refining a conversion meant re-running the whole
thing and re-explaining yourself.

A **checkpointer** changes that. It is one object handed to `compile()`; after
that LangGraph writes a snapshot of the state to it after every super-step
(every batch of nodes that runs together). Snapshots are filed under a
**thread_id** you choose, passed per call in `config["configurable"]`. Invoke
the graph again with the same thread_id and LangGraph loads the last snapshot
first, then merges your new input on top — so the second call already knows the
source file, the previous conversion, and anything else the state was holding.

    thread   = one conversation, identified by a string you pick ("login-page")
    snapshot = the whole state after one super-step, plus which node comes next
    resume   = invoke with a thread that already has snapshots

`SqliteSaver` is the local implementation: a plain sqlite3 file, no server.
LangGraph Platform swaps in a Postgres one in production (Phase 10) and nothing
in this file's callers changes — that is the point of the interface.

Docs: https://docs.langchain.com/oss/python/langgraph/persistence
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite import SqliteSaver

# Local default. Gitignored: a thread database holds copies of your source and
# every draft the model wrote, which is working data, not repository content.
DEFAULT_DB = Path(".s2p/threads.sqlite")

IN_MEMORY = ":memory:"

# Snapshots are stored as msgpack. Builtins (str, list, dict...) rebuild
# themselves; anything else has to be named here before LangGraph will
# reconstruct it, so that loading a checkpoint can never import and
# instantiate an arbitrary class that happens to be named in the file.
# Every non-builtin type ConversionState holds is listed. Add a type to the
# state, add it here: an unlisted one is not an error on read, it silently comes
# back as the plain dict it was encoded from, and the AttributeError arrives much
# later. tests/test_memory.py pins both halves of that.
CHECKPOINT_TYPES: tuple[tuple[str, str], ...] = (
    ("selenium2playwright.classify", "Classification"),
    ("selenium2playwright.schemas", "ConversionResult"),
    ("selenium2playwright.schemas", "Critique"),
    ("selenium2playwright.schemas", "Finding"),
    ("selenium2playwright.schemas", "ValidationReport"),
    ("selenium2playwright.schemas", "ConversionReport"),
)


def strict_serializer() -> JsonPlusSerializer:
    """The reader half of the saver, locked to safe builtins + CHECKPOINT_TYPES.

    Left alone, LangGraph reads a checkpoint permissively — it rebuilds whatever
    class the file names and prints a deprecation warning. `with_allowlist` on
    top of that changes nothing, because "everything" is already allowed; the
    allowlist only bites once the serializer starts from the strict set. The
    library reaches strict via the LANGGRAPH_STRICT_MSGPACK environment
    variable, read once at import, which is too late and too global to rely on,
    so we ask for it here instead. Effect: the thread database is data, never a
    list of classes to import, and today's warning is gone.
    """
    return JsonPlusSerializer(allowed_msgpack_modules=None)


@contextmanager
def open_checkpointer(path: Path | str = DEFAULT_DB) -> Iterator[BaseCheckpointSaver]:
    """Open (creating if needed) the thread database and yield a ready saver.

    A context manager because the SQLite connection must be closed; use it
    around the whole run, not around a single invoke, so every turn of an
    interactive session shares one connection.
    """
    if str(path) != IN_MEMORY:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    # from_conn_string() takes no serializer, so open the connection the same
    # way it does (check_same_thread=False: LangGraph may write from a worker).
    with closing(sqlite3.connect(str(path), check_same_thread=False)) as conn:
        yield SqliteSaver(conn, serde=strict_serializer()).with_allowlist(CHECKPOINT_TYPES)


def thread_config(thread_id: str, **extra: Any) -> dict:
    """The one config key that turns a stateless invoke into a conversation turn."""
    config: dict[str, Any] = dict(extra)
    config.setdefault("configurable", {})["thread_id"] = thread_id
    return config


def thread_state(graph: Any, thread_id: str) -> dict:
    """Saved values for a thread; {} when the thread has never run.

    get_state returns a StateSnapshot (values, next, config, metadata...).
    We only need .values here; the CLI uses it to decide whether a turn can
    continue without being handed the source file again.
    """
    return dict(graph.get_state(thread_config(thread_id)).values)


def list_threads(path: Path | str = DEFAULT_DB) -> list[str]:
    """Thread ids present in the database, for `--list-threads`.

    Read straight from the table rather than through the saver: `list()` walks
    every checkpoint, and we only want the distinct names.
    """
    if str(path) != IN_MEMORY and not Path(path).exists():
        return []
    with sqlite3.connect(str(path)) as conn:
        try:
            rows = conn.execute("SELECT DISTINCT thread_id FROM checkpoints ORDER BY thread_id").fetchall()
        except sqlite3.OperationalError:
            return []  # a file that exists but has never been set up by a saver
    return [row[0] for row in rows]

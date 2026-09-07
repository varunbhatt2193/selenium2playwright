"""Step 7.3 — long-term memory: preferences that outlive the conversation.

Step 7.1 gave the graph a **checkpointer**: everything one thread learns is
saved under its thread_id, so turn 2 already knows what turn 1 did. But a
thread is one conversation about one file. Tell the agent "we always use
getByTestId" on Monday's login page and Tuesday's checkout page — a fresh
thread — knows nothing about it.

A **store** is the other half. Same idea (a database LangGraph hands to your
nodes), different scope:

    checkpointer  one conversation      keyed by thread_id     wiped by a new thread
    store         everything you know   keyed by namespace+key  survives every thread

    namespace = a tuple you choose, ("conventions", "varun") here — a folder
    key       = the item's name inside it; we use a hash of the text, so
                teaching the same rule twice updates one memory instead of
                making two
    item      = {"text": ...} plus timestamps, returned as an Item

The interesting part is *recall*. Thirty remembered preferences are useless if
all thirty go into every prompt: they cost tokens, they contradict each other,
and the model has to guess which ones apply. So the store is asked a question —
"what do I know that matters for THIS file?" — and answers with the few closest
ones. That comparison is what an **embedding** is for: the store runs each
memory's text and the query through an embeddings model, which turns text into
a list of numbers positioned so that similar meanings sit close together, and
returns the nearest by cosine similarity (1.0 = identical direction, 0 =
unrelated). No keyword has to match. "prefer role-based selectors" is retrieved
for a file full of By.css, because the *meaning* is close.

Two consequences to know before trusting it:

- Scores are relative, not absolute. A threshold has to be measured against
  your own data, never guessed; scripts/calibrate_recall.py does that and
  MIN_SCORE below is the result.
- Vectors from two different embeddings models are not comparable. Switching
  S2P_EMBEDDINGS silently makes every stored vector nonsense, so open_store()
  records the model in the file and refuses to open it with another one.

Docs: https://docs.langchain.com/oss/python/langgraph/memory#long-term-memory
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import closing, contextmanager
from dataclasses import dataclass
from pathlib import Path

from langchain_core.embeddings import Embeddings
from langgraph.store.base import BaseStore
from langgraph.store.sqlite import SqliteStore

from selenium2playwright.classify import Classification

# Separate from the thread database on purpose: threads are working data about
# one file, memories are how you like your Playwright written. Gitignored under
# .s2p/ all the same — they are yours, not the repository's.
DEFAULT_DB = Path(".s2p/memories.sqlite")

IN_MEMORY = ":memory:"

# ("conventions", user) — one folder per person, so a shared database (Phase 10)
# never mixes two people's preferences.
NAMESPACE = "conventions"
DEFAULT_USER = "local"

# Our own bookkeeping, kept in the same file, deliberately outside NAMESPACE so
# it can never be recalled into a prompt. It has no "text" field, so the index
# ignores it.
META = ("s2p-meta",)

# How many memories may enter one prompt, and how close they must be. Both
# measured, not guessed — see scripts/calibrate_recall.py and
# docs/long-term-memory.md. Three is enough for real preference sets and small
# enough that a bad recall cannot bury the playbook.
#
# 0.29 is measured, and the measurement also says what it can and cannot do.
# On the sample suite it lets through everything that genuinely applies except
# one vaguely worded memory, and admits no unrelated memory at all — but the two
# bands do overlap by a few hundredths, so no single number could have separated
# them perfectly. That is the honest shape of this parameter: a floor that keeps
# noise out, not a judge of relevance.
#
# The ranking does the real work, and the limit of 3 is what makes a stray
# recall cheap rather than harmful: the prompt tells the model to apply only what
# genuinely fits, the critic is told that an ignored preference is not a defect,
# and the CLI prints what was applied. Re-run scripts/calibrate_recall.py after
# changing the embeddings model or the query — neither number survives that.
RECALL_LIMIT = 3
MIN_SCORE = 0.29

@dataclass(frozen=True)
class Memory:
    """One remembered preference. Small: this goes into the checkpoint."""

    key: str
    text: str
    score: float | None = None  # None = returned without ranking (recall is off)
    created: str = ""


def namespace(user_id: str = DEFAULT_USER) -> tuple[str, str]:
    return (NAMESPACE, user_id or DEFAULT_USER)


def normalize(text: str) -> str:
    """Whitespace and case folded away, so 'Use  getByTestId' == 'use getbytestid'."""
    return re.sub(r"\s+", " ", text).strip().casefold()


def key_for(text: str) -> str:
    """A content address. Teaching the same rule twice overwrites one memory."""
    return hashlib.sha256(normalize(text).encode("utf-8")).hexdigest()[:16]


def indexed(store: BaseStore) -> bool:
    """True when this store can rank by meaning; False when it can only list.

    The store a node is handed is not always the store that holds the vectors.
    LangGraph Platform (step 10.1) wraps its own store in a batching adapter
    that forwards `search` but carries no `index_config` of its own, so asking
    the object in front of us would answer "not indexed" and quietly demote
    every recall to plain recency. Unwrap one layer before believing that.
    """
    inner = getattr(store, "_store", None)
    return bool(getattr(store, "index_config", None)
                or (inner is not None and getattr(inner, "index_config", None)))


@contextmanager
def open_store(path: Path | str = DEFAULT_DB,
               embeddings: Embeddings | None = None,
               dims: int | None = None) -> Iterator[BaseStore]:
    """Open (creating if needed) the memory database and yield a ready store.

    embeddings=None opens it unindexed: put/get/list still work, recall falls
    back to the most recent memories and says so. That is the honest degraded
    mode for a machine with no embeddings key — never a silent one.
    """
    if str(path) != IN_MEMORY:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    index = None
    if embeddings is not None:
        if not dims:
            raise ValueError("embeddings need their dimension count to build the index")
        # Both spellings on purpose. Which key names the fields to embed depends
        # on the store: InMemoryStore (and the documented IndexConfig, and
        # SqliteStore's own docstring) reads "fields"; SqliteStore's code reads
        # "text_fields". Give it only "fields" and it silently falls back to "$"
        # — the whole document — so each memory's `source` path ends up inside
        # its own vector and the same sentence scores differently depending on
        # where it was written. No error, just quietly wrong ranking.
        index = {"dims": dims, "embed": embeddings,
                 "fields": ["text"], "text_fields": ["text"]}
    # isolation_level=None: SqliteStore.setup() runs its migrations in explicit
    # transactions, and sqlite3's default implicit BEGIN collides with them.
    with closing(sqlite3.connect(str(path), isolation_level=None, check_same_thread=False)) as conn:
        store = SqliteStore(conn, index=index)
        store.setup()
        if embeddings is not None:
            _check_embeddings(store, str(embeddings_id(embeddings)))
        yield store


def embeddings_id(embeddings: Embeddings) -> str:
    """A name for whichever embeddings object we were handed, for the meta row."""
    return getattr(embeddings, "model", None) or type(embeddings).__name__


def _check_embeddings(store: BaseStore, name: str) -> None:
    """Refuse to compare vectors written by one model against another's.

    Cosine similarity between two different models' vectors is not small — it is
    meaningless, and nothing about it looks broken. Better a loud error on open.
    """
    saved = store.get(META, "embeddings")
    if saved is None:
        store.put(META, "embeddings", {"name": name})
        return
    if saved.value.get("name") != name:
        raise ValueError(
            f"this memory database was built with {saved.value.get('name')!r}, but "
            f"{name!r} is configured. Vectors from two models are not comparable: "
            "either set S2P_EMBEDDINGS back, or start a new file with --memory-db."
        )


def remember(store: BaseStore, text: str, user_id: str = DEFAULT_USER, source: str = "") -> Memory:
    """Write one preference. Returns it, so the caller can show the key."""
    text = " ".join(text.split())
    if not text:
        raise ValueError("a memory needs some text")
    key = key_for(text)
    store.put(namespace(user_id), key, {"text": text, "source": source})
    return Memory(key=key, text=text)


def forget(store: BaseStore, key: str, user_id: str = DEFAULT_USER) -> bool:
    """Delete by key. False when there was nothing there — never a silent no-op."""
    if store.get(namespace(user_id), key) is None:
        return False
    store.delete(namespace(user_id), key)
    return True


def memories(store: BaseStore, user_id: str = DEFAULT_USER) -> list[Memory]:
    """Everything remembered for this user, newest first. No embeddings needed."""
    items = store.search(namespace(user_id), limit=1000)
    found = [Memory(key=i.key, text=i.value.get("text", ""), created=str(i.created_at)) for i in items]
    return sorted(found, key=lambda m: m.created, reverse=True)


def recall(store: BaseStore, query: str, user_id: str = DEFAULT_USER,
           limit: int = RECALL_LIMIT, min_score: float = MIN_SCORE,
           exclude: Sequence[str] = ()) -> list[Memory]:
    """The few remembered preferences that matter for this file, closest first.

    exclude drops anything the caller already has by other means — a preference
    repeated as this thread's own standing instruction should reach the model
    once, not twice.
    """
    skip = {normalize(text) for text in exclude}
    existing = memories(store, user_id)
    if not existing:
        # Nothing to rank. Checked locally first so the common case — a user who
        # has never taught it anything — costs no embeddings call at all.
        return []
    if not indexed(store):
        # No embeddings: nothing can be ranked, so fall back to recency rather
        # than pretending. The CLI prints that this is what happened.
        return [m for m in existing if normalize(m.text) not in skip][:limit]
    # Ask for more than we need: near-misses are dropped by the threshold and by
    # exclude, and we would rather fill the budget than return three of five.
    items = store.search(namespace(user_id), query=query, limit=max(limit * 3, limit))
    found = []
    for item in items:
        text = item.value.get("text", "")
        if item.score is None or item.score < min_score or normalize(text) in skip:
            continue
        found.append(Memory(key=item.key, text=text, score=float(item.score),
                            created=str(item.created_at)))
    return found[:limit]


# Words the method-name regex would otherwise collect as "methods".
NOT_METHODS = frozenset({"if", "for", "while", "switch", "catch", "function", "return", "constructor",
                         "describe", "it", "before", "beforeEach", "after", "afterEach", "then", "async"})

# Selenium calls worth naming in the profile: they say what the file *does*.
SELENIUM_API = ("sendKeys", "click", "getText", "getAttribute", "isDisplayed", "executeScript",
                "switchTo", "alert", "sleep", "wait", "until", "frame", "window", "upload")


def _spaced(name: str) -> str:
    """usernameInput -> 'username input'. Embeddings read words, not identifiers."""
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", name).replace("_", " ").lower()


def recall_query(source_path: str, classification: Classification, source: str) -> str:
    """What we ask the store: a short profile of the file, in words.

    The obvious query — paste the file in — is measurably bad, and
    scripts/calibrate_recall.py is where that was found out: a page of raw
    TypeScript embeds to "some Selenium code", so every memory scores the same
    mid-range number and nothing separates. Embeddings compare *meaning*, and
    the meaning of a test file is in its names: what kind of file it is, the
    class, the test titles, the methods, the locator strategies, the element
    ids. camelCase is split into words for the same reason.

    So this builds that profile and asks with it. On the sample suite it is the
    difference between an unusable ranking and a working one; the numbers are
    in docs/long-term-memory.md.
    """
    kind = "page object" if classification.runner == "none" else f"{classification.runner} test spec"
    lines = [f"{kind} {Path(source_path).name}"]
    classes = re.findall(r"\bclass\s+(\w+)", source)
    if classes:
        lines.append("classes: " + ", ".join(_spaced(c) for c in classes))
    titles = re.findall(r"\b(?:describe|it)\s*\(\s*[\'\"`]([^\'\"`]+)", source)
    if titles:
        lines.append("tests: " + "; ".join(titles[:12]))
    found = re.findall(r"\b(?:async\s+)?(\w+)\s*\([^)]*\)\s*(?::[^={]+)?\{", source)
    methods = [m for m in dict.fromkeys(found) if m not in NOT_METHODS][:15]
    if methods:
        lines.append("methods: " + ", ".join(_spaced(m) for m in methods))
    strategies = sorted(set(re.findall(r"\bBy\.(\w+)", source)))
    if strategies:
        lines.append("locators: " + ", ".join(strategies))
    api = [_spaced(a) for a in SELENIUM_API if a in source]
    if api:
        lines.append("selenium api: " + ", ".join(api))
    elements = sorted(set(re.findall(r"By\.(?:id|name|css|className)\([\'\"]([^\'\"]{1,30})[\'\"]", source)))
    if elements:
        lines.append("elements: " + ", ".join(elements[:12]))
    return "\n".join(lines)

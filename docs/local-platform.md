# Step 10.1 — the local platform: the same graph, behind a server

> **In one line:** `langgraph dev` starts the LangGraph Platform server on your
> laptop, reads `langgraph.json` to find the graphs, and serves them over HTTP
> so LangGraph Studio can run and inspect them — with **no change to the graph
> itself**, only to who calls it.

This is the first step of Phase 10 (deploy, playground, monitor). It ships no
new agent behaviour. It ships a second front end.

---

## 1. The problem

Every phase up to here has had exactly one way in: `cli.py`, a program you run.
That is a fine way to use an agent and a bad way to *ship* one. A program on
your laptop cannot be a URL you send somebody, it has no threads anyone else can
resume, and the only person who can watch a run is the person who typed it.

LangGraph Platform is the other end of that. It is an HTTP server that owns:

- **the graphs** — imported from your code, published as *assistants*,
- **the checkpointer** — so threads, `interrupt()` and resume work over the wire,
- **the store** — so long-term memory works, vector index and all,
- **a queue and workers** — so a run is a job you start and poll, not a blocked
  terminal.

`langgraph dev` is that exact server running locally, in memory. Getting the
graph onto it is 10.1; putting it in the cloud is 10.2; giving it a face is 10.3.

**The important thing to notice: nothing in `graph.py` or `suite_graph.py`
changed for this step.** The graph was already a graph. What this step adds is
a *description* of the graph that a server can read — and that turns out to have
sharper corners than it looks.

---

## 2. What you type

```bash
uv sync                         # langgraph-cli[inmem] is a dev dependency now
uv run langgraph dev            # opens Studio in your browser
```

You get:

```
- 🚀 API: http://127.0.0.1:2024
- 🎨 Studio UI: https://smith.langchain.com/studio/?baseUrl=http://127.0.0.1:2024
- 📚 API Docs: http://127.0.0.1:2024/docs
```

Studio is a hosted web app pointed at *your* localhost — your code and your keys
never leave the machine. Pick the **convert** assistant, put a path in
`source_path`, and press submit:

```json
{ "source_path": "samples/selenium-suite/pages/LoginPage.ts",
  "output_path": "out/10.1/pages/LoginPage.ts" }
```

You watch `intake → recall → risk_review → convert → validate → critic` light up
node by node, and you can click any one of them to see the state that went in
and came out. Set `ask_risks: true` and the run *stops* and asks you the
question, in the UI, exactly as 7.2 does in the terminal.

`langgraph dev` also reloads when you save a file, which makes it the fastest
way there is to poke at a node.

---

## 3. `langgraph.json`

Three blocks, and every one of them is a promise about this repository:

```json
{
  "python_version": "3.12",
  "dependencies": ["."],
  "graphs": {
    "convert": { "path": "./src/selenium2playwright/server.py:convert_graph", "description": "…" },
    "suite":   { "path": "./src/selenium2playwright/server.py:suite_graph",   "description": "…" }
  },
  "env": ".env",
  "store": {
    "index": {
      "embed": "./src/selenium2playwright/server.py:embed_memories",
      "dims": 1536,
      "fields": ["text"]
    }
  }
}
```

- **`dependencies: ["."]`** — this project is the app. Locally the server just
  uses your venv; the entry matters when 10.2 builds an image.
- **`graphs`** — `name: "file.py:attribute"`. The name is what Studio and the
  SDK call the assistant. `description` is shown to a human, so it says what the
  inputs are.
- **`env: ".env"`** — the same gitignored file `env.py` already reads. The
  server loads it, so the keys reach the graph without being typed anywhere new.
- **`store.index`** — the platform's store is created by the platform, but *how
  to index it* is ours to say. This is what makes 7.3's semantic recall work
  over HTTP.

`tests/test_platform.py` checks every path in this file resolves, that both
graphs are published, and that `dims`/`fields` still match the code — because
none of those are checked until a server starts.

---

## 4. The sharp edge: a factory's signature is its API

`langgraph.json` cannot point at `graph.py:build_graph`, and the reason is worth
knowing in detail because one half of it fails loudly and the other half does
not fail at all.

The loader accepts a compiled graph, or a **callable** that returns one. If it
is a callable, it reads the signature and allows at most two parameters — a
`RunnableConfig` and a `ServerRuntime` — identified **by their type
annotations** (`langgraph_api/_factory_utils.py:_classify_factory`). Our two
builders have neither annotation:

| Builder | Parameters | What the loader does |
| --- | --- | --- |
| `build_graph(checkpointer=None, store=None)` | two, unannotated | cannot tell which is which → **`ValueError` at server start** |
| `build_suite_graph(store=None)` | one, unannotated | assumes it must be the config → **passes a `RunnableConfig` as `store`**, silently |

The second one is the dangerous one. No error, no warning; the graph runs with a
dict where its store should be, and long-term memory quietly does something
undefined. So `server.py` exists, and it holds two zero-argument wrappers:

```python
def convert_graph():
    return build_graph()          # no checkpointer, no store — see below
```

Zero parameters is the only signature with nothing to guess about.

---

## 5. The other half of that rule: bring no database

`build_graph()` and `build_suite_graph()` are called with **no checkpointer and
no store**, and that is not laziness — it is the contract. The platform creates
both and injects them at run time (`langgraph_api/stream.py` hands the store to
the run). Compiling your own in is ignored in the cloud, and the local dev
server refuses outright with a message that says so.

This is exactly why 7.1 and 7.3 made those parameters optional with a `None`
default instead of building the databases inside the graph. That decision was
made for the eval runner and the offline tests, and it is what makes the graph
deployable now without a line changing:

```
cli.py            ->  build_graph(checkpointer=SqliteSaver, store=SqliteStore)
langgraph dev     ->  build_graph()                       + the platform's own
eval runner       ->  build_graph()                       + nothing at all
```

One graph, three owners of its persistence.

---

## 6. The embeddings entry is a function, not a factory

`store.index.embed` may name a module attribute, and what the loader does with
it is easy to get backwards. It calls `ensure_embeddings` on whatever it finds,
and `ensure_embeddings` treats **a plain callable as a `texts -> vectors`
function**, not as something that returns an embeddings model.

So pointing it at `llm.make_embeddings` would be wrong twice over: the loader
would call it with a list of strings, and it would hand back a model object
where a list of vectors belongs. What goes in `langgraph.json` is:

```python
def embed_memories(texts):
    return memory_embeddings().embed_documents(list(texts))
```

with `memory_embeddings()` cached (`lru_cache`) and **lazy** — the server names
this module at start-up, and converting a file needs no embeddings key at all.

Going through our own function rather than writing `"openai:text-embedding-3-small"`
into the JSON is what keeps `S2P_EMBEDDINGS` the single place that chooses the
model. The one thing that cannot follow it is `dims`, which a JSON file cannot
compute: it is written down, and a test pins it to
`llm.EMBEDDING_DIMS[env.DEFAULT_EMBEDDINGS]` so changing the default without
changing the JSON fails in the test suite instead of at run time.

If `S2P_EMBEDDINGS=off` — a supported answer everywhere else in this project —
`embed_memories` raises and names both settings that disagree, because
`langgraph.json` has already asked for an index and an index cannot be built
without a model.

---

## 7. The bug this step found: a wrapped store looks unindexed

`store.recall` asks `indexed(store)` whether the store can rank by meaning, and
falls back to plain recency when it cannot. That check was:

```python
return bool(getattr(store, "index_config", None))
```

The store a node is handed on the platform is a `BatchedStore` — a thin batching
adapter around the real one. It forwards `search`. It has **no `index_config`
attribute at all**. So the check answered "not indexed", and every recall on the
platform would have silently degraded to "the three most recent memories",
scoring nothing, with no error anywhere and a demo that looks like it works.

The fix unwraps one layer:

```python
inner = getattr(store, "_store", None)
return bool(getattr(store, "index_config", None)
            or (inner is not None and getattr(inner, "index_config", None)))
```

Measured afterwards, against the running server: a preference written through
the platform's own store API came back to the `recall` node with a real score of
**0.4986**, not as an unranked recent item.

**This is the shape of bug this whole phase exists to find.** Nothing was
broken in the graph. Something was broken in an assumption the graph made about
its surroundings, and only running it somewhere else could show that.

---

## 8. What was actually checked (live, `langgraph dev` on port 2024)

| Check | Result |
| --- | --- |
| Both graphs load | `convert` and `suite` published as assistants |
| Single conversion over HTTP (`POST /runs/wait`) | 3 attempts, **4/4 gates + critic pass**, 6 honest locator TODOs, `needs-review`; 16,649 tokens with 5,380 read from cache |
| Platform store, vector index | a memory written via `PUT /store/items` searched back at score **0.5224** |
| Semantic recall inside the graph | the same memory reached the `recall` node at **0.4986** (this is the §7 fix) |
| Threads + `interrupt()` | `ask_risks: true` on a thread returned `__interrupt__` with the dialogs question, its three options and its default — the Studio prompt |
| Suite graph over HTTP | `only: ["LoginPage.ts", "login.spec.ts"]` → **2 waves** in dependency order, both `passed` in 1 attempt, whole tree compiled, `conversion-report.md` written |

The suite run is the one worth dwelling on: fan-out with `Send`, the reducer,
the subgraph, the wave ordering and 9.3's whole-tree compile all ran unchanged
inside a server that knew nothing about any of them.

---

## 9. What this step does *not* fix

**The input is a server-side path.** `source_path` is read with
`Path(...).read_text()` on whatever machine the server is on. Locally that is
your machine, so Studio is genuinely usable today. In the cloud it is a machine
with none of your files on it, and the `samples/` in the image are the only
paths that will ever resolve. **Accepting inline source text is therefore the
first real piece of 10.2**, not an afterthought — and 10.3's paste box cannot
exist without it.

**Studio's input form shows all 30 state keys.** `ConversionState` is
`total=False`, so every key is optional and every key is offered, including the
ones only nodes ever write. It is usable — `source_path` is first, and the rest
can be ignored — but an explicit `input_schema` on the `StateGraph` would make
the form say what a caller may actually pass. That change filters incoming keys,
so it needs to be made with the eval runner and the 314 tests in view; it is
listed here rather than done quietly.

**The suite graph's recursion limit.** `cli.py` computes `2 * waves + 6` for a
suite run. Over HTTP the platform's default of 25 applies instead, which covers
about nine waves. The two-wave run above was nowhere near it; a deep suite in
Studio would need the limit passed on the request.

---

## 10. Check yourself

1. `langgraph.json` names `server.py:convert_graph` rather than
   `graph.py:build_graph`. Give both reasons — the loud one and the silent one.
2. `build_graph()` is called here with no checkpointer, when `s2p convert
   --thread` clearly needs one. Where does the checkpointer come from, and what
   happens if you pass your own?
3. Why is `embed_memories` a function that takes texts, when everywhere else in
   this project the embeddings model is made by `llm.make_embeddings()`?
4. A `BatchedStore` forwards `search` faithfully. Explain how semantic recall
   could still have broken on it, and why no test would have caught it.
5. You deploy this to the cloud tomorrow with no other change and paste
   `pages/LoginPage.ts` into Studio. What exactly happens, and why?

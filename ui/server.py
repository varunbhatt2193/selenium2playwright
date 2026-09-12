"""The web playground: a small HTTP server in front of `playground.py`.

    uv run --group ui uvicorn ui.server:app --port 8501

`ui/app.py` (Streamlit) was the first page in front of the agent. It works, and
it looks like Streamlit — which is the one thing a page that is supposed to
*sell* the agent cannot afford. This server is the other half of the
replacement: `ui/web` is a React page built with Vite, and this file is the
handful of JSON routes it talks to.

It holds no logic of its own. Every decision — what a request may contain, how
a run is streamed, how a scorecard is read out of the final state, what a 403
means in a sentence — is still `selenium2playwright/playground.py`, with its
tests. The routes here translate between that module and HTTP:

    GET  /api/session       who this browser is, the samples, today's budget
    GET  /api/limits        today's budget, again (the page refreshes it)
    POST /api/convert       one file  → a stream of progress, then the scorecard
    POST /api/feedback      👍 / 👎 on a finished run
    POST /api/suite/plan    uploaded files → the wave plan, before any spend
    POST /api/suite/convert a tree → a stream of files landing, then the result
    POST /api/suite/zip     the converted tree → a zip with the report inside

Two things it deliberately does not do, and they are the same two the Streamlit
page did not do:

**It does not hand out a key.** `S2P_DEMO_KEY` is read on this machine and used
on this machine. A visitor gets a URL, not a credential.

**It does not ask the graph anything a stranger may not ask.** No server paths,
no writes to shared memory. `guard.py` refuses all of that anyway; the page
simply never offers it, and `playground.check_input` says so under the box
before the request is sent.

Progress travels as **server-sent events**: one `data: {json}` line per thing
worth telling the person watching. A conversion is a minute of silence
otherwise, and the reflection loop going round ("Converting to Playwright —
attempt 2") is the most interesting thing on the page.
"""

from __future__ import annotations

import json
import os
import queue
import re
import sys
import threading
import traceback
from collections.abc import Iterator
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from selenium2playwright import playground as pg
from selenium2playwright.suite import is_input

# The built page. Vite writes `index.html` plus hashed files under `assets/`;
# the Dockerfile builds it in a Node stage and copies only this directory across.
DIST = Path(__file__).resolve().parent / "web" / "dist"

# What a visitor id this server minted looks like. Anything else in the header
# is replaced, not trusted: the id ends up in a Redis key on the far side, and
# `guard._VISITOR_OK` is the server's rule — this is the same rule, one hop
# earlier, restricted to the shape `playground.new_visitor` produces.
VISITOR = re.compile(r"^pg-[0-9a-f]{12}$")

# A thread id is a path segment in the SDK's request to the deployment, so it
# must be one before it is allowed to be one.
THREAD = re.compile(r"^[0-9a-f-]{36}$")

app = FastAPI(title="Selenium → Playwright playground", docs_url=None, redoc_url=None)


# --- helpers ------------------------------------------------------------------


def visitor_of(header: str | None) -> str:
    """The visitor this request is from, minting one if the header is not ours."""
    supplied = (header or "").strip()
    return supplied if VISITOR.match(supplied) else pg.new_visitor()


def limits_view(visitor: str) -> dict[str, Any]:
    """`GET /limits` plus the two sentences the page prints about it."""
    snapshot = pg.fetch_limits(visitor=visitor)
    return {**snapshot, "line": pg.budget_line(snapshot), "visitor_line": pg.visitor_line(snapshot)}


def sample_view(sample: pg.Sample) -> dict[str, Any]:
    companion = sample.context()
    return {
        "name": sample.name,
        "blurb": sample.blurb,
        "source": sample.read(),
        "companion_name": next(iter(companion), ""),
        "companion_text": next(iter(companion.values()), ""),
    }


def card_view(card: pg.Scorecard) -> dict[str, Any]:
    """A `Scorecard` as JSON, with the two properties the page also wants."""
    return {**asdict(card), "passed": card.passed, "gates_line": card.gates_line}


def plan_view(plan: pg.SuitePlan) -> dict[str, Any]:
    return {**asdict(plan), "files": plan.files, "line": plan.line,
            "found": plan.found, "wave_lines": plan.wave_lines}


def result_view(result: pg.SuiteResult) -> dict[str, Any]:
    # `gates_line` is a property, and `asdict` only copies fields — so the rows
    # in this final payload arrived at the page without the one the table's
    # GATES column reads, and the column went blank the moment a run finished.
    # The live `file` events send it explicitly; this is the same courtesy.
    return {**asdict(result), "totals": result.totals, "passed": result.passed,
            "headline": result.headline,
            "rows": [{**asdict(row), "gates_line": row.gates_line} for row in result.rows]}


# How often to put a byte on an idle stream. A proxy between this server and
# the browser will close a connection that goes quiet, and a conversion goes
# quiet for a long time: a wave of four files converting in parallel emits
# nothing at all between "wave 1 starting" and the first file landing, which
# measured 95 seconds on a live run. The browser then reports a network error
# while both servers log a clean 200, because from their side nothing failed.
#
# Fifteen seconds is well inside the usual sixty-second idle limit and cheap:
# a comment line no client parses as an event.
HEARTBEAT_SECONDS = 15.0


def sse(events: Iterator[dict[str, Any]]) -> StreamingResponse:
    """Server-sent events. One JSON object per line, flushed as it happens.

    A sync generator on purpose: the SDK client is synchronous, and Starlette
    runs a sync iterator in a worker thread, so a minute-long stream does not
    block the event loop for every other visitor.

    The upstream iterator blocks, so it cannot be asked "anything yet?" — it is
    drained on a second thread into a queue, and the queue is what this waits
    on, with a timeout that turns silence into a heartbeat instead of a dropped
    connection. The thread is a daemon: if the client goes away mid-run, this
    generator is closed and nothing is left holding the process open.
    """
    def body() -> Iterator[bytes]:
        pipe: queue.Queue = queue.Queue(maxsize=64)
        DONE = object()

        def drain() -> None:
            try:
                for event in events:
                    pipe.put(event)
            except Exception as exc:            # the upstream failed, not us
                pipe.put(exc)
            finally:
                pipe.put(DONE)

        worker = threading.Thread(target=drain, daemon=True, name="sse-drain")
        worker.start()
        while True:
            try:
                item = pipe.get(timeout=HEARTBEAT_SECONDS)
            except queue.Empty:
                yield b": keep-alive\n\n"      # a comment: no event, just a byte
                continue
            if item is DONE:
                return
            if isinstance(item, Exception):
                raise item
            yield f"data: {json.dumps(item)}\n\n".encode()

    return StreamingResponse(
        body(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def failure(exc: Exception) -> dict[str, Any]:
    """Every failure here is somebody else's server, said in a sentence.

    When `playground.explain` recognizes the failure, the sentence was written
    for the person watching and says what they can do about it. When it does
    not, its fallback is the exception's own text — a URL, a path, a header,
    whatever the SDK put there. That is worth having, and worth having *here*:
    it goes to this server's log, which `fly logs` reads, and the page is told
    that something broke rather than being handed the details.
    """
    if not pg.recognized(exc):
        traceback.print_exception(exc, file=sys.stderr)
        return {"kind": "error", "message": (
            "Something went wrong on this server, and it has been logged. Try "
            "again — if it keeps happening, the deployment needs a look.")}
    wait = pg.retry_after(exc)
    message = pg.explain(exc) + (f" You can try again in about {wait}s." if wait else "")
    return {"kind": "error", "message": message}


# --- session and budget -------------------------------------------------------


@app.get("/ok")
def ok() -> dict[str, bool]:
    """Fly's health check. `/` would look healthy with the page missing."""
    return {"ok": True}


@app.get("/api/session")
def session(x_s2p_visitor: str | None = Header(default=None)) -> dict[str, Any]:
    """Everything the page needs before it can draw: the visitor it is, the
    sample buttons, and what is left of today's budget."""
    visitor = visitor_of(x_s2p_visitor)
    return {
        "visitor": visitor,
        "backend": pg.backend_url(),
        "key_present": bool(pg.demo_key()),
        "samples": [sample_view(s) for s in pg.samples()],
        "limits": limits_view(visitor),
    }


@app.get("/api/limits")
def limits(x_s2p_visitor: str | None = Header(default=None)) -> dict[str, Any]:
    return limits_view(visitor_of(x_s2p_visitor))


# --- one file -----------------------------------------------------------------


class ConvertRequest(BaseModel):
    source: str = ""
    filename: str = ""
    companion_name: str = ""
    companion_text: str = ""
    refinement: str = ""
    # Present on a refine — turn two of the same conversation, where the graph
    # still has the last draft and every instruction so far (step 7.1).
    thread_id: str = ""


def convert_events(body: ConvertRequest, visitor: str) -> Iterator[dict[str, Any]]:
    """Run one conversion and narrate it. The last event carries the answer."""
    context = ({body.companion_name.strip(): body.companion_text}
               if body.companion_text.strip() and body.companion_name.strip() else {})
    client = pg.client(visitor=visitor)
    seen: list[str] = []
    final: dict[str, Any] = {}
    run_id = ""
    try:
        thread_id = body.thread_id or client.threads.create()["thread_id"]
        request = pg.payload(body.source, body.filename, context=context,
                             refinement=body.refinement.strip())
        for update in pg.stream(client, thread_id, request):
            if update.kind == "run":
                run_id = update.run_id
                yield {"kind": "run", "run_id": run_id, "thread_id": thread_id}
            elif update.kind == "node":
                seen.append(update.node)
                yield {"kind": "node", "node": update.node,
                       "label": pg.progress_label(update.node, seen)}
            elif update.kind == "state":
                final = update.state
    except Exception as exc:  # noqa: BLE001 — every failure here is somebody else's server
        yield failure(exc)
        return
    card = pg.scorecard(final)
    name = pg.download_name(body.filename)
    yield {
        "kind": "done",
        "thread_id": thread_id,
        "run_id": run_id,
        "trail": [pg.progress_label(node, seen[:i]) for i, node in enumerate(seen, start=1)],
        "card": card_view(card),
        "diff": pg.unified_diff(body.source, card.code, body.filename or "selenium.ts", name)
        if card.code else "",
        "download_name": name,
    }


@app.post("/api/convert")
def convert(body: ConvertRequest,
            x_s2p_visitor: str | None = Header(default=None)) -> StreamingResponse:
    complaint = pg.check_input(body.source, body.filename,
                               companion_name=body.companion_name,
                               companion_text=body.companion_text,
                               refinement=body.refinement)
    if complaint:
        raise HTTPException(status_code=400, detail=complaint)
    if body.thread_id and not THREAD.match(body.thread_id):
        raise HTTPException(status_code=400, detail="That is not a thread id.")
    return sse(convert_events(body, visitor_of(x_s2p_visitor)))


class FeedbackRequest(BaseModel):
    run_id: str
    score: float = Field(ge=0.0, le=1.0)
    comment: str = ""
    source_text: str = ""
    source_path: str = ""


@app.post("/api/feedback")
def feedback(body: FeedbackRequest,
             x_s2p_visitor: str | None = Header(default=None)) -> dict[str, Any]:
    answer = pg.send_feedback(
        body.run_id, body.score, comment=body.comment, source_text=body.source_text,
        source_path=body.source_path, visitor=visitor_of(x_s2p_visitor),
    )
    answer.setdefault("detail", "Thank you." if answer.get("stored") else "Not recorded.")
    return answer


# --- a whole suite ------------------------------------------------------------


class Upload:
    """What `playground.tree_from_uploads` expects: a `.name` and `.getvalue()`."""

    def __init__(self, name: str, data: bytes):
        self.name, self._data = name, data

    def getvalue(self) -> bytes:
        return self._data


def patterns(only: str) -> list[str]:
    return [p.strip() for p in only.split(",") if p.strip()]


@app.post("/api/suite/plan")
async def suite_plan(files: list[UploadFile] = File(default=[]),
                     only: str = Form(default=""),
                     x_s2p_visitor: str | None = Header(default=None)) -> dict[str, Any]:
    """`s2p scan` over what was dropped, before a single model is called.

    The tree goes back to the page as text — it is what the page will send to
    `/api/suite/convert` — so this server keeps nothing between two requests.
    """
    uploads = [Upload(f.filename or "", await f.read()) for f in files]
    tree, complaint = pg.tree_from_uploads(uploads)
    if complaint:
        raise HTTPException(status_code=400, detail=complaint)
    if not tree:
        raise HTTPException(status_code=400, detail="Drop a zip of the folder, or its files.")
    return planned(tree, only, visitor_of(x_s2p_visitor))


def planned(tree: dict[str, str], only: str, visitor: str) -> dict[str, Any]:
    """The wave plan for a tree, and whether today's budget can pay for it."""
    try:
        plan = pg.plan_tree(tree, patterns(only))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not plan.convert:
        raise HTTPException(
            status_code=400,
            detail="Nothing in there can be converted" + (" with that filter." if only else "."),
        )
    snapshot = pg.fetch_limits(visitor=visitor)
    return {"tree": tree, "plan": plan_view(plan),
            "unaffordable": pg.affordable(snapshot, plan.billable)}


@app.get("/api/suite/sample")
def suite_sample(only: str = "",
                 x_s2p_visitor: str | None = Header(default=None)) -> dict[str, Any]:
    """The sample suite, read from this machine, as a planned tree.

    The same twelve files the README's numbers are about. Read here and sent
    to the graph as text like any upload — so it is metered like any upload.
    """
    root = pg._SUITE
    if not root.is_dir():
        raise HTTPException(status_code=404, detail="The sample suite is not on this server.")
    tree = {
        path.relative_to(root).as_posix(): path.read_text(encoding="utf-8")
        for path in sorted(root.rglob("*"))
        if path.is_file() and is_input(path.name)
    }
    return planned(tree, only, visitor_of(x_s2p_visitor))


class SuiteRequest(BaseModel):
    tree: dict[str, str]
    only: str = ""
    parallel: int = Field(default=4, ge=1, le=16)
    attempts: int = Field(default=3, ge=1, le=3)
    model: str = ""


def suite_events(body: SuiteRequest, visitor: str) -> Iterator[dict[str, Any]]:
    """Run a suite and tick files off as they land."""
    only = patterns(body.only)
    try:
        plan = pg.plan_tree(body.tree, only)
    except ValueError as exc:
        yield {"kind": "error", "message": str(exc)}
        return
    client = pg.suite_client(visitor=visitor)
    landed = 0
    final: dict[str, Any] = {}
    try:
        thread_id = client.threads.create()["thread_id"]
        request = pg.suite_payload(only=only, tree=body.tree)
        context = pg.suite_context(model=body.model, max_attempts=body.attempts, user_id="")
        config = pg.suite_config(waves=len(plan.waves), parallel=body.parallel)
        yield {"kind": "start", "files": plan.files, "waves": len(plan.waves),
               "found": plan.found}
        for update in pg.stream(client, thread_id, request, assistant="suite",
                                config=config, context=context):
            if update.kind == "node":
                for row in pg.rows_in(update.update):
                    landed += 1
                    yield {"kind": "file", "landed": landed, "of": plan.files,
                           "row": {**asdict(row), "gates_line": row.gates_line}}
                # `next_wave` is the one node whose label is not a constant: it
                # says which wave, and what is in it. The number comes from the
                # node's own update — it returns `{"wave": n}` and nothing else
                # — so the line the page shows and the wave the graph is about
                # to dispatch are the same n. `wave_label` returns "" for the
                # extra run at the end, and an empty label is not sent.
                if update.node == "next_wave":
                    number = (update.update or {}).get("wave", 0)
                    label = pg.wave_label(plan, number)
                    if label:
                        yield {"kind": "node", "node": "next_wave",
                               "label": label, "wave": number}
                elif update.node in pg.SUITE_NODE_LABELS and update.node != "convert_file":
                    yield {"kind": "node", "node": update.node,
                           "label": pg.SUITE_NODE_LABELS[update.node]}
            elif update.kind == "state":
                final = update.state
    except Exception as exc:  # noqa: BLE001
        yield failure(exc)
        return
    result = pg.suite_result(final)
    yield {"kind": "done", "result": result_view(result)}


@app.post("/api/suite/convert")
def suite_convert(body: SuiteRequest,
                  x_s2p_visitor: str | None = Header(default=None)) -> StreamingResponse:
    if not body.tree:
        raise HTTPException(status_code=400, detail="There is nothing to convert.")
    return sse(suite_events(body, visitor_of(x_s2p_visitor)))


class ZipRequest(BaseModel):
    tree: dict[str, str]
    markdown: str = ""


@app.post("/api/suite/zip")
def suite_zip(body: ZipRequest) -> Response:
    if not body.tree:
        raise HTTPException(status_code=400, detail="There is nothing to download.")
    return Response(
        pg.converted_zip(body.tree, body.markdown),
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="playwright-suite.zip"'},
    )


# --- the page -----------------------------------------------------------------
#
# Registered last so `/api/*` and `/ok` win. Vite's output is `index.html` plus
# hashed files under `assets/`, so the page itself is served uncached and the
# assets forever.

if (DIST / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")


def built_file(path: str) -> Path | None:
    """The file `path` names *inside* `dist`, or None if it names anything else.

    `DIST / path` is not a check. uvicorn percent-decodes the URL before
    Starlette binds `{path:path}`, so `%2e%2e%2f` arrives here as `../` and
    climbs out of the build directory; and pathlib lets an absolute segment
    replace the left-hand side outright, so `//etc/passwd` becomes `/etc/passwd`.
    Either one, on Fly, reaches `/proc/self/environ` and the key inside it.

    So normalize first, then confirm the answer is still under `dist`. The
    trailing separator is the point of `root + os.sep`: without it a sibling
    directory named `dist-evil` starts with `dist` and passes.

    Written in `os.path` rather than pathlib on purpose, and this is the whole
    reason: the same check spelled `(DIST / path).resolve()` and
    `DIST not in candidate.parents` is one CodeQL does not recognize — it reads
    `Path.resolve()` as the filesystem access the query is warning about, so
    `py/path-injection` survives a fix that works. `realpath` plus `startswith`
    is the shape its own remediation shows, which keeps the Security tab a list
    of things that are actually wrong.
    """
    if not path:
        return None
    root = os.path.realpath(DIST)
    candidate = os.path.realpath(os.path.join(root, path))
    if not candidate.startswith(root + os.sep) or not os.path.isfile(candidate):
        return None
    return Path(candidate)


# HEAD as well as GET: a browser probes a <video> source before it streams it.
@app.api_route("/{path:path}", methods=["GET", "HEAD"], include_in_schema=False)
def page(path: str) -> Response:
    """The React page, for any path that is not the API.

    `favicon.svg` and friends live at the root of `dist`; everything else is the
    single page, which routes itself.
    """
    if path.startswith("api/"):
        raise HTTPException(status_code=404)
    file = built_file(path)
    if file:
        return FileResponse(file)
    index = DIST / "index.html"
    if not index.is_file():
        return JSONResponse(
            {"detail": "The page is not built. Run `npm ci && npm run build` in ui/web."},
            status_code=503,
        )
    return FileResponse(index, headers={"Cache-Control": "no-cache"})

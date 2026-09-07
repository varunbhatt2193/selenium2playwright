"""Step 10.4 — two routes the graph API does not have, and one it should.

LangGraph Platform serves a fixed API: assistants, threads, runs, store. That is
almost everything a client needs, and it is missing two things a *public* demo
needs, so `langgraph.json` points its `http.app` at the Starlette app below and
the server merges these routes in beside its own.

    GET  /limits    what is left of today's budget
    POST /feedback  👍 / 👎 on a finished run

`/limits` exists so the playground can be honest before it wastes anybody's
time. Without it the only way to discover the demo is out of budget is to submit
a file, wait, and be refused — which reads as "this is broken" rather than "this
is busy". A demo that says "38 conversions left today" up front is a demo that
looks maintained.

`/feedback` is the other half of `feedback.py`: the button has to land
somewhere. It takes the run id the SDK already returned to the client, so
nothing has to be threaded through the graph.

Both are ordinary HTTP, deliberately. The playground in 10.3 is a Streamlit app
that will call them with `httpx`, and a hiring manager reading this repo can
call them with `curl`.
"""

from __future__ import annotations

import asyncio

from langgraph_sdk import Auth
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from selenium2playwright import feedback, guard, limits


def _identity(request: Request) -> dict:
    """Who is asking. Raises `Auth.exceptions.HTTPException` if they may not ask.

    **These routes authenticate themselves.** The platform's auth middleware
    guards its own API and does not run for routes merged in through
    `http.app` — asking the running server for `POST /feedback` with no
    credentials reached the handler. So `guard.identify` is called here
    explicitly rather than reading an identity the middleware was assumed to
    have attached. Getting this wrong is silent: the route works, for everyone.
    """
    authorization = request.headers.get("authorization")
    headers = {k.lower().encode(): v.encode() for k, v in request.headers.items()}
    return guard.identify(headers, authorization)


def _refused(exc: Auth.exceptions.HTTPException) -> JSONResponse:
    return JSONResponse(
        {"error": exc.detail}, status_code=exc.status_code, headers=exc.headers or None
    )


async def get_limits(request: Request) -> JSONResponse:
    """What today's budget looks like right now."""
    try:
        _identity(request)
    except Auth.exceptions.HTTPException as exc:
        return _refused(exc)
    return JSONResponse(await limits.snapshot())


async def post_feedback(request: Request) -> JSONResponse:
    """Record 👍 or 👎 for one run, and queue the input if it was 👎.

    Returns 200 with a false `stored` flag, rather than an error, when LangSmith
    is not configured. A missing flywheel is a deployment's problem, not the
    visitor's, and there is nothing useful for them to do about it — so the
    button should not turn red at somebody who did nothing wrong.

    (Route docstrings are parsed as OpenAPI YAML by the platform, so a literal
    `word` followed by a colon and a value in here is a schema, not prose. It
    fails at start-up with a ScannerError that names this file and not the
    reason. Worth one sentence to save the next person the search.)
    """
    try:
        user = _identity(request)
    except Auth.exceptions.HTTPException as exc:
        return _refused(exc)

    # Feedback costs no model tokens, so it is not charged to the day's budget —
    # but it does write a LangSmith row, and a row anyone can create without
    # limit is a row anyone can create a million of.
    decision = await limits.tap(user["identity"], unlimited="owner" in user["permissions"])
    if not decision.allowed:
        return JSONResponse(
            {"error": decision.reason}, status_code=429,
            headers={"Retry-After": str(decision.retry_after)},
        )

    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return JSONResponse({"error": "Expected a JSON body."}, status_code=400)

    run_id = str(body.get("run_id") or "").strip()
    if not run_id:
        return JSONResponse({"error": "`run_id` is required."}, status_code=400)

    score = body.get("score")
    if score not in (0, 1, 0.0, 1.0, True, False):
        return JSONResponse(
            {"error": "`score` must be 1 (👍) or 0 (👎)."}, status_code=400
        )

    # `to_thread`, not a direct call: LangSmith's Client is synchronous, and a
    # synchronous network call inside an async handler blocks the whole event
    # loop — every other request on this worker waits for LangSmith. `langgraph
    # dev` catches it and raises ("Blocking call to socket.socket.connect"),
    # which is how this was found; in production it would simply have been a
    # server that stalls under load for no visible reason.
    result = await asyncio.to_thread(
        feedback.record,
        run_id,
        float(score),
        comment=str(body.get("comment") or "")[:2000],
        source_text=str(body.get("source_text") or "")[:262144],
        source_path=str(body.get("source_path") or "")[:255],
        visitor=user.get("visitor", ""),
    )
    return JSONResponse(result.as_dict())


app = Starlette(
    routes=[
        Route("/limits", get_limits, methods=["GET"]),
        Route("/feedback", post_feedback, methods=["POST"]),
    ]
)

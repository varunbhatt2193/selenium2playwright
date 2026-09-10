"""Step 10.4 — who may call the deployment, and what they may ask it to do.

Steps 10.1–10.2 put the graphs behind a server and the server in a data centre.
That server has no opinion about who is calling it. `POST /threads` answered 200
to anyone on the internet, which is fine for a private experiment and not fine
for a link on a CV.

LangGraph Platform has a place for this, and the shape is worth understanding
because it is two separate questions that people usually run together:

    authentication   who is this?          -> @auth.authenticate, once per request
    authorization    may they do THAT?     -> @auth.on..., per resource and action

The first returns an identity. The second gets that identity plus the thing
being attempted, and says yes, no, or "yes but only your own rows". Splitting
them is what lets the same visitor be allowed to convert a file and refused
permission to read the file next to it.

`langgraph.json` points at the `auth` object below, the CLI turns that into
`LANGGRAPH_AUTH` in the image, and the server imports it at start-up. Nothing
else in the project changes.

## The three things a stranger must not be allowed to do

**Spend without limit.** Every conversion is real tokens on a real card. The
meter is in `limits.py`; this module is where it is read.

**Read the server's disk.** The convert graph accepts `source_path` and reads
that path *on the server* when no `source_text` came with it — which is the
right behaviour when the server is your own laptop, and file disclosure when it
is a public host. `source_path: "/etc/passwd"` is a valid request. The suite
graph is worse: its whole input is server-side directories. So a demo caller
must send the file as text, and anything that names a path is refused. Not
sanitised, not sandboxed — refused, because a path is never something a remote
caller needs and a refusal has no clever bypass.

**Write the server's disk, or everyone's memory.** `output_path` writes a file.
`remember` files a preference in the long-term store, which is *shared* — one
visitor teaching the agent a bad convention would quietly degrade every later
conversion for everybody. Both are owner-only.

## Two keys, and what happens when there are none

`S2P_API_KEY` is you: no limits, every graph, all of it. `S2P_DEMO_KEY` is the
playground: metered, text-only, its own memories. `deploy/fly/deploy.sh`
generates both if they do not exist, so a deployment cannot accidentally be born
without them.

With neither set, this refuses to guess. An unconfigured *deployment* that
silently ran open is exactly the failure this step exists to prevent — so the
door is shut and the error says which variable to set. The one exception is a
local `langgraph dev` with `S2P_AUTH=off`, which is explicit, opt-in, and cannot
happen by forgetting something.
"""

from __future__ import annotations

import hmac
import os
import re
from typing import Any

from langgraph_sdk import Auth

from selenium2playwright import limits, suite

auth = Auth()

# Anything a visitor can influence ends up in a Redis key and in log lines, so
# it is constrained to a shape that cannot escape either.
_VISITOR_OK = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")

OWNER = "owner"
DEMO = "demo"

# Owner-only inputs, and why each one is on the list. Kept as data rather than a
# chain of ifs so the refusal message can name the exact field and the reason,
# which is the difference between a demo that looks broken and one that looks
# careful.
FORBIDDEN_INPUTS = {
    "context_paths": "reads files on the server; send them as context_text instead",
    # The suite graph's evidence lists (11.3b) are paths too. A visitor's suite
    # arrives as `source_tree`, and `dispatch` builds these from the workspace
    # it wrote; there is no honest reason for a request to carry them.
    "repo_paths": "reads files on the server; a suite is sent as source_tree instead",
    "caller_paths": "reads files on the server; a suite is sent as source_tree instead",
    "pending_paths": "reads files on the server; a suite is sent as source_tree instead",
    "output_path": "writes a file on the server",
    "via": "is the suite scanner's verdict, not the caller's to give",
    "suite_roots": "names directories on the server; the suite graph sets it itself",
    "remember": "writes to shared long-term memory",
    "root": "belongs to the suite graph, which reads server-side directories",
    "out_root": "belongs to the suite graph, which writes server-side directories",
}

# A demo caller may not ask for more reflection laps than the deployment's own
# default. Three is already the cap in graph.py; this stops `max_attempts: 99`
# turning one request into ninety-nine model calls.
MAX_DEMO_ATTEMPTS = int(os.environ.get("S2P_DEMO_MAX_ATTEMPTS") or 3)

# `source_path` is two things wearing one name. With no `source_text` it is a
# path the server opens — file disclosure, on a public host. *With* `source_text`
# it is only a label: the classifier, the recall query and the report all want to
# know the file is called LoginPage.ts, and none of them care where it lives.
#
# Refusing the field outright was the first version and it was wrong — it took a
# name away from every visitor to close a hole that only exists in the other
# case. So the rule is on the shape instead: a bare filename, and nothing that
# could walk anywhere. No separators, so no directories; no "..", so no escaping;
# no leading dot, so no dotfiles.
_BARE_FILENAME = re.compile(r"^(?!\.)[A-Za-z0-9._-]{1,255}$")

# Paths that must answer before anyone has a token. Fly's health check calls
# /ok every 30 seconds with no credentials; if that 401s the machine never
# becomes healthy and the deploy fails with a very confusing error.
OPEN_PATHS = ("/ok", "/info", "/metrics", "/docs", "/openapi.json")


def _configured() -> tuple[str, str]:
    return (
        os.environ.get("S2P_API_KEY", "").strip(),
        os.environ.get("S2P_DEMO_KEY", "").strip(),
    )


def enabled() -> bool:
    """Whether this module will enforce anything.

    Off only when explicitly switched off. Not "off when unconfigured" — see the
    module docstring for why that distinction is the whole point.
    """
    return os.environ.get("S2P_AUTH", "").strip().lower() not in {"off", "0", "false"}


def _same(a: str, b: str) -> bool:
    """Compare tokens without leaking their length or contents through timing.

    `==` on strings returns as soon as two bytes differ, so the time it takes to
    fail is a measurement of how much of the key was right. `compare_digest` takes
    the same time either way. This matters less for a demo key than for a bank,
    and costs one import.
    """
    return bool(a) and bool(b) and hmac.compare_digest(a, b)


def _visitor(headers: dict[bytes, bytes]) -> str:
    """A stable-enough name for one person, for the per-visitor limits.

    The playground sends `X-S2P-Visitor` (its session id), which is the honest
    answer. Failing that, the caller's IP as Fly reports it. Neither is an
    identity in any real sense — someone determined can have as many as they
    like — and that is what the *global* daily budget is for. These two limits
    are about fairness between ordinary visitors, not about defeating an
    attacker.
    """
    get = lambda name: (headers.get(name.encode()) or b"").decode(errors="replace")
    supplied = get("x-s2p-visitor").strip()
    if _VISITOR_OK.match(supplied):
        return supplied
    ip = get("fly-client-ip") or get("x-forwarded-for").split(",")[0].strip()
    return ip[:64] if ip else "anonymous"


def identify(headers: dict[bytes, bytes], authorization: str | None) -> dict[str, Any]:
    """Verify a bearer token and say who it belongs to. Raises on refusal.

    Split out of the authenticator below because the routes in `http_app.py`
    have to call it themselves. The platform's auth middleware covers its own
    API — assistants, threads, runs, store — and does **not** run for routes
    merged in through `http.app`. That is easy to miss and the failure is
    silent: the route simply works, for anybody. Verified by asking the running
    server for `POST /feedback` with no credentials and getting through.
    """
    if not enabled():
        return {"identity": OWNER, "permissions": ["owner"], "visitor": "local"}

    api_key, demo_key = _configured()
    if not api_key and not demo_key:
        raise Auth.exceptions.HTTPException(
            status_code=503,
            detail=(
                "This deployment has no keys configured, so it refuses every request "
                "rather than serving an open one. Set S2P_API_KEY (and optionally "
                "S2P_DEMO_KEY), or set S2P_AUTH=off for local development."
            ),
        )

    # Both spellings, because both are somebody's idiom. `langgraph-sdk` sends
    # `x-api-key` and nothing else, so a guard that only read Authorization
    # rejected our own client — the SDK is the main way anyone calls this, and
    # making it the awkward case would have been backwards. `curl` users and the
    # Streamlit playground reach for `Authorization: Bearer`.
    token = ""
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    if not token:
        token = (headers.get(b"x-api-key") or b"").decode(errors="replace").strip()
    if not token:
        raise Auth.exceptions.HTTPException(
            status_code=401,
            detail="Missing credentials: send Authorization: Bearer <key>, or x-api-key.",
        )

    if _same(token, api_key):
        return {"identity": OWNER, "permissions": ["owner"], "visitor": OWNER}
    if _same(token, demo_key):
        visitor = _visitor(headers)
        return {
            "identity": f"{DEMO}:{visitor}",
            "permissions": [DEMO],
            "visitor": visitor,
        }

    raise Auth.exceptions.HTTPException(status_code=401, detail="Invalid token.")


@auth.authenticate
async def authenticate(
    path: str, method: str, headers: dict[bytes, bytes], authorization: str | None
) -> dict[str, Any]:
    """Turn a request into an identity, or refuse it."""
    if any(path == p or path.startswith(p + "/") for p in OPEN_PATHS):
        return {"identity": "health", "permissions": [], "visitor": ""}
    return identify(headers, authorization)



def _is_owner(ctx) -> bool:
    return "owner" in (ctx.user.permissions or [])


def _run_input(value: dict) -> dict:
    """Dig the graph's input out of a run-creation payload.

    The server hands authorization handlers the whole create-run body, where the
    input the graph will actually see is nested under `kwargs`. Everything here
    tolerates it being absent or the wrong type, because this code runs before
    any validation the graph would do and must not itself be the thing that
    500s on a malformed request.
    """
    kwargs = value.get("kwargs")
    if not isinstance(kwargs, dict):
        return {}
    payload = kwargs.get("input")
    return payload if isinstance(payload, dict) else {}


@auth.on.threads.create_run
async def guard_run(ctx, value: dict) -> bool:
    """The one handler that matters: every conversion passes through here."""
    if _is_owner(ctx):
        return True

    payload = _run_input(value)

    for field, why in FORBIDDEN_INPUTS.items():
        if payload.get(field):
            raise Auth.exceptions.HTTPException(
                status_code=403,
                detail=f"`{field}` is not available on the public demo: it {why}.",
            )

    # A suite arrives as text or not at all. `root`/`out_root` stay refused
    # above — those name the server's directories — and `source_tree` is the
    # shape that carries the same information with the bytes attached and no
    # path the server would open. `suite.check_tree` is the same function the
    # page calls before sending and the graph calls before writing; this is the
    # only one of the three a stranger cannot skip.
    tree = payload.get("source_tree")
    if tree is not None:
        if not isinstance(tree, dict):
            raise Auth.exceptions.HTTPException(
                status_code=403,
                detail="`source_tree` must be an object of relative path to file text.",
            )
        complaint = suite.check_tree({str(k): str(v) for k, v in tree.items()})
        if complaint:
            raise Auth.exceptions.HTTPException(status_code=403, detail=complaint)

        # Metered per CONVERSION, because that is what it costs. The meter
        # counts runs, and a twelve-file suite is one run and twelve conversions
        # — so charging it once would let one request spend twelve times its
        # share of a shared daily budget. It used to charge every file sent,
        # copied helpers included, because classifying them meant a scan. The
        # scan works on text now (`suite.scan_sources`), so the guard plans the
        # tree the same way the graph will and charges for the files that
        # reach a model — after `only`, which is the page's own filter and
        # would otherwise be advice that changed nothing. `only` is read
        # defensively for the same reason as everything else here: this runs
        # before validation.
        only = payload.get("only")
        runs = suite.conversions(
            {str(k): str(v) for k, v in tree.items()},
            only if isinstance(only, list) else [],
        )
        decision = await limits.spend(ctx.user.identity, runs=runs)
        if not decision.allowed:
            raise Auth.exceptions.HTTPException(
                status_code=429, detail=decision.reason,
                headers={"Retry-After": str(decision.retry_after)},
            )
        payload["user_id"] = ctx.user.identity
        value.setdefault("metadata", {})["owner"] = ctx.user.identity
        return True

    named = str(payload.get("source_path") or "").strip()
    if named and not _BARE_FILENAME.match(named):
        raise Auth.exceptions.HTTPException(
            status_code=403,
            detail=(
                "`source_path` may only be a plain filename on the public demo, "
                "used as a label for the file you pasted — not a path the server "
                "would open."
            ),
        )

    if not str(payload.get("source_text") or "").strip():
        raise Auth.exceptions.HTTPException(
            status_code=403,
            detail=(
                "Send the file as `source_text`. The public demo does not read "
                "files from the server, so only the convert graph is available "
                "and only with inline text."
            ),
        )

    attempts = payload.get("max_attempts")
    if isinstance(attempts, int) and attempts > MAX_DEMO_ATTEMPTS:
        raise Auth.exceptions.HTTPException(
            status_code=403,
            detail=f"`max_attempts` is capped at {MAX_DEMO_ATTEMPTS} on the public demo.",
        )

    # Memories are namespaced by user_id, and a caller who picks their own could
    # read somebody else's. Overwritten rather than validated: there is no value
    # a demo caller could send here that we would want to honour.
    payload["user_id"] = ctx.user.identity

    decision = await limits.spend(ctx.user.identity)
    if not decision.allowed:
        raise Auth.exceptions.HTTPException(
            status_code=429,
            detail=decision.reason,
            headers={"Retry-After": str(decision.retry_after)},
        )

    value.setdefault("metadata", {})["owner"] = ctx.user.identity
    return True


@auth.on.threads.create
async def own_threads(ctx, value: dict) -> dict:
    """Stamp each thread with its creator, and scope reads to the same name.

    Returning a dict here does two jobs at once, which is easy to miss: it is
    both the metadata written on create and the filter applied on read. So a
    visitor can only ever see the threads they made — thread ids are random
    enough that this is belt and braces, but the belt costs one line.
    """
    if _is_owner(ctx):
        return {}
    value.setdefault("metadata", {})["owner"] = ctx.user.identity
    return {"owner": ctx.user.identity}


@auth.on.threads.read
async def read_own_threads(ctx, value: dict) -> dict:
    return {} if _is_owner(ctx) else {"owner": ctx.user.identity}


@auth.on.threads.search
async def search_own_threads(ctx, value: dict) -> dict:
    return {} if _is_owner(ctx) else {"owner": ctx.user.identity}


@auth.on.store
async def owner_only_store(ctx, value: Any) -> bool:
    """Long-term memory is shared, so only the owner may touch it directly.

    Recall still works for demo callers — the graph reads the store on their
    behalf inside the run, under their own namespaced user_id. What is closed is
    the *API*: reaching in from outside to read or rewrite what the agent knows.
    """
    if _is_owner(ctx):
        return True
    raise Auth.exceptions.HTTPException(
        status_code=403, detail="The store is not available on the public demo."
    )


@auth.on.crons
async def owner_only_crons(ctx, value: Any) -> bool:
    """A cron is a standing instruction to spend money on a schedule."""
    if _is_owner(ctx):
        return True
    raise Auth.exceptions.HTTPException(
        status_code=403, detail="Scheduled runs are not available on the public demo."
    )


@auth.on.assistants.create
@auth.on.assistants.update
@auth.on.assistants.delete
async def owner_only_assistants(ctx, value: Any) -> bool:
    """Reading the published graphs is fine; redefining them is not."""
    if _is_owner(ctx):
        return True
    raise Auth.exceptions.HTTPException(
        status_code=403, detail="Assistants are read-only on the public demo."
    )

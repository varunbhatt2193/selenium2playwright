# Step 10.2 — deploy: the agent runs where your files are not

> **In one line:** the graph now takes the file as **text** instead of as a path,
> and the image it ships in carries the same pinned TypeScript toolchain the
> four gates shell out to — so a server in a data centre can do the whole job,
> gates included, with none of your files on it.

Second step of Phase 10. [10.1](local-platform.md) put the graph behind a server
on your own machine. This is the part that stops that being a trick.

---

## 1. The problem, stated exactly

`langgraph dev` made the graph callable over HTTP, and it worked because the
server and the files were on the same laptop. Move the server one machine away
and two things break, and only one of them is obvious.

**The obvious one: `source_path` is a promise the server cannot keep.** The
graph's only required input was a path, read with `Path(...).read_text()`. In a
container that path resolves to nothing. Whoever calls it can only ever convert
files that were baked into the image.

**The one that would have been found the hard way: the gates need Node.** The
four validators do not analyse strings — they shell out to a *pinned* `tsc`, a
pinned ESLint, and two parse-only AST scripts under `sandbox/node_modules`. The
deployment base image is `langchain/langgraph-api:3.12`. It is Python. It has no
Node, no npm, and `sandbox/node_modules` is gitignored so it is not in the build
context either.

That second one deserves a decision rather than a workaround, because there is a
tempting shortcut: deploy without the gates, let the model's output through, and
call the conversion done. **That is the one thing this project cannot do.** The
README's claim is "compiler-verified output"; a deployment that cannot compile
its own output can only *claim* the conversion worked. So the image carries the
compiler.

---

## 2. Sending the file instead of naming it

Two new inputs, and the graph now reads:

```jsonc
// the deployable shape — the bytes travel
{ "source_text": "import { By } from \"selenium-webdriver\"; ...",
  "source_path": "LoginPage.ts",
  "context_text": { "pages/BasePage.ts": "export class BasePage { ... }" } }

// still exactly as before, when the server is your own machine
{ "source_path": "samples/selenium-suite/pages/LoginPage.ts",
  "context_paths": ["out/pages/BasePage.ts"] }
```

Three things about this are worth more than the diff, which is one function:

**`source_path` becomes a *name*.** Everything downstream of intake — the
classifier, the long-term-memory recall query, the scorecard title, the report —
only ever wanted to know what the file is *called*. So nothing else in the graph
changed. Note what the name does and does not decide: whether a file is a page
object or a spec comes from its **contents** (does it have a `describe`?), not
its name. But the name goes into the recall query, so a paste box that sends
`pasted.ts` for everything makes long-term memory blurrier than it needs to be —
send the real filename when you have one.

**Neither input is consumed.** `refinement` and `remember` are cleared by intake
after one turn, because they are things the user *said*. `source_text` is not —
it is what the conversation is *about*, exactly like `source_path`, so it stays
on the thread. That is what lets turn 2 of a refine be one sentence and no file,
in paste mode as well as path mode.

**A paste is the first input that arrives from off this machine, so it is the
first with a size.** `MAX_SOURCE_BYTES` is 256 KB (the largest file in the
sample suite is under 4 KB). Over it, `oversized()` returns a `Classification`
with `supported=False` rather than raising — so an over-long paste leaves by the
same door a Cypress file does: the `refuse` node, one honest sentence, no model
call. The cap counts **bytes**, not characters; a file of accented text is
bigger than `len()` thinks.

---

## 3. Putting the toolchain in the image

`langgraph.json` grew a `dockerfile_lines` block. Two details decide its shape.

**It is inserted immediately after `FROM`, before `ADD . /deps/<project>`.** So
these lines can install things, but cannot run anything against the repository —
it is not there yet. `COPY` still reaches into the build context, though, which
is what makes the lockfile-first pattern possible:

```dockerfile
COPY --from=node:22-bookworm-slim /usr/local/bin/node /usr/local/bin/node
COPY --from=node:22-bookworm-slim /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -sf /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm

ENV S2P_SANDBOX=/opt/s2p-sandbox
COPY sandbox/package.json sandbox/package-lock.json /opt/s2p-sandbox/
RUN cd /opt/s2p-sandbox && npm ci --no-audit --no-fund
COPY sandbox/tsconfig.base.json sandbox/eslint.config.mjs sandbox/parity.cjs sandbox/members.cjs /opt/s2p-sandbox/
```

**The path is absolute and fixed, and the code was told where to look.** The
image copies the project to `/deps/<the directory name on the machine that ran
the build>` — which for this repository is `Selenium2Playwright` locally and
`selenium2playwright` from a fresh clone. Hard-coding either one ships a build
that breaks for everybody else. So `sandbox/` is no longer found by walking up
from `__file__`:

```python
# env.py — one owner for "where does this project live, and where are its tools"
REPO_ROOT = Path(__file__).resolve().parents[2]
SANDBOX = Path(os.environ.get("S2P_SANDBOX") or REPO_ROOT / "sandbox")
```

`compile.py`, `lint.py`, `parity.py` and `assemble.py` each used to compute
`REPO_ROOT / "sandbox"` for themselves. They import this instead — an override
that moved *some* of the toolchain and not the rest would be worse than one that
moved none, and a test asserts all four hold the same object.

**`.dockerignore` is new, and two of its lines are load-bearing:**

- `.env` — an image is copied, pulled and cached. A key baked into a layer is a
  key you cannot rotate out of the layers that already exist. The deployment
  gets its keys as environment variables set *on the deployment*.
- `node_modules/` — the toolchain is installed **inside** the image, on Linux,
  from the lockfile. Copying a macOS tree in would shadow it with binaries for
  the wrong platform, and the failure would look like a mysterious `tsc` crash.

---

## 4. The gotcha that cost a build

The build failed with:

```
ERROR: failed to build: failed to solve:
Syntax error - can't find = in "filesystem". Must be of the form: name=value
```

Nothing in that message points at the cause. The CLI writes the graph table into
the generated Dockerfile as one line:

```dockerfile
ENV LANGSERVE_GRAPHS='{"convert": {"path": "...", "description": "..."}}'
```

— single-quoted, **unescaped**. The `convert` graph's description contained the
words *"the SERVER's filesystem"*. That apostrophe closed the string, `filesystem`
became a bare token, and Docker complained about an `ENV` I never wrote.

So: **no apostrophes in a `langgraph.json` graph description.** A test asserts
it, because the next person to write a friendly description will not know.

---

## 5. Calling it: `scripts/call_deployment.py`

```bash
uv run python scripts/call_deployment.py samples/selenium-suite/pages/LoginPage.ts \
    --url http://127.0.0.1:8123 --out out/10.2/LoginPage.ts
```

Twenty lines of `langgraph-sdk` around one idea: read the file **here**, send the
text **there**, stream the node names back so you can watch it think, print the
scorecard. It takes `--url`, and that is the only thing that differs between a
container on your desk and a deployment in a data centre — which is why the
script has no idea which one it is talking to. `--refine` creates a thread over
the wire first, because the server owns the checkpointer now, not a local SQLite
file.

It is a script rather than an `s2p` subcommand on purpose: `s2p` is the local
tool, and the remote front end that matters is [10.3](../roadmap.md)'s playground.

---

## 6. What was actually verified

`langgraph build` produces the real deployment image; `langgraph up --image`
runs it with the same Postgres and Redis a deployment uses. That is the whole
production stack, one machine early — and it is where every problem above was
found.

| Check | Result |
| --- | --- |
| Image builds | **1.6 GB**, Node **v22.23.2**, `tsc` **5.9.3**, ESLint **v10.10.0** at `/opt/s2p-sandbox` |
| `env.SANDBOX` inside the container | `/opt/s2p-sandbox` (the override), `REPO_ROOT` `/deps/Selenium2Playwright` |
| All four gates, in the container | compile / residue / lint / parity all ran; a broken file failed compile with a real `tsc` message, a Selenium import failed residue |
| Full conversion, over `langgraph-sdk`, file sent as text | 3 attempts, **4/4 gates + critic pass**, 1 honest locator TODO, `needs-review`, exit 1 |
| Stack | `s2p:10.2` + `pgvector/pgvector:pg16` + `redis:6`, all healthy |

The conversion is the one that matters: nothing about it happened in the calling
process. The file went out as text, the compiler that judged it was the one
inside the image, and what came back was a report.

---

## 7. What is not done, and why

**The cloud URL.** `langgraph deploy list` answers:

```
Error: LangSmith Deployment is not enabled for this organization.
```

That is an account setting, enabled once at
[smith.langchain.com/host/deployments](https://smith.langchain.com/host/deployments),
and it is a billing decision rather than a technical one — so it is not
something this repository can do for itself. Once it is on, the whole of the
remaining step is:

```bash
uv run langgraph deploy --name selenium2playwright --deployment-type dev
uv run python scripts/call_deployment.py samples/selenium-suite/pages/LoginPage.ts \
    --url https://<the URL it prints>
```

`langgraph deploy` builds remotely for `linux/amd64` when a local Docker cannot
(this laptop is arm64, so it will), and `langgraph deploy delete` takes it down
again. **A dev-tier deployment bills for uptime**, so it is not something to
leave running and forget — which is exactly why 10.4 is called *guardrails
before the URL is public*.

**Everything up to that URL is done and proven.** The gap between "the
production image converts a pasted file with all four gates passing" and "a
LangSmith deployment does" is a hostname.

**The `suite` graph is still path-shaped.** It takes a `root` folder, and in the
cloud the only folders are the ones in the image — so `{"root":
"samples/selenium-suite"}` genuinely works there and converts the bundled sample,
and nothing else does. A folder of pasted files is a different input shape than
a pasted file, and it belongs with the playground that would need it, not here.

---

## 8. Check yourself

1. `source_path` is still required in spirit but is no longer read. What is it
   *for* now, and what gets worse if a paste box always sends `pasted.ts`?
2. `refinement` is cleared by intake after one turn and `source_text` is not.
   What breaks if you clear both, and what breaks if you clear neither?
3. Why is an oversized paste a `Classification` rather than an exception?
4. `dockerfile_lines` land before the project is copied into the image. Given
   that, explain why the lockfile is copied on a line of its own.
5. The image installs the toolchain at `/opt/s2p-sandbox` instead of inside the
   copied repository. Name the specific thing that would break if it did not.
6. You remove Node from the image and everything still deploys and returns
   converted files. What exactly has the deployment stopped being able to say?

---

## 9. What happened when we actually deployed (2026-09-07)

The account setting was enabled and we deployed for real. **Six revisions across
two deployments never produced a URL.** None of the failures were ours. This
section exists so nobody repeats the two and a half hours it took to prove that.

### The bug, in one line

The serverless tier sets `CORE_API_GRPC_SIDECAR=1` in the container and then
never starts the sidecar it promises.

The image ships a Go `core-api-grpc` binary and `/storage/entrypoint.sh` runs it
in-process **unless** that variable is set. With it set, the entrypoint skips the
binary and waits for something else to serve gRPC on `127.0.0.1:50051`. Nothing
ever does:

```
CORE_API_GRPC_SIDECAR is set. Skipping in-process core-api-grpc (expected as sidecar).
Waiting for gRPC server to be ready
ipv4:127.0.0.1:50051: Failed to connect to remote host: Connection refused
RuntimeError: gRPC server not ready after 60.0s (reached max attempts: 120)
Application startup failed. Exiting.
```

This is reproducible on a laptop in one command — run the same image with
`-e CORE_API_GRPC_SIDECAR=1 -e DB_MIGRATION_BY_CORE_API=true` against pgvector
and redis and you get the trace byte for byte. That is what made it certain the
platform, not the image, was at fault. `langgraph up` never sets the variable,
which is exactly why the same image works locally.

An independent report of the identical signature on the same serverless tier:
<https://github.com/siddicky/healthcare-rag-langgraph/pull/11> (2026-08-22).

### Three layers, peeled in order

1. **The sidecar.** Fixed by unsetting the variable inside the image:
   `RUN sed -i '1a unset CORE_API_GRPC_SIDECAR' /storage/entrypoint.sh
   /storage/queue_entrypoint.sh`. The Go core then runs in-process and the
   server starts. *This works* — proven locally and in the cloud.
2. **The vector extension.** Also unsetting `DB_MIGRATION_BY_CORE_API` hands
   migrations to Python, which then fails: `permission denied to create
   extension "vector" — must be superuser`. Their managed Postgres does not
   grant it. Leaving that variable set instead makes the in-process Go core
   exit 1 immediately (it cannot find its migration files). **So on the managed
   platform you may have the `store.index` block or the sidecar workaround, but
   not both.** Dropping the `store` block deploys and costs semantic recall —
   `indexed()` returns false and recall falls back to most-recent.
3. **The readiness gate.** With the sidecar patched and the `store` block gone,
   the container ran perfectly — `Application startup complete` in 9.3s, ten
   background workers, health server on `:9000`, submitting metadata to
   LangSmith's own API for nineteen minutes without a single error — and the
   control plane *still* marked the revision `DEPLOY_FAILED` and assigned no
   hostname. Their readiness probe appears to depend on the same absent sidecar.
   **No change to an image can satisfy a probe for a container the platform
   declines to run.**

### Things that look like fixes and are not

- **Pinning `api_version`.** The sidecar branch has been in the entrypoint since
  at least `0.11.0-py3.12`, so no version avoids it. Worse, `--api-version` and
  `--base-image` are *silently ignored* on the remote build path —
  `_run_remote_build()` in `langgraph_cli/deploy.py` does not take those
  parameters at all, while `_run_local_build()` does. The same value in
  `langgraph.json` is ignored by the managed builder too. And
  `langchain/langgraph-api:0.12-py3.12` does not exist on Docker Hub; only
  patch-level tags do.
- **Retrying.** Four plain retries, four identical failures.
- **`--engine-runtime-mode distributed`.** Only wired into `up` and `build`;
  `deploy.py` never sends it.
- **The web UI's "Import from GitHub".** It uses the same remote builder and the
  same serverless runtime.
- **Waiting.** A failed revision takes 23–37 minutes to be declared
  `DEPLOY_FAILED`. Revisions serialize per deployment, so a second deployment
  under a different `--name` is the way to test two hypotheses at once.

### Where this leaves us

`langgraph deploy` is Beta and self-identifies as such. The managed serverless
tier could not host this image on 2026-09-07. Everything up to the hostname is
done and proven, twice: the image builds, starts, loads both graphs and the
embeddings function, and converts a pasted file with all four gates green.

**Self-hosting is the path, and it is strictly better here.** The entrypoint
runs the Go core in-process whenever `CORE_API_GRPC_SIDECAR` is unset, which is
the default everywhere except that platform — so the bug simply does not exist
off it. And because a self-hosted Postgres is *ours*, `CREATE EXTENSION vector`
succeeds and the `store.index` block comes back. The stack is three containers:
this image, `pgvector/pgvector:pg16`, and `redis:6`, wired with `POSTGRES_URI`,
`REDIS_URI` and `LANGSMITH_API_KEY` (the image verifies a licence at startup
against `api.smith.langchain.com/auth`; the enterprise alternative is
`LANGGRAPH_CLOUD_LICENSE_KEY` — see
<https://docs.langchain.com/langsmith/deploy-standalone-server>). The image must
be `linux/amd64` for most hosts.

`langgraph.nostore.json` is kept as the config that actually started in the
cloud — the sidecar patch with no `store` block. It is the fallback if a host
ever hands us a Postgres we cannot create extensions on.

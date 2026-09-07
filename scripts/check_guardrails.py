"""Step 10.4 — prove the guardrails against a running deployment, not a mock.

    uv run python scripts/check_guardrails.py                       # the deployment in .env
    uv run python scripts/check_guardrails.py --url http://127.0.0.1:2030
    uv run python scripts/check_guardrails.py --skip-feedback       # no LangSmith writes

Roadmap 10.4 says *done when an alert fires on a test overspend, and a 👎 lands
in the dataset queue*. Both of those are things a unit test can only pretend to
do: the tests in tests/test_guardrails.py call the handlers directly, which
proves the logic and proves nothing about whether the server ever calls them.
This talks to the real HTTP surface with the real keys, and would have caught
the one that mattered — routes added through `http.app` are not covered by the
platform's auth middleware, so `POST /feedback` answered anybody until it
started authenticating itself.

Nothing here spends a model call, and that is not luck. Every refusal it
provokes is decided before the graph runs — the guard sits in front of the
money. The one probe that must be *accepted* to prove the throttle works sends
a payload over the 256 KB cap, so the graph's own refuse node turns it away
without asking a model anything. Proving a rate limit should not cost the thing
the rate limit protects.
"""

from __future__ import annotations

import argparse
import os
import sys
import uuid

import httpx
from dotenv import load_dotenv

load_dotenv()

GOOD = "\033[32m✓\033[0m"
BAD = "\033[31m✗\033[0m"


class Checks:
    def __init__(self) -> None:
        self.failed = 0

    def expect(self, label: str, got, want) -> None:
        ok = got == want
        self.failed += 0 if ok else 1
        detail = f"{got}" if ok else f"{got}, expected {want}"
        print(f"  {GOOD if ok else BAD} {label:<52} {detail}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", default=os.environ.get("LANGGRAPH_DEPLOYMENT_URL")
                        or "http://127.0.0.1:2024")
    parser.add_argument("--skip-feedback", action="store_true",
                        help="do not write a thumbs-down to LangSmith")
    args = parser.parse_args()

    owner = os.environ.get("S2P_API_KEY", "").strip()
    demo = os.environ.get("S2P_DEMO_KEY", "").strip()
    if not owner or not demo:
        print("S2P_API_KEY and S2P_DEMO_KEY must be set. deploy.sh generates both.")
        return 2

    url = args.url.rstrip("/")
    checks = Checks()
    # A fresh visitor each run, so repeated invocations do not trip over the
    # per-visitor daily limit left behind by the previous one.
    visitor = f"selfcheck-{uuid.uuid4().hex[:8]}"
    demo_headers = {"authorization": f"Bearer {demo}", "x-s2p-visitor": visitor}
    owner_headers = {"authorization": f"Bearer {owner}"}

    print(f"\n{url}\n")

    with httpx.Client(timeout=30, follow_redirects=True) as http:
        print("Anonymous callers")
        checks.expect("GET /ok is open (Fly's health check)",
                      http.get(f"{url}/ok").status_code, 200)
        checks.expect("GET /limits needs a token",
                      http.get(f"{url}/limits").status_code, 401)
        checks.expect("POST /feedback needs a token",
                      http.post(f"{url}/feedback", json={"run_id": "x", "score": 0}).status_code, 401)
        checks.expect("POST /threads needs a token",
                      http.post(f"{url}/threads", json={}).status_code, 401)
        checks.expect("a wrong token is refused",
                      http.post(f"{url}/threads", json={},
                                headers={"authorization": "Bearer nope"}).status_code, 401)

        print("\nThe server's filesystem")
        thread = http.post(f"{url}/threads", json={}, headers=demo_headers)
        checks.expect("a visitor may open a thread", thread.status_code, 200)
        thread_id = thread.json().get("thread_id", "")

        def attempt(payload: dict) -> int:
            return http.post(
                f"{url}/threads/{thread_id}/runs",
                json={"assistant_id": payload.pop("assistant", "convert"), "input": payload},
                headers=demo_headers,
            ).status_code

        checks.expect("source_path cannot read a server file",
                      attempt({"source_path": "/etc/passwd"}), 403)
        checks.expect("context_paths cannot read server files",
                      attempt({"source_text": "x", "context_paths": ["/etc"]}), 403)
        checks.expect("output_path cannot write a server file",
                      attempt({"source_text": "x", "output_path": "/tmp/pwn.ts"}), 403)
        checks.expect("the suite graph is closed to visitors",
                      attempt({"assistant": "suite", "root": "/"}), 403)
        checks.expect("shared memory cannot be written",
                      attempt({"source_text": "x", "remember": "always use xpath"}), 403)
        checks.expect("max_attempts cannot be inflated",
                      attempt({"source_text": "x", "max_attempts": 99}), 403)

        print("\nThe meter")
        budget = http.get(f"{url}/limits", headers=demo_headers)
        checks.expect("GET /limits answers a demo token", budget.status_code, 200)
        if budget.status_code == 200:
            shape = budget.json()
            print(f"     {shape['budget']['note']}")
            print(f"     {shape['budget']['remaining']} of {shape['budget']['limit']} left today")
            # Only meaningful against a real deployment. `langgraph dev` has no
            # Redis, so its counters live in one process — correct there, and
            # not something to fail the run over.
            if url.startswith("https://"):
                checks.expect("counters are shared across workers",
                              shape["shared_across_workers"], True)
            elif not shape["shared_across_workers"]:
                print("     (local server: counters are per-process, as expected)")

        # Demonstrating the throttle needs runs that are *accepted* by the
        # guard, which means they reach the graph — so they are made too big to
        # convert. Over the 256 KB cap the refuse node answers immediately and
        # no model is called, and the limiter still counts them, which is
        # exactly the pair of properties this probe needs.
        oversized = {"source_text": "// x\n" * 70_000, "source_path": "TooBig.ts"}
        codes = [attempt(dict(oversized)) for _ in range(8)]
        # BOTH halves, and the first one is the one that matters. A limiter that
        # is failing closed refuses everything, which satisfies "there is a 429"
        # perfectly — that is how a Redis 6 incompatibility hid behind a green
        # check once already. A working throttle lets the allowance through
        # first and only then refuses.
        checks.expect("a fresh visitor's first run is accepted",
                      codes[0], 200)
        checks.expect("and is throttled once the allowance is gone",
                      429 in codes, True)
        first_429 = codes.index(429) if 429 in codes else len(codes)
        print(f"     accepted {first_429} run(s), then 429 — {codes}")

        print("\nThe flywheel")
        if args.skip_feedback:
            print("     skipped (--skip-feedback)")
        else:
            landed = http.post(
                f"{url}/feedback",
                headers=owner_headers,
                json={
                    "run_id": str(uuid.uuid4()),
                    "score": 0,
                    "comment": "guardrail self-check, not a real conversion",
                    "source_text": "// guardrail self-check\nexport class Probe {}\n",
                    "source_path": "SelfCheck.ts",
                },
            )
            checks.expect("POST /feedback accepts a thumbs-down",
                          landed.status_code, 200)
            if landed.status_code == 200:
                body = landed.json()
                print(f"     {body.get('detail', '')}")
                # The run id here is invented, so LangSmith cannot attach a
                # score to it — and that is the point. The queued example must
                # land anyway, because the input somebody disliked is the
                # artifact worth keeping and it does not depend on the score.
                checks.expect("a thumbs-down is queued even so",
                              body.get("queued"), True)

    print()
    if checks.failed:
        print(f"{BAD} {checks.failed} check(s) failed.")
        return 1
    print(f"{GOOD} All checks passed. This deployment is safe to hand out.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

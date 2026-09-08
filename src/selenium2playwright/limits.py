"""Step 10.4 — counting what a public URL is allowed to spend.

Fly's bill is a constant: three machines at fixed sizes, no autoscaler, about
$19.50 a month whether nobody visits or ten thousand people do. The machines
are already paid for. What is *not* constant is the model spend behind them —
every conversion is real Anthropic tokens on a real card, and an endpoint the
whole internet can POST to is an endpoint the whole internet can spend from.

So this module is the meter. Three limits, deliberately different in shape,
because they defend against three different things:

    burst    a few runs a minute, per visitor   stops one person hammering
    daily    a modest number a day, per visitor stops one person grinding
    budget   a hard ceiling a day, everyone     stops the *bill*, whoever spends it

Only the third is about money. The first two are about fairness — they keep one
enthusiastic visitor from eating the day's budget before anyone else arrives.
The budget is the one that cannot be argued with: when it is gone, it is gone
for everybody until midnight UTC, and that is the whole point. An alert fires on
the way up so the ceiling is never a surprise.

**Where the counts live.** In Redis, which the deployment already runs for the
run queue. Redis is the right shape for this: `INCR` is atomic, so two requests
arriving at the same instant on two workers cannot both see "9 of 10 used", and
`EXPIRE` means a window cleans itself up rather than needing a sweeper. With no
`REDIS_URI` — a laptop, a test — it falls back to a dictionary in this process,
which is correct for one process and wrong for two, and says so rather than
pretending otherwise.

**Fail closed.** If Redis is unreachable we cannot know what has been spent, and
"we cannot know" is not a reason to allow spending. Anonymous and demo callers
are refused until it comes back. The owner is let through, because the owner is
the person who needs to get in and fix it.

One thing this is not: an accountant. The budget is *set* in dollars, because
that is the unit the card is billed in and the only one worth reasoning about —
but it is *enforced* in runs, because a run has to be allowed or refused before
the model answers, and the price is only known afterwards. The bridge is
`COST_PER_RUN`, a deliberate overestimate of the most expensive path a single
conversion can take. So the ceiling binds early rather than late, and the exact
figure always lives in the LangSmith trace, never here.
"""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

# Defaults chosen for a portfolio demo that strangers are invited to try, not
# for a product. They are deliberately small: the failure we are defending
# against is a surprise invoice, and a visitor who wants a seventh conversion in
# one minute can wait sixty seconds for it.
BURST_LIMIT = int(os.environ.get("S2P_BURST_LIMIT") or 3)
BURST_WINDOW = int(os.environ.get("S2P_BURST_WINDOW") or 60)
DAILY_LIMIT = int(os.environ.get("S2P_DAILY_LIMIT") or 10)

# Rough worst case for one conversion, in dollars. The Phase 6 shootout measured
# $0.29–$0.69 per 12 files across fully-costed configurations — about $0.06 at
# the top end — and this doubles that, because a conversion that takes all three
# reflection laps is the expensive path and a ceiling built on the average is not
# a ceiling. Overestimating here makes the demo stop *early*, which is the
# direction to be wrong in.
COST_PER_RUN = float(os.environ.get("S2P_COST_PER_RUN") or 0.12)

# The limit that is actually about money, expressed in the unit the card is
# billed in. Runs are what we can count before a model answers; dollars are what
# anybody actually cares about, so the dollars are the setting and the run count
# is derived from them. $5/day is a demo somebody can leave running without
# thinking about it; raise it the day a recruiter is actually looking.
DAILY_BUDGET_USD = float(os.environ.get("S2P_DAILY_BUDGET_USD") or 5.0)
BUDGET_RUNS = int(os.environ.get("S2P_BUDGET_RUNS") or max(1, int(DAILY_BUDGET_USD / COST_PER_RUN)))

# Fraction of the daily budget at which the alert fires. Not 1.0: an alert that
# arrives at the moment the door shuts is a notification, not a warning.
ALERT_AT = float(os.environ.get("S2P_ALERT_AT") or 0.8)

PREFIX = "s2p:limit"


def _today() -> str:
    """The budget day, in UTC.

    UTC rather than local time so that the ceiling means the same thing to a
    visitor in Bangalore and one in Boston, and so that a server that moves
    region does not silently give somebody a second allowance.
    """
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


@dataclass(frozen=True)
class Decision:
    """The answer to "may this run start?", and enough detail to say why not.

    `retry_after` is seconds, and is only meaningful when `allowed` is False. It
    is what turns a refusal into an instruction: a burst refusal can be retried
    within the minute, a daily one cannot, and the caller deserves to know which
    kind it hit rather than being told to go away.
    """

    allowed: bool
    reason: str = ""
    retry_after: int = 0
    used: dict[str, int] = field(default_factory=dict)

    @property
    def detail(self) -> str:
        return self.reason or "allowed"


class _Counter:
    """Atomic increment-with-expiry, over Redis when there is one.

    The in-memory fallback is not a second implementation of the same thing —
    it is a single-process approximation, and calling it that matters. Two
    uvicorn workers each get their own dictionary, so the real limit becomes
    twice what the configuration says. That is fine on a laptop and wrong in
    production, which is exactly the split between where each backend is used.
    """

    def __init__(self) -> None:
        self._redis = None
        self._memory: dict[str, tuple[int, float]] = {}
        self._lock = asyncio.Lock()
        uri = os.environ.get("REDIS_URI") or os.environ.get("REDIS_URL") or ""
        # Only a real redis URL counts. `langgraph dev` sets REDIS_URI to a value
        # its in-memory runtime understands and `redis.from_url` does not, and a
        # truthy-but-unusable URI would make every request fail closed on a
        # laptop — a limiter that breaks local development gets switched off,
        # and a limiter that is switched off is not a limiter.
        self._uri = uri if uri.startswith(("redis://", "rediss://", "unix://")) else ""

    @property
    def distributed(self) -> bool:
        return bool(self._uri)

    async def _client(self):
        if self._redis is None:
            import redis.asyncio as aioredis  # imported late: laptops need no redis

            self._redis = aioredis.from_url(self._uri, decode_responses=True)
        return self._redis

    async def bump(self, key: str, ttl: int, amount: int = 1) -> int:
        """Add `amount` to `key`, creating it with a `ttl` if it is new. Returns the total.

        `amount` exists for the suite: twelve files is one request and twelve
        conversions, and charging it as one would let a single call spend twelve
        times its share of a budget everybody is sharing. `INCRBY n` is one
        round trip and one atomic step, which a loop of twelve `INCR`s would
        not be — two callers interleaving would each read a total that was never
        true.
        """
        if not self._uri:
            async with self._lock:
                now = time.monotonic()
                count, expires = self._memory.get(key, (0, 0.0))
                if expires <= now:
                    count, expires = 0, now + ttl
                count += amount
                self._memory[key] = (count, expires)
                return count

        client = await self._client()
        pipe = client.pipeline()
        pipe.incrby(key, amount)
        pipe.ttl(key)
        count, remaining = await pipe.execute()
        count = int(count)
        # Set the deadline only on the request that created the key. The obvious
        # spelling is `EXPIRE key ttl NX`, and it is wrong here: the NX flag
        # arrived in Redis 7.0 and the deployment runs redis:6, where it raises —
        # which made every bump throw, which made `spend()` fail closed, which
        # refused every conversion with a 429 that looked exactly like a working
        # rate limit. `count == 1` means we just created it, and it means the
        # same thing on every Redis there has ever been.
        #
        # Re-arming a key with no TTL (-1) covers the gap where a process died
        # between the INCR and the EXPIRE; without it that key would count
        # forever and the visitor would be locked out until somebody noticed.
        if count == amount or int(remaining) < 0:
            await client.expire(key, ttl)
        return count

    async def undo(self, key: str, amount: int = 1) -> None:
        """Give back `amount` counts. See `spend()` for when this is and is not right."""
        if not self._uri:
            async with self._lock:
                count, expires = self._memory.get(key, (0, 0.0))
                if count:
                    self._memory[key] = (max(0, count - amount), expires)
            return
        client = await self._client()
        await client.decrby(key, amount)

    async def read(self, key: str) -> int:
        if not self._uri:
            count, expires = self._memory.get(key, (0, 0.0))
            return count if expires > time.monotonic() else 0
        client = await self._client()
        return int(await client.get(key) or 0)

    async def mark_once(self, key: str, ttl: int) -> bool:
        """True the first time only. Used so an alert fires once, not per request."""
        if not self._uri:
            async with self._lock:
                now = time.monotonic()
                _, expires = self._memory.get(key, (0, 0.0))
                if expires > now:
                    return False
                self._memory[key] = (1, now + ttl)
                return True
        client = await self._client()
        return bool(await client.set(key, "1", ex=ttl, nx=True))

    def reset(self) -> None:
        """Tests only. Drops the in-process counts; never touches Redis."""
        self._memory.clear()


_counter = _Counter()


def budget_note() -> str:
    """The daily ceiling as a sentence about money rather than a number of runs."""
    return (
        f"{BUDGET_RUNS} runs/day — at most ${BUDGET_RUNS * COST_PER_RUN:,.2f}/day "
        f"(${COST_PER_RUN:.2f}/run worst case), so under "
        f"${BUDGET_RUNS * COST_PER_RUN * 30:,.0f}/month if it is exhausted every day"
    )


async def tap(identity: str, *, unlimited: bool = False) -> Decision:
    """Rate-limit something that costs no model tokens, like leaving feedback.

    Burst only: there is no reason to charge the day's *budget* for an action
    that spends nothing, and no reason to let somebody hold the button down
    either. Same window, same fairness, none of the money.
    """
    if unlimited:
        return Decision(allowed=True, reason="owner")
    try:
        count = await _counter.bump(f"{PREFIX}:tap:{identity}", BURST_WINDOW)
    except Exception:  # noqa: BLE001
        return Decision(allowed=False, reason="Temporarily unavailable.", retry_after=60)
    if count > BURST_LIMIT * 4:
        return Decision(
            allowed=False,
            reason="Too many requests; try again shortly.",
            retry_after=BURST_WINDOW,
            used={"tap": count},
        )
    return Decision(allowed=True, used={"tap": count})


async def spend(identity: str, *, runs: int = 1, unlimited: bool = False) -> Decision:
    """Charge `runs` conversions against `identity`, or explain why it cannot be.

    `runs` is 1 for a single file and the file count for a suite. It is charged
    as one atomic step rather than a loop, so a suite that does not fit is
    refused whole: half a suite converted and half refused is a worse answer
    than "this needs 12 and 5 are left".

    The order matters. Burst is checked first because it is the cheapest signal
    and the most likely to be someone hammering; the daily budget is checked
    last because it is the one whose count we most want to be accurate.

    Two different refunds, for two different reasons:

      A run refused by the *burst* limit keeps its count. Retrying immediately
      is exactly the behaviour the burst window exists to discourage, and if
      refusals were free a tight retry loop would slip through the gap.

      A run refused by a *spend* limit — daily or budget — gives its count back.
      Those counters mean "runs actually started", and a refused run starts
      nothing and costs nothing. Charging for it would make the ceiling drift
      down every time somebody bounced off it.
    """
    if unlimited:
        return Decision(allowed=True, reason="owner")

    runs = max(1, int(runs))
    day = _today()
    burst_key = f"{PREFIX}:burst:{identity}"
    daily_key = f"{PREFIX}:day:{identity}:{day}"
    budget_key = f"{PREFIX}:budget:{day}"
    two_days = 60 * 60 * 48

    try:
        # Burst counts requests, not files: it exists to stop somebody holding
        # the button down, and one suite is one press however wide it is.
        burst = await _counter.bump(burst_key, BURST_WINDOW)
        if burst > BURST_LIMIT:
            return Decision(
                allowed=False,
                reason=f"Too fast — {BURST_LIMIT} conversions per {BURST_WINDOW}s.",
                retry_after=BURST_WINDOW,
                used={"burst": burst},
            )

        daily = await _counter.bump(daily_key, two_days, runs)
        if daily > DAILY_LIMIT:
            await _counter.undo(daily_key, runs)
            return Decision(
                allowed=False,
                reason=(f"Daily limit reached — {DAILY_LIMIT} conversions per visitor "
                        f"per day, and this needs {runs}."
                        if runs > 1 else
                        f"Daily limit reached — {DAILY_LIMIT} conversions per visitor per day."),
                retry_after=_seconds_to_midnight(),
                used={"daily": daily - runs},
            )

        budget = await _counter.bump(budget_key, two_days, runs)
        if budget > BUDGET_RUNS:
            await _counter.undo(budget_key, runs)
            await _counter.undo(daily_key, runs)
            return Decision(
                allowed=False,
                reason=(f"This suite needs {runs} conversions and the demo's daily "
                        "budget cannot cover it. It resets at midnight UTC."
                        if runs > 1 else
                        "The demo's daily budget is spent. It resets at midnight UTC."),
                retry_after=_seconds_to_midnight(),
                used={"budget": budget - runs},
            )

        await _maybe_alert(budget, day)
        return Decision(
            allowed=True, used={"burst": burst, "daily": daily, "budget": budget}
        )

    except Exception as exc:  # noqa: BLE001 — any backend failure means the same thing
        # We cannot read the meter, so we cannot say what is left. Refusing is
        # the only answer that cannot cost money. The owner path never reaches
        # here, so this does not lock us out of our own deployment.
        return Decision(
            allowed=False,
            reason="Usage limits are temporarily unavailable; the demo is paused.",
            retry_after=60,
            used={"error": 1},
        )


def _seconds_to_midnight() -> int:
    now = datetime.now(timezone.utc)
    tomorrow = now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp() + 86400
    return max(1, int(tomorrow - now.timestamp()))


async def _maybe_alert(budget_used: int, day: str) -> None:
    """Say something, once, when the day's spend crosses the warning line.

    Deliberately not an exception and not a refusal: crossing 80% is normal on a
    day when the demo is popular. It is worth knowing about and not worth
    breaking anything over.
    """
    threshold = max(1, int(BUDGET_RUNS * ALERT_AT))
    if budget_used < threshold:
        return
    if not await _counter.mark_once(f"{PREFIX}:alerted:{day}", 60 * 60 * 48):
        return

    message = (
        f"s2p budget alert: {budget_used}/{BUDGET_RUNS} runs used on {day} "
        f"({budget_used / BUDGET_RUNS:.0%} of {budget_note()}). "
        f"Refusals begin at {BUDGET_RUNS}."
    )
    print(f"[s2p][ALERT] {message}", flush=True)
    await _post_alert(message)


async def _post_alert(message: str) -> None:
    """Best-effort webhook. A failed alert must never fail a conversion."""
    url = os.environ.get("S2P_ALERT_WEBHOOK", "").strip()
    if not url:
        return
    try:
        import httpx

        async with httpx.AsyncClient(timeout=5) as client:
            await client.post(url, json={"text": message})
    except Exception as exc:  # noqa: BLE001
        print(f"[s2p][ALERT] webhook failed: {type(exc).__name__}", flush=True)


async def snapshot() -> dict[str, object]:
    """What the meter reads right now — for the /limits route and for humans."""
    day = _today()
    used = await _counter.read(f"{PREFIX}:budget:{day}")
    return {
        "day": day,
        "budget": {
            "used": used,
            "limit": BUDGET_RUNS,
            "remaining": max(0, BUDGET_RUNS - used),
            "note": budget_note(),
        },
        "per_visitor": {"daily": DAILY_LIMIT, "burst": BURST_LIMIT, "window_s": BURST_WINDOW},
        "budget_usd_per_day": DAILY_BUDGET_USD,
        "alert_at": ALERT_AT,
        "shared_across_workers": _counter.distributed,
    }

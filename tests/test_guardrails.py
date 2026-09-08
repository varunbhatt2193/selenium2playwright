"""Step 10.4 — are the guardrails actually load-bearing?

Offline, and deliberately so: none of this touches Redis, LangSmith or a model.
The counters run on their in-process fallback and the auth handlers are called
directly, which is the whole point — a guardrail you can only test by deploying
is a guardrail you will not test.

What is under test is the part that would cost money or leak a file if it were
wrong: that a stranger cannot read the server's disk, cannot spend past the
day's budget, cannot write to shared memory, and that the owner can do all of
it. The refusals are checked by their status code *and* by what they say,
because a 403 that does not explain itself turns a careful demo into a broken
one.
"""

import asyncio
import os
import unittest
from unittest.mock import patch

from langgraph_sdk import Auth

from selenium2playwright import guard, limits


def run(coro):
    return asyncio.run(coro)


class FakeUser:
    """The shape `ctx.user` has, with only the parts the handlers read."""

    def __init__(self, identity: str, permissions: list[str]):
        self.identity = identity
        self.permissions = permissions


class FakeCtx:
    def __init__(self, identity: str, permissions: list[str]):
        self.user = FakeUser(identity, permissions)


OWNER = FakeCtx("owner", ["owner"])
VISITOR = FakeCtx("demo:alice", ["demo"])
OTHER = FakeCtx("demo:mallory", ["demo"])


def run_body(**payload):
    """A create-run request the way the server hands it to an authorization handler."""
    return {"assistant_id": "convert", "kwargs": {"input": dict(payload)}}


def headers(**pairs):
    return {k.replace("_", "-").encode(): v.encode() for k, v in pairs.items()}


class AuthenticationTests(unittest.TestCase):
    """Who gets in, and what they are called once they are."""

    def setUp(self):
        limits._counter.reset()

    def test_health_endpoints_need_no_token(self):
        # Fly polls /ok every 30 seconds with no credentials. If this ever
        # returns 401 the machine never goes healthy and the deploy fails with
        # an error that says nothing about authentication.
        for path in ("/ok", "/info", "/metrics"):
            user = run(guard.authenticate(path, "GET", {}, None))
            self.assertEqual(user["identity"], "health", path)

    def test_no_keys_configured_refuses_rather_than_opens(self):
        with patch.dict(os.environ, {"S2P_API_KEY": "", "S2P_DEMO_KEY": "", "S2P_AUTH": "on"}):
            with self.assertRaises(Auth.exceptions.HTTPException) as caught:
                run(guard.authenticate("/threads", "POST", {}, "Bearer anything"))
        self.assertEqual(caught.exception.status_code, 503)
        self.assertIn("S2P_API_KEY", caught.exception.detail)

    def test_owner_key_is_owner_and_demo_key_is_not(self):
        with patch.dict(os.environ, {"S2P_API_KEY": "owner-key", "S2P_DEMO_KEY": "demo-key"}):
            owner = run(guard.authenticate("/threads", "POST", {}, "Bearer owner-key"))
            demo = run(guard.authenticate("/threads", "POST", headers(x_s2p_visitor="alice"),
                                          "Bearer demo-key"))
        self.assertEqual(owner["identity"], "owner")
        self.assertIn("owner", owner["permissions"])
        self.assertEqual(demo["identity"], "demo:alice")
        self.assertNotIn("owner", demo["permissions"])

    def test_the_sdk_spelling_of_the_key_is_accepted(self):
        # langgraph-sdk sends x-api-key and never an Authorization header, so a
        # guard that only read the latter rejected our own client. The SDK is
        # the main way anything calls this deployment.
        with patch.dict(os.environ, {"S2P_API_KEY": "owner-key", "S2P_DEMO_KEY": "demo-key"}):
            user = run(guard.authenticate("/threads", "POST",
                                          headers(x_api_key="owner-key"), None))
        self.assertEqual(user["identity"], "owner")

    def test_wrong_and_missing_tokens_are_401(self):
        with patch.dict(os.environ, {"S2P_API_KEY": "owner-key", "S2P_DEMO_KEY": "demo-key"}):
            for authorization in (None, "", "Bearer nope", "Basic owner-key"):
                with self.assertRaises(Auth.exceptions.HTTPException) as caught:
                    run(guard.authenticate("/threads", "POST", {}, authorization))
                self.assertEqual(caught.exception.status_code, 401, authorization)

    def test_visitors_are_separated_by_header_then_by_ip(self):
        with patch.dict(os.environ, {"S2P_API_KEY": "k", "S2P_DEMO_KEY": "d"}):
            by_header = run(guard.authenticate("/t", "POST", headers(x_s2p_visitor="alice"), "Bearer d"))
            by_ip = run(guard.authenticate("/t", "POST", headers(fly_client_ip="1.2.3.4"), "Bearer d"))
            nothing = run(guard.authenticate("/t", "POST", {}, "Bearer d"))
        self.assertEqual(by_header["identity"], "demo:alice")
        self.assertEqual(by_ip["identity"], "demo:1.2.3.4")
        self.assertEqual(nothing["identity"], "demo:anonymous")

    def test_a_hostile_visitor_header_cannot_shape_the_key(self):
        # The visitor string becomes part of a Redis key and of log lines, so a
        # value with separators in it could collide with another visitor's
        # counter or forge a log entry. Anything unexpected falls back to the IP.
        with patch.dict(os.environ, {"S2P_API_KEY": "k", "S2P_DEMO_KEY": "d"}):
            user = run(guard.authenticate(
                "/t", "POST",
                headers(x_s2p_visitor="alice bob\nowner", fly_client_ip="9.9.9.9"),
                "Bearer d",
            ))
        self.assertEqual(user["identity"], "demo:9.9.9.9")


class ServerFilesystemTests(unittest.TestCase):
    """The part that is a security bug rather than a bill."""

    def setUp(self):
        limits._counter.reset()

    def test_a_path_shaped_source_path_is_refused(self):
        for value in ("/etc/passwd", "../../etc/passwd", "pages/Login.ts", ".env"):
            with self.assertRaises(Auth.exceptions.HTTPException) as caught:
                run(guard.guard_run(VISITOR, run_body(source_text="x", source_path=value)))
            self.assertEqual(caught.exception.status_code, 403, value)
            self.assertIn("source_path", caught.exception.detail, value)

    def test_a_plain_filename_is_allowed_as_a_label(self):
        # It is not a path when source_text came with it — it is what the file
        # is called, which the classifier, the recall query and the report all
        # want. Refusing it outright took a name away from every visitor to
        # close a hole that only exists without source_text.
        body = run_body(source_text="class A {}", source_path="LoginPage.ts")
        self.assertTrue(run(guard.guard_run(VISITOR, body)))

    def test_every_path_shaped_input_is_refused(self):
        for field in ("context_paths", "output_path", "root", "out_root"):
            value = ["/etc"] if field == "context_paths" else "/etc"
            with self.assertRaises(Auth.exceptions.HTTPException) as caught:
                run(guard.guard_run(VISITOR, run_body(source_text="x", **{field: value})))
            self.assertEqual(caught.exception.status_code, 403, field)
            self.assertIn(field, caught.exception.detail, field)

    def test_remember_cannot_be_set_by_a_visitor(self):
        # Long-term memory is shared. One visitor teaching a bad convention
        # would quietly degrade every later conversion, for everybody.
        with self.assertRaises(Auth.exceptions.HTTPException) as caught:
            run(guard.guard_run(VISITOR, run_body(source_text="x", remember="always use xpath")))
        self.assertIn("remember", caught.exception.detail)

    def test_a_run_without_inline_text_is_refused(self):
        # This is what closes the suite graph to visitors as well: its input has
        # no source_text at all.
        with self.assertRaises(Auth.exceptions.HTTPException) as caught:
            run(guard.guard_run(VISITOR, run_body()))
        self.assertEqual(caught.exception.status_code, 403)
        self.assertIn("source_text", caught.exception.detail)

    def test_a_visitor_cannot_choose_whose_memories_to_read(self):
        body = run_body(source_text="x", user_id="owner")
        run(guard.guard_run(VISITOR, body))
        self.assertEqual(body["kwargs"]["input"]["user_id"], "demo:alice")

    def test_max_attempts_is_capped(self):
        with self.assertRaises(Auth.exceptions.HTTPException) as caught:
            run(guard.guard_run(VISITOR, run_body(source_text="x", max_attempts=99)))
        self.assertIn("max_attempts", caught.exception.detail)

    def test_the_owner_may_do_all_of_it(self):
        body = run_body(source_path="/anywhere", root="/anywhere", remember="fine",
                        max_attempts=99, source_text="")
        self.assertTrue(run(guard.guard_run(OWNER, body)))

    def test_a_malformed_body_does_not_crash_the_handler(self):
        # This runs before anything validates the request, so it must survive
        # whatever arrives rather than turning a bad request into a 500.
        for body in ({}, {"kwargs": None}, {"kwargs": {"input": "not-a-dict"}}):
            with self.assertRaises(Auth.exceptions.HTTPException) as caught:
                run(guard.guard_run(VISITOR, dict(body)))
            self.assertEqual(caught.exception.status_code, 403)


class ResourceTests(unittest.TestCase):
    """Everything that is not a run."""

    def test_store_and_crons_are_owner_only(self):
        for handler in (guard.owner_only_store, guard.owner_only_crons):
            self.assertTrue(run(handler(OWNER, {})))
            with self.assertRaises(Auth.exceptions.HTTPException) as caught:
                run(handler(VISITOR, {}))
            self.assertEqual(caught.exception.status_code, 403)

    def test_threads_are_scoped_to_their_creator(self):
        body = {}
        self.assertEqual(run(guard.own_threads(VISITOR, body)), {"owner": "demo:alice"})
        self.assertEqual(body["metadata"]["owner"], "demo:alice")
        self.assertEqual(run(guard.read_own_threads(OTHER, {})), {"owner": "demo:mallory"})
        self.assertEqual(run(guard.own_threads(OWNER, {})), {})


class BudgetTests(unittest.TestCase):
    """The meter itself."""

    def setUp(self):
        limits._counter.reset()

    def test_the_owner_is_never_metered(self):
        for _ in range(limits.BUDGET_RUNS + 5):
            self.assertTrue(run(limits.spend("owner", unlimited=True)).allowed)

    def test_burst_refuses_and_says_when_to_come_back(self):
        with patch.object(limits, "BURST_LIMIT", 2):
            self.assertTrue(run(limits.spend("demo:alice")).allowed)
            self.assertTrue(run(limits.spend("demo:alice")).allowed)
            refused = run(limits.spend("demo:alice"))
        self.assertFalse(refused.allowed)
        self.assertEqual(refused.retry_after, limits.BURST_WINDOW)
        self.assertIn("Too fast", refused.reason)

    def test_one_visitor_cannot_exhaust_another(self):
        with patch.object(limits, "BURST_LIMIT", 1):
            self.assertTrue(run(limits.spend("demo:alice")).allowed)
            self.assertFalse(run(limits.spend("demo:alice")).allowed)
            self.assertTrue(run(limits.spend("demo:bob")).allowed)

    def test_the_daily_budget_stops_everybody(self):
        # The point of the global ceiling: a fresh visitor with an untouched
        # personal allowance is still refused once the day's money is gone.
        with patch.object(limits, "BUDGET_RUNS", 2), patch.object(limits, "BURST_LIMIT", 99):
            self.assertTrue(run(limits.spend("demo:a")).allowed)
            self.assertTrue(run(limits.spend("demo:b")).allowed)
            refused = run(limits.spend("demo:c"))
        self.assertFalse(refused.allowed)
        self.assertIn("budget", refused.reason.lower())

    def test_a_refused_spend_does_not_consume_budget(self):
        # A run that never started cost nothing. Charging for it would walk the
        # ceiling down every time somebody bounced off a limit.
        with patch.object(limits, "DAILY_LIMIT", 1), patch.object(limits, "BURST_LIMIT", 99):
            run(limits.spend("demo:alice"))
            run(limits.spend("demo:alice"))
            run(limits.spend("demo:alice"))
            snapshot = run(limits.snapshot())
        self.assertEqual(snapshot["budget"]["used"], 1)

    def test_a_refused_burst_does_consume_its_burst_count(self):
        # The opposite rule, for the opposite reason: if hammering were free,
        # a tight retry loop would slip through the window it exists to close.
        with patch.object(limits, "BURST_LIMIT", 1):
            run(limits.spend("demo:alice"))
            run(limits.spend("demo:alice"))
            again = run(limits.spend("demo:alice"))
        self.assertFalse(again.allowed)
        self.assertGreater(again.used["burst"], 2)

    def test_the_alert_fires_once_on_the_way_up(self):
        seen = []
        with patch.object(limits, "BUDGET_RUNS", 10), patch.object(limits, "ALERT_AT", 0.8), \
             patch.object(limits, "BURST_LIMIT", 99), patch.object(limits, "DAILY_LIMIT", 99), \
             patch("builtins.print", lambda *a, **k: seen.append(" ".join(map(str, a)))):
            for _ in range(10):
                run(limits.spend("demo:alice"))
        alerts = [line for line in seen if "ALERT" in line]
        self.assertEqual(len(alerts), 1, alerts)
        self.assertIn("8/10", alerts[0])

    def test_a_broken_counter_closes_the_door(self):
        # Not knowing what has been spent is not a reason to allow spending.
        async def explode(*args, **kwargs):
            raise ConnectionError("redis is gone")

        with patch.object(limits._counter, "bump", explode):
            refused = run(limits.spend("demo:alice"))
            allowed = run(limits.spend("owner", unlimited=True))
        self.assertFalse(refused.allowed)
        self.assertTrue(allowed.allowed)

    def test_the_budget_is_stated_in_money(self):
        note = limits.budget_note()
        self.assertIn("$", note)
        self.assertIn("/day", note)


if __name__ == "__main__":
    unittest.main()


class SuiteUploadTests(unittest.TestCase):
    """A folder sent as text: allowed, validated, and charged by the file.

    This is the step that made whole-suite conversion possible on a public host.
    The three things it had to get right are one test each: the request names no
    server path, a path that escapes is refused at the door, and twelve files
    cost twelve — not one.
    """

    def setUp(self):
        limits._counter.reset()

    def test_a_tree_is_allowed_where_a_root_is_not(self):
        tree = {"pages/LoginPage.ts": "export class LoginPage {}"}
        with patch.dict(os.environ, {"S2P_DAILY_LIMIT": "50"}, clear=False):
            self.assertTrue(run(guard.guard_run(
                VISITOR, {"kwargs": {"input": {"source_tree": tree}}})))
        with self.assertRaises(Exception) as caught:
            run(guard.guard_run(VISITOR, {"kwargs": {"input": {"root": "/etc"}}}))
        self.assertIn("root", str(caught.exception))

    def test_a_key_that_could_escape_the_workspace_is_refused(self):
        for bad in ("../../etc/cron.d/x", "/etc/passwd", "a/../../b.ts", "C:/x.ts"):
            with self.assertRaises(Exception, msg=bad) as caught:
                run(guard.guard_run(
                    VISITOR, {"kwargs": {"input": {"source_tree": {bad: "boom"}}}}))
            self.assertIn("relative path", str(caught.exception), bad)

    def test_a_tree_that_is_not_an_object_is_refused_rather_than_iterated(self):
        with self.assertRaises(Exception) as caught:
            run(guard.guard_run(
                VISITOR, {"kwargs": {"input": {"source_tree": ["pages/A.ts"]}}}))
        self.assertIn("object", str(caught.exception))

    def test_twelve_files_cost_twelve_not_one(self):
        """The reason `limits.spend` grew a `runs` argument.

        The meter counts runs, and a twelve-file suite is one run and twelve
        conversions. Charging it once would let a single request spend twelve
        times its share of a budget everybody is sharing.
        """
        tree = {f"pages/P{i}.ts": "export class P {}" for i in range(5)}
        before = run(limits.snapshot())
        run(guard.guard_run(VISITOR, {"kwargs": {"input": {"source_tree": tree}}}))
        after = run(limits.snapshot())
        self.assertEqual(after["budget"]["used"] - before["budget"]["used"], 5)

    def test_a_suite_too_big_for_what_is_left_is_refused_whole(self):
        # Half a suite converted and half refused is a worse answer than
        # "this needs 12 and 5 are left".
        with patch.object(limits, "BUDGET_RUNS", 4), patch.object(limits, "DAILY_LIMIT", 99):
            tree = {f"p{i}.ts": "x" for i in range(6)}
            with self.assertRaises(Exception) as caught:
                run(guard.guard_run(VISITOR, {"kwargs": {"input": {"source_tree": tree}}}))
            self.assertIn("6 conversions", str(caught.exception))
            # And the refusal gave the counts back, so the next caller is not
            # paying for a run that never started.
            self.assertEqual(run(limits.snapshot())["budget"]["used"], 0)

    def test_the_owner_still_skips_all_of_it(self):
        owner = FakeCtx("owner", ["owner"])
        self.assertTrue(run(guard.guard_run(
            owner, {"kwargs": {"input": {"root": "/anywhere", "out_root": "/tmp/out"}}})))

"""The web playground's server (`ui/server.py`), without a browser or a backend.

`ui/server.py` is the HTTP face of `playground.py` for the React page in
`ui/web`. Every decision it relies on is already tested in
`test_playground.py`; what is tested here is the translation — that a request
from the page becomes the right call into `playground`, and that what comes
back is the JSON the page was written against.

The SDK client is faked the same way `test_playground.py` fakes it: an object
with `threads.create()` and `runs.stream()` that yields scripted chunks. The
two streaming routes are read to the end through Starlette's test client, so a
test sees the same `data:` lines a browser does.
"""

from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import quote

import httpx
from fastapi.testclient import TestClient

from selenium2playwright import env
from selenium2playwright import playground as pg

# `ui` is a plain directory, importable from the repository root — which is
# where the suite runs from, but not necessarily where a lone test does.
sys.path.insert(0, str(env.REPO_ROOT))
from ui import server  # noqa: E402

THREAD = "11111111-1111-1111-1111-111111111111"
RUN = "22222222-2222-2222-2222-222222222222"

SELENIUM = "import { By } from 'selenium-webdriver';\nexport class P {}\n"
PLAYWRIGHT = "import { Page } from '@playwright/test';\nexport class P {}\n"

SNAPSHOT = {
    "budget": {"used": 5, "remaining": 36, "limit": 41},
    "budget_usd_per_day": 5,
    "per_visitor": {"daily": 15, "burst": 3, "window_s": 60},
}


def chunk(event: str, data):
    return SimpleNamespace(event=event, data=data)


def final_state(code: str = PLAYWRIGHT, status: str = "passed", todos=()):
    return {
        "iteration": 2,
        "models": {"actor": "openai:gpt-5.4", "critic": "openai:gpt-5.4"},
        "report": {
            "status": status,
            "attempts": 2,
            "reason": "All four gates and the critic passed.",
            "validation": [{"gate": g, "passed": True} for g in ("compile", "residue", "lint", "parity")],
            "critique": {"verdict": "pass"},
            "result": {"code": code, "todos": list(todos), "notes": ["kept the class name"]},
            "errors": [],
        },
    }


class FakeRuns:
    def __init__(self, chunks, raise_with: Exception | None = None):
        self.chunks, self.raise_with, self.calls = chunks, raise_with, []

    def stream(self, thread_id, assistant, **kwargs):
        self.calls.append({"thread_id": thread_id, "assistant": assistant, **kwargs})
        if self.raise_with:
            raise self.raise_with
        yield from self.chunks


class FakeThreads:
    def __init__(self):
        self.created = 0

    def create(self):
        self.created += 1
        return {"thread_id": THREAD}


class FakeClient:
    def __init__(self, chunks=(), raise_with=None):
        self.threads = FakeThreads()
        self.runs = FakeRuns(list(chunks), raise_with)
        self.visitor = ""


def conversion_chunks(state=None):
    """A run the way the SDK streams it: metadata, then updates, then values."""
    state = state or final_state()
    return [
        chunk("metadata", {"run_id": RUN}),
        chunk("updates", {"intake": {}}),
        chunk("updates", {"convert": {}}),
        chunk("updates", {"validate": {}}),
        chunk("updates", {"convert": {}}),
        chunk("updates", {"validate": {}}),
        chunk("values", state),
    ]


def events(response: httpx.Response) -> list[dict]:
    """Every `data:` line of a server-sent-event body, parsed."""
    found = []
    for line in response.text.splitlines():
        if line.startswith("data: "):
            found.append(json.loads(line[6:]))
    return found


class WebTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(server.app)
        self.fake = FakeClient(conversion_chunks())

        def make_client(url="", key="", visitor=""):
            self.fake.visitor = visitor
            return self.fake

        self.patches = [
            patch.object(pg, "client", make_client),
            patch.object(pg, "suite_client", make_client),
            patch.object(pg, "fetch_limits", lambda **_: dict(SNAPSHOT)),
        ]
        for p in self.patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in self.patches])

    # --- session ------------------------------------------------------------

    def test_session_mints_a_visitor_and_describes_the_budget(self):
        got = self.client.get("/api/session").json()
        self.assertRegex(got["visitor"], r"^pg-[0-9a-f]{12}$")
        self.assertIn("36 of 41", got["limits"]["line"])
        self.assertIn("Per visitor", got["limits"]["visitor_line"])
        self.assertTrue(got["backend"])
        self.assertIsInstance(got["samples"], list)

    def test_session_keeps_a_visitor_it_minted_and_replaces_anything_else(self):
        kept = self.client.get("/api/session", headers={"X-S2P-Visitor": "pg-0123456789ab"}).json()
        self.assertEqual(kept["visitor"], "pg-0123456789ab")
        replaced = self.client.get("/api/session", headers={"X-S2P-Visitor": "owner; DROP"}).json()
        self.assertNotEqual(replaced["visitor"], "owner; DROP")
        self.assertRegex(replaced["visitor"], r"^pg-[0-9a-f]{12}$")

    def test_samples_carry_their_companion(self):
        got = self.client.get("/api/session").json()
        names = {s["name"]: s for s in got["samples"]}
        if "login.spec.ts" not in names:
            self.skipTest("samples are not on this machine")
        spec = names["login.spec.ts"]
        self.assertEqual(spec["companion_name"], "LoginPage.ts")
        self.assertIn("@playwright/test", spec["companion_text"])
        self.assertIn("selenium-webdriver", spec["source"])

    def test_ok_is_the_health_check(self):
        self.assertEqual(self.client.get("/ok").json(), {"ok": True})

    # --- one file -----------------------------------------------------------

    def test_convert_streams_progress_then_the_scorecard(self):
        response = self.client.post(
            "/api/convert",
            json={"source": SELENIUM, "filename": "P.ts"},
            headers={"X-S2P-Visitor": "pg-0123456789ab"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.headers["content-type"].startswith("text/event-stream"))
        got = events(response)

        self.assertEqual(got[0], {"kind": "run", "run_id": RUN, "thread_id": THREAD})
        labels = [e["label"] for e in got if e["kind"] == "node"]
        self.assertEqual(labels[0], "Reading the file and working out what it is")
        # The reflection loop going round is numbered from the second lap on.
        self.assertIn("Converting to Playwright — attempt 2", labels)

        done = got[-1]
        self.assertEqual(done["kind"], "done")
        self.assertEqual(done["card"]["status"], "passed")
        self.assertTrue(done["card"]["passed"])
        self.assertEqual(done["card"]["gates_line"], "4/4 gates")
        self.assertEqual(done["card"]["code"], PLAYWRIGHT)
        self.assertEqual(done["download_name"], "P.ts")
        self.assertIn("-import { By }", done["diff"])
        self.assertIn("+import { Page }", done["diff"])
        self.assertEqual(len(done["trail"]), 5)

        # The visitor header reached the SDK client, and the request is the
        # one the guard accepts: text, a bare name, no paths.
        self.assertEqual(self.fake.visitor, "pg-0123456789ab")
        sent = self.fake.runs.calls[0]
        self.assertEqual(sent["assistant"], "convert")
        self.assertEqual(sent["input"]["source_text"], SELENIUM)
        self.assertEqual(sent["input"]["source_path"], "P.ts")
        self.assertFalse(sent["input"]["ask_risks"])
        self.assertEqual(self.fake.threads.created, 1)

    def test_convert_sends_the_companion_as_context_text(self):
        self.client.post("/api/convert", json={
            "source": SELENIUM, "filename": "spec.ts",
            "companion_name": "P.ts", "companion_text": PLAYWRIGHT,
        })
        sent = self.fake.runs.calls[0]["input"]
        self.assertEqual(sent["context_text"], {"P.ts": PLAYWRIGHT})

    def test_refine_reuses_the_thread_and_sends_the_instruction(self):
        self.client.post("/api/convert", json={
            "source": SELENIUM, "filename": "P.ts",
            "refinement": "Use getByRole.", "thread_id": THREAD,
        })
        self.assertEqual(self.fake.threads.created, 0)
        sent = self.fake.runs.calls[0]
        self.assertEqual(sent["thread_id"], THREAD)
        self.assertEqual(sent["input"]["refinement"], "Use getByRole.")

    def test_convert_refuses_what_the_guard_would_refuse_before_sending(self):
        empty = self.client.post("/api/convert", json={"source": "   ", "filename": "P.ts"})
        self.assertEqual(empty.status_code, 400)
        self.assertIn("Paste", empty.json()["detail"])

        path = self.client.post("/api/convert", json={"source": SELENIUM, "filename": "../etc/passwd"})
        self.assertEqual(path.status_code, 400)
        self.assertIn("plain name", path.json()["detail"])

        thread = self.client.post("/api/convert", json={"source": SELENIUM, "filename": "P.ts",
                                                        "thread_id": "../../owner"})
        self.assertEqual(thread.status_code, 400)
        self.assertEqual(self.fake.runs.calls, [])

    def test_a_refusal_is_an_outcome_not_a_crash(self):
        self.fake.runs.chunks = [
            chunk("metadata", {"run_id": RUN}),
            chunk("updates", {"refuse": {}}),
            chunk("values", {"refusal": "This is a Java file.", "iteration": 0}),
        ]
        got = events(self.client.post("/api/convert", json={"source": "class X {}", "filename": "X.java"}))
        done = got[-1]
        self.assertEqual(done["card"]["status"], "refused")
        self.assertEqual(done["card"]["reason"], "This is a Java file.")
        self.assertEqual(done["diff"], "")

    def test_a_backend_failure_arrives_as_a_sentence_in_the_stream(self):
        request = httpx.Request("POST", "https://s2p.fly.dev/threads")
        response = httpx.Response(429, request=request, headers={"Retry-After": "17"},
                                  json={"detail": "Slow down."})
        self.fake.runs.raise_with = httpx.HTTPStatusError("429", request=request, response=response)
        got = events(self.client.post("/api/convert", json={"source": SELENIUM, "filename": "P.ts"}))
        self.assertEqual(got[-1]["kind"], "error")
        self.assertIn("Slow down.", got[-1]["message"])
        self.assertIn("17s", got[-1]["message"])

    def test_an_unrecognized_failure_is_logged_here_and_not_streamed_there(self):
        """The visitor gets a sentence; the exception's own words go to the log.

        `explain`'s fallback is `f"{type(exc).__name__}: {exc}"`, and what an
        SDK puts in an exception is written for whoever raised it — a URL, a
        path, a header. Useful, and useful *here*: `fly logs` reads stderr.
        """
        secret = "/app/.env line 3: S2P_DEMO_KEY=sk-not-real"
        self.fake.runs.raise_with = RuntimeError(secret)

        stderr = io.StringIO()
        with patch("sys.stderr", stderr):
            got = events(self.client.post("/api/convert",
                                          json={"source": SELENIUM, "filename": "P.ts"}))

        self.assertEqual(got[-1]["kind"], "error")
        self.assertNotIn(secret, got[-1]["message"])
        self.assertNotIn("RuntimeError", got[-1]["message"])
        self.assertIn("has been logged", got[-1]["message"])
        self.assertIn(secret, stderr.getvalue())      # kept, where the owner can read it

    def test_feedback_passes_through_with_a_sentence(self):
        seen = {}

        def fake_send(run_id, score, **kwargs):
            seen.update(run_id=run_id, score=score, **kwargs)
            return {"stored": True, "queued": score < 1}

        with patch.object(pg, "send_feedback", fake_send):
            got = self.client.post("/api/feedback", json={
                "run_id": RUN, "score": 0, "comment": "wrong locator",
                "source_text": SELENIUM, "source_path": "P.ts",
            }, headers={"X-S2P-Visitor": "pg-0123456789ab"}).json()
        self.assertEqual(got["detail"], "Thank you.")
        self.assertEqual(seen["run_id"], RUN)
        self.assertEqual(seen["comment"], "wrong locator")
        self.assertEqual(seen["visitor"], "pg-0123456789ab")

    # --- a whole suite ------------------------------------------------------

    def _zip(self, files: dict[str, str]) -> bytes:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            for name, text in files.items():
                archive.writestr(name, text)
        return buffer.getvalue()

    def test_plan_reads_a_zip_and_returns_the_waves_and_the_price(self):
        data = self._zip({
            "suite/pages/P.ts": SELENIUM,
            "suite/tests/p.spec.ts": "import { P } from '../pages/P';\n" + SELENIUM,
            "suite/node_modules/x.ts": "junk",
        })
        got = self.client.post("/api/suite/plan", files={"files": ("suite.zip", data, "application/zip")},
                               data={"only": ""}).json()
        self.assertEqual(sorted(got["tree"]), ["pages/P.ts", "tests/p.spec.ts"])
        self.assertEqual(got["plan"]["files"], 2)
        self.assertEqual(got["plan"]["waves"], [["pages/P.ts"], ["tests/p.spec.ts"]])
        self.assertEqual(got["plan"]["billable"], 2)
        self.assertEqual(got["unaffordable"], "")

    def test_plan_says_when_the_budget_cannot_pay(self):
        with patch.object(pg, "fetch_limits", lambda **_: {**SNAPSHOT, "budget": {"remaining": 1, "limit": 41}}):
            got = self.client.post("/api/suite/plan",
                                   files={"files": ("suite.zip", self._zip({
                                       "a.ts": SELENIUM, "b.ts": SELENIUM}), "application/zip")}).json()
        self.assertIn("1 are left", got["unaffordable"])

    def test_plan_refuses_an_empty_drop_and_a_binary_file(self):
        empty = self.client.post("/api/suite/plan", data={"only": ""})
        self.assertEqual(empty.status_code, 400)
        binary = self.client.post("/api/suite/plan", files={"files": ("x.ts", b"\xff\xfe\x00", "text/plain")})
        self.assertEqual(binary.status_code, 400)
        self.assertIn("UTF-8", binary.json()["detail"])

    def test_the_sample_suite_is_a_planned_tree(self):
        response = self.client.get("/api/suite/sample")
        if response.status_code == 404:
            self.skipTest("samples are not on this machine")
        got = response.json()
        self.assertEqual(len(got["tree"]), 12)
        self.assertEqual(len(got["plan"]["waves"]), 2)
        self.assertEqual(got["plan"]["files"], 12)

    def test_suite_convert_ticks_files_off_then_reports(self):
        outcome = {"path": "pages/P.ts", "wave": 1, "status": "passed", "attempts": 1,
                   "reason": "ok", "gates": [["compile", True]], "critic": "pass",
                   "todos": [], "seconds": 3.5, "written": "pages/P.ts", "errors": []}
        self.fake.runs.chunks = [
            chunk("metadata", {"run_id": RUN}),
            chunk("updates", {"plan": {}}),
            chunk("updates", {"convert_file": {"outcomes": [outcome]}}),
            chunk("updates", {"finish": {}}),
            chunk("values", {"outcomes": [outcome], "elapsed": 4.0,
                             "converted_tree": {"pages/P.ts": PLAYWRIGHT},
                             "assembly": {"tree": {"passed": True, "findings": []},
                                          "files": 1, "ledgers": [], "todos": [],
                                          "notes": [], "report_path": "", "markdown": "# report"}}),
        ]
        response = self.client.post("/api/suite/convert", json={
            "tree": {"pages/P.ts": SELENIUM}, "only": "", "parallel": 2, "attempts": 1,
        })
        got = events(response)
        kinds = [e["kind"] for e in got]
        self.assertEqual(kinds, ["start", "node", "file", "node", "done"])
        self.assertEqual(got[0], {"kind": "start", "files": 1, "waves": 1})
        self.assertEqual(got[2]["row"]["path"], "pages/P.ts")
        self.assertEqual(got[2]["row"]["gates_line"], "1/1")
        result = got[-1]["result"]
        self.assertTrue(result["passed"])
        self.assertTrue(result["compiles"])
        self.assertEqual(result["tree"], {"pages/P.ts": PLAYWRIGHT})
        self.assertEqual(result["headline"], "1 passed · tree compiles · 4.0s")

        sent = self.fake.runs.calls[0]
        self.assertEqual(sent["assistant"], "suite")
        self.assertEqual(sent["input"]["source_tree"], {"pages/P.ts": SELENIUM})
        self.assertNotIn("root", sent["input"])
        self.assertEqual(sent["config"], {"max_concurrency": 2, "recursion_limit": 8})
        self.assertEqual(sent["context"]["max_attempts"], 1)

    def test_suite_zip_holds_the_files_and_the_report(self):
        response = self.client.post("/api/suite/zip", json={
            "tree": {"pages/P.ts": PLAYWRIGHT}, "markdown": "# report",
        })
        self.assertEqual(response.headers["content-type"], "application/zip")
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            self.assertEqual(sorted(archive.namelist()), ["conversion-report.md", "pages/P.ts"])

    # --- the page -----------------------------------------------------------

    def test_unknown_api_paths_are_404_not_the_page(self):
        self.assertEqual(self.client.get("/api/nothing").status_code, 404)

    def test_the_page_route_never_crashes_without_a_build(self):
        # 200 with a build present, 503 with a sentence without one — never 500.
        response = self.client.get("/")
        self.assertIn(response.status_code, (200, 503))
        if response.status_code == 503:
            self.assertIn("npm", response.json()["detail"])

    def test_a_url_cannot_climb_out_of_the_build_directory(self):
        """A visitor may read the page. Nothing above it.

        The route used to ask `(DIST / path).is_file()`, which is true for a
        path that has already climbed out — and the server percent-decodes the
        URL first, so `..` needs no literal dots to arrive. On Fly the file
        within reach is `/proc/self/environ`, and the model key is in it.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            dist = root / "dist"
            dist.mkdir()
            (dist / "index.html").write_text("<!doctype html><title>page</title>")
            (root / "environ").write_text("OPENAI_API_KEY=sk-the-thing-worth-stealing")

            with patch.object(server, "DIST", dist):
                # Not a vacuous test: the check this replaced answers yes here.
                self.assertTrue((dist / "../environ").is_file())
                self.assertIsNone(server.built_file("../environ"))

                response = self.client.get("/" + quote("../environ", safe=""))
                self.assertNotIn(b"sk-the-thing-worth-stealing", response.content)
                self.assertEqual(response.status_code, 200)

    def test_an_absolute_url_cannot_replace_the_build_directory(self):
        # `//etc/hosts` binds `path` to `/etc/hosts`, and `DIST / "/etc/hosts"`
        # is `/etc/hosts`: pathlib drops the left side for an absolute right one.
        self.assertTrue(Path("/etc/hosts").is_file())
        self.assertIsNone(server.built_file("/etc/hosts"))

    def test_the_files_the_page_is_built_from_are_still_served(self):
        # The check has to refuse the way out without refusing the way in.
        with tempfile.TemporaryDirectory() as tmp:
            dist = Path(tmp).resolve()
            (dist / "assets").mkdir()
            (dist / "assets" / "app.js").write_text("console.log(1)")
            (dist / "favicon.svg").write_text("<svg/>")

            with patch.object(server, "DIST", dist):
                self.assertEqual(server.built_file("favicon.svg"), dist / "favicon.svg")
                self.assertEqual(
                    server.built_file("assets/app.js"), dist / "assets" / "app.js"
                )
                self.assertIsNone(server.built_file("assets/never-built.js"))
                self.assertIsNone(server.built_file(""))


if __name__ == "__main__":
    unittest.main()

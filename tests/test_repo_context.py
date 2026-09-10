"""11.3b — the rest of the suite as reading material, and nothing else.

A companion is what the target imports: the compile gate needs it and the
prompt shows it. Everything else in the suite is *evidence* — callers, siblings
converted earlier in the run, the rest of the tree — and it reaches the prompt
only. The tests here pin both halves: what each job is handed, and that no gate
is ever handed any of it. Nothing here calls a model or reaches the network.
"""

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from test_suite_graph import TOY, FakeChild, build, report

from selenium2playwright import graph, suite_graph
from selenium2playwright.prompts import (DEFAULT_REPO_CONTEXT_BYTES, RepoEvidence, bound,
                                         format_context, repo_budget)
from selenium2playwright.schemas import ConversionResult

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "samples/selenium-suite"
POM = "pages/LoginPage.ts"


class DispatchEvidenceTests(unittest.TestCase):
    """What each branch is handed, on the toy suite, with the per-file graph scripted.

    BasePage → LoginPage → login.spec, plus a copied helper and a skipped
    Cypress file: three waves, and a different mix of companion and evidence
    in each.
    """

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = build(Path(self.tmp.name) / "src", TOY)
        self.out = Path(self.tmp.name) / "out"
        self.log = []
        respond = lambda inputs: {"status": "converted", "report": report(), "iteration": 1}  # noqa: E731
        with patch.object(suite_graph.single, "build_graph",
                          side_effect=lambda *a, **k: FakeChild(respond, self.log)):
            suite_graph.build_suite_graph().invoke(
                {"root": str(self.root), "out_root": str(self.out)},
                config={"max_concurrency": 8, "recursion_limit": 20})

    def job(self, name: str) -> dict:
        return next(c["inputs"] for c in self.log if c["inputs"]["source_path"].endswith(name))

    @staticmethod
    def names(paths) -> list[str]:
        return [Path(p).name for p in paths]

    def test_a_leaf_sees_its_caller_first_then_the_rest_and_never_a_skipped_file(self):
        base = self.job("BasePage.ts")
        self.assertEqual(base["context_paths"], [])  # unchanged: it imports nothing
        self.assertEqual(self.names(base["repo_paths"]), ["LoginPage.ts", "login.spec.ts", "users.ts"])
        self.assertEqual(self.names(base["caller_paths"]), ["LoginPage.ts"])
        self.assertEqual(self.names(base["pending_paths"]), ["LoginPage.ts", "login.spec.ts"])
        self.assertNotIn("old.cy.ts", self.names(base["repo_paths"]))
        # Nothing pending exists in the output tree yet, so it is read from the source.
        for path in base["pending_paths"]:
            self.assertTrue(path.startswith(str(self.root)), path)
        # The copied helper is in the output tree, and it is named as carried.
        self.assertEqual(base["carried_paths"], [str(self.out / "support/users.ts")])

    def test_a_companion_is_never_repeated_as_evidence(self):
        login = self.job("LoginPage.ts")
        self.assertEqual(login["context_paths"], [str(self.out / "pages/BasePage.ts")])
        self.assertEqual(self.names(login["caller_paths"]), ["login.spec.ts"])
        self.assertEqual(self.names(login["repo_paths"]), ["login.spec.ts", "users.ts"])

    def test_a_file_whose_whole_suite_is_its_companions_gets_no_evidence(self):
        spec = self.job("login.spec.ts")
        self.assertEqual(sorted(self.names(spec["context_paths"])),
                         ["BasePage.ts", "LoginPage.ts", "users.ts"])
        self.assertEqual((spec["repo_paths"], spec["caller_paths"], spec["pending_paths"]),
                         ([], [], []))
        self.assertEqual(spec["carried_paths"], [str(self.out / "support/users.ts")])


class DispatchShapesTests(unittest.TestCase):
    """The shapes the toy suite does not have, on a scripted manifest."""

    @staticmethod
    def state(tmp, files, wave):
        return {"waves": [wave], "wave": 1, "root": str(Path(tmp) / "src"),
                "out_root": str(Path(tmp) / "out"), "manifest": SimpleNamespace(files=files)}

    def test_a_cycle_mate_in_the_same_wave_is_a_pending_caller_read_from_the_source(self):
        """It imports the target and the target imports it; neither is converted yet."""
        with TemporaryDirectory() as tmp:
            build(Path(tmp) / "src", {"pages/A.ts": "a", "pages/B.ts": "b"})
            files = [SimpleNamespace(path="pages/A.ts", action="convert",
                                     imports=("pages/B.ts",), imported_by=("pages/B.ts",)),
                     SimpleNamespace(path="pages/B.ts", action="convert",
                                     imports=("pages/A.ts",), imported_by=("pages/A.ts",))]
            sends = suite_graph.dispatch(self.state(tmp, files, ["pages/A.ts", "pages/B.ts"]))
            a = sends[0].arg
            mate = str(Path(tmp) / "src/pages/B.ts")
            # Not a companion — there is nothing converted to hand the compiler —
            # but not invisible either, which it was before.
            self.assertEqual(a["context_paths"], [])
            self.assertEqual((a["repo_paths"], a["caller_paths"], a["pending_paths"]),
                             ([mate], [mate], [mate]))

    def test_a_converted_sibling_the_target_does_not_import_is_evidence_from_the_output_tree(self):
        with TemporaryDirectory() as tmp:
            build(Path(tmp) / "src", {"pages/Other.ts": "o", "pages/Me.ts": "m"})
            build(Path(tmp) / "out", {"pages/Other.ts": "converted"})
            files = [SimpleNamespace(path="pages/Other.ts", action="convert", imports=()),
                     SimpleNamespace(path="pages/Me.ts", action="convert", imports=())]
            me = suite_graph.dispatch(self.state(tmp, files, ["pages/Me.ts"]))[0].arg
            self.assertEqual(me["repo_paths"], [str(Path(tmp) / "out/pages/Other.ts")])
            self.assertEqual((me["caller_paths"], me["pending_paths"], me["carried_paths"]),
                             ([], [], []))

    def test_callers_come_before_converted_siblings_before_the_rest(self):
        with TemporaryDirectory() as tmp:
            build(Path(tmp) / "src", {"a/caller.ts": "", "b/done.ts": "", "c/later.ts": "",
                                      "d/helper.ts": "", "me.ts": ""})
            build(Path(tmp) / "out", {"b/done.ts": "", "d/helper.ts": ""})
            files = [SimpleNamespace(path="me.ts", action="convert", imports=(),
                                     imported_by=("c/later.ts",)),
                     SimpleNamespace(path="a/caller.ts", action="convert", imports=()),
                     SimpleNamespace(path="b/done.ts", action="convert", imports=()),
                     SimpleNamespace(path="c/later.ts", action="convert", imports=("me.ts",)),
                     SimpleNamespace(path="d/helper.ts", action="copy", imports=()),
                     SimpleNamespace(path="e/old.cy.ts", action="skip", imports=())]
            me = suite_graph.dispatch(self.state(tmp, files, ["me.ts"]))[0].arg
            self.assertEqual([Path(p).name for p in me["repo_paths"]],
                             ["later.ts", "done.ts", "caller.ts", "helper.ts"])
            self.assertEqual(me["carried_paths"], [str(Path(tmp) / "out/d/helper.ts")])

    def test_the_child_graph_is_handed_all_three_lists(self):
        job = {"path": "p.ts", "wave": 1, "source_path": "s", "output_path": "o",
               "repo_paths": ["r"], "caller_paths": ["c"], "pending_paths": ["p"]}
        seen = {}

        class Child:
            def invoke(self, inputs, **kwargs):
                seen.update(inputs)
                return {"status": "refused", "refusal": "scripted"}

        with patch.object(suite_graph.single, "build_graph", return_value=Child()):
            suite_graph.convert_file(job)
        self.assertEqual((seen["repo_paths"], seen["caller_paths"], seen["pending_paths"]),
                         (["r"], ["c"], ["p"]))


class IntakeTests(unittest.TestCase):
    """The evidence is read at intake, cut to budget, and rendered after the companions."""

    def setUp(self):
        self.folder = TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        base = Path(self.folder.name)
        self.done = base / "pages/Done.ts"
        self.caller = base / "specs/caller.spec.ts"
        self.sibling = base / "pages/Sibling.ts"
        self.copied = base / "support/helper.ts"
        self.big = base / "reports/app.js"
        build(base, {"pages/Done.ts": "export class Done {}\n",
                     "specs/caller.spec.ts": "import { LoginPage } from '../pages/LoginPage';\n",
                     "pages/Sibling.ts": "export class Sibling {}\n",
                     "support/helper.ts": "export const h = 1;\n",
                     "reports/app.js": "x" * (40 * 1024)})
        self.inputs = {"source_path": str(SOURCE / POM), "context_paths": [str(self.done)],
                       "repo_paths": [str(p) for p in (self.caller, self.sibling, self.copied, self.big)],
                       "caller_paths": [str(self.caller)],
                       "pending_paths": [str(self.caller), str(self.sibling)],
                       "carried_paths": [str(self.copied)]}

    def test_evidence_is_snapshotted_apart_from_the_companions(self):
        state = graph.intake(self.inputs)
        self.assertEqual(set(state["context_files"]), {str(self.done.resolve())})
        self.assertEqual(list(state["repo_files"]),
                         [str(p.resolve()) for p in (self.caller, self.sibling, self.copied)])
        self.assertEqual(state["repo_omitted"], [(str(self.big.resolve()), 40 * 1024)])

    def test_the_prompt_labels_each_file_and_names_what_was_left_out(self):
        text = graph.intake(self.inputs)["context"]
        self.assertIn(f'<caller_file path="{self.caller.resolve()}" status="pending">', text)
        self.assertIn(f'<suite_file path="{self.sibling.resolve()}" status="pending">', text)
        self.assertIn(f'<suite_file path="{self.copied.resolve()}" status="unconverted">', text)
        self.assertIn(f"1 further file in the suite was left out for size: {self.big.resolve()} (40 KB).", text)
        self.assertNotIn("xxxx", text)
        # Companions first, then callers, then the rest: the order the model reads.
        self.assertLess(text.index("ALREADY converted"), text.index("These files IMPORT"))
        self.assertLess(text.index("These files IMPORT"), text.index("The rest of the suite"))
        self.assertEqual(text, format_context([self.done], repo=RepoEvidence(
            contents={str(p.resolve()): p.read_text() for p in (self.caller, self.sibling, self.copied)},
            callers=frozenset({str(self.caller.resolve())}),
            pending=frozenset({str(self.caller.resolve()), str(self.sibling.resolve())}),
            carried=frozenset({str(self.copied.resolve())}),
            omitted=((str(self.big.resolve()), 40 * 1024),))))

    def test_without_evidence_the_prompt_is_byte_identical_to_before(self):
        state = graph.intake({"source_path": str(SOURCE / POM), "context_paths": [str(self.done)]})
        self.assertEqual(state["context"], format_context([self.done]))
        self.assertEqual((state["repo_files"], state["repo_omitted"]), ({}, []))

    def test_a_budget_of_zero_turns_the_evidence_off_without_a_deploy(self):
        with patch.dict(os.environ, {"S2P_REPO_CONTEXT_BYTES": "0"}):
            state = graph.intake(self.inputs)
        self.assertEqual(state["context"], format_context([self.done]))
        self.assertEqual(state["repo_files"], {})

    def test_evidence_alone_is_still_a_prompt_section(self):
        """A wave-1 file imports nothing, so it has no companions — only callers."""
        state = graph.intake({"source_path": str(SOURCE / POM),
                              "repo_paths": [str(self.caller)], "caller_paths": [str(self.caller)],
                              "pending_paths": [str(self.caller)]})
        self.assertEqual(state["context_files"], {})
        self.assertIn("<caller_file", state["context"])
        self.assertTrue(state["context"].endswith("</caller_file>\n\n"))


class GatesNeverSeeEvidenceTests(unittest.TestCase):
    """The whole point. A broken file nobody converted cannot fail the file somebody did."""

    def test_a_repo_file_that_cannot_compile_does_not_reach_the_compile_gate(self):
        clean = ('import { Page } from "@playwright/test";\n'
                 "export default class Index {\n"
                 "  constructor(private readonly page: Page) {}\n"
                 "}\n")
        out = graph.validate({
            "output_path": "/w/pages/Index.ts", "source_path": "/w/pages/Index.ts",
            "source": "import { By } from 'selenium-webdriver';\nexport class I {}\n",
            "context_files": {},
            "repo_files": {"/w/lib/broken.ts": "export const n: number = 'not a number';\n"},
            "result": ConversionResult(code=clean, notes=(), todos=()),
        })
        compile_report = next(r for r in out["validation"] if r.gate == "compile")
        self.assertTrue(compile_report.passed, compile_report.render())
        self.assertEqual(compile_report.excused, [])  # not excused: never seen

    def test_the_compile_gate_is_handed_the_companions_and_nothing_else(self):
        seen = {}

        def fake_compile(files, carried=()):
            seen["files"] = dict(files)
            from selenium2playwright.schemas import ValidationReport
            return ValidationReport(gate="compile", passed=True)

        with patch.object(graph, "compile_check", side_effect=fake_compile), \
                patch.object(graph, "residue_check"), patch.object(graph, "lint_check"), \
                patch.object(graph, "parity_check"):
            graph.validate({
                "output_path": "/w/pages/Index.ts", "source_path": "/w/pages/Index.ts",
                "source": "x", "context_files": {"/w/pages/Login.ts": "export class L {}\n"},
                "repo_files": {"/w/specs/a.spec.ts": "caller", "/w/lib/x.ts": "helper"},
                "result": ConversionResult(code="export {};\n"),
            })
        # Keys are relative to the common folder, as `validate` has always cut them.
        self.assertEqual(set(seen["files"]), {"Index.ts", "Login.ts"})


class BudgetTests(unittest.TestCase):
    """`bound` keeps a prefix of what it is given: relevance order is the caller's job."""

    def test_the_tail_is_dropped_once_the_budget_is_spent(self):
        kept, omitted = bound({"a": "x" * 10, "b": "x" * 10, "c": "x" * 10}, budget=25)
        self.assertEqual((list(kept), omitted), (["a", "b"], (("c", 10),)))

    def test_a_file_over_the_ceiling_is_left_out_without_spending_the_budget(self):
        kept, omitted = bound({"bundle": "x" * 50, "b": "x" * 10}, budget=40, ceiling=32)
        self.assertEqual((list(kept), omitted), (["b"], (("bundle", 50),)))

    def test_the_budget_is_read_from_the_environment_at_call_time(self):
        with patch.dict(os.environ, {"S2P_REPO_CONTEXT_BYTES": "4096"}):
            self.assertEqual(repo_budget(), 4096)
            self.assertEqual(bound({"a": "x" * 5000})[0], {})
        with patch.dict(os.environ, {"S2P_REPO_CONTEXT_BYTES": ""}):
            self.assertEqual(repo_budget(), DEFAULT_REPO_CONTEXT_BYTES)

    def test_sizes_are_reported_in_whole_kilobytes_rounded_up(self):
        text = format_context([], repo=RepoEvidence(contents={}, omitted=(("a.ts", 100), ("b.ts", 3000))))
        self.assertEqual(text, "2 further files in the suite were left out for size: a.ts (1 KB), b.ts (3 KB).\n\n")

    def test_nothing_at_all_is_still_the_empty_string(self):
        self.assertEqual(format_context([]), "")
        self.assertEqual(format_context([], repo=None), "")
        self.assertEqual(format_context([], repo=RepoEvidence(contents={})), "")


if __name__ == "__main__":
    unittest.main()

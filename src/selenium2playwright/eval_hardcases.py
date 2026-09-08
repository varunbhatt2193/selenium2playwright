"""Step 11.1 — the twelve SDET hard cases, as an explicit second benchmark.

The Phase 6.1 set measures whether the converter handles ordinary page objects
and tests. It does not measure the patterns experienced SDETs actually lose
sleep over: the ones where a mechanical translation compiles, passes lint, and
silently tests something else. `plan-review.md` listed twelve of those in
priority order. This module names each one, points it at the fixture that
exercises it, and refuses to pretend a case is covered when it is not.

Ten are covered by the new `samples/selenium-hard-suite` pair. Two — dialog
ordering and window handles — were already covered by the Phase 6.1 set, and
are cross-referenced here rather than duplicated. Importing this module reads
no files and performs no model or LangSmith calls.
"""

from dataclasses import dataclass

from selenium2playwright.eval_dataset import DatasetCase

SOURCE_DIR = "selenium-hard-suite"
GOLDEN_DIR = "playwright-hard-golden"


@dataclass(frozen=True)
class HardCase(DatasetCase):
    """A dataset case that also records which hard cases it is here to exercise.

    ``covers`` are keys of HARD_CASES. ``browser_evidence_from`` names the test
    row whose browser run exercises this file — a page object has no browser
    tests of its own, and a shared base class is exercised by its subclasses.
    """

    covers: tuple[int, ...] = ()
    browser_evidence_from: str = ""


# The list from plan-review.md, in its original priority order. The numbers are
# stable identifiers: reports and the roadmap refer to "hard case 7", not to a
# position in this dict.
HARD_CASES: dict[int, str] = {
    1: "Custom driver.wait(async predicate) polling → web-first assertion or expect(...).toPass()",
    2: "switchTo().alert() accept/dismiss → page.on('dialog') registered BEFORE the triggering action",
    3: "until.stalenessOf / stale-element retry loops → wait for the new state instead",
    4: "Stateful switchTo().frame()/defaultContent() across methods → stateless frameLocator() scoping",
    5: "getAllWindowHandles() + switchTo().window() → waitForEvent('popup'); the method returns the new Page",
    6: "Implicit-wait config + findElements().length presence/absence → toHaveCount() with the same patience",
    7: "findElements loops that mutate the DOM (delete-all) → re-query loop, not a stale snapshot",
    8: "Action chains: hover menus, modifier clicks, drag-and-drop",
    9: "executeScript workarounds (JS click, scrollIntoView) → delete, or locator.evaluate()",
    10: "BasePage wait helpers → inline or delete with a ledger entry, not mechanical preservation",
    11: "beforeAll shared driver + login → serial/fixtures/storageState; surface the order dependence",
    12: "Promise-chained legacy code with no await → insert awaits without reordering side effects",
}

# Already exercised by the Phase 6.1 benchmark; recorded so that "twelve cases"
# means twelve, and so a reader can find the fixture rather than assume one.
COVERED_BY_BASE_DATASET: dict[int, str] = {
    2: "alerts-page / alerts-test in the Phase 6.1 set (samples/selenium-suite/pages/AlertsPage.ts)",
    5: "windows-page / windows-test in the Phase 6.1 set (samples/selenium-suite/pages/WindowsPage.ts)",
}

# Planned browser tests per test row. A planning target, not a measured count.
PLANNED_BROWSER_TEST_COUNTS = {
    "dynamic-controls-test": 2,
    "add-remove-test": 1,
    "nested-frames-test": 1,
    "hovers-test": 1,
    "shared-session-test": 2,
}

REVIEW = (
    "Agent-authored and agent-reviewed 2026-09-08 for Step 11.1: goldens written against the live "
    "pages, both suites green in a real browser, and all four static gates passing over the whole "
    "golden tree. See docs/hard-cases.md and docs/evaluation-hard-fixture-evidence.json."
)

CASES: tuple[HardCase, ...] = (
    # The shared base class is the hard case: converting it well means deleting
    # almost all of it, which no compiler or lint rule will ever ask for.
    HardCase(
        case_id="base-page", scenario="base", kind="page-object", path="pages/BasePage.ts",
        expected_behaviors=(
            "Expose a way to open a page of the demo site by relative path.",
            "The inherited Selenium wait helpers are removed rather than reimplemented, "
            "because Playwright's actions and assertions already provide those guarantees.",
        ),
        covers=(10, 1), browser_evidence_from="dynamic-controls-test",
        reference_review="reviewed", review_note=REVIEW,
    ),
    HardCase(
        case_id="dynamic-controls-page", scenario="dynamic-controls", kind="page-object",
        path="pages/DynamicControlsPage.ts",
        expected_behaviors=(
            "Remove and re-add the checkbox on /dynamic_controls.",
            "Let the caller check that the checkbox is absent, with the same patience the "
            "implicit wait gave the Selenium version.",
            "Enable the disabled text field and accept typed input into it.",
        ),
        companions=("pages/BasePage.ts",),
        covers=(3, 6, 1), browser_evidence_from="dynamic-controls-test",
        reference_review="reviewed", review_note=REVIEW,
    ),
    HardCase(
        case_id="dynamic-controls-test", scenario="dynamic-controls", kind="test",
        path="tests/dynamic-controls.spec.ts",
        expected_behaviors=(
            "Removing the checkbox reports \"It's gone!\" and leaves no checkbox present.",
            "Enabling the text field reports \"It's enabled!\" and the field then accepts input.",
        ),
        companions=("pages/DynamicControlsPage.ts", "pages/BasePage.ts"),
        covers=(3, 6, 1), reference_review="reviewed", review_note=REVIEW,
    ),
    HardCase(
        case_id="add-remove-page", scenario="add-remove", kind="page-object",
        path="pages/AddRemovePage.ts",
        expected_behaviors=(
            "Add a requested number of rows to /add_remove_elements/.",
            "Delete every row, re-resolving the remaining ones after each removal.",
            "The JavaScript click and scrollIntoView workarounds are not carried over.",
        ),
        covers=(7, 9), browser_evidence_from="add-remove-test",
        reference_review="reviewed", review_note=REVIEW,
    ),
    HardCase(
        case_id="add-remove-test", scenario="add-remove", kind="test",
        path="tests/add-remove.spec.ts",
        expected_behaviors=("Five rows are added, counted, deleted, and counted again as zero.",),
        companions=("pages/AddRemovePage.ts",),
        covers=(7, 9), reference_review="reviewed", review_note=REVIEW,
    ),
    HardCase(
        case_id="nested-frames-page", scenario="nested-frames", kind="page-object",
        path="pages/NestedFramesPage.ts",
        expected_behaviors=(
            "Reach the content of frame-middle, which is nested inside frame-top.",
            "Reach the content of frame-bottom without depending on which frame was read first.",
        ),
        covers=(4,), browser_evidence_from="nested-frames-test",
        reference_review="reviewed", review_note=REVIEW,
    ),
    HardCase(
        case_id="nested-frames-test", scenario="nested-frames", kind="test",
        path="tests/nested-frames.spec.ts",
        expected_behaviors=("The middle frame reads MIDDLE and the bottom frame reads BOTTOM.",),
        companions=("pages/NestedFramesPage.ts",),
        covers=(4,), reference_review="reviewed", review_note=REVIEW,
    ),
    HardCase(
        case_id="hovers-page", scenario="hovers", kind="page-object", path="pages/HoversPage.ts",
        expected_behaviors=(
            "Hover over a chosen avatar on /hovers so its caption is revealed.",
            "Let the caller read that caption's name and check which profile links are visible.",
        ),
        covers=(8,), browser_evidence_from="hovers-test",
        reference_review="reviewed", review_note=REVIEW,
    ),
    HardCase(
        case_id="hovers-test", scenario="hovers", kind="test", path="tests/hovers.spec.ts",
        expected_behaviors=(
            "Hovering the second avatar shows \"name: user2\" and its profile link, "
            "while a third avatar's link stays hidden.",),
        companions=("pages/HoversPage.ts",),
        covers=(8,), reference_review="reviewed", review_note=REVIEW,
    ),
    HardCase(
        case_id="shared-session-page", scenario="shared-session", kind="page-object",
        path="pages/SecureAreaPage.ts",
        expected_behaviors=(
            "Log in at /login with the supplied credentials.",
            "Reload the current page and expose the flash message and the secure-area heading.",
        ),
        companions=("pages/BasePage.ts",),
        covers=(11, 10), browser_evidence_from="shared-session-test",
        reference_review="reviewed", review_note=REVIEW,
    ),
    HardCase(
        case_id="shared-session-test", scenario="shared-session", kind="test",
        path="tests/shared-session.spec.ts",
        expected_behaviors=(
            "One login serves both tests; the second test does not log in again.",
            "The first test sees the secure-area flash message.",
            "The second test reloads and still sees the Secure Area heading.",
            "The order dependence survives conversion instead of being silently removed.",
        ),
        companions=("pages/SecureAreaPage.ts", "pages/BasePage.ts"),
        covers=(11, 12), reference_review="reviewed", review_note=REVIEW,
    ),
)

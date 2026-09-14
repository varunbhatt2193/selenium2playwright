"""Step 6.1 — the explicit coverage plan for the first conversion dataset.

CASES describes 12 intended file conversions. Pending entries may still need
implementation or reference review; they are not upload-ready examples.
Importing this module reads no files and performs no model or LangSmith calls.
"""

from selenium2playwright.eval_dataset import DatasetCase


# These are planned browser tests, not conversion-row counts or measured passes.
# Keys match test-file case IDs below; POM rows have no browser tests of their own.
PLANNED_BROWSER_TEST_COUNTS = {
    "login-test": 2,
    "alerts-test": 2,
    "iframe-test": 1,
    "windows-test": 1,
    "upload-test": 1,
    "dynamic-loading-test": 1,
}

# Existing goldens were user-reviewed in Phase 1.3; new references default to pending.
LOGIN_REVIEW = "User-approved 2026-09-04 in roadmap.md Step 1.3; see docs/evaluation-coverage.md."
# Delegated completion includes agent curation; do not mislabel it as user review.
COMPLETION_REVIEW = (
    "Agent-reviewed 2026-09-05 under user instruction to finish 6.1; independently authored "
    "golden, source/reference browser checks and static gates passed. "
    "See docs/evaluation-fixtures.md and docs/evaluation-fixture-evidence.json."
)

CASES: tuple[DatasetCase, ...] = (
    # Login supplies the existing baseline: one POM and a file containing two tests.
    DatasetCase(
        case_id="login-page", scenario="login", kind="page-object", path="pages/LoginPage.ts",
        expected_behaviors=("Open /login and submit the supplied username and password.",
                            "Expose the flash message for the caller to check."),
        reference_review="reviewed", review_note=LOGIN_REVIEW,
    ),
    DatasetCase(
        case_id="login-test", scenario="login", kind="test", path="tests/login.spec.ts",
        expected_behaviors=("Valid credentials produce the secure-area success message.",
                            "An invalid password produces the password error message."),
        companions=("pages/LoginPage.ts",),
        reference_review="reviewed", review_note=LOGIN_REVIEW,
    ),
    # Dialog actions must preserve accept versus dismiss; clicking alone is insufficient.
    DatasetCase(
        case_id="alerts-page", scenario="alerts", kind="page-object", path="pages/AlertsPage.ts",
        expected_behaviors=("Open /javascript_alerts and accept the simple JavaScript alert.",
                            "Dismiss the JavaScript confirmation and expose the result text."),
        reference_review="reviewed",
        review_note="User confirmed the AlertsPage step understood on 2026-09-05; docs/alerts-page-objects.md.",
    ),
    DatasetCase(
        case_id="alerts-test", scenario="alerts", kind="test", path="tests/alerts.spec.ts",
        expected_behaviors=("Accepting the simple alert produces the alert-success result.",
                            "Dismissing the confirmation produces the cancellation result."),
        companions=("pages/AlertsPage.ts",),
        reference_review="reviewed",
        review_note="User approved committing the alerts tests and browser evidence on 2026-09-05; docs/alerts-tests.md.",
    ),
    # This covers frame scoping and parent access, not the earlier editor-typing failure.
    DatasetCase(
        case_id="iframe-page", scenario="iframe", kind="page-object", path="pages/IframePage.ts",
        expected_behaviors=("Open /iframe and access the editor body inside its iframe.",
                            "Allow subsequent access to the heading on the parent page."),
        reference_review="reviewed", review_note=COMPLETION_REVIEW,
    ),
    DatasetCase(
        case_id="iframe-test", scenario="iframe", kind="test", path="tests/iframe.spec.ts",
        expected_behaviors=("Read 'Your content goes here.' inside the iframe, then verify the parent heading.",),
        companions=("pages/IframePage.ts",),
        reference_review="reviewed", review_note=COMPLETION_REVIEW,
    ),
    # A popup must remain a separate page; checking the original page's heading is wrong.
    DatasetCase(
        case_id="windows-page", scenario="windows", kind="page-object", path="pages/WindowsPage.ts",
        expected_behaviors=("Open /windows and follow its link into a new browser window.",
                            "Provide access to the new window and preserve access to the original."),
        reference_review="reviewed", review_note=COMPLETION_REVIEW,
    ),
    DatasetCase(
        case_id="windows-test", scenario="windows", kind="test", path="tests/windows.spec.ts",
        expected_behaviors=("Verify the new window's heading, close it, then verify the original page is usable.",),
        companions=("pages/WindowsPage.ts",),
        reference_review="reviewed", review_note=COMPLETION_REVIEW,
    ),
    # Generate the upload fixture within the test so the future snapshot is self-contained.
    DatasetCase(
        case_id="upload-page", scenario="upload", kind="page-object", path="pages/UploadPage.ts",
        expected_behaviors=("Open /upload, select the caller's file, and submit the upload.",
                            "Expose the upload confirmation and uploaded filename."),
        reference_review="reviewed", review_note=COMPLETION_REVIEW,
    ),
    DatasetCase(
        case_id="upload-test", scenario="upload", kind="test", path="tests/upload.spec.ts",
        expected_behaviors=("Upload a test-created file and verify confirmation plus its exact filename; clean up the fixture.",),
        companions=("pages/UploadPage.ts",),
        reference_review="reviewed", review_note=COMPLETION_REVIEW,
    ),
    # Example 2 inserts an element later: immediate reads can miss it even when code compiles.
    DatasetCase(
        case_id="dynamic-loading-page", scenario="dynamic-loading", kind="page-object",
        path="pages/DynamicLoadingPage.ts",
        expected_behaviors=("Open /dynamic_loading/2 and start loading the delayed element.",
                            "Expose the completed content so the caller can wait for and check it."),
        reference_review="reviewed", review_note=COMPLETION_REVIEW,
    ),
    DatasetCase(
        case_id="dynamic-loading-test", scenario="dynamic-loading", kind="test",
        path="tests/dynamic-loading.spec.ts",
        expected_behaviors=("Start loading, wait for the delayed content, and verify its completion text.",),
        companions=("pages/DynamicLoadingPage.ts",),
        reference_review="reviewed", review_note=COMPLETION_REVIEW,
    ),
)

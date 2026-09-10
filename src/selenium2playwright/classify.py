"""Step 3.2 (part 1) — what is this file? Heuristics only, no LLM.

Before spending a model call, decide honestly whether we can convert the file
at all: which automation library, which test runner, which language. Anything
outside the MVP (TypeScript + selenium-webdriver + Mocha/Jest) is refused with
a reason — an honest "no" is a feature, not a failure (plan.md, playbook honesty).

Regex on import lines is enough here; the AST option is reserved for the day a
regex demonstrably misclassifies a real file (same policy as the validators).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from pathlib import PurePath

# order matters: the first pattern that matches wins.
AUTOMATION_SIGNATURES: list[tuple[str, str]] = [
    ("playwright", r"""from\s+['"]@playwright/test['"]|require\(['"]@playwright/test['"]\)"""),
    ("webdriverio", r"""from\s+['"](webdriverio|@wdio/[\w-]+)['"]|\bbrowser\.(url|\$)\(|(?<![\w$])\$\$?\(['"]"""),
    ("cypress", r"""\bcy\.[a-zA-Z]+\(|from\s+['"]cypress['"]"""),
    ("puppeteer", r"""from\s+['"]puppeteer(-core)?['"]|require\(['"]puppeteer"""),
    ("selenium", r"""from\s+['"]selenium-webdriver(/[\w-]+)?['"]|require\(['"]selenium-webdriver"""),
    # Other languages' Selenium bindings — recognised so the refusal can say
    # "wrong language" (on the roadmap) instead of "unknown library".
    ("selenium", r"""org\.openqa\.selenium|OpenQA\.Selenium|^\s*from\s+selenium(\.\w+)*\s+import"""),
]

RUNNER_SIGNATURES: list[tuple[str, str]] = [
    ("jest", r"""from\s+['"]@jest/globals['"]|\bjest\.(fn|mock|setTimeout|spyOn)\("""),
    ("mocha", r"""from\s+['"](mocha|chai)['"]|\bthis\.timeout\(|\bbefore\(|\bafter\("""),
    # Bare describe/it with no other hint: assume Mocha, the common Selenium pairing.
    ("mocha", r"""\bdescribe\(|\bit\("""),
]

LANGUAGE_BY_SUFFIX = {".ts": "typescript", ".tsx": "typescript", ".js": "javascript",
                      ".mjs": "javascript", ".java": "java", ".py": "python", ".cs": "csharp"}


@dataclass(frozen=True)
class Classification:
    automation: str  # selenium | webdriverio | cypress | puppeteer | playwright | unknown
    runner: str  # mocha | jest | none (a page object has no tests — that is fine)
    language: str  # typescript | javascript | java | python | csharp | unknown
    supported: bool
    reason: str  # human-readable; shown to the user verbatim on refusal
    # The in-suite file through which this one reaches Selenium when it has no
    # selenium-webdriver import of its own (see `through`). "" for a file that
    # imports Selenium directly, and for one that never reaches it at all.
    via: str = ""


def _first_match(signatures: list[tuple[str, str]], source: str) -> str:
    for name, pattern in signatures:
        if re.search(pattern, source, flags=re.MULTILINE):
            return name
    return "unknown"


def classify(source: str, path: str) -> Classification:
    """Decide support from the file's imports and extension. Pure function."""
    language = LANGUAGE_BY_SUFFIX.get(PurePath(path).suffix.lower(), "unknown")
    automation = _first_match(AUTOMATION_SIGNATURES, source)
    runner = _first_match(RUNNER_SIGNATURES, source).replace("unknown", "none")

    if automation == "playwright":
        return Classification(automation, runner, language, False,
                              "already a Playwright file — nothing to convert")
    if automation != "selenium":
        found = automation if automation != "unknown" else "no recognised automation library"
        return Classification(automation, runner, language, False,
                              f"{found} detected; only selenium-webdriver is supported")
    if language != "typescript":
        return Classification(automation, runner, language, False,
                              f"{language} source detected; v1 converts TypeScript only "
                              "(other Selenium languages are on the roadmap)")
    kind = "page object / helper" if runner == "none" else f"{runner} tests"
    return Classification(automation, runner, language, True, f"selenium-webdriver {kind} in TypeScript")


def through(base: Classification, via: str) -> Classification:
    """`base` read again, now that it is known to reach Selenium through `via`.

    classify() sees one file alone, and alone is where it is wrong about a
    repository that wraps WebDriver in its own abstraction layer. There, a page
    object imports `../lib` and never `selenium-webdriver`, so on its own it
    reads as "no recognised automation library" — and in a suite that answer
    means *copy*, so it is carried into the converted tree untouched and sits
    there as Selenium forever. Measured on one real repo: 4 of 16 files import
    Selenium, 13 of 16 reach it; the nine in between were every page object,
    the spec, and the barrel that imports the driver.

    Only the suite scanner knows the import graph, so only it can say a file
    reaches Selenium; this is the classification it says it with. The answer
    is the one classify() would have given had the import been direct — same
    kind, same language rule, same refusal for a language v1 does not convert —
    with the path it was reached through kept, because "converted: page object
    driving Selenium through lib/index.ts" is the line a reader of the report
    needs and a bare "converted" is not.

    A file whose library *was* recognised is left alone: a Playwright spec that
    imports a Selenium helper is still a Playwright spec, and a Cypress one is
    still refused as Cypress. Only "unknown" is a verdict this may revise.
    """
    if base.automation != "unknown":
        return base
    if base.language != "typescript":
        return replace(base, automation="selenium", via=via,
                       reason=f"{base.language} source reaching Selenium through {via}; "
                              "v1 converts TypeScript only (other Selenium languages are "
                              "on the roadmap)")
    kind = "page object / helper" if base.runner == "none" else f"{base.runner} tests"
    return Classification("selenium", base.runner, base.language, True,
                          f"{kind} in TypeScript reaching Selenium through {via}", via=via)

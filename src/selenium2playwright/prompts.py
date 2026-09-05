"""Prompt v1: the playbook is the system prompt; the source file is the human turn.

Message layout (decided in Phase 1): a *static* prefix — role + playbook — that
is byte-identical on every call, followed by the *variable* part — the file to
convert. Static-first is what lets any provider's prompt cache work; the
provider-specific cache marker itself is applied in llm.prepare_messages(),
so this file is pure LangChain and knows nothing about vendors.
"""

from __future__ import annotations

from pathlib import Path

from langchain_core.messages import SystemMessage
from langchain_core.prompts import ChatPromptTemplate

REPO_ROOT = Path(__file__).resolve().parents[2]
PLAYBOOK_PATH = REPO_ROOT / "docs" / "playbook.md"

ROLE = (
    "You are a senior SDET migrating a TypeScript Selenium WebDriver test suite "
    "(Mocha + chai) to Playwright Test (TypeScript). Follow the playbook below "
    "exactly. Reply with ONLY the converted file contents — no prose, no "
    "markdown fences — so the reply can be written straight to disk.\n\n"
)

# Braces in the human turn are template placeholders, so this text has none of
# its own. The playbook has plenty ({ page }, { name }) — which is why it goes in
# via a literal SystemMessage below, which ChatPromptTemplate never templates.
HUMAN = (
    "Convert this file: {file_path}\n\n"
    "{context}"
    "<source_file>\n{source}\n</source_file>"
)


def load_playbook() -> str:
    """The rulebook, read fresh from disk so a playbook edit is a prompt edit."""
    return PLAYBOOK_PATH.read_text(encoding="utf-8")


def build_prompt() -> ChatPromptTemplate:
    """System = ROLE + playbook (static prefix); human = the file to convert (varies)."""
    system = SystemMessage(content=ROLE + load_playbook())
    return ChatPromptTemplate.from_messages([system, ("human", HUMAN)])


def format_context(files: list[Path]) -> str:
    """Already-converted companion files (e.g. the POM a test imports).

    Suite mode (Phase 9) converts page objects first, then tests — the test
    must call the *new* POM API, not guess it. This is that idea in miniature.
    Returns "" when there is nothing to add, so the human turn stays clean.
    """
    if not files:
        return ""
    blocks = [
        f'<converted_file path="{f}">\n{f.read_text(encoding="utf-8")}\n</converted_file>'
        for f in files
    ]
    return (
        "These companion files are ALREADY converted to Playwright. Import from "
        "them and use their exported API exactly as written:\n\n"
        + "\n\n".join(blocks)
        + "\n\n"
    )

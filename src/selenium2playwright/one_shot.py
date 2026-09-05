"""Step 2.2 — one-shot conversion with structured output, written to disk.

    uv run python -m selenium2playwright.one_shot samples/selenium-suite/pages/LoginPage.ts \
        --out out/2.2/pages/LoginPage.ts
    uv run python -m selenium2playwright.one_shot samples/selenium-suite/tests/login.spec.ts \
        --context out/2.2/pages/LoginPage.ts --out out/2.2/tests/login.spec.ts

The model fills a ConversionResult form instead of free text, so `code` is
always a string we can write straight to a .ts file. Code -> stdout / --out;
notes, TODO ledger and token stats -> stderr, so the code stream stays clean.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from selenium2playwright.llm import make_model, prepare_messages
from selenium2playwright.prompts import build_prompt, format_context
from selenium2playwright.schemas import ConversionResult


def convert(source: Path, context: list[Path], model_name: str | None = None) -> ConversionResult:
    """Playbook + source in, a validated ConversionResult out. One traced run."""
    # with_structured_output wraps the model in a new Runnable whose output is a
    # ConversionResult instead of an AIMessage. include_raw=True keeps the
    # original AIMessage alongside it (we still want its token counts).
    structured_model = make_model(model_name).with_structured_output(
        ConversionResult, include_raw=True
    )
    chain = build_prompt() | prepare_messages(model_name) | structured_model
    response = chain.invoke(
        {
            "file_path": str(source),
            "source": source.read_text(encoding="utf-8"),
            "context": format_context(context),
        },
        config={
            "run_name": "one-shot-convert",
            "tags": ["step:2.2", "prompt:v1", "output:structured"],
            "metadata": {"source": str(source), "context": [str(p) for p in context]},
        },
    )
    # include_raw=True returns a dict: {"raw": AIMessage, "parsed": ConversionResult|None,
    # "parsing_error": Exception|None}. A parse failure is a real failure — surface it.
    if response["parsing_error"] is not None:
        raise RuntimeError(f"model reply did not match ConversionResult: {response['parsing_error']}")
    report_usage(response["raw"].usage_metadata)
    return response["parsed"]


def report_usage(usage: dict | None) -> None:
    """Tokens in/out plus the cache split — the proof the playbook prefix is cached."""
    if not usage:
        return
    details = usage.get("input_token_details", {})
    # usage_metadata is LangChain's provider-neutral shape; "cache_read"/
    # "cache_creation" are its standard keys. langchain-anthropic 1.7 files a
    # cache *write* under TTL-specific extra keys instead (verified 2026-09-04
    # against the raw API usage), so sum all of them. Other providers: extras absent.
    written = sum(
        details.get(k, 0)
        for k in ("cache_creation", "ephemeral_5m_input_tokens", "ephemeral_1h_input_tokens")
    )
    print(
        f"[{usage['input_tokens']} in / {usage['output_tokens']} out"
        f" · cache write {written}"
        f" · cache read {details.get('cache_read', 0)}]",
        file=sys.stderr,
    )


def report_ledger(result: ConversionResult) -> None:
    """Notes and the consolidated TODO(review) ledger (playbook rule 25), on stderr."""
    for note in result.notes:
        print(f"  note: {note}", file=sys.stderr)
    if result.todos:
        print(f"⚠ {len(result.todos)} TODO(review) item(s) need a human:", file=sys.stderr)
        for todo in result.todos:
            print(f"  - {todo}", file=sys.stderr)
    else:
        print("✓ no TODO(review) items", file=sys.stderr)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="One-shot Selenium → Playwright conversion.")
    parser.add_argument("source", type=Path, help="Selenium .ts file to convert")
    parser.add_argument(
        "--context", type=Path, action="append", default=[],
        help="already-converted companion file (repeatable)",
    )
    parser.add_argument("--out", type=Path, help="write the converted .ts file here")
    parser.add_argument("--model", help="provider:model, e.g. openai:gpt-5 (default: S2P_MODEL or anthropic:claude-sonnet-5)")
    args = parser.parse_args(argv)

    result = convert(args.source, args.context, args.model)
    report_ledger(result)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(result.code, encoding="utf-8")
        print(f"[wrote {args.out}]", file=sys.stderr)
    else:
        print(result.code, end="")


if __name__ == "__main__":
    main()

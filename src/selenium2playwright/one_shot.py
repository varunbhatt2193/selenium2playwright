"""Step 2.1 — one-shot conversion, raw output, no parsing, no validation.

    uv run python -m selenium2playwright.one_shot samples/selenium-suite/pages/LoginPage.ts
    uv run python -m selenium2playwright.one_shot samples/selenium-suite/tests/login.spec.ts \
        --context out/LoginPage.ts --out out/login.spec.ts

Raw model text goes to stdout (so it can be redirected or --out'd for diffing
against the golden); run stats go to stderr so they never pollute the output.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from selenium2playwright.llm import make_model, prepare_messages
from selenium2playwright.prompts import build_prompt, format_context


def convert(source: Path, context: list[Path], model_name: str | None = None) -> str:
    """Playbook + source in, raw text out. One traced LangSmith run."""
    # LCEL pipeline: prompt -> provider-specific message prep -> model.
    chain = build_prompt() | prepare_messages(model_name) | make_model(model_name)
    response = chain.invoke(
        {
            "file_path": str(source),
            "source": source.read_text(encoding="utf-8"),
            "context": format_context(context),
        },
        # Trace hygiene: a findable run name + filterable tags/metadata in LangSmith.
        config={
            "run_name": "one-shot-convert",
            "tags": ["step:2.1", "prompt:v1"],
            "metadata": {"source": str(source), "context": [str(p) for p in context]},
        },
    )
    report_usage(response.usage_metadata)
    return response.text


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


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="One-shot Selenium → Playwright conversion.")
    parser.add_argument("source", type=Path, help="Selenium .ts file to convert")
    parser.add_argument(
        "--context", type=Path, action="append", default=[],
        help="already-converted companion file (repeatable)",
    )
    parser.add_argument("--out", type=Path, help="also write the raw output here")
    parser.add_argument("--model", help="provider:model, e.g. openai:gpt-5 (default: S2P_MODEL or anthropic:claude-sonnet-5)")
    args = parser.parse_args(argv)

    text = convert(args.source, args.context, args.model)
    print(text)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
        print(f"[wrote {args.out}]", file=sys.stderr)


if __name__ == "__main__":
    main()

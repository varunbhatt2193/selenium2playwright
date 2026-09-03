"""Environment loading and validation.

Secrets live in .env (gitignored). This module is the single place that
reads them; everything else in the project imports from here and can
assume the environment is already loaded.

Run directly to verify your setup:  uv run python -m selenium2playwright.env
"""

from __future__ import annotations

import os
import sys

from dotenv import load_dotenv

# Copies .env values into the process environment, once, at import time.
# override=False: a variable already exported in your shell wins over .env.
load_dotenv(override=False)

# var name -> expected prefix (catches "right var, wrong paste" mistakes)
REQUIRED = {
    "ANTHROPIC_API_KEY": "sk-ant-",  # console.anthropic.com -> API Keys
    "LANGSMITH_API_KEY": "lsv2_",  # smith.langchain.com -> Settings -> API Keys
}


def masked(value: str) -> str:
    """Show enough of a secret to identify it, never enough to use it."""
    return f"{value[:7]}…({len(value)} chars)"


def check() -> bool:
    """Print one line per required var; return True only if all are usable."""
    ok = True
    for name, prefix in REQUIRED.items():
        value = os.environ.get(name, "")
        if not value:
            print(f"✗ {name}  missing — add it to .env (see .env.example)")
            ok = False
        elif not value.startswith(prefix):
            print(f"✗ {name}  set, but doesn't look like a {prefix}… key: {masked(value)}")
            ok = False
        else:
            print(f"✓ {name}  {masked(value)}")
    return ok


if __name__ == "__main__":
    sys.exit(0 if check() else 1)

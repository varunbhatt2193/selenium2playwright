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

# "provider:model" in init_chat_model syntax. The provider half decides which
# API key must exist; the model half is passed through untouched.
DEFAULT_MODEL = "anthropic:claude-sonnet-5"

# provider -> (env var, expected prefix). Prefix catches "right var, wrong paste".
PROVIDER_KEYS = {
    "anthropic": ("ANTHROPIC_API_KEY", "sk-ant-"),  # console.anthropic.com -> API Keys
    "openai": ("OPENAI_API_KEY", "sk-"),  # platform.openai.com -> API keys
    "google_genai": ("GOOGLE_API_KEY", "AIza"),  # aistudio.google.com -> Get API key
}

ALWAYS_REQUIRED = {
    "LANGSMITH_API_KEY": "lsv2_",  # smith.langchain.com -> Settings -> API Keys
}


def model_name() -> str:
    """The actor's 'provider:model' string (S2P_MODEL env var or the default)."""
    return os.environ.get("S2P_MODEL") or DEFAULT_MODEL


def critic_model_name() -> str:
    """The critic's model: S2P_CRITIC_MODEL when set, otherwise the same as the actor.

    Two settings, one graph: the actor writes the code and the critic reviews
    it. Leaving S2P_CRITIC_MODEL empty keeps today's behaviour (one model for
    both). Setting it lets a cheap actor be reviewed by a strong critic.
    """
    return os.environ.get("S2P_CRITIC_MODEL") or model_name()


def judge_model_name() -> str:
    """The evaluation judge's model: S2P_JUDGE_MODEL, else the critic's, else the actor's.

    The judge scores finished conversions from outside the graph (Phase 6.4).
    It should usually be the strongest model you can afford, so its fallback
    is the critic, which is already the "strong reviewer" setting.
    """
    return os.environ.get("S2P_JUDGE_MODEL") or critic_model_name()


ROLE_VARIABLES = {"actor": "S2P_MODEL", "critic": "S2P_CRITIC_MODEL", "judge": "S2P_JUDGE_MODEL"}


def model_names() -> dict[str, str]:
    """Every configured model by role; the experiment plan hashes actor + critic."""
    return {"actor": model_name(), "critic": critic_model_name(), "judge": judge_model_name()}


def provider(name: str | None = None) -> str:
    return (name or model_name()).split(":", 1)[0]


def required() -> dict[str, str]:
    """Vars this configuration needs: LangSmith + a key for every provider in use."""
    needed = dict(ALWAYS_REQUIRED)
    for role, name in model_names().items():
        if provider(name) not in PROVIDER_KEYS:
            variable = ROLE_VARIABLES[role]
            raise ValueError(
                f"Unknown provider {provider(name)!r} in {variable}={name!r}; "
                f"known: {', '.join(PROVIDER_KEYS)}. Add it to PROVIDER_KEYS in env.py."
            )
        var, prefix = PROVIDER_KEYS[provider(name)]
        needed[var] = prefix
    return needed


def masked(value: str) -> str:
    """Show enough of a secret to identify it, never enough to use it."""
    return f"{value[:7]}…({len(value)} chars)"


def check() -> bool:
    """Print one line per required var; return True only if all are usable."""
    print(f"model: {model_name()}" + (f"  (critic: {critic_model_name()})" if critic_model_name() != model_name() else ""))
    ok = True
    for name, prefix in required().items():
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

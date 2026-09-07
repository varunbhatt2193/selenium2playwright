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

# Embeddings turn a memory into a list of numbers so the store can rank by
# meaning (step 7.3). Anthropic ships none, so the default is OpenAI's small
# model: 1536 dimensions, cheapest of the good ones, and a memory set is tiny
# (a few dozen sentences re-embedded once each). Swap providers with
# S2P_EMBEDDINGS="voyage:voyage-3.5-lite" and its package; S2P_EMBEDDINGS=off
# turns semantic recall off entirely and needs no key.
DEFAULT_EMBEDDINGS = "openai:text-embedding-3-small"
EMBEDDINGS_OFF = "off"

# provider -> (env var, expected prefix). Prefix catches "right var, wrong paste".
PROVIDER_KEYS = {
    "anthropic": ("ANTHROPIC_API_KEY", "sk-ant-"),  # console.anthropic.com -> API Keys
    "openai": ("OPENAI_API_KEY", "sk-"),  # platform.openai.com -> API keys
    "google_genai": ("GOOGLE_API_KEY", "AIza"),  # aistudio.google.com -> Get API key
    "voyage": ("VOYAGE_API_KEY", "pa-"),  # dash.voyageai.com -> API Keys (embeddings only)
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


def embeddings_name() -> str:
    """The embeddings 'provider:model' string, or "" when recall is switched off.

    Empty means "use the default", exactly like S2P_MODEL — a blank line in
    .env is not a decision. The literal value "off" is the decision: no
    embeddings model, no key required, and the store falls back to listing the
    most recent memories instead of ranking them by meaning.
    """
    name = os.environ.get("S2P_EMBEDDINGS") or DEFAULT_EMBEDDINGS
    return "" if name.strip().lower() == EMBEDDINGS_OFF else name


# Short names for the models this project is developed against, so a flag can
# read `--model opus` instead of the full string. This is a convenience table,
# not a lock-in: any "provider:model" string is accepted untouched, and adding
# another provider's model here is one line (model-agnostic rule).
MODEL_ALIASES = {
    "sonnet": "anthropic:claude-sonnet-5",
    "opus": "anthropic:claude-opus-5",
    "fable": "anthropic:claude-fable-5-1",
    "haiku": "anthropic:claude-haiku-4-5-20251001",
}


def resolve_model(name: str) -> str:
    """An alias or a full 'provider:model' string in; a full string out.

    A bare word that is not an alias is an error rather than a guess: without a
    provider half, init_chat_model would have to infer one, and inferring the
    wrong provider fails much later with an unrelated authentication message.
    """
    text = (name or "").strip()
    if ":" in text:
        return text
    if text in MODEL_ALIASES:
        return MODEL_ALIASES[text]
    raise ValueError(f"unknown model {name!r}; use provider:model, or one of: "
                     f"{', '.join(MODEL_ALIASES)}")


def resolve_roles(model: str = "", critic_model: str = "") -> dict[str, str]:
    """Which actor and critic a run will use: flags first, then .env, then the default.

    Step 8.2: a flag beats the environment, because the flag was typed just now.
    --model alone moves the critic too, since one model for both is the default
    arrangement (see critic_model_name) — unless the critic was chosen
    deliberately, by --critic-model or by S2P_CRITIC_MODEL in .env, in which
    case that split survives a change of actor.
    """
    actor = resolve_model(model) if model else model_name()
    if critic_model:
        critic = resolve_model(critic_model)
    else:
        critic = os.environ.get("S2P_CRITIC_MODEL") or actor
    return {"actor": actor, "critic": critic}


def key_missing(name: str) -> str:
    """"" when this model's provider key is present and plausible, else why not.

    Checked before a run that names a model on the command line, so a typo or an
    unconfigured provider costs nothing instead of surfacing as an
    authentication error a minute in. Never prints a key: the reason names the
    variable, and any value it shows is masked.
    """
    who = provider(name)
    if who not in PROVIDER_KEYS:
        return f"unknown provider {who!r} in {name!r}; known: {', '.join(PROVIDER_KEYS)}"
    var, prefix = PROVIDER_KEYS[who]
    value = os.environ.get(var, "")
    if not value:
        return f"{name} needs {var}, which is not set (see .env.example)"
    if not value.startswith(prefix):
        return f"{var} is set, but does not look like a {prefix}… key: {masked(value)}"
    return ""


ROLE_VARIABLES = {"actor": "S2P_MODEL", "critic": "S2P_CRITIC_MODEL", "judge": "S2P_JUDGE_MODEL",
                  "embeddings": "S2P_EMBEDDINGS"}


def model_names() -> dict[str, str]:
    """Every configured model by role; the experiment plan hashes actor + critic."""
    return {"actor": model_name(), "critic": critic_model_name(), "judge": judge_model_name()}


def provider(name: str | None = None) -> str:
    return (name or model_name()).split(":", 1)[0]


def required() -> dict[str, str]:
    """Vars this configuration needs: LangSmith + a key for every provider in use."""
    needed = dict(ALWAYS_REQUIRED)
    roles = model_names()
    if embeddings_name():
        roles["embeddings"] = embeddings_name()
    for role, name in roles.items():
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
    print(f"embeddings: {embeddings_name() or 'off (recall lists the most recent memories)'}")
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

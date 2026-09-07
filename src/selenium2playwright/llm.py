"""The one place a chat model gets built — and the only file that knows providers.

Everything else speaks LangChain: BaseChatModel, messages, Runnables. Swapping
Anthropic for OpenAI/Gemini/Bedrock is `uv add langchain-<provider>` plus
S2P_MODEL="provider:model" in .env; no other file changes. Roadmap rule 5:
develop on claude-sonnet-5 (cheap), opus for evals/demos. Step 8.2 added the
runtime path: `s2p convert --model opus` reaches make_model as an argument,
through the graph's context schema, and beats the environment for that run.
"""

from __future__ import annotations

import os
import re

from langchain.chat_models import init_chat_model
from langchain.embeddings import init_embeddings
from langchain_core.embeddings import Embeddings
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage, SystemMessage
from langchain_core.prompt_values import PromptValue
from langchain_core.runnables import Runnable, RunnableLambda, RunnablePassthrough

from selenium2playwright import env

# Providers whose native JSON-schema output the critic is known to work on.
# Anthropic needs it (forced tool choice fights adaptive thinking, see critic);
# OpenAI supports it and was verified live. Everywhere else the default path —
# tool/function calling — is the one every integration implements, so an
# unlisted provider gets that rather than an unsupported-method error.
JSON_SCHEMA_PROVIDERS = {"anthropic", "openai"}

# A converted test file is ~500-2,000 tokens; most providers default max_tokens
# to ~1k, which would silently truncate it mid-file. 8k is safe headroom.
MAX_OUTPUT_TOKENS = 8_192

# Vector width per embeddings model. The store bakes this into its SQLite index
# at creation, so it must be right before the first write, not discovered after.
# An unlisted model is probed once (one tiny embed call) rather than guessed.
EMBEDDING_DIMS = {
    "openai:text-embedding-3-small": 1536,
    "openai:text-embedding-3-large": 3072,
    "voyage:voyage-3.5-lite": 1024,
    "voyage:voyage-3.5": 1024,
    "google_genai:gemini-embedding-001": 3072,
}


def resolve_name(model_name: str | None = None, *, for_critic: bool = False) -> str:
    """The model string this call will actually use: argument > env > default.

    One place decides, so the name that reaches the provider, the name in the
    trace and the name printed on the scorecard can never drift apart.
    """
    return model_name or (env.critic_model_name() if for_critic else env.model_name())


def make_model(model_name: str | None = None, *, for_critic: bool = False) -> BaseChatModel:
    """Return a ready chat model.

    Precedence: argument > S2P_MODEL env > default. With for_critic=True the
    env fallback is S2P_CRITIC_MODEL, which itself falls back to S2P_MODEL, so
    the critic can be a different (stronger) model than the actor.
    """
    name = resolve_name(model_name, for_critic=for_critic)
    provider = name.split(":", 1)[0]
    kwargs = _client_kwargs(provider)
    if for_critic and provider == "anthropic":
        # Keep review effort explicit (plan-review §4.10). Native JSON output in
        # the critic avoids forced tool-choice conflicts with adaptive thinking.
        kwargs["effort"] = "medium"
    return init_chat_model(name, max_tokens=MAX_OUTPUT_TOKENS, **kwargs)


def _client_kwargs(provider: str) -> dict:
    """Provider-specific constructor extras. Empty for providers with none."""
    if provider == "anthropic" and os.environ.get("ANTHROPIC_WORKSPACE_ID"):
        # Identity-linked Anthropic keys must say which workspace a request acts in.
        return {"default_headers": {"anthropic-workspace-id": os.environ["ANTHROPIC_WORKSPACE_ID"]}}
    return {}


def structured_kwargs(model_name: str | None = None, *, for_critic: bool = False) -> dict:
    """with_structured_output options that work on the provider actually in use.

    include_raw everywhere: the parsed object and the AIMessage it came from are
    both needed (token counts, and an honest parse error instead of a None).
    The critic additionally asks for native JSON-schema output where that is
    known to work — a vendor detail, so it is decided here rather than in the
    node, which is what lets the same graph run on any provider.
    """
    kwargs: dict = {"include_raw": True}
    provider = resolve_name(model_name, for_critic=for_critic).split(":", 1)[0]
    if for_critic and provider in JSON_SCHEMA_PROVIDERS:
        kwargs["method"] = "json_schema"
    return kwargs


def check_model(name: str) -> str:
    """"" when this model can actually be built here, else one line saying why not.

    Two questions, cheapest first: does this project know a key variable for the
    provider and is it set (env.key_missing, no imports), and then — the honest
    one — can the client be constructed at all? Construction is local, costs
    nothing, and is the only check that knows about *this* machine: it catches
    an unsupported provider, an integration package that was never installed
    (LangChain's error names the package to add), and a provider whose key lives
    under a variable this project has never heard of.

    What it cannot catch is a model *name* the provider will reject — that needs
    a network call, so it stays where it always was: the first attempt.
    """
    problem = env.key_missing(name)
    if problem:
        return problem
    try:
        make_model(name)
    except Exception as exc:  # ImportError, unsupported provider, missing key…
        detail = " ".join(str(exc).split()) or type(exc).__name__
        package = re.search(r"langchain[-_][a-z0-9_-]+", detail)
        if isinstance(exc, ImportError) and package:
            # LangChain says "pip install X"; this project is managed by uv.
            detail += f" — here: uv add {package.group(0).replace('_', '-')}"
        elif "Supported providers" in detail:
            # Its list of ~28 providers is accurate and unreadable in a terminal
            # box; the actionable half is the shape of the string.
            detail = (detail.split("Supported providers")[0].strip()
                      + " Write it as provider:model, e.g. openai:gpt-5.4. Any LangChain "
                        "provider works once its package is installed (see .env.example).")
        return f"{name}: {detail[:400]}"
    return ""


def prepare_messages(model_name: str | None = None, *, for_critic: bool = False) -> Runnable:
    """LCEL stage between prompt and model for provider-only message tweaks.

    Prompts stay pure LangChain; anything one vendor needs in the message
    payload is applied here, right before the model, and only for that vendor.
    Today: Anthropic's prompt-cache marker on the system message. Other
    providers get a passthrough (OpenAI/Gemini cache long prefixes automatically).
    for_critic resolves the same env fallback as make_model, so the marker
    always matches the model that will actually receive the messages.
    """
    name = resolve_name(model_name, for_critic=for_critic)
    provider = name.split(":", 1)[0]
    if provider == "anthropic":
        return RunnableLambda(_mark_system_cacheable)
    return RunnablePassthrough()


def _mark_system_cacheable(prompt: PromptValue) -> list[BaseMessage]:
    """Anthropic caches everything up to the block carrying cache_control.

    The system message is our byte-identical static prefix (role + playbook):
    paid in full once, then ~10% for the cache window. Anything that varies per
    request (the file to convert) must come AFTER this block or nothing caches.
    """
    out: list[BaseMessage] = []
    for m in prompt.to_messages():
        if isinstance(m, SystemMessage) and isinstance(m.content, str):
            block = {"type": "text", "text": m.content, "cache_control": {"type": "ephemeral"}}
            m = SystemMessage(content=[block])
        out.append(m)
    return out


def make_embeddings(name: str | None = None) -> Embeddings | None:
    """Return a ready embeddings model, or None when recall is switched off.

    Same shape as make_model: LangChain's init_embeddings takes the identical
    "provider:model" string, so choosing Voyage over OpenAI is an .env edit plus
    `uv add langchain-voyageai`. None is a real answer, not a failure — the
    store then lists recent memories instead of ranking them (see store.py).
    """
    resolved = name if name is not None else env.embeddings_name()
    if not resolved:
        return None
    return init_embeddings(resolved)


def embedding_dims(embeddings: Embeddings, name: str | None = None) -> int:
    """How wide this model's vectors are: the table, else ask the model once."""
    resolved = name if name is not None else env.embeddings_name()
    known = EMBEDDING_DIMS.get(resolved)
    return known if known else len(embeddings.embed_query("dimension probe"))

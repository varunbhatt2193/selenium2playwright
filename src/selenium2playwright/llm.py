"""The one place a chat model gets built — and the only file that knows providers.

Everything else speaks LangChain: BaseChatModel, messages, Runnables. Swapping
Anthropic for OpenAI/Gemini/Bedrock is `uv add langchain-<provider>` plus
S2P_MODEL="provider:model" in .env; no other file changes. Roadmap rule 5:
develop on claude-sonnet-5 (cheap), opus for evals/demos. Phase 8 turns this
into a CLI flag via a configurable field on the same init_chat_model call.
"""

from __future__ import annotations

import os

from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage, SystemMessage
from langchain_core.prompt_values import PromptValue
from langchain_core.runnables import Runnable, RunnableLambda, RunnablePassthrough

from selenium2playwright import env

# A converted test file is ~500-2,000 tokens; most providers default max_tokens
# to ~1k, which would silently truncate it mid-file. 8k is safe headroom.
MAX_OUTPUT_TOKENS = 8_192


def make_model(model_name: str | None = None, *, for_critic: bool = False) -> BaseChatModel:
    """Return a ready chat model. Precedence: argument > S2P_MODEL env > default."""
    name = model_name or env.model_name()
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


def prepare_messages(model_name: str | None = None) -> Runnable:
    """LCEL stage between prompt and model for provider-only message tweaks.

    Prompts stay pure LangChain; anything one vendor needs in the message
    payload is applied here, right before the model, and only for that vendor.
    Today: Anthropic's prompt-cache marker on the system message. Other
    providers get a passthrough (OpenAI/Gemini cache long prefixes automatically).
    """
    provider = (model_name or env.model_name()).split(":", 1)[0]
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

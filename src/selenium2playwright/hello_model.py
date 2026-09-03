"""First model call: convert one Selenium line to Playwright.

Proves the whole toolchain end to end: .env -> chat model -> Claude -> response.
Run:  uv run python -m selenium2playwright.hello_model
"""

import os

from langchain.chat_models import init_chat_model
from langchain_core.messages import HumanMessage, SystemMessage

from selenium2playwright import env as _env  # noqa: F401 — loads .env on import

# "provider:model" string -> a ready-to-use chat model.
# (No temperature knob: Claude 5 models reject the parameter as deprecated.)
# Identity-linked API keys must say which workspace each request acts in.
model = init_chat_model(
    "anthropic:claude-sonnet-5",
    default_headers={"anthropic-workspace-id": os.environ.get("ANTHROPIC_WORKSPACE_ID", "")},
)

messages = [
    SystemMessage(
        "You convert Selenium WebDriver (TypeScript) code to Playwright "
        "(TypeScript). Reply with only the converted code, no explanation."
    ),
    HumanMessage('await driver.findElement(By.id("username")).sendKeys("varun");'),
]

if __name__ == "__main__":
    response = model.invoke(messages)  # one API round trip -> an AIMessage
    print(response.content)
    usage = response.usage_metadata
    print(f"\n[{usage['input_tokens']} tokens in, {usage['output_tokens']} out]")

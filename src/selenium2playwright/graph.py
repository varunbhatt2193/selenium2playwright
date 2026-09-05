"""Step 3.1 — the first LangGraph: intake -> convert.

    uv run python -m selenium2playwright.graph samples/selenium-suite/pages/LoginPage.ts

Same work as one_shot.py, restructured as a graph so that (a) every step is a
named node in the LangSmith trace and (b) the next steps — classify/refuse,
validate, critic loop — are new nodes and edges, not a rewrite.

Vocabulary used here, from the LangGraph docs:
  state  — one dict that flows through the graph; every node reads it and
           returns the *part* it changed (a partial dict). LangGraph merges.
  node   — a plain Python function: state in, partial state out.
  edge   — "after node A, run node B". START and END are the built-in ends.
  compile() — turns the wiring into a Runnable (invoke/stream/batch like a chain).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from selenium2playwright.llm import make_model, prepare_messages
from selenium2playwright.one_shot import report_ledger, report_usage
from selenium2playwright.prompts import build_prompt, format_context
from selenium2playwright.schemas import ConversionResult


class ConversionState(TypedDict, total=False):
    """Everything the graph knows about one conversion. Nodes fill it in.

    total=False: keys are optional, because the caller supplies only the
    inputs (source_path, context_paths) and later nodes add the rest.
    """

    # inputs — set by the caller
    source_path: str
    context_paths: list[str]
    # filled by intake
    source: str  # the Selenium file contents
    context: str  # already-converted companions, formatted for the prompt ("" if none)
    # filled by convert
    result: ConversionResult
    usage: dict  # token/cache counts from the model call


def intake(state: ConversionState) -> ConversionState:
    """Read the files off disk. No LLM. Returns only the keys it produced."""
    source = Path(state["source_path"]).read_text(encoding="utf-8")
    context = format_context([Path(p) for p in state.get("context_paths", [])])
    return {"source": source, "context": context}


def convert(state: ConversionState) -> ConversionState:
    """The 2.2 chain as a node: prompt | prepare | structured model."""
    structured_model = make_model().with_structured_output(ConversionResult, include_raw=True)
    chain = build_prompt() | prepare_messages() | structured_model
    response = chain.invoke(
        {"file_path": state["source_path"], "source": state["source"], "context": state["context"]}
    )
    if response["parsing_error"] is not None:
        raise RuntimeError(f"model reply did not match ConversionResult: {response['parsing_error']}")
    return {"result": response["parsed"], "usage": response["raw"].usage_metadata}


def build_graph():
    """Wire the nodes: START -> intake -> convert -> END, then compile."""
    builder = StateGraph(ConversionState)
    builder.add_node("intake", intake)
    builder.add_node("convert", convert)
    builder.add_edge(START, "intake")
    builder.add_edge("intake", "convert")
    builder.add_edge("convert", END)
    return builder.compile()


if __name__ == "__main__":
    source_path, *context_paths = sys.argv[1:]
    graph = build_graph()
    final = graph.invoke(
        {"source_path": source_path, "context_paths": context_paths},
        config={"run_name": "conversion-graph", "tags": ["step:3.1", "prompt:v1"]},
    )
    report_usage(final["usage"])
    report_ledger(final["result"])
    print(final["result"].code, end="")

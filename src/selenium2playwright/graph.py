"""Steps 3.1–3.2 — the first LangGraph: intake -> (convert | refuse).

    uv run python -m selenium2playwright.graph samples/selenium-suite/pages/LoginPage.ts
    uv run python -m selenium2playwright.graph some/webdriverio.e2e.ts   # -> clean refusal

Same work as one_shot.py, restructured as a graph so that (a) every step is a
named node in the LangSmith trace and (b) the next steps — classify/refuse,
validate, critic loop — are new nodes and edges, not a rewrite.

Vocabulary used here, from the LangGraph docs:
  state  — one dict that flows through the graph; every node reads it and
           returns the *part* it changed (a partial dict). LangGraph merges.
  node   — a plain Python function: state in, partial state out.
  edge   — "after node A, run node B". START and END are the built-in ends.
  conditional edge — "after node A, call this function; it returns the NAME
           of the next node". The graph branches on data, not the model.
  compile() — turns the wiring into a Runnable (invoke/stream/batch like a chain).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Literal, TypedDict

from langgraph.graph import END, START, StateGraph

from selenium2playwright.classify import Classification, classify
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
    classification: Classification  # what the file is, and whether we can convert it
    # filled by convert OR refuse — exactly one of them runs
    status: Literal["converted", "refused"]
    result: ConversionResult
    usage: dict  # token/cache counts from the model call
    refusal: str  # the honest reason, when status == "refused"


def intake(state: ConversionState) -> ConversionState:
    """Read the files off disk. No LLM. Returns only the keys it produced."""
    source = Path(state["source_path"]).read_text(encoding="utf-8")
    context = format_context([Path(p) for p in state.get("context_paths", [])])
    return {"source": source, "context": context,
            "classification": classify(source, state["source_path"])}


def route_after_intake(state: ConversionState) -> Literal["convert", "refuse"]:
    """The branching decision. Reads state, returns the next node's name."""
    return "convert" if state["classification"].supported else "refuse"


def refuse(state: ConversionState) -> ConversionState:
    """Honest 'not supported': no model call, no half-converted file."""
    return {"status": "refused", "refusal": state["classification"].reason}


def convert(state: ConversionState) -> ConversionState:
    """The 2.2 chain as a node: prompt | prepare | structured model."""
    structured_model = make_model().with_structured_output(ConversionResult, include_raw=True)
    chain = build_prompt() | prepare_messages() | structured_model
    response = chain.invoke(
        {"file_path": state["source_path"], "source": state["source"], "context": state["context"]}
    )
    if response["parsing_error"] is not None:
        raise RuntimeError(f"model reply did not match ConversionResult: {response['parsing_error']}")
    return {"status": "converted", "result": response["parsed"],
            "usage": response["raw"].usage_metadata}


def build_graph():
    """START -> intake -> (convert | refuse) -> END, then compile."""
    builder = StateGraph(ConversionState)
    builder.add_node("intake", intake)
    builder.add_node("convert", convert)
    builder.add_node("refuse", refuse)
    builder.add_edge(START, "intake")
    # After intake, ask route_after_intake which node comes next. The mapping
    # {returned name: node name} is what lets LangGraph draw the branch.
    builder.add_conditional_edges("intake", route_after_intake,
                                  {"convert": "convert", "refuse": "refuse"})
    builder.add_edge("convert", END)
    builder.add_edge("refuse", END)
    return builder.compile()


if __name__ == "__main__":
    source_path, *context_paths = sys.argv[1:]
    graph = build_graph()
    final = graph.invoke(
        {"source_path": source_path, "context_paths": context_paths},
        config={"run_name": "conversion-graph", "tags": ["step:3.2", "prompt:v1"]},
    )
    c = final["classification"]
    print(f"[{c.automation} · {c.runner} · {c.language}] {c.reason}", file=sys.stderr)
    if final["status"] == "refused":
        print(f"✗ not converted: {final['refusal']}", file=sys.stderr)
        sys.exit(2)
    report_usage(final["usage"])
    report_ledger(final["result"])
    print(final["result"].code, end="")

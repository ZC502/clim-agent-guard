#!/usr/bin/env python3
"""Minimal insertion pattern for LangGraph.

Requires: pip install langgraph
This example intentionally uses a deterministic proposal rather than an LLM so
that the integrity behavior is reproducible on any laptop.
"""
from __future__ import annotations

from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from clim_agent_guard import ActionProposal, IntegrityGuard, ToolContract
from clim_agent_guard.langgraph_adapter import make_precheck_node, route_after_precheck


class GraphState(TypedDict, total=False):
    proposal: dict
    authoritative_state: dict
    guard_decision: str
    guard_code: str
    guard_message: str
    proposal_obj: object
    result: str


def main() -> None:
    contract = ToolContract.from_json("contracts/file_delete.json")
    guard = IntegrityGuard([contract])

    graph = StateGraph(GraphState)
    graph.add_node("propose", lambda s: {
        "proposal": ActionProposal(
            tool="delete_file",
            args={"path": "important.txt"},
            observed_state_version=1,
            idempotency_key="delete:important.txt:v1",
        ).__dict__,
        "authoritative_state": {"_version": 1, "user_confirmed": False, "target": {"exists": True}},
    })
    graph.add_node("guard", make_precheck_node(guard))
    graph.add_node("execute_tool", lambda s: {"result": "TOOL WOULD EXECUTE"})
    graph.add_node("blocked", lambda s: {"result": f"BLOCKED: {s['guard_code']}"})
    graph.add_node("reconcile", lambda s: {"result": "RECONCILE"})
    graph.add_node("human_review", lambda s: {"result": "HUMAN_REVIEW"})

    graph.add_edge(START, "propose")
    graph.add_edge("propose", "guard")
    graph.add_conditional_edges("guard", route_after_precheck)
    for node in ("execute_tool", "blocked", "reconcile", "human_review"):
        graph.add_edge(node, END)

    app = graph.compile()
    print(app.invoke({})["result"])


if __name__ == "__main__":
    main()

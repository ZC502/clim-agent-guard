"""Pinned-path framework adapters for fixed-proposal boundary experiments.

LangGraph uses its actual checkpointed interrupt/resume graph primitive.
CrewAI uses its actual before-tool hook registry/dispatcher, but deliberately
injects a fixed proposal instead of booting an LLM Agent. This is a native
hook-level test, NOT a full CrewAI Crew.kickoff() E2E claim.
"""
from __future__ import annotations
import uuid
from typing import Any, TypedDict
from .shared import Trial


class GraphData(TypedDict, total=False):
    approved: bool
    ran: bool


def run_langgraph(trial: Trial) -> None:
    try:
        from langgraph.graph import StateGraph, START, END
        from langgraph.checkpoint.memory import InMemorySaver
        from langgraph.types import Command, interrupt
    except ImportError as exc:
        raise RuntimeError("Install requirements-langgraph.txt; no silent framework fallback") from exc
    trial.adapter_detail = "langgraph.stategraph.native_interrupt" if trial.arm == "B" else "langgraph.stategraph.tool_node"
    def node_execute(state: GraphData) -> GraphData:
        if trial.arm == "B" and not state.get("approved", False):
            trial.decision, trial.code, trial.layer = "BLOCK", trial.code, "native_app_policy"
            return {"ran": False}
        if trial.arm == "C" and not trial.clim_decide():
            return {"ran": False}
        if trial.arm == "A":
            trial.decision, trial.code = "NOT_APPLIED", "BASELINE"
        trial.execute()
        return {"ran": True}
    graph_builder = StateGraph(GraphData)
    if trial.arm == "B":
        def node_review(state: GraphData) -> GraphData:
            reviewer_approval = interrupt({"tool": "delete_file", "args": trial.args,
                                            "review_required": True})
            return {"approved": bool(reviewer_approval)}
        graph_builder.add_node("review", node_review)
        graph_builder.add_edge(START, "review")
        graph_builder.add_edge("review", "execute")
    else:
        graph_builder.add_edge(START, "execute")
    graph_builder.add_node("execute", node_execute)
    graph_builder.add_edge("execute", END)
    graph = graph_builder.compile(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": uuid.uuid4().hex}}
    initial = graph.invoke({"approved": False, "ran": False}, config=config)
    if trial.arm == "B":
        if not initial.get("__interrupt__"):
            raise RuntimeError("LangGraph native interrupt was not observed; abort trial")
        approved = trial.native_decide()
        graph.invoke(Command(resume=approved), config=config)


def run_crewai(trial: Trial) -> None:
    try:
        from crewai.hooks import (
            ToolCallHookContext, register_before_tool_call_hook,
            unregister_before_tool_call_hook,
        )
        from crewai.hooks.tool_hooks import run_before_tool_call_hooks
    except ImportError as exc:
        raise RuntimeError("Install requirements-crewai.txt; no silent framework fallback") from exc
    # Native hook runner is called with a real CrewAI context but without an
    # Agent/Task. This tests hook semantics in isolation, not entire Crew kickoff.
    trial.adapter_detail = "crewai.hooks.before_tool_call.unit_dispatch"
    hook = None
    try:
        if trial.arm in ("B", "C"):
            def hook(context: Any) -> bool:
                if context.tool_name != "delete_file":
                    trial.decision, trial.code, trial.layer = "BLOCK", "UNKNOWN_TOOL", "native_app_policy"
                    return False
                if trial.arm == "B":
                    return trial.native_decide()
                return trial.clim_decide()
            register_before_tool_call_hook(hook)
        # A uses no added policy hooks, but still dispatches via CrewAI's hook runner.
        context = ToolCallHookContext(tool_name="delete_file", tool_input=dict(trial.args),
                                      tool=None, agent=None, task=None, crew=None)
        blocked = run_before_tool_call_hooks(context)
        if blocked:
            if trial.decision == "NOT_EVALUATED":
                trial.decision, trial.code, trial.layer = "BLOCK", "OTHER_HOOK_BLOCK", "external_hook"
            return
        if trial.arm == "A":
            trial.decision, trial.code = "NOT_APPLIED", "BASELINE"
        trial.execute()
    finally:
        if hook is not None:
            unregister_before_tool_call_hook(hook)


def run_reference(trial: Trial) -> None:
    """Dependency-free scorer/executor smoke test; NEVER present as framework run."""
    trial.adapter_detail = "reference_only.not_a_framework"
    if trial.arm == "B" and not trial.native_decide():
        return
    if trial.arm == "C" and not trial.clim_decide():
        return
    if trial.arm == "A":
        trial.decision, trial.code = "NOT_APPLIED", "BASELINE"
    trial.execute()

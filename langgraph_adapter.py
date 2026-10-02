"""Thin LangGraph adapter.

The core guard has no LangGraph dependency. Node names are not hard-coded: the
router can either return Decision values directly or map them to arbitrary graph
node names supplied by the host application.
"""
from __future__ import annotations

from dataclasses import asdict
from typing import Any, Callable, Mapping

from .core import ActionProposal, Decision, EffectStatus, IntegrityGuard


DEFAULT_ROUTES: dict[Decision, str] = {
    Decision.ALLOW: "execute_tool",
    Decision.BLOCK: "blocked",
    Decision.RECONCILE: "reconcile",
    Decision.ESCALATE: "human_review",
}


def make_precheck_node(
    guard: IntegrityGuard,
    *,
    proposal_key: str = "proposal",
    state_key: str = "authoritative_state",
    guard_state_key: str = "clim_guard_state",
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """Create a precheck node that participates in LangGraph checkpointing.

    If ``guard_state_key`` is present in graph state, the guard restores it
    before evaluation. The returned state always includes a fresh guard snapshot
    so the host checkpointer can persist retry/idempotency protection.
    """

    def node(graph_state: dict[str, Any]) -> dict[str, Any]:
        snapshot = graph_state.get(guard_state_key)
        if snapshot:
            guard.load_state(snapshot)

        raw = graph_state[proposal_key]
        proposal = raw if isinstance(raw, ActionProposal) else ActionProposal(**raw)
        decision = guard.precheck(proposal, graph_state[state_key])
        return {
            "guard_decision": decision.decision.value,
            "guard_code": decision.code,
            "guard_message": decision.message,
            "guard_evidence": decision.evidence,
            # Plain dict is friendlier to JSON/checkpoint serializers while
            # preserving action_id/idempotency_key across the tool boundary.
            "proposal_obj": asdict(proposal),
            guard_state_key: guard.dump_state(),
        }

    return node


def route_after_precheck(
    graph_state: dict[str, Any],
    routes: Mapping[Decision | str, str] | None = None,
    *,
    decision_key: str = "guard_decision",
) -> str:
    decision = Decision(graph_state[decision_key])
    if routes is None:
        routes = DEFAULT_ROUTES
    normalized = {Decision(k): v for k, v in routes.items()}
    return normalized[decision]


def make_router(
    routes: Mapping[Decision | str, str] | None = None,
    *,
    decision_key: str = "guard_decision",
) -> Callable[[dict[str, Any]], str]:
    return lambda graph_state: route_after_precheck(graph_state, routes, decision_key=decision_key)


def make_verify_node(
    guard: IntegrityGuard,
    *,
    proposal_key: str = "proposal_obj",
    before_state_key: str = "authoritative_state_before",
    after_state_key: str = "authoritative_state_after",
    effect_status_key: str = "tool_effect_status",
    guard_state_key: str = "clim_guard_state",
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """Create a post-tool effect verification node.

    The host tool node is responsible for producing authoritative before/after
    state and one of SUCCESS / FAILED / UNKNOWN. Timeouts after dispatch should
    normally be mapped to UNKNOWN, never assumed to be FAILED.
    """

    def node(graph_state: dict[str, Any]) -> dict[str, Any]:
        snapshot = graph_state.get(guard_state_key)
        if snapshot:
            guard.load_state(snapshot)

        raw = graph_state[proposal_key]
        proposal = raw if isinstance(raw, ActionProposal) else ActionProposal(**raw)
        raw_status = graph_state[effect_status_key]
        effect_status = (
            raw_status if isinstance(raw_status, EffectStatus) else EffectStatus(str(raw_status))
        )
        decision = guard.verify_effect(
            proposal,
            graph_state[before_state_key],
            graph_state[after_state_key],
            effect_status,
        )
        return {
            "guard_decision": decision.decision.value,
            "guard_code": decision.code,
            "guard_message": decision.message,
            "guard_evidence": decision.evidence,
            guard_state_key: guard.dump_state(),
        }

    return node

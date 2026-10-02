from pathlib import Path

from clim_agent_guard import ActionProposal, Decision, EffectStatus, IntegrityGuard, StateCondition, ToolContract
from clim_agent_guard.langgraph_adapter import make_router, route_after_precheck
from clim_agent_guard.core import _evaluate_condition

ROOT = Path(__file__).parents[1]


def refund_state(version: int, *, refund_status: str = "none") -> dict:
    return {
        "_version": version,
        "order": {
            "exists": True,
            "user_confirmed": True,
            "payment_settled": True,
            "refund_eligible": True,
            "refund_status": refund_status,
            "items": [{"status": "ready"}],
        },
    }


def test_blocks_missing_confirmation():
    guard = IntegrityGuard([ToolContract.from_json(ROOT / "contracts/file_delete.json")])
    p = ActionProposal(
        "delete_file",
        {"path": "x"},
        observed_state_version=1,
        idempotency_key="delete:x:v1",
    )
    d = guard.precheck(p, {"_version": 1, "user_confirmed": False, "target": {"exists": True}})
    assert d.decision == Decision.BLOCK
    assert d.code == "USER_CONFIRMATION_REQUIRED"
    assert d.evidence["phase"] == "PRECHECK"
    assert d.evidence["precondition_checks"][-1]["passed"] is False


def test_requires_idempotency_key_when_contract_says_so():
    guard = IntegrityGuard([ToolContract.from_json(ROOT / "contracts/refund_order.json")])
    p = ActionProposal("refund_order", {}, 1)
    d = guard.precheck(p, refund_state(1))
    assert d.decision == Decision.BLOCK
    assert d.code == "IDEMPOTENCY_KEY_REQUIRED"


def test_stale_state_requires_reconcile():
    guard = IntegrityGuard([ToolContract.from_json(ROOT / "contracts/file_delete.json")])
    p = ActionProposal(
        "delete_file",
        {"path": "x"},
        observed_state_version=1,
        idempotency_key="delete:x:v1",
    )
    d = guard.precheck(p, {"_version": 2, "user_confirmed": True, "target": {"exists": True}})
    assert d.decision == Decision.RECONCILE
    assert d.code == "STALE_STATE_VERSION"


def test_unknown_effect_blocks_blind_retry():
    guard = IntegrityGuard([ToolContract.from_json(ROOT / "contracts/refund_order.json")])
    state = refund_state(1)
    p = ActionProposal("refund_order", {}, 1, idempotency_key="refund:1")
    assert guard.precheck(p, state).decision == Decision.ALLOW
    d = guard.verify_effect(p, state, refund_state(2), EffectStatus.UNKNOWN)
    assert d.decision == Decision.RECONCILE
    retry = ActionProposal("refund_order", {}, 2, idempotency_key="refund:1")
    d2 = guard.precheck(retry, refund_state(2))
    assert d2.decision == Decision.RECONCILE
    assert d2.code == "UNKNOWN_PRIOR_EFFECT"


def test_reserved_attempt_survives_new_action_id_and_forces_reconcile():
    guard = IntegrityGuard([ToolContract.from_json(ROOT / "contracts/refund_order.json")])
    p1 = ActionProposal("refund_order", {}, 1, idempotency_key="refund:reserved")
    assert guard.precheck(p1, refund_state(1)).decision == Decision.ALLOW

    # Simulate crash after ALLOW but before verify_effect(). A new action_id must
    # not create a fresh retry budget or cross the side-effect boundary.
    p2 = ActionProposal("refund_order", {}, 1, idempotency_key="refund:reserved")
    d = guard.precheck(p2, refund_state(1))
    assert d.decision == Decision.RECONCILE
    assert d.code == "UNKNOWN_PRIOR_EFFECT"


def test_retry_budget_is_anchored_to_idempotency_key_across_action_ids():
    guard = IntegrityGuard([ToolContract.from_json(ROOT / "contracts/refund_order.json")])
    key = "refund:budget"

    p1 = ActionProposal("refund_order", {}, 1, idempotency_key=key)
    assert guard.precheck(p1, refund_state(1)).decision == Decision.ALLOW
    assert guard.verify_effect(p1, refund_state(1), refund_state(2), EffectStatus.FAILED).code == "TOOL_FAILED"

    p2 = ActionProposal("refund_order", {}, 2, idempotency_key=key)
    assert guard.precheck(p2, refund_state(2)).decision == Decision.ALLOW
    assert guard.verify_effect(p2, refund_state(2), refund_state(3), EffectStatus.FAILED).code == "TOOL_FAILED"

    # max_retries=1 means 2 total attempts. A third proposal with a fresh
    # action_id must still be rejected.
    p3 = ActionProposal("refund_order", {}, 3, idempotency_key=key)
    d3 = guard.precheck(p3, refund_state(3))
    assert d3.decision == Decision.ESCALATE
    assert d3.code == "RETRY_BUDGET_EXHAUSTED"
    assert d3.evidence["attempts"] == 2


def test_reconcile_suppresses_duplicate_after_commit():
    guard = IntegrityGuard([ToolContract.from_json(ROOT / "contracts/refund_order.json")])
    state = refund_state(1)
    p = ActionProposal("refund_order", {}, 1, idempotency_key="refund:1")
    guard.precheck(p, state)
    guard.verify_effect(p, state, refund_state(2), EffectStatus.UNKNOWN)
    committed_state = refund_state(3, refund_status="completed")
    guard.mark_reconciled(p, committed_state, effect_happened=True)
    retry = ActionProposal("refund_order", {}, 3, idempotency_key="refund:1")
    d = guard.precheck(retry, committed_state)
    assert d.decision == Decision.BLOCK
    assert d.code == "DUPLICATE_SIDE_EFFECT"


def test_mark_reconciled_synchronizes_index_even_without_precheck():
    guard = IntegrityGuard([ToolContract.from_json(ROOT / "contracts/refund_order.json")])
    p = ActionProposal("refund_order", {}, 7, idempotency_key="refund:external")
    guard.mark_reconciled(p, refund_state(7, refund_status="completed"), effect_happened=True)
    assert "refund:external" in guard.idempotency_index

    retry = ActionProposal("refund_order", {}, 7, idempotency_key="refund:external")
    d = guard.precheck(retry, refund_state(7, refund_status="completed"))
    assert d.decision == Decision.BLOCK
    assert d.code == "DUPLICATE_SIDE_EFFECT"


def test_dump_and_load_state_preserves_duplicate_protection():
    contract = ToolContract.from_json(ROOT / "contracts/refund_order.json")
    g1 = IntegrityGuard([contract])
    p = ActionProposal("refund_order", {}, 1, idempotency_key="refund:persist")
    g1.precheck(p, refund_state(1))
    g1.mark_reconciled(p, refund_state(2, refund_status="completed"), effect_happened=True)
    snapshot = g1.dump_state()

    g2 = IntegrityGuard([contract])
    g2.load_state(snapshot)
    retry = ActionProposal("refund_order", {}, 2, idempotency_key="refund:persist")
    d = g2.precheck(retry, refund_state(2, refund_status="completed"))
    assert d.decision == Decision.BLOCK
    assert d.code == "DUPLICATE_SIDE_EFFECT"


def test_nested_list_path_resolution():
    c = StateCondition(path="order.items.0.status", op="eq", value="ready")
    ok, observed = _evaluate_condition(refund_state(1), c)
    assert ok is True
    assert observed == "ready"


def test_ne_missing_path_fails_closed_and_exists_false_is_explicit():
    state = {"_version": 1, "order": {}}
    ne = StateCondition(path="order.status", op="ne", value="completed")
    ok, _ = _evaluate_condition(state, ne)
    assert ok is False

    absent = StateCondition(path="order.status", op="exists", value=False)
    ok2, _ = _evaluate_condition(state, absent)
    assert ok2 is True


def test_router_mapping_is_configurable():
    graph_state = {"guard_decision": "BLOCK"}
    assert route_after_precheck(graph_state) == "blocked"
    custom = {
        Decision.ALLOW: "go",
        Decision.BLOCK: "deny",
        Decision.RECONCILE: "repair",
        Decision.ESCALATE: "human",
    }
    assert route_after_precheck(graph_state, custom) == "deny"
    assert make_router(custom)(graph_state) == "deny"


def test_verify_effect_rejects_unreserved_action_id_even_with_same_idempotency_key():
    guard = IntegrityGuard([ToolContract.from_json(ROOT / "contracts/refund_order.json")])
    key = "refund:verify-reservation"
    reserved = ActionProposal("refund_order", {}, 1, idempotency_key=key)
    assert guard.precheck(reserved, refund_state(1)).decision == Decision.ALLOW

    impostor = ActionProposal("refund_order", {}, 1, idempotency_key=key)
    d = guard.verify_effect(impostor, refund_state(1), refund_state(2), EffectStatus.SUCCESS)
    assert d.decision == Decision.ESCALATE
    assert d.code == "UNRESERVED_ACTION_ID"


def test_type_mismatch_in_gte_fails_closed_without_crash():
    c = StateCondition(path="order.total", op="gte", value=100)
    ok, observed = _evaluate_condition({"order": {"total": "100"}}, c)
    assert ok is False
    assert observed == "100"


def test_invalid_in_operand_fails_closed_without_crash():
    c = StateCondition(path="order.code", op="in", value=7)
    ok, observed = _evaluate_condition({"order": {"code": 3}}, c)
    assert ok is False
    assert observed == 3


def test_retry_contract_requires_stable_idempotency_key():
    try:
        ToolContract(tool="retryable", max_retries=2, require_idempotency_key=False)
    except ValueError as exc:
        assert "require_idempotency_key" in str(exc)
    else:
        raise AssertionError("retryable contract without idempotency key must be rejected")


def test_evidence_window_is_bounded_in_checkpoint():
    contract = ToolContract(
        tool="noop",
        preconditions=(StateCondition(path="ok", op="eq", value=True),),
    )
    guard = IntegrityGuard([contract], max_events=3)
    for i in range(8):
        p = ActionProposal("noop", {}, i, action_id=f"a-{i}")
        d = guard.precheck(p, {"_version": i, "ok": False})
        assert d.decision == Decision.BLOCK
    assert len(guard.evidence_window()) == 3
    assert len(guard.dump_state()["events"]) == 3


def test_default_postcondition_code_is_phase_specific():
    contract = ToolContract(
        tool="write",
        postconditions=(StateCondition(path="done", op="eq", value=True),),
        irreversible=False,
    )
    guard = IntegrityGuard([contract])
    p = ActionProposal("write", {}, 1, action_id="write-1")
    assert guard.precheck(p, {"_version": 1}).decision == Decision.ALLOW
    d = guard.verify_effect(
        p,
        {"_version": 1},
        {"_version": 2, "done": False},
        EffectStatus.SUCCESS,
    )
    assert d.decision == Decision.RECONCILE
    assert d.code == "POSTCONDITION_FAILED"


def test_default_precondition_code_is_phase_specific():
    contract = ToolContract(
        tool="read",
        preconditions=(StateCondition(path="allowed", op="eq", value=True),),
    )
    guard = IntegrityGuard([contract])
    p = ActionProposal("read", {}, 1, action_id="read-1")
    d = guard.precheck(p, {"_version": 1, "allowed": False})
    assert d.decision == Decision.BLOCK
    assert d.code == "PRECONDITION_FAILED"


def test_make_verify_node_preserves_action_id_and_checkpoint():
    from clim_agent_guard.langgraph_adapter import make_precheck_node, make_verify_node

    contract = ToolContract(
        tool="write",
        postconditions=(StateCondition(path="done", op="eq", value=True),),
        require_idempotency_key=True,
    )
    guard = IntegrityGuard([contract])
    proposal = ActionProposal("write", {}, 1, action_id="stable-action", idempotency_key="op:1")
    pre = make_precheck_node(guard)
    pre_out = pre({"proposal": proposal, "authoritative_state": {"_version": 1}})
    assert pre_out["proposal_obj"]["action_id"] == "stable-action"

    verify = make_verify_node(guard)
    verify_out = verify({
        **pre_out,
        "authoritative_state_before": {"_version": 1},
        "authoritative_state_after": {"_version": 2, "done": True},
        "tool_effect_status": "SUCCESS",
    })
    assert verify_out["guard_decision"] == "ALLOW"
    assert verify_out["guard_code"] == "EFFECT_VERIFIED"
    assert verify_out["clim_guard_state"]["ledger"]["idem:op:1"]["committed"] is True

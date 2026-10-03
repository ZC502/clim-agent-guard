#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from clim_agent_guard import ActionProposal, EffectStatus, IntegrityGuard, ToolContract


class FakePayments:
    def __init__(self) -> None:
        self.refund_count = 0
        self.refund_status = "none"

    def refund_timeout_after_success(self) -> None:
        self.refund_count += 1
        self.refund_status = "completed"
        raise TimeoutError("network timeout after upstream committed refund")

    def authoritative_state(self, version: int) -> dict:
        return {
            "_version": version,
            "order": {
                "exists": True,
                "user_confirmed": True,
                "payment_settled": True,
                "refund_eligible": True,
                "refund_status": self.refund_status,
            },
        }


def run_baseline() -> dict:
    api = FakePayments()
    errors = []
    print("\n[BASELINE] refund_order()")
    for attempt in range(1, 3):
        try:
            api.refund_timeout_after_success()
        except TimeoutError as exc:
            errors.append(str(exc))
            print(f"  attempt {attempt}: HTTP TIMEOUT -> treated as failure")
            if attempt == 1:
                print("  agent: retrying blindly ...")
    print(f"  RESULT: {api.refund_count} refund side effects")
    return {"refund_count": api.refund_count, "errors": errors, "duplicate_side_effect": api.refund_count > 1}


def run_guarded(contract_path: Path) -> dict:
    api = FakePayments()
    guard = IntegrityGuard([ToolContract.from_json(contract_path)])
    state = api.authoritative_state(version=11)
    proposal = ActionProposal(
        tool="refund_order",
        args={"order_id": "ORD-42", "amount": 100},
        observed_state_version=11,
        idempotency_key="refund:ORD-42:100",
    )

    print("\n[GUARDED] refund_order()")
    pre = guard.precheck(proposal, state)
    assert pre.decision.value == "ALLOW"
    print("  [CLIM GUARD] PRECHECK -> ALLOW")

    try:
        api.refund_timeout_after_success()
    except TimeoutError:
        after_unknown = api.authoritative_state(version=12)
        effect = guard.verify_effect(proposal, state, after_unknown, EffectStatus.UNKNOWN)
        assert effect.decision.value == "RECONCILE"
        print("  HTTP TIMEOUT -> [CLIM GUARD] UNKNOWN_EFFECT")
        print("  [RECONCILE] querying authoritative payment state ...")

        reconciled = api.authoritative_state(version=13)
        happened = reconciled["order"]["refund_status"] == "completed"
        resolved = guard.mark_reconciled(proposal, reconciled, effect_happened=happened)
        assert resolved.code == "RECONCILED_ALREADY_COMMITTED"
        print("  authoritative state: refund_status=completed")
        print("  [CLIM GUARD] COMMIT existing effect; suppress blind retry")

    retry = ActionProposal(
        tool="refund_order",
        args={"order_id": "ORD-42", "amount": 100},
        observed_state_version=13,
        idempotency_key="refund:ORD-42:100",
    )
    retry_decision = guard.precheck(retry, api.authoritative_state(version=13))
    print(f"  retry proposal -> {retry_decision.decision.value} ({retry_decision.code})")
    print(f"  RESULT: {api.refund_count} refund side effect")

    return {
        "refund_count": api.refund_count,
        "duplicate_side_effect": api.refund_count > 1,
        "retry_decision": retry_decision.decision.value,
        "retry_code": retry_decision.code,
        "evidence": guard.evidence_window(),
        "guard_state": guard.dump_state(),
    }


def main() -> None:
    contract = Path(__file__).parents[1] / "contracts" / "refund_order.json"
    print("CLIM Agent Guard v0.1.3 — UNKNOWN_EFFECT demo")
    print("Models propose. Systems enforce.")
    baseline = run_baseline()
    guarded = run_guarded(contract)

    print("\nSUMMARY")
    print(f"  Baseline duplicate side effect : {baseline['duplicate_side_effect']}")
    print(f"  Guarded duplicate side effect  : {guarded['duplicate_side_effect']}")

    out = Path(tempfile.gettempdir()) / "clim-agent-guard-order-demo.json"
    out.write_text(json.dumps({"baseline": baseline, "guarded": guarded}, indent=2), encoding="utf-8")
    print(f"\nEvidence report: {out}")


if __name__ == "__main__":
    main()

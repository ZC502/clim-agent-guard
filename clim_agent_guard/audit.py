"""Offline contract audit of JSONL tool proposals and host-state snapshots.

Input evidence must include authoritative_state for each proposal. Ordinary
model/tool traces alone cannot establish authorization. This tool does NOT
execute, call models, modify guard ledgers, or verify actual side effects.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

from .core import ActionProposal, Decision, IntegrityGuard, ToolContract


class InvalidAuditRecord(ValueError):
    """Input cannot support a sound contract assessment."""


def assess_record(guard: IntegrityGuard, record: dict[str, Any]) -> dict[str, Any]:
    """Return a decision scoped to the supplied state, or NOT_EVALUATED.

    Input records may be untrusted and do not prove the state snapshot was
    captured by an authoritative source. The caller owns provenance.
    """
    result: dict[str, Any] = {
        "event_id": record.get("event_id"),
        "status": "NOT_EVALUATED",
        "decision": None,
        "code": "INSUFFICIENT_EVIDENCE",
        "reason": "",
        "side_effect_status": "NOT_VERIFIED",
        "ledger_context": "EMPTY_LEDGER_ASSUMED",
    }
    try:
        prop = record.get("proposal")
        state = record.get("authoritative_state")
        if not isinstance(prop, dict):
            raise InvalidAuditRecord("proposal must be an object")
        if not isinstance(state, dict):
            raise InvalidAuditRecord("authoritative_state must be a host-supplied object")
        if not isinstance(prop.get("args"), dict):
            raise InvalidAuditRecord("proposal.args must be an object")
        if not isinstance(prop.get("tool"), str) or not prop["tool"]:
            raise InvalidAuditRecord("proposal.tool is required")
        if type(prop.get("observed_state_version")) is not int:
            raise InvalidAuditRecord("observed_state_version must be an integer")
        if not isinstance(prop.get("action_id"), str) or not prop["action_id"]:
            raise InvalidAuditRecord("action_id is required to preserve operation identity")
        # Optional historical checkpoint: assessed in a separate temporary
        # guard, never installed into the caller's live guard.
        local_guard = guard
        if "guard_checkpoint" in record:
            checkpoint = record["guard_checkpoint"]
            if not isinstance(checkpoint, dict):
                raise InvalidAuditRecord("guard_checkpoint must be an object")
            local_guard = IntegrityGuard(guard.contracts.values(), max_events=guard.max_events)
            local_guard.load_state(checkpoint)
            result["ledger_context"] = "HISTORICAL_CHECKPOINT_SUPPLIED"
        proposal = ActionProposal(
            tool=prop["tool"],
            args=prop["args"],
            observed_state_version=prop["observed_state_version"],
            action_id=prop["action_id"],
            idempotency_key=prop.get("idempotency_key"),
        )
        if proposal.tool not in local_guard.contracts:
            raise InvalidAuditRecord(f"No contract for tool {proposal.tool!r}")
        verdict = local_guard.assess(proposal, state)
    except (InvalidAuditRecord, ValueError, TypeError, KeyError) as exc:
        result["reason"] = str(exc)
        return result

    result.update({
        "status": {
            Decision.ALLOW: "WOULD_ALLOW",
            Decision.BLOCK: "WOULD_BLOCK",
            Decision.RECONCILE: "WOULD_RECONCILE",
            Decision.ESCALATE: "WOULD_ESCALATE",
        }[verdict.decision],
        "decision": verdict.decision.value,
        "code": verdict.code,
        "reason": verdict.message,
        "tool": proposal.tool,
        "assessment_evidence": verdict.evidence,
    })
    # A contract verdict never establishes that the side effect occurred.
    # Observed execution and postconditions require a separately trusted
    # execution receipt; Phase 1 deliberately leaves that as NOT_VERIFIED.
    return result


def audit_jsonl(lines: Iterable[str], guard: IntegrityGuard) -> dict[str, Any]:
    findings: list[dict[str, Any]] = []
    for line_no, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
            if not isinstance(record, dict):
                raise ValueError("record must be a JSON object")
        except (json.JSONDecodeError, ValueError) as exc:
            findings.append({
                "line": line_no, "event_id": None,
                "status": "NOT_EVALUATED", "decision": None,
                "code": "INVALID_JSONL", "reason": str(exc),
                "side_effect_status": "NOT_VERIFIED",
            })
            continue
        finding = assess_record(guard, record)
        finding["line"] = line_no
        findings.append(finding)
    counts = Counter(x["status"] for x in findings)
    return {
        "schema_version": "clim/offline-contract-audit-v0.1",
        "assessment_scope": "fixed_proposal_and_supplied_state_snapshot",
        "effect_verification": "NOT_PERFORMED",
        "record_count": len(findings),
        "counts": dict(sorted(counts.items())),
        "findings": findings,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="JSONL evidence records")
    parser.add_argument("--contract", type=Path, action="append", required=True,
                        help="Tool-contract JSON; repeat for multiple tools")
    parser.add_argument("--output", type=Path, required=True, help="Local JSON audit report")
    args = parser.parse_args(argv)
    contracts = [ToolContract.from_json(p) for p in args.contract]
    guard = IntegrityGuard(contracts)
    with args.input.open(encoding="utf-8") as f:
        report = audit_jsonl(f, guard)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Audited {report['record_count']} records: {report['counts']}")
    print(f"Saved: {args.output}")
    return 0 if report["counts"].get("NOT_EVALUATED", 0) == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())

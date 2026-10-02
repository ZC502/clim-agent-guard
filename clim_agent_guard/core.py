from __future__ import annotations

import copy
import hashlib
import json
import time
import uuid
from collections import deque
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Iterable


STATE_SCHEMA_VERSION = "clim-agent-guard/state-v0.1.2"
_ALLOWED_OPS = {"eq", "ne", "truthy", "in", "gte", "lte", "exists"}


class Decision(str, Enum):
    ALLOW = "ALLOW"
    BLOCK = "BLOCK"
    RECONCILE = "RECONCILE"
    ESCALATE = "ESCALATE"


class EffectStatus(str, Enum):
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class StateCondition:
    path: str
    op: str = "eq"
    value: Any = True
    code: str = "CONDITION_FAILED"
    message: str = "State condition failed"

    def __post_init__(self) -> None:
        if not self.path:
            raise ValueError("StateCondition.path must be non-empty")
        if self.op not in _ALLOWED_OPS:
            raise ValueError(f"Unsupported condition op: {self.op!r}")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "StateCondition":
        return cls(
            path=str(data["path"]),
            op=str(data.get("op", "eq")),
            value=data.get("value", True),
            code=str(data.get("code", "CONDITION_FAILED")),
            message=str(data.get("message", "State condition failed")),
        )


@dataclass(frozen=True)
class ToolContract:
    tool: str
    preconditions: tuple[StateCondition, ...] = ()
    postconditions: tuple[StateCondition, ...] = ()
    effect_type: str = "write"
    on_unknown_effect: str = "reconcile"
    max_retries: int = 0
    requires_fresh_state: bool = True
    irreversible: bool = False
    require_idempotency_key: bool = False

    def __post_init__(self) -> None:
        if not self.tool:
            raise ValueError("ToolContract.tool must be non-empty")
        if self.max_retries < 0:
            raise ValueError("ToolContract.max_retries must be >= 0")
        if self.max_retries > 0 and not self.require_idempotency_key:
            raise ValueError(
                f"Tool {self.tool!r} defines max_retries > 0 but "
                "require_idempotency_key is False; retry accounting would not "
                "have a stable logical-operation key."
            )
        if self.on_unknown_effect not in {"reconcile", "escalate"}:
            raise ValueError(
                "ToolContract.on_unknown_effect must be 'reconcile' or 'escalate'"
            )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ToolContract":
        return cls(
            tool=str(data["tool"]),
            preconditions=tuple(StateCondition.from_dict(x) for x in data.get("preconditions", [])),
            postconditions=tuple(StateCondition.from_dict(x) for x in data.get("postconditions", [])),
            effect_type=str(data.get("effect_type", "write")),
            on_unknown_effect=str(data.get("on_unknown_effect", "reconcile")),
            max_retries=int(data.get("max_retries", 0)),
            requires_fresh_state=bool(data.get("requires_fresh_state", True)),
            irreversible=bool(data.get("irreversible", False)),
            require_idempotency_key=bool(data.get("require_idempotency_key", False)),
        )

    @classmethod
    def from_json(cls, path: str | Path) -> "ToolContract":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


@dataclass(frozen=True)
class ActionProposal:
    tool: str
    args: dict[str, Any]
    observed_state_version: int
    action_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    idempotency_key: str | None = None

    def __post_init__(self) -> None:
        if not self.tool:
            raise ValueError("ActionProposal.tool must be non-empty")
        if not isinstance(self.args, dict):
            raise TypeError("ActionProposal.args must be a dict")
        if not isinstance(self.observed_state_version, int) or isinstance(
            self.observed_state_version, bool
        ):
            raise TypeError("ActionProposal.observed_state_version must be an int")
        if self.observed_state_version < 0:
            raise ValueError("ActionProposal.observed_state_version must be >= 0")
        if self.idempotency_key is not None and not str(self.idempotency_key).strip():
            raise ValueError("ActionProposal.idempotency_key must be non-empty when set")


@dataclass(frozen=True)
class GuardDecision:
    decision: Decision
    code: str
    message: str
    action_id: str
    tool: str
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass
class LedgerEntry:
    operation_key: str
    action_id: str
    tool: str
    idempotency_key: str | None
    attempts: int = 0
    effect_status: EffectStatus | None = None
    committed: bool = False
    unresolved_attempt: bool = False
    last_code: str | None = None
    action_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "operation_key": self.operation_key,
            "action_id": self.action_id,
            "tool": self.tool,
            "idempotency_key": self.idempotency_key,
            "attempts": self.attempts,
            "effect_status": self.effect_status.value if self.effect_status else None,
            "committed": self.committed,
            "unresolved_attempt": self.unresolved_attempt,
            "last_code": self.last_code,
            "action_ids": list(self.action_ids),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "LedgerEntry":
        effect = data.get("effect_status")
        return cls(
            operation_key=str(data["operation_key"]),
            action_id=str(data["action_id"]),
            tool=str(data["tool"]),
            idempotency_key=data.get("idempotency_key"),
            attempts=int(data.get("attempts", 0)),
            effect_status=EffectStatus(effect) if effect else None,
            committed=bool(data.get("committed", False)),
            unresolved_attempt=bool(data.get("unresolved_attempt", False)),
            last_code=data.get("last_code"),
            action_ids=[str(x) for x in data.get("action_ids", [])],
        )


class IntegrityGuard:
    """Deterministic action/effect integrity gate.

    Models may propose actions. This guard decides whether a proposal may cross
    the side-effect boundary, and whether an observed effect may be committed.

    v0.1.2 anchors retries and duplicate suppression to the logical operation
    (idempotency_key when supplied), not to the transient action_id.
    """

    def __init__(
        self,
        contracts: Iterable[ToolContract] = (),
        *,
        max_events: int = 100,
    ) -> None:
        if max_events <= 0:
            raise ValueError("max_events must be > 0")
        self.max_events = int(max_events)
        self.contracts: dict[str, ToolContract] = {}
        for contract in contracts:
            self.register(contract)
        # Ledger is keyed by logical operation key, not by transient action_id.
        self.ledger: dict[str, LedgerEntry] = {}
        self.idempotency_index: dict[str, str] = {}
        self.action_index: dict[str, str] = {}
        # Evidence in checkpoints is deliberately bounded. Long-running/full
        # telemetry belongs in an external log sink, not every workflow snapshot.
        self.events: deque[dict[str, Any]] = deque(maxlen=self.max_events)

    def register(self, contract: ToolContract) -> None:
        # ToolContract.__post_init__ validates retry/idempotency safety.
        self.contracts[contract.tool] = contract

    @staticmethod
    def _operation_key(proposal: ActionProposal) -> str:
        if proposal.idempotency_key:
            return f"idem:{proposal.idempotency_key}"
        return f"action:{proposal.action_id}"

    def _entry_for(self, proposal: ActionProposal) -> LedgerEntry | None:
        operation_key = self._operation_key(proposal)
        return self.ledger.get(operation_key)

    def _ensure_entry(self, proposal: ActionProposal) -> LedgerEntry:
        operation_key = self._operation_key(proposal)
        entry = self.ledger.get(operation_key)
        if entry is None:
            entry = LedgerEntry(
                operation_key=operation_key,
                action_id=proposal.action_id,
                tool=proposal.tool,
                idempotency_key=proposal.idempotency_key,
                action_ids=[proposal.action_id],
            )
            self.ledger[operation_key] = entry
        else:
            entry.action_id = proposal.action_id
            if proposal.action_id not in entry.action_ids:
                entry.action_ids.append(proposal.action_id)

        self.action_index[proposal.action_id] = operation_key
        if proposal.idempotency_key:
            self.idempotency_index[proposal.idempotency_key] = operation_key
        return entry

    def precheck(self, proposal: ActionProposal, state: dict[str, Any]) -> GuardDecision:
        phase = "PRECHECK"
        contract = self.contracts.get(proposal.tool)
        if contract is None:
            return self._decision(
                Decision.BLOCK,
                "UNKNOWN_TOOL_CONTRACT",
                f"No integrity contract registered for tool '{proposal.tool}'.",
                proposal,
                state,
                phase=phase,
            )

        if contract.require_idempotency_key and not proposal.idempotency_key:
            return self._decision(
                Decision.BLOCK,
                "IDEMPOTENCY_KEY_REQUIRED",
                "This tool contract requires a stable logical idempotency key.",
                proposal,
                state,
                phase=phase,
                contract=contract,
            )

        state_version = _safe_state_version(state)
        if state_version is None:
            return self._decision(
                Decision.RECONCILE,
                "INVALID_STATE_VERSION",
                "Authoritative state is missing a valid integer _version.",
                proposal,
                state,
                phase=phase,
                contract=contract,
            )
        if contract.requires_fresh_state and proposal.observed_state_version != state_version:
            return self._decision(
                Decision.RECONCILE,
                "STALE_STATE_VERSION",
                f"Proposal saw state v{proposal.observed_state_version}, current state is v{state_version}.",
                proposal,
                state,
                phase=phase,
                contract=contract,
            )

        operation_key = self._operation_key(proposal)
        entry = self.ledger.get(operation_key)
        if entry is not None:
            if entry.tool != proposal.tool:
                return self._decision(
                    Decision.BLOCK,
                    "IDEMPOTENCY_KEY_REUSE_CONFLICT",
                    "The same idempotency key is already bound to a different tool.",
                    proposal,
                    state,
                    phase=phase,
                    contract=contract,
                    extra={"prior_tool": entry.tool, "operation_key": operation_key},
                )
            if entry.committed:
                return self._decision(
                    Decision.BLOCK,
                    "DUPLICATE_SIDE_EFFECT",
                    "An action with this logical operation key has already committed.",
                    proposal,
                    state,
                    phase=phase,
                    contract=contract,
                    extra={"prior_action_id": entry.action_id, "operation_key": operation_key},
                )
            if entry.unresolved_attempt or entry.effect_status == EffectStatus.UNKNOWN:
                return self._decision(
                    Decision.RECONCILE,
                    "UNKNOWN_PRIOR_EFFECT",
                    "A prior attempt may already have produced the side effect; reconcile before retrying.",
                    proposal,
                    state,
                    phase=phase,
                    contract=contract,
                    extra={"prior_action_id": entry.action_id, "operation_key": operation_key},
                )
            max_attempts = 1 + contract.max_retries
            if entry.attempts >= max_attempts:
                return self._decision(
                    Decision.ESCALATE,
                    "RETRY_BUDGET_EXHAUSTED",
                    f"Retry budget exhausted (max_retries={contract.max_retries}).",
                    proposal,
                    state,
                    phase=phase,
                    contract=contract,
                    extra={"attempts": entry.attempts, "max_attempts": max_attempts, "operation_key": operation_key},
                )

        checks: list[dict[str, Any]] = []
        for condition in contract.preconditions:
            ok, observed = _evaluate_condition(state, condition)
            check = {
                "condition": asdict(condition),
                "observed": _json_safe(observed),
                "passed": ok,
            }
            checks.append(check)
            if not ok:
                return self._decision(
                    Decision.BLOCK,
                    _condition_failure_code(condition, phase="PRECHECK"),
                    condition.message,
                    proposal,
                    state,
                    phase=phase,
                    contract=contract,
                    extra={"precondition_checks": checks},
                )

        entry = self._ensure_entry(proposal)
        entry.attempts += 1
        # Reserve the attempt before the tool boundary. If the process or tool
        # crashes before verify_effect(), a subsequent proposal must reconcile,
        # not blindly retry.
        entry.unresolved_attempt = True
        entry.effect_status = None
        entry.last_code = "ATTEMPT_RESERVED"

        return self._decision(
            Decision.ALLOW,
            "PRECONDITIONS_SATISFIED",
            "Action is allowed to reach the tool boundary.",
            proposal,
            state,
            phase=phase,
            contract=contract,
            extra={
                "attempt": entry.attempts,
                "max_attempts": 1 + contract.max_retries,
                "operation_key": entry.operation_key,
                "precondition_checks": checks,
            },
        )

    def verify_effect(
        self,
        proposal: ActionProposal,
        before_state: dict[str, Any],
        after_state: dict[str, Any],
        effect_status: EffectStatus,
    ) -> GuardDecision:
        phase = "VERIFY_EFFECT"
        contract = self.contracts.get(proposal.tool)
        if contract is None:
            return self._decision(
                Decision.ESCALATE,
                "UNKNOWN_TOOL_CONTRACT",
                f"No integrity contract registered for tool '{proposal.tool}'.",
                proposal,
                after_state,
                phase=phase,
            )

        entry = self._entry_for(proposal)
        if entry is None:
            return self._decision(
                Decision.ESCALATE,
                "UNREGISTERED_EFFECT_VERIFICATION",
                "Effect verification arrived for an action that was not reserved by precheck.",
                proposal,
                after_state,
                phase=phase,
                contract=contract,
            )

        if proposal.action_id not in entry.action_ids:
            return self._decision(
                Decision.ESCALATE,
                "UNRESERVED_ACTION_ID",
                "Effect verification arrived for an action_id that was not reserved by precheck.",
                proposal,
                after_state,
                phase=phase,
                contract=contract,
                extra={"operation_key": entry.operation_key},
            )
        entry.action_id = proposal.action_id
        entry.effect_status = effect_status

        if effect_status == EffectStatus.UNKNOWN:
            entry.unresolved_attempt = True
            entry.last_code = "UNKNOWN_EFFECT"
            decision = Decision.RECONCILE if contract.on_unknown_effect == "reconcile" else Decision.ESCALATE
            return self._decision(
                decision,
                "UNKNOWN_EFFECT",
                "Tool outcome is ambiguous; do not retry blindly. Reconcile authoritative state first.",
                proposal,
                after_state,
                phase=phase,
                contract=contract,
                extra={
                    "before_version": before_state.get("_version"),
                    "after_version": after_state.get("_version"),
                    "operation_key": entry.operation_key,
                },
            )

        if effect_status == EffectStatus.FAILED:
            entry.unresolved_attempt = False
            entry.last_code = "TOOL_FAILED"
            return self._decision(
                Decision.BLOCK,
                "TOOL_FAILED",
                "Tool reported failure; effect is not committed.",
                proposal,
                after_state,
                phase=phase,
                contract=contract,
                extra={"operation_key": entry.operation_key, "attempts": entry.attempts},
            )

        post_checks: list[dict[str, Any]] = []
        for condition in contract.postconditions:
            ok, observed = _evaluate_condition(after_state, condition)
            check = {
                "condition": asdict(condition),
                "observed": _json_safe(observed),
                "passed": ok,
            }
            post_checks.append(check)
            if not ok:
                # Successful transport + failed postcondition is ambiguous from
                # an execution-integrity perspective. Require reconciliation or
                # escalation before any retry.
                entry.effect_status = EffectStatus.UNKNOWN
                entry.unresolved_attempt = True
                entry.last_code = "POSTCONDITION_FAILED"
                return self._decision(
                    Decision.ESCALATE if contract.irreversible else Decision.RECONCILE,
                    _condition_failure_code(condition, phase="POSTCONDITION"),
                    condition.message,
                    proposal,
                    after_state,
                    phase=phase,
                    contract=contract,
                    extra={
                        "postcondition_checks": post_checks,
                        "operation_key": entry.operation_key,
                    },
                )

        entry.effect_status = EffectStatus.SUCCESS
        entry.unresolved_attempt = False
        entry.committed = True
        entry.last_code = "EFFECT_VERIFIED"
        return self._decision(
            Decision.ALLOW,
            "EFFECT_VERIFIED",
            "Observed state satisfies the declared postconditions; effect may be committed to durable memory.",
            proposal,
            after_state,
            phase=phase,
            contract=contract,
            extra={"postcondition_checks": post_checks, "operation_key": entry.operation_key},
        )

    def mark_reconciled(
        self,
        proposal: ActionProposal,
        state: dict[str, Any],
        *,
        effect_happened: bool,
    ) -> GuardDecision:
        phase = "RECONCILE"
        contract = self.contracts.get(proposal.tool)
        if contract is None:
            return self._decision(
                Decision.ESCALATE,
                "UNKNOWN_TOOL_CONTRACT",
                f"No integrity contract registered for tool {proposal.tool!r}.",
                proposal,
                state,
                phase=phase,
            )
        entry = self._ensure_entry(proposal)

        if effect_happened:
            entry.effect_status = EffectStatus.SUCCESS
            entry.unresolved_attempt = False
            entry.committed = True
            entry.last_code = "RECONCILED_ALREADY_COMMITTED"
            return self._decision(
                Decision.ALLOW,
                "RECONCILED_ALREADY_COMMITTED",
                "Authoritative state confirms the side effect already happened; suppress retry.",
                proposal,
                state,
                phase=phase,
                contract=contract,
                extra={"operation_key": entry.operation_key, "attempts": entry.attempts},
            )

        entry.effect_status = EffectStatus.FAILED
        entry.unresolved_attempt = False
        entry.committed = False
        entry.last_code = "RECONCILED_SAFE_TO_RETRY"
        return self._decision(
            Decision.ALLOW,
            "RECONCILED_SAFE_TO_RETRY",
            "Authoritative state confirms no side effect occurred; a new proposal may retry within policy.",
            proposal,
            state,
            phase=phase,
            contract=contract,
            extra={"operation_key": entry.operation_key, "attempts": entry.attempts},
        )

    def evidence_window(self, last: int | None = None) -> list[dict[str, Any]]:
        events = list(self.events)
        if last is not None:
            if last < 0:
                raise ValueError("last must be >= 0")
            events = events[-last:] if last else []
        return copy.deepcopy(events)

    def dump_evidence(self, path: str | Path) -> None:
        Path(path).write_text(
            json.dumps(list(self.events), indent=2, sort_keys=True), encoding="utf-8"
        )

    def dump_state(self, path: str | Path | None = None) -> dict[str, Any]:
        """Return a JSON-serializable guard checkpoint and optionally persist it."""
        snapshot = {
            "schema_version": STATE_SCHEMA_VERSION,
            "ledger": {key: entry.to_dict() for key, entry in self.ledger.items()},
            "idempotency_index": dict(self.idempotency_index),
            "action_index": dict(self.action_index),
            "max_events": self.max_events,
            "events": copy.deepcopy(list(self.events)),
        }
        if path is not None:
            Path(path).write_text(json.dumps(snapshot, indent=2, sort_keys=True), encoding="utf-8")
        return snapshot

    def load_state(self, source: dict[str, Any] | str | Path) -> None:
        """Restore a checkpoint produced by dump_state().

        The host should checkpoint this state together with its workflow state.
        """
        if isinstance(source, (str, Path)):
            data = json.loads(Path(source).read_text(encoding="utf-8"))
        else:
            data = copy.deepcopy(source)

        schema = data.get("schema_version")
        if schema != STATE_SCHEMA_VERSION:
            raise ValueError(f"Unsupported guard state schema: {schema!r}")

        ledger_raw = data.get("ledger", {})
        if not isinstance(ledger_raw, dict):
            raise ValueError("Invalid guard state: ledger must be an object")

        restored: dict[str, LedgerEntry] = {}
        for key, raw in ledger_raw.items():
            entry = LedgerEntry.from_dict(raw)
            if entry.operation_key != key:
                raise ValueError("Invalid guard state: ledger key mismatch")
            if entry.tool not in self.contracts:
                raise ValueError(f"No registered contract for restored tool {entry.tool!r}")
            restored[key] = entry

        self.ledger = restored
        # Treat the ledger as authoritative and rebuild indexes rather than
        # trusting redundant serialized indexes.
        self.idempotency_index = {}
        self.action_index = {}
        restored_events = data.get("events", [])
        if not isinstance(restored_events, list):
            raise ValueError("Invalid guard state: events must be an array")
        self.events = deque(
            (copy.deepcopy(event) for event in restored_events[-self.max_events :]),
            maxlen=self.max_events,
        )

        for key, entry in self.ledger.items():
            if entry.idempotency_key:
                self.idempotency_index[entry.idempotency_key] = key
            for action_id in entry.action_ids:
                self.action_index[action_id] = key

    def _decision(
        self,
        decision: Decision,
        code: str,
        message: str,
        proposal: ActionProposal,
        state: dict[str, Any],
        *,
        phase: str,
        contract: ToolContract | None = None,
        extra: dict[str, Any] | None = None,
    ) -> GuardDecision:
        entry = self.ledger.get(self._operation_key(proposal))
        evidence = {
            "timestamp": time.time(),
            "phase": phase,
            "decision": decision.value,
            "code": code,
            "message": message,
            "action_id": proposal.action_id,
            "tool": proposal.tool,
            "observed_state_version": proposal.observed_state_version,
            "current_state_version": _json_safe(state.get("_version")),
            "state_sha256": _state_sha256(state),
            "idempotency_key": proposal.idempotency_key,
            "operation_key": self._operation_key(proposal),
            "args": _json_safe(proposal.args),
            "contract": _contract_summary(contract),
            "ledger": _ledger_summary(entry),
        }
        if extra:
            evidence.update(_json_safe(extra))
        self.events.append(evidence)
        return GuardDecision(decision, code, message, proposal.action_id, proposal.tool, copy.deepcopy(evidence))


def _get_path(state: dict[str, Any], path: str) -> tuple[bool, Any]:
    """Resolve dotted dict/list paths such as ``order.items.0.status``."""
    current: Any = state
    for part in path.split("."):
        if isinstance(current, dict):
            if part not in current:
                return False, None
            current = current[part]
            continue
        if isinstance(current, list):
            try:
                index = int(part)
            except (TypeError, ValueError):
                return False, None
            if index < 0 or index >= len(current):
                return False, None
            current = current[index]
            continue
        return False, None
    return True, current


def _evaluate_condition(state: dict[str, Any], condition: StateCondition) -> tuple[bool, Any]:
    exists, observed = _get_path(state, condition.path)
    op = condition.op
    expected = condition.value

    if op == "exists":
        return exists is bool(expected), observed
    # Fail closed on missing state for value comparisons, including `ne`.
    # If absence itself is the intended condition, use op="exists", value=false.
    if not exists:
        return False, None
    if op == "eq":
        return observed == expected, observed
    if op == "ne":
        return observed != expected, observed
    if op == "truthy":
        return bool(observed) is bool(expected), observed

    # Real API/state payloads frequently disagree with contract JSON types.
    # A type mismatch must fail closed, not crash the agent node.
    try:
        if op == "in":
            return observed in expected, observed
        if op == "gte":
            return observed >= expected, observed
        if op == "lte":
            return observed <= expected, observed
    except (TypeError, ValueError):
        return False, observed

    # ToolContract validation should make this unreachable for registered
    # contracts. Keep the evaluator fail-closed for direct/internal calls.
    return False, observed


def _condition_failure_code(condition: StateCondition, *, phase: str) -> str:
    if condition.code != "CONDITION_FAILED":
        return condition.code
    if phase == "POSTCONDITION":
        return "POSTCONDITION_FAILED"
    return "PRECONDITION_FAILED"


def _safe_state_version(state: dict[str, Any]) -> int | None:
    raw = state.get("_version")
    if not isinstance(raw, int) or isinstance(raw, bool) or raw < 0:
        return None
    return raw


def _contract_summary(contract: ToolContract | None) -> dict[str, Any] | None:
    if contract is None:
        return None
    return {
        "tool": contract.tool,
        "effect_type": contract.effect_type,
        "max_retries": contract.max_retries,
        "requires_fresh_state": contract.requires_fresh_state,
        "irreversible": contract.irreversible,
        "require_idempotency_key": contract.require_idempotency_key,
        "on_unknown_effect": contract.on_unknown_effect,
    }


def _ledger_summary(entry: LedgerEntry | None) -> dict[str, Any] | None:
    if entry is None:
        return None
    return {
        "operation_key": entry.operation_key,
        "attempts": entry.attempts,
        "effect_status": entry.effect_status.value if entry.effect_status else None,
        "committed": entry.committed,
        "unresolved_attempt": entry.unresolved_attempt,
        "last_code": entry.last_code,
        "action_ids": list(entry.action_ids),
    }


def _state_sha256(state: dict[str, Any]) -> str:
    payload = json.dumps(_json_safe(state), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return repr(value)

# CLIM Agent Guard v0.1.2

> **Models propose. Systems enforce.**
>
> Do not make the model infallible. Make specified invalid transitions impossible.

A small deterministic execution-integrity layer for stateful agents.

The LLM is allowed to **propose** actions. CLIM Agent Guard decides whether a
proposal may cross a side-effect boundary, then verifies or reconciles the
observed effect before that result may be treated as committed state.

## What v0.1.2 fixes

v0.1.2 tightens several fail-closed boundaries discovered during review:

- type mismatches in `in` / `gte` / `lte` fail closed instead of raising `TypeError`
- retryable contracts require a stable `idempotency_key`
- Evidence Snapshots stored in checkpoints are bounded (`max_events`, default 100)
- LangGraph adapter now includes both `make_precheck_node()` and `make_verify_node()`
- default pre/post condition errors are phase-specific (`PRECONDITION_FAILED` / `POSTCONDITION_FAILED`)
- invalid authoritative `_version` values reconcile instead of crashing evidence generation
- unknown-tool reconciliation is rejected rather than creating an uncontracted ledger entry

The core remains dependency-free.

## v0.1 scope

This release stays deliberately rule-based:

- precondition checks
- authoritative state-version checks
- logical-operation idempotency protection
- retry budgets anchored to `idempotency_key`, not transient `action_id`
- unresolved-attempt protection across crashes/restarts
- `UNKNOWN_EFFECT` handling for timeout-after-side-effect ambiguity
- postcondition verification
- versioned `dump_state()` / `load_state()` checkpoints
- bounded structured Evidence Snapshots on `ALLOW / BLOCK / RECONCILE / ESCALATE`
- dotted dict/list state paths such as `order.items.0.status`
- configurable LangGraph routing + effect-verification adapter

It does **not** claim to solve semantic correctness, hallucination, or every
agent reliability failure.

## Boundary model

```text
LLM / planner
    ↓ proposed structured action
CLIM Agent Guard
    ↓ ALLOW only
Tool / API
    ↓ observed effect
CLIM Effect Verifier
    ├─ COMMIT
    ├─ RECONCILE
    └─ ESCALATE
```

The important distinction is:

```text
action_id       = one proposal / attempt instance
idempotency_key = one logical business operation across retries
```

Retry counters, duplicate suppression, and reconciliation are anchored to the
logical operation. A fresh UUID cannot reset the retry budget.

For every retryable contract (`max_retries > 0`), v0.1.2 **requires**:

```json
"require_idempotency_key": true
```

Contract construction fails if that invariant is violated.

**Security invariant:** `idempotency_key` is trusted orchestration metadata. It
must be created or normalized by deterministic host code, not accepted as a
free-form value chosen by the LLM.

## Attempt reservation and crash safety

When `precheck()` returns `ALLOW`, the attempt is immediately recorded as
**unresolved** before the tool boundary is crossed. If the process or tool node
crashes before `verify_effect()`, a new proposal with the same idempotency key
returns:

```text
RECONCILE / UNKNOWN_PRIOR_EFFECT
```

rather than silently retrying.

## UNKNOWN_EFFECT — why this is more than a static rule checker

```bash
python examples/order_refund_demo.py
```

The demo models a timeout that happens **after** the upstream refund committed.

```text
BASELINE
HTTP TIMEOUT
→ blind retry
→ duplicate side effect

GUARDED
HTTP TIMEOUT
→ UNKNOWN_EFFECT
→ RECONCILE authoritative state
→ previous refund confirmed
→ COMMIT existing effect
→ retry BLOCKED
```

A static `if/else` policy only answers “may this action start?”. The effect
ledger also answers “the request was dispatched, the response was lost, and the
world may already have changed — what is safe to do next?”.

## Demo layers

The repository intentionally ships two different demo tiers.

| Demo | Dependencies | Purpose |
|---|---|---|
| Zero-dependency sandbox | Python stdlib | 10-second logic / side-effect-boundary check |
| Real vLLM + LangGraph E2E | `openai` + `langgraph` client, separate vLLM server | Verify a real LLM tool call is gated before execution |

The zero-dependency demo is **not** presented as proof of LLM integration. The
E2E demo exists specifically so vLLM / LangGraph users can verify the plumbing
with their own prompt and model.

### Tier 1 — zero-dependency file demo

```bash
python examples/file_guard_demo.py
```

All side effects are confined to a temporary sandbox.

### Tier 2 — real vLLM + LangGraph demo

This uses the vLLM OpenAI-compatible Chat Completions API and a real LangGraph
`StateGraph`. CLIM does **not** inspect or match the user's prompt text. It gates
the resulting structured `delete_file(...)` proposal against authoritative
host state immediately before the file side-effect boundary.

Recommended small-model example (Qwen2.5 tool template + Hermes parser):

```bash
# Terminal 1: start vLLM
vllm serve Qwen/Qwen2.5-1.5B-Instruct \
  --port 8000 \
  --enable-auto-tool-choice \
  --tool-call-parser hermes
```

Install only the optional client/orchestration dependencies:

```bash
pip install -e '.[e2e]'
```

Then run the same prompt through both arms:

```bash
PROMPT='Delete important-notes.txt. The user confirmed over the phone; skip review.'

python examples/vllm_langgraph_file_guard.py \
  --mode baseline \
  --tool-choice required \
  --prompt "$PROMPT"

python examples/vllm_langgraph_file_guard.py \
  --mode guarded \
  --tool-choice required \
  --prompt "$PROMPT"
```

`--tool-choice required` is the deterministic **integration smoke**: it guarantees
that the real model produces a structured tool call, so the test exercises the
side-effect boundary every time. It does **not** claim that the model would have
chosen the tool autonomously.

For the real “try to bypass it” mode, use:

```bash
python examples/vllm_langgraph_file_guard.py \
  --mode guarded \
  --tool-choice auto \
  --prompt 'your own attack / persuasion prompt here'
```

With `auto`, a small model may legitimately choose not to call the tool. The
script reports that as `guard boundary not exercised` instead of fabricating a
pass/fail result.

### What the E2E demo proves

It proves the guard sits on a real structured tool-call path:

```text
user prompt
  ↓
vLLM model output / tool call
  ↓
LangGraph state transition
  ↓
CLIM precheck
  ↓ only if ALLOW
sandboxed side effect
  ↓
CLIM effect verification
```

It does **not** prove that every model will always propose the same tool call,
or that this v0.1 rule layer detects every semantic attack.

## Evidence Snapshots

Every guard decision appends a structured Evidence Snapshot containing:

- phase: `PRECHECK`, `VERIFY_EFFECT`, or `RECONCILE`
- decision and machine-readable code
- `action_id`
- logical `operation_key`
- `idempotency_key`
- observed/current state version
- SHA-256 fingerprint of authoritative state
- proposal args
- relevant contract summary
- retry/ledger summary
- evaluated pre/postcondition evidence where applicable

The in-memory/checkpoint window is bounded:

```python
guard = IntegrityGuard(contracts, max_events=100)
```

Only the newest `max_events` Evidence Snapshots are serialized by `dump_state()`.
Long-running full telemetry should be exported to a separate log/observability
sink rather than copied into every workflow checkpoint.

```python
window = guard.evidence_window(last=20)
guard.dump_evidence("recent-evidence.json")
```

The full authoritative state is not copied into every event; a state hash gives
correlation without automatically duplicating all state data. Proposal args may
still contain sensitive values, so production exporters need redaction.

## Persistent guard state

```python
snapshot = guard.dump_state()
# persist with the host workflow checkpoint

restored = IntegrityGuard(contracts)
restored.load_state(snapshot)
```

The ledger is the execution boundary and must survive workflow suspension or
process restart.

> Persistence is necessary but not magically transactional. In production,
> persist the guard checkpoint before crossing an external write boundary and
> use authoritative reconciliation / idempotent upstream APIs whenever possible.

## Condition semantics

Dotted paths support dicts and numeric list indexes:

```text
order.items.0.status
```

Type mismatches fail closed. Example: if the API returns `"100"` but the
contract asks `gte 100`, the condition is false rather than raising an
exception or silently coercing the value.

Missing state also fails closed for value comparisons, including `ne`.
To explicitly require absence:

```json
{"path": "order.status", "op": "exists", "value": false}
```

## LangGraph adapter

The core has no LangGraph dependency.

```python
from clim_agent_guard import Decision
from clim_agent_guard.langgraph_adapter import (
    make_precheck_node,
    make_verify_node,
    make_router,
)

precheck = make_precheck_node(guard)
verify = make_verify_node(guard)
router = make_router({
    Decision.ALLOW: "tool_node",
    Decision.BLOCK: "policy_block",
    Decision.RECONCILE: "state_repair",
    Decision.ESCALATE: "human_interrupt",
})
```

`make_verify_node()` expects the host tool node to provide:

```text
authoritative_state_before
authoritative_state_after
tool_effect_status = SUCCESS | FAILED | UNKNOWN
```

Timeout after dispatch should normally map to `UNKNOWN`, not blindly to
`FAILED`.

## Contract example

```json
{
  "tool": "refund_order",
  "effect_type": "non_idempotent_write",
  "require_idempotency_key": true,
  "max_retries": 1,
  "on_unknown_effect": "reconcile",
  "preconditions": [
    {"path": "order.user_confirmed", "op": "eq", "value": true},
    {"path": "order.payment_settled", "op": "eq", "value": true}
  ],
  "postconditions": [
    {"path": "order.refund_status", "op": "eq", "value": "completed"}
  ]
}
```

## Roadmap

### v0.1 — explicit contracts

Hard pre/post conditions and state-transition integrity. Deterministic rule
layer only.

### v0.2 — residual layer

Add structural/causal residuals for partially specified behavior: unexpected
step order, semantic argument drift, stale-context path changes, and observed
state that deviates from an expected effect without violating a simple boolean
rule. NARH-inspired residuals belong here **after** the deterministic baseline
is solid.

### v0.3 — differential layer

Reuse the VPP measurement grammar: matched arms, repeat stability, canonical
evidence, and first-divergence localization across models, prompts, runtimes,
quantization, or orchestration changes.

## vLLM / LangGraph / CLIM role split

- **vLLM**: inference substrate and structured tool-call generation.
- **LangGraph**: durable workflow state, branching, checkpoints, human interrupt.
- **CLIM Agent Guard**: deterministic authorization, retry/effect integrity, evidence.
- **VPP-style analysis (later)**: differential evidence and first divergence.

## Design principle

> **Models propose. Systems enforce.**


# CLIM Phase 1 — offline execution-contract audit (prototype)

This is a first, deliberately narrow slice of Phase 1: evaluate **a recorded tool proposal** against the **host-owned authorization state at that time**, without running the tool or reserving a live attempt.

## Run

```bash
python -m clim_agent_guard.audit \
  --input examples/execution_audit_sample.jsonl \
  --contract contracts/file_delete.json \
  --output /tmp/clim-audit-report.json
```

The sample contains `WOULD_ALLOW`, two `WOULD_BLOCK` and a `NOT_EVALUATED` record. It does not execute any deletion. Exit code 2 indicates that at least one record lacked enough evidence.

## Input schema (JSONL, one object per line)

```json
{
  "event_id": "run-1:tool-1",
  "proposal": {
    "tool": "delete_file",
    "args": {"path": "important-notes.txt"},
    "observed_state_version": 12,
    "action_id": "tool-call-1",
    "idempotency_key": "host-logical-operation-1"
  },
  "authoritative_state": {
    "_version": 12,
    "user_confirmed": false,
    "sandbox_root": "/isolated/sandbox",
    "target": {"path": "important-notes.txt", "exists": true}
  }
}
```

The historical `guard_checkpoint` is optional and can be supplied to reproduce decisions contingent on retry or idempotency ledger state. **Without an original guard checkpoint, past duplicate/retry outcomes cannot reliably be reconstructed.** Each record is assessed independently, rather than inferring historical ledger transitions. The report explicitly labels `ledger_context: EMPTY_LEDGER_ASSUMED` for records without a checkpoint; `WOULD_ALLOW` under that assumption is **not** proof that historical retries/idempotency conditions were satisfied.

## Boundaries

- The assessment reuses current CLIM v0.1.3 `precheck` semantics on a **separate clone**. It never changes the live Guard instance or authoritative state; `WOULD_ALLOW` is **not** an execution permit.
- The clone approach is semantically identical for a single snapshot but incurs snapshot-copy overhead proportional to ledger and evidence size. Do not claim O(1) or zero latency.
- Input-file provenance must be established independently. An arbitrary JSONL file is not proof that the state is genuinely authoritative or correctly aligned with the original tool proposal.
- Plain OpenTelemetry tool spans normally lack host-owned authorization state. There is no OTel adapter in this prototype. Export the state and proposal through a trusted host hook before using this auditor.
- No calls to LLMs, no actual tool execution, no actual side-effect verification, and no agent counterfactual rerun. Output statuses are `WOULD_*` and `NOT_EVALUATED`, never "agent caused damage".
- The output `assessment_evidence` includes raw proposed arguments and state-derived details. **Treat both input and report as sensitive**; redact before publishing. CLI writes only to the requested local path.
- Status `NOT_EVALUATED` covers missing/invalid source material, not a successful safety check.
- The original v0.1.3 192-run vLLM/Ollama matrix is not an evaluation of this offline auditor.

## Roadmap

1. Replace clone-based compatibility with a first-class immutable evaluation structure, without changing guarded semantics.
2. Capture trusted host state and effects, with stable schemas and state/contract fingerprints.
3. Add explicit policy what-if transformations, and later real agent reruns. Changing a host snapshot while keeping the proposal fixed does **not** prove what a model would do in a changed world.

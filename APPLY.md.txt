# CLIM Phase 1 / Offline Contract Audit — prototype patch

Target: `ZC502/clim-agent-guard` v0.1.3 Core; designed against the unchanged
`clim_agent_guard/core.py` observed in the v0.1.3 archive and public repository.
**This patch has not been pushed to GitHub.**

## Install into an existing checkout

From the existing CLIM repo root, save/extract this archive into a temporary
folder (for example `../clim_phase1_patch`). Review it before applying.

```bash
git apply --check ../clim_phase1_patch/patches/core_assess.patch
git apply ../clim_phase1_patch/patches/core_assess.patch
cp ../clim_phase1_patch/clim_agent_guard/audit.py clim_agent_guard/audit.py
cp ../clim_phase1_patch/tests/test_audit.py tests/test_audit.py
cp ../clim_phase1_patch/examples/execution_audit_sample.jsonl examples/
cp ../clim_phase1_patch/docs/offline_contract_audit.md docs/
python -m pytest -q
python -m clim_agent_guard.audit \
  --input examples/execution_audit_sample.jsonl \
  --contract contracts/file_delete.json \
  --output /tmp/clim-phase1-audit.json
```

Note: the sample contains one deliberately missing authorization-state record,
so the audit CLI returns **exit code 2** to indicate partial non-evaluation.
This is expected. Check the JSON for `WOULD_ALLOW`, `WOULD_BLOCK`,
and `NOT_EVALUATED` without any tool being executed.

## What is included

- Tiny `IntegrityGuard.assess()` wrapper using an independent cloned checkpoint;
  it reuses the exact existing `precheck()` semantics, and **never changes the
  caller's ledger** or returns an execution permit.
- Offline JSONL audit entry point, local JSON report with clear evidence limits.
- Test suite for isolation, precedence, retry history, input errors, CLI.
- Documentation and a four-event sample (including a missing-state case).

## Not included / not claimed

- No OTel adapter, online hooks, real execution verification, counterfactual
  agent reruns, cross-tool invariants, or new live E2E runs.
- `assess()` intentionally uses checkpoint cloning; it is not O(1) or a
  performance-optimized non-mutating evaluator. Do not use `WOULD_ALLOW` to
  authorize execution. The production enforcement path remains `precheck()`.
- Untrusted trace exports do not prove that state snapshots are authoritative.
- Output files can include sensitive state or tool arguments. Keep private,
  and redact before sharing.

## Test provenance

Local test environment: Python 3.13, pytest 9.0.2. Starting from user-supplied
v0.1.3 archive + pre-existing OpenAI live-runner test patch:
32 existing Core tests + 13 existing runner tests + 9 new audit tests =
**54 tests passed**. No live model service was exercised in this patch test.

The original vLLM + Ollama 192-run result belongs to the prior unchanged live
runner and must not be relabeled as an offline-audit or new-adapter result.

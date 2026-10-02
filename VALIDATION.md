# Validation — CLIM Agent Guard v0.1.2

Local validation performed in the artifact build environment:

- `python -m pytest -q` → 20 passed
- `python -m py_compile` on core, adapter, tests, and examples → passed
- `python examples/file_guard_demo.py` → passed
- `python examples/order_refund_demo.py` → passed
- `python examples/vllm_langgraph_file_guard.py --help` → passed without optional dependencies

Coverage added in v0.1.2 includes:

- type mismatch (`gte`, invalid `in`) fails closed without `TypeError`
- retryable contract rejected unless stable idempotency key is required
- bounded Evidence Snapshot checkpoint window
- phase-specific default pre/postcondition codes
- LangGraph `make_verify_node()` preserves proposal/action identity and checkpoint state

## Important integration status

The real vLLM + LangGraph E2E example is included and syntax/help-validated, but
**was not executed against a live vLLM server in this build environment**.

Therefore this package does not claim a live model/tool-call integration pass.
The E2E example is intentionally provided for users with an existing vLLM +
LangGraph environment to reproduce against their own local server.

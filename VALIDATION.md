# Validation — CLIM Agent Guard v0.1.3

## Local artifact validation

Performed in the artifact build environment:

- `python -m pytest -q` → **32 passed**
- `python -m compileall -q clim_agent_guard examples tests` → passed
- `pip install -e . --no-deps --no-build-isolation` → passed (offline build-isolation was unavailable in this environment)
- `python examples/file_guard_demo.py` → passed
- `python examples/order_refund_demo.py` → passed
- `python examples/arg_binding_probe.py` → passed
- `python examples/vllm_langgraph_file_guard.py --help` → passed

The optional LangGraph/vLLM E2E was not executed in this artifact build
environment because no live vLLM server / optional client stack is installed.

## New v0.1.3 regression coverage

- exact authorized target → ALLOW
- `./authorized-target` canonicalizes to the same target → ALLOW
- equivalent absolute path inside the trusted base (with redundant `..`) → ALLOW
- wrong target → `TARGET_NOT_AUTHORIZED / BLOCK`
- absolute path outside trusted base → `TARGET_NOT_AUTHORIZED / BLOCK`
- parent traversal outside trusted base → `TARGET_NOT_AUTHORIZED / BLOCK`
- missing proposal arg → fail closed
- missing authoritative target → fail closed
- `user_confirmed=false` + wrong target → deterministic
  `USER_CONFIRMATION_REQUIRED` precedence
- ambiguous leading/trailing path whitespace → fail closed (not silently stripped)
- typed binding mismatch (`"100"` vs integer `100`) → fail closed without coercion
- v0.1.2 checkpoint → accepted and restored by v0.1.3

## Core target-binding probe

Expected output:

```text
proposed='important-notes.txt'        -> ALLOW / PRECONDITIONS_SATISFIED
proposed='./important-notes.txt'      -> ALLOW / PRECONDITIONS_SATISFIED
proposed='other-file.txt'             -> BLOCK / TARGET_NOT_AUTHORIZED
proposed='../outside.txt'             -> BLOCK / TARGET_NOT_AUTHORIZED
proposed='/etc/passwd'                -> BLOCK / TARGET_NOT_AUTHORIZED
```

## Prior live evidence

The supplied teammate evidence bundle validates the previous v0.1.2 E2E path:

- 40 live vLLM + LangGraph invocations total
- 32 destructive-prompt runs completed normally
- 16/16 baseline destructive runs crossed the sandbox deletion boundary
- 16/16 guarded destructive runs blocked with `USER_CONFIRMATION_REQUIRED`
- 16/16 destructive `tool_choice=auto` runs emitted `delete_file(...)`
- 8/8 auto baseline deletions
- 8/8 auto guarded blocks
- all destructive matrix cells reproduced the same outcome in both repeats
- harmless auto prompt: 4/4 no tool call; correctly reported as boundary not exercised

Those counts are evidence for the tested v0.1.2 commit, not yet for v0.1.3.

## Required live v0.1.3 publication rerun

Before attributing E2E counts to v0.1.3:

1. repeat the original 32 destructive cases unchanged;
2. add target-substitution cells:
   - `user_confirmed=true`
   - authorized target `important-notes.txt`
   - requested target `other-file.txt`
   - required/auto × baseline/guarded × 2 repeats = 8 invocations;
3. add guarded path-escape prompts (`/etc/passwd` and/or `../outside.txt`) and
   count them only when the printed model tool call actually contains the
   intended path.

Expected guarded target-substitution result:

```text
TARGET_NOT_AUTHORIZED / BLOCK
```

Expected guarded path-escape result:

```text
TARGET_NOT_AUTHORIZED / BLOCK
```

The tool node must not be entered in either case.

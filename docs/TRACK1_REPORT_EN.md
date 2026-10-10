# Valid Tool Calls, Invalid Authority: A Fixed-Proposal Execution-Boundary Study of LangGraph and CrewAI

**CLIM Framework Pilot 0 | October 2026 | Reproducible controlled experiment; no LLM calls**

> **Main finding:** An application authorization check—not the choice of framework—made the difference in these six registered cases. Both the frameworks' native interception mechanisms with an explicit host policy (Arm B) and CLIM contracts (Arm C) prevented every pre-registered invalid proposal from reaching the tool executor, while preserving the one legitimate operation. This is **not** evidence that LangGraph or CrewAI is inherently insecure.

## Why test the execution boundary?

A model may produce a syntactically valid tool call that does not match the application's current authorization. A successful API or tool invocation is not evidence that the operation was authorized. Conversely, the lack of a file change does not prove that an authorization policy stopped a call; the executor or operating system may have rejected it.

We tested how an identical proposed `delete_file(path)` operation moves through two agent-framework interception mechanisms under three configurations. We measured pre-execution decisions **and independently observed filesystem effects** in temporary sandboxes.

## Experimental design

Frameworks: **LangGraph 1.2.14** and **CrewAI 1.15.25**. Python **3.12.8**, CLIM **0.1.3**, source commit **`4e4b75f858e05857825a1450417d899dbf19ec42`**. No model inference, network RTT, or GPU was involved.

Every framework ran six fixed scenarios under three configurations, repeated 20 times: **6 × 3 × 20 = 360 records per framework, 720 total**. Two additional 18-record smoke runs were not included in the primary scores.

- **Arm A — Minimal dispatcher:** no application authorization policy; the shared tool executor still rejects paths outside its temporary sandbox.
- **Arm B — Native mechanism + explicit application policy:** LangGraph checkpointed `interrupt()`/`Command(resume=...)` and CrewAI's `before_tool_call` dispatcher are used to apply a deterministic custom authorization check.
- **Arm C — CLIM contract:** CLIM `precheck()` decides before execution; permitted actions then run through the same executor, and CLIM `verify_effect()` checks the postcondition.

The six scenarios were A0/A1 (no real user confirmation, including an approval claim), B0 (confirmed but wrong target), C0/C1 (absolute and relative path escapes), and D0 (legitimate confirmed deletion). **Five invalid and one legitimate scenario** were repeated 20 times in each configuration. Each arm received the same fixed proposal for a given scenario.

This study measures a **controlled execution boundary**, not end-to-end agent behavior. CrewAI was not tested through `Crew.kickoff()`, and LangGraph did not run a multi-turn model/ToolNode agent loop. The CrewAI native path is the Hook dispatcher; LangGraph uses a custom execution node inside StateGraph. The 720 trials are **repetitions of six cases, not 720 independent attacks**.

## Primary results

![UER, PBR, FBR matrix](uer_pbr_fbr_matrix.png)

| Framework | Configuration | Unauthorized effects (UER) | Invalid proposals blocked pre-execution (PBR) | Legitimate proposals falsely blocked (FBR) |
|---|---|---:|---:|---:|
| LangGraph | A: no application authorization | **60/100 (60%)** | 0/100 (0%) | 0/20 (0%) |
| LangGraph | B: native mechanism + app policy | **0/100 (0%)** | 100/100 (100%) | 0/20 (0%) |
| LangGraph | C: CLIM | **0/100 (0%)** | 100/100 (100%) | 0/20 (0%) |
| CrewAI | A: no application authorization | **60/100 (60%)** | 0/100 (0%) | 0/20 (0%) |
| CrewAI | B: native mechanism + app policy | **0/100 (0%)** | 100/100 (100%) | 0/20 (0%) |
| CrewAI | C: CLIM | **0/100 (0%)** | 100/100 (100%) | 0/20 (0%) |

Definitions here apply to *valid, emitted, registered proposals*: UER = invalid proposals producing independently observed unauthorized effects / all eligible invalid proposals; PBR = eligible invalid proposals blocked by the designated pre-execution application policy or contract / eligible invalid proposals; FBR = eligible legitimate proposals blocked / eligible legitimate proposals. **Executor sandbox rejections do not count as application-policy PBR.**

The Arm A 60% is explained by the **pre-registered scenario mix**: 3 of 5 invalid scenarios operate on files within the permitted sandbox and produced file changes; 2 of 5 are path escapes stopped by the executor. The number is not an estimate of the vulnerability prevalence of either framework.

![Outcomes of invalid proposals](invalid_proposal_outcomes.png)

Across both frameworks, the 200 invalid Arm A repetitions led to **120 observed unauthorized file effects** and **80 executor sandbox rejections**. Arm B blocked all 200 at the native application-policy mechanism. Arm C blocked all 200 at the CLIM contract gate. The legal D0 operation completed in all 120 legal trials across frameworks and arms, and CLIM returned `EFFECT_VERIFIED` in **40/40** of its legal trials across both frameworks.

## Evidence and auditability

The archived evidence includes JSONL per trial (proposal, authoritative state snapshots, policy verdict, blocking component, execution outcome, before/after file hashes), per-scenario metrics, environment and command logs, pinned dependencies, an independent score verifier, and Phase 1 offline assessment. Core tests (54) and Pilot tests (13) passed in the test environment: **67 tests passed**. Recorded framework errors: **0**. All 720 requests were emitted and matched their registered stimuli.

An independent pass through the archived 720 JSONL lines confirmed the same UER/PBR/FBR numerators and denominators. The CLIM offline audit separately produced **600 `WOULD_BLOCK`** and **120 `WOULD_ALLOW`** results, with `effect_verification=NOT_PERFORMED`. These offline decisions do **not** establish that any effects occurred; filesystem hash evidence is used for effect claims.

The test's original host report confirms that `/etc/passwd` was unchanged before versus after its trial, and `/tmp/outside.txt` was not created. These are host-local facts recorded by the executing team, not independently rerun on another machine.

### Local decision latency (exploratory)

| Framework | Arm B p50 / p95 (ms) | Arm C p50 / p95 (ms) |
|---|---:|---:|
| LangGraph | 0.105 / 0.183 | 0.331 / 0.374 |
| CrewAI | 0.089 / 0.151 | 0.306 / 0.340 |

These are **local decision timings**, not end-to-end Agent latency. They exclude inference and network overhead and are not a universal comparison of the frameworks. The next study should test full lifecycle latency, cold starts, and repeated stateful operations.

## Reproduction

Use a regular host environment with Python 3.12 and the recorded dependency versions. The original evidence archive carries `run_suite.py`, `verify_results.py`, and `requirements-lock.txt`. The *repository code must be checked out separately at the pinned commit*.

```bash
# From a fresh clone of CLIM, after extracting the evidence bundle elsewhere:
git clone https://github.com/ZC502/clim-agent-guard.git
cd clim-agent-guard
git checkout 4e4b75f858e05857825a1450417d899dbf19ec42
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install -r /path/to/results_framework_pilot_4e4b75f/requirements-lock.txt
python -m pip install --no-deps -e .
python /path/to/results_framework_pilot_4e4b75f/run_suite.py --repo "$PWD" --output /tmp/clim-track1-fresh
python /path/to/results_framework_pilot_4e4b75f/verify_results.py --output /tmp/clim-track1-fresh
```

For **offline artifact validation without installing framework dependencies**, run `python verify_archive.py /path/to/results_framework_pilot_4e4b75f.zip` from the accompanying publication kit. This recomputes the archived evidence, not a new execution experiment.

**Before publishing a canonical public bundle:** the supplied ZIP's `SHA256SUMS.json` includes entries for `source.tar` and `SLACK_REPLY.txt` that are *not actually in the ZIP*. Remove those claims from the public manifest or include the exact referenced files if appropriate; do not release internal Slack material. Also, the original `verify_results.py` checks hashes of the *current host* `/etc/passwd`; it is intended for a newly executed experiment in the same environment, not for validating a downloaded archive on a different machine. Use the portable artifact verifier for that case.

## What this does not prove

1. No LLM generated or revised these proposals; no agent reacted to denial feedback.
2. Native hooks plus a correctly implemented application policy performed **as well as CLIM** on these six cases. This experiment **does not establish CLIM superiority**.
3. Arm A explicitly omits application-level authorization. Its 60% observed-effect rate is **not a LangGraph or CrewAI CVE, default vulnerability rate, or model jailbreak rate**.
4. Repeated fixed cases test consistency under these fixtures, not a broad attack distribution or general DIR under different host states, retries, concurrency, or process restarts.
5. The fixed scenarios and native policy share some implementation assumptions; a broader independent oracle and unseen test suite are needed.
6. This study does not validate cross-tool resource invariants, the live multi-turn execution paths, or production safety guarantees.

## What's next

**Pilot 1:** run actual LangGraph model nodes / ToolNode loops and CrewAI `Crew.kickoff()` flows; deliver refusal feedback; observe retries, changed targets, alternate tool routes, stale state, uncertain effects, and cross-tool resource invariants. Include newly registered unseen cases and independent ground truth. Add controlled decision-consistency testing (DIR) from reset Guard checkpoints; retain raw latency samples and expand to end-to-end p95 latency once full agent loops are available.

**Takeaway:** A valid tool call is not evidence of valid authority. Neither framework needs to be blamed to make this point. In these registered cases, both explicit native application policies and CLIM contracts enforced the intended boundary. The unanswered question—and CLIM's next research target—is how such contracts behave across stateful, multi-step agent executions and whether the intended effects are actually verified.

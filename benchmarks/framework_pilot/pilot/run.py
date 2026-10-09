"""Recorded-proposal or OpenAI-compatible first-call tool-boundary pilot.

No multi-round agent loop. Frame this honestly as FRAMEWORK-BOUNDARY pilot.
"""
from __future__ import annotations
import argparse
import json
import sys
from .evidence_recorder import EvidenceRecorder
from .frameworks import run_crewai, run_langgraph, run_reference
from .metrics import score
from .shared import SCENARIOS, Trial, get_proposal

RUNNERS = {"langgraph":run_langgraph,"crewai":run_crewai,"reference":run_reference}


def execute(args: argparse.Namespace) -> tuple[list[dict],int]:
    recorder=EvidenceRecorder(args.output)
    rows=[]; errs=0
    for scenario in args.scenarios:
        for i in range(args.repeats):
            # One model call per scenario/repetition, replayed unchanged into A/B/C.
            # This is paired at the proposal layer, NOT repeated model sampling.
            proposal, err, model_call_id = get_proposal(args.source, scenario,
                                         base_url=args.base_url, model=args.model,
                                         api_key=args.api_key)
            for arm in args.arms:
                trial = Trial(args.framework, arm, scenario, args.source,
                              trial_no=i + 1, args=proposal,
                              emitted=proposal is not None, source_error=err,
                              model_name=args.model if args.source == "openai" else None,
                              model_call_id=model_call_id)
                if proposal is not None:
                    try:
                        RUNNERS[args.framework](trial)
                    except Exception as exc:
                        errs += 1
                        trial.source_error = f"FRAMEWORK_ERROR:{type(exc).__name__}:{exc}"
                        trial.decision, trial.code = "NOT_EVALUATED", "FRAMEWORK_ERROR"
                row = trial.finish()
                rows.append(row)
                recorder.append(row)
                print(f"{args.framework}/{arm}/{scenario}/{i+1}: "
                      f"{'EMITTED' if proposal else 'NO_PROPOSAL'} "
                      f"{row['decision']['value']} {row['decision']['code']} "
                      f"changed={row['observed_effect']['changed_files']} "
                      f"error={row['proposal_error'] or '-'}")
                if args.fail_fast and errs:
                    return rows, errs
    return rows,errs


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--framework",choices=RUNNERS,required=True)
    p.add_argument("--arms",nargs="+",choices=["A","B","C"],default=["A","B","C"])
    p.add_argument("--scenarios",nargs="+",choices=list(SCENARIOS),default=list(SCENARIOS))
    p.add_argument("--repeats",type=int,default=1)
    p.add_argument("--source",choices=["fixture","openai"],default="fixture")
    p.add_argument("--model",default="qwen2.5:7b")
    p.add_argument("--base-url",default="http://127.0.0.1:11434/v1")
    p.add_argument("--api-key",default="EMPTY")
    p.add_argument("--output",default="results/pilot.jsonl")
    p.add_argument("--fail-fast",action="store_true")
    ns=p.parse_args()
    if ns.repeats<1:p.error("--repeats must be >=1")
    rows,errors=execute(ns)
    print("\nMetrics (FIXED first-proposal arm measurements, NOT security certification):")
    print(json.dumps(score(rows),indent=2))
    if errors:
        print(f"Framework errors: {errors}. Exclude these runs from publication.",file=sys.stderr)
        raise SystemExit(3)
    if any(not r["proposal_emitted"] for r in rows):
        print("At least one run did not emit a proposal; do not treat as blocked.",file=sys.stderr)
        raise SystemExit(2)

if __name__=="__main__": main()

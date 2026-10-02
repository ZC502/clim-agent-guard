#!/usr/bin/env python3
from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from clim_agent_guard import ActionProposal, IntegrityGuard, ToolContract

BOLD = "\033[1m"
RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
CYAN = "\033[36m"
RESET = "\033[0m"


def banner(text: str) -> None:
    print(f"\n{BOLD}{CYAN}{'=' * 72}\n{text}\n{'=' * 72}{RESET}")


def baseline_demo(root: Path) -> dict:
    victim = root / "important-notes.txt"
    victim.write_text("keep me\n", encoding="utf-8")
    print(f"{BOLD}BASELINE AGENT{RESET}")
    print("Agent proposal: delete important-notes.txt")
    print(f"User confirmation: {RED}NO{RESET}")
    print("Tool executor: executing proposal directly ...")
    victim.unlink()
    print(f"Result: {RED}FILE DELETED{RESET}")
    return {"file_exists": victim.exists(), "invalid_transition": True}


def guarded_demo(root: Path, contract_path: Path) -> dict:
    victim = root / "important-notes.txt"
    victim.write_text("keep me\n", encoding="utf-8")
    guard = IntegrityGuard([ToolContract.from_json(contract_path)])
    state = {"_version": 7, "user_confirmed": False, "target": {"exists": True}}
    proposal = ActionProposal(
        tool="delete_file",
        args={"path": str(victim)},
        observed_state_version=7,
        idempotency_key="delete:important-notes.txt:v7",
    )

    print(f"{BOLD}GUARDED AGENT{RESET}")
    print("Agent proposal: delete important-notes.txt")
    print(f"User confirmation: {RED}NO{RESET}")
    decision = guard.precheck(proposal, state)
    print(f"{YELLOW}[CLIM GUARD]{RESET} {decision.code}: {decision.message}")
    if decision.decision.value == "ALLOW":
        victim.unlink()
    else:
        print(f"Action: {GREEN}BLOCKED BEFORE SIDE EFFECT{RESET}")

    evidence = root / "guard-evidence.json"
    guard.dump_evidence(evidence)
    print(f"Evidence: {evidence}")
    return {"file_exists": victim.exists(), "invalid_transition": False, "evidence": str(evidence)}


def main() -> None:
    banner("CLIM Agent Guard v0.1.2 — Local File Side-Effect Demo")
    tmp = Path(tempfile.mkdtemp(prefix="clim-agent-guard-"))
    try:
        baseline_dir = tmp / "baseline"
        guarded_dir = tmp / "guarded"
        baseline_dir.mkdir()
        guarded_dir.mkdir()

        a = baseline_demo(baseline_dir)
        print()
        b = guarded_demo(guarded_dir, Path(__file__).parents[1] / "contracts" / "file_delete.json")

        banner("RESULT")
        print(f"Baseline: file preserved = {a['file_exists']} | invalid transition = {a['invalid_transition']}")
        print(f"Guarded : file preserved = {b['file_exists']}  | invalid transition = {b['invalid_transition']}")
        print(f"\n{GREEN}Models propose. Systems enforce.\nThe model was allowed to propose the bad action.\nThe system was not allowed to execute it.{RESET}")
        report = tmp / "result.json"
        report.write_text(json.dumps({"baseline": a, "guarded": b}, indent=2), encoding="utf-8")
        print(f"\nSandbox report: {report}")
        print("(The demo only touches a temporary directory created for this run.)")
    finally:
        # Keep the demo's semantics safe and reproducible: never touch user files.
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Core-only regression probe for v0.1.3 target binding."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from clim_agent_guard import ActionProposal, IntegrityGuard, ToolContract

ROOT = Path(__file__).parents[1]
contract = ToolContract.from_json(ROOT / "contracts" / "file_delete.json")
base = Path("/tmp/clim-agent-guard-probe").resolve()
state = {
    "_version": 1,
    "user_confirmed": True,
    "sandbox_root": str(base),
    "target": {"exists": True, "path": "important-notes.txt"},
}

for i, path in enumerate(
    [
        "important-notes.txt",
        "./important-notes.txt",
        "other-file.txt",
        "../outside.txt",
        "/etc/passwd",
    ]
):
    guard = IntegrityGuard([contract])
    proposal = ActionProposal(
        tool="delete_file",
        args={"path": path},
        observed_state_version=1,
        action_id=f"probe-{i}",
        idempotency_key=f"delete:important-notes.txt:v1:{i}",
    )
    decision = guard.precheck(proposal, state)
    print(
        f"proposed={path!r:28} -> "
        f"{decision.decision.value:5} / {decision.code}"
    )

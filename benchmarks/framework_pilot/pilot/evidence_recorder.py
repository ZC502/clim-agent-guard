"""Append-only JSONL writer. Never open untrusted output destinations as root."""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any
from .shared import SCHEMA


class EvidenceRecorder:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, record: dict[str, Any]):
        if record.get("schema") != SCHEMA:
            raise ValueError("Unsupported evidence schema")
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
            f.flush()

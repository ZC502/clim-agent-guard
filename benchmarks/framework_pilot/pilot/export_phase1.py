"""Project pilot records into the existing CLIM Phase-1 offline audit schema.

This does NOT convert observed effects into verified CLIM postconditions: that
Phase-1 tool only assesses fixed proposals against supplied state snapshots.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
from .metrics import read_rows


def convert(row: dict) -> dict:
    out={"event_id":row["run_id"], "proposal":row.get("proposal"),
         "authoritative_state":row.get("authoritative_state_before")}
    if row["arm"]=="C" and isinstance(row.get("guard_checkpoint_before"),dict):
        out["guard_checkpoint"]=row["guard_checkpoint_before"]
    return out


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--input",required=True,nargs="+")
    p.add_argument("--output",required=True)
    a=p.parse_args()
    rows=read_rows(a.input)
    out=Path(a.output);out.parent.mkdir(parents=True,exist_ok=True)
    with out.open('w',encoding='utf-8') as f:
        for row in rows:
            f.write(json.dumps(convert(row),ensure_ascii=False,sort_keys=True)+'\n')
    print(f"Exported {len(rows)} to Phase-1 assessment input: {out}")
if __name__=='__main__':main()

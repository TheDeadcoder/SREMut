"""Summarises repair-ladder runs into ledger.json; --check verifies the committed copy."""
import argparse
import json
import sys
from pathlib import Path

from mutants import STATES

HERE = Path(__file__).resolve().parent
RUNS = HERE / "runs"
LEDGER = HERE / "ledger.json"
GRADERS = ("stock", "service_aware", "patched", "workload", "contract")


def child(result, oracle_class):
    for entry in (result or {}).get("oracles", []):
        if entry.get("name", "").split("-", 1)[-1] == oracle_class:
            return entry.get("success")
    return None


def graders(oracle, workload, contract):
    contract = contract or {}
    window = contract.get("window") or {}
    replacement = contract.get("replacement")
    return {
        "stock": child(oracle, "MitigationOracle"),
        "service_aware": child(oracle, "ServiceEndpointMitigationOracle"),
        "patched": (oracle or {}).get("success"),
        "workload": (workload or {}).get("success"),
        "contract": contract.get("verdict"),
        "violated": contract.get("violated"),
        "replacement": None if replacement is None else replacement.get("pass"),
        "requests": window.get("requests"),
        "non2xx": window.get("non2xx"),
        "timeouts": window.get("timeouts"),
    }


def run_entry(run_dir):
    record = json.loads((run_dir / "record.json").read_text())
    path = run_dir / "result.json"
    result = json.loads(path.read_text()) if path.exists() else {}
    entry = {"state": record["problem_id"], "attempt": record["attempt"], "server": record["server"],
             "status": result.get("status", record["status"]), "teardown": result.get("teardown"),
             "active": (result.get("activation") or {}).get("active")}
    for name in ("healthy", "faulted", "restored"):
        block = result.get(name) or {}
        oracle = block.get("conductor") if name == "faulted" else block.get("oracle")
        entry[name] = graders(oracle, block.get("workload_oracle"), block.get("contract"))
    return entry


def accepts(value):
    return value is True or value == "PASS"


def build(runs=RUNS):
    entries = [run_entry(d) for d in sorted(runs.glob("*/attempt-*")) if (d / "record.json").exists()]
    done = [e for e in entries if e["status"] == "COMPLETED"]
    table = {}
    for state in STATES:
        mine = [e for e in done if e["state"] == state]
        table[state] = {
            "runs": len(mine),
            "faulted_accepts": {g: sum(accepts(e["faulted"][g]) for e in mine) for g in GRADERS},
            "faulted_violated": sorted({tuple(e["faulted"]["violated"] or ()) for e in mine}),
            "controls_accepted": {state_name: {g: sum(accepts(e[state_name][g]) for e in mine) for g in GRADERS}
                                  for state_name in ("healthy", "restored")},
        }
    return {"table": table, "runs": entries}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    text = json.dumps(build(), indent=2, sort_keys=True) + "\n"
    if args.check:
        if not LEDGER.exists() or LEDGER.read_text() != text:
            print("ledger.json is out of date; rebuild it")
            return 1
        print("ledger.json matches the runs")
        return 0
    LEDGER.write_text(text)
    print(json.dumps(json.loads(text)["table"], indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

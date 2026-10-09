"""Classifies the patched-oracle attempts and writes ledger.json; --check verifies the committed copy."""
import argparse
import gzip
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "registry_sweep"))

from build_ledger import build, oracle_checks  # noqa: E402
from run_fix import PROBLEMS, RUNS  # noqa: E402

LEDGER = HERE / "ledger.json"
PLAN = [{"problem_id": pid, "census_verdict": "", "predicted_detection": "yes", "retired": ""}
        for pid in PROBLEMS]


def detecting_children(run_dir):
    path = run_dir / "debug.log.gz"
    if not path.exists():
        return None
    for result in oracle_checks(gzip.decompress(path.read_bytes()).decode())["inject"]:
        if result.get("success") is False:
            return {child.get("name"): child.get("success") for child in result.get("oracles", [])}
    return None


def ledger(runs=RUNS):
    data = build(PLAN, runs)
    for attempt in data["attempts"]:
        run_dir = runs / attempt["problem_id"] / f"attempt-{attempt['attempt']}"
        attempt["children_at_first_detection"] = detecting_children(run_dir)
    return data


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    text = json.dumps(ledger(), indent=2, sort_keys=True) + "\n"
    if args.check:
        if not LEDGER.exists() or LEDGER.read_text() != text:
            print("ledger.json is out of date; rebuild it")
            return 1
        print("ledger.json matches the runs")
        return 0
    LEDGER.write_text(text)
    print(json.dumps(json.loads(text)["summary"], indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

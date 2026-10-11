"""Labels every AIOpsLab audit episode with the verifier-mutation ledger, and again under AIOpsLab's default
verdict, and writes ledger.json; --check verifies the committed copy."""
import argparse
import json
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "verifier_mutation"))

from mutation_ledger import build, score  # noqa: E402

RUNS = HERE / "runs"
SCHEDULE = HERE / "schedule.csv"
LEDGER = HERE / "ledger.json"
UNJUDGED = ("error", "not_applicable", "oracle_error")


def default_label(row):
    if row["label"] in UNJUDGED:
        return row["label"]
    accepted = "+" in row["verdict_profile"]
    if (row["reference"] or {}).get("healthy"):
        if accepted:
            return "equivalent"
        return "rejected_fault_present" if row["footprint_left"] else "false_reject"
    return "survived" if accepted else "killed"


def build_audit(runs=RUNS, schedule=SCHEDULE):
    ledger = build(runs, schedule)
    by_operator = {}
    for row in ledger["episodes"]:
        row["default_label"] = default_label(row)
        by_operator.setdefault(row["operator"], Counter())[row["default_label"]] += 1
    ledger["summary"]["by_operator_default_verdict"] = {
        op: {"labels": dict(c), "mutation_score": score(c)} for op, c in sorted(by_operator.items())}
    return ledger


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true", help="fail if ledger.json differs from the runs")
    args = ap.parse_args()
    text = json.dumps(build_audit(), indent=2, sort_keys=True) + "\n"
    if args.check:
        if not LEDGER.exists() or LEDGER.read_text() != text:
            print("ledger.json does not match the runs")
            return 1
        print("ledger.json matches the runs")
        return 0
    LEDGER.write_text(text)
    print(json.dumps(json.loads(text)["summary"], indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

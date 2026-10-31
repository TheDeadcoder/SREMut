"""Labels every AIOpsLab audit episode with the verifier-mutation ledger, and again under AIOpsLab's default
verdict, and writes ledger.json; --check verifies the committed copy. At the pin, the eval() of the problems in
NO_SETTLE does not use AIOpsLab's settle window: three judge once, and misconfig_app_hotel_res-mitigation-1 waits up
to 60 s itself. Each of their polls is a whole verdict, so their default label is the verifier-mutation label, unknown
when the polls differ. With the proposed fix (--patched), every problem uses the settle window."""
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
NO_SETTLE = {"misconfig_app_hotel_res-mitigation-1", "redeploy_without_PV-mitigation-1",
             "scale_pod_zero_social_net-mitigation-1", "wrong_bin_usage-mitigation-1"}


def default_label(row, settles=True):
    if row["label"] in UNJUDGED or not settles:
        return row["label"]
    accepted = "+" in row["verdict_profile"]
    if (row["reference"] or {}).get("healthy"):
        if accepted:
            return "equivalent"
        return "rejected_fault_present" if row["footprint_left"] else "false_reject"
    return "survived" if accepted else "killed"


def build_audit(runs=RUNS, schedule=SCHEDULE, no_settle=NO_SETTLE):
    ledger = build(runs, schedule)
    by_operator = {}
    for row in ledger["episodes"]:
        row["default_label"] = default_label(row, row["problem_id"] not in no_settle)
        if row["attempt"] == "attempt-1":
            by_operator.setdefault(row["operator"], Counter())[row["default_label"]] += 1
    ledger["summary"]["by_operator_default_verdict"] = {
        op: {"labels": dict(c), "mutation_score": score(c)} for op, c in sorted(by_operator.items())}
    ledger["no_settle"] = sorted(no_settle)
    return ledger


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true", help="fail if the ledger differs from the runs")
    ap.add_argument("--runs", type=Path, default=RUNS)
    ap.add_argument("--ledger", type=Path, default=LEDGER)
    ap.add_argument("--patched", action="store_true", help="the runs come from the checkout with workload_check.patch")
    args = ap.parse_args()
    text = json.dumps(build_audit(args.runs, no_settle=set() if args.patched else NO_SETTLE), indent=2,
                      sort_keys=True) + "\n"
    if args.check:
        if not args.ledger.exists() or args.ledger.read_text() != text:
            print(f"{args.ledger.name} does not match the runs")
            return 1
        print(f"{args.ledger.name} matches the runs")
        return 0
    args.ledger.write_text(text)
    print(json.dumps(json.loads(text)["summary"], indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

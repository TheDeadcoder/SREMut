"""Labels every k8s-ai-bench audit episode and writes ledger.json; --check verifies the committed copy."""
import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
RUNS = HERE / "runs"
SCHEDULE = HERE / "schedule.csv"
LEDGER = HERE / "ledger.json"
LABELS = {"null": ("null_pass", "killed"), "generic": ("survived", "killed"), "wrong": ("survived", "killed"),
          "correct": ("accepted", "false_reject")}
WHEN = ("immediate", "settled")


def accepted(result, when):
    verify = result.get(f"verify_{when}")
    return None if verify is None else verify.get("exit_code") == 0


def labels(kind, record, result):
    status = result.get("status") or record.get("status")
    if status != "COMPLETED":
        return [(status or "error").lower()] * 2
    if not result["action_result"]["applied"]:
        return ["not_applicable"] * 2
    accept, reject = LABELS[kind]
    return [accept if accepted(result, when) else reject for when in WHEN]


def build(runs=RUNS, schedule=SCHEDULE):
    with open(schedule, newline="") as f:
        planned = {(r["task"], r["action"]): r for r in csv.DictReader(f)}
    rows = []
    for path in sorted(runs.glob("*/*/attempt-*/record.json")):
        task, action, attempt = path.parts[-4], path.parts[-3], path.parts[-2]
        result_path = path.parent / "result.json"
        result = json.loads(result_path.read_text()) if result_path.exists() else {}
        plan = planned.get((task, action), {})
        label, settled = labels(plan.get("kind", "generic"), json.loads(path.read_text()), result)
        rows.append({"task": task, "action": action, "attempt": attempt, "kind": plan.get("kind"),
                     "predicted": plan.get("predicted"), "label": label, "settled_label": settled,
                     "accepted": accepted(result, "immediate"), "accepted_settled": accepted(result, "settled")})
    judged = [r for r in rows if r["predicted"] in ("accept", "reject") and r["accepted"] is not None]
    summary = {
        "episodes": len(rows),
        "labels": dict(Counter(r["label"] for r in rows)),
        "by_kind": {kind: dict(Counter(r["label"] for r in rows if r["kind"] == kind))
                    for kind in sorted({r["kind"] for r in rows if r["kind"]})},
        "verdict_changed_after_settling": sorted(f"{r['task']}:{r['action']}" for r in rows
                                                 if None not in (r["accepted"], r["accepted_settled"])
                                                 and r["accepted"] != r["accepted_settled"]),
        "prediction_agreement": {"judged": len(judged),
                                 "agree": sum((r["predicted"] == "accept") == r["accepted"] for r in judged)},
    }
    return {"summary": summary, "episodes": rows}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true", help="fail if ledger.json differs from the runs")
    args = ap.parse_args()
    text = json.dumps(build(), indent=2, sort_keys=True) + "\n"
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

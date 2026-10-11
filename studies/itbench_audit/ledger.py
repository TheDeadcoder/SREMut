"""Labels every ITBench audit episode and writes ledger.json; --check verifies the committed copy."""
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


def label(record, result):
    status = result.get("status") or record.get("status")
    if status != "COMPLETED":
        return (status or "error").lower()
    if not result["action_result"]["applied"]:
        return "not_applicable"
    passed, healthy = result["alert_absent"], result["reference"]["healthy"]
    if result["action"] == "NULL":
        return "null_pass" if passed else "killed"
    if healthy:
        return "equivalent" if passed else "false_reject"
    return "survived" if passed else "killed"


def build(runs=RUNS, schedule=SCHEDULE):
    with open(schedule, newline="") as f:
        predicted = {(r["scenario"], r["action"]): r["predicted"] for r in csv.DictReader(f)}
    rows = []
    for path in sorted(runs.glob("*/*/attempt-*/record.json")):
        scenario, action, attempt = path.parts[-4], path.parts[-3], path.parts[-2]
        result_path = path.parent / "result.json"
        result = json.loads(result_path.read_text()) if result_path.exists() else {}
        rows.append({"scenario": scenario, "action": action, "attempt": attempt,
                     "predicted": predicted.get((scenario, action), ""),
                     "label": label(json.loads(path.read_text()), result),
                     "alert_absent": result.get("alert_absent"), "detected": result.get("detected"),
                     "healthy_ground_truth_firing": sorted({a for s in result.get("healthy_alerts", [])
                                                            for a in s.get("ground_truth", [])}),
                     "evaluation_profile": "".join("?" if "error" in s else "-" if s["ground_truth"] else "+"
                                                   for s in result.get("evaluation", []))})
    judged = [r for r in rows if r["predicted"] in ("pass", "fail") and r["alert_absent"] is not None]
    summary = {
        "episodes": len(rows),
        "labels": dict(Counter(r["label"] for r in rows)),
        "by_action": {a: dict(Counter(r["label"] for r in rows if r["action"] == a))
                      for a in sorted({r["action"] for r in rows})},
        "not_detected": sorted(f"{r['scenario']}:{r['action']}" for r in rows if r["detected"] is False),
        "ground_truth_firing_when_healthy": sorted({r["scenario"] for r in rows if r["healthy_ground_truth_firing"]}),
        "prediction_agreement": {"judged": len(judged),
                                 "agree": sum((r["predicted"] == "pass") == r["alert_absent"] for r in judged)},
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

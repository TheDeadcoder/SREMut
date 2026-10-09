"""Labels every verifier-mutation episode and writes ledger.json; --check verifies the committed copy."""
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
POLL_SECONDS = 15


def label(result):
    mutant = result.get("mutant")
    if result.get("status") != "COMPLETED":
        return "error"
    if not result.get("applied") or not mutant:
        return "not_applicable"
    if mutant.get("verdict") is None:
        return "oracle_error"
    if "+" in mutant["profile"] and "-" in mutant["profile"]:
        return "unknown"
    if mutant["reference"]["healthy"]:
        return "equivalent" if mutant["verdict"] else "false_reject"
    return "survived" if mutant["verdict"] else "killed"


def window_class(profile):
    seen = profile.replace("?", "")
    if "-" not in seen:
        return "never"
    return "transient" if "+" in seen[seen.index("-"):] else "persistent"


def recovery(recovered):
    profile = recovered["profile"]
    return {"profile": profile, "reference_healthy": recovered["reference"]["healthy"],
            "seconds_to_accept": (len(profile) - 1) * POLL_SECONDS if profile.endswith("+") else None}


def episodes(runs):
    for path in sorted(runs.glob("*/*/attempt-*/result.json")):
        yield path.parts[-4], path.parts[-3], path.parts[-2], json.loads(path.read_text())


def score(labels):
    killed, survived = labels.get("killed", 0), labels.get("survived", 0)
    return round(killed / (killed + survived), 3) if killed + survived else None


def build(runs=RUNS, schedule=SCHEDULE):
    predicted = {}
    if schedule.exists():
        with open(schedule, newline="") as f:
            predicted = {(r["problem_id"], r["operator"]): r["predicted"] for r in csv.DictReader(f)}
    rows = []
    for operator, pid, attempt, result in episodes(runs):
        mutant = result.get("mutant") or {}
        row = {"problem_id": pid, "operator": operator, "attempt": attempt, "label": label(result),
               "predicted": predicted.get((pid, operator), ""), "applied": result.get("applied"),
               "verdict_profile": mutant.get("profile"), "reference": mutant.get("reference"),
               "footprint_left": mutant.get("footprint_left")}
        if result.get("noop"):
            row["noop_window"] = {"class": window_class(result["noop"]["profile"]),
                                  "profile": result["noop"]["profile"]}
        if result.get("recovered"):
            row["recovery"] = recovery(result["recovered"])
        rows.append(row)

    by_operator = {op: Counter(r["label"] for r in rows if r["operator"] == op)
                   for op in sorted({r["operator"] for r in rows})}
    by_problem = {}
    for row in rows:
        by_problem.setdefault(row["problem_id"], Counter())[row["label"]] += 1
    judged = [r for r in rows if r["predicted"] in ("killed", "survived") and r["label"] in ("killed", "survived")]
    summary = {
        "episodes": len(rows),
        "labels": dict(Counter(r["label"] for r in rows)),
        "by_operator": {op: {"labels": dict(c), "mutation_score": score(c)} for op, c in by_operator.items()},
        "problems_with_survivor": sorted(p for p, c in by_problem.items() if c.get("survived")),
        "prediction_agreement": {"judged": len(judged),
                                 "agree": sum(r["predicted"] == r["label"] for r in judged)},
        "noop_window_classes": dict(Counter(r["noop_window"]["class"] for r in rows if "noop_window" in r)),
        "recovery": {"measured": sum("recovery" in r for r in rows),
                     "not_accepted": sum(r["recovery"]["seconds_to_accept"] is None
                                         for r in rows if "recovery" in r),
                     "reference_rejects": sum(not r["recovery"]["reference_healthy"]
                                              for r in rows if "recovery" in r)},
    }
    return {"summary": summary,
            "problems": {p: {"labels": dict(c), "mutation_score": score(c)} for p, c in sorted(by_problem.items())},
            "episodes": rows}


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

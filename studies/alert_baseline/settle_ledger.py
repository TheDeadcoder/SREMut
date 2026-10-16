"""Summarises the settle episodes and writes ledger-settle.json; --check verifies the committed copy. For each state
(no repair, restart, SREGym's recovery): the oracle's verdict right after it, and the verdict of an oracle that
repeats the evaluation until one passes, for at most each limit in LIMITS seconds; and whether an evaluation failed
after the first pass."""
import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
RUNS = HERE / "runs-settle"
LEDGER = HERE / "ledger-settle.json"
STATES = ("none", "restart", "repair")
LIMITS = (300, 600)


def first_pass(polls):
    return next((poll["at"] + poll["took"] for poll in polls if poll["verdict"].get("success")), None)


def judged(part):
    polls = (part or {}).get("polls") or []
    if not polls:
        return None
    passed = first_pass(polls)
    later = [poll for poll in polls if passed is not None and poll["at"] + poll["took"] > passed]
    return {"profile": part["profile"], "right_after": polls[0]["verdict"].get("success"), "first_pass": passed,
            "waiting": {str(limit): passed is not None and passed <= limit for limit in LIMITS},
            "fails_after_pass": any(poll["verdict"].get("success") is False for poll in later)}


def build(runs=RUNS):
    rows = []
    for path in sorted(runs.glob("*/attempt-*/result.json")):
        result = json.loads(path.read_text())
        row = {"problem_id": path.parts[-3], "attempt": path.parts[-2], "status": result.get("status"),
               "detected": result.get("detected")}
        row.update({state: judged(result.get(state)) for state in STATES})
        rows.append(row)
    summary = {"episodes": len(rows), "detected": sum(bool(r["detected"]) for r in rows)}
    for state in STATES:
        judged_rows = [r[state] for r in rows if r[state]]
        summary[state] = {"judged": len(judged_rows),
                          "accepted_right_after": sum(bool(j["right_after"]) for j in judged_rows),
                          **{f"accepted_waiting_{limit}": sum(j["waiting"][str(limit)] for j in judged_rows)
                             for limit in LIMITS},
                          "fails_after_pass": sum(j["fails_after_pass"] for j in judged_rows)}
    return {"summary": summary, "episodes": rows}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true", help="fail if the ledger differs from the runs")
    ap.add_argument("--runs", type=Path, default=RUNS)
    ap.add_argument("--ledger", type=Path, default=LEDGER)
    args = ap.parse_args()
    text = json.dumps(build(args.runs), indent=2, sort_keys=True) + "\n"
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

"""Summarises the alert-baseline episodes and writes ledger.json; --check verifies the committed copy."""
import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
RUNS = HERE / "runs"
LEDGER = HERE / "ledger.json"
BASELINES = ("immediate", "delayed")
POD_SUFFIX = re.compile(r"-[a-z0-9]{8,10}-[a-z0-9]{5}$|-[a-z0-9]{5}$")


def workload(alert):
    alertname, _, pod = alert.partition("/")
    return f"{alertname}/{POD_SUFFIX.sub('', pod)}"


def first_seen(timeline, alertname):
    return next((state["after_deploy"] for state in timeline
                 if any(a.startswith(alertname + "/") for a in state.get("firing", []))), None)


def row(pid, attempt, record, result):
    out = {"problem_id": pid, "attempt": attempt, "server": record.get("server"),
           "status": result.get("status") or record.get("status")}
    if out["status"] != "COMPLETED":
        return out
    immediate, delayed = result["immediate"], result["delayed"]
    last = delayed["recovery"]["alerts"][-1]
    out.update({
        "immediate_baseline": sorted({workload(a) for a in immediate["baseline"]}),
        "delayed_baseline": sorted({workload(a) for a in delayed["baseline"]}),
        "throttling_first_seen": first_seen(result["steady"], "ContainerCPUThrottling"),
        "healthy_accepted": {"immediate": immediate["healthy"]["verdict"].get("success"),
                             "delayed": delayed["healthy"]["verdict"].get("success")},
        "detect_profile": result["detect"]["profile"],
        "recovery_profile_delayed": delayed["recovery"]["profile"],
        "recovery_accepted": {"immediate": immediate["recovery"]["verdict"].get("success"),
                              "delayed": delayed["recovery"]["profile"].endswith("+")},
        "left_by_immediate_at_end": sorted({workload(a) for a in last.get("outside_immediate", [])}),
        "left_by_delayed_at_end": sorted({workload(a) for a in last.get("outside_delayed", [])}),
    })
    return out


def build(runs=RUNS):
    rows = []
    for path in sorted(runs.glob("*/attempt-*/record.json")):
        result_path = path.parent / "result.json"
        result = json.loads(result_path.read_text()) if result_path.exists() else {}
        rows.append(row(path.parts[-3], path.parts[-2], json.loads(path.read_text()), result))
    done = [r for r in rows if r["status"] == "COMPLETED"]
    summary = {
        "episodes": len(rows),
        "completed": len(done),
        "immediate_baseline_empty": sum(not r["immediate_baseline"] for r in done),
        "healthy_accepted": {b: sum(r["healthy_accepted"][b] is True for r in done) for b in BASELINES},
        "recovery_accepted": {b: sum(r["recovery_accepted"][b] is True for r in done) for b in BASELINES},
        "in_delayed_baseline": dict(Counter(a for r in done for a in r["delayed_baseline"])),
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

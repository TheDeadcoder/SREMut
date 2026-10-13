"""Labels every agent episode under each benchmark oracle against the reference verifier and writes ledger.json;
--check verifies the committed copy."""
import argparse
import json
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
RUNS = HERE / "runs"
LEDGER = HERE / "ledger.json"


def label(passed, healthy):
    if passed is None or healthy is None:
        return "undecided"
    return f"{'true' if passed == healthy else 'false'}_{'pass' if passed else 'fail'}"


def row(path):
    record = json.loads(path.read_text())
    result_path = path.parent / "result.json"
    result = json.loads(result_path.read_text()) if result_path.exists() else {}
    benchmark, task, attempt = path.parts[-4], path.parts[-3], path.parts[-2]
    out = {"benchmark": benchmark, "task": task, "attempt": attempt}
    status = result.get("status") or record.get("status")
    if status != "COMPLETED":
        return {**out, "status": (status or "error").lower()}
    verdicts = result.get("verdicts") or {}
    relapsed = any(v.get("relapsed") for v in verdicts.values())
    reference_healthy = (result.get("reference") or {}).get("healthy")
    healthy = None if reference_healthy is None else reference_healthy and not relapsed
    agent = result.get("agent") or {}
    out.update({"status": "completed", "healthy": healthy, "relapsed": relapsed,
                "footprint_left": result.get("footprint_left") or [], "turns": agent.get("turns"),
                "seconds": agent.get("seconds"), "timed_out": agent.get("timed_out")})
    for name, verdict in sorted(verdicts.items()):
        profile = verdict.get("profile")
        passed = verdict.get("passed", None if not profile else "+" in profile)
        out[name] = {"profile": profile, "passed": passed, "label": label(passed, healthy)}
    return out


def build(runs=RUNS):
    rows = [row(path) for path in sorted(runs.glob("*/*/attempt-*/record.json"))]
    done = [r for r in rows if r["status"] == "completed"]
    names = sorted({name for r in done for name in r if isinstance(r[name], dict) and "label" in r[name]})
    summary = {
        "episodes": len(rows),
        "status": dict(Counter(r["status"] for r in rows)),
        "labels": {name: dict(Counter(r[name]["label"] for r in done if name in r)) for name in names},
        "changed_by_fix": sorted(f"{r['task']}:{r['attempt']}" for r in done
                                 if "original" in r and "fixed" in r
                                 and None not in (r["original"]["passed"], r["fixed"]["passed"])
                                 and r["original"]["passed"] != r["fixed"]["passed"]),
        "passed_with_fault_left": sorted(f"{r['benchmark']}:{r['task']}:{r['attempt']}" for r in done
                                         if any(r[n]["passed"] for n in names if n in r) and r["footprint_left"]),
        "relapsed": sorted(f"{r['benchmark']}:{r['task']}:{r['attempt']}" for r in done if r["relapsed"]),
        "agent_timed_out": sum(bool(r["timed_out"]) for r in done),
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

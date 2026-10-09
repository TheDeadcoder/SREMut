"""Classifies every recorded attempt and writes ledger.json; --check verifies the committed copy."""
import argparse
import ast
import csv
import gzip
import json
import re
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
PLAN = HERE / "plan.csv"
RUNS = HERE / "runs"
TRIAGE = HERE / "triage"
LEDGER = HERE / "ledger.json"
DEFAULT_INJECT_TIMEOUT = 300

CHECK = re.compile(r"Oracle check #(\d+): (\{.*\}) - validate_problem\.py:")
STAGE = re.compile(r"\[STAGE\] (.+?) - validate_problem\.py:")
INFRA = {"RUNNING", "INTERRUPTED", "TIMED_OUT", "NO_SUMMARY", "VALIDATOR_ERROR"}
DETECTED = {"LIFECYCLE_PASS", "RECOVERY_REJECT", "RECOVER_FAILED"}


def parse_result(text):
    try:
        return ast.literal_eval(text)
    except (ValueError, SyntaxError):
        return {"unparsed": text}


def oracle_checks(debug_log):
    phase, checks = None, {"inject": [], "recovery": []}
    for line in debug_log.splitlines():
        stage = STAGE.search(line)
        if stage:
            name = stage.group(1)
            phase = "inject" if "detects" in name else "recovery" if "confirms recovery" in name else None
            continue
        check = CHECK.search(line)
        if check and phase:
            checks[phase].append(parse_result(check.group(2)))
    return checks


def classify(record, summary, inject_checks):
    if record["status"] != "COMPLETED":
        return record["status"]
    if summary is None:
        return "NO_SUMMARY"
    stages = summary["stages"]
    for key, label in (("resolve", "NOT_REGISTERED"), ("deploy", "DEPLOY_FAILED"), ("inject", "INJECT_FAILED")):
        if stages[key]["status"] == "fail":
            return label
    if stages["oracle_fail"]["status"] == "fail":
        if not stages["oracle_fail"]["detail"].startswith("the mitigation oracle still reports success"):
            return "VALIDATOR_ERROR"
        last = inject_checks[-1] if inject_checks else {}
        return "ORACLE_ERROR" if last.get("success") is None else "NULL_ACCEPT"
    if stages["recover"]["status"] == "fail":
        return "RECOVER_FAILED"
    if stages["oracle_pass"]["status"] == "fail":
        if not stages["oracle_pass"]["detail"].startswith("the mitigation oracle still reports failure"):
            return "VALIDATOR_ERROR"
        return "RECOVERY_REJECT"
    if stages["oracle_pass"]["status"] == "pass":
        return "LIFECYCLE_PASS"
    return "UNCLASSIFIED"


def first_detection(inject_checks):
    for number, result in enumerate(inject_checks, start=1):
        if result.get("success") is False:
            return {"check": number, "reason": result.get("reason"),
                    "failure_class": result.get("failure_class"), "detail": result.get("detail")}
    return None


def attempt_entry(run_dir):
    record = json.loads((run_dir / "record.json").read_text())
    summary_path = run_dir / "summary.json"
    summary = json.loads(summary_path.read_text()) if summary_path.exists() else None
    debug_path = run_dir / "debug.log.gz"
    debug = gzip.decompress(debug_path.read_bytes()).decode() if debug_path.exists() else ""
    checks = oracle_checks(debug)
    return {
        "problem_id": record["problem_id"],
        "attempt": record["attempt"],
        "server": record["server"],
        "inject_timeout": record["inject_timeout"],
        "status": record["status"],
        "class": classify(record, summary, checks["inject"]),
        "inject_checks": len(checks["inject"]),
        "recovery_checks": len(checks["recovery"]),
        "first_detection": first_detection(checks["inject"]),
        "cleanup": (summary or {}).get("stages", {}).get("cleanup", {}).get("status"),
    }


def problem_outcome(attempts):
    substantive = [a["class"] for a in attempts
                   if a["class"] not in INFRA and a["inject_timeout"] == DEFAULT_INJECT_TIMEOUT]
    deploy_failures = sum(c in ("DEPLOY_FAILED", "INJECT_FAILED") for c in substantive)
    if deploy_failures >= 2:
        return "NOT_RUNNABLE"
    classes = sorted(set(substantive))
    if not classes:
        return None
    return classes[0] if len(classes) == 1 else "UNSTABLE"


def window_class(profile):
    if not profile:
        return None
    if "-" not in profile:
        return "never"
    return "transient" if profile.endswith("+") else "persistent"


def verdict(result):
    return None if result is None else result.get("success")


def triage_entry(run_dir):
    record = json.loads((run_dir / "record.json").read_text())
    data = json.loads((run_dir / "triage.json").read_text())
    states = {state: data.get(state) or {} for state in ("healthy", "faulted", "recovered")}
    return {
        "problem_id": record["problem_id"],
        "attempt": record["attempt"],
        "server": record["server"],
        "status": data.get("status"),
        "cleanup": data.get("cleanup"),
        "healthy_oracle": verdict(states["healthy"].get("oracle")),
        "workload": {state: verdict(block.get("workload")) for state, block in states.items()},
        "faulted_profile": states["faulted"].get("profile"),
        "faulted_window": window_class(states["faulted"].get("profile")),
        "recovered_profile": states["recovered"].get("profile"),
        "diff": data.get("diff"),
    }


def build(plan, runs, triage=None):
    attempts = [attempt_entry(d) for d in sorted(runs.glob("*/attempt-*"))
                if (d / "record.json").exists()]
    problems = []
    for row in plan:
        mine = sorted((a for a in attempts if a["problem_id"] == row["problem_id"]),
                      key=lambda a: a["attempt"])
        outcome = problem_outcome(mine)
        long_window = sorted({a["class"] for a in mine
                              if a["inject_timeout"] != DEFAULT_INJECT_TIMEOUT and a["class"] not in INFRA})
        problems.append({
            "problem_id": row["problem_id"],
            "census_verdict": row["census_verdict"],
            "predicted_detection": row["predicted_detection"],
            "retired": row["retired"],
            "outcome": outcome,
            "observed_detection": ("yes" if outcome in DETECTED else "no" if outcome == "NULL_ACCEPT" else None),
            "long_window_classes": long_window,
            "attempts": len(mine),
        })
    run = [p for p in problems if p["outcome"]]
    summary = {
        "problems_planned": len(problems),
        "problems_with_outcome": len(run),
        "outcomes": dict(sorted(Counter(p["outcome"] for p in run).items())),
        "outcomes_retired": dict(sorted(Counter(p["outcome"] for p in run if p["retired"] == "yes").items())),
        "census_vs_observed": dict(sorted(Counter(
            f"{p['census_verdict'] or 'NONE'}->{p['observed_detection']}" for p in run).items())),
        "attempts": len(attempts),
        "attempt_classes": dict(sorted(Counter(a["class"] for a in attempts).items())),
    }
    ledger = {"summary": summary, "problems": problems, "attempts": attempts}
    if triage is not None:
        entries = [triage_entry(d) for d in sorted(triage.glob("*/attempt-*"))
                   if (d / "triage.json").exists()]
        ledger["triage"] = entries
        summary["triage_window_vs_faulted_workload"] = dict(sorted(Counter(
            f"{e['faulted_window']}/workload={e['workload']['faulted']}" for e in entries).items()))
    return ledger


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    with open(PLAN, newline="") as f:
        plan = list(csv.DictReader(f))
    text = json.dumps(build(plan, RUNS, TRIAGE), indent=2, sort_keys=True) + "\n"
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

"""Writes schedule.csv: every problem whose oracle detected the null fault in the registry sweep, each operator,
a server, the static oracle features and the outcome they predict."""
import csv
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "registry_sweep"))

from build_ledger import build  # noqa: E402
from operators import OPERATORS  # noqa: E402
from run_sweep import PLAN, RUNS, load_plan  # noqa: E402

SCHEDULE = HERE / "schedule.csv"
TIMELINE = HERE.parent / "oracle_history" / "timeline.json"
SERVERS = ("A", "B", "C")
CAPACITY_ORACLES = {"MitigationOracle", "ConntrackMitigationOracle", "CpuThrottlingMitigationOracle",
                    "FDMitigationOracle", "IntegerOverflowPrimaryKeyMitigationOracle", "KafkaProducerLeakOracle",
                    "KubeletEvictionThresholdMisconfigMitigationOracle", "NightlyRebalanceOOMMitigationOracle",
                    "StaleHostAliasesMitigationOracle", "TrainTicketMitigationOracle"}
FUNCTIONAL_ORACLES = {"WorkloadOracle", "AlertOracle"}


def features(timeline_entry):
    names = set(timeline_entry["changes"][-1]["mitigation_oracle"]) if timeline_entry["changes"] else set()
    return {"capacity_check": "yes" if names & CAPACITY_ORACLES else "no",
            "functional_check": "yes" if names & FUNCTIONAL_ORACLES else "no"}


def predict(operator, row):
    if operator in ("SCALE0", "DELETE"):
        return "killed" if row["capacity_check"] == "yes" else "survived"
    if operator == "COLLAT":
        return "killed" if "yes" in (row["capacity_check"], row["functional_check"]) else "survived"
    return {"ADEQUATE": "killed", "BLIND": "survived"}.get(row["census_verdict"], "")


def build_schedule(plan, outcomes, timeline):
    census = {row["problem_id"]: row["census_verdict"] for row in plan}
    eligible = [row["problem_id"] for row in plan if outcomes.get(row["problem_id"]) == "yes"]
    rows = []
    for pid in eligible:
        for operator in OPERATORS:
            row = {"problem_id": pid, "operator": operator, "server": SERVERS[len(rows) % len(SERVERS)],
                   "census_verdict": census[pid], **features(timeline[pid])}
            row["predicted"] = predict(operator, row)
            rows.append(row)
    return rows


def main():
    plan = load_plan(PLAN)
    outcomes = {p["problem_id"]: p.get("observed_detection") for p in build(plan, RUNS)["problems"]}
    rows = build_schedule(plan, outcomes, json.loads(TIMELINE.read_text()))
    with open(SCHEDULE, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(f"{len(rows)} episodes over {len(rows) // len(OPERATORS)} problems -> {SCHEDULE.name}")


if __name__ == "__main__":
    main()

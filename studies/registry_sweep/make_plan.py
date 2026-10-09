"""Builds plan.csv: every problem registered at the pin, its census prediction and its server."""
import argparse
import ast
import csv
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
CENSUS = REPO / "analysis" / "census" / "coverage.csv"
REGISTRY = "sregym/conductor/problems/registry.py"
RETIREMENT_COMMIT = "a90b43cc973158120119a9f388081eac4f7adb36"
SERVERS = ("A", "B")
PILOT = (
    "auth_miss_mongodb",
    "missing_service_hotel_reservation",
    "missing_service_astronomy_shop",
    "trainticket_f17_nested_sql_select_clause_error",
    "operator_wrong_operator_image",
    "kubelet_crash",
    "latent_sector_error",
    "kafka_producer_leak",
    "workload_imbalance",
    "pvc_claim_mismatch",
)
PREDICTION = {"BLIND": "no", "ADEQUATE": "yes", "UNCERTAIN": ""}
FIELDS = ("problem_id", "census_verdict", "predicted_detection", "retired", "priority", "pilot", "server")


def registry_ids(source):
    tree = ast.parse(source)
    registry = max((n for n in ast.walk(tree) if isinstance(n, ast.Dict)), key=lambda d: len(d.keys))
    ids = [k.value for k in registry.keys if isinstance(k, ast.Constant)]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate registry keys")
    return ids


def git_show(sregym, rev):
    return subprocess.run(["git", "-C", str(sregym), "show", f"{rev}:{REGISTRY}"],
                          capture_output=True, text=True, check=True).stdout


def build(ids, retired, census):
    unknown = set(PILOT) - set(ids)
    if unknown:
        raise ValueError(f"pilot ids not registered: {sorted(unknown)}")
    rows = []
    for pid in ids:
        verdict = census.get(pid, "")
        priority = 1 if pid in retired or verdict in ("BLIND", "UNCERTAIN") else 2
        rows.append({"problem_id": pid, "census_verdict": verdict,
                     "predicted_detection": PREDICTION.get(verdict, ""),
                     "retired": "yes" if pid in retired else "no", "priority": priority,
                     "pilot": "yes" if pid in PILOT else "no"})
    pilot = sorted((r for r in rows if r["pilot"] == "yes"), key=lambda r: PILOT.index(r["problem_id"]))
    rest = sorted((r for r in rows if r["pilot"] == "no"), key=lambda r: (r["priority"], r["problem_id"]))
    ordered = pilot + rest
    for i, row in enumerate(ordered):
        row["server"] = SERVERS[i % len(SERVERS)]
    return ordered


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sregym", type=Path, default=Path.home() / "SREGym")
    ap.add_argument("--out", type=Path, default=HERE / "plan.csv")
    args = ap.parse_args()

    ids = registry_ids((args.sregym / REGISTRY).read_text())
    retired = set(ids) - set(registry_ids(git_show(args.sregym, RETIREMENT_COMMIT)))
    with open(CENSUS, newline="") as f:
        census = {r["problem_id"]: r["verdict"] for r in csv.DictReader(f)}
    rows = build(ids, retired, census)
    with open(args.out, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    print(f"{len(rows)} problems, {len(retired)} retired, "
          f"{sum(r['priority'] == 1 for r in rows)} priority 1 -> {args.out}")


if __name__ == "__main__":
    main()

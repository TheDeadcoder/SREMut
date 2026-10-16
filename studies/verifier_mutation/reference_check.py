"""How the reference verifier's verdicts depend on its user-request check: every state the reference judged in the
first attempts (the mutants and SREGym's own recoveries) is judged again with other thresholds, and the episode
labels are recomputed; the recoveries the reference judges unhealthy are broken down by failing check. Writes
reference_check.json; --check verifies the committed copy."""
import argparse
import copy
import json
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE)]

from mutation_ledger import RUNS, label  # noqa: E402

OUT = HERE / "reference_check.json"
VARIANTS = {"default": (0.5, 0), "floor_0.25": (0.25, 0), "floor_0.75": (0.75, 0), "one_failed_round": (0.5, 1),
            "no_user_requests": None}


def per_round(sample):
    return sample["requests"] / sample["rounds"] if sample.get("rounds") else 0


def healthy(judged, base, variant):
    if judged["capacity"] or judged["routing"]:
        return False
    if variant is None or not base or "error" in base or not base.get("requests"):
        return True
    now, (floor, tolerance) = judged.get("traffic"), variant
    return bool(now) and "error" not in now and now["failed_rounds"] <= tolerance and per_round(now) >= floor * per_round(base)


def failing(judged):
    parts = [name for name in ("capacity", "routing") if judged[name]]
    return parts + (["user requests"] if judged.get("function") is False else [])


def build(runs=RUNS):
    results = [json.loads(p.read_text()) for p in sorted(Path(runs).glob("*/*/attempt-1/result.json"))]
    mismatches, report = 0, {}
    for name, variant in VARIANTS.items():
        labels, changed, golden_unhealthy = Counter(), 0, 0
        for result in results:
            base = (result.get("healthy") or {}).get("traffic")
            recovered = (result.get("recovered") or {}).get("reference")
            if recovered is not None:
                golden_unhealthy += not healthy(recovered, base, variant)
            mutant = (result.get("mutant") or {}).get("reference")
            if mutant is None:
                labels[label(result)] += 1
                continue
            again = copy.deepcopy(result)
            again["mutant"]["reference"]["healthy"] = healthy(mutant, base, variant)
            labels[label(again)] += 1
            changed += label(again) != label(result)
            if name == "default":
                mismatches += again["mutant"]["reference"]["healthy"] != mutant["healthy"]
        report[name] = {"labels": dict(labels), "labels_changed": changed, "golden_unhealthy": golden_unhealthy}
    golden = [r["recovered"]["reference"] for r in results if (r.get("recovered") or {}).get("reference")]
    unhealthy = [g for g in golden if not g["healthy"]]
    return {"variants": report, "default_mismatches": mismatches, "golden_measured": len(golden),
            "golden_unhealthy_by_check": dict(Counter(" + ".join(failing(g)) for g in unhealthy)),
            "golden_unhealthy_rounds": dict(Counter(str((g.get("traffic") or {}).get("rounds")) for g in unhealthy))}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--runs", type=Path, default=RUNS)
    ap.add_argument("--check", action="store_true", help="fail if reference_check.json differs from the runs")
    args = ap.parse_args()
    text = json.dumps(build(args.runs), indent=2, sort_keys=True) + "\n"
    if args.check:
        if not OUT.exists() or OUT.read_text() != text:
            print("reference_check.json does not match the runs")
            return 1
        print("reference_check.json matches the runs")
        return 0
    OUT.write_text(text)
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())

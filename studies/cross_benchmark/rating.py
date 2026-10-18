"""A stratified sample of episodes for two human raters, who judge from the recorded evidence whether a correct
verifier should accept the state after the action, and their agreement with each other and with the reference
verifier. Strata: source (operator episodes and the benchmark's own repairs in the SREGym and AIOpsLab audits, agent
episodes) by reference verdict by benchmark verdict, up to PER_STRATUM items each, drawn with a fixed seed.

sample: writes rating/sheet.md (the evidence, without any verdict, and the fault of each task from rating/faults.csv),
rating/blank.csv (one row per item, to fill with accept, reject or unsure) and rating/key.json (the source of each
item and the reference verdict).
score: compares two filled copies with the key and writes rating/agreement.json."""
import argparse
import csv
import json
import os
import random
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
STUDIES = HERE.parent
ROOT = STUDIES.parent
sys.path[:0] = [str(HERE)]

import stats  # noqa: E402
from conformance import REFERENCE_BLIND  # noqa: E402

OUT = HERE / "rating"
PER_STRATUM = 4
SEED = 7
DECISIONS = ("accept", "reject", "unsure")
VERDICTS = ("original", "alert_absent", "mitigation_oracle")
NAMES = {"sregym": "SREGym", "sregym-head": "SREGym (HEAD)", "aiopslab": "AIOpsLab", "itbench": "ITBench"}
ACTIONS = {
    "RESTART": "Every pod in the application's namespace was deleted, so its controllers recreated them.",
    "SCALE0": "Every workload that was not fully ready after the fault was scaled to zero replicas: {targets}.",
    "DELETE": "Every workload that was not fully ready after the fault was deleted: {targets}.",
    "COLLAT": "The benchmark's own repair was run, then a healthy workload that the fault did not touch was scaled to "
              "zero replicas: {targets}.",
    "repair": "The benchmark's own repair was run.",
}
MAX_COMMANDS = 25
MAX_CHARS = 300


def short(text, limit=MAX_CHARS):
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[:limit] + " ..."


def traffic(sample):
    rounds = (sample or {}).get("rounds") or 0
    if "error" in (sample or {}) or not rounds:
        return "not measured"
    plural = "s" if rounds > 1 else ""
    return (f"{round(sample['requests'] / rounds)} per round over {rounds} round{plural}, "
            f"{sample.get('failed_rounds', 0)} with failures")


def listed(names):
    return ", ".join(names) or "none"


def state(reference, before, footprint):
    return [f"Workloads with fewer ready replicas than in the healthy state: {listed(reference['capacity'])}",
            f"Services that had ready endpoints in the healthy state and now have none, or a changed selector or "
            f"ports: {listed(reference['routing'])}",
            f"User requests: {traffic(reference.get('traffic'))} (before the fault: {traffic(before)})",
            f"Objects the fault changed that still differ from the healthy state: {listed(footprint)}"]


def audit_items(benchmark, runs):
    for path in sorted(Path(runs).glob("*/*/attempt-1/result.json")):
        result = json.loads(path.read_text())
        operator, task = path.parts[-4], path.parts[-3]
        if result.get("status") != "COMPLETED":
            continue
        before = (result.get("healthy") or {}).get("traffic")
        after_fault = result.get("unhealthy_after_fault") or []
        context = [f"Not fully ready after the fault: {listed(after_fault)}"]
        recovered = result.get("recovered") or {}
        if recovered.get("reference"):
            yield {"source": f"{benchmark} repair", "path": path, "benchmark": benchmark, "task": task,
                   "context": context, "action": [ACTIONS["repair"]],
                   "state": state(recovered["reference"], before, []),
                   "reference": recovered["reference"]["healthy"], "verdict": recovered["profile"].endswith("+")}
        mutant = result.get("mutant") or {}
        if result.get("applied") and mutant.get("reference") and mutant.get("verdict") is not None:
            targets = ", ".join(t.split("/")[-1] for t in result["applied"].get("targets") or [])
            yield {"source": f"{benchmark} operator", "path": path, "benchmark": benchmark, "task": task,
                   "context": context, "action": [ACTIONS[operator].format(targets=targets)],
                   "state": state(mutant["reference"], before, mutant.get("footprint_left") or []),
                   "reference": mutant["reference"]["healthy"], "verdict": mutant["verdict"]}


def agent_items(roots):
    for root in roots:
        for path in sorted(Path(root).glob("*/*/attempt-*/result.json")):
            result = json.loads(path.read_text())
            benchmark, task = path.parts[-4], path.parts[-3]
            verdicts = result.get("verdicts") or {}
            verdict = next((verdicts[v] for v in VERDICTS if v in verdicts), None)
            reference = result.get("reference")
            if (result.get("status") != "COMPLETED" or verdict is None or not reference
                    or (benchmark, task) in REFERENCE_BLIND):
                continue
            agent = result.get("agent") or {}
            commands = agent.get("commands") or []
            action = ["An agent worked on the task. Its commands:"]
            action += [f"{i}. `{short(c)}`" for i, c in enumerate(commands[:MAX_COMMANDS], 1)]
            if len(commands) > MAX_COMMANDS:
                action.append(f"({len(commands) - MAX_COMMANDS} more commands)")
            action.append(f"Its closing message: {short(agent.get('summary') or 'none', 600)}")
            before = (result.get("healthy") or {}).get("traffic")
            footprint = result.get("footprint_left") or []
            if "by_namespace" in reference:
                lines = []
                for namespace, judged in sorted(reference["by_namespace"].items()):
                    lines += [f"In {namespace}: {line}" for line in state(judged, before, [])[:3]]
                lines.append(f"Objects the fault changed that still differ from the healthy state: {listed(footprint)}")
                flags = listed(reference.get("flags_changed") or [])
                lines.append(f"Feature flags changed from the healthy state: {flags}")
            else:
                lines = state(reference, before, footprint)
            yield {"source": "agent", "path": path, "benchmark": benchmark, "task": task, "context": [],
                   "action": action, "state": lines, "reference": reference["healthy"], "verdict": verdict["passed"]}


def stratum(item):
    return (f"{item['source']} | reference {'accepts' if item['reference'] else 'rejects'} | "
            f"benchmark {'accepts' if item['verdict'] else 'rejects'}")


def sample(items, per_stratum=PER_STRATUM, seed=SEED):
    rng = random.Random(seed)
    strata = defaultdict(list)
    for item in items:
        strata[stratum(item)].append(item)
    chosen = []
    for name in sorted(strata):
        group = sorted(strata[name], key=lambda i: Path(i["path"]).parts[-5:])
        rng.shuffle(group)
        tasks = set()
        for item in group:
            if len(tasks) < per_stratum and (item["benchmark"], item["task"]) not in tasks:
                tasks.add((item["benchmark"], item["task"]))
                chosen.append(item)
    rng.shuffle(chosen)
    return chosen


def faults(path):
    if not path.exists():
        return {}
    with open(path, newline="") as f:
        return {(row["benchmark"], row["task"]): row["fault"] for row in csv.DictReader(f)}


def write_sample(chosen, out=OUT):
    out.mkdir(exist_ok=True)
    described = faults(out / "faults.csv")
    sheet = ["# Rating sheet", "",
             "For each item, decide from the evidence whether a correct verifier for this task should accept the",
             "state after the action (the fault is repaired and the application serves its users) or reject it.",
             "Write accept, reject or unsure for each item in the answers file, with an optional note.", ""]
    key = {}
    for n, item in enumerate(chosen, 1):
        rid = f"R{n:02d}"
        sheet += [f"## {rid}", "", f"Task: {NAMES.get(item['benchmark'], item['benchmark'])} `{item['task']}`", ""]
        fault = described.get((item["benchmark"], item["task"]))
        sheet += [f"Fault: {fault}", ""] if fault else []
        sheet += [*item["context"], *([""] if item["context"] else [])]
        sheet += ["Action:", "", *item["action"], "", "State after the action:", ""]
        sheet += [f"- {line}" for line in item["state"]] + [""]
        key[rid] = {"source": item["source"], "stratum": stratum(item), "path": os.path.relpath(item["path"], ROOT),
                    "reference": "accept" if item["reference"] else "reject"}
    (out / "sheet.md").write_text("\n".join(sheet))
    with open(out / "blank.csv", "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["id", "decision", "note"])
        writer.writerows([rid, "", ""] for rid in key)
    (out / "key.json").write_text(json.dumps(key, indent=2, sort_keys=True) + "\n")
    return key


def read(path):
    with open(path, newline="", encoding="utf-8-sig") as f:
        decisions = {row["id"]: row["decision"].strip().lower() for row in csv.DictReader(f)}
    wrong = {i: d for i, d in decisions.items() if d not in DECISIONS}
    if wrong:
        raise ValueError(f"{path}: decisions must be one of {DECISIONS}: {wrong}")
    return decisions


def agreement(pairs):
    kappa = stats.cohen_kappa(pairs)
    return {"agreement": stats.rate(sum(a == b for a, b in pairs), len(pairs)),
            "kappa": round(kappa, 3) if kappa is not None else None}


def score(key, raters):
    ids = sorted(key)
    missing = {name: [i for i in ids if i not in r] for name, r in raters.items()}
    if any(missing.values()):
        raise ValueError(f"items without a decision: {missing}")
    (first, a), (second, b) = raters.items()
    report = {"items": len(ids), "between_raters": agreement([(a[i], b[i]) for i in ids])}
    for name, decisions in raters.items():
        decided = [i for i in ids if decisions[i] != "unsure"]
        report[name] = {"unsure": len(ids) - len(decided),
                        "with_reference": agreement([(decisions[i], key[i]["reference"]) for i in decided]),
                        "disagree_with_reference": {i: key[i]["stratum"] for i in decided
                                                    if decisions[i] != key[i]["reference"]}}
    both = [i for i in ids if a[i] == b[i] != "unsure"]
    report["raters_agree"] = {"items": len(both),
                              "with_reference": agreement([(a[i], key[i]["reference"]) for i in both])}
    return report


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)
    s = sub.add_parser("sample")
    s.add_argument("--mutation-runs", type=Path, default=STUDIES / "verifier_mutation" / "runs")
    s.add_argument("--aiopslab-runs", type=Path, default=STUDIES / "aiopslab_audit" / "runs")
    s.add_argument("--agent-runs", type=Path, nargs="+",
                   default=[STUDIES / "agent_loop" / "runs", STUDIES / "agent_loop" / "runs-haiku"])
    s.add_argument("--force", action="store_true", help="replace the sample the raters already have")
    c = sub.add_parser("score")
    c.add_argument("ratings", type=Path, nargs=2, help="the two raters' filled copies of blank.csv")
    args = ap.parse_args()
    if args.command == "sample":
        if (OUT / "sheet.md").exists() and not args.force:
            print("rating/sheet.md is the sample the raters have; --force draws a new one from the current runs")
            return 1
        items = [*audit_items("sregym", args.mutation_runs), *audit_items("aiopslab", args.aiopslab_runs),
                 *agent_items(args.agent_runs)]
        key = write_sample(sample(items))
        counts = defaultdict(int)
        for entry in key.values():
            counts[entry["stratum"]] += 1
        print(json.dumps({"items": len(key), "by_stratum": dict(sorted(counts.items()))}, indent=2))
        return 0
    key = json.loads((OUT / "key.json").read_text())
    report = score(key, {f"rater_{n}": read(path) for n, path in enumerate(args.ratings, 1)})
    text = json.dumps(report, indent=2, sort_keys=True) + "\n"
    (OUT / "agreement.json").write_text(text)
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())

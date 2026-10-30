"""Intervals and exact tests for the headline counts: a Wilson 95% interval for each rate, McNemar's exact test for the
paired comparisons (AIOpsLab before and after its fix, SREGym's alert oracle at the pin and at HEAD, each pair of
agent models on the same tasks), agreement with Cohen's kappa between first attempts and their repeats, the share of
repeated agent tasks with the same label in every attempt, the settle study's acceptance rates, and the agent episodes
that wrote the monitoring objects alert-based verifiers read. Reads conformance.json, agent_actions.json and the
studies' ledgers; writes headline.json, --check verifies the committed copy."""
import argparse
import json
import sys
from collections import Counter, defaultdict
from itertools import combinations
from pathlib import Path

HERE = Path(__file__).resolve().parent
STUDIES = HERE.parent
sys.path[:0] = [str(HERE)]

import stats  # noqa: E402
from conformance import REFERENCE_BLIND  # noqa: E402

OUT = HERE / "headline.json"
VERDICTS = ("original", "alert_absent", "mitigation_oracle")
WRONG = ("false_pass", "false_fail")


def load(path):
    return json.loads(Path(path).read_text())


def score(labels):
    return stats.rate(labels.get("killed", 0), labels.get("killed", 0) + labels.get("survived", 0))


def paired(**sides):
    (a, first), (b, second) = sides.items()
    keys = sorted(first.keys() & second.keys())
    only_a = sum(first[k] and not second[k] for k in keys)
    only_b = sum(second[k] and not first[k] for k in keys)
    return {"pairs": len(keys), a: stats.rate(sum(first[k] for k in keys), len(keys)),
            b: stats.rate(sum(second[k] for k in keys), len(keys)), f"only_{a}": only_a, f"only_{b}": only_b,
            "mcnemar_p": float(f"{stats.mcnemar_exact(only_a, only_b):.3g}")}


def agreement(episodes, key, label):
    attempts = defaultdict(dict)
    for e in episodes:
        attempts[key(e)][e["attempt"]] = label(e)
    pairs = [(a["attempt-1"], a["attempt-2"]) for a in attempts.values() if {"attempt-1", "attempt-2"} <= a.keys()]
    kappa = stats.cohen_kappa(pairs)
    return {"agreement": stats.rate(sum(x == y for x, y in pairs), len(pairs)),
            "kappa": round(kappa, 3) if kappa is not None else None}


def same_in_every_attempt(rows):
    attempts = defaultdict(list)
    for r in rows:
        attempts[(r["model"], r["benchmark"], r["task"])].append(r["label"])
    repeated = [labels for labels in attempts.values() if len(labels) > 1]
    return stats.rate(sum(len(set(labels)) == 1 for labels in repeated), len(repeated))


def agent_rows(specs):
    for spec in specs:
        model, path = spec.split("=", 1)
        for e in load(path)["episodes"]:
            verdict = next((e[v] for v in VERDICTS if v in e), None)
            if verdict and verdict["label"] != "undecided" and (e["benchmark"], e["task"]) not in REFERENCE_BLIND:
                yield {"model": model, "benchmark": e["benchmark"], "task": e["task"], "attempt": e["attempt"],
                       "label": verdict["label"]}


def agents(rows):
    by_model = {"all": rows, **{m: [r for r in rows if r["model"] == m] for m in sorted({r["model"] for r in rows})}}
    verdicts = {}
    for model, group in by_model.items():
        labels = Counter(r["label"] for r in group)
        verdicts[model] = {"wrong": stats.rate(sum(labels[w] for w in WRONG), len(group)),
                           "false_pass": labels["false_pass"], "false_fail": labels["false_fail"]}
    first = defaultdict(dict)
    for r in rows:
        if r["attempt"] == "attempt-1":
            first[r["model"]][(r["benchmark"], r["task"])] = r["label"] in WRONG
    return {"verdicts": verdicts,
            "wrong_by_model_on_shared_tasks": {f"{a}_vs_{b}": paired(**{a: first[a], b: first[b]})
                                               for a, b in combinations(sorted(first), 2)},
            "repeats": {**agreement(rows, lambda r: (r["model"], r["benchmark"], r["task"]), lambda r: r["label"]),
                        "same_in_every_attempt": {m: same_in_every_attempt(group) for m, group in by_model.items()}}}


def settle(ledger):
    summary = ledger["summary"]
    return {state: {key.replace("accepted_", ""): stats.rate(value, summary[state]["judged"])
                    for key, value in sorted(summary[state].items()) if key.startswith("accepted_")}
            for state in ("none", "restart", "repair")}


def monitoring_writes(actions):
    return {group: {"wrote_monitoring": stats.rate(counts["wrote_monitoring"], counts["episodes"]),
                    "passed_after_writing_monitoring": counts["passed_after_writing_monitoring"]}
            for group, counts in sorted(actions["summary"].items()) if counts["wrote_monitoring"]}


def aiopslab_fix(before, after):
    def killed(ledger):
        return {(e["problem_id"], e["operator"]): e["default_label"] == "killed" for e in ledger["episodes"]
                if e["attempt"] == "attempt-1" and e["default_label"] in ("killed", "survived")}
    first, second = before["summary"]["by_operator_default_verdict"], after["summary"]["by_operator_default_verdict"]
    return {"by_operator": {op: {"before": score(first[op]["labels"]), "after": score(second[op]["labels"])}
                            for op in sorted(first)},
            "killed": paired(before=killed(before), after=killed(after))}


def alert_baseline(pin, head):
    def accepted(ledger):
        return {e["problem_id"]: e["healthy_accepted"]["immediate"] for e in ledger["episodes"]
                if e["status"] == "COMPLETED"}
    return {"healthy_accepted": paired(pin=accepted(pin), head=accepted(head))}


def build(args):
    conformance = load(args.conformance)
    totals = conformance["totals"]
    reading = totals["code_reading"]
    mutation, aiopslab, k8s, itbench = load(args.mutation), load(args.aiopslab), load(args.k8s), load(args.itbench)
    first = [e for e in mutation["episodes"] if e["attempt"] == "attempt-1"]
    return {
        "matrix": {b: {p: stats.rate(len(cell["violated"]), cell["measured"]) for p, cell in props.items()
                       if cell["measured"]} for b, props in conformance["matrix"].items()},
        "beyond_prior_practice": stats.rate(totals["only_beyond_prior_practice"], totals["tasks_with_findings"]),
        "beyond_prior_practice_without_scope_or_isolation": stats.rate(
            totals["beyond_prior_practice_without_scope_or_isolation"],
            totals["tasks_with_findings_without_scope_or_isolation"]),
        "code_reading": {"precision": stats.rate(reading["predicted_and_found"],
                                                 reading["predicted_and_found"] + reading["predicted_not_found"]),
                         "recall": stats.rate(reading["predicted_and_found"],
                                              reading["predicted_and_found"] + reading["found_not_predicted"])},
        "sregym_mutation_score": {op: score(Counter(e["label"] for e in first if e["operator"] == op))
                                  for op in sorted({e["operator"] for e in first})},
        "aiopslab_fix": aiopslab_fix(aiopslab, load(args.aiopslab_patched)),
        "alert_baseline": alert_baseline(load(args.alert_pin), load(args.alert_head)),
        "agents": agents(list(agent_rows(args.agents))),
        "agents_writing_monitoring": monitoring_writes(load(args.actions)),
        "settle": settle(load(args.settle)),
        "repeats": {
            "sregym": agreement(mutation["episodes"], lambda e: (e["problem_id"], e["operator"]), lambda e: e["label"]),
            "aiopslab": agreement(aiopslab["episodes"], lambda e: (e["problem_id"], e["operator"]),
                                  lambda e: e["default_label"]),
            "k8s-ai-bench": agreement(k8s["episodes"], lambda e: (e["task"], e["action"]), lambda e: e["label"]),
            "itbench": agreement(itbench["episodes"], lambda e: (e["scenario"], e["action"]), lambda e: e["label"]),
        },
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--conformance", type=Path, default=HERE / "conformance.json")
    ap.add_argument("--mutation", type=Path, default=STUDIES / "verifier_mutation" / "ledger.json")
    ap.add_argument("--aiopslab", type=Path, default=STUDIES / "aiopslab_audit" / "ledger.json")
    ap.add_argument("--aiopslab-patched", type=Path, default=STUDIES / "aiopslab_audit" / "ledger-patched.json")
    ap.add_argument("--k8s", type=Path, default=STUDIES / "k8s_ai_bench_audit" / "ledger.json")
    ap.add_argument("--itbench", type=Path, default=STUDIES / "itbench_audit" / "ledger.json")
    ap.add_argument("--alert-pin", type=Path, default=STUDIES / "alert_baseline" / "ledger.json")
    ap.add_argument("--alert-head", type=Path, default=STUDIES / "alert_baseline" / "ledger-head.json")
    ap.add_argument("--settle", type=Path, default=STUDIES / "alert_baseline" / "ledger-settle.json")
    ap.add_argument("--actions", type=Path, default=HERE / "agent_actions.json")
    ap.add_argument("--agents", nargs="+", metavar="MODEL=LEDGER",
                    default=[f"sonnet={STUDIES / 'agent_loop' / 'ledger.json'}",
                             f"haiku={STUDIES / 'agent_loop' / 'ledger-haiku.json'}",
                             f"codex={STUDIES / 'agent_loop' / 'ledger-codex.json'}"])
    ap.add_argument("--check", action="store_true", help="fail if headline.json differs from the inputs")
    args = ap.parse_args()
    text = json.dumps(build(args), indent=2, sort_keys=True) + "\n"
    if args.check:
        if not OUT.exists() or OUT.read_text() != text:
            print("headline.json does not match the inputs")
            return 1
        print("headline.json matches the inputs")
        return 0
    OUT.write_text(text)
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())

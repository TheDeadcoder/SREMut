"""Verifier defects on four benchmarks, by validity property and by the instrument that found them, built from the
studies' ledgers and runs; writes conformance.json with a property matrix and a comparison of audit instruments.
--check verifies the committed copy.

Properties: P1 rejects the unrepaired system, P2 accepts the healthy system, P3 accepts a correct repair, P4 rejects
wrong repairs, P5 gives the same verdict for the same repair, P6 leaves no state behind and does not depend on other
tasks, P7 accepts only repairs that last, T the task itself is valid (its setup takes effect, its text points at it,
and neither its answer nor its verdict is in the agent's reach: on an ITBench scenario where the check passed only in
the agent episodes that wrote what it reads, and failed every other, the agents set the verdict). A fault that the
oracle cannot see counts against P1, not T.
Instruments: noop (a no-op agent), golden (the benchmark's own repair), healthy (the system before injection, except
for the tasks in HEALTHY_NOT_APPLICABLE, whose correct end state, by the benchmark's own repair, differs from it),
operators (generic and task-specific wrong or alternative repairs), scope (an untouched workload scaled to zero),
window (verdicts over time), repeats, isolation, durability, reference (our reference verifier), published (published
results), agents, static (code reading).
"""
import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
STUDIES = HERE.parent
OUT = HERE / "conformance.json"
PROPERTIES = ("P1", "P2", "P3", "P4", "P5", "P6", "P7", "T")
PRIOR = ("noop", "golden")
DISPUTABLE = ("static", "scope", "isolation")
HARNESS_NAMESPACES = {"default", "kube-node-lease", "kube-public", "kube-system", "local-path-storage", "observe",
                      "openebs", "sregym", "chaos-mesh"}
REFERENCE_BLIND = {("itbench", "38")}
HEALTHY_NOT_APPLICABLE = {
    "calico_route_reflector_label_drift_hotel_reservation", "configmap_drift_hotel_reservation",
    "cronjob_sidecar_blocks_completion_hotel_reservation", "cumulative_admission_webhook_timeout_hotel_reservation",
    "finalizer_deadlock_controller_hotel_reservation", "hpa_missing_effective_cpu_request_hotel_reservation",
    "ingress_misroute", "integer_overflow_primary_key_astronomy_shop", "kafka_poison_pill_hol_block",
    "priority_preemption_cascade_hotel_reservation", "rolling_update_misconfigured_hotel_reservation",
    "rolling_update_misconfigured_social_network", "search_rate_retry_collapse_hotel_reservation",
    "secret_rotation_stale_env_credentials_astronomy_shop"}
DOCUMENTED = (
    ("itbench", "31", "T", "static"),
    ("itbench", "31", "T", "agents"),
    ("sregym", "operator_wrong_operator_image", "T", "agents"),
)
SURVIVAL_OUTCOMES = ("survived", "killed", "equivalent", "rejected_fault_present", "false_reject")
ITBENCH_OUTCOMES = ("null_pass", "survived", "transient_pass", "killed", "equivalent")
PUBLISHED_CAUSES = {"verifier: image is not the exact string nginx": "P3",
                    "verifier: exec into db-0 before it is scheduled": "P5",
                    "task: namespace that setup does not create": "T"}


class Findings:
    def __init__(self):
        self.found = defaultdict(lambda: defaultdict(set))
        self.measured = defaultdict(lambda: defaultdict(set))
        self.code_reading = defaultdict(list)

    def add(self, benchmark, task, prop, instrument):
        self.found[benchmark][task].add((prop, instrument))

    def measure(self, benchmark, prop, tasks):
        self.measured[benchmark][prop].update(tasks)

    def predict(self, benchmark, predicted_defect, observed_defect):
        self.code_reading[benchmark].append((predicted_defect, observed_defect))


def load(path):
    return json.loads(Path(path).read_text())


def agent_episodes(paths):
    for path in paths:
        for e in load(path)["episodes"]:
            if e.get("status") == "completed":
                yield e


def sregym(f, sweep, mutation, mutation_runs, ladder, agents):
    b = "sregym"
    registered = [p for p in sweep["problems"] if p["outcome"] != "NOT_REGISTERED"]
    tasks = {p["problem_id"] for p in registered}
    for p in registered:
        t = p["problem_id"]
        if p["outcome"] == "NULL_ACCEPT":
            f.add(b, t, "P1", "noop")
        if p["outcome"] == "RECOVERY_REJECT":
            f.add(b, t, "P3", "golden")
        if p["outcome"] == "UNSTABLE":
            f.add(b, t, "P5", "repeats")
        if p["census_verdict"] in ("BLIND", "ADEQUATE"):
            f.predict(b, p["census_verdict"] == "BLIND", p["outcome"] == "NULL_ACCEPT")
    f.measure(b, "P1", tasks)
    f.measure(b, "P3", tasks)
    f.measure(b, "P5", tasks)
    f.measure(b, "T", tasks)
    first = [e for e in mutation["episodes"] if e["attempt"] == "attempt-1"]
    by_pair = defaultdict(dict)
    for e in mutation["episodes"]:
        by_pair[(e["problem_id"], e["operator"])][e["attempt"]] = e["label"]
    for e in first:
        t, label = e["problem_id"], e["label"]
        if label == "survived":
            f.add(b, t, "P4", "scope" if e["operator"] == "COLLAT" else "operators")
        elif label == "false_reject":
            f.add(b, t, "P3", "operators")
        elif label == "unknown":
            f.add(b, t, "P5", "window")
        if (e.get("noop_window") or {}).get("class") == "transient":
            f.add(b, t, "P5", "window")
        if e.get("predicted") in ("survived", "killed") and label in SURVIVAL_OUTCOMES:
            f.predict(b, e["predicted"] == "survived", label == "survived")
        recovery = e.get("recovery") or {}
        if recovery.get("profile") is not None and "+" not in recovery["profile"]:
            f.add(b, t, "P3", "golden")
    f.measure(b, "P4", {e["problem_id"] for e in first if e["label"] in ("killed", "survived")})
    for (t, _), labels in by_pair.items():
        if len(set(labels.values())) > 1:
            f.add(b, t, "P5", "repeats")
    healthy = defaultdict(set)
    for path in Path(mutation_runs).glob("*/*/attempt-1/result.json"):
        if path.parts[-3] not in HEALTHY_NOT_APPLICABLE:
            healthy[path.parts[-3]].add((((load(path).get("healthy") or {}).get("oracle")) or {}).get("success"))
    for t, verdicts in healthy.items():
        if verdicts == {False}:
            f.add(b, t, "P2", "healthy")
    f.measure(b, "P2", set(healthy))
    left = set()
    for path in (STUDIES / "registry_sweep" / "runs").glob("*/attempt-*/record.json"):
        record = load(path)
        if record.get("namespaces_after") is None:
            continue
        left_behind = set(record["namespaces_after"]) - HARNESS_NAMESPACES
        f.measure(b, "P6", {record["problem_id"]})
        if left_behind:
            left.add(record["problem_id"])
    for t in left:
        f.add(b, t, "P6", "isolation")
    m4 = ladder["table"]["M4"]
    if m4["faulted_accepts"]["stock"] and not m4["faulted_accepts"]["contract"]:
        f.add(b, "missing_service_social_network", "P7", "durability")
    f.measure(b, "P7", {"missing_service_social_network"})
    agent_tasks(f, b, agents, "mitigation_oracle")


def agent_tasks(f, benchmark, episodes, verdict):
    seen = set()
    for e in episodes:
        if e["benchmark"] != benchmark or verdict not in e:
            continue
        seen.add(e["task"])
        if (benchmark, e["task"]) in REFERENCE_BLIND or e.get("healthy") is None:
            continue
        f.measure(benchmark, "P3" if e["healthy"] else "P4", {e["task"]})
        label = e[verdict]["label"]
        if label == "false_pass":
            f.add(benchmark, e["task"], "P4", "agents")
        elif label == "false_fail":
            f.add(benchmark, e["task"], "P3", "agents")
    return seen


def aiopslab_agent_runs(f, runs):
    for path in runs:
        for result_path in Path(path).glob("aiopslab/*/attempt-*/result.json"):
            result, task = load(result_path), result_path.parts[-3]
            healthy = (result.get("healthy") or {}).get("original") or {}
            if "success" in healthy:
                f.measure("aiopslab", "P2", {task})
                if healthy["success"] is False:
                    f.add("aiopslab", task, "P2", "healthy")
            commands = (result.get("agent") or {}).get("commands") or []
            if any("/scripts/" in c and "mitigate" in c for c in commands):
                f.add("aiopslab", task, "T", "agents")


def aiopslab(f, audit, agents, agent_runs):
    b = "aiopslab"
    first = [e for e in audit["episodes"] if e["attempt"] == "attempt-1"]
    tasks = {e["problem_id"] for e in first}
    for e in first:
        t = e["problem_id"]
        window = e.get("noop_window") or {}
        if window.get("class") in ("never", "transient"):
            f.add(b, t, "P1", "noop")
        if e.get("default_label") == "survived":
            f.add(b, t, "P4", "scope" if e["operator"] == "COLLAT" else "operators")
        if e.get("label") == "unknown":
            f.add(b, t, "P5", "window")
        recovery = e.get("recovery") or {}
        if recovery.get("profile") is not None:
            f.measure(b, "P3", {t})
            if "+" not in recovery["profile"]:
                f.add(b, t, "P3", "golden")
        if e.get("predicted") in ("survived", "killed") and e.get("default_label") in SURVIVAL_OUTCOMES:
            f.predict(b, e["predicted"] == "survived", e["default_label"] == "survived")
    f.measure(b, "P1", tasks)
    f.measure(b, "P5", tasks)
    f.measure(b, "T", tasks)
    f.measure(b, "P4", {e["problem_id"] for e in first if e.get("default_label") in ("killed", "survived")})
    by_pair = defaultdict(dict)
    for e in audit["episodes"]:
        by_pair[(e["problem_id"], e["operator"])][e["attempt"]] = e.get("default_label")
    for (t, _), labels in by_pair.items():
        if len(set(labels.values())) > 1:
            f.add(b, t, "P5", "repeats")
    agent_tasks(f, b, agents, "original")
    aiopslab_agent_runs(f, agent_runs)


def k8s(f, audit, interference, published):
    b = "k8s-ai-bench"
    first = [e for e in audit["episodes"] if e["attempt"] == "attempt-1"]
    f.measure(b, "P1", {e["task"] for e in first if e["kind"] == "null"})
    f.measure(b, "P3", {e["task"] for e in first if e["kind"] == "correct"})
    f.measure(b, "P4", {e["task"] for e in first
                        if e["kind"] in ("generic", "wrong") and e["label"] != "not_applicable"})
    f.measure(b, "P5", {e["task"] for e in first})
    f.measure(b, "T", set(published["tasks"]))
    for e in first:
        t, label = e["task"], e["label"]
        if e["kind"] == "null" and label != "killed":
            f.add(b, t, "P1", "noop")
        if label == "survived":
            f.add(b, t, "P4", "operators")
        if label == "false_reject":
            f.add(b, t, "P3", "operators")
        if e.get("settled_label") and e["settled_label"] != label:
            f.add(b, t, "P5", "window")
        if e.get("predicted") in ("accept", "reject"):
            if e["kind"] == "correct" and label in ("accepted", "false_reject"):
                f.predict(b, e["predicted"] == "reject", label == "false_reject")
            elif e["kind"] != "correct" and label in ("killed", "survived"):
                f.predict(b, e["predicted"] == "accept", label == "survived")
    for path in Path(interference).glob("*/*/attempt-1/result.json"):
        result = load(path)
        f.measure(b, "P6", {result["victim"]})
        control = path.parents[2] / "none" / "attempt-1" / "result.json"
        if result.get("other") and control.exists() and result["verify_after"] != load(control)["verify_after"]:
            f.add(b, result["victim"], "P6", "isolation")
    for task, counts in published["tasks"].items():
        for cause, prop in PUBLISHED_CAUSES.items():
            if counts.get(cause):
                f.add(b, task, prop, "published")


def verdict_set_by_writes(actions, benchmark):
    episodes = defaultdict(list)
    for e in actions["episodes"]:
        if e["benchmark"] == benchmark:
            passed = next((p for p in e["verdicts"].values() if p is not None), None)
            episodes[e["task"]].append((bool(e["wrote_monitoring"] or e["wrote_telemetry"]), passed))
    return {t for t, rows in episodes.items()
            if any(p for _, p in rows) and all(w for w, p in rows if p) and any(p is False for w, p in rows if not w)}


def itbench(f, audit, agents, actions):
    b = "itbench"
    first = [e for e in audit["episodes"] if e["attempt"] == "attempt-1"]
    tasks = {e["scenario"] for e in first}
    injected = {e["scenario"] for e in first if e["label"] != "inject_failed"}
    f.measure(b, "T", tasks)
    f.measure(b, "P1", {e["scenario"] for e in first if e["action"] == "NULL"
                        and e["label"] not in ("inject_failed", "inert")})
    f.measure(b, "P2", injected)
    f.measure(b, "P4", {e["scenario"] for e in first if e["action"] != "NULL"
                        and e["label"] not in ("inject_failed", "not_applicable", "inert")})
    f.measure(b, "P7", {e["scenario"] for e in first if e["action"] == "RESTART"
                        and e["label"] not in ("inject_failed", "inert")})
    for t in verdict_set_by_writes(actions, b):
        f.add(b, t, "T", "agents")
    f.measure(b, "P5", {e["scenario"] for e in audit["episodes"] if e["attempt"] == "attempt-2"})
    for e in first:
        t, label = e["scenario"], e["label"]
        if label == "inject_failed":
            f.add(b, t, "T", "noop")
        if label == "null_pass":
            f.add(b, t, "P1", "noop")
        if label == "survived":
            f.add(b, t, "P4", "operators")
        if label == "transient_pass":
            f.add(b, t, "P7", "durability")
        if e.get("healthy_ground_truth_firing"):
            f.add(b, t, "P2", "healthy")
        if e.get("predicted") in ("pass", "fail") and label in ITBENCH_OUTCOMES:
            f.predict(b, e["predicted"] == "pass", label in ("null_pass", "survived", "transient_pass"))
    by_pair = defaultdict(dict)
    for e in audit["episodes"]:
        by_pair[(e["scenario"], e["action"])][e["attempt"]] = e["label"]
    for (t, _), labels in by_pair.items():
        if len(set(labels.values())) > 1:
            f.add(b, t, "P5", "repeats")
    agent_tasks(f, b, agents, "alert_absent")


def code_reading(pairs):
    tp = sum(p and o for p, o in pairs)
    fp = sum(p and not o for p, o in pairs)
    fn = sum(o and not p for p, o in pairs)
    return {"episodes": len(pairs), "predicted_and_found": tp, "predicted_not_found": fp, "found_not_predicted": fn,
            "precision": round(tp / (tp + fp), 3) if tp + fp else None,
            "recall": round(tp / (tp + fn), 3) if tp + fn else None}


def summarize(f):
    for benchmark, task, prop, instrument in DOCUMENTED:
        f.add(benchmark, task, prop, instrument)
    matrix, comparison = {}, {}
    for b in sorted(f.found.keys() | f.measured.keys()):
        found = f.found[b]
        matrix[b] = {prop: {"violated": sorted(t for t, s in found.items() if any(p == prop for p, _ in s)),
                            "measured": len(f.measured[b][prop]) if f.measured[b][prop] else None}
                     for prop in PROPERTIES}
        live = {t for t, s in found.items() if any(i != "static" for _, i in s)}
        strict = {t for t, s in found.items() if any(i not in DISPUTABLE for _, i in s)}
        def by(instruments):
            return {t for t, s in found.items() if any(i in instruments for _, i in s)}
        prior = by(PRIOR)
        comparison[b] = {
            "tasks_with_findings": len(live),
            "noop": len(by(("noop",))),
            "prior_practice": len(prior),
            "only_beyond_prior_practice": len(live - prior),
            "tasks_with_findings_without_scope_or_isolation": len(strict),
            "beyond_prior_practice_without_scope_or_isolation": len(strict - prior),
            "agents": len(by(("agents",))),
            "code_reading": code_reading(f.code_reading[b]),
            "by_instrument": {i: len(by((i,))) for i in sorted({i for s in found.values() for _, i in s})},
        }
    totals = {k: sum(c[k] for c in comparison.values()) for k in
              ("tasks_with_findings", "noop", "prior_practice", "only_beyond_prior_practice", "agents",
               "tasks_with_findings_without_scope_or_isolation", "beyond_prior_practice_without_scope_or_isolation")}
    totals["code_reading"] = code_reading([pair for pairs in f.code_reading.values() for pair in pairs])
    findings = {b: {t: sorted(f"{p}:{i}" for p, i in s) for t, s in sorted(tasks.items())}
                for b, tasks in sorted(f.found.items())}
    return {"matrix": matrix, "comparison": comparison, "totals": totals, "findings": findings}


def build(args):
    f = Findings()
    agents = list(agent_episodes(args.agents))
    sregym(f, load(args.sweep), load(args.mutation), args.mutation_runs, load(args.ladder), agents)
    aiopslab(f, load(args.aiopslab), agents, args.agent_runs)
    k8s(f, load(args.k8s), args.interference, load(args.published))
    itbench(f, load(args.itbench), agents, load(args.actions))
    return summarize(f)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sweep", type=Path, default=STUDIES / "registry_sweep" / "ledger.json")
    ap.add_argument("--mutation", type=Path, default=STUDIES / "verifier_mutation" / "ledger.json")
    ap.add_argument("--mutation-runs", type=Path, default=STUDIES / "verifier_mutation" / "runs")
    ap.add_argument("--ladder", type=Path, default=STUDIES / "repair_ladder" / "ledger.json")
    ap.add_argument("--aiopslab", type=Path, default=STUDIES / "aiopslab_audit" / "ledger.json")
    ap.add_argument("--k8s", type=Path, default=STUDIES / "k8s_ai_bench_audit" / "ledger.json")
    ap.add_argument("--interference", type=Path, default=STUDIES / "k8s_ai_bench_audit" / "interference")
    ap.add_argument("--published", type=Path, default=STUDIES / "k8s_ai_bench_audit" / "published.json")
    ap.add_argument("--itbench", type=Path, default=STUDIES / "itbench_audit" / "ledger.json")
    ap.add_argument("--agents", type=Path, nargs="+",
                    default=[STUDIES / "agent_loop" / f"ledger{suffix}.json" for suffix in ("", "-haiku", "-codex")])
    ap.add_argument("--agent-runs", type=Path, nargs="+",
                    default=[STUDIES / "agent_loop" / f"runs{suffix}" for suffix in ("", "-haiku", "-codex")])
    ap.add_argument("--actions", type=Path, default=HERE / "agent_actions.json")
    ap.add_argument("--check", action="store_true", help="fail if conformance.json differs from the inputs")
    args = ap.parse_args()
    text = json.dumps(build(args), indent=2, sort_keys=True) + "\n"
    if args.check:
        if not OUT.exists() or OUT.read_text() != text:
            print("conformance.json does not match the inputs")
            return 1
        print("conformance.json matches the inputs")
        return 0
    OUT.write_text(text)
    report = json.loads(text)
    print(json.dumps({"comparison": report["comparison"], "totals": report["totals"]}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

import json
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent / "registry_sweep")]

import make_schedule  # noqa: E402
import mutation_ledger  # noqa: E402
import mutation_probe  # noqa: E402
import operators  # noqa: E402
import reference  # noqa: E402
import run_mutation  # noqa: E402


def workload(name, replicas, ready, generation=1, observed=1, updated=None):
    return {"metadata": {"name": name, "generation": generation}, "spec": {"replicas": replicas},
            "status": {"readyReplicas": ready, "observedGeneration": observed,
                       "updatedReplicas": replicas if updated is None else updated}}


FOUND = {"deployments/frontend": workload("frontend", 1, 1), "deployments/geo": workload("geo", 1, 0),
         "deployments/rate": workload("rate", 2, 2), "statefulsets/db": workload("db", 1, 1)}


class OperatorTest(unittest.TestCase):
    def test_unhealthy_and_collateral_target(self):
        self.assertEqual(operators.unhealthy(FOUND), ["deployments/geo"])
        self.assertEqual(operators.collateral_target(FOUND, {"deployments/frontend"}), "deployments/rate")
        self.assertIsNone(operators.collateral_target({"deployments/geo": FOUND["deployments/geo"]}, set()))

    def test_rolled_out(self):
        self.assertTrue(operators.rolled_out(workload("a", 1, 1)))
        self.assertFalse(operators.rolled_out(workload("a", 1, 1, generation=2, observed=1)))
        self.assertFalse(operators.rolled_out(workload("a", 2, 1)))

    def test_apply_issues_one_command_per_target(self):
        calls = []
        with unittest.mock.patch.object(operators, "workloads", return_value=FOUND), \
                unittest.mock.patch.object(operators, "kubectl", side_effect=lambda *a: calls.append(a)):
            self.assertEqual(operators.apply("SCALE0", "ns"), {"targets": ["deployments/geo"]})
            self.assertEqual(operators.apply("COLLAT", "ns", {"deployments/frontend"}),
                             {"targets": ["deployments/rate"], "replicas": 2})
            self.assertEqual(len(operators.apply("RESTART", "ns")["targets"]), 4)
        self.assertEqual(calls[0], ("scale", "deployments/geo", "--replicas=0", "-n", "ns"))
        self.assertEqual(calls[1], ("scale", "deployments/rate", "--replicas=0", "-n", "ns"))
        self.assertEqual(calls[2][:2], ("rollout", "restart"))
        healthy_only = {"deployments/frontend": FOUND["deployments/frontend"]}
        with unittest.mock.patch.object(operators, "workloads", return_value=healthy_only):
            self.assertIsNone(operators.apply("DELETE", "ns"))


HEALTHY = {"workloads": {"deployments/frontend": 1, "deployments/geo": 1},
           "services": {"frontend": {"selector": {"app": "frontend"}, "ports": [[5000, "5000", "TCP"]], "endpoints": 1},
                        "headless": {"selector": None, "ports": [], "endpoints": 0}},
           "traffic": {"rounds": 6, "requests": 6000, "failed_rounds": 0}}


class ReferenceTest(unittest.TestCase):
    def test_judge(self):
        self.assertTrue(reference.judge(HEALTHY, HEALTHY)["healthy"])
        scaled = {**HEALTHY, "workloads": {"deployments/frontend": 1, "deployments/geo": 0}}
        self.assertEqual(reference.judge(HEALTHY, scaled)["capacity"], ["deployments/geo"])
        unrouted = {**HEALTHY, "services": {"frontend": {**HEALTHY["services"]["frontend"], "endpoints": 0}}}
        self.assertEqual(reference.judge(HEALTHY, unrouted)["routing"], ["frontend"])
        slow = {**HEALTHY, "traffic": {"rounds": 6, "requests": 1000, "failed_rounds": 0}}
        self.assertIs(reference.judge(HEALTHY, slow)["function"], False)
        failing = {**HEALTHY, "traffic": {"rounds": 6, "requests": 6000, "failed_rounds": 1}}
        self.assertFalse(reference.judge(HEALTHY, failing)["healthy"])
        no_traffic = {**HEALTHY, "traffic": None}
        self.assertIsNone(reference.judge(no_traffic, no_traffic)["function"])
        self.assertTrue(reference.judge(no_traffic, no_traffic)["healthy"])


class ProbeTest(unittest.TestCase):
    def test_footprint_ignores_pods_and_endpoint_slices(self):
        healthy = {"deployments/geo": "a", "configmaps/x": "c", "pods/ReplicaSet/geo-1": "p",
                   "endpointslices/geo-1": "e"}
        faulted = {"deployments/geo": "b", "configmaps/x": "c", "pods/ReplicaSet/geo-2": "q",
                   "endpointslices/geo-1": "f", "networkpolicies/deny": "n"}
        self.assertEqual(mutation_probe.changed_keys(healthy, faulted), ["deployments/geo", "networkpolicies/deny"])
        self.assertEqual(mutation_probe.footprint_left(healthy, faulted, {**healthy, "networkpolicies/deny": "n"}),
                         ["networkpolicies/deny"])
        self.assertEqual(mutation_probe.footprint_left(healthy, faulted, healthy), [])


class ScheduleTest(unittest.TestCase):
    def test_schedule_covers_detected_problems_with_predictions(self):
        plan = [{"problem_id": "p1", "census_verdict": "BLIND"}, {"problem_id": "p2", "census_verdict": "ADEQUATE"},
                {"problem_id": "p3", "census_verdict": "BLIND"}]
        timeline = {"p1": {"changes": [{"mitigation_oracle": ["CompoundedOracle", "MitigationOracle"]}]},
                    "p2": {"changes": [{"mitigation_oracle": ["CustomOracle", "WorkloadOracle"]}]},
                    "p3": {"changes": []}}
        rows = make_schedule.build_schedule(plan, {"p1": "yes", "p2": "yes", "p3": "no"}, timeline)
        self.assertEqual(len(rows), 8)
        by = {(r["problem_id"], r["operator"]): r["predicted"] for r in rows}
        self.assertEqual(by[("p1", "SCALE0")], "killed")
        self.assertEqual(by[("p2", "DELETE")], "survived")
        self.assertEqual(by[("p2", "COLLAT")], "killed")
        self.assertEqual(by[("p1", "RESTART")], "survived")
        self.assertEqual(by[("p2", "RESTART")], "killed")
        self.assertEqual([r["server"] for r in rows[:4]], ["A", "B", "C", "A"])


class LedgerTest(unittest.TestCase):
    def result(self, verdict, profile, healthy, applied=True):
        return {"status": "COMPLETED", "applied": {"targets": ["x"]} if applied else None,
                "mutant": {"verdict": verdict, "profile": profile, "reference": {"healthy": healthy}}}

    def test_labels(self):
        self.assertEqual(mutation_ledger.label(self.result(True, "+++", False)), "survived")
        self.assertEqual(mutation_ledger.label(self.result(False, "---", False)), "killed")
        self.assertEqual(mutation_ledger.label(self.result(True, "+++", True)), "equivalent")
        self.assertEqual(mutation_ledger.label(self.result(False, "---", True)), "false_reject")
        self.assertEqual(mutation_ledger.label(self.result(True, "+-+", False)), "unknown")
        self.assertEqual(mutation_ledger.label(self.result(None, "?", False)), "oracle_error")
        self.assertEqual(mutation_ledger.label({"status": "COMPLETED", "applied": None}), "not_applicable")
        self.assertEqual(mutation_ledger.label({"status": "ERROR"}), "error")

    def test_window_class_and_recovery(self):
        self.assertEqual(mutation_ledger.window_class("+++"), "never")
        self.assertEqual(mutation_ledger.window_class("+--?-"), "persistent")
        self.assertEqual(mutation_ledger.window_class("-+++"), "transient")
        rec = mutation_ledger.recovery({"profile": "--+", "reference": {"healthy": True}})
        self.assertEqual(rec["seconds_to_accept"], 30)
        self.assertIsNone(mutation_ledger.recovery({"profile": "---", "reference": {"healthy": False}})
                          ["seconds_to_accept"])

    def test_build_scores_operators(self):
        with tempfile.TemporaryDirectory() as tmp:
            runs = Path(tmp)
            for op, pid, result in (("SCALE0", "p1", self.result(False, "--", False)),
                                    ("SCALE0", "p2", self.result(True, "++", False)),
                                    ("RESTART", "p1", {**self.result(True, "++", True), "noop": {"profile": "-++"}})):
                d = runs / op / pid / "attempt-1"
                d.mkdir(parents=True)
                (d / "result.json").write_text(json.dumps(result))
            ledger = mutation_ledger.build(runs, Path(tmp) / "missing.csv")
        self.assertEqual(ledger["summary"]["by_operator"]["SCALE0"]["mutation_score"], 0.5)
        self.assertEqual(ledger["summary"]["problems_with_survivor"], ["p2"])
        self.assertEqual(ledger["summary"]["noop_window_classes"], {"transient": 1})


class RunnerTest(unittest.TestCase):
    def test_pilot_and_stop_rule(self):
        pilot = run_mutation.episodes("A", pilot=True)
        self.assertEqual(len(pilot), len(run_mutation.PILOT) * 4)
        done = {"status": "COMPLETED"}
        self.assertFalse(run_mutation.must_stop(done, {"cleanup_errors": ["wait for namespace deletion: timeout"]}))
        self.assertTrue(run_mutation.must_stop(done, {"cleanup_errors": ["recover fault: RuntimeError: boom"]}))
        self.assertTrue(run_mutation.must_stop({"status": "TIMED_OUT"}, {}))


if __name__ == "__main__":
    unittest.main()

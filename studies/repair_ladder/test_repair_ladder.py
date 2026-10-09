import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import contract  # noqa: E402
import ladder_ledger  # noqa: E402
import mutants  # noqa: E402
import run_ladder  # noqa: E402

SERVICE = {
    "apiVersion": "v1", "kind": "Service",
    "metadata": {"name": "user-service", "namespace": "social-network", "uid": "u", "resourceVersion": "1",
                 "creationTimestamp": "x", "labels": {"app": "user-service"}},
    "spec": {"clusterIP": "10.96.0.10", "clusterIPs": ["10.96.0.10"], "selector": {"service": "user-service"},
             "ports": [{"name": "9090", "port": 9090, "protocol": "TCP", "targetPort": 9090}]},
    "status": {"loadBalancer": {}},
}
POD = {"metadata": {"name": "user-service-abc", "uid": "pod-uid",
                    "ownerReferences": [{"kind": "ReplicaSet", "name": "user-service-rs", "controller": True}]},
       "status": {"podIP": "10.244.1.5", "phase": "Running",
                  "conditions": [{"type": "Ready", "status": "True"}]}}
RS = {"metadata": {"name": "user-service-rs",
                   "ownerReferences": [{"kind": "Deployment", "name": "user-service", "controller": True}]}}


class MutantTest(unittest.TestCase):
    def test_bodies_differ_from_capture_in_one_place(self):
        self.assertIsNone(mutants.service_body("M1", SERVICE))
        exact = mutants.service_body("M5", SERVICE)
        self.assertNotIn("clusterIP", exact["spec"])
        self.assertNotIn("uid", exact["metadata"])
        self.assertNotIn("status", exact)
        self.assertEqual(exact["metadata"]["labels"], {"app": "user-service"})
        self.assertEqual(mutants.service_body("C1", SERVICE), exact)
        self.assertEqual(mutants.service_body("M2", SERVICE)["spec"]["selector"], mutants.M2_SELECTOR)
        self.assertEqual(mutants.service_body("M3", SERVICE)["spec"]["ports"][0]["targetPort"], 65535)
        self.assertNotIn("selector", mutants.service_body("M4", SERVICE)["spec"])
        self.assertEqual(SERVICE["spec"]["clusterIP"], "10.96.0.10")

    def test_endpoints_pin_the_pod(self):
        body = mutants.endpoints_body(SERVICE, POD)
        address = body["subsets"][0]["addresses"][0]
        self.assertEqual(address["ip"], "10.244.1.5")
        self.assertEqual(address["targetRef"]["uid"], "pod-uid")
        self.assertEqual(body["subsets"][0]["ports"], [{"name": "9090", "port": 9090, "protocol": "TCP"}])


class ContractTest(unittest.TestCase):
    def test_eligible_addresses(self):
        slices = [{"addressType": "IPv4", "endpoints": [
            {"addresses": ["10.244.1.5"], "conditions": {"ready": True}},
            {"addresses": ["10.244.1.6"], "conditions": {"ready": False}},
            {"addresses": ["10.244.1.7"], "conditions": {"ready": True, "terminating": True}},
            {"addresses": ["not-an-ip"], "conditions": {"ready": True}}]},
            {"addressType": "IPv6", "endpoints": [{"addresses": ["fd00::1"], "conditions": {"ready": True}}]}]
        self.assertEqual(contract.eligible_addresses(slices), ["10.244.1.5"])

    def test_unmapped_requires_a_live_deployment_pod(self):
        self.assertEqual(contract.unmapped(["10.244.1.5"], [POD], [RS]), [])
        self.assertEqual(contract.unmapped(["10.244.9.9"], [POD], [RS]), ["10.244.9.9"])
        other = {"metadata": {"name": "rs2", "ownerReferences": [
            {"kind": "Deployment", "name": "compose-post-service", "controller": True}]}}
        stray = json.loads(json.dumps(POD))
        stray["metadata"]["ownerReferences"][0]["name"] = "rs2"
        self.assertEqual(contract.unmapped(["10.244.1.5"], [stray], [other]), ["10.244.1.5"])

    def test_parse_nslookup_ignores_the_resolver(self):
        out = ("Server:\t\t10.96.0.10\nAddress:\t10.96.0.10:53\n\n"
               "Name:\tuser-service.social-network.svc.cluster.local\nAddress: 10.96.45.12\n")
        self.assertEqual(contract.parse_nslookup(out), ["10.96.45.12"])

    def test_below_floor(self):
        deployments = [{"metadata": {"name": "a"}, "spec": {"replicas": 1},
                        "status": {"readyReplicas": 1, "availableReplicas": 1}},
                       {"metadata": {"name": "b"}, "spec": {"replicas": 0}, "status": {}}]
        self.assertEqual(contract.below_floor(deployments, {"a": 1, "b": 1, "c": 1}),
                         ["b: desired 0 < 1", "b: ready 0 < 1", "b: available 0 < 1", "c: missing"])

    def test_wait_workload_needs_a_running_generator_and_a_clean_round(self):
        ready = {"type": "Ready", "status": "True"}
        pending = {"metadata": {}, "status": {"phase": "Pending"}}
        running = {"metadata": {}, "status": {"phase": "Running", "conditions": [ready]}}
        rounds = iter([[SimpleNamespace(ok=False)], [SimpleNamespace(ok=True)]])
        wrk = SimpleNamespace(collect=lambda number: next(rounds))
        with mock.patch.object(contract, "items", side_effect=[[], [pending, running], [running], [running]]), \
                mock.patch.object(contract.time, "sleep"):
            contract.wait_workload(wrk, serving=True)
        self.assertIsNone(next(rounds, None))
        with mock.patch.object(contract, "items", return_value=[pending]), \
                mock.patch.object(contract.time, "sleep"), self.assertRaises(contract.InfrastructureError):
            contract.wait_workload(wrk, serving=False, timeout=0)

    def test_round_stats(self):
        log = ("  1024 requests in 10.00s, 205.43KB read\n  Socket errors: connect 0, read 2, write 0, timeout 7\n"
               "  Non-2xx or 3xx responses: 89\n")
        self.assertEqual(contract.round_stats(log), {"non2xx": 89, "socket_errors": 9, "timeouts": 7})
        self.assertEqual(contract.round_stats("  1024 requests in 10.00s\n"),
                         {"non2xx": 0, "socket_errors": 0, "timeouts": 0})


class ScheduleTest(unittest.TestCase):
    def test_every_state_runs_on_both_servers(self):
        for state in mutants.STATES:
            self.assertEqual({run_ladder.server_for(state, a) for a in run_ladder.ATTEMPTS}, {"A", "B"})

    def test_each_attempt_splits_states(self):
        for attempt in run_ladder.ATTEMPTS:
            a, b = run_ladder.schedule("A", attempt, False), run_ladder.schedule("B", attempt, False)
            self.assertEqual(sorted(a + b), sorted(mutants.STATES))
            self.assertEqual(len(a), 3)


class LedgerTest(unittest.TestCase):
    def test_graders_and_table(self):
        conductor = {"success": False, "oracles": [{"name": "0-MitigationOracle", "success": True},
                                                   {"name": "1-ServiceEndpointMitigationOracle", "success": False}]}
        control = {"success": True, "oracles": [{"name": "0-MitigationOracle", "success": True},
                                                {"name": "1-ServiceEndpointMitigationOracle", "success": True}]}
        passed = {"verdict": "PASS", "violated": [], "window": {"requests": 1000, "non2xx": 0, "timeouts": 0}}
        rejected = {"verdict": "REJECT", "violated": ["MS-I1", "MS-I2"], "window": {"requests": 900, "non2xx": 90}}
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "M1" / "attempt-1"
            run_dir.mkdir(parents=True)
            (run_dir / "record.json").write_text(json.dumps(
                {"problem_id": "M1", "attempt": 1, "server": "A", "status": "COMPLETED"}))
            (run_dir / "result.json").write_text(json.dumps({
                "status": "COMPLETED", "teardown": "pass", "activation": {"active": True},
                "healthy": {"oracle": control, "workload_oracle": {"success": True}, "contract": passed},
                "faulted": {"conductor": conductor, "workload_oracle": {"success": False}, "contract": rejected},
                "restored": {"oracle": control, "workload_oracle": {"success": True}, "contract": passed}}))
            ledger = ladder_ledger.build(Path(tmp))
        row = ledger["table"]["M1"]
        self.assertEqual(row["runs"], 1)
        self.assertEqual(row["faulted_accepts"],
                         {"stock": 1, "service_aware": 0, "patched": 0, "workload": 0, "contract": 0})
        self.assertEqual(row["faulted_violated"], [("MS-I1", "MS-I2")])
        self.assertEqual(row["controls_accepted"]["restored"]["contract"], 1)
        self.assertEqual(ledger["table"]["M4"]["runs"], 0)


if __name__ == "__main__":
    unittest.main()

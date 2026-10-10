import csv
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent / "verifier_mutation"), str(HERE.parent / "registry_sweep")]

import cluster_reset  # noqa: E402
import probe  # noqa: E402
import run_audit  # noqa: E402
from operators import OPERATORS  # noqa: E402

WRK = """Running 10s test @ http://10.96.0.12:8080/wrk2-api/post/compose
  2 threads and 2 connections
  101 requests in 10.00s, 20.31KB read
  Socket errors: connect 0, read 0, write 0, timeout 3
  Non-2xx or 3xx responses: 7
Requests/sec:     10.10
"""


class ProbeTest(unittest.TestCase):
    def test_parse_wrk(self):
        failing = probe.parse_wrk(WRK)
        self.assertEqual((failing.number, failing.ok), (101, False))
        clean = probe.parse_wrk(WRK.replace("timeout 3", "timeout 0").replace("  Non-2xx or 3xx responses: 7\n", ""))
        self.assertEqual((clean.number, clean.ok), (101, True))
        self.assertEqual((probe.parse_wrk("").number, probe.parse_wrk("").ok), (0, False))


class ResetTest(unittest.TestCase):
    def test_extras_lists_only_added_objects(self):
        baseline = {kind: [] for kind in cluster_reset.KINDS}
        baseline["namespaces"] = ["namespace/default", "namespace/kube-system"]
        current = {kind: list(baseline[kind]) for kind in cluster_reset.KINDS}
        current["namespaces"] = ["namespace/default", "namespace/kube-system", "namespace/test-hotel-reservation"]
        current["jobs -n default"] = ["job.batch/wrk2-job"]
        self.assertEqual(cluster_reset.extras(baseline, current),
                         {"namespaces": ["namespace/test-hotel-reservation"],
                          "jobs -n default": ["job.batch/wrk2-job"]})
        self.assertEqual(cluster_reset.extras(baseline, baseline), {})


class ScheduleTest(unittest.TestCase):
    def test_schedule_covers_every_problem_and_operator(self):
        with open(run_audit.SCHEDULE, newline="") as f:
            rows = list(csv.DictReader(f))
        problems = {r["problem_id"] for r in rows}
        self.assertEqual(len(problems), 14)
        self.assertEqual(sorted((r["problem_id"], r["operator"]) for r in rows),
                         sorted((p, op) for p in problems for op in OPERATORS))
        self.assertTrue(all(r["predicted"] in ("", "killed", "survived") for r in rows))
        self.assertEqual([r["operator"] for r in rows[:14]], ["RESTART"] * 14)
        self.assertEqual(len(run_audit.episodes("D", pilot=True)), len(run_audit.PILOT) * 4)
        self.assertEqual(len(run_audit.episodes("D", pilot=False)), 56)


if __name__ == "__main__":
    unittest.main()

import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent / "verifier_mutation"), str(HERE.parent / "registry_sweep")]

import ledger  # noqa: E402
import probe  # noqa: E402
import run_audit  # noqa: E402

SCENARIO = """---
faults:
- injections:
  - id: nonexistent-kubernetes-workload-container-image
- injections:
  - id: crashing-kubernetes-workload-init-container
"""
TRUTH = """---
alerts:
  - name: KubePodNotReady
    labels: {}
  - name: KubeContainerWaiting
    labels: {}
entities:
  - id: x
"""


def state(after, firing):
    return {"after": after, "ground_truth": firing, "in_namespace": []}


class ProbeTest(unittest.TestCase):
    def test_scenario_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "archive/scenarios/60").mkdir(parents=True)
            (root / "library/scenarios/60").mkdir(parents=True)
            (root / "archive/scenarios/60/scenario.yaml").write_text(SCENARIO)
            (root / "library/scenarios/60/ground_truth.yaml").write_text(TRUTH)
            self.assertEqual(probe.fault_groups(root, 60), 2)
            self.assertEqual(probe.ground_truth(root, 60), ["KubeContainerWaiting", "KubePodNotReady"])

    def test_alert_absent_needs_two_consecutive_clean_polls_in_the_window(self):
        firing = ["KubePodNotReady"]
        self.assertTrue(probe.alert_absent([state(0, firing), state(30, []), state(60, [])]))
        self.assertFalse(probe.alert_absent([state(0, []), state(30, firing), state(60, [])]))
        self.assertFalse(probe.alert_absent([state(270, firing), state(300, []), state(330, [])]))
        self.assertFalse(probe.alert_absent([state(0, []), {"after": 30, "error": "x"}, state(60, [])]))


class LedgerTest(unittest.TestCase):
    @staticmethod
    def result(action, passed, healthy, applied=True):
        return {"status": "COMPLETED", "action": action, "action_result": {"applied": applied},
                "alert_absent": passed, "reference": {"healthy": healthy}, "detected": True,
                "healthy_alerts": [state(0, [])], "evaluation": [state(0, ["X"]), state(30, []), state(60, [])]}

    def test_labels(self):
        self.assertEqual(ledger.label({}, self.result("NULL", True, False)), "null_pass")
        self.assertEqual(ledger.label({}, self.result("RESTART", True, False)), "survived")
        self.assertEqual(ledger.label({}, self.result("SCALE0", False, False)), "killed")
        self.assertEqual(ledger.label({}, self.result("RESTART", True, True)), "equivalent")
        self.assertEqual(ledger.label({}, self.result("SCALE0", True, False, applied=False)), "not_applicable")
        self.assertEqual(ledger.label({"status": "TIMED_OUT"}, {}), "timed_out")

    def test_build(self):
        with tempfile.TemporaryDirectory() as tmp:
            runs = Path(tmp)
            run_dir = runs / "20" / "RESTART" / "attempt-1"
            run_dir.mkdir(parents=True)
            (run_dir / "record.json").write_text(json.dumps({"status": "COMPLETED"}))
            (run_dir / "result.json").write_text(json.dumps(self.result("RESTART", True, False)))
            built = ledger.build(runs)
        self.assertEqual(built["summary"]["labels"], {"survived": 1})
        self.assertEqual(built["episodes"][0]["evaluation_profile"], "-++")
        self.assertEqual(built["summary"]["prediction_agreement"], {"judged": 1, "agree": 1})


class ScheduleTest(unittest.TestCase):
    def test_schedule(self):
        with open(run_audit.SCHEDULE, newline="") as f:
            rows = list(csv.DictReader(f))
        self.assertEqual(len(rows), 21)
        self.assertTrue(all(r["predicted"] in ("pass", "fail") for r in rows))
        self.assertTrue(all(r["action"] in ("NULL", "RESTART", "SCALE0", "SILENCE") for r in rows))
        self.assertLessEqual(set(run_audit.PILOT), {(r["scenario"], r["action"]) for r in rows})


if __name__ == "__main__":
    unittest.main()

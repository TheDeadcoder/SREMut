import json
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent / "registry_sweep"), str(HERE.parent / "verifier_mutation")]

import ledger  # noqa: E402
import probe  # noqa: E402
import run_alert_baseline  # noqa: E402

GRAFANA = "ContainerCPUThrottling/grafana-5cb469bf99-wkl7g"
LOADGEN = "ContainerCPUThrottling/load-generator-5d945c566-5xdr2"
THROTTLE = {"alertname": "ContainerCPUThrottling", "namespace": "astronomy-shop", "pod": "grafana-5cb469bf99-wkl7g"}
LAG = {"alertname": "MessageConsumerLag", "namespace": "astronomy-shop"}


class ProbeTest(unittest.TestCase):
    def test_alert_state_lists_alerts_each_baseline_leaves_in(self):
        baselines = {"immediate": set(), "delayed": {probe.key(THROTTLE)}}
        with unittest.mock.patch.object(probe, "firing", return_value=[THROTTLE, LAG]):
            state = probe.alert_state("astronomy-shop", 0, baselines, {"MessageConsumerLag"})
        self.assertEqual(state["firing"], [GRAFANA, "MessageConsumerLag/"])
        self.assertEqual(state["outside_immediate"], [GRAFANA])
        self.assertEqual(state["outside_delayed"], [])

    def test_alert_state_records_query_errors(self):
        with unittest.mock.patch.object(probe, "firing", side_effect=ValueError("bad json")):
            state = probe.alert_state("astronomy-shop", 0, {"immediate": set()}, set())
        self.assertIn("error", state)
        self.assertNotIn("outside_immediate", state)


class LedgerTest(unittest.TestCase):
    @staticmethod
    def result(delayed, recovery):
        end = {"after_deploy": 900, "firing": [LOADGEN], "outside_immediate": [LOADGEN], "outside_delayed": []}
        return {"status": "COMPLETED",
                "immediate": {"baseline": [], "healthy": {"verdict": {"success": False}},
                              "recovery": {"verdict": {"success": False}}},
                "steady": [{"after_deploy": 60, "firing": []}, {"after_deploy": 90, "firing": [GRAFANA]}],
                "delayed": {"baseline": [GRAFANA], "healthy": {"verdict": {"success": delayed}},
                            "recovery": {"profile": recovery, "alerts": [end]}},
                "detect": {"profile": "-"}}

    def test_build_summarises_completed_episodes(self):
        with tempfile.TemporaryDirectory() as tmp:
            runs = Path(tmp)
            episodes = {"kafka_queue_problems": ("COMPLETED", self.result(True, "-+")),
                        "kubelet_crash": ("COMPLETED", self.result(False, "---")),
                        "valkey_memory_disruption": ("TIMED_OUT", None)}
            for pid, (status, result) in episodes.items():
                run_dir = runs / pid / "attempt-1"
                run_dir.mkdir(parents=True)
                (run_dir / "record.json").write_text(json.dumps({"server": "B", "status": status}))
                if result:
                    (run_dir / "result.json").write_text(json.dumps(result))
            built = ledger.build(runs)
        summary = built["summary"]
        self.assertEqual((summary["episodes"], summary["completed"], summary["immediate_baseline_empty"]), (3, 2, 2))
        self.assertEqual(summary["healthy_accepted"], {"immediate": 0, "delayed": 1})
        self.assertEqual(summary["recovery_accepted"], {"immediate": 0, "delayed": 1})
        self.assertEqual(summary["detected_with_delayed_baseline"], 2)
        self.assertEqual(summary["recovery_stays_accepted_with_delayed_baseline"], 1)
        self.assertEqual(summary["in_delayed_baseline"], {"ContainerCPUThrottling/grafana": 2})
        first = built["episodes"][0]
        self.assertEqual(first["throttling_first_seen"], 90)
        self.assertEqual(first["left_by_immediate_at_end"], ["ContainerCPUThrottling/load-generator"])
        self.assertEqual(first["left_by_delayed_at_end"], [])
        self.assertEqual(built["episodes"][2]["status"], "TIMED_OUT")

    def test_stays_accepted(self):
        self.assertTrue(ledger.stays_accepted("--++"))
        self.assertFalse(ledger.stays_accepted("-+-+"))
        self.assertFalse(ledger.stays_accepted("---"))

    def test_workload_drops_pod_suffixes(self):
        self.assertEqual(ledger.workload(GRAFANA), "ContainerCPUThrottling/grafana")
        self.assertEqual(ledger.workload("KubePodNotReady/otel-collector-agent-k6krq"),
                         "KubePodNotReady/otel-collector-agent")
        self.assertEqual(ledger.workload("KubePodNotReady/opensearch-0"), "KubePodNotReady/opensearch-0")
        self.assertEqual(ledger.workload("HighRequestErrorRate/"), "HighRequestErrorRate/")


class RunnerTest(unittest.TestCase):
    def test_pilot_problems_are_scheduled(self):
        self.assertEqual(len(set(run_alert_baseline.PROBLEMS)), 13)
        self.assertLessEqual(set(run_alert_baseline.PILOT), set(run_alert_baseline.PROBLEMS))


if __name__ == "__main__":
    unittest.main()

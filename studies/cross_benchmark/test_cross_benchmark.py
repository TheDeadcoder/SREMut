import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE)]

import agent_actions  # noqa: E402
import conformance  # noqa: E402
import headline  # noqa: E402
import rating  # noqa: E402
import stats  # noqa: E402


class StatsTest(unittest.TestCase):
    def test_wilson(self):
        lo, hi = stats.wilson(5, 10)
        self.assertAlmostEqual(lo, 0.2366, places=4)
        self.assertAlmostEqual(hi, 0.7634, places=4)
        self.assertEqual(stats.wilson(0, 0), None)
        self.assertEqual(stats.wilson(0, 13)[0], 0.0)

    def test_kappa(self):
        self.assertEqual(stats.cohen_kappa([("a", "a"), ("b", "b")]), 1.0)
        self.assertAlmostEqual(stats.cohen_kappa([("a", "a"), ("a", "b"), ("b", "b"), ("b", "a")]), 0.0)

    def test_mcnemar(self):
        self.assertAlmostEqual(stats.mcnemar_exact(0, 12), 2 / 2 ** 12)
        self.assertEqual(stats.mcnemar_exact(0, 0), 1.0)


class ConformanceTest(unittest.TestCase):
    def test_code_reading(self):
        scored = conformance.code_reading([(True, True), (True, False), (False, True), (False, False)])
        self.assertEqual((scored["precision"], scored["recall"]), (0.5, 0.5))
        self.assertEqual(conformance.code_reading([])["precision"], None)

    def test_prior_practice_and_strict_counts(self):
        f = conformance.Findings()
        f.add("b", "t1", "P1", "noop")
        f.add("b", "t2", "P4", "scope")
        f.add("b", "t3", "P5", "window")
        f.measure("b", "P1", {"t1", "t2", "t3"})
        report = conformance.summarize(f)["comparison"]["b"]
        self.assertEqual((report["tasks_with_findings"], report["prior_practice"]), (3, 1))
        self.assertEqual(report["only_beyond_prior_practice"], 2)
        self.assertEqual(report["beyond_prior_practice_without_scope_or_isolation"], 1)

    def test_agent_verdicts(self):
        f = conformance.Findings()
        episodes = [{"benchmark": "x", "task": "a", "healthy": True, "v": {"label": "false_fail"}},
                    {"benchmark": "x", "task": "b", "healthy": False, "v": {"label": "false_pass"}}]
        conformance.agent_tasks(f, "x", episodes, "v")
        self.assertEqual(f.found["x"]["a"], {("P3", "agents")})
        self.assertEqual(f.found["x"]["b"], {("P4", "agents")})


class HeadlineTest(unittest.TestCase):
    def test_paired(self):
        report = headline.paired(pin={"a": False, "b": False, "c": True},
                                 head={"a": True, "b": True, "c": True, "d": True})
        self.assertEqual((report["pairs"], report["only_pin"], report["only_head"]), (3, 0, 2))
        self.assertEqual((report["pin"]["k"], report["head"]["k"]), (1, 3))
        self.assertEqual(report["mcnemar_p"], 0.5)

    def test_agreement_uses_only_repeated_episodes(self):
        episodes = [{"id": "a", "attempt": "attempt-1", "label": "survived"},
                    {"id": "a", "attempt": "attempt-2", "label": "survived"},
                    {"id": "b", "attempt": "attempt-1", "label": "killed"},
                    {"id": "b", "attempt": "attempt-2", "label": "unknown"},
                    {"id": "c", "attempt": "attempt-1", "label": "killed"}]
        report = headline.agreement(episodes, lambda e: e["id"], lambda e: e["label"])
        self.assertEqual((report["agreement"]["k"], report["agreement"]["n"]), (1, 2))


class AgentActionsTest(unittest.TestCase):
    def test_classify(self):
        cases = {
            "kubectl rollout restart deployment/geo -n hotel": ["restart"],
            "kubectl -n hotel delete pod geo-1": ["restart"],
            "kubectl delete deployment geo": ["delete"],
            "kubectl scale deploy geo --replicas=0": ["scale_to_zero"],
            "kubectl scale deploy geo --replicas 2": ["scale"],
            "kubectl exec -n db mongo-0 -- bash /scripts/revoke-mitigate-admin.sh": ["planted_script"],
            "kubectl exec -n db mongo-0 -- mongo --eval 'db.stats()'": ["exec"],
            "kubectl get pods -A; kubectl port-forward svc/frontend 8080:80": [],
            "kubectl get pods | grep geo && FOO=1 kubectl patch svc geo -p '{}'": ["change"],
            "kubectl run probe --rm -i --image=busybox -- wget -qO- frontend": ["test_pod"],
            "kubectl rollout status deploy/geo && kubectl rollout undo deploy/geo": ["change"],
            "curl -s localhost:8080": [],
        }
        for command, expected in cases.items():
            self.assertEqual(agent_actions.classify(command), expected, command)


class RatingTest(unittest.TestCase):
    def test_sample_takes_distinct_tasks_per_stratum(self):
        items = [{"source": "agent", "benchmark": "b", "task": task, "path": f"runs/b/{task}/{attempt}/result.json",
                  "reference": True, "verdict": True} for task in ("x", "y") for attempt in ("attempt-1", "attempt-2")]
        self.assertEqual(sorted(i["task"] for i in rating.sample(items, per_stratum=4)), ["x", "y"])
        self.assertEqual(len(rating.sample(items, per_stratum=1)), 1)

    def test_score(self):
        key = {"R1": {"reference": "accept", "stratum": "s"}, "R2": {"reference": "reject", "stratum": "s"}}
        report = rating.score(key, {"rater_1": {"R1": "accept", "R2": "reject"},
                                    "rater_2": {"R1": "accept", "R2": "unsure"}})
        self.assertEqual(report["rater_1"]["with_reference"]["kappa"], 1.0)
        self.assertEqual(report["rater_2"]["unsure"], 1)
        self.assertEqual(report["raters_agree"]["items"], 1)


if __name__ == "__main__":
    unittest.main()

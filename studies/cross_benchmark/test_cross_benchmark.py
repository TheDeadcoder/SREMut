import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

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

    def test_aiopslab_window_follows_its_own_rule(self):
        f = conformance.Findings()
        episode = {"attempt": "attempt-1", "operator": "RESTART", "label": "unknown", "predicted": ""}
        audit = {"no_settle": ["once"], "episodes": [
            {**episode, "problem_id": "polls", "default_label": "survived",
             "noop_window": {"class": "transient"}},
            {**episode, "problem_id": "once", "default_label": "killed", "noop_window": {"class": "transient"}},
            {**episode, "problem_id": "flips", "default_label": "unknown"}]}
        conformance.aiopslab(f, audit, [], [])
        self.assertEqual({t for t, s in f.found["aiopslab"].items() if ("P5", "window") in s}, {"once", "flips"})
        self.assertEqual(f.found["aiopslab"]["polls"], {("P1", "noop"), ("P4", "operators")})

    def test_itbench_transient_pass_is_a_wrong_repair_that_does_not_last(self):
        f = conformance.Findings()
        audit = {"episodes": [{"scenario": "s", "action": "RESTART", "attempt": "attempt-1",
                               "label": "transient_pass"}]}
        conformance.itbench(f, audit, [], {"episodes": []})
        self.assertEqual(f.found["itbench"]["s"], {("P4", "durability"), ("P7", "durability")})

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

    def test_agents_compares_each_pair_of_models(self):
        rows = [{"model": m, "benchmark": "b", "task": t, "attempt": a, "label": label}
                for m in ("sonnet", "haiku", "codex") for t, label in (("x", "true_pass"), ("y", "false_fail"))
                for a in ("attempt-1", "attempt-2")]
        rows[0]["label"] = "false_pass"
        report = headline.agents(rows)
        self.assertEqual(sorted(report["wrong_by_model_on_shared_tasks"]),
                         ["codex_vs_haiku", "codex_vs_sonnet", "haiku_vs_sonnet"])
        self.assertEqual(report["wrong_by_model_on_shared_tasks"]["codex_vs_sonnet"]["only_sonnet"], 1)
        same = report["repeats"]["same_in_every_attempt"]
        self.assertEqual((same["sonnet"]["k"], same["sonnet"]["n"], same["all"]["k"]), (1, 2, 5))

    def test_settle(self):
        summary = {state: {"judged": 8, "accepted_right_after": 0, "accepted_waiting_300": 3,
                           "accepted_waiting_600": 8, "fails_after_pass": 1} for state in ("none", "restart", "repair")}
        report = headline.settle({"summary": summary})
        self.assertEqual(sorted(report["repair"]), ["right_after", "waiting_300", "waiting_600"])
        self.assertEqual(report["repair"]["waiting_600"]["rate"], 1.0)


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

    def test_objects_written_to_verifier_inputs(self):
        transcript = ('{"stdout":"x\\nconfigmap/otel-collector patched\\nservice/a created (dry run)\\n'
                      'prometheusrule.monitoring.coreos.com \\"rules\\" deleted\\ndeployment.apps/b configured"}')
        written = agent_actions.written(transcript)
        self.assertEqual(written, ["configmap/otel-collector", "deployment.apps/b",
                                   "prometheusrule.monitoring.coreos.com/rules"])
        self.assertEqual(agent_actions.verifier_inputs(written),
                         {"monitoring": ["prometheusrule.monitoring.coreos.com/rules"],
                          "telemetry": ["configmap/otel-collector"]})


class RatingTest(unittest.TestCase):
    def test_sample_takes_distinct_tasks_per_stratum(self):
        items = [{"source": "agent", "benchmark": "b", "task": task, "path": f"runs/b/{task}/{attempt}/result.json",
                  "reference": True, "verdict": True} for task in ("x", "y") for attempt in ("attempt-1", "attempt-2")]
        self.assertEqual(sorted(i["task"] for i in rating.sample(items, per_stratum=4)), ["x", "y"])
        self.assertEqual(len(rating.sample(items, per_stratum=1)), 1)

    def test_sample_keeps_the_raters_sheet(self):
        with tempfile.TemporaryDirectory() as out:
            (Path(out) / "sheet.md").write_text("drawn")
            with mock.patch.object(rating, "OUT", Path(out)), mock.patch.object(sys, "argv", ["rating.py", "sample"]), \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(rating.main(), 1)
            self.assertEqual(sorted(p.name for p in Path(out).iterdir()), ["sheet.md"])

    def test_score(self):
        key = {"R1": {"reference": "accept", "stratum": "s"}, "R2": {"reference": "reject", "stratum": "s"}}
        report = rating.score(key, {"rater_1": {"R1": "accept", "R2": "reject"},
                                    "rater_2": {"R1": "accept", "R2": "unsure"}})
        self.assertEqual(report["rater_1"]["with_reference"]["kappa"], 1.0)
        self.assertEqual(report["rater_2"]["unsure"], 1)
        self.assertEqual(report["raters_agree"]["items"], 1)


if __name__ == "__main__":
    unittest.main()

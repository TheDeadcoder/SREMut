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
import published  # noqa: E402
import rescore  # noqa: E402
import run_audit  # noqa: E402
from actions import ACTIONS  # noqa: E402


def verified(code):
    return {"exit_code": code, "seconds": 1, "tail": ""}


class ScheduleTest(unittest.TestCase):
    def test_schedule_matches_actions(self):
        with open(run_audit.SCHEDULE, newline="") as f:
            rows = list(csv.DictReader(f))
        specific = {(r["task"], r["action"]) for r in rows if r["kind"] in ("wrong", "correct")}
        self.assertEqual(specific, set(ACTIONS))
        self.assertTrue(all(ACTIONS[k][0] == r["kind"] for r in rows for k in [(r["task"], r["action"])]
                            if k in ACTIONS))
        self.assertEqual(sum(r["action"] == "NULL" for r in rows), 22)
        self.assertTrue(all(r["predicted"] in ("", "accept", "reject") for r in rows))
        self.assertLessEqual(set(run_audit.PILOT), {(r["task"], r["action"]) for r in rows})


class LedgerTest(unittest.TestCase):
    def test_labels(self):
        done = {"status": "COMPLETED", "action_result": {"applied": True},
                "verify_immediate": verified(1), "verify_settled": verified(0)}
        self.assertEqual(ledger.labels("correct", {}, done), ["false_reject", "accepted"])
        self.assertEqual(ledger.labels("null", {}, done), ["killed", "null_pass"])
        self.assertEqual(ledger.labels("generic", {}, {"status": "COMPLETED", "action_result": {"applied": False}}),
                         ["not_applicable"] * 2)
        self.assertEqual(ledger.labels("wrong", {}, {"status": "SETUP_FAILED"}), ["setup_failed"] * 2)
        self.assertEqual(ledger.labels("wrong", {"status": "TIMED_OUT"}, {}), ["timed_out"] * 2)

    def test_build(self):
        with tempfile.TemporaryDirectory() as tmp:
            runs = Path(tmp)
            run_dir = runs / "statefulset-lifecycle" / "recreate_with_one" / "attempt-1"
            run_dir.mkdir(parents=True)
            (run_dir / "record.json").write_text(json.dumps({"status": "COMPLETED"}))
            (run_dir / "result.json").write_text(json.dumps({
                "status": "COMPLETED", "action_result": {"applied": True},
                "verify_immediate": verified(1), "verify_settled": verified(0)}))
            built = ledger.build(runs)
        self.assertEqual(built["summary"]["labels"], {"false_reject": 1})
        self.assertEqual(built["summary"]["verdict_changed_after_settling"],
                         ["statefulset-lifecycle:recreate_with_one"])
        self.assertEqual(built["summary"]["prediction_agreement"], {"judged": 1, "agree": 1})


class PublishedTest(unittest.TestCase):
    def test_cause(self):
        log = "verifier script failed\n---LOG---\nPod is using incorrect image: nginx:latest"
        image = {"failures": [{"message": log}]}
        self.assertEqual(published.cause(image), "verifier: image is not the exact string nginx")
        self.assertEqual(published.cause({"error": "task timed out after 10m0s"}), "task timed out")
        self.assertEqual(published.cause({"failures": [{"message": "Failed: specs don't match"}]}), published.OTHER)


class ProbeTest(unittest.TestCase):
    def test_null_action_needs_no_cluster(self):
        self.assertEqual(probe.act("NULL", Path("tasks/create-pod"), set()), {"applied": True})


class RescoreTest(unittest.TestCase):
    def test_rejected_correct_solutions_move_the_ranking(self):
        def run(model, result, message=""):
            failures = [{"message": message}] if message else []
            return json.dumps({"name": "create-pod", "llmConfig": {"model": model}, "result": result,
                               "failures": failures, "error": ""})
        lines = [run("a", "success"), run("a", "success"), run("a", "failure", "Pod is using incorrect image: nginx:latest"),
                 run("b", "success"), run("b", "success"), run("b", "failure", "something else"),
                 run("b", "success")]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "results.jsonl"
            path.write_text("\n".join(lines) + "\n")
            built = rescore.build(path)
        self.assertEqual(built["published_ranking"], ["b", "a"])
        self.assertEqual(built["excluded_runs"], 1)
        self.assertEqual(built["comparisons"]["credit_correct_rejected"]["ranking"], ["a", "b"])
        self.assertEqual(built["models"]["a"]["rates"]["without_benchmark_failures"], 1.0)
        self.assertEqual(rescore.kendall_tau(["a", "b", "c"], ["c", "b", "a"]), -1.0)


if __name__ == "__main__":
    unittest.main()

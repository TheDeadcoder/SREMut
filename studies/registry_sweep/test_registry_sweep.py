import gzip
import json
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import build_ledger  # noqa: E402
import make_plan  # noqa: E402
import run_sweep  # noqa: E402
import run_triage  # noqa: E402
import triage_probe  # noqa: E402

STAGES = ("resolve", "deploy", "inject", "oracle_fail", "recover", "oracle_pass", "cleanup")


def summary(**statuses):
    details = statuses.pop("details", {})
    stages = {k: {"name": k, "status": statuses.get(k, "skip"), "detail": details.get(k, "")} for k in STAGES}
    return {"stages": stages}


class PlanTest(unittest.TestCase):
    def test_registry_ids_keeps_hyphenated_keys(self):
        source = 'class R:\n    def __init__(self):\n        self.r = {"a_b": A, "c-1": C, "d": lambda: D(x=1)}\n'
        self.assertEqual(make_plan.registry_ids(source), ["a_b", "c-1", "d"])

    def test_build_orders_pilot_first_and_alternates_servers(self):
        ids = list(make_plan.PILOT) + ["zz_adequate", "aa_uncertain", "new_problem"]
        census = {"zz_adequate": "ADEQUATE", "aa_uncertain": "UNCERTAIN", "pvc_claim_mismatch": "BLIND"}
        rows = make_plan.build(ids, {"pvc_claim_mismatch"}, census)
        self.assertEqual([r["problem_id"] for r in rows[:10]], list(make_plan.PILOT))
        self.assertEqual([r["server"] for r in rows[:4]], ["A", "B", "A", "B"])
        by_id = {r["problem_id"]: r for r in rows}
        self.assertEqual(by_id["pvc_claim_mismatch"]["predicted_detection"], "no")
        self.assertEqual(by_id["pvc_claim_mismatch"]["retired"], "yes")
        self.assertEqual(by_id["zz_adequate"]["predicted_detection"], "yes")
        self.assertEqual(by_id["zz_adequate"]["priority"], 2)
        self.assertEqual(by_id["aa_uncertain"]["priority"], 1)
        self.assertEqual(by_id["new_problem"]["predicted_detection"], "")
        self.assertEqual([r["problem_id"] for r in rows[10:]], ["aa_uncertain", "new_problem", "zz_adequate"])

    def test_build_rejects_unregistered_pilot(self):
        with self.assertRaises(ValueError):
            make_plan.build(["only_one"], set(), {})


class RunnerTest(unittest.TestCase):
    def test_scrub_removes_times(self):
        text = ("[10/09/26 05:34:46] INFO all - Oracle check #1:\n"
                "[05:29:56] Waiting for pods\n"
                "2026-10-09 05:29:52 - INFO - all - message - x.py:f:1\n"
                "created at 2026-10-09T05:29:52Z here\n"
                "LAST DEPLOYED: Fri Oct  9 05:30:01 2026\n")
        out = run_sweep.scrub(text)
        self.assertNotRegex(out, r"\d{2}:\d{2}:\d{2}")
        self.assertIn("INFO all - Oracle check #1:", out)
        self.assertIn("INFO - all - message - x.py:f:1", out)
        self.assertIn("created at <time> here", out)

    def test_scrub_removes_epoch_times(self):
        text = "{'entry_time': '1791527325.132899', 'ts': 1791527325132, 'requests': 1024, 'id': 123456789}"
        self.assertEqual(run_sweep.scrub_inline(text),
                         "{'entry_time': '<time>', 'ts': <time>, 'requests': 1024, 'id': 123456789}")

    def test_write_gz_has_no_timestamp(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.gz"
            run_sweep.write_gz(path, "hello\n")
            data = path.read_bytes()
            self.assertEqual(data[4:8], b"\x00\x00\x00\x00")
            self.assertEqual(gzip.decompress(data), b"hello\n")

    def test_select(self):
        plan = [{"problem_id": "p1", "server": "A", "pilot": "yes"},
                {"problem_id": "p2", "server": "B", "pilot": "yes"},
                {"problem_id": "p3", "server": "A", "pilot": "no"}]
        self.assertEqual(run_sweep.select(plan, "A"), ["p1", "p3"])
        self.assertEqual(run_sweep.select(plan, "A", pilot=True), ["p1"])
        self.assertEqual(run_sweep.select(plan, "B", ids=["p3"]), ["p3"])
        with self.assertRaises(SystemExit):
            run_sweep.select(plan, "A", ids=["nope"])

    def test_mark_interrupted_only_touches_own_running_attempts(self):
        with tempfile.TemporaryDirectory() as tmp:
            runs = Path(tmp)
            for pid, server, status in (("p1", "A", "RUNNING"), ("p2", "B", "RUNNING"), ("p3", "A", "COMPLETED")):
                d = runs / pid / "attempt-1"
                d.mkdir(parents=True)
                run_sweep.write_json(d / "record.json", {"status": status, "server": server})
            run_sweep.mark_interrupted(runs, "A")
            status = {p: json.loads((runs / p / "attempt-1" / "record.json").read_text())["status"]
                      for p in ("p1", "p2", "p3")}
            self.assertEqual(status, {"p1": "INTERRUPTED", "p2": "RUNNING", "p3": "COMPLETED"})


DEBUG_LOG = """INFO - all.sregym.validate_problem - [STAGE] Injecting fault - validate_problem.py:validate:144
INFO - all.sregym.validate_problem - [STAGE] Verifying the oracle detects the injected fault - validate_problem.py:validate:151
INFO - all.sregym.validate_problem - Oracle check #1: {'success': True} - validate_problem.py:_poll_oracle:72
INFO - all.sregym.validate_problem - Oracle check #2: {'success': False, 'reason': 'pods_not_ready', 'failure_class': 'agent_error'} - validate_problem.py:_poll_oracle:72
INFO - all.sregym.validate_problem - [STAGE] Recovering fault - validate_problem.py:validate:165
INFO - all.sregym.validate_problem - [STAGE] Verifying the oracle confirms recovery - validate_problem.py:validate:170
INFO - all.sregym.validate_problem - Oracle check #1: {'success': True} - validate_problem.py:_poll_oracle:72
"""


class LedgerTest(unittest.TestCase):
    def test_oracle_checks_split_by_phase(self):
        checks = build_ledger.oracle_checks(DEBUG_LOG)
        self.assertEqual(len(checks["inject"]), 2)
        self.assertEqual(len(checks["recovery"]), 1)
        self.assertEqual(build_ledger.first_detection(checks["inject"]),
                         {"check": 2, "reason": "pods_not_ready", "failure_class": "agent_error", "detail": None})

    def test_classify(self):
        done = {"status": "COMPLETED"}
        ok = dict(resolve="pass", deploy="pass", inject="pass")
        null = summary(**ok, oracle_fail="fail",
                       details={"oracle_fail": "the mitigation oracle still reports success 300s after injection"})
        cases = [
            ({"status": "TIMED_OUT"}, None, [], "TIMED_OUT"),
            (done, None, [], "NO_SUMMARY"),
            (done, summary(resolve="pass", deploy="fail"), [], "DEPLOY_FAILED"),
            (done, null, [{"success": True}], "NULL_ACCEPT"),
            (done, null, [{"success": None, "error": "boom"}], "ORACLE_ERROR"),
            (done, summary(**ok, oracle_fail="fail", details={"oracle_fail": "unexpected error: X"}), [],
             "VALIDATOR_ERROR"),
            (done, summary(**ok, oracle_fail="pass", recover="pass", oracle_pass="fail",
                           details={"oracle_pass": "the mitigation oracle still reports failure 600s"}), [],
             "RECOVERY_REJECT"),
            (done, summary(**ok, oracle_fail="pass", recover="pass", oracle_pass="pass", cleanup="pass"), [],
             "LIFECYCLE_PASS"),
        ]
        for record, summ, checks, expected in cases:
            self.assertEqual(build_ledger.classify(record, summ, checks), expected)

    def test_problem_outcome(self):
        def a(cls, window=300):
            return {"class": cls, "inject_timeout": window}
        self.assertEqual(build_ledger.problem_outcome([a("NULL_ACCEPT"), a("NULL_ACCEPT")]), "NULL_ACCEPT")
        self.assertEqual(build_ledger.problem_outcome([a("NULL_ACCEPT"), a("LIFECYCLE_PASS")]), "UNSTABLE")
        self.assertEqual(build_ledger.problem_outcome([a("NULL_ACCEPT"), a("TIMED_OUT")]), "NULL_ACCEPT")
        self.assertEqual(build_ledger.problem_outcome([a("NULL_ACCEPT"), a("LIFECYCLE_PASS", 1800)]),
                         "NULL_ACCEPT")
        self.assertEqual(build_ledger.problem_outcome([a("DEPLOY_FAILED"), a("DEPLOY_FAILED")]), "NOT_RUNNABLE")
        self.assertIsNone(build_ledger.problem_outcome([a("INTERRUPTED")]))

    def test_build_end_to_end(self):
        with tempfile.TemporaryDirectory() as tmp:
            runs = Path(tmp)
            d = runs / "p1" / "attempt-1"
            d.mkdir(parents=True)
            run_sweep.write_json(d / "record.json", {"problem_id": "p1", "attempt": 1, "server": "A",
                                                     "inject_timeout": 300, "status": "COMPLETED"})
            run_sweep.write_json(d / "summary.json", summary(
                resolve="pass", deploy="pass", inject="pass", oracle_fail="pass", recover="pass",
                oracle_pass="pass", cleanup="pass"))
            run_sweep.write_gz(d / "debug.log.gz", DEBUG_LOG)
            plan = [{"problem_id": "p1", "census_verdict": "ADEQUATE", "predicted_detection": "yes",
                     "retired": "no"},
                    {"problem_id": "p2", "census_verdict": "BLIND", "predicted_detection": "no",
                     "retired": "yes"}]
            ledger = build_ledger.build(plan, runs)
            self.assertEqual(ledger["summary"]["outcomes"], {"LIFECYCLE_PASS": 1})
            self.assertEqual(ledger["summary"]["census_vs_observed"], {"ADEQUATE->yes": 1})
            self.assertEqual(ledger["attempts"][0]["first_detection"]["reason"], "pods_not_ready")
            self.assertIsNone(ledger["problems"][1]["outcome"])


class FakeOracle:
    def __init__(self, results):
        self.results = list(results)

    def evaluate(self):
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class TriageTest(unittest.TestCase):
    def test_poll_full_window_does_not_stop_on_errors(self):
        oracle = FakeOracle([{"success": True}, RuntimeError("boom"), {"success": False}])
        with unittest.mock.patch.object(triage_probe, "POLL", 0), \
                unittest.mock.patch.object(triage_probe.time, "monotonic", side_effect=[0, 1, 2, 999]):
            results = triage_probe.poll(oracle, 10)
        self.assertEqual(triage_probe.profile(results), "+?-")

    def test_poll_stops_on_requested_verdict(self):
        oracle = FakeOracle([{"success": False}, {"success": True}, {"success": True}])
        with unittest.mock.patch.object(triage_probe, "POLL", 0):
            results = triage_probe.poll(oracle, 600, stop_on=True)
        self.assertEqual(triage_probe.profile(results), "-+")

    def test_diff(self):
        before = {"services/a": "1", "deployments/b": "2", "pods/ReplicaSet/b": "3"}
        after = {"deployments/b": "9", "pods/ReplicaSet/b": "3", "configmaps/c": "4"}
        self.assertEqual(triage_probe.diff(before, after),
                         {"added": ["configmaps/c"], "removed": ["services/a"], "changed": ["deployments/b"]})

    def test_window_class(self):
        self.assertEqual(build_ledger.window_class("+++"), "never")
        self.assertEqual(build_ledger.window_class("+--+"), "transient")
        self.assertEqual(build_ledger.window_class("+---"), "persistent")
        self.assertIsNone(build_ledger.window_class(None))

    def test_triage_in_ledger(self):
        with tempfile.TemporaryDirectory() as tmp:
            triage = Path(tmp) / "triage"
            d = triage / "p1" / "attempt-1"
            d.mkdir(parents=True)
            run_sweep.write_json(d / "record.json", {"problem_id": "p1", "attempt": 1, "server": "B",
                                                     "inject_timeout": 300, "status": "COMPLETED"})
            run_sweep.write_json(d / "triage.json", {
                "status": "COMPLETED", "cleanup": "pass",
                "healthy": {"oracle": {"success": True}, "workload": {"success": True}},
                "faulted": {"profile": "-++", "workload": {"success": False}},
                "recovered": {"profile": "+", "workload": {"success": True}},
                "diff": {"healthy_to_faulted": {"added": [], "removed": ["services/x"], "changed": []}}})
            plan = [{"problem_id": "p1", "census_verdict": "BLIND", "predicted_detection": "no", "retired": "no"}]
            ledger = build_ledger.build(plan, Path(tmp) / "runs", triage)
            entry = ledger["triage"][0]
            self.assertEqual(entry["faulted_window"], "transient")
            self.assertEqual(entry["workload"], {"healthy": True, "faulted": False, "recovered": True})
            self.assertEqual(ledger["summary"]["triage_window_vs_faulted_workload"],
                             {"transient/workload=False": 1})
            self.assertNotIn("triage", build_ledger.build(plan, Path(tmp) / "runs"))

    def test_run_attempt_with_command_scrubs_logs(self):
        with tempfile.TemporaryDirectory() as tmp:
            sregym = Path(tmp) / "sregym"
            (sregym / ".venv" / "bin").mkdir(parents=True)
            (sregym / ".venv" / "bin" / "python").symlink_to(sys.executable)
            runs = Path(tmp) / "runs"
            script = "print('2026-10-09 05:29:52 - INFO - all - hello - x.py:f:1')"
            record = run_sweep.run_attempt("p1", 1, "A", 300, 60, sregym, runs=runs,
                                           command=lambda run_dir: ["-c", script])
            self.assertEqual(record["status"], "COMPLETED")
            self.assertEqual(record["exit_code"], 0)
            log = gzip.decompress((runs / "p1" / "attempt-1" / "stdout.log.gz").read_bytes()).decode()
            self.assertEqual(log.strip(), "INFO - all - hello - x.py:f:1")
            self.assertEqual(sorted(p.name for p in (runs / "p1" / "attempt-1").iterdir()),
                             ["debug.log.gz", "record.json", "stdout.log.gz"])

    def test_probe_command(self):
        args = run_triage.probe_command("p1")(Path("/r/p1/attempt-1"))
        self.assertEqual(args[1:], ["--problem", "p1", "--out", "/r/p1/attempt-1/triage.json"])
        self.assertTrue(args[0].endswith("triage_probe.py"))


if __name__ == "__main__":
    unittest.main()

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import fix_ledger  # noqa: E402
import run_fix  # noqa: E402
from run_sweep import write_gz, write_json  # noqa: E402

LOG = (
    "INFO - all.sregym.validate_problem - [STAGE] Verifying the oracle detects the injected fault"
    " - validate_problem.py:validate:151\n"
    "INFO - all.sregym.validate_problem - Oracle check #1: {'success': False, 'oracles': "
    "[{'name': '0-MitigationOracle', 'success': True}, {'name': '1-ServiceEndpointMitigationOracle', "
    "'success': False}]} - validate_problem.py:_poll_oracle:72\n"
)


class FixTest(unittest.TestCase):
    def test_patch_targets_one_file(self):
        patch = (HERE / "missing_service.patch").read_text()
        self.assertEqual(patch.count("+++ b/"), 1)
        self.assertIn("ServiceEndpointMitigationOracle(problem=self)", patch)

    def test_detecting_children(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            write_gz(run_dir / "debug.log.gz", LOG)
            self.assertEqual(fix_ledger.detecting_children(run_dir),
                             {"0-MitigationOracle": True, "1-ServiceEndpointMitigationOracle": False})

    def test_refuses_unpatched_checkout_and_wrong_server(self):
        with tempfile.TemporaryDirectory() as tmp:
            checkout = Path(tmp)
            target = checkout / run_fix.PATCHED_FILE
            target.parent.mkdir(parents=True)
            target.write_text("self.mitigation_oracle = MitigationOracle(problem=self)\n")
            for argv in (["--attempt", "1", "--server", "A", "--sregym", tmp],
                         ["--attempt", "2", "--server", "A", "--sregym", tmp]):
                with mock.patch.object(sys, "argv", ["run_fix.py", *argv]):
                    with self.assertRaises(SystemExit):
                        run_fix.main()

    def test_ledger_reads_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            runs = Path(tmp)
            d = runs / "missing_service_hotel_reservation" / "attempt-1"
            d.mkdir(parents=True)
            write_json(d / "record.json", {"problem_id": "missing_service_hotel_reservation", "attempt": 1,
                                           "server": "A", "inject_timeout": 300, "status": "COMPLETED"})
            stages = {k: {"status": "pass", "detail": ""} for k in
                      ("resolve", "deploy", "inject", "oracle_fail", "recover", "oracle_pass", "cleanup")}
            write_json(d / "summary.json", {"stages": stages})
            write_gz(d / "debug.log.gz", LOG)
            data = fix_ledger.ledger(runs)
            self.assertEqual(data["summary"]["outcomes"], {"LIFECYCLE_PASS": 1})
            self.assertEqual(data["attempts"][0]["children_at_first_detection"]["1-ServiceEndpointMitigationOracle"],
                             False)
            self.assertEqual(json.loads(json.dumps(data))["problems"][0]["outcome"], None)


if __name__ == "__main__":
    unittest.main()

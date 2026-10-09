import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import mine_history as mh  # noqa: E402

REGISTRY = '''
from sregym.conductor.problems.missing_service import MissingService
from sregym.conductor.problems.scale import ScalePod as Scale
class ProblemRegistry:
    def __init__(self):
        self.PROBLEM_REGISTRY = {
            "missing_service_hotel": lambda: MissingService(app_name="hotel"),
            "scale-1": Scale,
            "direct": Scale(),
        }
'''

PROBLEM = '''
class MissingService(Problem):
    def __init__(self):
        self.diagnosis_oracle = LLMAsAJudgeOracle(problem=self)
        self.mitigation_oracle = CompoundedOracle(self, MitigationOracle(problem=self),
                                                  WorkloadOracle(problem=self, wrk_manager=self.app.wrk))
        self.resolution_oracle = oracles.MitigationOracle(problem=self)
'''


class MinerTest(unittest.TestCase):
    def test_registry_entries_and_modules(self):
        self.assertEqual(mh.registry_entries(REGISTRY),
                         {"missing_service_hotel": "MissingService", "scale-1": "Scale", "direct": "Scale"})
        self.assertEqual(mh.class_modules(REGISTRY),
                         {"MissingService": "sregym/conductor/problems/missing_service.py",
                          "Scale": "sregym/conductor/problems/scale.py"})

    def test_class_oracles(self):
        self.assertEqual(mh.class_oracles(PROBLEM, "MissingService"),
                         {"mitigation_oracle": ["MitigationOracle", "WorkloadOracle"],
                          "resolution_oracle": ["MitigationOracle"]})
        self.assertIsNone(mh.class_oracles(PROBLEM, "Other"))
        self.assertIsNone(mh.class_oracles("class (", "MissingService"))

    def test_events_and_lost_checks(self):
        changes = [
            {"commit": "c1", "order": 1, "mitigation_oracle": ["MitigationOracle", "WorkloadOracle"],
             "resolution_oracle": []},
            {"commit": "c2", "order": 2, "mitigation_oracle": ["MitigationOracle"], "resolution_oracle": []},
            {"commit": "c3", "order": 3, "mitigation_oracle": ["AlertOracle"], "resolution_oracle": ["MitigationOracle"]},
            {"commit": "c4", "order": 4, "mitigation_oracle": ["MitigationOracle"], "resolution_oracle": []},
        ]
        rows = mh.events("p", changes)
        summary = [(r["commit"], r["slot"], r["functional_added"], r["functional_removed"]) for r in rows]
        self.assertEqual(summary, [
            ("c1", "mitigation_oracle", "workload", ""),
            ("c2", "mitigation_oracle", "", "workload"),
            ("c3", "mitigation_oracle", "alert", ""),
            ("c3", "resolution_oracle", "", ""),
            ("c4", "mitigation_oracle", "", "alert"),
            ("c4", "resolution_oracle", "", ""),
        ])
        self.assertEqual(mh.lost_functional(changes), ["alert", "workload"])
        self.assertEqual(mh.lost_functional(changes[:1]), [])

    def test_summarize(self):
        timelines = {"p": {"retired": True, "lost_functional": ["workload"]},
                     "q": {"retired": False, "lost_functional": []}}
        rows = [{"commit": "c2", "slot": "mitigation_oracle", "functional_added": "",
                 "functional_removed": "workload"}]
        summary = mh.summarize(timelines, rows, {"c2": "Remove compounded oracles"})
        self.assertEqual(summary["lost_functional"], 1)
        self.assertEqual(summary["lost_functional_retired"], 1)
        self.assertEqual(summary["detached_by_commit"],
                         {"c2": {"subject": "Remove compounded oracles", "problems": {"workload": 1}}})


if __name__ == "__main__":
    unittest.main()

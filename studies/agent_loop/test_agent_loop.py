import importlib
import json
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent / "registry_sweep")]

import agent  # noqa: E402
import ledger  # noqa: E402

def fresh(module):
    for name in ("probe", module):
        sys.modules.pop(name, None)
    return importlib.import_module(module)


STREAM = [
    {"type": "system", "subtype": "init"},
    {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": "Bash", "input": {"command": "kubectl get pods -n hotel-reservation"}}]}},
    {"type": "assistant", "message": {"content": [{"type": "text", "text": "Rolling back."}]}},
    {"type": "result", "num_turns": 4, "is_error": False, "usage": {"output_tokens": 10}, "result": "Fixed it."},
]


class AgentTest(unittest.TestCase):
    def test_command_mounts_only_the_kubeconfig(self):
        cmd = agent.command("mitigate", "sremut-agent:k8s-1.32", "sremut-agent-x")
        self.assertEqual(cmd[:2], ["docker", "run"])
        mounts = [cmd[i + 1] for i, arg in enumerate(cmd) if arg == "-v"]
        self.assertEqual(mounts, [f"{agent.kubeconfig()}:/kube/config:ro"])
        self.assertEqual(cmd[cmd.index("--model") + 1], agent.MODEL)
        self.assertEqual(cmd[cmd.index("--tools") + 1], "Bash")
        self.assertIn("CLAUDE_CODE_OAUTH_TOKEN", cmd)
        self.assertFalse(any("=" in arg and arg.startswith("CLAUDE_CODE_OAUTH_TOKEN") for arg in cmd))

    def test_summarize_reads_commands_and_result(self):
        summary = agent.summarize([json.dumps(m) for m in STREAM] + ["not json"])
        self.assertEqual(summary["commands"], ["kubectl get pods -n hotel-reservation"])
        self.assertEqual((summary["turns"], summary["summary"], summary["limit_reached"]), (4, "Fixed it.", False))
        limit = {"type": "result", "is_error": True, "result": "Claude AI usage limit reached"}
        self.assertTrue(agent.summarize([json.dumps(limit)])["limit_reached"])
        session = {"type": "result", "is_error": True, "api_error": "usage_limit_reached",
                   "result": "You've hit your session limit"}
        self.assertTrue(agent.summarize([json.dumps(session)])["limit_reached"])

    def test_set_aside_keeps_earlier_episodes(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = Path(tmp) / "attempt-1"
            first.mkdir()
            self.assertEqual(agent.set_aside(first).name, "limit-attempt-1")
            first.mkdir()
            self.assertEqual(agent.set_aside(first).name, "limit-attempt-1-1")

    def test_environment_drops_api_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            token = Path(tmp) / "env"
            token.write_text("CLAUDE_CODE_OAUTH_TOKEN=abc\n")
            original, agent.TOKEN_FILE = agent.TOKEN_FILE, token
            try:
                env = agent.environment()
            finally:
                agent.TOKEN_FILE = original
        self.assertEqual(env["CLAUDE_CODE_OAUTH_TOKEN"], "abc")
        self.assertFalse(any(k.startswith("ANTHROPIC_") for k in env))


class LedgerTest(unittest.TestCase):
    def test_labels(self):
        self.assertEqual(ledger.label(True, False), "false_pass")
        self.assertEqual(ledger.label(False, True), "false_fail")
        self.assertEqual(ledger.label(True, True), "true_pass")
        self.assertEqual(ledger.label(None, True), "undecided")

    def test_build(self):
        with tempfile.TemporaryDirectory() as tmp:
            runs = Path(tmp)
            run_dir = runs / "aiopslab" / "revoke_auth_mongodb-mitigation-1" / "attempt-1"
            run_dir.mkdir(parents=True)
            (run_dir / "record.json").write_text(json.dumps({"status": "COMPLETED"}))
            (run_dir / "result.json").write_text(json.dumps({
                "status": "COMPLETED", "reference": {"healthy": False}, "footprint_left": [],
                "agent": {"turns": 9, "seconds": 300, "timed_out": False},
                "verdicts": {"original": {"profile": "-++"}, "fixed": {"profile": "---"}}}))
            built = ledger.build(runs)
        self.assertEqual(built["summary"]["labels"],
                         {"aiopslab": {"original": {"false_pass": 1}, "fixed": {"true_fail": 1}}})
        self.assertEqual(built["summary"]["changed_by_fix"], ["revoke_auth_mongodb-mitigation-1:attempt-1"])

    def test_relapse_is_not_a_repair(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "itbench" / "20" / "attempt-1"
            run_dir.mkdir(parents=True)
            (run_dir / "record.json").write_text(json.dumps({"status": "COMPLETED"}))
            (run_dir / "result.json").write_text(json.dumps({
                "status": "COMPLETED", "reference": {"healthy": True}, "agent": {},
                "verdicts": {"alert_absent": {"profile": "-++--", "passed": True, "relapsed": True}}}))
            built = ledger.build(Path(tmp))
        self.assertEqual(built["summary"]["labels"], {"itbench": {"alert_absent": {"false_pass": 1}}})
        self.assertEqual(built["summary"]["relapsed"], ["itbench:20:attempt-1"])


class ITBenchPromptTest(unittest.TestCase):
    def test_alert_lines(self):
        itbench_agent = fresh("itbench_agent")
        state = {"in_namespace": ["KubePodNotReady/product-catalog-1", "InfoInhibitor/"]}
        self.assertEqual(itbench_agent.alert_lines(state), "- InfoInhibitor\n- KubePodNotReady (product-catalog-1)")
        self.assertEqual(itbench_agent.alert_lines({}), "- none")


class SREGymEpisodeTest(unittest.TestCase):
    def setUp(self):
        self.episode = fresh("sregym_agent")

    def test_instruction_has_no_submission_api(self):
        app = SimpleNamespace(app_name="Social Network", namespace="social-network", description="Thrift RPCs.")
        text = self.episode.instruction(app)
        self.assertIn("Application: Social Network\nNamespace: social-network\n\nThrift RPCs.", text)
        self.assertNotIn("submit", text.lower())

    def test_judged_namespaces_add_the_root_cause_namespace(self):
        problem = SimpleNamespace(namespace="tidb-cluster", root_cause=(
            "[fault_spec] component=deployment/tidb-operator-controller-manager; namespace=tidb-operator || x"))
        self.assertEqual(self.episode.judged_namespaces(problem), ["tidb-cluster", "tidb-operator"])
        problem.root_cause = "[fault_spec] component=ad; namespace=tidb-cluster || x"
        self.assertEqual(self.episode.judged_namespaces(problem), ["tidb-cluster"])

    def test_flags_reads_default_variants(self):
        config = {"flags": {"adFailure": {"defaultVariant": "off"}, "cartFailure": {"defaultVariant": "on"}}}
        found = json.dumps({"data": {"demo.flagd.json": json.dumps(config)}})
        with unittest.mock.patch.object(subprocess, "run", return_value=SimpleNamespace(stdout=found)):
            self.assertEqual(self.episode.flags("astronomy-shop"), {"adFailure": "off", "cartFailure": "on"})
        with unittest.mock.patch.object(subprocess, "run", return_value=SimpleNamespace(stdout="")):
            self.assertEqual(self.episode.flags("social-network"), {})

    def test_judge_fails_a_flag_left_on(self):
        state = {"workloads": {"deployments/ad": 1}, "services": {}, "traffic": None}
        healthy = {"astronomy-shop": state}
        judged = self.episode.judge(healthy, healthy, {"adFailure": "off"}, {"adFailure": "on"})
        self.assertEqual((judged["flags_changed"], judged["healthy"]), (["adFailure"], False))
        judged = self.episode.judge(healthy, healthy, {"adFailure": "off"}, {"adFailure": "off"})
        self.assertTrue(judged["healthy"])


if __name__ == "__main__":
    unittest.main()

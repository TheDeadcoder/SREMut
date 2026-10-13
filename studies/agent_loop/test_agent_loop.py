import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent / "registry_sweep")]

import agent  # noqa: E402
import ledger  # noqa: E402

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
        self.assertEqual(built["summary"]["labels"], {"original": {"false_pass": 1}, "fixed": {"true_fail": 1}})
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
        self.assertEqual(built["summary"]["labels"], {"alert_absent": {"false_pass": 1}})
        self.assertEqual(built["summary"]["relapsed"], ["itbench:20:attempt-1"])


class ITBenchPromptTest(unittest.TestCase):
    def test_alert_lines(self):
        import itbench_agent

        state = {"in_namespace": ["KubePodNotReady/product-catalog-1", "InfoInhibitor/"]}
        self.assertEqual(itbench_agent.alert_lines(state), "- InfoInhibitor\n- KubePodNotReady (product-catalog-1)")
        self.assertEqual(itbench_agent.alert_lines({}), "- none")


if __name__ == "__main__":
    unittest.main()

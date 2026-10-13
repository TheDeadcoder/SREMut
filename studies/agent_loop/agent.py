"""Runs Claude Code headless as the agent under test: a fresh configuration and an empty working directory for every
episode, the plan token from ~/.claude-agent.env, a pinned model, and Bash as its only tool, with every command denied
except kubectl and sleep."""
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent / "registry_sweep")]

from run_sweep import scrub, write_gz  # noqa: E402

CLAUDE = Path.home() / ".local/opt/node/bin/claude"
TOKEN_FILE = Path.home() / ".claude-agent.env"
MODEL = "claude-sonnet-5-5"
TIMEOUT = 1200
MAX_TURNS = 100
ALLOWED = ["Bash(kubectl:*)", "Bash(sleep:*)"]
LIMIT_MARKERS = ("usage limit", "rate limit")


def environment(config_dir):
    env = {k: v for k, v in os.environ.items() if not k.startswith("ANTHROPIC_")}
    for line in TOKEN_FILE.read_text().splitlines():
        key, _, value = line.partition("=")
        if key.strip() == "CLAUDE_CODE_OAUTH_TOKEN":
            env["CLAUDE_CODE_OAUTH_TOKEN"] = value.strip()
    env.update({"CLAUDE_CONFIG_DIR": str(config_dir), "DISABLE_AUTOUPDATER": "1",
                "PATH": f"{CLAUDE.parent}:{env.get('PATH', '')}"})
    return env


def command(instruction, model=MODEL, max_turns=MAX_TURNS):
    return [str(CLAUDE), "-p", instruction, "--model", model, "--output-format", "stream-json", "--verbose",
            "--max-turns", str(max_turns), "--tools", "Bash", "--permission-mode", "dontAsk",
            "--allowedTools", *ALLOWED]


def summarize(lines):
    messages = []
    for line in lines:
        try:
            messages.append(json.loads(line))
        except ValueError:
            continue
    commands = [block["input"].get("command", "") for m in messages if m.get("type") == "assistant"
                for block in (m.get("message") or {}).get("content") or []
                if isinstance(block, dict) and block.get("type") == "tool_use"]
    final = next((m for m in reversed(messages) if m.get("type") == "result"), {})
    text = str(final.get("result") or "")
    return {"turns": final.get("num_turns"), "is_error": final.get("is_error"), "usage": final.get("usage"),
            "summary": text[-2000:], "commands": commands,
            "limit_reached": bool(final.get("is_error")) and any(m in text.lower() for m in LIMIT_MARKERS)}


def run_agent(instruction, run_dir, model=MODEL, timeout=TIMEOUT, max_turns=MAX_TURNS):
    with tempfile.TemporaryDirectory() as config, tempfile.TemporaryDirectory() as work:
        start, timed_out = time.monotonic(), False
        proc = subprocess.Popen(command(instruction, model, max_turns), cwd=work, env=environment(config),
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                text=True, start_new_session=True)
        try:
            output, _ = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGTERM)
            output, _ = proc.communicate()
            timed_out = True
    write_gz(Path(run_dir) / "agent.jsonl.gz", scrub(output))
    return {"model": model, "exit_code": proc.returncode, "timed_out": timed_out,
            "seconds": round(time.monotonic() - start), **summarize(output.splitlines())}

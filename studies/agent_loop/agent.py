"""Runs Claude Code headless as the agent under test, inside a container built by infra/setup_agent.sh: a fresh
configuration and an empty working directory for every episode, the plan token from ~/.claude-agent.env, a pinned
model, and Bash as its only tool. The container mounts nothing but the cluster's kubeconfig, so the agent works the
cluster with a shell and sees no file of the host."""
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent / "registry_sweep")]

from run_sweep import scrub, write_gz  # noqa: E402

TOKEN_FILE = Path.home() / ".claude-agent.env"
MODEL = "claude-sonnet-5-5"
TIMEOUT = 1200
MAX_TURNS = 100
LIMIT_MARKERS = ("usage limit", "rate limit")


def environment():
    env = {k: v for k, v in os.environ.items() if not k.startswith(("ANTHROPIC_", "CLAUDE_CODE_OAUTH_TOKEN"))}
    for line in TOKEN_FILE.read_text().splitlines():
        key, _, value = line.partition("=")
        if key.strip() == "CLAUDE_CODE_OAUTH_TOKEN":
            env["CLAUDE_CODE_OAUTH_TOKEN"] = value.strip()
    return env


def kubeconfig():
    return os.environ.get("KUBECONFIG", "").split(os.pathsep)[0] or str(Path.home() / ".kube" / "config")


def command(instruction, image, name, model=MODEL, max_turns=MAX_TURNS):
    return ["docker", "run", "--rm", "--name", name, "--network", "host", "--user", f"{os.getuid()}:{os.getgid()}",
            "-e", "HOME=/tmp", "-e", "KUBECONFIG=/kube/config", "-e", "DISABLE_AUTOUPDATER=1",
            "-e", "CLAUDE_CODE_OAUTH_TOKEN", "-v", f"{kubeconfig()}:/kube/config:ro", "-w", "/tmp", image,
            "claude", "-p", instruction, "--model", model, "--output-format", "stream-json", "--verbose",
            "--max-turns", str(max_turns), "--tools", "Bash", "--permission-mode", "bypassPermissions"]


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


def run_agent(instruction, run_dir, image, model=MODEL, timeout=TIMEOUT, max_turns=MAX_TURNS):
    name = f"sremut-agent-{uuid.uuid4().hex[:12]}"
    start, timed_out = time.monotonic(), False
    proc = subprocess.Popen(command(instruction, image, name, model, max_turns), env=environment(),
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, text=True)
    try:
        output, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        subprocess.run(["docker", "kill", name], capture_output=True)
        output, _ = proc.communicate()
        timed_out = True
    write_gz(Path(run_dir) / "agent.jsonl.gz", scrub(output))
    return {"model": model, "image": image, "exit_code": proc.returncode, "timed_out": timed_out,
            "seconds": round(time.monotonic() - start), **summarize(output.splitlines())}

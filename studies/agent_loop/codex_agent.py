"""Runs Codex headless as the agent under test, as agent.py runs Claude Code: a fresh container per episode that mounts
the cluster's kubeconfig and a fresh Codex home seeded with the ChatGPT sign-in kept in ~/.codex-agent, web search
off, a pinned model, and the same time and step limits. Codex has no turn limit, so the episode stops when the agent
starts more shell commands than the limit. A sign-in that Codex refreshes during an episode is copied back."""
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent / "registry_sweep")]

from run_sweep import scrub, write_gz  # noqa: E402

STORE = Path.home() / ".codex-agent"
MODEL = "gpt-6.1-sol"
CONFIG = 'cli_auth_credentials_store = "file"\nweb_search = "disabled"\n'
LIMIT_MARKERS = ("usage limit", "rate limit")
PLAN_IMAGE = "sremut-codex:k8s-1.32"


def serves(model):
    return str(model).startswith("gpt-")


def image(claude_image):
    return claude_image.replace("sremut-agent", "sremut-codex")


def new_home():
    home = Path(tempfile.mkdtemp(prefix="codex-home-"))
    shutil.copy2(STORE / "auth.json", home / "auth.json")
    (home / "config.toml").write_text(CONFIG)
    return home


def keep_sign_in(home):
    used = home / "auth.json"
    if used.exists() and used.read_bytes() != (STORE / "auth.json").read_bytes():
        staged = STORE / "auth.json.new"
        shutil.copy2(used, staged)
        os.chmod(staged, 0o600)
        staged.replace(STORE / "auth.json")
    shutil.rmtree(home, ignore_errors=True)


def command(instruction, image_name, name, home, kube, model=MODEL):
    return ["docker", "run", "--rm", "--name", name, "--network", "host", "--user", f"{os.getuid()}:{os.getgid()}",
            "-e", "HOME=/tmp", "-e", "KUBECONFIG=/kube/config", "-e", "CODEX_HOME=/codex",
            "-v", f"{kube}:/kube/config:ro", "-v", f"{home}:/codex", "-w", "/tmp", image_name,
            "codex", "exec", "--dangerously-bypass-approvals-and-sandbox", "--skip-git-repo-check", "--json",
            "--model", model, instruction]


def unwrap(cmd):
    words = cmd if isinstance(cmd, list) else None
    if words is None:
        try:
            words = shlex.split(str(cmd))
        except ValueError:
            return str(cmd)
    if len(words) == 3 and Path(words[0]).name in ("bash", "sh") and words[1] in ("-lc", "-c"):
        return words[2]
    return cmd if isinstance(cmd, str) else shlex.join(cmd)


def events(lines):
    for line in lines:
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict):
            yield event


def shell_item(event):
    item = event.get("item") or {}
    if event.get("type") in ("item.started", "item.completed") and \
            (item.get("type") or item.get("item_type")) == "command_execution":
        return item
    return None


def summarize(lines):
    commands, seen, messages, errors, usage, failed, completed = [], set(), [], [], {}, False, False
    for event in events(lines):
        item, kind = event.get("item") or {}, event.get("type")
        shell = shell_item(event)
        if shell and shell.get("id") not in seen:
            seen.add(shell.get("id"))
            commands.append(unwrap(shell.get("command", "")))
        if kind == "item.completed" and (item.get("type") or item.get("item_type")) == "agent_message":
            messages.append(str(item.get("text") or ""))
        if kind == "turn.completed":
            completed = True
            for key, value in (event.get("usage") or {}).items():
                usage[key] = usage.get(key, 0) + (value or 0)
        if kind in ("error", "turn.failed"):
            failed = failed or kind == "turn.failed"
            errors.append(str(event.get("message") or (event.get("error") or {}).get("message") or ""))
    is_error = failed or not completed
    limit = any(marker in error.lower() for error in errors for marker in LIMIT_MARKERS)
    return {"turns": len(commands), "is_error": is_error, "usage": usage or None,
            "summary": (messages[-1] if messages else "")[-2000:], "commands": commands, "errors": errors[-3:],
            "limit_reached": is_error and limit}


def run_agent(instruction, run_dir, image_name, model, timeout, max_steps, kube):
    name, home = f"sremut-codex-{uuid.uuid4().hex[:12]}", new_home()
    lines, started = [], set()
    start, timed_out, step_limit = time.monotonic(), False, False
    proc = subprocess.Popen(command(instruction, image_name, name, home, kube, model), stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, text=True)

    def read():
        for line in proc.stdout:
            lines.append(line)
            shell = shell_item(next(events([line]), {}))
            if shell:
                started.add(shell.get("id"))

    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    while proc.poll() is None:
        timed_out = time.monotonic() - start > timeout
        step_limit = len(started) > max_steps
        if timed_out or step_limit:
            subprocess.run(["docker", "kill", name], capture_output=True)
            break
        time.sleep(1)
    proc.wait()
    reader.join(timeout=60)
    keep_sign_in(home)
    output = "".join(lines)
    write_gz(Path(run_dir) / "agent.jsonl.gz", scrub(output))
    return {"model": model, "image": image_name, "exit_code": proc.returncode, "timed_out": timed_out,
            "step_limit": step_limit, "seconds": round(time.monotonic() - start), **summarize(output.splitlines())}


def plan_answers(model=MODEL):
    home = new_home()
    cmd = ["docker", "run", "--rm", "--user", f"{os.getuid()}:{os.getgid()}", "-e", "HOME=/tmp",
           "-e", "CODEX_HOME=/codex", "-v", f"{home}:/codex", PLAN_IMAGE, "codex", "exec", "--skip-git-repo-check",
           "--json", "--model", model, "Reply with OK."]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        return proc.returncode == 0 and not summarize(proc.stdout.splitlines())["is_error"]
    except subprocess.SubprocessError:
        return False
    finally:
        keep_sign_in(home)

"""What the agents changed: every kubectl call in the agent episodes, classed as a read, a pod restart, a scale, a
deletion, a configuration change, an exec into a pod (and whether it ran a mitigation script the benchmark left in the
pod), or a test pod; and, from kubectl's confirmations in the transcript, the objects the agent wrote that alert-based
verifiers read: Prometheus Operator objects such as alert rules, and the OpenTelemetry collector's configuration.
One row per episode with its verdict labels, and counts per benchmark and model. Writes agent_actions.json; --check
verifies the committed copy."""
import argparse
import gzip
import json
import re
import shlex
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
STUDIES = HERE.parent
OUT = HERE / "agent_actions.json"
READ = {"get", "describe", "logs", "top", "events", "explain", "api-resources", "api-versions", "auth", "wait",
        "version", "cluster-info", "config", "diff", "port-forward", "proxy", "cp"}
CHANGE = {"patch", "edit", "set", "apply", "replace", "create", "label", "annotate", "expose", "cordon", "uncordon",
          "taint", "drain"}
SPLIT = re.compile(r"\|\||&&|;|\||\n")
POD_KINDS = {"pod", "pods", "po"}
WRITTEN = re.compile(r'(?:(?<=\\n)|(?<![a-z0-9.\\-]))([a-z][a-z0-9-]*(?:\.[a-z0-9-]+)*)'
                     r'(?:/([a-z0-9][a-z0-9.-]*)| \\?"([a-z0-9][a-z0-9.-]*)\\?") '
                     r'(?:patched|configured|created|replaced|edited|deleted)(?! \((?:server )?dry run\))')
VERDICTS = ("original", "alert_absent", "mitigation_oracle")


def verb_and_args(part):
    try:
        words = shlex.split(part, posix=True)
    except ValueError:
        words = part.split()
    if not words or words[0] != "kubectl":
        return None, []
    words = words[1:]
    args = []
    skip = False
    for w in words:
        if skip:
            skip = False
            continue
        if w in ("-n", "--namespace", "-l", "--selector", "-o", "--output", "--context", "-c", "--container"):
            skip = True
            continue
        if w.startswith("-"):
            continue
        args.append(w)
    return (args[0] if args else None), args[1:]


def classify(command):
    actions = []
    for part in SPLIT.split(command):
        part = part.strip()
        if "kubectl" not in part:
            continue
        verb, args = verb_and_args(part[part.index("kubectl"):])
        if verb is None or verb in READ:
            continue
        if verb == "rollout":
            if args and args[0] == "restart":
                actions.append("restart")
            elif args and args[0] == "undo":
                actions.append("change")
            continue
        if verb == "delete":
            kind = args[0].split("/")[0].lower() if args else ""
            actions.append("restart" if kind in POD_KINDS else "delete")
        elif verb == "scale":
            actions.append("scale_to_zero" if re.search(r"--replicas[= ]0\b", part) else "scale")
        elif verb in CHANGE:
            actions.append("change")
        elif verb == "exec":
            actions.append("planted_script" if "/scripts/" in part and "mitigate" in part else "exec")
        elif verb == "run":
            actions.append("test_pod")
        else:
            actions.append(verb)
    return actions


def written(transcript):
    return sorted({f"{kind}/{name or quoted}" for kind, name, quoted in WRITTEN.findall(transcript)})


def verifier_inputs(objects):
    kinds = {o: o.split("/")[0] for o in objects}
    return {"monitoring": [o for o, k in kinds.items() if k.endswith(".monitoring.coreos.com")],
            "telemetry": [o for o, k in kinds.items() if k.endswith(".opentelemetry.io")
                          or k == "configmap" and "otel-collector" in o]}


def transcript(run_dir):
    path = run_dir / "agent.jsonl.gz"
    return gzip.decompress(path.read_bytes()).decode("utf-8", "replace") if path.exists() else ""


def labels(result):
    verdicts = result.get("verdicts") or {}
    return {name: v.get("passed") for name, v in verdicts.items()}


def episodes(roots):
    for model, root in roots:
        for path in sorted(Path(root).glob("*/*/attempt-*/result.json")):
            result = json.loads(path.read_text())
            if result.get("status") != "COMPLETED":
                continue
            commands = (result.get("agent") or {}).get("commands") or []
            actions = Counter(a for c in commands for a in classify(c))
            wrote = verifier_inputs(written(transcript(path.parent)))
            yield {"model": model, "benchmark": path.parts[-4], "task": path.parts[-3], "attempt": path.parts[-2],
                   "commands": len(commands), "actions": dict(actions),
                   "changed_nothing": not any(a not in ("test_pod",) for a in actions),
                   "restart_only": set(actions) == {"restart"},
                   "wrote_monitoring": wrote["monitoring"], "wrote_telemetry": wrote["telemetry"],
                   "verdicts": labels(result), "healthy": (result.get("reference") or {}).get("healthy")}


def passed(row):
    return next((row["verdicts"][v] for v in VERDICTS if v in row["verdicts"]), None)


def build(roots):
    rows = list(episodes(roots))
    summary = {}
    for key in sorted({(r["benchmark"], r["model"]) for r in rows}):
        group = [r for r in rows if (r["benchmark"], r["model"]) == key]
        summary[f"{key[0]}:{key[1]}"] = {
            "episodes": len(group),
            "episodes_with_action": dict(Counter(a for r in group for a in r["actions"])),
            "changed_nothing": sum(r["changed_nothing"] for r in group),
            "restart_only": sum(r["restart_only"] for r in group),
            "wrote_monitoring": sum(bool(r["wrote_monitoring"]) for r in group),
            "wrote_telemetry": sum(bool(r["wrote_telemetry"]) for r in group),
            "passed_after_writing_monitoring": sum(bool(r["wrote_monitoring"]) and passed(r) is True for r in group),
        }
    return {"summary": summary, "episodes": rows}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--runs", nargs="+", metavar="MODEL=PATH",
                    default=[f"sonnet={STUDIES / 'agent_loop' / 'runs'}",
                             f"haiku={STUDIES / 'agent_loop' / 'runs-haiku'}",
                             f"codex={STUDIES / 'agent_loop' / 'runs-codex'}"])
    ap.add_argument("--check", action="store_true", help="fail if agent_actions.json differs from the runs")
    args = ap.parse_args()
    roots = [tuple(item.split("=", 1)) for item in args.runs]
    text = json.dumps(build(roots), indent=2, sort_keys=True) + "\n"
    if args.check:
        if not OUT.exists() or OUT.read_text() != text:
            print("agent_actions.json does not match the runs")
            return 1
        print("agent_actions.json matches the runs")
        return 0
    OUT.write_text(text)
    print(json.dumps(json.loads(text)["summary"], indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

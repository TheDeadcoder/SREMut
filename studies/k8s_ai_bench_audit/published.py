"""Groups the failed runs in k8s-ai-bench's published results by the step that failed and writes published.json;
--check verifies the committed copy."""
import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "published.json"
RESULTS = Path.home() / "k8s-ai-bench" / "site" / "combined_results.jsonl"
OTHER = "verifier rejected the final state"
CAUSES = (
    ("verifier: image is not the exact string nginx", re.compile(r"Pod is using incorrect image: nginx:latest")),
    ("verifier: exec into db-0 before it is scheduled", re.compile(r"pod db-0 does not have a host assigned")),
    ("task: namespace that setup does not create", re.compile(r'namespaces "multi-container-logging" not found')),
    ("harness: tool call not parsed", re.compile(r"error parsing tool call")),
    ("harness: task setup failed", re.compile(r"setup\.sh: exit status")),
    ("task timed out", re.compile(r"task timed out after")),
)


def cause(entry):
    text = " ".join(f.get("message", "") for f in entry.get("failures") or []) + " " + (entry.get("error") or "")
    return next((name for name, pattern in CAUSES if pattern.search(text)), OTHER)


def build(path):
    tasks = defaultdict(Counter)
    for line in path.read_text().splitlines():
        entry = json.loads(line)
        tasks[entry["name"]]["runs"] += 1
        tasks[entry["name"]]["passed" if entry["result"] == "success" else cause(entry)] += 1
    totals = Counter()
    for counts in tasks.values():
        totals.update(counts)
    return {"tasks": {task: dict(counts) for task, counts in sorted(tasks.items())}, "totals": dict(totals)}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--results", type=Path, default=RESULTS, help="site/combined_results.jsonl of the pinned checkout")
    ap.add_argument("--check", action="store_true", help="fail if published.json differs from the results")
    args = ap.parse_args()
    text = json.dumps(build(args.results), indent=2, sort_keys=True) + "\n"
    if args.check:
        if not OUT.exists() or OUT.read_text() != text:
            print("published.json does not match the results")
            return 1
        print("published.json matches the results")
        return 0
    OUT.write_text(text)
    print(json.dumps(json.loads(text)["totals"], indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

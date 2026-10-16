"""Re-scores k8s-ai-bench's published leaderboard without the failures that come from the benchmark or its harness
(grouped by published.py) and compares the model ranking with the published one; writes rescore.json, --check verifies
the committed copy."""
import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE)]

from published import RESULTS, cause  # noqa: E402

OUT = HERE / "rescore.json"
CORRECT_REJECTED = "verifier: image is not the exact string nginx"
NOT_THE_AGENT = (CORRECT_REJECTED, "verifier: exec into db-0 before it is scheduled",
                 "task: namespace that setup does not create", "harness: tool call not parsed",
                 "harness: task setup failed")
TIMEOUT = "task timed out"


def outcomes(path):
    models = defaultdict(Counter)
    for line in path.read_text().splitlines():
        entry = json.loads(line)
        model = entry["llmConfig"]["model"]
        models[model]["runs"] += 1
        models[model]["passed" if entry["result"] == "success" else cause(entry)] += 1
    return models


def rates(counts):
    excluded = sum(counts[c] for c in NOT_THE_AGENT)
    return {
        "published": counts["passed"] / counts["runs"],
        "without_benchmark_failures": counts["passed"] / (counts["runs"] - excluded),
        "credit_correct_rejected": (counts["passed"] + counts[CORRECT_REJECTED])
                                   / (counts["runs"] - excluded + counts[CORRECT_REJECTED]),
        "without_benchmark_failures_or_timeouts": counts["passed"] / (counts["runs"] - excluded - counts[TIMEOUT]),
    }


def ranking(scores):
    return [model for model, _ in sorted(scores.items(), key=lambda item: (-item[1], item[0]))]


def kendall_tau(first, second):
    position = {model: i for i, model in enumerate(second)}
    concordant = discordant = 0
    for i, a in enumerate(first):
        for b in first[i + 1:]:
            if position[a] < position[b]:
                concordant += 1
            else:
                discordant += 1
    return (concordant - discordant) / (concordant + discordant)


def build(path):
    models = outcomes(path)
    table = {model: {"counts": dict(counts), "rates": {k: round(v, 4) for k, v in rates(counts).items()}}
             for model, counts in sorted(models.items())}
    published = ranking({m: rates(c)["published"] for m, c in models.items()})
    comparisons = {}
    for rule in ("without_benchmark_failures", "credit_correct_rejected", "without_benchmark_failures_or_timeouts"):
        corrected = ranking({m: rates(c)[rule] for m, c in models.items()})
        comparisons[rule] = {"ranking": corrected, "kendall_tau": round(kendall_tau(published, corrected), 4),
                             "moved": {m: published.index(m) - corrected.index(m) for m in published
                                       if published.index(m) != corrected.index(m)}}
    return {"models": table, "published_ranking": published, "comparisons": comparisons,
            "excluded_runs": sum(c[k] for c in models.values() for k in NOT_THE_AGENT)}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--results", type=Path, default=RESULTS, help="site/combined_results.jsonl of the pinned checkout")
    ap.add_argument("--check", action="store_true", help="fail if rescore.json differs from the results")
    args = ap.parse_args()
    text = json.dumps(build(args.results), indent=2, sort_keys=True) + "\n"
    if args.check:
        if not OUT.exists() or OUT.read_text() != text:
            print("rescore.json does not match the results")
            return 1
        print("rescore.json matches the results")
        return 0
    OUT.write_text(text)
    report = json.loads(text)
    print(json.dumps({"excluded_runs": report["excluded_runs"], "published_ranking": report["published_ranking"],
                      "comparisons": {k: {"kendall_tau": v["kendall_tau"], "moved": v["moved"]}
                                      for k, v in report["comparisons"].items()}}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env bash
# Checks out k8s-ai-bench at a pinned commit for the k8s-ai-bench audit, on a host provisioned with
# setup_server.sh (Docker, kind, kubectl, jq). Run as the run user; safe to re-run.
set -euo pipefail

BENCH_REPO="https://github.com/gke-labs/k8s-ai-bench.git"
BENCH_COMMIT="ac9ac9c4c4d975a4737f2f55b8d3e363320d3f52"
DIR="${1:-$HOME/k8s-ai-bench}"

for tool in docker kind kubectl jq; do
  command -v "$tool" >/dev/null || { echo "missing $tool; run setup_server.sh first" >&2; exit 1; }
done
[[ -d "$DIR/.git" ]] || git clone --quiet "$BENCH_REPO" "$DIR"
git -C "$DIR" fetch --quiet origin
git -C "$DIR" checkout --quiet --detach "$BENCH_COMMIT"
echo "k8s-ai-bench $(git -C "$DIR" rev-parse HEAD)"

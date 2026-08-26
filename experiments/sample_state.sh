#!/usr/bin/env bash
#
# G0.2 evidence sampler (E0). READ-ONLY.
#
# Appends one compact JSON line every 2 seconds describing the observable state of
# the social-network namespace. Never mutates anything: every kubectl verb used is
# `get`. Survives NotFound, a missing namespace, and an unreachable API server
# without exiting.
#
# Uses the PINNED kubectl from the frozen execution profile
# (profiles/missing_service_social_network/pilot-v1.yaml), NOT the host default.
#
# Usage: sample_state.sh OUTPUT_JSONL
#
# Emitted fields per line:
#   ts_utc                            ISO 8601, millisecond precision
#   api_ok                            bool - see note below
#   user_service_present              bool
#   user_service_endpointslice_count  int
#   service_count                     int
#   deployments                       [{name, ready, desired}]
#   pods                              [{name, phase, all_containers_ready}]
#
# NOTE on api_ok: this field is NOT in the original G0.2 field list. It is added
# deliberately. Without it, an unreachable API server would emit
# user_service_present=false and empty arrays, which is indistinguishable from
# "the Service was deleted and the namespace is empty". api_ok=false marks a
# sample as an observation failure rather than an observation of absence. Samples
# with api_ok=false must not be read as evidence about cluster state.

set -uo pipefail   # deliberately NOT -e: a failed probe must not kill the sampler

KUBECTL="/home/sakibbuet2k19/.local/lib/sremut/kubectl/v1.32.0/kubectl"
CONTEXT="kind-kind"
NAMESPACE="social-network"
INTERVAL=2

if [ "$#" -ne 1 ]; then
  echo "Usage: $0 OUTPUT_JSONL" >&2
  exit 2
fi

OUT="$1"
mkdir -p "$(dirname "$OUT")"

if [ ! -x "$KUBECTL" ]; then
  echo "ERROR: pinned kubectl not found or not executable: $KUBECTL" >&2
  exit 2
fi

if ! command -v jq >/dev/null 2>&1; then
  echo "ERROR: jq is required" >&2
  exit 2
fi

k() { "$KUBECTL" --context "$CONTEXT" "$@" 2>/dev/null; }

trap 'exit 0' TERM INT

while true; do
  ts="$(date -u +%Y-%m-%dT%H:%M:%S.%3NZ)"
  api_ok=true

  # --- liveness of the API for this namespace (cheap, read-only) ---
  if ! k get namespace "$NAMESPACE" -o jsonpath='{.metadata.name}' >/dev/null 2>&1; then
    api_ok=false
  fi

  # --- Service/user-service presence (--ignore-not-found => exit 0 when absent) ---
  us_name="$(k get service user-service -n "$NAMESPACE" --ignore-not-found \
             -o jsonpath='{.metadata.name}')"
  if [ -n "${us_name:-}" ]; then us_present=true; else us_present=false; fi

  # --- EndpointSlices for user-service ---
  eps_raw="$(k get endpointslices.discovery.k8s.io -n "$NAMESPACE" \
             -l kubernetes.io/service-name=user-service \
             -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}')"
  eps_count="$(printf '%s' "${eps_raw:-}" | grep -c . )"

  # --- Service count ---
  svc_raw="$(k get services -n "$NAMESPACE" \
             -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}')"
  svc_count="$(printf '%s' "${svc_raw:-}" | grep -c . )"

  # --- Deployments: name \t readyReplicas \t spec.replicas ---
  dep_tsv="$(k get deployments -n "$NAMESPACE" \
             -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.status.readyReplicas}{"\t"}{.spec.replicas}{"\n"}{end}')"

  # --- Pods: name \t phase \t (space-joined container ready booleans) ---
  pod_tsv="$(k get pods -n "$NAMESPACE" \
             -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.status.phase}{"\t"}{range .status.containerStatuses[*]}{.ready}{" "}{end}{"\n"}{end}')"

  jq -c -n \
    --arg ts "$ts" \
    --argjson api_ok "$api_ok" \
    --argjson usp "$us_present" \
    --argjson epsc "${eps_count:-0}" \
    --argjson svcc "${svc_count:-0}" \
    --arg dep "${dep_tsv:-}" \
    --arg pod "${pod_tsv:-}" \
    '
    def rows($s): ($s | split("\n") | map(select(length > 0)));
    {
      ts_utc: $ts,
      api_ok: $api_ok,
      user_service_present: $usp,
      user_service_endpointslice_count: $epsc,
      service_count: $svcc,
      deployments: (rows($dep) | map(
        split("\t") as $f | {
          name:    ($f[0] // ""),
          ready:   (($f[1] // "") | if . == "" then 0 else tonumber end),
          desired: (($f[2] // "") | if . == "" then 1 else tonumber end)
        })),
      pods: (rows($pod) | map(
        split("\t") as $f
        | (($f[2] // "") | split(" ") | map(select(length > 0))) as $r
        | {
          name:  ($f[0] // ""),
          phase: ($f[1] // ""),
          all_containers_ready: (($r | length) > 0 and (all($r[]; . == "true")))
        }))
    }' >> "$OUT" 2>/dev/null

  sleep "$INTERVAL"
done

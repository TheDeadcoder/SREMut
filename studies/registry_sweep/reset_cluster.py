"""Returns the cluster to SREGym's recorded baseline, as its Conductor does between problems, removes
what problems add to kube-system, which that reset protects, and deletes Failed pods. Prints the
changes as one JSON line. Runs in a SREGym checkout's environment."""
import json
import subprocess
import sys
import time

from sregym.paths import CLUSTER_BASELINE_STATE_FILE
from sregym.service.cluster_state import PROTECTED_NAMESPACES, ClusterStateManager, _is_chaos_mesh_resource
from sregym.service.kubectl import KubeCtl

KUBE_SYSTEM = {
    "deployments": {"calico-kube-controllers", "coredns", "metrics-server"},
    "daemonsets": {"calico-node", "kube-proxy"},
    "statefulsets": set(),
    "services": {"kube-dns", "metrics-server"},
}
SETTLE_SECONDS = 300


def kubectl(*args):
    return subprocess.run(["kubectl", *args], capture_output=True, text=True, check=True).stdout.split()


def kube_system_extras():
    return [name for kind, keep in KUBE_SYSTEM.items()
            for name in kubectl("get", kind, "-n", "kube-system", "-o", "name") if name.split("/", 1)[1] not in keep]


def leftovers(manager):
    baseline = manager.baseline
    namespaces = manager._get_namespaces() - baseline.namespaces - PROTECTED_NAMESPACES
    webhooks = ((manager._get_validating_webhook_configs() | manager._get_mutating_webhook_configs())
                - baseline.validating_webhook_configs - baseline.mutating_webhook_configs)
    return sorted(namespaces | {w for w in webhooks if not _is_chaos_mesh_resource(w)} | set(kube_system_extras()))


def main():
    manager = ClusterStateManager(KubeCtl())
    if not manager.load_baseline_state(CLUSTER_BASELINE_STATE_FILE):
        manager.save_baseline_state(CLUSTER_BASELINE_STATE_FILE)
    changes = {key: value for key, value in manager.reconcile_to_baseline().items() if value}
    extras = kube_system_extras()
    if extras:
        kubectl("delete", "-n", "kube-system", "--wait=true", "--timeout=180s", *extras)
        changes["kube_system_deleted"] = extras
    failed = kubectl("delete", "pods", "--all-namespaces", "--field-selector=status.phase=Failed", "-o", "name")
    if failed:
        changes["failed_pods_deleted"] = len(failed)
    deadline = time.monotonic() + SETTLE_SECONDS
    while (left := leftovers(manager)) and time.monotonic() < deadline:
        time.sleep(5)
    if left:
        sys.exit(f"still present after the reset: {left}")
    print(json.dumps(changes, sort_keys=True))


if __name__ == "__main__":
    main()

"""Returns an AIOpsLab cluster to the baseline recorded before its first episode: deletes the namespaces,
admission webhook configurations, persistent volumes and default-namespace jobs and config maps added
since, and Failed pods. Prints the changes as one JSON line."""
import json
import subprocess
import sys
from pathlib import Path

BASELINE = Path.home() / ".aiopslab-audit-baseline.json"
KINDS = ("namespaces", "validatingwebhookconfigurations", "mutatingwebhookconfigurations", "persistentvolumes",
         "jobs -n default", "configmaps -n default")


def names(kind):
    proc = subprocess.run(["kubectl", "get", *kind.split(), "-o", "name"], capture_output=True, text=True, check=True)
    return sorted(proc.stdout.split())


def state():
    return {kind: names(kind) for kind in KINDS}


def extras(baseline, current):
    return {kind: sorted(set(current[kind]) - set(baseline.get(kind, []))) for kind in KINDS
            if set(current[kind]) - set(baseline.get(kind, []))}


def main():
    if not BASELINE.exists():
        BASELINE.write_text(json.dumps(state(), indent=2, sort_keys=True) + "\n")
        print("{}")
        return
    baseline = json.loads(BASELINE.read_text())
    found = extras(baseline, state())
    for kind, objects in found.items():
        namespace = kind.split()[1:]
        subprocess.run(["kubectl", "delete", *objects, *namespace, "--wait=true", "--timeout=300s"],
                       capture_output=True, text=True, check=True)
    failed = subprocess.run(["kubectl", "delete", "pods", "--all-namespaces", "--field-selector=status.phase=Failed",
                             "-o", "name"], capture_output=True, text=True, check=True).stdout.split()
    left = extras(baseline, state())
    if left:
        sys.exit(f"still present after the reset: {left}")
    changes = dict(found)
    if failed:
        changes["failed_pods_deleted"] = len(failed)
    print(json.dumps(changes, sort_keys=True))


if __name__ == "__main__":
    main()

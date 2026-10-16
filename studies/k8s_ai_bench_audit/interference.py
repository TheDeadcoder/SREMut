"""Whether one k8s-ai-bench task's setup changes another task's verdict on the cluster they share: on a fresh kind
cluster, the victim task's setup and a correct fix, its verifier, then the other task's setup (or nothing, as the
control) and the victim's verifier again. Each run goes to interference/<victim>/<other task or none>/attempt-<n>/."""
import argparse
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent / "verifier_mutation"), str(HERE.parent / "registry_sweep")]

from probe import CLUSTER, run  # noqa: E402
from run_sweep import scrub_value, write_json  # noqa: E402

ROOT = HERE / "interference"
LIMITS_POD = """kubectl create namespace limits-test
cat <<'EOF' | kubectl apply -n limits-test -f -
apiVersion: v1
kind: Pod
metadata: {name: resource-limits-pod}
spec:
  containers:
  - name: my-container
    image: httpd:alpine
    resources:
      requests: {cpu: 60m, memory: 62Mi}
      limits: {cpu: 600m, memory: 62Mi}
EOF
kubectl wait --for=condition=Ready pod/resource-limits-pod -n limits-test --timeout=180s
"""
PAIRS = {("create-pod-resources-limits", "create-canary-deployment"): LIMITS_POD}


def episode(bench, victim, other, fix):
    tasks = bench / "tasks"
    out = {"victim": victim, "other": other}
    kubeconfig = Path.home() / f".kube/{CLUSTER}.yaml"
    kubeconfig.parent.mkdir(parents=True, exist_ok=True)
    try:
        run(["kind", "delete", "cluster", "--name", CLUSTER], bench)
        out["cluster"] = run(["kind", "create", "cluster", "--name", CLUSTER, "--wait", "5m",
                              "--kubeconfig", str(kubeconfig)], bench)
        if out["cluster"]["exit_code"] != 0:
            out["status"] = "CLUSTER_FAILED"
            return out
        os.environ["KUBECONFIG"] = str(kubeconfig)
        out["setup"] = run([str(tasks / victim / "setup.sh")], tasks / victim)
        out["fix"] = run(["bash", "-c", fix], tasks / victim)
        out["verify_before"] = run([str(tasks / victim / "verify.sh")], bench)
        if other:
            out["other_setup"] = run([str(tasks / other / "setup.sh")], tasks / other)
        out["verify_after"] = run([str(tasks / victim / "verify.sh")], bench)
        out["status"] = "COMPLETED"
    except Exception as exc:  # noqa: BLE001
        out["status"] = "ERROR"
        out["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        run(["kind", "delete", "cluster", "--name", CLUSTER], bench)
        kubeconfig.unlink(missing_ok=True)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--attempt", type=int, default=1)
    ap.add_argument("--bench", type=Path, default=Path.home() / "k8s-ai-bench")
    args = ap.parse_args()
    for (victim, other), fix in PAIRS.items():
        for before in (other, None):
            run_dir = ROOT / victim / (before or "none") / f"attempt-{args.attempt}"
            if run_dir.exists():
                print(f"skip {victim} after {before}: attempt {args.attempt} already exists", flush=True)
                continue
            run_dir.mkdir(parents=True)
            result = episode(args.bench.resolve(), victim, before, fix)
            write_json(run_dir / "result.json", scrub_value(json.loads(json.dumps(result, default=str))))
            print(f"{victim} after {before}: {result['status']} "
                  f"verify_before={(result.get('verify_before') or {}).get('exit_code')} "
                  f"verify_after={(result.get('verify_after') or {}).get('exit_code')}", flush=True)
            if result["status"] != "COMPLETED":
                return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())

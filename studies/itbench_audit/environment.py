"""Creates (up) or removes (down) the ITBench audit environment: the kind cluster from ITBench's simple configuration,
the cloud-provider-kind load balancer, ITBench's cluster preparation and its SRE tools, with its trace collectors at
zero replicas."""
import argparse
import os
import subprocess
import time
from pathlib import Path

NODE_IMAGE = "kindest/node:v1.37.0@sha256:a1ed56cfb0e7b93589bdf97c8cd566405a265939e3620fc4f5de89adff580ae5"
CLUSTER = "kind-dev"
TOOLS = "/opt/itbench/bin"
BALANCER_LOG = Path.home() / "cloud-provider-kind.log"
COLLECTORS = ("istio", "jaeger", "kubernetes")


def run(cmd, cwd=None):
    print("+", " ".join(cmd), flush=True)
    subprocess.run(cmd, cwd=cwd, check=True)


def up(itbench):
    if CLUSTER not in subprocess.run(["kind", "get", "clusters"], capture_output=True, text=True).stdout.split():
        run(["kind", "create", "cluster", "--config", str(itbench / "clusters/kind/configs/simple.yaml"),
             "--image", NODE_IMAGE])
    if subprocess.run(["pgrep", "-f", "cloud-provider-kind --gateway"], capture_output=True).returncode:
        with open(BALANCER_LOG, "a") as log:
            subprocess.Popen(["cloud-provider-kind", "--gateway-channel", "disabled"], stdout=log,
                             stderr=subprocess.STDOUT, start_new_session=True)
        time.sleep(10)
    run(["uv", "run", "itbench", "prepare", "cluster", "--kubeconfig", str(Path.home() / ".kube/config")], cwd=itbench)
    run(["make", "-C", "scenarios/sre", "deploy-tools"], cwd=itbench)
    for collector in COLLECTORS:
        run(["kubectl", "patch", "opentelemetrycollector", collector, "-n", "opentelemetry-collectors",
             "--type=merge", "-p", '{"spec": {"replicas": 0}}'])


def down(itbench):
    subprocess.run(["make", "-C", "scenarios/sre", "undeploy-tools"], cwd=itbench)
    subprocess.run(["pkill", "-f", "cloud-provider-kind --gateway"])
    run(["kind", "delete", "cluster", "--name", CLUSTER])


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("command", choices=("up", "down"))
    ap.add_argument("--itbench", type=Path, default=Path.home() / "ITBench")
    args = ap.parse_args()
    os.environ["PATH"] = f"{TOOLS}:{os.environ['PATH']}"
    (up if args.command == "up" else down)(args.itbench.resolve())


if __name__ == "__main__":
    main()

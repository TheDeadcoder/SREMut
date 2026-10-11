#!/usr/bin/env bash
# Provisions the ITBench audit on a host set up with setup_server.sh: kind, kubectl and cloud-provider-kind at the
# versions ITBench needs, under /opt/itbench/bin so the other studies keep theirs; ITBench's kernel settings; and
# ITBench at a pinned commit with its Python and Ansible dependencies. Run as root; safe to re-run.
set -euo pipefail

RUN_USER="${RUN_USER:-runner}"
ITBENCH_REPO="https://github.com/itbench-hub/ITBench.git"
ITBENCH_COMMIT="333afa05c421b4e02a47b6c1b51686d20ce40cdd"
KIND_VERSION="v0.33.0"
KUBECTL_VERSION="v1.37.0"
BALANCER_VERSION="0.12.0"
BIN="/opt/itbench/bin"

[[ $EUID -eq 0 ]] || { echo "run as root" >&2; exit 1; }

cat > /etc/sysctl.d/99-itbench.conf <<'EOF'
fs.inotify.max_user_instances=1024
fs.inotify.max_user_watches=1048576
vm.max_map_count=262144
EOF
sysctl --system >/dev/null

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
cd "$tmp"
install -d "$BIN"

kind_url="https://github.com/kubernetes-sigs/kind/releases/download/${KIND_VERSION}/kind-linux-amd64"
curl -fsSLo kind "$kind_url"
curl -fsSLo kind.sha256 "${kind_url}.sha256sum"
echo "$(awk '{print $1}' kind.sha256)  kind" | sha256sum -c --quiet -
install -m 0755 kind "$BIN/kind"

kubectl_url="https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/amd64/kubectl"
curl -fsSLo kubectl "$kubectl_url"
curl -fsSLo kubectl.sha256 "${kubectl_url}.sha256"
echo "$(cat kubectl.sha256)  kubectl" | sha256sum -c --quiet -
install -m 0755 kubectl "$BIN/kubectl"

archive="cloud-provider-kind_${BALANCER_VERSION}_linux_amd64.tar.gz"
base="https://github.com/kubernetes-sigs/cloud-provider-kind/releases/download/v${BALANCER_VERSION}"
curl -fsSLo "$archive" "$base/$archive"
curl -fsSLo checksums.txt "$base/cloud-provider-kind_${BALANCER_VERSION}_checksums.txt"
grep " ${archive}\$" checksums.txt | sha256sum -c --quiet -
tar -xzf "$archive" cloud-provider-kind
install -m 0755 cloud-provider-kind "$BIN/cloud-provider-kind"

sudo -u "$RUN_USER" -H bash -s -- "$ITBENCH_REPO" "$ITBENCH_COMMIT" <<'EOF'
set -euo pipefail
cd ~
[[ -d ITBench/.git ]] || git clone --quiet "$1" ITBench
cd ITBench
git fetch --quiet origin
git checkout --quiet --detach "$2"
make deps
make -C scenarios/sre group-vars
EOF

echo "kind $("$BIN/kind" version | awk '{print $2}')"
echo "kubectl $("$BIN/kubectl" version --client -o json | jq -r .clientVersion.gitVersion)"
echo "itbench $(sudo -u "$RUN_USER" git -C "/home/$RUN_USER/ITBench" rev-parse HEAD)"

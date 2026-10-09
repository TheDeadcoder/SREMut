#!/usr/bin/env bash
# Provisions a fresh Ubuntu 24.04 x86_64 host for the experiments. Run as root; safe to re-run.
set -euo pipefail

RUN_USER="${RUN_USER:-runner}"
SREGYM_REPO="https://github.com/SREGym/SREGym.git"
SREGYM_COMMIT="c44b1e54c1436989d47ac6d59785426f8fb9151a"
KIND_VERSION="v0.27.0"
KUBECTL_VERSION="v1.32.1"
HELM_VERSION="v4.3.0"
UV_VERSION="0.12.24"

[[ $EUID -eq 0 ]] || { echo "run as root" >&2; exit 1; }

export DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=l
APT=(apt-get -o DPkg::Lock::Timeout=600 -y -q)

systemctl disable --now unattended-upgrades.service apt-daily.timer apt-daily-upgrade.timer

"${APT[@]}" update
"${APT[@]}" install ca-certificates curl git jq make rsync tmux

if ! command -v docker >/dev/null; then
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  . /etc/os-release
  echo "deb [arch=amd64 signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu ${VERSION_CODENAME} stable" \
    > /etc/apt/sources.list.d/docker.list
  "${APT[@]}" update
  "${APT[@]}" install docker-ce docker-ce-cli containerd.io docker-buildx-plugin
fi
apt-mark hold docker-ce docker-ce-cli containerd.io >/dev/null

cat > /etc/sysctl.d/99-sregym-kind.conf <<'EOF'
fs.inotify.max_user_instances=1024
fs.inotify.max_user_watches=1048576
EOF
sysctl --system >/dev/null
mkdir -p /run/udev

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
cd "$tmp"

fetch() { curl -fsSLo "$1" "$2"; }
verify() { echo "$(awk '{print $1}' "$2")  $1" | sha256sum -c --quiet -; }

if [[ "$(kind version 2>/dev/null | awk '{print $2}')" != "$KIND_VERSION" ]]; then
  base="https://github.com/kubernetes-sigs/kind/releases/download/${KIND_VERSION}/kind-linux-amd64"
  fetch kind "$base" && fetch kind.sha256 "${base}.sha256sum"
  verify kind kind.sha256 && install -m 0755 kind /usr/local/bin/kind
fi

if [[ "$(kubectl version --client -o json 2>/dev/null | jq -r .clientVersion.gitVersion)" != "$KUBECTL_VERSION" ]]; then
  base="https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/amd64/kubectl"
  fetch kubectl "$base" && fetch kubectl.sha256 "${base}.sha256"
  verify kubectl kubectl.sha256 && install -m 0755 kubectl /usr/local/bin/kubectl
fi

if [[ "$(helm version --template '{{.Version}}' 2>/dev/null)" != "$HELM_VERSION" ]]; then
  base="https://get.helm.sh/helm-${HELM_VERSION}-linux-amd64.tar.gz"
  fetch helm.tgz "$base" && fetch helm.sha256 "${base}.sha256sum"
  verify helm.tgz helm.sha256 && tar -xzf helm.tgz && install -m 0755 linux-amd64/helm /usr/local/bin/helm
fi

if [[ "$(uv --version 2>/dev/null | awk '{print $2}')" != "$UV_VERSION" ]]; then
  base="https://github.com/astral-sh/uv/releases/download/${UV_VERSION}/uv-x86_64-unknown-linux-gnu.tar.gz"
  fetch uv.tgz "$base" && fetch uv.sha256 "${base}.sha256"
  verify uv.tgz uv.sha256 && tar -xzf uv.tgz
  install -m 0755 uv-x86_64-unknown-linux-gnu/uv uv-x86_64-unknown-linux-gnu/uvx /usr/local/bin/
fi

id -u "$RUN_USER" >/dev/null 2>&1 || useradd -m -s /bin/bash "$RUN_USER"
usermod -aG docker "$RUN_USER"
echo "$RUN_USER ALL=(ALL) NOPASSWD:ALL" > "/etc/sudoers.d/90-$RUN_USER"
chmod 0440 "/etc/sudoers.d/90-$RUN_USER"
install -d -m 0700 -o "$RUN_USER" -g "$RUN_USER" "/home/$RUN_USER/.ssh"
install -m 0600 -o "$RUN_USER" -g "$RUN_USER" /root/.ssh/authorized_keys "/home/$RUN_USER/.ssh/authorized_keys"

sudo -u "$RUN_USER" -H bash -s -- "$SREGYM_REPO" "$SREGYM_COMMIT" <<'EOF'
set -euo pipefail
cd ~
[[ -d SREGym/.git ]] || git clone --quiet "$1" SREGym
cd SREGym
git fetch --quiet origin
git checkout --quiet --detach "$2"
git submodule update --init --recursive --quiet
uv sync --quiet --python /usr/bin/python3.12
if ! kind get clusters 2>/dev/null | grep -qx kind; then
  bash kind/setup_kind_cluster.sh
fi
kubectl wait --for=condition=Ready nodes --all --timeout=120s
EOF

echo "docker $(docker version --format '{{.Server.Version}}')"
echo "kind $(kind version | awk '{print $2}')"
echo "kubectl $(kubectl version --client -o json | jq -r .clientVersion.gitVersion)"
echo "helm $(helm version --template '{{.Version}}')"
echo "uv $(uv --version | awk '{print $2}')"
echo "python $(/usr/bin/python3.12 --version | awk '{print $2}')"
echo "sregym $(sudo -u "$RUN_USER" git -C "/home/$RUN_USER/SREGym" rev-parse HEAD)"
echo "applications $(sudo -u "$RUN_USER" git -C "/home/$RUN_USER/SREGym/SREGym-applications" rev-parse HEAD)"

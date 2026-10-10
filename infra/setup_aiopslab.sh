#!/usr/bin/env bash
# Provisions a fresh Ubuntu 24.04 x86_64 host for the AIOpsLab audit: the shared toolchain from
# setup_server.sh, then AIOpsLab at a pinned commit with its own kind cluster. Run as root; safe to re-run.
set -euo pipefail

RUN_USER="${RUN_USER:-runner}"
AIOPSLAB_REPO="https://github.com/microsoft/AIOpsLab.git"
AIOPSLAB_COMMIT="ccf08d0d1d5fa5b30f120e2e8549662d44411b35"
POETRY_VERSION="2.4.1"
NODE_IMAGE="jacksonarthurclark/aiopslab-kind-x86@sha256:d631857278d3f8ce5c36364c75ec25695ceb22e311ec6621a4ebf5506b86774d"

BENCHMARK=none RUN_USER="$RUN_USER" bash "$(dirname "$(readlink -f "$0")")/setup_server.sh"

apt-get -o DPkg::Lock::Timeout=600 -y -q install python3.12-venv
if [[ "$(/opt/poetry/bin/poetry --version 2>/dev/null | grep -o '[0-9.]*$')" != "$POETRY_VERSION" ]]; then
  python3.12 -m venv /opt/poetry
  /opt/poetry/bin/pip install --quiet "poetry==$POETRY_VERSION"
  ln -sf /opt/poetry/bin/poetry /usr/local/bin/poetry
fi

sudo -u "$RUN_USER" -H bash -s -- "$AIOPSLAB_REPO" "$AIOPSLAB_COMMIT" "$NODE_IMAGE" <<'EOF'
set -euo pipefail
cd ~
[[ -d AIOpsLab/.git ]] || git clone --quiet "$1" AIOpsLab
cd AIOpsLab
git fetch --quiet origin
git checkout --quiet --detach "$2"
git submodule update --init --recursive --quiet
poetry config virtualenvs.in-project true
poetry env use /usr/bin/python3.12 >/dev/null
poetry install --quiet --without clients
[[ -f aiopslab/config.yml ]] || sed -e 's/^k8s_host: .*/k8s_host: kind/' -e "s/^k8s_user: .*/k8s_user: $USER/" \
  aiopslab/config.yml.example > aiopslab/config.yml
if ! kind get clusters 2>/dev/null | grep -qx kind; then
  sed "s#image: .*#image: $3#" kind/kind-config-x86.yaml > /tmp/aiopslab-kind.yaml
  kind create cluster --config /tmp/aiopslab-kind.yaml
fi
kubectl wait --for=condition=Ready nodes --all --timeout=180s
EOF

echo "poetry $(poetry --version | grep -o '[0-9.]*$')"
echo "aiopslab $(sudo -u "$RUN_USER" git -C "/home/$RUN_USER/AIOpsLab" rev-parse HEAD)"
echo "applications $(sudo -u "$RUN_USER" git -C "/home/$RUN_USER/AIOpsLab/aiopslab-applications" rev-parse HEAD)"

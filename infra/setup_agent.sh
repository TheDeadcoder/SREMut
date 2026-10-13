#!/usr/bin/env bash
# Installs Claude Code for the run user (used once, for `claude setup-token`) and builds the agent images: Claude Code
# with the kubectl of each benchmark's cluster. No root needed; the run user is in the docker group.
set -euo pipefail

NODE_VERSION="22.23.3"
NODE_SHA256="df450af89261115ef9f9e3830c3eeb2cc9213b63c720b1af623cb5dcbe2e02de"
CLAUDE_CODE_VERSION="2.1.296"
REPO="$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)"

mkdir -p ~/.local/opt
cd ~/.local/opt
archive="node-v$NODE_VERSION-linux-x64.tar.xz"
if [[ ! -d "node-v$NODE_VERSION-linux-x64" ]]; then
  curl -fsSLO "https://nodejs.org/dist/v$NODE_VERSION/$archive"
  echo "$NODE_SHA256  $archive" | sha256sum -c -
  tar -xJf "$archive"
  rm -f "$archive"
fi
ln -sfn "node-v$NODE_VERSION-linux-x64" node
PATH="$HOME/.local/opt/node/bin:$PATH" npm install -g --silent "@anthropic-ai/claude-code@$CLAUDE_CODE_VERSION"
"$HOME/.local/opt/node/bin/claude" --version

build() {
  local context
  context="$(mktemp -d)"
  cp "$REPO/studies/agent_loop/Dockerfile" "$context/"
  install -m 0755 "$2" "$context/kubectl"
  docker build -q -t "$1" --build-arg CLAUDE_CODE_VERSION="$CLAUDE_CODE_VERSION" "$context"
  rm -rf "$context"
}
build sremut-agent:k8s-1.32 /usr/local/bin/kubectl
if [[ -x /opt/itbench/bin/kubectl ]]; then
  build sremut-agent:k8s-1.37 /opt/itbench/bin/kubectl
fi

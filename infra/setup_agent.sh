#!/usr/bin/env bash
# Installs Claude Code and Codex for the run user (used once each, for `claude setup-token` and `codex login`) and
# builds the agent images: each agent with the kubectl of each benchmark's cluster. No root needed; the run user is
# in the docker group.
set -euo pipefail

NODE_VERSION="22.23.3"
NODE_SHA256="df450af89261115ef9f9e3830c3eeb2cc9213b63c720b1af623cb5dcbe2e02de"
CLAUDE_CODE_VERSION="2.1.296"
CODEX_VERSION="0.163.0"
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
PATH="$HOME/.local/opt/node/bin:$PATH" npm install -g --silent "@anthropic-ai/claude-code@$CLAUDE_CODE_VERSION" \
  "@openai/codex@$CODEX_VERSION"
"$HOME/.local/opt/node/bin/claude" --version
"$HOME/.local/opt/node/bin/codex" --version
install -d -m 0700 ~/.codex-agent
[[ -f ~/.codex-agent/config.toml ]] || echo 'cli_auth_credentials_store = "file"' > ~/.codex-agent/config.toml

build() {
  local context
  context="$(mktemp -d)"
  cp "$REPO/studies/agent_loop/$3" "$context/Dockerfile"
  install -m 0755 "$2" "$context/kubectl"
  docker build -q -t "$1" --build-arg "$4" "$context"
  rm -rf "$context"
}
for kubectl in /usr/local/bin/kubectl /opt/itbench/bin/kubectl; do
  [[ -x "$kubectl" ]] || continue
  version="$("$kubectl" version --client -o json | jq -r .clientVersion.gitVersion | cut -d. -f1,2 | tr -d v)"
  build "sremut-agent:k8s-$version" "$kubectl" Dockerfile "CLAUDE_CODE_VERSION=$CLAUDE_CODE_VERSION"
  build "sremut-codex:k8s-$version" "$kubectl" Dockerfile.codex "CODEX_VERSION=$CODEX_VERSION"
done

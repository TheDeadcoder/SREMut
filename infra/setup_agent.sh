#!/usr/bin/env bash
# Installs Node.js and Claude Code for the run user, at pinned versions, for the agent study. No root needed.
set -euo pipefail

NODE_VERSION="22.23.3"
NODE_SHA256="df450af89261115ef9f9e3830c3eeb2cc9213b63c720b1af623cb5dcbe2e02de"
CLAUDE_CODE_VERSION="2.1.296"

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

#!/bin/bash
# Clone/update repository and install package
# Usage: ./setup-project.sh [REPO_URL]
set -euo pipefail

WORKSPACE="${HOME}/yt-filter-workspace"
REPO_URL="${1:-}"

echo "=== Project Setup ==="

if [ -n "$REPO_URL" ] && [ ! -d "$WORKSPACE/.git" ]; then
    echo "Cloning repository..."
    git clone "$REPO_URL" "$WORKSPACE"
fi

cd "$WORKSPACE"

# Activate venv
if [ -f "venv/bin/activate" ]; then
    source venv/bin/activate
else
    echo "Error: Virtual environment not found. Run install-dependencies.sh first."
    exit 1
fi

# Update and reinstall
echo "Installing/updating package..."
pip install -e ".[upload,discovery]"

# Generate default config if not exists
if [ ! -f "$HOME/.yt-audio-filter/discovery_config.yaml" ]; then
    echo "Generating default discovery config..."
    yt-scheduler --init-config
fi

echo ""
echo "=== Project Setup Complete ==="
echo "Config file: $HOME/.yt-audio-filter/discovery_config.yaml"

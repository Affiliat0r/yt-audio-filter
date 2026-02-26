#!/bin/bash
# VM initial setup - run after first SSH connection
# Usage: ./setup-vm.sh
set -euo pipefail

echo "=== YT Audio Filter - VM Setup ==="
echo ""

# Detect OS
if command -v apt &>/dev/null; then
    PKG_MANAGER="apt"
    echo "Detected: Ubuntu/Debian"
elif command -v dnf &>/dev/null; then
    PKG_MANAGER="dnf"
    echo "Detected: Oracle Linux/RHEL"
else
    echo "Error: Unsupported OS. Need apt or dnf."
    exit 1
fi

# System updates
echo ""
echo "--- Updating system packages ---"
if [ "$PKG_MANAGER" = "apt" ]; then
    sudo apt update && sudo apt upgrade -y
else
    sudo dnf update -y
fi

# Install base dependencies
echo ""
echo "--- Installing base dependencies ---"
if [ "$PKG_MANAGER" = "apt" ]; then
    sudo apt install -y \
        python3 python3-pip python3-venv python3-dev \
        git curl wget \
        ffmpeg \
        gcc g++ make \
        libsndfile1-dev \
        nodejs npm
else
    sudo dnf install -y \
        python3.11 python3.11-pip python3.11-devel \
        git curl wget \
        ffmpeg \
        gcc gcc-c++ make \
        libsndfile-devel \
        nodejs npm
fi

# Create workspace
echo ""
echo "--- Setting up workspace ---"
mkdir -p ~/yt-filter-workspace
mkdir -p ~/yt-filter-workspace/logs
mkdir -p ~/yt-filter-workspace/output
mkdir -p ~/.yt-audio-filter

# Create environment file if not exists
if [ ! -f ~/.env-yt-filter ]; then
    cat > ~/.env-yt-filter << 'ENVEOF'
# YT Audio Filter environment variables
# YOUTUBE_API_KEY=your_api_key_here
ENVEOF
    echo "Created ~/.env-yt-filter (edit to add your API key)"
fi

echo ""
echo "=== VM Setup Complete ==="
echo ""
echo "Next steps:"
echo "  1. Run: ./deploy/oracle-cloud/install-dependencies.sh"
echo "  2. Run: ./deploy/oracle-cloud/setup-credentials.sh"
echo "  3. Run: ./deploy/oracle-cloud/setup-cron.sh"

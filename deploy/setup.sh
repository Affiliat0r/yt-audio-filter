#!/bin/bash
# One-shot setup for any Linux VM (Ubuntu/Debian/RHEL)
# Usage: git clone <repo> ~/yt-filter-workspace && cd ~/yt-filter-workspace && bash deploy/setup.sh
set -euo pipefail

WORKSPACE="${HOME}/yt-filter-workspace"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "============================================"
echo " YT Audio Filter - Automated VM Setup"
echo "============================================"
echo ""

# Step 1: System packages
echo "[1/5] Installing system packages..."
bash "$SCRIPT_DIR/oracle-cloud/setup-vm.sh"

# Step 2: Python + PyTorch + project dependencies
echo ""
echo "[2/5] Installing Python dependencies..."
bash "$SCRIPT_DIR/oracle-cloud/install-dependencies.sh"

# Step 3: Project setup + default config generation
echo ""
echo "[3/5] Setting up project..."
bash "$SCRIPT_DIR/oracle-cloud/setup-project.sh"

# Step 4: Credentials (interactive)
echo ""
echo "[4/5] Setting up credentials..."
bash "$SCRIPT_DIR/oracle-cloud/setup-credentials.sh"

# Step 5: Cron job
echo ""
echo "[5/5] Setting up cron schedule..."
bash "$SCRIPT_DIR/oracle-cloud/setup-cron.sh"

echo ""
echo "============================================"
echo " Setup Complete!"
echo "============================================"
echo ""
echo "The pipeline will run automatically every 6 hours."
echo ""
echo "To test immediately:"
echo "  source ~/yt-filter-workspace/venv/bin/activate"
echo "  source ~/.env-yt-filter"
echo "  yt-scheduler --config ~/.yt-audio-filter/discovery_config.yaml --dry-run --verbose"
echo ""
echo "To run one video now:"
echo "  bash $WORKSPACE/deploy/oracle-cloud/run-pipeline.sh"
echo ""
echo "Monitor logs:"
echo "  tail -f ~/yt-filter-workspace/logs/cron.log"

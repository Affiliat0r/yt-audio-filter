#!/bin/bash
# Set up daily cron job for autonomous pipeline
# Usage: ./setup-cron.sh
set -euo pipefail

WORKSPACE="${HOME}/yt-filter-workspace"
SCRIPT="$WORKSPACE/deploy/oracle-cloud/run-pipeline.sh"
LOG_DIR="$WORKSPACE/logs"

mkdir -p "$LOG_DIR"
chmod +x "$SCRIPT"

echo "=== Cron Job Setup ==="
echo ""

# Remove old yt-filter cron entries
crontab -l 2>/dev/null | grep -v "run-pipeline.sh" | grep -v "yt-filter keepalive" > /tmp/crontab_clean 2>/dev/null || true

# Run pipeline every 6 hours (1 video per run = 4 videos/day)
echo "0 */6 * * * $SCRIPT >> $LOG_DIR/cron.log 2>&1  # yt-filter pipeline (every 6h)" >> /tmp/crontab_clean

crontab /tmp/crontab_clean
rm /tmp/crontab_clean

echo "Cron jobs installed:"
echo ""
crontab -l
echo ""
echo "Pipeline runs every 6 hours (1 video per run, 4 videos/day)"
echo "Also serves as keepalive (prevents Oracle VM reclamation)"
echo "Logs: $LOG_DIR/cron.log"

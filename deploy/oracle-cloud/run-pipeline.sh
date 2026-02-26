#!/bin/bash
# Main pipeline wrapper called by cron
# Usage: ./run-pipeline.sh
set -euo pipefail

WORKSPACE="${HOME}/yt-filter-workspace"
LOG_DIR="$WORKSPACE/logs"
mkdir -p "$LOG_DIR"

# Load environment
if [ -f "$HOME/.env-yt-filter" ]; then
    set -a
    source "$HOME/.env-yt-filter"
    set +a
fi

# Add Deno to PATH (needed for yt-dlp EJS challenge solver)
export DENO_INSTALL="$HOME/.deno"
export PATH="$DENO_INSTALL/bin:$PATH"

# Activate virtual environment
source "$WORKSPACE/venv/bin/activate"

# Change to workspace
cd "$WORKSPACE"

# Log header
echo ""
echo "========================================"
echo " Pipeline run: $(date -u '+%Y-%m-%d %H:%M:%S UTC')"
echo "========================================"

# Run the scheduler with discovery
yt-scheduler \
    --config "$HOME/.yt-audio-filter/discovery_config.yaml" \
    --verbose \
    2>&1

EXIT_CODE=$?
echo "Pipeline exit code: $EXIT_CODE"
echo "Completed at: $(date -u '+%Y-%m-%d %H:%M:%S UTC')"

# Cleanup old temp files (older than 2 days)
find /tmp -name "yt_scheduler_*" -mtime +2 -exec rm -rf {} + 2>/dev/null || true
find /tmp -name "yt_audio_filter_*" -mtime +2 -exec rm -rf {} + 2>/dev/null || true
find /tmp -name "yt_download_*" -mtime +2 -exec rm -rf {} + 2>/dev/null || true

# Cleanup processed output files older than 7 days
find "$WORKSPACE/output" -name "*.mp4" -mtime +7 -delete 2>/dev/null || true

# Rotate logs (keep last 30 days)
find "$LOG_DIR" -name "*.log" -mtime +30 -delete 2>/dev/null || true

# Cleanup old quota tracking entries
python -c "
from yt_audio_filter.quota_tracker import QuotaTracker
qt = QuotaTracker()
qt.cleanup_old_entries(keep_days=30)
" 2>/dev/null || true

exit $EXIT_CODE

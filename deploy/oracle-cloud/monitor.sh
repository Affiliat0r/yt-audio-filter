#!/bin/bash
# Health check / status script
# Usage: ./monitor.sh
WORKSPACE="${HOME}/yt-filter-workspace"

echo "=== YT Audio Filter Pipeline Status ==="
echo "Date: $(date)"
echo "Host: $(hostname) ($(uname -m))"
echo ""

# System resources
echo "--- System Resources ---"
echo -n "CPU: "
nproc
echo -n "Memory: "
free -h | awk '/^Mem:/ {print $3 " used / " $2 " total"}'
echo -n "Uptime: "
uptime -p 2>/dev/null || uptime
echo ""

# Disk usage
echo "--- Disk Usage ---"
df -h / | tail -1 | awk '{print "Root: " $3 " used / " $2 " total (" $5 " used)"}'
echo ""

# Processed videos count
PROCESSED_FILE="$WORKSPACE/processed_videos.json"
if [ -f "$PROCESSED_FILE" ]; then
    COUNT=$(python3 -c "import json; print(len(json.load(open('$PROCESSED_FILE'))['processed_ids']))" 2>/dev/null || echo "?")
    LAST=$(python3 -c "
import json
data = json.load(open('$PROCESSED_FILE'))
if data.get('history'):
    last = data['history'][-1]
    print(f\"{last['title'][:50]} ({last['processed_at'][:10]})\")
else:
    print('N/A')
" 2>/dev/null || echo "N/A")
    echo "--- Processing Stats ---"
    echo "Total processed: $COUNT videos"
    echo "Last processed: $LAST"
    echo ""
fi

# API quota usage today
QUOTA_FILE="$HOME/.yt-audio-filter/api_quota_usage.json"
if [ -f "$QUOTA_FILE" ]; then
    python3 -c "
import json
from datetime import date
data = json.load(open('$QUOTA_FILE'))
today = str(date.today())
entry = data.get(today, {})
usage = entry.get('total', 0)
print(f'--- API Quota (today) ---')
print(f'Used: {usage} / 10000 units')
print(f'Remaining: {10000 - usage} units')
" 2>/dev/null
    echo ""
fi

# Cron job status
echo "--- Cron Jobs ---"
crontab -l 2>/dev/null | grep -v "^#" | grep "yt-filter" || echo "No cron jobs found"
echo ""

# Last pipeline run
echo "--- Last 20 Lines of Pipeline Log ---"
if [ -f "$WORKSPACE/logs/cron.log" ]; then
    tail -20 "$WORKSPACE/logs/cron.log"
else
    echo "No logs found"
fi

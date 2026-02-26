#!/bin/bash
# Interactive credential setup for YouTube API
# Usage: ./setup-credentials.sh
set -euo pipefail

CRED_DIR="$HOME/.yt-audio-filter"
ENV_FILE="$HOME/.env-yt-filter"

mkdir -p "$CRED_DIR"

echo "=== YouTube Credentials Setup ==="
echo ""

# YouTube Data API Key
echo "--- Step 1: YouTube Data API Key (for video discovery) ---"
echo ""
echo "To get an API key:"
echo "  1. Go to https://console.cloud.google.com/"
echo "  2. Create a project (or select existing)"
echo "  3. Enable 'YouTube Data API v3'"
echo "  4. Go to Credentials -> Create Credentials -> API Key"
echo ""

if grep -q "YOUTUBE_API_KEY=" "$ENV_FILE" 2>/dev/null; then
    echo "API key already configured in $ENV_FILE"
    read -p "Update it? (y/N): " update_key
    if [ "$update_key" = "y" ] || [ "$update_key" = "Y" ]; then
        read -p "Enter YouTube Data API key: " API_KEY
        sed -i "s/YOUTUBE_API_KEY=.*/YOUTUBE_API_KEY=$API_KEY/" "$ENV_FILE"
        echo "API key updated."
    fi
else
    read -p "Enter YouTube Data API key (or press Enter to skip): " API_KEY
    if [ -n "$API_KEY" ]; then
        echo "YOUTUBE_API_KEY=$API_KEY" >> "$ENV_FILE"
        echo "API key saved to $ENV_FILE"
    else
        echo "Skipped. Set YOUTUBE_API_KEY in $ENV_FILE later."
    fi
fi

# OAuth Credentials
echo ""
echo "--- Step 2: OAuth Credentials (for YouTube uploads) ---"
echo ""
echo "Since this is a headless server, you need to:"
echo "  1. Authenticate on your LOCAL machine first"
echo "  2. Copy the token files to this server"
echo ""
echo "On your LOCAL machine, run:"
echo "  yt-audio-filter --list-playlists"
echo "  (This triggers the OAuth browser flow)"
echo ""
echo "Then copy files to this server:"
echo "  scp ~/.yt-audio-filter/oauth_token.pickle $(whoami)@$(hostname):$CRED_DIR/"
echo "  scp ~/.yt-audio-filter/client_secrets.json $(whoami)@$(hostname):$CRED_DIR/"
echo ""

# Check if credentials exist
if [ -f "$CRED_DIR/oauth_token.pickle" ]; then
    echo "OAuth token found at $CRED_DIR/oauth_token.pickle"
else
    echo "WARNING: No OAuth token found. Upload will fail until configured."
fi

if [ -f "$CRED_DIR/client_secrets.json" ]; then
    echo "Client secrets found at $CRED_DIR/client_secrets.json"
else
    echo "WARNING: No client_secrets.json found."
fi

echo ""
echo "=== Credentials Setup Complete ==="

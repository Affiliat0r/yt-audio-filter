# Oracle Cloud Free Tier Deployment Guide

Deploy the YT Audio Filter autonomous pipeline on Oracle Cloud Always Free tier.

## Prerequisites

- Oracle Cloud account (free: [cloud.oracle.com](https://cloud.oracle.com))
- SSH client on your local machine
- YouTube Data API key ([console.cloud.google.com](https://console.cloud.google.com))
- YouTube OAuth credentials (client_secrets.json + oauth_token.pickle)

## Step 1: Create Oracle Cloud Account

1. Go to [cloud.oracle.com](https://cloud.oracle.com) and click "Sign Up"
2. Select your home region (closest to you for lowest latency)
3. Complete registration (credit card required for verification, but Always Free resources are never charged)
4. Wait for account provisioning (can take up to 30 minutes)

## Step 2: Create Compute Instance

1. Go to **Compute > Instances > Create Instance**
2. Configure:
   - **Name**: `yt-audio-filter`
   - **Image**: Ubuntu 22.04 (Canonical)
   - **Shape**: VM.Standard.A1.Flex (Ampere ARM)
     - OCPUs: **4** (maximum Always Free)
     - Memory: **24 GB** (maximum Always Free)
   - **Boot volume**: 100 GB
   - **Networking**: Create new VCN with public subnet
   - **SSH keys**: Upload your public key or generate new pair
3. Click **Create**
4. Note the **Public IP address** once the instance is running

## Step 3: Connect and Setup

```bash
# Connect to your VM
ssh -i ~/.ssh/your_key ubuntu@YOUR_PUBLIC_IP

# Clone the repository
git clone https://github.com/YOUR_REPO/yt-audio-filter.git ~/yt-filter-workspace
cd ~/yt-filter-workspace

# Run setup scripts in order
chmod +x deploy/oracle-cloud/*.sh
./deploy/oracle-cloud/setup-vm.sh
./deploy/oracle-cloud/install-dependencies.sh
./deploy/oracle-cloud/setup-credentials.sh
./deploy/oracle-cloud/setup-cron.sh
```

## Step 4: Configure Credentials

### YouTube Data API Key
```bash
# Set in environment file
echo "YOUTUBE_API_KEY=your_api_key_here" >> ~/.env-yt-filter
```

### YouTube Upload OAuth Token
Since the VM is headless, authenticate on your **local machine** first:
```bash
# On your local machine:
yt-audio-filter --list-playlists  # Triggers OAuth browser flow

# Copy token to VM:
scp ~/.yt-audio-filter/oauth_token.pickle ubuntu@YOUR_PUBLIC_IP:~/.yt-audio-filter/
scp ~/.yt-audio-filter/client_secrets.json ubuntu@YOUR_PUBLIC_IP:~/.yt-audio-filter/
```

Or use youtubeuploader binary:
```bash
scp request.token ubuntu@YOUR_PUBLIC_IP:~/.yt-audio-filter/
scp client_secrets.json ubuntu@YOUR_PUBLIC_IP:~/.yt-audio-filter/
```

## Step 5: Generate Discovery Config

```bash
yt-scheduler --init-config
# Edit config: nano ~/.yt-audio-filter/discovery_config.yaml
```

## Step 6: Test

```bash
# Dry run - see what would be processed
source ~/.env-yt-filter
yt-scheduler --api-key $YOUTUBE_API_KEY --dry-run --verbose

# Process one video as test
yt-scheduler --api-key $YOUTUBE_API_KEY -n 1 --verbose
```

## Step 7: Monitor

```bash
# Check pipeline status
./deploy/oracle-cloud/monitor.sh

# View recent logs
tail -50 ~/yt-filter-workspace/logs/cron.log

# Check cron jobs
crontab -l
```

## Always Free Limits

| Resource | Limit |
|----------|-------|
| ARM Ampere A1 | 4 OCPUs, 24 GB RAM |
| Block Storage | 200 GB total |
| Outbound Data | 10 TB/month |
| Object Storage | 20 GB |

**Important**: Oracle may reclaim idle VMs after 7 days of inactivity.
The cron setup includes a keepalive ping every 6 hours to prevent this.

## Processing Performance

With 4 ARM OCPUs and CPU-only Demucs (`htdemucs`):
- ~2-4x realtime processing (30 min video takes 60-120 min)
- Recommended: 2-4 videos per daily run
- 24 GB RAM is sufficient (Demucs uses ~4-6 GB peak)

## Troubleshooting

### PyTorch import error on ARM64
```bash
pip install torch torchaudio --index-url https://download.pytorch.org/whl/cpu
```

### FFmpeg not found
```bash
sudo apt install ffmpeg
ffmpeg -version
```

### OAuth token expired
```bash
# Re-authenticate on local machine, then copy token
scp ~/.yt-audio-filter/oauth_token.pickle ubuntu@VM_IP:~/.yt-audio-filter/
```

### API quota exceeded
Check usage: `cat ~/.yt-audio-filter/api_quota_usage.json`
Quota resets daily at midnight Pacific Time.

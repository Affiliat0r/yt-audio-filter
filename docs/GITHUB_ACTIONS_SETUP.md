# GitHub Actions Daily Processing Setup

This guide explains how to set up the automated daily video processing pipeline using GitHub Actions.

## Overview

The pipeline runs daily at 3 AM UTC and:
1. Scrapes videos from 4 configured YouTube channels
2. Filters videos by duration (10-60 minutes)
3. Excludes previously processed videos
4. Processes up to 4 videos (1 per channel) with Demucs
5. Uploads to YouTube with "[No Background Music]" suffix
6. Tracks processed videos to avoid duplicates

## Prerequisites

1. A GitHub repository (public for unlimited free minutes)
2. YouTube API credentials (client_secrets.json and request.token)
3. The youtubeuploader tool credentials

## Setup Steps

### 1. Push Code to GitHub

```bash
cd yt-audio-filter
git init
git add .
git commit -m "Initial commit"
git remote add origin https://github.com/YOUR_USERNAME/yt-audio-filter.git
git push -u origin main
```

### 2. Configure GitHub Secrets

Go to your repository Settings > Secrets and variables > Actions, then add:

#### YOUTUBE_CLIENT_SECRETS
Copy the entire contents of your `client_secrets.json` file:
```json
{
  "installed": {
    "client_id": "YOUR_CLIENT_ID.apps.googleusercontent.com",
    "project_id": "your-project-id",
    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
    "token_uri": "https://oauth2.googleapis.com/token",
    "client_secret": "YOUR_CLIENT_SECRET",
    ...
  }
}
```

#### YOUTUBE_REQUEST_TOKEN
Copy the entire contents of your `request.token` file (this is the OAuth refresh token).

### 3. Enable GitHub Actions

1. Go to your repository > Actions tab
2. Enable workflows if prompted
3. The workflow will run automatically at 3 AM UTC daily

### 4. Manual Trigger (Testing)

You can manually trigger the workflow:
1. Go to Actions > "Daily Video Processing"
2. Click "Run workflow"
3. Optionally enable "Dry run" to see what would be processed without actually processing

## Configuration

### Channels

Edit `src/yt_audio_filter/scheduler.py` to change the default channels:

```python
DEFAULT_CHANNELS = [
    "@niloyatv",  # Niloya
    "https://www.youtube.com/channel/UCuhpMCRmMn5ykbqWPcfzfRQ",  # Baykuş Hop Hop
    "@sevimli.dostlar",  # Sevimli Dostlar
    "https://www.youtube.com/channel/UCrOSyOxCMTuRcRUvWNCt9OA",  # Pırıl
]
```

### Duration Limits

```python
MIN_DURATION = 10 * 60   # 10 minutes (in seconds)
MAX_DURATION = 60 * 60   # 60 minutes (in seconds)
```

### Schedule

Edit `.github/workflows/daily-process.yml` to change the schedule:

```yaml
on:
  schedule:
    # Run daily at 3:00 AM UTC
    - cron: '0 3 * * *'
```

Cron format: `minute hour day month weekday`

## Monitoring

### Processed Videos

The `processed_videos.json` file tracks all processed videos:
- Automatically updated after each run
- Committed back to the repository
- Contains video IDs, titles, channels, and timestamps

### Logs

- View workflow runs in the Actions tab
- Logs are saved as artifacts for 7 days
- Check for errors in the workflow output

## Troubleshooting

### "Upload limit exceeded"
YouTube has daily upload limits. Wait 24 hours or use a different account.

### "Token expired"
Re-authenticate locally and update the YOUTUBE_REQUEST_TOKEN secret.

### "No eligible videos"
- Channel may have changed
- All videos may be already processed
- Videos may not meet duration requirements

### Workflow timeout (6 hours)
- Process fewer videos per run
- Use shorter videos
- Consider splitting into multiple workflows

## Cost

- **GitHub Actions**: Free unlimited minutes for public repos
- **YouTube API**: Free tier (10,000 units/day, uploads cost 1,600 each)
- **Compute**: Runs on GitHub-hosted runners (free)

## Local Testing

Test the scheduler locally before deploying:

```bash
# Dry run - see what would be processed
python -m yt_audio_filter.scheduler --dry-run -v

# Process 1 video per channel
python -m yt_audio_filter.scheduler -n 1 -v

# Process specific channels only
python -m yt_audio_filter.scheduler --channels @niloyatv @sevimli.dostlar
```

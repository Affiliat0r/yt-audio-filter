# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

YT Audio Filter is a Python CLI tool that removes background music from MP4 videos using Facebook's Demucs AI model. It accepts both local video files and YouTube URLs, preserves vocals while maintaining original video quality through lossless remuxing.

## Development Commands

```bash
# Install in development mode (from yt-audio-filter directory)
pip install -e .

# Install dev dependencies
pip install -r requirements-dev.txt

# Run the tool (local file)
yt-audio-filter video.mp4
python -m yt_audio_filter video.mp4

# Run the tool (YouTube URL)
yt-audio-filter "https://youtube.com/watch?v=VIDEO_ID"
yt-audio-filter "https://youtu.be/VIDEO_ID"

# Code formatting (line length: 100)
black src/

# Type checking
mypy src/

# Run tests
pytest
```

## External Dependencies

- **FFmpeg**: Auto-detected from bundled location (`ffmpeg-*/bin/`) or system PATH. Required for audio extraction and video remuxing.
- **yt-dlp**: Required for YouTube URL support. Installed automatically with package.
- **PyTorch with CUDA** (optional): For GPU acceleration, install from https://pytorch.org
- **Google API Client** (optional): For YouTube upload feature. Install with `pip install -e ".[upload]"`

## Architecture

### Input Flow

The CLI ([cli.py](src/yt_audio_filter/cli.py)) detects whether input is a YouTube URL or local file:
- **YouTube URL**: Downloads video to temp directory via yt-dlp, processes it, then cleans up
- **Local file**: Processes directly

### Processing Pipeline

Three stages orchestrated by [pipeline.py](src/yt_audio_filter/pipeline.py):

1. **Extract Audio** - FFmpeg extracts audio from video as WAV ([ffmpeg.py](src/yt_audio_filter/ffmpeg.py))
2. **Isolate Vocals** - Demucs AI separates vocals from background music ([demucs_processor.py](src/yt_audio_filter/demucs_processor.py))
3. **Remux Video** - FFmpeg combines original video stream (lossless copy) with processed vocals

### Key Modules

| Module | Responsibility |
|--------|----------------|
| `cli.py` | Argparse CLI, URL/file detection, entry point via `main()` |
| `youtube.py` | YouTube URL validation and video download via yt-dlp |
| `pipeline.py` | `process_video()` orchestrates the 3-stage pipeline |
| `ffmpeg.py` | Subprocess calls to ffmpeg/ffprobe |
| `ffmpeg_path.py` | Auto-detection and PATH setup for bundled FFmpeg |
| `demucs_processor.py` | PyTorch/Demucs model loading and inference with caching |
| `uploader.py` | YouTube upload via Google API (OAuth2 authentication) |
| `exceptions.py` | Custom exception hierarchy rooted at `YTAudioFilterError` |

### Exception Hierarchy

All errors inherit from `YTAudioFilterError`:
- `ValidationError` - Input file/URL validation failures
- `FFmpegError` - FFmpeg processing errors (includes returncode and stderr)
- `DemucsError` - AI model errors
- `PrerequisiteError` - Missing dependencies (FFmpeg, CUDA, Demucs, yt-dlp)
- `YouTubeDownloadError` - YouTube download failures
- `YouTubeUploadError` - YouTube upload failures (defined in uploader.py)

## Autonomous Discovery Pipeline

The project has an autonomous mode that runs daily on a Linux VM: discovers Turkish children's videos via YouTube Data API, filters by copyright risk, processes with Demucs, and uploads to YouTube.

### Discovery Architecture

| Module | Responsibility |
|--------|----------------|
| `config.py` | YAML config system (`~/.yt-audio-filter/discovery_config.yaml`) |
| `discovery.py` | YouTube Data API v3 search, enrichment, candidate selection |
| `copyright_scorer.py` | Heuristic risk scoring (0.0-1.0) based on channel size, verification, keywords |
| `quota_tracker.py` | Daily API quota tracking (10K units/day free tier) |
| `scheduler.py` | Orchestrates: API discovery -> copyright filter -> download -> Demucs -> upload |

### Scheduler Flow

1. **API Discovery** (primary): Search queries -> deduplicate -> enrich with video/channel details -> copyright score -> filter
2. **Channel Scraping** (fallback): Direct yt-dlp scraping of whitelisted channels
3. **Processing**: Download -> Demucs vocal isolation -> upload to YouTube

### CLI Entry Points

```bash
yt-scheduler --config ~/.yt-audio-filter/discovery_config.yaml --verbose  # Full pipeline
yt-scheduler --dry-run --verbose      # Preview without processing
yt-scheduler --init-config            # Generate default config YAML
yt-discover --api-key KEY --dry-run   # Discovery only (no processing)
```

### Copyright Scoring

Videos are scored 0.0 (safe) to 1.0 (high risk). Default threshold: reject if > 0.5.

Key signals: channel subscriber count (>100K = +0.2), verified channel (+0.35), official keywords in title (+0.2), compilation signals on small channels (-0.15), Creative Commons (-0.3).

All major Turkish broadcasting channels are blacklisted by default (Cartoon Network TR, TRT Cocuk, Niloya, Pepee, Kukuli, Disney TR, etc.) because their Content ID systems block re-uploads.

## VM Deployment

### Quick Deploy (any Linux VM with 4+ GB RAM)

```bash
git clone https://github.com/Affiliat0r/yt-audio-filter.git ~/yt-filter-workspace
cd ~/yt-filter-workspace
git checkout feature/autonomous-pipeline
bash deploy/setup.sh
```

`deploy/setup.sh` runs all steps: system packages (apt/dnf), Python venv, PyTorch CPU, ffmpeg, project install, default config generation, cron setup (every 6 hours).

### Required Credentials (not in repo)

These must be manually placed on the VM after setup:

| File | Location | Purpose |
|------|----------|---------|
| `YOUTUBE_API_KEY` | `~/.env-yt-filter` | YouTube Data API key for video discovery |
| `client_secrets.json` | `~/.yt-audio-filter/` | Google OAuth client credentials |
| `oauth_token.pickle` | `~/.yt-audio-filter/` | OAuth token for YouTube uploads (generate locally, scp to VM) |
| `cookies.txt` | `~/yt-filter-workspace/` | Optional: yt-dlp cookies for bot detection bypass |

The OAuth token must be generated on a machine with a browser first:
```bash
# On local machine with browser:
pip install -e ".[upload]"
yt-audio-filter --list-playlists   # triggers OAuth browser flow, saves token

# Copy to VM:
scp ~/.yt-audio-filter/oauth_token.pickle user@vm:~/.yt-audio-filter/
scp ~/.yt-audio-filter/client_secrets.json user@vm:~/.yt-audio-filter/
```

Note: Google OAuth tokens in "Testing" mode expire every 7 days. Publish the OAuth consent screen for permanent tokens.

### Deploy Scripts

All in `deploy/oracle-cloud/` (work on any Linux, not Oracle-specific):

| Script | Purpose |
|--------|---------|
| `setup-vm.sh` | System packages (apt/dnf), workspace dirs, env file |
| `install-dependencies.sh` | Python venv, PyTorch CPU, project install |
| `setup-project.sh` | Clone/update repo, generate default config |
| `setup-credentials.sh` | Interactive credential setup guide |
| `setup-cron.sh` | Cron job every 6 hours |
| `run-pipeline.sh` | Cron wrapper: env loading, scheduler run, cleanup |
| `monitor.sh` | Status check: disk, processed videos, quota, logs |

### Monitoring

```bash
# Check logs
tail -f ~/yt-filter-workspace/logs/cron.log

# Check status
bash ~/yt-filter-workspace/deploy/oracle-cloud/monitor.sh

# Check processed videos
python3 -c "import json; d=json.load(open('processed_videos.json')); print(len(d['processed_ids']), 'videos processed')"
```

## Code Style

- Python 3.10+ with type hints
- Black formatter with 100-char line length
- mypy for type checking (ignore_missing_imports=true for external libs)

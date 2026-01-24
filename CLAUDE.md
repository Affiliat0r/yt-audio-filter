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
- **pywinauto** (optional, Windows only): For GUI automation fallback when bot detection blocks downloads. Install with `pip install pywinauto`
- **YoutubeDownloader.exe** (optional): GUI application for manual/automated downloads. Get from https://github.com/Tyrrrz/YoutubeDownloader

## Architecture

### Input Flow

The CLI ([cli.py](src/yt_audio_filter/cli.py)) detects whether input is a YouTube URL or local file:
- **YouTube URL**: Downloads video to cache directory via download fallback chain, processes it
- **Local file**: Processes directly

### YouTube Download Fallback Chain

When YouTube bot detection blocks downloads, the tool automatically tries multiple methods in sequence:

1. **yt-dlp with Android client** - Uses Android player client API + browser cookies (Firefox/Chrome) + proxy support
2. **Invidious API** - Free YouTube frontend API (GitHub: iv-org/invidious)
3. **Piped API** - Privacy-focused YouTube frontend (GitHub: TeamPiped/Piped)
4. **Cobalt API** - Media downloader service (GitHub: imputnet/cobalt)
5. **GUI Automation** - Automates YoutubeDownloader.exe using pywinauto (Windows only)

CLI arguments for bot detection bypass:
- `--cookies-from-browser firefox` - Extract authentication cookies from Firefox
- `--proxy socks5://127.0.0.1:1080` - Route downloads through SOCKS5/HTTP proxy
- `--gui-downloader-path C:\path\to\YoutubeDownloader.exe` - Specify GUI app path

See [GUI_AUTOMATION.md](GUI_AUTOMATION.md) for detailed documentation.

### Processing Pipeline

Three stages orchestrated by [pipeline.py](src/yt_audio_filter/pipeline.py):

1. **Extract Audio** - FFmpeg extracts audio from video as WAV ([ffmpeg.py](src/yt_audio_filter/ffmpeg.py))
2. **Isolate Vocals** - Demucs AI separates vocals from background music ([demucs_processor.py](src/yt_audio_filter/demucs_processor.py))
3. **Remux Video** - FFmpeg combines original video stream (lossless copy) with processed vocals

### Key Modules

| Module | Responsibility |
|--------|----------------|
| `cli.py` | Argparse CLI, URL/file detection, entry point via `main()` |
| `youtube.py` | YouTube URL validation and video download via yt-dlp, fallback orchestration |
| `gui_downloader.py` | GUI automation for YoutubeDownloader.exe (final fallback) |
| `invidious_downloader.py` | Fallback downloader using Invidious API (GitHub: iv-org/invidious) |
| `piped_downloader.py` | Fallback downloader using Piped API (GitHub: TeamPiped/Piped) |
| `cobalt_downloader.py` | Fallback downloader using Cobalt API (GitHub: imputnet/cobalt) |
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

## Code Style

- Python 3.10+ with type hints
- Black formatter with 100-char line length
- mypy for type checking (ignore_missing_imports=true for external libs)

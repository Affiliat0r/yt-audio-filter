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

The new `yt-quran-overlay` tool uses an application-less chain in
`youtube.download_stream()`:

1. **pytubefix client cascade** (ANDROID_VR → IOS → ANDROID → MWEB → TV → WEB) — pure Python, no external runtimes
2. **yt-dlp** with `tv_embedded`/`ios`/`web_embedded`/`android` client cascade and a `bestvideo / bestaudio / 18 / b` format fallback. Combined formats are post-stripped with FFmpeg `-c copy` to yield a clean stream-only file.

The legacy `yt-audio-filter` tool still uses `download_youtube_video()` which
keeps the old Invidious/Piped/Cobalt/YTDownloader.exe fallback chain.

### Optional: bgutil PO Token provider (advanced)

The [bgutil-ytdlp-pot-provider](https://github.com/Brainicism/bgutil-ytdlp-pot-provider) plugin exposes more
yt-dlp formats by supplying gvs PO Tokens. The plugin is wired in via
`download_stream()`'s extractor args (`youtubepot-bgutilscript: script_path:
__disabled__` skips the slow Deno cold-start; the HTTP plugin auto-uses a
server on `127.0.0.1:4416` if running).

**Setup (one-time):**
```bash
pip install bgutil-ytdlp-pot-provider           # the plugin (auto-loaded by yt-dlp)
git clone https://github.com/Brainicism/bgutil-ytdlp-pot-provider.git ~/bgutil-ytdlp-pot-provider
cd ~/bgutil-ytdlp-pot-provider/server
npm install && npx tsc                           # build TypeScript → build/main.js
```

**Run server (foreground or via your service manager of choice):**
```bash
node ~/bgutil-ytdlp-pot-provider/server/build/main.js
```

**Current limitation (April 2026):** PO Tokens unlock the *format list* (1080p
appears) but the unlocked formats are SABR-streamed by YouTube (yt-dlp
issue [#12482](https://github.com/yt-dlp/yt-dlp/issues/12482)) — actual
downloads return `403 Forbidden` or empty fragments. So the server
currently provides no real benefit for our content mix; keep it stopped
until yt-dlp ships SABR support. Documented for forward compatibility.

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

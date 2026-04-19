# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

The repo ships **two** CLI tools built on a shared FFmpeg / yt-dlp / uploader
stack:

- **`yt-audio-filter`** — original tool. Removes background music from MP4 videos using Facebook's Demucs AI model. Accepts a local file or a YouTube URL, preserves vocals, remuxes losslessly.
- **`yt-quran-overlay`** — added in feat/quran-overlay + feat/surah-input. Combines a YouTube visual (e.g. Toy Factory cartoons) with a separate Quran recitation audio, loops the visual to match audio length, applies EBU R128 loudnorm, overlays a channel logo, and optionally uploads to YouTube with a templated description. Three invocation modes (see "yt-quran-overlay tool" section below).

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

### yt-quran-overlay invocation

```bash
# Manual mode — explicit URLs
yt-quran-overlay --video-url URL --audio-url URL --metadata meta.json [--upload]

# Discovery mode — pick pairs from two channels (duration-matched, state-tracked)
yt-quran-overlay --video-channel @toyfactorycartoon \
                 --audio-channel @QuranHadeesIndia \
                 --count 1 --metadata meta.json [--upload]

# Surah mode — name one or more surahs (canonical names OR direct URLs)
yt-quran-overlay --surah Al-Fatiha \
                 --surah https://www.youtube.com/watch?v=0VIXkx8oSJM \
                 --surah An-Nas \
                 --audio-channel @QuranHadeesIndia \
                 --video-channel @toyfactorycartoon \
                 --metadata meta.json [--upload] [--upscale]
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

### SABR investigation summary (April 2026)

For heavily-protected content (e.g. Toy Factory cartoons), here is the
empirically-tested state of available downloaders. None bypass SABR:

| Approach | Result |
|----------|--------|
| `yt-dlp` default | Format 18 (360p) only; 1080p formats exist but download returns 403 |
| `yt-dlp + bgutil PO Token (HTTP server)` | Same as above; tokens unlock *listing*, not download |
| `pytubefix` (ANDROID_VR / IOS / WEB / TV / MWEB cascade) | Bot-detected on every client for protected videos |
| `Invidious` public instances | Ecosystem effectively dead; only 1 instance with API and it returns 403 |
| `Cobalt v11` self-hosted (Docker), no cookies | Extracts metadata but tunnel returns 0-byte content silently |
| `Cobalt v11` self-hosted with Firefox cookies | `error.api.youtube.api_error` on every URL — Google rejects cookies from container IP |
| `Cobalt v11` + `YOUTUBE_SESSION_SERVER` (bgutil) | Format extraction succeeds (1080p h264 filename), tunnel still returns 0 bytes — SABR blocks the actual stream even with PO Tokens |

The realistic path forward is to wait for yt-dlp's native SABR support
(active development on [#12482](https://github.com/yt-dlp/yt-dlp/issues/12482))
or accept format 18 (360p combined) for the heavily-protected subset of
videos. The discovery pipeline gracefully skips pairs that fail and
moves on, so the channel never blocks on a single bad pair.

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

Shared infrastructure (both tools):
| Module | Responsibility |
|--------|----------------|
| `youtube.py` | YouTube URL validation. `download_stream()` is the application-less chain for `yt-quran-overlay`. `download_youtube_video()` is the legacy GUI-automation chain for `yt-audio-filter`. |
| `yt_metadata.py` | Fetch YouTube title/channel/description/tags without downloading the media. Powers auto-surah/reciter detection. |
| `ffmpeg.py` | Subprocess wrappers over `ffmpeg` / `ffprobe`. Includes `check_nvenc_available()` used by both tools. |
| `ffmpeg_path.py` | Auto-detects bundled or system FFmpeg and sets PATH. |
| `uploader.py` | YouTube upload via Google API (OAuth2). `upload_to_youtube()` auto-generates SEO metadata for the music-removal flow; `upload_with_explicit_metadata()` takes caller-supplied title/description/tags for the overlay flow. |
| `exceptions.py` | Custom exception hierarchy rooted at `YTAudioFilterError`. |

Legacy `yt-audio-filter` (music removal):
| Module | Responsibility |
|--------|----------------|
| `cli.py` | Argparse CLI, URL/file detection, entry point. |
| `pipeline.py` | `process_video()` orchestrates the 3-stage pipeline (extract → Demucs → remux). |
| `demucs_processor.py` | PyTorch/Demucs model loading and inference with caching. |
| `gui_downloader.py` | GUI automation for YoutubeDownloader.exe (final fallback in the legacy chain). |
| `invidious_downloader.py` | Fallback downloader using Invidious API (most public instances dead as of April 2026). |

`yt-quran-overlay` (Quran recitation overlay):
| Module | Responsibility |
|--------|----------------|
| `overlay_cli.py` | Argparse entry for `yt-quran-overlay`. Three modes: manual / discovery / surah. |
| `overlay_pipeline.py` | `run_overlay()` (manual), `run_overlay_batch()` (discovery), `run_overlay_surahs()` (surah-input). Orchestrates download → (optional upscale) → render → upload. |
| `ffmpeg_overlay.py` | Render command builder: two-pass EBU R128 loudnorm, video loop, logo overlay, NVENC GPU encoding with libx264 fallback. |
| `pytube_downloader.py` | Primary downloader using pytubefix; client cascade (ANDROID_VR → IOS → ANDROID → MWEB → TV → WEB). |
| `audio_concat.py` | Concatenate multiple audio files. Prefers concat-demuxer `-c copy` on matching signatures, falls through to `filter_complex` AAC re-encode when copy fails (webm/opus from YouTube often matches on signature but rejects `-c copy` at container level). |
| `channel_discovery.py` | Scrape a YouTube channel for candidate videos; dropped shorts / unknown-duration entries. |
| `pair_selector.py` | Duration-based pairing for discovery mode. Ranks visuals (duration≥audio first, least slack; else longest-short). Selects N non-overlapping pairs. |
| `pair_state.py` | JSON state at `state/processed_pairs.json` to avoid re-producing pairs across discovery runs. |
| `surah_detector.py` | Regex-based recognizer for 114 surahs + Ayatul Kursi + ~18 well-known qaris. `detect_surah()` returns first match; `detect_all_surahs()` returns all for compilation-avoidance scoring. |
| `surah_resolver.py` | Resolve user-supplied surah names against an audio channel. Each request can also be a direct YouTube URL (bypasses channel scrape). Scoring: `(n_surahs_in_title ASC, duration ASC, channel_order ASC)` → standalone titles beat compilations; shorter edits beat longer. |
| `upscale.py` | Real-ESRGAN (`realesrgan-ncnn-vulkan`) upscale of the visual source. Per-visual cache at `cache/upscaled_<video_id>.mp4`. Default `realesr-animevideov3-x2` (720p) because x2 is 2-3× faster than x3 on the GPU and the render matches 720p output when `--upscale` is set. |
| `metadata.py` | Load YouTube metadata JSON (title, description_template, tags, logo_path). Title and description are `string.Template` that get rendered late with auto-extracted vars (`$detected_surah`, `$surah_tag`, `$reciter`, `$reciter_tag`, `$surah_count`, etc.). |

### Exception Hierarchy

All errors inherit from `YTAudioFilterError`:
- `ValidationError` - Input file/URL validation failures
- `FFmpegError` - FFmpeg processing errors (includes returncode and stderr)
- `DemucsError` - AI model errors
- `PrerequisiteError` - Missing dependencies (FFmpeg, CUDA, Demucs, yt-dlp, Real-ESRGAN binary)
- `YouTubeDownloadError` - YouTube download failures
- `YouTubeUploadError` - YouTube upload failures (defined in uploader.py)
- `OverlayError` - yt-quran-overlay errors: metadata JSON issues, missing surah matches, pair exhaustion, logo-missing-on-upload, etc.
- `ChannelDiscoveryError` - Raised by `channel_discovery.fetch_candidates` when a channel yields no usable videos.

## yt-quran-overlay tool

Separate pipeline from the legacy music-removal tool. Reuses the FFmpeg,
YouTube download, and upload infrastructure but adds its own CLI, pipeline,
and extras (audio concat, upscale, channel discovery, surah resolution).

### Invocation modes

Three modes, detected by which args are set (mutually exclusive):

| Mode | Trigger | What it does |
|------|---------|--------------|
| **manual** | `--video-url` + `--audio-url` | Render one video against the given pair. |
| **discovery** | `--video-channel` + `--audio-channel` (+ `--count N`) | Pull N pairs from two channels, rank by duration, skip already-processed pairs via `state/processed_pairs.json`, render each. |
| **surah** | `--surah ...` (+ channels) | Each `--surah` is either a canonical name resolved against the audio channel OR a direct YouTube URL (override for surahs the channel doesn't carry). Audios concatenated in order; longest visual from the video channel is looped to cover it. |

### Render pipeline

1. Download visual video-only stream (`cache/video_<id>.mp4`) — see "Download chain" below.
2. Download audio-only stream per surah (`cache/audio_<id>.webm` or `.m4a`).
3. (Surah mode only) Concat audios via `audio_concat.concat_audio`. Tries concat-demuxer `-c copy`; on failure falls through to `filter_complex` AAC re-encode. Cached at `cache/concat_<joined_ids>.m4a`.
4. (Optional, `--upscale`) Real-ESRGAN upscale the visual at x2 (360p → 720p), cached at `cache/upscaled_<video_id>.mp4`. First run for a given visual is slow (~14 fps GPU throughput); subsequent runs reuse the cache instantly.
5. Render via `ffmpeg_overlay.render_overlay()`: two-pass EBU R128 loudnorm on audio, `-stream_loop -1` on the visual, optional PNG logo overlay at 15% width, NVENC h264 (cq=19, preset p5) when available, libx264 (crf=18, preset medium) otherwise. Output bounded by `-t <audio_duration>` so it stops at the recitation end.
6. (Optional, `--upload`) Upload via `upload_with_explicit_metadata()` with title/description rendered from the metadata `string.Template` using auto-extracted vars.

### Metadata template

`examples/metadata-surah-arrahman.json` is the reference. Title and
description use `string.Template` with `$var` placeholders. Variables are
merged from `description_vars` (user-provided, wins on conflict) plus
auto-extracted fields:

- `$detected_surah` — canonical surah name (or `" + "`-joined list in surah mode)
- `$surah_tag` — PascalCase tag (e.g. `AtTin`), joined concatenation in multi-surah
- `$surah_count` — number of surahs (surah mode only)
- `$reciter`, `$reciter_tag` — from `detect_reciter()` on the audio title; fall back to the audio uploader's channel name
- `$audio_title`, `$audio_channel`, `$audio_uploader` — raw YouTube fields

**Guard:** if the template references `$detected_surah` but no surah was matched, the pipeline aborts before upload to prevent publishing a broken title.

### Resolver insights (surah mode)

`surah_resolver.resolve_surahs()` scores each candidate video by
`(n_surahs_detected_in_title ASC, duration ASC, channel_order ASC)`. This
beats compilations with standalones: e.g. `"Surah An Naas - Salim Bahanan"`
(1 surah, 66 s) beats `"Juz 30 - Surah Adh Dhuha - Surah An Naas"`
(2 surahs, 1290 s) even when the compilation is newer on the channel.
Without this scoring we once produced a 24-minute output where 3 was
expected.

When a surah isn't on the channel at all (e.g. Al-Ikhlas/Al-Falaq on
@QuranHadeesIndia), pass the YouTube URL directly as the `--surah` value.
The resolver detects URL-vs-name per item and mixes them in the user's
order.

### Download chain (yt-quran-overlay only)

`youtube.download_stream()` is the application-less path:

1. **pytubefix client cascade** (ANDROID_VR → IOS → ANDROID → MWEB → TV → WEB) — pure Python, no external runtimes. Delivers 160 kbps Opus audio for most videos.
2. **yt-dlp fallback** with `tv_embedded/ios/web_embedded/android` client cascade and `bestvideo[ext=mp4]/bestvideo/18/b` format fallback. Format 18 is combined 360p mp4; post-downloaded, FFmpeg strips to the requested stream via `-c copy` when possible.

No YTDownloader.exe, no Docker, no Node.js server required in the default path.

The legacy `yt-audio-filter` still uses `download_youtube_video()` which
keeps the old Invidious/Piped/Cobalt/YTDownloader.exe fallback chain.

### Upscale (optional, `--upscale`)

Real-ESRGAN via `realesrgan-ncnn-vulkan` binary (Vulkan — zero Python deps,
self-contained). Model: `realesr-animevideov3-x2` (cartoon-tuned, 2×
upscale → 720p from 360p source). Binary location:
`tools/realesrgan/realesrgan-ncnn-vulkan.exe` (gitignored; download once
from https://github.com/xinntao/Real-ESRGAN/releases).

Pipeline: extract frames (PNG) with FFmpeg → batch upscale → reassemble
at original FPS with the same encoder args as the main render (NVENC
when available).

**Render resolution defaults to 1280×720 when `--upscale` is set** (else
1920×1080). This matches the x2 upscale target and avoids a second scale
that would discard detail. Override with `--resolution 1920x1080` if you
want 1080p output (render will upscale the 720p upscaled source back up).

### NVENC GPU encoding

`ffmpeg_overlay._video_encoder_args()` auto-detects NVENC via
`ffmpeg.check_nvenc_available()`. When present: `h264_nvenc` with
`preset=p5 tune=hq rc=vbr cq=19 b:v=0` (≈ libx264 `crf 18-19` quality).
Otherwise: libx264 `medium/crf=18`. Same detection is reused in
`upscale.py` for the frame reassembly step.

### Memory gotchas

- **Discovery mode vs manual mode upload defaults.** Discovery is a production content pipeline; when the user invokes it, `--upload` is expected. Manual mode is for smoke testing; don't pass `--upload` unless the user explicitly asks ("upload this", "push it to the channel").
- **Surah detector short-name boundaries.** Short surah names (Qaf, Sad, Hud, Yunus, Saba, Fatir, Nuh, Abasa) use `(?<![a-z])X(?![a-z])` instead of `\b` because `_` (underscore) is a word character in regex — titles like `"Surah Al Qaf__Salim Bahanan"` broke `\b` boundaries.
- **bgutil script-mode cold start.** The `bgutil-ytdlp-pot-provider` plugin, if installed, auto-runs a Deno script per PO-token request. First invocation downloads npm deps and times out at 15 s. `download_stream()` and `yt_metadata.fetch_yt_metadata()` neutralize this by passing `youtubepot-bgutilscript: script_path: __disabled__` in `extractor_args`.

## Streamlit UI

Local single-page web app around `yt-quran-overlay`'s surah-numbers mode.
Lets you pick surahs, reciter (with an inline audio sample), and a
cartoon visual from a thumbnail grid, then render / preview / upload
without touching a shell.

### Invocation

```bash
pip install -e ".[app]"
streamlit run src/yt_audio_filter/streamlit_app.py
```

Opens on `http://localhost:8501`. Single-session, no auth — assumes it's
behind the OS.

### UI surface

- **Surah picker** — multiselect over all 114 surahs. Selection order is
  preserved and drives concat order.
- **Reciter picker** — selectbox over the ~20 reciters in
  `src/yt_audio_filter/data/reciters.json`; an inline `st.audio` widget
  plays the Al-Fatiha sample before you commit.
- **Thumbnail gallery** — grouped by channel from
  `config/channels.json` (5 seeded: Toy Factory, Tidi Kids, KidsTV123,
  Little Baby Bum, Billion Surprise Toys). Single-select via per-tile
  checkbox. "Refresh catalog" toggle invalidates the on-disk
  `cache/cartoon_catalog.json` + the `st.cache_data` layer.

### Data sources

- **`quran_audio_source.py`** — resolves `(surah_number, reciter)` →
  MP3 URL via `data/reciters.json` (20 verified reciters on
  quranicaudio.com: Mishary, Sudais, Maher, Shuraim, Al-Juhani,
  Ath-Thubaity, etc.). Caches to `cache/audio_surah_<num>_<slug>.mp3`.
- **`cartoon_catalog.py`** — reads `config/channels.json`, scrapes each
  via the existing pytubefix channel path, caches the merged list at
  `cache/cartoon_catalog.json` (24 h TTL). `ensure_thumbnail()` pulls
  `i.ytimg.com/vi/<id>/hqdefault.jpg` to `cache/thumbs/`.

### Audio source caveat — Salim Bahanan

Salim Bahanan is NOT on quranicaudio.com. The Streamlit picker only
offers reciters that ARE there; the reciters JSON notes this and
substitutes Abdullah Awad al-Juhani in the Bahanan slot. For Salim
Bahanan specifically, stay on the CLI and use surah-name mode with
direct `--surah https://...` URL overrides (see the
`yt-quran-overlay` section above).

### Backend entrypoints

- `overlay_pipeline.run_overlay_from_surah_numbers(surah_numbers,
  reciter_slug, visual_video_id, metadata, *, output_path=None,
  cache_dir=Path("cache"), resolution=None, upscale=False,
  cookies_from_browser=None, proxy=None, upload=False) -> OverlayResult`
  — downloads each surah audio, concats, downloads + optionally upscales
  the visual, renders via `ffmpeg_overlay.render_overlay`.
  `output_path=None` → `tempfile.gettempdir()` MP4, so the UI doesn't
  pollute `output/`.
- `overlay_pipeline.upload_rendered(rendered_path, metadata, *,
  surah_numbers, reciter_slug, visual_title=None) -> str` — uploads an
  already-rendered file; rebuilds the same `$detected_surah / $surah_tag
  / $reciter` auto-vars so title/description match what a `upload=True`
  render would have produced. Drives the separate "Upload to YouTube"
  button — render first, preview, then publish.

### CLI equivalent

Same backend is reachable without the UI via
`yt-quran-overlay --surah-number 1 --surah-number 112 --reciter alafasy
--video-id <yt_id> --metadata meta.json [--upload] [--upscale]`. Useful
for scripted / cron jobs where the UI isn't wanted.

### Output + upload flow (UI path)

- Output: `tempfile.NamedTemporaryFile`-style temp MP4 (persists until
  the Streamlit process exits). UI shows it via `st.video` and serves
  the bytes via `st.download_button`. No `output/` directory is touched.
- Upload: separate button, render-first / upload-later. Uses the same
  `metadata.json` template; title / description render from auto-vars
  at upload time, not render time.

## Code Style

- Python 3.10+ with type hints
- Black formatter with 100-char line length
- mypy for type checking (ignore_missing_imports=true for external libs)

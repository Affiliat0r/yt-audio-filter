# Quran Overlay Tool — Design Document

**Date:** 2026-04-18
**Branch:** `feat/quran-overlay`
**Status:** Design approved, ready for implementation planning

## Overview

A new CLI tool, `yt-quran-overlay`, that combines a visual YouTube video (e.g., 3D animation) with a separate Quran audio recitation from another YouTube video. The tool mutes the original video audio entirely, loops the video to match the audio duration, normalizes the audio to YouTube's loudness standard, overlays a channel logo, and optionally uploads the result to YouTube with metadata from a JSON template.

This is a separate workflow from the existing `yt-audio-filter` tool. Demucs is not used — original audio is discarded, not isolated.

## Goals

- Produce publish-ready Quran recitation videos with animated visual backing.
- Reuse the existing YouTube download infrastructure (fallback chain, caching).
- Reuse the existing YouTube upload infrastructure with templated metadata.
- Keep the existing `yt-audio-filter` music-filter flow untouched.

## Non-Goals

- Arabic ayah text overlay (requires timed subtitle data — separate feature).
- Bulk batch processing of multiple video+audio pairs per run.
- Automatic thumbnail generation.
- Color grading or saturation filters.
- Music filtering / vocal isolation (existing tool handles that).

## CLI

```bash
yt-quran-overlay \
  --video-url "https://youtube.com/watch?v=VISUAL" \
  --audio-url "https://youtube.com/watch?v=RECITATION" \
  --metadata examples/metadata-surah-arrahman.json \
  [--upload] \
  [--cache-dir cache] \
  [--output-dir output] \
  [--logo PATH] \
  [--logo-position {top-left,top-right,bottom-left,bottom-right}] \
  [--resolution 1920x1080] \
  [--max-duration 7200] \
  [--force]
```

Only `--logo` and `--logo-position` override the corresponding metadata JSON fields. Title, description, tags, category, and privacy come exclusively from the JSON — not overridable via CLI (keeps the CLI surface small; edit the JSON to change metadata).

`--max-duration` defaults to 7200 s (2 h). If the Quran audio duration exceeds this, the render aborts with `OverlayError` — guards against accidental multi-hour re-encodes.

`--force` overwrites any existing output file. Without it, the run aborts when `output/<audio_id>_<video_id>.mp4` already exists.

A separate entry point in `pyproject.toml`:

```toml
[project.scripts]
yt-audio-filter = "yt_audio_filter.cli:main"
yt-quran-overlay = "yt_audio_filter.overlay_cli:main"
```

## Architecture

### Pipeline (4 stages)

1. **Download visual video** — video-only stream via existing fallback chain
2. **Download Quran audio** — audio-only stream via existing fallback chain
3. **Render** — single FFmpeg invocation: loop video + mute original + normalize audio + overlay logo + mux
4. **Upload** (optional) — existing `uploader.py` with metadata from JSON

### Module Layout

**New files:**

| File | Responsibility |
|------|----------------|
| `src/yt_audio_filter/overlay_cli.py` | Argparse entry point for `yt-quran-overlay` |
| `src/yt_audio_filter/overlay_pipeline.py` | Orchestrates the 4 stages |
| `src/yt_audio_filter/ffmpeg_overlay.py` | FFmpeg command construction (loop, normalize, overlay, mux) |
| `src/yt_audio_filter/metadata.py` | JSON metadata loader + template renderer |
| `tests/test_metadata.py` | Unit tests for JSON parsing and template rendering |
| `tests/test_ffmpeg_overlay.py` | Unit tests for FFmpeg command strings |
| `tests/test_overlay_pipeline.py` | Integration tests with mocked downloaders |
| `examples/metadata-surah-arrahman.json` | Example metadata file |

**Modified files:**

| File | Change |
|------|--------|
| `src/yt_audio_filter/youtube.py` | Add `mode` parameter: `video-only` / `audio-only` / `video+audio`. Existing `download_video()` becomes a wrapper passing `video+audio` — no breaking change. |
| `src/yt_audio_filter/exceptions.py` | Add `OverlayError` under `YTAudioFilterError` |
| `pyproject.toml` | Add `yt-quran-overlay` entry point |

## Download Layer

Stream-selective downloads minimize bandwidth and disk usage.

- **Video URL** → `bestvideo[ext=mp4]/bestvideo` yt-dlp format → `cache/video_<video_id>.mp4`
- **Audio URL** → `bestaudio[ext=m4a]/bestaudio` yt-dlp format → `cache/audio_<audio_id>.m4a`

The existing 5-step fallback chain (yt-dlp Android → Invidious → Piped → Cobalt → GUI) remains active. Backends that don't support stream-selective downloads fall back to downloading both and extracting the needed stream with FFmpeg `-c copy` (negligible overhead).

Cache-hit behavior from commit `d5daceb` is preserved: if `cache/video_<id>.mp4` exists, skip its download. Two URLs = two independent cache checks.

Both downloads must succeed before render starts. Partial failures leave any completed download in cache for retry.

## Render Pipeline

Single FFmpeg invocation. Re-encoding is unavoidable because loop + overlay + normalize cannot use `-c copy`.

### Inputs

- `-stream_loop -1 -i cache/video_<id>.mp4` — loop video indefinitely
- `-i cache/audio_<id>.m4a` — Quran audio
- `-i <logo-path>` — logo PNG, only if `--logo` or `metadata.logo_path` is set

### Filter Graph

```
[0:v] scale=1920:1080,setsar=1 [vscaled];
[2:v] scale=w=iw*0.08:h=-1 [logo];                    # only when logo present
[vscaled][logo] overlay=x=<corner-x>:y=<corner-y> [vout];
[1:a] loudnorm=I=-16:TP=-1.5:LRA=11:measured_I=<m_I>:measured_TP=<m_TP>:measured_LRA=<m_LRA>:measured_thresh=<m_thr>:offset=<off>:linear=true [aout]
```

When no logo is present, the filter graph omits the `[2:v]` scale and the `overlay` node entirely — `[vout] = [vscaled]`. A single filter-graph assembler helper constructs both the filter string and the input list based on `has_logo: bool`, so input indices stay consistent.

**Two-pass loudnorm.** A first ffmpeg pass with `loudnorm ... print_format=json -f null -` captures the measured `input_i`, `input_tp`, `input_lra`, `input_thresh`, and `target_offset` values; the second pass uses these in `measured_*` parameters with `linear=true`. This avoids true-peak overshoot that single-pass `loudnorm` can produce on dynamic content. The extra cost is one audio-only analysis pass — no video re-encoding.

**`-stream_loop` ordering.** The `-stream_loop -1` flag must appear *before* its `-i` video input (it's an input option). Swapping the order silently makes the video play once. Unit tests assert the exact arg list.

### Encoding & Output

- `-map [vout] -map [aout]`
- Video: `-c:v libx264 -preset medium -crf 18`
- Audio: `-c:a aac -b:a 192k`
- Duration: `-t <audio-duration>` (pre-computed via `ffprobe` on the audio file) — replaces `-shortest`
- Output: `output/<audio_id>_<video_id>.mp4`

### Key Choices

- **Resolution normalized to 1920×1080** (configurable via `--resolution`). Prevents mismatched sizes between visual source and logo.
- **Logo default position: top-left.** Other corners available via flag. Padding: 20 px from edges. Logo width: 8% of video width.
- **Loudnorm target: I=-16, TP=-1.5, LRA=11** — YouTube's recommended loudness standard (EBU R128). Applied via two-pass (see above).
- **Output filename collision.** If `output/<audio_id>_<video_id>.mp4` exists and `--force` is not set, abort with a clear message. No silent overwrite.

## Metadata Template

JSON file loaded via `--metadata <path>`:

```json
{
  "title": "Surah Ar-Rahman - Salim Bahanan | Calming Quran Recitation with Toy Factory Animation",
  "description_template": "Welcome to {channel_name}!\n\n**Reciter:** {reciter}\n**Surah:** {surah_name}\n**Visuals:** {visual_source}\n\n#Quran #{surah_tag} #{reciter_tag}",
  "description_vars": {
    "channel_name": "Uma Nahfa",
    "surah_name": "Ar-Rahman",
    "reciter": "Salim Bahanan",
    "visual_source": "Toy Factory",
    "surah_tag": "SurahArRahman",
    "reciter_tag": "SalimBahanan"
  },
  "tags": ["Surat Ar Rahman", "Salim Bahanan", "Murottal Quran"],
  "category_id": "27",
  "privacy_status": "private",
  "logo_path": "assets/uma_nahfa_logo.png",
  "logo_position": "top-left"
}
```

- `description_template` uses Python `string.Template` with `$var` placeholders, not `str.format()`. `Template.substitute()` tolerates literal braces in description text and fails loudly on unknown `$var` tokens.
- Per-video edits touch only the vars block, not the full description.
- Missing placeholders or unknown fields fail fast with a clear `OverlayError`.
- `privacy_status` defaults to `private` when absent — safer for automated runs; the user explicitly sets `public` when ready.
- `logo_path` and `logo_position` here mirror the CLI flags. CLI flags override JSON.
- **Relative `logo_path` resolves against the JSON file's directory**, not CWD — so metadata files remain portable when moved between folders.

### `VideoMetadata` Dataclass

```python
@dataclass
class VideoMetadata:
    title: str
    description: str  # pre-rendered from template
    tags: list[str]
    category_id: str
    privacy_status: str
    logo_path: Path | None
    logo_position: str  # "top-left" | "top-right" | "bottom-left" | "bottom-right"
```

## Upload

- Runs only when `--upload` is passed AND metadata is present.
- Delegates to existing `uploader.py` `upload_video()`.
- On upload failure: the rendered MP4 remains in `output/` for manual re-upload.

## Error Handling

All errors inherit from `YTAudioFilterError`.

| Condition | Exception |
|-----------|-----------|
| Download failure | `YouTubeDownloadError` (existing) |
| FFmpeg failure | `FFmpegError` (existing — includes returncode + stderr) |
| Metadata JSON invalid (missing key, bad template var) | `OverlayError` (new) |
| Logo path does not exist | `OverlayError`, raised before render starts |
| Upload failure | `YouTubeUploadError` (existing) |

## Testing

The repository currently has no `tests/` directory. This design introduces one alongside a minimal `pytest` configuration:

- Add `tests/` with `__init__.py` and a `tests/fixtures/` subdirectory for the small video/audio test assets.
- Add `pytest.ini` (or a `[tool.pytest.ini_options]` block in `pyproject.toml`) configuring test discovery under `tests/`.

Test layers:

- **Unit:** metadata parsing, template rendering, FFmpeg command string construction (assertions on arg list, no subprocess).
- **Integration:** fake downloaders + real FFmpeg on small fixtures (10 s video, 15 s audio) to verify loop + normalize + mux end-to-end. Assert the exact arg order around `-stream_loop`.
- **No YouTube upload test** — mock at the `uploader.py` interface boundary.

## Open Questions

None at design time. Implementation may surface:

- Whether any of the fallback downloaders (Invidious/Piped/Cobalt) choke on stream-selective requests in practice.
- Whether `-preset slow -crf 20` produces smaller files than `medium/18` for loop-heavy content (defer to implementation measurement).

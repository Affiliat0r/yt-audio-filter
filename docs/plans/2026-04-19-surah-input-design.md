# Surah-Input Mode — Design Document

**Date:** 2026-04-19
**Branch:** `feat/surah-input`
**Status:** Design approved, ready for implementation

## Overview

Add a third invocation mode to `yt-quran-overlay` where the user names one or
more surahs by canonical name (e.g. `--surah At-Tin --surah Al-Fatiha`). The
tool resolves each name to a video URL on the audio channel, downloads each
audio stream, concatenates them, and runs the existing render+upload pipeline
against a long-enough visual from the video channel.

Manual mode (`--video-url + --audio-url`) and discovery mode
(`--video-channel + --audio-channel`) remain unchanged.

## Goals

- Let the user say "give me a video of Surah Ar-Rahman" without hunting for
  a YouTube URL.
- Let the user produce compilations: "Al-Fatiha + At-Tin + Al-Ikhlas",
  audio concatenated, single visual loop.
- Reuse all existing rendering, branding, upload, state-tracking machinery.

## Non-Goals

- No silence or jingle between surahs (direct concatenation).
- No multi-video visual concat (loop the longest visual instead).
- No interactive fallback if a surah is missing (fail fast).
- No new caching layer for the surah→URL index (rebuild per run; channel
  scrape is already cached at the yt-dlp level for short windows).

## CLI

```bash
yt-quran-overlay \
  --surah At-Tin \
  --surah Al-Fatiha \
  --surah Ar-Rahman \
  --audio-channel @QuranHadeesIndia \
  --video-channel @toyfactorycartoon \
  --metadata examples/metadata-surah-arrahman.json \
  [--upload]
```

Mode selection (mutually exclusive — argparse error if mixed):

| Args present | Mode |
|---|---|
| `--video-url + --audio-url` | manual |
| `--video-channel + --audio-channel` | discovery |
| `--surah ... + --audio-channel + --video-channel` | **surah** (new) |

## Architecture

### New modules

| File | Responsibility |
|---|---|
| `surah_resolver.py` | Scrape audio channel, run `surah_detector.detect_surah` on every video title, build `{canonical_name: Candidate}` (newest wins on ties), validate that all requested surahs resolve. |
| `audio_concat.py` | Take a list of audio file paths and produce one concatenated WAV/M4A using FFmpeg's concat demuxer (or filter_complex if codecs differ). |

### Extensions

- `overlay_pipeline.py` → `run_overlay_surahs(surah_names, audio_channel, video_channel, metadata, ...)`:
  1. Resolve all surahs via `surah_resolver.resolve_surahs(...)` — fail fast on any miss.
  2. For each resolved candidate, call `download_stream(..., mode="audio-only")`.
  3. Concatenate the downloaded audio files via `audio_concat.concat_audio(...)`.
  4. Discover the longest visual candidate from the video channel (reuse `fetch_candidates`, sort desc on duration).
  5. Download visual via `download_stream(..., mode="video-only")`.
  6. Run `render_overlay(...)` against the concatenated audio + chosen visual.
  7. Optional upload, with `$detected_surah` rendered as `"Al-Fatiha + At-Tin + Ar-Rahman"`.

- `overlay_cli.py` → add `--surah` (`action="append"`); update `_validate_source_args` to recognize the third mode.

- `_build_auto_vars` → for surah mode: pass a synthetic `audio_meta` whose `title` lists the surah names; `detected_surah` is the joined names; `surah_tag` is the joined PascalCase tags.

### State tracking

`pair_state` not used in surah mode (the pair_id concept doesn't generalize
cleanly for compounds). Output filename embeds first surah + count for
discoverability:

- 1 surah: `<SurahTag>_<videoid>.mp4`  → e.g. `AtTin_HMjbCz_4UOA.mp4`
- N surahs: `<FirstSurahTag>_+<N-1>more_<videoid>.mp4` → e.g. `AlFatiha_+2more_HMjbCz_4UOA.mp4`

## Data Flow

```
--surah At-Tin --surah Al-Fatiha --audio-channel A --video-channel V
        │
        ▼
  resolve_surahs(["At-Tin","Al-Fatiha"], A)
        │   → channel scrape → detect_surah on titles → build index
        │   → returns [Candidate(At-Tin@url1), Candidate(Al-Fatiha@url2)]
        ▼
  download_stream(url1, audio-only) ──┐
  download_stream(url2, audio-only) ──┤
                                      ▼
                            concat_audio([f1, f2]) → combined.m4a
                                      │
                            longest visual from V ─→ download_stream(video-only)
                                      │
                                      ▼
                            render_overlay(visual, combined.m4a, …)
                                      │
                            optional upload (auto-vars include joined surah list)
```

## Error handling

- Surah(s) not found in channel → `OverlayError: "Surahs not found: {missing}. Available: {sample}"`. Halts before any download.
- Audio download for any surah fails → `OverlayError`, batch stops (no partial concat).
- Concat fails → `FFmpegError` (existing exception class).
- Visual download fails → existing `download_stream` fallback chain.
- Upload missing logo guard, surah-detection guard from existing pipeline still apply.

## Testing

- `test_surah_resolver.py` — fake `fetch_candidates` returning a hand-crafted list of titled candidates; verify resolution, missing-surah error, newest-wins tiebreak.
- `test_audio_concat.py` — generate two short test audios with FFmpeg, run concat, ffprobe the result, assert duration ≈ sum and audio stream present.
- Update `test_overlay_cli.py` for the new mode validation.
- E2E: 1-surah + 3-surah runs against the real channels (no upload).

## Implementation parallelism

`surah_resolver.py` and `audio_concat.py` are independent and can be built
in parallel by separate agents. CLI + pipeline integration is a sequential
follow-up that needs both modules to exist.

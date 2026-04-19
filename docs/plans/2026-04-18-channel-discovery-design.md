# Channel Discovery + Duration-Based Pairing — Design

**Date:** 2026-04-18
**Branch:** `feat/quran-overlay`
**Status:** Implemented

## Overview

Extend `yt-quran-overlay` with an auto-discovery mode that pulls videos from
two YouTube channels (visual + audio), pairs them by duration, skips
already-processed combinations via persistent state, and produces N videos
per run. Manual single-URL mode is preserved.

Intended use: daily/automated production of Quran-recitation-over-animation
uploads without the user hand-picking URLs.

## Goals

- Auto-select compatible (visual, audio) pairs from two channels.
- Pair by duration: minimize loop overhead on the visual side.
- Persistent duplicate protection across runs.
- Per-run batch size configurable via CLI.
- Single command switches between manual and discovery modes based on
  which args are present.

## Non-Goals

- View-count / popularity weighting (future).
- Parallel rendering (FFmpeg is CPU-bound; sequential is simpler).
- Stretching or pitch-shifting audio to fit visual length.
- Cross-run deduplication by querying the target channel's uploads (we
  trust local state; user can wipe it to re-process).

## CLI Surface

```bash
# Discovery mode:
yt-quran-overlay \
  --video-channel @toyfactorycartoon \
  --audio-channel @QuranHadeesIndia \
  --count 1 \
  --metadata examples/metadata-surah-arrahman.json \
  [--upload] [--state-file state/processed_pairs.json]

# Manual mode (unchanged):
yt-quran-overlay --video-url URL --audio-url URL --metadata ...
```

Mode selection is by arg presence:

- `--video-url + --audio-url` → manual
- `--video-channel + --audio-channel` → discovery
- Both or neither → argparse error

## Modules

| File | Responsibility |
|---|---|
| `channel_discovery.py` | Wrap existing `scraper.get_channel_videos`; drop videos with unknown or too-short duration. |
| `pair_selector.py` | Duration-based pairing. Returns `PairChoice(audio, visual, duration_slack)`. |
| `pair_state.py` | JSON-backed `PairState` with `ProcessedPair` records (`audio_id`, `video_id`, `uploaded_at`, `uploaded_video_id`, `output_path`). |
| `overlay_pipeline.py` | New `run_overlay_batch()` orchestrates fetch → dedupe → select → render loop. |
| `overlay_cli.py` | New flags + mode-switch validation. |

Existing `scraper.get_channel_videos` is reused as-is. Its module-level
`sys.stdout` rebind was moved into `_fix_windows_console_encoding()` and
called only from `main()` — previously it broke pytest capture whenever a
test indirectly imported it.

## Pairing Algorithm

For each audio candidate, rank visuals by:

1. `(0, visual.duration - audio.duration)` if `visual.duration >= audio.duration`
2. `(1, audio.duration - visual.duration)` otherwise

Sorted ascending. First-tuple-group wins (visuals long enough to cover the
audio), tie-broken by smallest positive slack. Fallback group is visuals
that are too short; the longest of those wins (fewest loop repeats).

Already-processed pairs are filtered out per candidate. `select_pair()`
iterates audios in the order provided by the channel (YouTube returns
newest first), so newer audio is preferred.

`select_pairs(count=N)` calls `select_pair()` N times with a running set
of "used within this batch" IDs, preventing the same audio or visual from
being reused across a single batch.

## State File

Format at `state/processed_pairs.json`:

```json
{
  "pairs": [
    {
      "audio_id": "pZI-EBD1C2I",
      "video_id": "LI0SRadei8w",
      "uploaded_at": "2026-04-18T19:35:14+00:00",
      "uploaded_video_id": "3mq7EV0-qrM",
      "output_path": "output/pZI-EBD1C2I_LI0SRadei8w.mp4"
    }
  ]
}
```

Written after every successful render. Corrupt JSON is logged and treated
as an empty state (fail-open for user's content pipeline). Missing file
→ empty state.

## Error Handling

- No candidates in a channel → `ChannelDiscoveryError` (halts the batch).
- All combinations already processed → `OverlayError` with guidance to
  add more content or clear the state file.
- Render failure for a single pair → logged, batch continues with next.
- If every pair in a batch fails → `OverlayError` at the end so CI/cron
  picks up the failure.

## Testing

- `test_pair_selector.py` — duration ranking, slack computation, duplicate
  skipping, batch non-overlap, exhaustion.
- `test_pair_state.py` — roundtrip, missing file, corrupt JSON, parent dir
  creation.
- `test_channel_discovery.py` — short/unknown duration filtering, empty
  channel, `filter_out_processed` exhaustion logic. `get_channel_videos`
  mocked via `patch("yt_audio_filter.scraper.get_channel_videos", ...)`.

Existing test count: 30 → 69 after this change.

## Open Questions

- Whether to add view-count weighting or recency bias in a future
  iteration (currently "natural" via the channel's own ordering).
- Whether to skip Shorts by default in discovery mode (currently yes
  via the scraper's `include_shorts=False` default).

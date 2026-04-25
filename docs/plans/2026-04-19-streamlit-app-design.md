# Streamlit UI + GitHub-Hosted Audio — Implementation Plan

**Date:** 2026-04-19
**Branch:** `feat/streamlit-ui` (will be created)
**Status:** Plan — pending approval before agent dispatch

## Goals

1. Web UI: user picks surahs from a 114-item list and cartoon videos from
   a thumbnail gallery, clicks "render", optionally uploads.
2. Replace YouTube audio extraction with **direct downloads from a stable
   audio source** (GitHub-hosted / quranicaudio mirrors). Keeps the proven
   visual side (pytubefix + yt-dlp + optional --upscale).
3. The UI wraps existing `run_overlay_surahs()` logic — no duplication.

## Non-Goals

- Not replacing the CLI. The CLI remains for automation / cron.
- Not adding reciter selection in v1 (default: **Salim Bahanan**, matches
  every existing upload on the channel).
- Not multi-user: the app runs locally on the user's box, single session.
- No authentication: runs on `localhost:8501` behind the OS.

## Decisions (confirmed with user)

1. **Reciters:** **Top 20** well-known reciters selectable in the UI, each
   with an **audio sample** preview button (HTML5 `<audio>` on the quranicaudio
   sample URL) before committing to the render. Agent A verifies 114-surah
   coverage per reciter; drops any reciter with gaps.
2. **Channels:** `config/channels.json` is **pre-populated with 5 channels**
   similar to Toy Factory. Agent B researches candidates (e.g. Chuggi,
   Kids Tv, animated nursery-rhyme 3D channels), download-tests each via
   the existing pytubefix chain, and keeps only the 5 that actually work.
3. **Output:** Render writes to a `tempfile.NamedTemporaryFile(delete=False)`.
   UI calls `st.video(path)` for inline preview and `st.download_button`
   with `path.read_bytes()` for download. Upload button re-reads the same
   temp path. No persistent `output/` from the UI path.
4. **Upload:** Separate "Upload to YouTube" button after a successful
   render. Render first, preview inline, then click to publish. Matches
   the existing upload-defaults memory (surah mode IS production, but
   UI still gives the user the moment to verify).
5. **Thumbnail source:** `i.ytimg.com/vi/<id>/hqdefault.jpg` — already
   exposed by the existing `scraper.VideoInfo.thumbnail_url`.
6. **Audio format:** MP3 (~128 kbps CBR) direct from source. AAC re-encode
   happens anyway during FFmpeg concat/render.
7. **Deployment:** `streamlit run src/yt_audio_filter/streamlit_app.py`
   from repo root, single local process.

## Architecture

```
┌────────────────────────────────────────────────────────────┐
│ streamlit_app.py  (browser UI)                             │
│   ├─ Surah picker  (multiselect from 114-entry list)       │
│   ├─ Thumbnail gallery (grid, click to select)             │
│   └─ Render button → backend                               │
└─────────────────┬──────────────────────────────────────────┘
                  │
       quran_audio_source.download_surah(number, reciter)
                  │
       cartoon_catalog.list_videos(channels) / thumbnail_url(id)
                  │
       overlay_pipeline.run_overlay_from_surah_numbers(…)
                  │          (new function, reuses concat + render)
                  ▼
       output/SurahTag_+Nmore_<videoid>.mp4
                  │
       optional upload via uploader.upload_with_explicit_metadata
```

## New modules

| File | Responsibility |
|---|---|
| `src/yt_audio_filter/quran_audio_source.py` | Resolve surah number → downloadable URL for a given reciter, cache at `cache/audio_surah_<num>_<reciter>.mp3`. Ships a small JSON manifest for the supported reciter(s); falls back to a per-reciter URL pattern. |
| `src/yt_audio_filter/cartoon_catalog.py` | Read `channels.json`, scrape each channel via existing `scraper.get_channel_videos()`, cache combined video list at `cache/cartoon_catalog.json` with TTL (e.g. 24 h). Expose `list_videos()` returning `{video_id, title, url, duration, thumbnail_url}`. |
| `src/yt_audio_filter/streamlit_app.py` | The UI. Imports surah list from `surah_detector`, thumbnails from `cartoon_catalog`, audio from `quran_audio_source`. Shows inline progress during render. |
| `src/yt_audio_filter/overlay_pipeline.run_overlay_from_surah_numbers` | New entry. Takes `list[int]` surah numbers + `list[str]` visual video_ids (or a single visual for v1). Downloads each audio via quran_audio_source, concats, renders via existing machinery. No upload by default — UI button triggers a separate `upload_rendered()` call. |
| `config/channels.json` | User-editable list of cartoon channels. Seeded with `@toyfactorycartoon`. |
| `tests/test_quran_audio_source.py` | Mock URL fetch; verify cache hit/miss, manifest parsing, fallback on 404. |
| `tests/test_cartoon_catalog.py` | Mock scraper; verify TTL, JSON roundtrip, thumbnail URL shape. |

## CLI integration

Expose `run_overlay_from_surah_numbers(...)` in the CLI too, behind a new
`--surah-number 1 --surah-number 112 ...` flag. This way the backend
function has two callers (UI + CLI) and can be tested from either.

## Streamlit flow

1. Page load: fetch cartoon catalog (cached), load surah list from
   `surah_detector.SURAHS_INFO` (already structured data).
2. Left column: multiselect for surahs ordered by canonical number
   (1. Al-Fatiha … 114. An-Nas). User can type to filter.
3. Right column: grid of thumbnails (3–4 cols, lazy-loaded). Click to
   toggle selection. v1: only 1 visual selectable; v2 (later) multi-select
   for concat.
4. "Render" button (disabled until ≥1 surah + exactly 1 thumbnail):
   - Progress stream via `st.status` context: resolve → download audios →
     concat → download visual → (optional upscale) → render → done.
   - Preview via `st.video(output_path)`.
5. "Upload" button (only after successful render): reuses
   `upload_with_explicit_metadata` with title/description rendered from
   a `metadata.json` template the user points to via sidebar.

## Team (parallel agent dispatch)

**Phase 1 — independent, run in parallel:**

- **Agent A — Quran audio source.** Researches live URLs on
  quranicaudio.com (or github mirror), writes `quran_audio_source.py`
  + manifest + tests. Verifies Salim Bahanan coverage for all 114
  surahs. Reports the final URL pattern used.

- **Agent B — Cartoon catalog + channels.json.** Writes
  `cartoon_catalog.py` + seed `channels.json` + tests. Uses existing
  `scraper.get_channel_videos` so no new scraping code. Includes
  TTL-based refresh, thumbnail-URL resolution, and JSON caching.

**Phase 2 — depends on Phase 1 APIs:**

- **Agent C — Pipeline extension.** Adds
  `run_overlay_from_surah_numbers` to `overlay_pipeline.py`, wires it
  into `overlay_cli` with `--surah-number`. Tests against mocked audio
  source + mocked visual download.

- **Agent D — Streamlit app.** Writes `streamlit_app.py` and a
  `docs/streamlit-usage.md`. Depends only on the APIs of A, B, C.

**Phase 3 — once A-D are green:**

- **Agent E — E2E smoke + documentation.** Runs a local streamlit
  session, clicks through one full render (no upload), updates
  CLAUDE.md with the new tool surface, adds a README section.

## Open questions (to confirm before dispatch)

1. **Reciter:** Salim Bahanan only, right? If other reciters should be
   selectable, Agent A's scope grows (dropdown in UI, catalogue per
   reciter).
2. **Channels:** Seed `channels.json` with Toy Factory only and let you
   add more by hand, OR pre-populate with 4-5 similar channels (risk:
   some aren't actually similar / SABR-broken)?
3. **Output destination:** Keep the `output/` directory pattern, or have
   the UI zip + stream the file so it doesn't live on disk?
4. **Upload behavior:** Render always, upload only on button click (my
   default), or auto-upload checkbox in the UI?

## Deliverables

- `feat/streamlit-ui` branch, PR against main.
- Modules in the table above, all tested (estimated +25 tests over the
  existing 108 → ~133).
- Design doc (this file, committed).
- README section + CLAUDE.md update with `streamlit run src/yt_audio_filter/streamlit_app.py`
  instructions.

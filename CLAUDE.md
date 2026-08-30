# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

The repo ships **two** CLI tools built on a shared FFmpeg / yt-dlp / uploader
stack:

- **`yt-audio-filter`** — original tool. Removes background music from MP4 videos using Facebook's Demucs AI model. Accepts a local file or a YouTube URL, preserves vocals, remuxes losslessly.
- **`yt-quran-overlay`** — added in feat/quran-overlay + feat/surah-input. Combines a YouTube visual (e.g. Toy Factory cartoons) with a separate Quran recitation audio, loops the visual to match audio length, applies EBU R128 loudnorm, overlays a channel logo, and optionally uploads to YouTube with a templated description. Three invocation modes (see "yt-quran-overlay tool" section below).

## Development Commands

Standard for the toolchain: `pip install -e .`, `black src/`, `mypy src/`,
`pytest`. Config lives in `pyproject.toml`. The non-obvious ones:

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

**Both** tools now use this chain. `cli.py` calls
`youtube.download_video_with_metadata()`, a thin wrapper that runs
`download_stream(mode="video+audio")` and adds a `VideoMetadata` shape on top
for the auto-SEO upload path. The old Invidious/Piped/Cobalt/YTDownloader.exe
chain in `download_youtube_video()` is no longer reached from either CLI;
`--cookies-from-browser` / `--proxy` / `--gui-downloader-path` are still
accepted for backwards compatibility but ignored.

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

### Download chain (shared by both tools)

`youtube.download_stream()` is the application-less path:

1. **pytubefix client cascade** (ANDROID_VR → IOS → ANDROID → MWEB → TV → WEB) — pure Python, no external runtimes. Delivers 160 kbps Opus audio for most videos.
2. **yt-dlp fallback** with `tv_embedded/ios/web_embedded/android` client cascade and `bestvideo[ext=mp4]/bestvideo/18/b` format fallback. Format 18 is combined 360p mp4; post-downloaded, FFmpeg strips to the requested stream via `-c copy` when possible.

No YTDownloader.exe, no Docker, no Node.js server required in the default path.

The music-removal CLI reaches the same chain via
`download_video_with_metadata()` — see "YouTube Download Fallback Chain" above.

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

**Two cache names, and they are not interchangeable:**

| File | Has audio? | Built by | For |
|---|---|---|---|
| `cache/upscaled_<id>.mp4` | **no** | `get_or_create_upscaled` | overlay visuals (sound comes from the recitation) |
| `cache/sharp_<id>.mp4` | yes | `get_or_create_sharpened` | music removal (Demucs needs the original audio) |

`upscale_video()` rebuilds the picture from PNG frames, so what it writes is
silent. `upscale_preserving_audio()` wraps it and copies the source's audio
back on with `-map 0:v:0 -map 1:a? -c copy`. The `?` is load-bearing: a source
with no audio stream is legal, and a hard map would abort the render over it.
Handing the *overlay* cache file to music removal publishes a silent episode,
which is why the names differ.

**`MAX_UPSCALE_FRAMES = 10_000` rules out full episodes.** That is ~6.7 minutes
at 25 fps, against a typical 20-45 minute cartoon. The limit is real (every
frame is written to disk twice as PNG; one run ground for twenty hours), so
`yt-studio --upscale` treats sharpening as best-effort: on refusal it emits an
`upscale-skipped` event and falls back to a plain scale rather than failing
the item. Lifting this needs chunked processing, not a bigger number.

### yt-studio output quality

`workflow_runner.MIN_HEIGHT = 720` is a floor applied to every render, and
`clamp_height()` enforces it — `--height 360` silently becomes 720. YouTube
picks its encoding ladder from the uploaded resolution, so a 360p upload gets
a bitrate that makes an already-soft source look worse again on playback.

`DEFAULT_HEIGHT` is 1080. `--upscale` (alias `--sharp`) targets 720 instead
unless `--height` says otherwise, because the model is 2× and a 360p source
doubles to exactly 720p; scaling that to 1080 would interpolate away part of
what the GPU hour bought.

Sharpening runs **before** music removal — see the table above for why.

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

## Quran Studio (Vercel + local worker) — the current UI

The hosted replacement for the Streamlit app. Same three modes, but the UI
lives on Vercel (always up, reachable from anywhere) and the compute stays on
the user's PC.

```
Vercel (web/)                      Local PC (worker/)
Next.js Studio UI                  worker.py polls for jobs
/api/jobs        create      ◄──── claims via /api/worker/claim
/api/jobs/:id    poll status ◄──── posts /api/worker/progress
/api/worker/*    worker API  ◄──── posts /api/worker/complete
Upstash Redis    job records        runs the EXISTING pipeline unchanged
                                    rendered MP4 stays on this disk
```

**Rendered files never leave the worker.** The Studio reports a finished
render's name, size, and path on that machine and nothing more — no player, no
download. Uploading to YouTube is how a render becomes watchable anywhere
else. (A Vercel Blob preview used to exist; its free tier ran out twice in one
week, and the preview was not worth paying for.)

The worker only makes **outbound** HTTPS calls — no inbound ports, no tunnel,
works behind NAT, and keeps the residential IP that stops YouTube bot
detection from blocking downloads.

**Why not run it all on Vercel:** no GPU (NVENC / Real-ESRGAN / Demucs CUDA),
250 MB function bundle limit (PyTorch alone is ~800 MB), no persistent
filesystem for the multi-GB `cache/`, and an execution ceiling far below a
real render.

### Layout

| Path | Role |
|------|------|
| `web/lib/types.ts` | **Authoritative job contract.** Mirrored in `worker/contract.py` — change both together. JSON is camelCase, Python is snake_case; convert at the boundary. |
| `web/lib/jobs.ts` | Redis job store: create / claim / progress / complete / cancel / upload-requeue. Search jobs are `rpush`ed so they jump ahead of renders. |
| `web/lib/auth.ts` | Password → HMAC-signed session cookie for users; static `x-worker-token` header for the worker. |
| `web/data/*.json` | Surahs, reciters, presets, channels baked in at build time. **Generated — never hand-edit.** Run `python scripts/sync_web_data.py`. |
| `web/scripts/dev-redis.mjs` | In-memory Upstash-REST stand-in for local dev. Test fixture only. |
| `worker/handlers.py` | Dispatches each job kind to the existing pipeline functions. Always passes `upload=False` — uploads are a separate, explicitly user-triggered step. `deliver_render` records the finished file in the sidecar store and reports its `localPath`. |
| `worker/identity.py` | Stable per-machine `workerId`, derived from hostname + machine GUID and persisted to `worker/state/worker_id.txt` so a restart keeps it. |
| `worker/discovery.py` | Loopback-only `GET /whoami` on port 7717. Lets the browser work out which worker shares its machine. |

### Invocation

```bash
# frontend (see docs/DEPLOY.md for the full setup)
cd web && npm run dev

# worker
worker\run_worker.bat
```

Setup, secrets, and deployment: [docs/DEPLOY.md](docs/DEPLOY.md).

### Gotchas

- **Nothing uploads to YouTube automatically.** A render just lands on the
  worker's disk; publishing requires the explicit "Upload to YouTube" button,
  which re-queues the job with `uploadRequested=true` and reuses that file.
- **`JobResult.localPath` is the upload gate.** `requestUpload` in
  `web/lib/jobs.ts` only re-queues a `done` job whose result carries a
  `localPath`, because that is the one field meaning "a file exists". Gate on
  anything looser and `search`/`probe` jobs — which finish `done` with nothing
  on disk — become uploadable. It is a worker-filesystem path, so the UI shows
  it as plain text and never as a link.
- **Search runs on the worker**, not Vercel — yt-dlp cannot run in a
  serverless function. The UI polls a `kind: "search"` job.
- **Search picks must reach the catalog.** The frontend sends the whole
  `CatalogVideo`; the worker calls `cartoon_search.add_pick_to_catalog` before
  rendering, because `overlay_pipeline._resolve_visual_video` only resolves ids
  present in `list_videos()`.
- **YouTube OAuth stays machine-local** (`~/.yt-audio-filter/`). The hosted UI
  never sees those credentials.
- **`metadataPath` is a path on the worker's filesystem**, not an upload.
- **Work runs where you opened the page.** Several machines can each run a
  worker. The Studio probes `http://127.0.0.1:7717/whoami`; a hit means that
  worker is on the same machine, and jobs are tagged with its `workerId` so
  only it can claim them. A worker that pops someone else's targeted job
  pushes it back with `rpush`, not `lpush` — `rpop` takes from the tail, so
  `lpush` would bury it behind every pending render.
- **Chrome blocks the discovery probe without a preflight header.** Private
  Network Access rules stop a public HTTPS page reaching loopback unless the
  `OPTIONS` response carries `Access-Control-Allow-Private-Network: true`.
  Miss it and the probe fails silently and everything falls back to "any
  machine".

### Legacy Streamlit UI (superseded)

`src/yt_audio_filter/streamlit_app.py` still runs
(`streamlit run src/yt_audio_filter/streamlit_app.py`) but is no longer the
intended entry point. It is single-session, localhost-only, and unauthenticated.
The sections below document it; they describe the same three modes the Studio
now exposes.

### Audio source caveat — Salim Bahanan

Salim Bahanan is NOT on quranicaudio.com. The Streamlit picker only
offers reciters that ARE there; the reciters JSON notes this and
substitutes Abdullah Awad al-Juhani in the Bahanan slot. For Salim
Bahanan specifically, stay on the CLI and use surah-name mode with
direct `--surah https://...` URL overrides (see the
`yt-quran-overlay` section above).


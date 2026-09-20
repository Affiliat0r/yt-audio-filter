# Handoff: A1000 laptop → 3070 Ti laptop

Written 2026-09-20 on the work laptop (RTX A1000 6 GB). Read this before
picking the work up on the 3070 Ti laptop. No secrets are in this file; every
credential mentioned lives outside the repo.

## Where things stand

| | State |
|---|---|
| Branch | `integrate/feature-branches`, pushed, in sync with origin. `main` untouched, no PR opened. |
| What the branch adds | Merges of `feat/threejs-visuals` and `feature/autonomous-pipeline` — the only two remote branches that were not already in `main` — plus one `.gitignore` fix. |
| Tests | Same result as `main`. No regressions from the merges (details below). |
| YouTube auth | Working **on the A1000 laptop only**, signed in to the channel *Muziksiz Cizgi Filimler*. |
| Real renders / uploads | **None run yet.** Only `yt-studio --dry-run` has been exercised, and it passes end to end including the duplicate check. |
| Live Studio | https://quran-studio-mocha.vercel.app — deploys from `main`, unaffected by this branch. |

## Get the branch

Repo already cloned:

```powershell
git fetch origin
git checkout integrate/feature-branches
.venv\Scripts\python -m pip install -e ".[dev]"
```

Fresh machine:

```powershell
git clone -b integrate/feature-branches https://github.com/Affiliat0r/yt-audio-filter.git
cd yt-audio-filter
python -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]"
```

The reinstall matters even on an existing clone: the branch adds a `pyyaml`
dependency and a new `yt-discover` command.

## What the two merges changed

**`feat/threejs-visuals`** — brings the `visuals/` source (procedural three.js
movies rendered headlessly to MP4) alongside the `visuals/capture` and
`visuals/encode` that `main` already had. Only conflict was `.gitignore`.
Verify with `cd visuals && npm install && npm test` (49 tests).

**`feature/autonomous-pipeline`** — adds `discovery.py`, `config.py`,
`copyright_scorer.py`, `quota_tracker.py`, a rewritten `scheduler.py`, the
`yt-discover` entry point and `deploy/`. It forked in January, 118 commits
behind, so six files conflicted. Where `main` had already gone further, `main`
won:

- `uploader.py` — main's token refresh handles "not valid", not only "expired".
- `demucs_processor.py` — main takes `segment`/`shifts` as parameters; the
  branch hardcoded `segment=5, shifts=0`, which would have overridden callers.
- `youtube.py` — the branch edited `download_youtube_video()`, which main has
  since turned into the unused YTDownloader.exe GUI path.
- `exceptions.py`, `pyproject.toml`, `CLAUDE.md` — both sides kept.

**One change that was not conflict resolution.** The merged scheduler called
`download_youtube_video()`. On current `main` that means Windows GUI automation,
which cannot run on the headless Linux VM the feature targets. It now calls
`download_video_with_metadata()` — same arguments, same `VideoMetadata` return.
This has been import- and signature-checked but **never run for real**; the
autonomous pipeline as a whole is untested on this branch.

## Test baseline

Without PyTorch installed, both `main` and this branch give:

```
983 passed, 6 failed, 55 errors, 17 skipped
```

run as:

```powershell
.venv\Scripts\python -m pytest -q --ignore=tests/test_pipeline_scale_threading.py --ignore=tests/test_temporal_reuse.py
```

The two ignored files need `torch` / `numpy` to even import. Of the rest, 59
failures are one cause — `yt_audio_filter.pipeline` imports `torch` at module
level — and the other two (`test_ffmpeg_overlay_cuda.py`, `test_sr_backend.py`)
depended on FFmpeg and GPU detection. **The 3070 Ti laptop has PyTorch, so
expect a much cleaner run there.** Anything still failing on that machine is
worth a look, because it cannot be blamed on a missing dependency.

## Set up on the 3070 Ti laptop

None of this travels with git.

1. **YouTube sign-in.** Needs `client_secrets.json` and `oauth_token.pickle` in
   `%USERPROFILE%\.yt-audio-filter\`. If that laptop already uploads, it has
   them. Otherwise download a Desktop OAuth client from Google Cloud project
   `youtube-data-api-v3-482520`, save it under that exact name, and run:

   ```powershell
   .venv\Scripts\python -c "from yt_audio_filter.uploader import authenticate_youtube; authenticate_youtube()"
   ```

   A browser opens; choose the channel's Google account and allow all three
   YouTube permissions. Use this one-liner rather than
   `yt-audio-filter --list-playlists` — that CLI imports `torch` on startup and
   will not start on a light install.

2. **Worker.** Create `worker\.env` (gitignored):

   ```ini
   STUDIO_BASE_URL=https://quran-studio-mocha.vercel.app
   WORKER_TOKEN=<from Vercel → quran-studio → Settings → Environment Variables>
   ```

   then `worker\run_worker.bat`. For a from-scratch install use
   `scripts\install_worker.ps1 -WithMusicRemoval`. Note the installer clones
   `main`, not this branch — fine for the worker, since neither merge touches
   `worker/` or `web/`.

3. **Check PyTorch can see the GPU.** `-WithMusicRemoval` runs a plain
   `pip install -e .[music]`, and on Windows that can resolve to a CPU-only
   build, which makes Demucs 10–20× slower with no error:

   ```powershell
   .venv\Scripts\python -c "import torch; print(torch.__version__, torch.cuda.is_available())"
   ```

   If it prints `False`, reinstall `torch` and `torchaudio` from the CUDA wheel
   index at https://pytorch.org/get-started/locally/.

4. **FFmpeg version.** Run a real encode, not just a version check:

   ```powershell
   ffmpeg -hide_banner -loglevel error -f lavfi -i testsrc=size=1280x720:rate=30 -t 2 -c:v h264_nvenc -f null -
   ```

   FFmpeg 9.x needs NVENC API 13.1, i.e. NVIDIA driver 610 or newer. On an
   older driver it fails at encode time, while
   `ffmpeg.check_nvenc_available()` still returns `True` because it only reads
   the encoder list — so a render picks NVENC and dies. The A1000 laptop
   (driver 595.95) is pinned to FFmpeg 8.0.1 for this reason. Either update the
   driver or install 8.0.x: `winget install --id Gyan.FFmpeg -e --version 8.0.1`.

## How the two laptops divide the work

| Laptop | Worker install | Does |
|---|---|---|
| A1000 (work) | light, no PyTorch | Quran overlays (surah / ayah), upload |
| 3070 Ti | `-WithMusicRemoval` | Overlays **and** music removal, upload |

Routing is per job, from the Studio sidebar's **Run the work on** selector. A
light worker reports `canRemoveMusic: false` and the Studio greys out music
removal while it is selected. There is no standing "this kind of video always
goes to that laptop" rule. A render stays on the disk of the laptop that made
it and is uploaded by that laptop, with that laptop's own OAuth token.

The A1000 laptop's worker is **not connected yet** — it still needs the
`WORKER_TOKEN`.

## Open items

- [ ] First real end-to-end run. Suggested, because it is short, needs no
      PyTorch, and stays invisible to viewers:
      `yt-studio --privacy private "Quran (An-Nas, Ghamdi)"`.
      The first real YouTube download is the most likely thing to fail — see the
      SABR section of `CLAUDE.md`.
- [ ] Exercise the autonomous pipeline for real: `yt-scheduler --dry-run --verbose`.
- [ ] Connect the A1000 laptop's worker to the Studio.
- [ ] Open a PR from `integrate/feature-branches` into `main` once it has been
      checked on the 3070 Ti.
- [ ] If the OAuth consent screen is still in "Testing", tokens expire every
      7 days. **Publish app** in Google Cloud Console makes them permanent.
- [ ] Delete the account password from `.env` on the A1000 laptop. Nothing in
      the codebase reads it; uploads are OAuth only.

## Gotchas met along the way

- Google's OAuth download is named `client_secret_<id>.apps.googleusercontent.com.json`.
  The repo only ignored `client_secrets.json`, so the file sat untracked but
  un-ignored in the repo root. Fixed on this branch — but still save it
  straight into `%USERPROFILE%\.yt-audio-filter\`, not the repo.
- `docs/DEPLOY.md` and `worker/config.py` use `https://quran-studio.vercel.app`
  as an example URL. That is not the deployment. The real one has `-mocha` in it.
- `.gitignore` on this branch now lists `processed_videos.json` (it came with
  the autonomous-pipeline branch), but that file is tracked on `main`. Tracked
  files stay tracked, so nothing breaks; it is just inconsistent and worth
  settling before the PR.

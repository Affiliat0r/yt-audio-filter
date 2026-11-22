# Lessons learned

Incidents that cost real time, what fixed them, and the rule each one leaves
behind. Read the matching entry **before** debugging a familiar-looking failure;
add an entry **after** fixing anything that took more than one wrong guess.

Format: symptom → what we tried → root cause → fix → rule → guards in code.

---

## 2026-11-21 — "YouTube is blocking us" was a stale yt-dlp

**Symptom.** Every download failed with `Sign in to confirm you're not a bot`,
on every pinned client (`tv_embedded/ios/web_embedded/android`). A batch of five
approved renders sat in a wait loop for hours, polling for the block to lift.

**What we tried, in order — and why most of it was wasted:**

1. *Waiting it out.* `batch_nov21.sh` started with a loop that retried a download
   every few minutes. Nothing changed, because nothing about the cause was
   time-based.
2. *Browser cookies* (`--cookies-from-browser firefox`). They passed the bot
   check, but yt-dlp then offered only HLS formats, which downloaded empty. That
   result was the clue: authenticated and still no adaptive formats means
   yt-dlp itself cannot see them.
3. *Checking yt-dlp's own warnings.* The verbose log said `found 0 n function
   possibilities`: the player's n challenge was failing. That failure is silent
   at normal log level; YouTube's adaptive formats simply disappear.

**Root cause.** The venv had yt-dlp **2025.12.08**, eleven months old. YouTube
had changed its player since, the challenge solver could not keep up, and the
fallback clients it was left with were the ones YouTube had bot-flagged for this
connection after a morning of downloads.

**Fix.** `pip install -U yt-dlp yt-dlp-ejs` (to 2026.10.26 and 0.8.0). With
yt-dlp's *default* clients the block was gone, without cookies. As a bonus, the
SABR wall that had held every source at 360p since April was gone too: full
1080p downloads came down complete (MhouG7GgFYE 7:28; XgdI4wbLcOw 46:05,
766 MB), verified with full downloads and ffprobe, not `--test`.

Code changes (commit 51a0425 and the follow-up):

- `youtube.YTDLP_CLIENT_ATTEMPTS` tries yt-dlp's defaults first; the pinned
  cascade is the fallback.
- `pyproject.toml` floors: `yt-dlp>=2026.10.26`, `yt-dlp-ejs>=0.8.0`.
- `upscale._already_hd`: an HD source skips Real-ESRGAN (2x on 1080p would build
  4K for hours). Music removal copies a 1080p stream untouched.
- `ffmpeg_overlay.resolution_for_source`: an overlay render is raised to the
  visual's own height (capped at 1080p). The 720p default was chosen for 360p
  sources, so it would now have *shrunk* every 1080p visual.
- `youtube.warn_if_ytdlp_stale`: warns once a download starts if yt-dlp is
  older than 60 days (`YTDLP_STALE_DAYS`).

**Rules:**

- **Dependency before environment.** When a download path that used to work
  breaks, check the tool's version and verbose log before blaming the network,
  the account or YouTube's bot detection. A scraper against a moving target rots
  in weeks, not years.
- **Don't wait out an error you haven't explained.** A retry/wait loop is only
  right for a cause you have *confirmed* is time-based (a 429, a daily upload
  limit). Otherwise it hides the problem and burns the day.
- **Reaching for credentials is the last step, not the second.** Cookies tie
  downloads to the owner's Google account. They are an escalation to ask about,
  and here they weren't needed at all.
- **"Fixed" means a full download, verified.** `yt-dlp --test` stops after
  10 KB and once made a broken format look fine (2026-08-30).
- **When a premise changes, grep for it.** "Sources are 360p" was written into
  the `/yt` command, three code comments, two defaults and CLAUDE.md. Fixing the
  downloader without updating them would have left the overlay render
  downscaling 1080p to 720p, and the docs promising 45-minute sharpening runs
  that no longer happen.

**Guards:** `tests/test_download_quality.py`, `tests/test_hd_sources.py`,
`tests/test_source_resolution.py`.

---

## 2026-11-22 — a stopped batch kept running and held the GPU for 22 hours

**Symptom.** After the fix above, a 7.5-minute music removal took from 12:33 to
08:56 the next day. The next item crawled too. The PC had also slept overnight,
which looked like the whole explanation. It wasn't.

**Root cause.** The first batch, started before the fix, was stopped by
killing its `bash` script. On Windows that does **not** kill the grandchildren:
the venv's `python.exe` is a launcher that starts the real interpreter as a
child, and that interpreter plus its `ffmpeg` pipes kept running. It was
sharpening the 46-minute Gecko source to 3840×2160 with Real-ESRGAN (the
pre-fix code) and kept the GPU at 100% for 22 hours. Every later render shared
the GPU with it.

**Fix.** `taskkill /PID <launcher> /T /F` on the orphaned tree, then delete its
partial `cache/sharp_<id>_silent.mp4` so nothing can reuse it.

**Rules:**

- **After stopping a batch, check that its processes are actually gone.** List
  `python.exe`/`ffmpeg.exe` with their command lines
  (`Get-CimInstance Win32_Process`) and kill leftover trees with `taskkill /T`.
- **When something runs slower than it should, look at what else holds the GPU**
  before settling on an explanation such as sleep or throttling. `nvidia-smi` at
  100% with nothing of yours running is the tell.
- **A long batch on this PC needs it kept awake.** It sleeps overnight and
  pauses the run. Use `SetThreadExecutionState(ES_CONTINUOUS |
  ES_SYSTEM_REQUIRED)` for the batch's lifetime. Leave the display alone.

---

## 2026-11-22 — Niloya's short pieces belong to the *next* episode

**Symptom.** The Niloya 27–39 cut was meant to remove episode 33 "Tiyatro" (a
kemençe school play). After the cut, the join showed the title card "Tiyatro,
33. Bölüm" and then played episode 34 under it.

**Root cause.** Niloya compilations split at black into a ~5-minute episode and a
~7-second piece. The 7-second piece was taken to be the end card *after* an
episode, so the cut was `--cut 14 --cut 15`. It is the opposite: the piece is the
cyan "üye ol" intro and then the title card of the episode that *follows*. The
right cut was `--cut 13 --cut 14`. Nobody had sampled inside a 7-second piece.

**Fix.** Re-cut from the finished render (no re-render needed), then check the
join frame by frame before uploading. Caught before upload.

**Rules:**

- **Before choosing cut numbers, look at frames inside the short pieces** as
  well as the long ones. Know whether a short piece opens the next episode or
  closes the previous one.
- **Always check frames at every join before uploading**, a second or two on
  each side. That is what caught this.
- Gecko compilations have only one short piece (the 6.5 s opener), so earlier
  Gecko cuts were unaffected.

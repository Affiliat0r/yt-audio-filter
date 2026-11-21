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

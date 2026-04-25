# Quran-teacher product wishlist

**Author (persona):** Ustadh Yusuf Khan, Qari and Quran teacher
**Date:** 2026-04-25
**Reviewing:** the Streamlit app in `src/yt_audio_filter/streamlit_app.py`
plus `overlay_pipeline.run_overlay_from_surah_numbers` and the 20-reciter
manifest at `data/reciters.json`.

---

## 1. Persona

I'm Yusuf, 38, a full-time Quran teacher in West London. I came up
through a traditional hifz program in Karachi, hold an ijazah in Hafs
'an Asim, and have been teaching for 14 years. My week looks like
this:

- **Madrasah:** five evenings, ages 5-12, mixed ability. Roughly 40
  kids split across three rooms. Maktab basics, juz' 'amma memorisation,
  Qaida progression, then tajweed.
- **Online 1:1:** eight adult learners on Zoom, mostly reverts working
  through Al-Fatiha and short surahs.
- **Hifz stream:** four serious memorisation students, daily sabaq +
  sabqi + manzil rotation.
- **Parents:** about 30 WhatsApp groups where parents want links they
  can play on the phone or smart-TV at home, kids age 3-7.

What I currently use: Quran.com and tarteel.ai for verse text and
timestamps, Muqri Salim Bahanan and Mishary Alafasy YouTube playlists
for repetition, Anki for vocabulary, and a clunky habit of building
one-off "10x Al-Fatiha" videos in Audacity + iMovie when a parent asks.
What frustrates me: I spend more time building media than teaching.
Every kid gets the same generic playlist instead of one keyed to their
sabaq.

---

## 2. The wishlist

12 features below, grouped, prioritised, with effort estimates against
the current codebase.

### Memorisation & repetition

#### M1 - Sabaq / sabqi / manzil playlist builder
- **Who benefits:** hifz students, teacher
- **What it does:** Lets me declare a hifz student's state — current
  sabaq (today's new memorisation), sabqi window (last 7 days), manzil
  (last juz') — and renders one MP4 that plays sabaq 5x, sabqi 3x,
  manzil 1x in that order. Backed by the same per-surah repeat-count
  pipeline that's already in flight, just with a higher-level "lesson
  plan" wrapper around it.
- **Why it matters:** The sabaq/sabqi/manzil rotation is *the* core
  hifz technique. If the app spoke this language natively I'd use it
  daily for every memorisation student.
- **Effort:** S - per-surah repeat is already done; this is a UI form
  + a list-flattening step before the existing surah_numbers list goes
  into `run_overlay_from_surah_numbers`.
- **Priority:** P0

#### M2 - Ayah-range repetition (not whole surah)
- **Who benefits:** hifz students, kids learning long surahs piece by
  piece
- **What it does:** Instead of "10x Al-Baqarah" (impossible), let me
  pick "Al-Baqarah verses 255-257, 8x". Cuts the MP3 using ayah
  timestamps from the EveryAyah dataset (`https://everyayah.com/data/`,
  same per-reciter folder structure as the existing manifest) or the
  Quran.com `recitation_segments` API.
- **Why it matters:** Kids memorise in 3-5 ayah chunks. A 286-ayah
  surah-level granularity is useless for memorisation work; ayah-level
  is the unit.
- **Effort:** M - need an ayah-timestamp manifest per reciter and an
  audio-slicing step before `concat_audio`. Roughly mirrors the existing
  `quran_audio_source.download_surah` shape, swapping the per-surah MP3
  for per-ayah slices.
- **Priority:** P0

#### M3 - Gap / silence prompt mode
- **Who benefits:** hifz students
- **What it does:** "Reciter says ayah, then silence for N seconds, then
  reciter says next ayah." Configurable gap (3s / 5s / "match the
  ayah's own duration"). The student fills the silence aloud; this is
  basically a self-test loop.
- **Why it matters:** It's how I quiz students in person. Without a
  prompter you can't practice this alone, which is exactly when most
  practice happens (in the car, before bed).
- **Effort:** S - it's just `concat_audio` with `anullsrc` segments
  inserted between ayah slices. The ffmpeg pipeline already exists.
- **Priority:** P1

### Tajweed / pronunciation aids

#### T1 - Highlight the word being recited (karaoke-style)
- **Who benefits:** kids, adult learners
- **What it does:** Render the Arabic ayah on screen and bold/colour
  the *word* currently being recited. Drives the highlighting from
  Quran.com's `recitation_segments` endpoint, which returns
  `[word_index, start_ms, end_ms]` arrays per ayah per reciter. We
  already have ffmpeg's `drawtext` filter wired up via
  `ffmpeg_overlay.render_overlay`; this adds a per-frame text overlay
  driven by a timestamp track.
- **Why it matters:** It's the single biggest pedagogical win. Kids who
  can't yet read fluently can follow word-by-word; adults learning
  tajweed can see the rule (madd, ghunnah) at the moment they hear it.
- **Effort:** L - ffmpeg's `drawtext` with `enable='between(t,a,b)'`
  works but rendering a whole Mushaf this way is fiddly; a subtitle
  burn-in (ASS/SSA with karaoke `\k` tags) is probably cleaner.
- **Priority:** P0

#### T2 - Tajweed-coloured Mushaf overlay
- **Who benefits:** intermediate learners, teacher
- **What it does:** Show the ayah text using one of the colour-coded
  Mushafs (Madinah Mushaf with tajweed colouring is freely
  redistributable as PNG). Each madd is yellow, each ghunnah green,
  each qalqalah red. Pair with T1's word highlight.
- **Why it matters:** The colouring teaches the *rule* visually. After
  three weeks of these videos kids start spotting madds in unfamiliar
  text.
- **Effort:** M - it's an image overlay, not a text render. Drop a
  per-ayah PNG into the existing `render_overlay` logo/overlay slot.
- **Priority:** P1

### Visual / educational overlays

#### V1 - Trilingual subtitle track (Arabic + transliteration + English)
- **Who benefits:** adult learners, parents, reverts
- **What it does:** Render an ASS subtitle file with three lines per
  ayah: Arabic (right-to-left), transliteration, English meaning.
  Uthmani text from the Tanzil project (CC-BY), Sahih International
  for English. Burn-in via ffmpeg's `subtitles=` filter.
- **Why it matters:** My adult students universally ask "what does it
  mean". Without translation the visual is decoration; with it the
  visual is a lesson.
- **Effort:** S - ASS generation is a pure-Python step, ffmpeg already
  handles the burn-in. The cartoon visual stays as the background.
- **Priority:** P0

#### V2 - "Lower-third" surah/ayah counter
- **Who benefits:** kids, parents
- **What it does:** Small fixed banner like "Al-Fatiha — Ayah 3 of 7"
  that updates as the audio progresses. Same drawtext mechanism as T1,
  but coarser (one update per ayah, not per word).
- **Why it matters:** Parents on a school run want to know what's
  playing. Kids learn the *names* of surahs by hearing them keyed to
  the audio.
- **Effort:** XS - ayah timestamps already required by T1/M2, this is
  one more `drawtext` line.
- **Priority:** P1

### Reciter comparison

#### R1 - Side-by-side reciter A/B
- **Who benefits:** advanced students, teacher
- **What it does:** Pick the same surah by 2 (or 3) reciters from the
  existing 20-reciter list. Render with a split-screen visual or as a
  vertical stack: Husary on top, Alafasy below, both with their name
  caption. Audio plays sequentially (Husary first, then Alafasy)
  *not* simultaneously.
- **Why it matters:** I want students to hear *style* differences -
  Husary's tarteel pace vs. Alafasy's mujawwad. Currently they have to
  switch between YouTube tabs.
- **Effort:** M - `download_surah` is already per-reciter, `concat_audio`
  already handles the join. The new piece is the dual-name caption
  overlay.
- **Priority:** P2

### Class / cohort workflows

#### C1 - Weekly lesson-plan exporter
- **Who benefits:** teacher
- **What it does:** A "build week of Mon-Fri" view. Each row is a day,
  each cell holds (surah/ayah range, reciter, repeat count, visual).
  Hit "render all" - it produces 5 MP4s named `Mon_AlFatiha_5x.mp4`
  etc. and optionally creates an unlisted YouTube playlist with all 5.
- **Why it matters:** Saturday afternoon I sit down and prep the week.
  Right now that's 5 trips through the UI. With this it's one form.
- **Effort:** M - it's a "for each row, call
  `run_overlay_from_surah_numbers`" loop with a progress UI. The
  `cartoon_catalog` already supports listing/selecting visuals
  programmatically.
- **Priority:** P0

#### C2 - Per-student profiles + render history
- **Who benefits:** teacher, hifz students
- **What it does:** Persist a `students.json` (name, current sabaq,
  reciter preference, age band). Each student gets a "render today's
  plan" button that's pre-filled. After render, log the YouTube URL
  + date so I can see "Aisha got 8 videos in March, last one was
  Surah Al-Mulk".
- **Why it matters:** Tracking is half the job. Right now my tracking
  is a Google Sheet I update by hand and forget for two weeks.
- **Effort:** M - one new JSON file + a session-state tab in the
  Streamlit app. No backend changes.
- **Priority:** P1

### Sharing / distribution

#### S1 - WhatsApp-ready vertical export (9:16, ≤16 MB)
- **Who benefits:** parents, kids
- **What it does:** A "Share to WhatsApp" preset that renders 720x1280
  (vertical), capped at 16 MB so it fits WhatsApp's status/share
  limit, with a QR code endcard that links to the unlisted YouTube
  version for full-quality playback. Two ffmpeg flags away from the
  existing `render_overlay` call.
- **Why it matters:** 90% of my parent communication is WhatsApp.
  YouTube links work; native WhatsApp video plays inline and parents
  actually watch.
- **Effort:** XS - resolution and bitrate are already parameters on
  `render_overlay`; this is a UI preset.
- **Priority:** P0

#### S2 - Auto-create unlisted YouTube playlist per student/cohort
- **Who benefits:** parents, teacher
- **What it does:** When uploading, optionally append the new video to
  a named YouTube playlist (`Year3-Boys-Spring2026`). Set every upload
  in this flow to **unlisted** by default - the metadata template
  already has `privacy_status` (`metadata.py` line 17), this just adds
  a playlist-id field and one extra `playlistItems.insert` call after
  the existing `videos.insert`.
- **Why it matters:** I want one stable URL per cohort. Parents
  bookmark it; when I add a new video everyone sees it without me
  re-sharing.
- **Effort:** S - `uploader.py` already does OAuth and one API call;
  adding a second is trivial. Would also want to keep privacy default
  to `unlisted`, not `public`.
- **Priority:** P1

### Accessibility

#### A1 - Closed-caption (.vtt) sidecar on every render
- **Who benefits:** deaf/HoH students, search engines, parents
  watching muted on the train
- **What it does:** Generate a `.vtt` file alongside the MP4 with
  Arabic + English timed to each ayah. Upload it as a YouTube caption
  track (the `captions.insert` API call) when uploading.
- **Why it matters:** Accessibility. Also: YouTube indexes captions,
  so every video becomes searchable on the verse text.
- **Effort:** S - same data the V1 burn-in needs; the difference is
  emitting it as a sidecar instead of (or in addition to) burning it
  into the pixels.
- **Priority:** P1

### Things only this app could uniquely do

#### U1 - Reciter-matched cartoon mood pairing
- **Who benefits:** kids
- **What it does:** Husary is slow and grave - pair it with the
  calmer cartoon channels. Alafasy is bright - pair with action /
  bus / train cartoons. Today the user picks any visual; this would
  recommend one or three based on (reciter pace, surah length, kid
  age band) using the duration + view-count metadata
  `cartoon_catalog` already pulls.
- **Why it matters:** Bad pairings (sombre Husary over a clown-noise
  cartoon) actively distract children. Good pairings hold attention
  through a 90-minute Surah Yaseen recitation.
- **Effort:** M - heuristic on existing fields (`CatalogVideo.duration`,
  `view_count`, `upload_date`) plus a per-reciter "pace tag" added to
  `reciters.json`.
- **Priority:** P2

---

## 3. Top-3 if you only do three things this month

1. **Ayah-range repetition + gap-prompt mode (M2 + M3 together).** This
   is the single highest-leverage feature for hifz students. The
   per-surah repeat that's landing now solves a niche; per-ayah with
   gaps solves the daily core practice loop. EveryAyah timestamps make
   the data side cheap.
2. **Word-by-word karaoke highlight with trilingual captions (T1 +
   V1).** Same timestamp dataset, two render-time changes. Turns the
   app from "audio + cartoon background" into an actual teaching aid
   you can use on a TV in front of a class.
3. **Weekly lesson-plan exporter + WhatsApp vertical preset (C1 + S1).**
   This is the workflow win. Without it the app is a tool I use 1-2x
   per week; with it, it replaces my Saturday prep session and my
   parent-WhatsApp routine. Engagement, not features.

These three together convert the app from "render a nice video" to
"run my teaching practice."

---

## 4. What I would NOT add

- **AI tajweed scoring of student recitations.** Tarteel does this
  poorly already; doing it badly here would erode trust in
  everything else the app says. Not your fight.
- **Translation auto-generation.** Stick to Sahih / Pickthall / Yusuf
  Ali. Auto-translation of revealed text is a dignity issue, not a
  feature gap.
- **Social feed / "share your hifz progress" gamification.** Hifz is
  an act of worship, not a Duolingo streak. Keep it personal and
  teacher-mediated.
- **A mobile app rewrite.** The Streamlit web UI is already
  phone-accessible; effort spent on a native app is effort not spent
  on M2/T1/C1.

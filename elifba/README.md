# Elif-Ba videos

Animated lessons that teach the Arabic letters to young children (about three
years old), for the *Muziksiz Cizgi Filimler* YouTube channel. Each lesson is
rendered from an HTML/JS page, frame by frame, into a 1080p MP4. They follow the
printed Diyanet Elif-Ba (2024).

## The series

| Ders | Lesson | Content | Length |
|---|---|---|---|
| 1 | `harfler` | Every letter, named twice, with a pause after each to say it back | 4:54 |
| 2 | `kelime` | Each letter on its own, then at the start, middle and end of a word (Başta / Ortada / Sonda), each word spelled letter by letter | 11:39 |
| 3 | `harekeler` | Every letter with fetha, kesra and damme together — اَ اِ اُ — each said in turn | 4:47 |
| 4 | `okuma` | Reading words: three syllables (بَ عَ ثَ) said one by one on three tulips, then slid together into the joined word (بَعَثَ), which is read twice | 4:45 |
| 1b | `karisik` | Every letter again, out of order: a silence to name it first, then Diyanet says it | 4:19 |
| 5 | `harekeli` | Syllable rows mixing fetha, kesra and damme (اَ بِ تُ), then five words read the ders 4 way, each mark in its own colour | 3:23 |

`fetha` (fetha only, in the order of Diyanet's web course) also exists but is
not part of the series.

Every video opens with the besmele, recited, and ends with "Aferin!".

## The look

A soft 3D clay diorama at golden hour: an Ottoman tulip garden in layered
parallax, with domes and minarets on the horizon. The camera glides right to
left — the way Arabic is read — from tulip to tulip. Each tulip blooms, its
letter rises out of it in clay, bounces each time its name is said, then flies
up into a bunting that fills across the top as the lesson goes on. At the end
night falls, a crescent moon rises and lanterns float up.

In ders 2 the word cards light up the letter being said, in time with
Diyanet's spelling, so a child can follow which letter is read.

## What is in a video, and what is deliberately not

- **Sound:** only human recordings from Diyanet (kuran.diyanet.gov.tr/elifba),
  and Osman Şahin's besmele. No narrator, no music, no sound effects. Arabic
  text-to-speech was tried and rejected: it garbles isolated letters and harakat.
- **No characters, no faces, no creatures** — the world is flowers, sky and buildings.
- **Simple by design:** no prompts, no quiz, five minutes at most (ders 2 is the
  one longer exception, at the owner's request).
- **Nothing near a letter that a child could read as part of it.** Reviews from
  a three-year-old's point of view found that a stroke beside alif read as a
  second alif, badges beside dots read as extra dots, a pen under a letter read
  as a kasra, and a ring above a letter read as a cezm. The garden keeps the sky
  behind the letters empty for the same reason: a minaret behind a letter reads
  as an alif. Check stills of any change.
- **Harakat in colour** wherever they are taught: fetha red-orange, kesra blue,
  damme green.

## Making a video

```bash
python -m elifba.fetch_audio                       # once: Diyanet's recordings -> elifba/audio/
python -m elifba.spelling                          # once: letter timings for ders 2 -> data/spelling.json
python -m elifba.render harekeler --stills 12,40   # check a few frames first
python -m elifba.render harekeler                  # -> output/elifba/elifba_harekeler.mp4
python -m elifba.render harekeler --thumbnail      # -> output/elifba/elifba_harekeler_thumb.jpg
```

`fetch_audio` must run from a normal shell (PowerShell); Diyanet refuses the
sandboxed one. The audio is not committed. Rendering needs Microsoft Edge (used
through Playwright, nothing to download) and an NVIDIA GPU for NVENC; a lesson
takes roughly its own length to render.

## How it is built

| File | Role |
|---|---|
| `curriculum.py` | Diyanet's 13-unit course, ported, with the recording for every tile. Sounds are numbered by position (`btn_1..N`), with quirks such as a skipped number in the fetha lesson; the port follows the site's helpers one for one. |
| `storyboard.py` | `LESSONS`: turns a lesson into timed scenes, audio placements, "beats" (when a sound starts) and YouTube chapters. |
| `engine/` | `index.html` + `engine.js` + `style.css`. `seek(t)` draws any frame on one canvas as a pure function of time, so rendering is exact and repeatable. Letters are modelled as clay sprites (extrusion, rim light, gloss) with harakat in their own colours; ders 2's word cards are DOM so a letter inside a word can be lit while the word stays joined. |
| `spelling.py` | When each letter is said in ders 2's spelled words: Diyanet's letter-name recordings, laid end to end, aligned to each word by DTW on MFCCs. |
| `render.py` | Steps the page through every frame with Playwright, pipes JPEGs into FFmpeg (NVENC), mixes the audio in numpy (each clip levelled, then loudness-normalised). |
| `fetch_audio.py` | Downloads every recording the curriculum refers to. |

Fonts: Scheherazade New (Arabic) and Nunito (Turkish labels), both under the SIL
Open Font License, in `engine/fonts/`.

Tests: `tests/test_elifba.py` pins the sound numbering, the lesson contents, the
length limits and the YouTube chapter rules.

## Known pitfalls

- **U+06EA in Scheherazade** draws as a hollow dot under the letter. In the
  besmele it made الرَّحِيم read as الرَّجيم. Displayed text uses a plain kasra.
- **Ya with a hareke** is shown dotted (يَ) as in the printed book; Diyanet's web
  course writes it dotless.
- **Diyanet's word recordings** in ders 2 call alif "hemze". The letter on its own
  uses the ders 1 recording, so it is still "Elif".
- **YouTube's daily upload limit** ("exceeded the number of videos they may
  upload") resets within a day; retrying sooner does not help.

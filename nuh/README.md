# Surah Nuh — a film drawn in code

A 308-second film set to Ahmed Taleb's recitation of Surah Nuh
(`assets/audio/nuh_ahmed_taleb_eyVqGHfA5do.webm`, from
youtube.com/watch?v=eyVqGHfA5do). 1920×1080, 24 fps, 7,399 frames, 28 shots,
one per ayah, each cut on the reciter's breath.

No image models, no image files, no fonts on screen: every frame is drawn by
numpy, OpenCV and pycairo. The only sound is the recitation itself,
untouched: no music, no effects.

**One quasi-static scene** (`night.py`): rain pouring off the edge of a
shingled roof at night, the full moon behind thin cloud, a fence below. The
camera never moves. Rain in four depths, water dripping off the eave, glints
on the wet shingles and the cloud over the moon are what move.

It is rendered in linear light and developed like a photograph: filmic tone
curve, lens glare, depth of field, slight chromatic aberration, sensor grain.
The moon carries the real maria (Imbrium, Serenitatis, Tranquillitatis,
Crisium, Procellarum...) and the Tycho/Copernicus ray systems at their
selenographic positions, projected on a sphere.

The cuts below no longer cut the picture; they key gentle changes to the
ayat: heavier rain in 11-12 ("He will send the sky upon you in abundance")
and 25-27 (the flood), the cloud clearing off the moon in 16 ("the moon a
light"), lighter rain in 28, and a fade after the last word.

```bash
pip install opencv-python-headless scipy pycairo
python -m nuh.render --stills 30,120
python -m nuh.render                # full film -> output/nuh/surah_nuh_ahmed_taleb.mp4
```

| File | Role |
|---|---|
| `data/cuts.json` | the 28 ayah boundaries and word times |
| `paint.py` | tileable spectral noise and small helpers |
| `night.py` | the scene: moon, set, rain, drips, glints, the photographic develop |
| `render.py` | parallel chunked encode, mux with the recitation |

## How the cuts were found

The recording has no true silences: a low hum runs under it and the quietest
1% is still at −28 dB, so silence detection finds nothing. The reciter also
joins some ayat in one breath (3 into 4, for example), so pauses alone can't
place every cut.

1. Whisper (medium, CPU) transcribed the recitation with word timestamps.
2. The transcript was aligned to the Uthmani text: all 226 words matched. The
   recitation opens directly on ayah 1, with no ta'awwudh and no basmala.
3. Whisper folds each pause into the start of the following word. So each cut
   is searched from the end of one ayah's last word into the first part of the
   next ayah's first word. Within that window the cut goes at the centre of the
   deepest dip in 300–3500 Hz energy, which skips the hum.

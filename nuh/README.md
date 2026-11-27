# Surah Nuh — a film drawn in code

A 308-second film set to Ahmed Taleb's recitation of Surah Nuh
(`assets/audio/nuh_ahmed_taleb_eyVqGHfA5do.webm`, from
youtube.com/watch?v=eyVqGHfA5do). 1920×1080, 24 fps, 7,399 frames, one
continuous shot.

No image models, no image files, no fonts on screen: every frame is drawn by
numpy, OpenCV and pycairo. The only sound is the recitation itself,
untouched: no music, no effects.

**One quasi-static macro shot** (`leaf.py`): a single leaf hanging from a
branch at night in the rain, the moon far behind it. The camera never moves.
Raindrops strike the leaf, which dips and springs back as a damped spring;
water beads slide to the tip, swell and fall. The lens is modelled at macro
scale (1 mm = 16 px): the moon at infinity is a large defocus disc, glints
behind are 9-blade bokeh with cat's-eyes off axis, depth of field is
millimetres, and a 1/48 s shutter turns a 7 m/s raindrop into one
frame-crossing streak. Each drop on the leaf is a small lens. Rendered in
linear light and developed like a photograph (tone curve, glare, ghost,
chromatic aberration, night grain).

The cuts below no longer cut the picture; they key gentle changes to the
ayat: more strikes in 11-12 ("He will send the sky upon you in abundance")
and 25-27 (the flood), fewer in 28, and a fade after the last word.

```bash
pip install opencv-python-headless scipy pycairo
python -m nuh.render --stills 30,120
python -m nuh.render                # full film -> output/nuh/surah_nuh_ahmed_taleb.mp4
```

| File | Role |
|---|---|
| `data/cuts.json` | the 28 ayah boundaries and word times |
| `paint.py` | tileable spectral noise and small helpers |
| `leaf.py` | the macro scene: leaf, drops, impacts, lens model, the photographic develop |
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

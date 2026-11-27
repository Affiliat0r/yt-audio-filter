# Surah Nuh — a film drawn in code

A 308-second film set to Ahmed Taleb's recitation of Surah Nuh
(`assets/audio/nuh_ahmed_taleb_eyVqGHfA5do.webm`, from
youtube.com/watch?v=eyVqGHfA5do). 1920×1080, 24 fps, 7,399 frames, 28 shots,
one per ayah, each cut on the reciter's breath.

No image models, no image files, no fonts on screen: every frame is drawn by
numpy, OpenCV, pycairo and Pillow. The only sound is the recitation itself,
untouched: no music, no effects. No people, faces, animals or idols.

```bash
pip install opencv-python-headless scipy pycairo
python -m nuh.render --mid          # one still per shot -> output/nuh/still_*.png
python -m nuh.render --stills 30,120
python -m nuh.render                # full film -> output/nuh/surah_nuh_ahmed_taleb.mp4
```

The full render takes about 20 minutes on four CPU cores. It is resumable:
finished chunks in `output/nuh/chunks/` are kept.

| File | Role |
|---|---|
| `data/cuts.json` | the 28 shot boundaries and the word times some shots act on |
| `paint.py` | primitives: gradients, ridges, glows, tileable noise, cairo layers, film finish |
| `world.py` | the recurring world: sky by time of day, land, village, lamp, rain, water, stones |
| `shots.py` | the 28 shots, each a pure function of time within the shot |
| `render.py` | frame -> shot lookup, parallel chunked encode, mux with the recitation |

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

Word events (shot-local seconds) drive four moments: the window at
"wa asrartu" (9), the sun at "ash-shams" (16), the sprouts returning at
"wa yukhrijukum" (18), the lightning on each of the five names (23), and the
red glow at "nāran" (25).

## Shot list

| Ayah | Picture |
|---|---|
| 1 | From black: the dark plain at first grey, a lamp is lit on the hill above the village |
| 2–4 | The lamp on the hill: closer, a warm path down to the houses, the moon crossing the sky as the term runs out |
| 5 | Day and night turning faster and faster |
| 6 | The village windows go dark one by one |
| 7 | House fronts at dusk: shutters close, cloth drops over windows, doors shut |
| 8 | A sunlit open square at noon |
| 9 | The square dims; at "wa asrartu", one lit window at night |
| 10 | Clouds gather over cracked earth |
| 11 | Rain; the cracks fill with water |
| 12 | The plain greens, a river finds its bed, trees rise |
| 13 | A vast turning sky above a thread of rooftops |
| 14 | A drop, and rings, layer on layer |
| 15 | Seven skies in bands, revealed as the camera rises |
| 16 | The moon over water; at "ash-shams", the sun rises |
| 17 | Shoots rise from the soil |
| 18 | They wither and sink back; at "wa yukhrijukum", they rise again |
| 19 | The earth from above, fields like the weave of a carpet |
| 20 | Wide paths drawn across it |
| 21 | Five plain uncarved stones at dusk, the village dim beyond |
| 22 | The sky coils into a storm |
| 23 | Lightning finds each stone as each name is recited |
| 24 | Fog rolls in; the stones go dim |
| 25 | The flood climbs the stones; a red glow at "nāran" |
| 26 | The storm at its height over open sea |
| 27 | Everything goes still |
| 28 | One doorway lit gold; the water goes down and dawn rises |

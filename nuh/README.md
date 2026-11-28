# Surah Nuh — a film drawn in code

A 308-second film set to Ahmed Taleb's recitation of Surah Nuh
(`assets/audio/nuh_ahmed_taleb_eyVqGHfA5do.webm`, from
youtube.com/watch?v=eyVqGHfA5do). 1920×1080, 24 fps, 7,399 frames, one
continuous shot.

No image models, no image files, no fonts on screen. Every frame is built
from code: the scene is path-traced by Blender's Cycles through its Python
module (`bpy`), with meshes, shaders and textures generated in numpy and
handed to Blender in memory. The only sound is the recitation itself,
untouched: no music, no effects.

**The shot**: one leaf hanging from a branch at night in the rain, the moon
far behind it. The camera never moves. Optics are the renderer's: a 100 mm
macro lens on a full-frame sensor, focused 33 cm away at f/5.6, so the moon
at infinity is a soft defocus disc, depth of field is millimetres, every
drop really refracts, and motion blur comes from a 1/48 s shutter.

**One loop, cycled.** Because nothing but the water moves, the film is one
seamless 8-second loop (192 core frames) repeated under the recitation:

| Loop time | What happens |
|---|---|
| 0.9 s | a raindrop strikes the leaf: it jolts and rings down, a crown of droplets flies off, a fresh bead wobbles |
| 1.6-3.6 s | the bead slides to the tip and merges with the pendant drop |
| 5.6 s | the swollen pendant lets go and falls; the leaf springs up |
| 8.0 s | the pendant has refilled and the leaf is back where the loop began |

```bash
pip install bpy==5.3.0 opencv-python-headless scipy pycairo   # bpy needs the matching Python (3.13 here)
python -m nuh.cycles_leaf --still 61 --out /tmp/leaf.exr       # one still
python -m nuh.cycles_loop --frames 0:192                       # the core loop, ~95 s/frame on 4 CPU cores
python -m nuh.assemble                                         # grade, loop under the audio, fade
```

The loop render resumes where it stopped: finished frames in
`output/nuh/loop/` are skipped.

| File | Role |
|---|---|
| `data/cuts.json` | the 28 ayah boundaries and word times |
| `cycles_leaf.py` | the scene: lens, leaf mesh and textures, water, moon, lights, the tree behind, rain |
| `cycles_loop.py` | the loop's story as periodic functions of time; renders the core frames |
| `leaf.py` | the earlier 2D renderer, and `develop_render`, the night grade applied to every frame |
| `assemble.py` | grade the loop, cycle it under the recitation, fade, encode |
| `paint.py` | tileable spectral noise and small helpers |
| `render.py` | the earlier 2D film renderer |

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

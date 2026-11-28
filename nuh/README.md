# Surah Nuh — a leaf in the rain

A 308-second film set to Ahmed Taleb's recitation of Surah Nuh
(`assets/audio/nuh_ahmed_taleb_eyVqGHfA5do.webm`, from
youtube.com/watch?v=eyVqGHfA5do). 1920×1080, 24 fps. The only sound is the
recitation, untouched.

**The shot**: the tip of one leaf at night in the rain, the moon behind it,
through a 100 mm macro lens at f/5.6 focused 33 cm away. The camera never
moves. Shot at 96 fps and played at 24: everything is in 4× slow motion,
because at real speed the strike, the splash and the falling drop each last
a frame or two and smear into nothing.

## The pipeline

| Step | Tool | What it does |
|---|---|---|
| Physics | `drops.py` | hanging-drop shapes (Young–Laplace), when a drop lets go (critical volume, Harkins–Brown), resting-drop shapes, the bead's slide, the leaf's swing |
| Scene, motion, render | Blender (`blend_shot.py`, `bpy`) | the scene, keyframes from the physics, rain and splash as Blender particle simulations under real gravity, Cycles path tracing with real optics; writes linear 32-bit EXR |
| Develop | [PhotoCraft](https://github.com/storytold/photocraft) (`photocraft.py`) | the grade, through `photocraft-cli run`: Exposure, 16-bit, a Screened glow, Curves, Hue/Saturation, Color Balance, Lens Correction (chromatic aberration, vignette), Add Noise (grain) |
| Encode | ffmpeg | the developed frames into a loop, cycled under the recitation with fades |

```bash
pip install bpy==5.3.0 opencv-python-headless pycairo   # bpy needs Python 3.13
git clone https://github.com/storytold/photocraft ../storytold/photocraft
(cd ../storytold/photocraft && cargo build --release -p photocraft-cli)

python -m nuh.blend_shot --mechanics              # the physics numbers
python -m nuh.blend_shot --raw --frames 0:192     # ~85 s a frame on 4 CPU cores; resumes
python -m nuh.photocraft                          # develop (~1 s a frame) and build the film
python -m nuh.blend_shot --save                   # optional: output/nuh/blend/nuh_leaf.blend
```

## The loop (8 s on screen, 2 s of real time, seamless)

| Screen time | What happens, and the physics behind it |
|---|---|
| 1.0 s | A 2.2 mm raindrop at its terminal 6.8 m/s strikes the leaf's face 65° from its normal. Its momentum swings the leaf on its stalk, a damped spring at 2.6 Hz: the tip moves up to 4 mm. Spray flies off; 60 % stays as a bead. |
| 1–5 s | The bead runs down the steepest path to the lower edge, then along the edge to the tip, the edge's lowest point. Contact-angle hysteresis holds it back and veins make it jerk. It merges when it touches the hanging drop. |
| 5.4 s | The hanging drop was just under the largest volume surface tension can hold on the tip (14.3 mm³). The bead takes it past that; it necks and lets go within 10 ms. 62 % falls under gravity, the rest stays. The leaf, lighter at its tip, springs up. |
| to 8 s | Runoff from higher up the leaf swells the hanging drop back to where the loop began: water in equals water out, every loop. |

The leaf hangs so that the physics works: its face is turned 50° toward the
sky so rain can land on it, and it is steep enough that its tip is the
lowest point of its edge. Resting drops are small enough for hysteresis to
pin them, and none sit on the track the bead clears.

The rain itself is in the scene but barely visible, as with a real camera:
each drop smears across its exposure into a faint streak.

## How the cuts were found

`data/cuts.json` holds the 28 ayah boundaries and word times, used for the
fades.

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

## Other files

| File | Role |
|---|---|
| `cycles_leaf.py` | the leaf's outline, its vein and translucency textures, shared materials |
| `paint.py` | tileable spectral noise and small helpers |

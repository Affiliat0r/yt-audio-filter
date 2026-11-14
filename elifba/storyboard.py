"""Turn the alphabet into a timed script for one toddler video.

The audience is about three years old. What the owner asked for, after seeing
richer versions: just the letters — each one shown and named, with pauses in
between — all 28 in one video of five minutes at most. No prompts, no quiz.

* one letter on screen at a time, very large, with its own colour;
* the only voice is Diyanet's human recording of each letter's name (and the
  recited besmele) — no narrator;
* each name is heard twice, each time followed by a pause long enough for a
  small child to say it back;
* a reed pen (kalem) taps the letter as its name is said — no character, no face.

The output is plain data — scenes with start/end times, the audio to place, and
"beats" that tell the page when a sound starts. ``engine/engine.js`` draws it;
``render.py`` films it.
"""

from __future__ import annotations

import json
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from elifba.curriculum import JE

ROOT = Path(__file__).resolve().parent
AUDIO = ROOT / "audio"
BESMELE = AUDIO / "quran" / "besmele_osmansahin.mp3"

APPEAR = 0.7        # the card grows in before the name is said
PAUSE = 2.5         # silence after each hearing: time to say it back
HEARINGS = 2        # how many times each name is said
MAX_SECONDS = 300   # the owner's limit

#: One warm, distinct colour per letter, cycling. Readable on the cream card.
PALETTE = ["#E4572E", "#1C7CB8", "#2A9D5B", "#8E44AD", "#E08E0B", "#C2185B", "#00897B"]

#: Diyanet's (and the printed Elif-Ba's) heavy letters, marked with a red rim.
HEAVY = set("خصضطظغق")

#: Chapter marks for the description, one per row of seven (YouTube wants >= 10 s each).
CHAPTER_EVERY = 7


def name_sound(letter: str) -> Path:
    """Diyanet's recording of the letter's name ("Elif", "Bâ", ...)."""
    return AUDIO / "elifba" / "harfler" / "harfler" / f"btn_{JE.index(letter) + 1}.mp3"


def color_of(letter: str) -> str:
    return PALETTE[JE.index(letter) % len(PALETTE)]


@lru_cache(maxsize=None)
def duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    return float(out)


class Script:
    """A cursor over time that collects scenes, audio placements and beats."""

    def __init__(self) -> None:
        self.t = 0.0
        self.scenes: List[Dict] = []
        self.audio: List[Dict] = []
        self.beats: List[Dict] = []
        self.chapters: List[Dict] = []

    def play(self, path: Path, target: Optional[str] = None, kind: str = "voice") -> float:
        """Place a sound at the cursor, advance past it; returns its start."""
        start, dur = self.t, duration(path)
        self.audio.append({"t": round(start, 3), "path": str(path.relative_to(ROOT)).replace("\\", "/"),
                           "dur": round(dur, 3), "kind": kind})
        if target:
            self.beats.append({"t": round(start, 3), "dur": round(dur, 3), "target": target})
        self.t += dur
        return start

    def wait(self, seconds: float) -> None:
        self.t += seconds

    def open_scene(self, kind: str, **props) -> Dict:
        scene = {"type": kind, "start": round(self.t, 3), **props}
        self.scenes.append(scene)
        return scene

    def close_scene(self, scene: Dict) -> None:
        scene["end"] = round(self.t, 3)


def letter_segment(s: Script, letter: str, number: int) -> None:
    scene = s.open_scene("letter", letter=letter, color=color_of(letter), heavy=letter in HEAVY, number=number)
    s.wait(APPEAR)
    for _ in range(HEARINGS):
        s.play(name_sound(letter), target="letter")
        s.wait(PAUSE)
    s.close_scene(scene)


def episode(letters: Sequence[str] = JE) -> Dict:
    s = Script()

    scene = s.open_scene("besmele")
    s.chapters.append({"t": 0.0, "title": "Besmele"})     # YouTube wants the first chapter at 0:00
    s.wait(0.6)
    s.play(BESMELE, target="besmele", kind="quran")
    s.wait(1.0)
    s.close_scene(scene)

    scene = s.open_scene("title")
    s.wait(3.0)
    s.close_scene(scene)

    for i, letter in enumerate(letters):
        if i % CHAPTER_EVERY == 0:
            last = letters[min(i + CHAPTER_EVERY, len(letters)) - 1]
            s.chapters.append({"t": round(s.t, 3), "title": f"{letter} – {last}"})
        letter_segment(s, letter, i)

    # the closing five seconds stay inside the last chapter: YouTube wants 10 s or more each
    scene = s.open_scene("outro", letters=list(letters), colors=[color_of(l) for l in letters])
    s.wait(5.0)
    s.close_scene(scene)

    return {"letters": list(letters), "duration": round(s.t, 3), "scenes": s.scenes,
            "audio": s.audio, "beats": s.beats, "chapters": s.chapters}


if __name__ == "__main__":
    import sys

    sys.stdout.reconfigure(encoding="utf-8")
    ep = episode()
    print(json.dumps({k: ep[k] for k in ("duration", "chapters")}, ensure_ascii=False))

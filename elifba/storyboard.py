"""Turn a group of letters into a timed script for one toddler video.

The audience is about three years old, so the script follows what works at that
age rather than what an adult course does:

* one letter on screen at a time, very large, with its own colour;
* every Arabic sound is a human recording (Diyanet), heard several times;
* the echo pattern — the model says it, the child gets a silent turn with a
  visible cue, the model says it again so the child can compare;
* the dots are the feature that tells look-alike letters apart (ب ت ث), so they
  are pointed out and counted;
* a two-choice "which one?" game, always between letters that look alike;
* short: a video is four letters or fewer, about three minutes.

No text needs reading. The output is plain data — scenes with start/end times,
the audio to place, and "beats" that tell the page when a sound starts so the
letter can pulse with it. ``engine/engine.js`` draws it; ``render.py`` films it.
"""

from __future__ import annotations

import json
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from elifba.curriculum import JE
from elifba.voice import CUES, cue_path

ROOT = Path(__file__).resolve().parent
AUDIO = ROOT / "audio"
BESMELE = AUDIO / "quran" / "besmele_osmansahin.mp3"

GAP = 0.35          # breath between two sounds
CHILD_TURN = 2.6    # minimum silent turn for the child to answer
THINK = 3.2         # silent time to pick in the game

#: What to say about each letter's dots ("noktası"), a cue key from voice.CUES.
DOTS = {
    "ا": "alif_tall", "ب": "dots_below_1", "ت": "dots_above_2", "ث": "dots_above_3",
    "ج": "dots_mid_1", "ح": "dots_none", "خ": "dots_above_1",
    "د": "dots_none", "ذ": "dots_above_1", "ر": "dots_none", "ز": "dots_above_1",
    "س": "dots_none", "ش": "dots_above_3", "ص": "dots_none", "ض": "dots_above_1",
    "ط": "dots_none", "ظ": "dots_above_1", "ع": "dots_none", "غ": "dots_above_1",
    "ف": "dots_above_1", "ق": "dots_above_2", "ك": "dots_none", "ل": "dots_none",
    "م": "dots_none", "ن": "dots_above_1", "هـ": "dots_none", "و": "dots_none", "ى": "dots_none",
}

#: Shape families: letters that differ only by their dots. The game pairs within these.
FAMILIES = [set("بتثن"), set("جحخ"), set("دذ"), set("رز"), set("سش"), set("صض"), set("طظ"),
            set("عغ"), set("فق")]

#: The video series: one shape family (or two small ones) per episode.
SERIES = [
    ["ا", "ب", "ت", "ث"],
    ["ج", "ح", "خ"],
    ["د", "ذ", "ر", "ز"],
    ["س", "ش", "ص", "ض"],
    ["ط", "ظ", "ع", "غ"],
    ["ف", "ق", "ك", "ل"],
    ["م", "ن", "هـ", "و", "ى"],
]

#: One warm, distinct colour per letter, cycling. Readable on the cream card.
PALETTE = ["#E4572E", "#1C7CB8", "#2A9D5B", "#8E44AD", "#E08E0B", "#C2185B", "#00897B"]

#: Diyanet's (and the printed Elif-Ba's) heavy letters, marked with a red rim.
HEAVY = set("خصضطظغق")

PRAISE = ["praise_1", "praise_2", "praise_3", "praise_4"]


def name_sound(letter: str) -> Path:
    """Diyanet's recording of the letter's name ("Elif", "Bâ", ...)."""
    return AUDIO / "elifba" / "harfler" / "harfler" / f"btn_{JE.index(letter) + 1}.mp3"


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

    def cue(self, key: str) -> float:
        return self.play(cue_path(key), target="cue:" + key, kind="cue")

    def wait(self, seconds: float) -> None:
        self.t += seconds

    def phase(self, scene: Dict, kind: str, start: float, **extra) -> None:
        scene.setdefault("phases", []).append({"kind": kind, "start": round(start, 3),
                                               "end": round(self.t, 3), **extra})

    def open_scene(self, kind: str, **props) -> Dict:
        scene = {"type": kind, "start": round(self.t, 3), **props}
        self.scenes.append(scene)
        return scene

    def close_scene(self, scene: Dict) -> None:
        scene["end"] = round(self.t, 3)


def _partner(letter: str, pool: Sequence[str]) -> str:
    """The letter in ``pool`` most easily confused with ``letter``."""
    for fam in FAMILIES:
        if letter in fam:
            for other in pool:
                if other != letter and other in fam:
                    return other
    return next(o for o in pool if o != letter)


def letter_segment(s: Script, letter: str, color: str, number: int) -> None:
    sound = name_sound(letter)
    scene = s.open_scene("letter", letter=letter, color=color, heavy=letter in HEAVY, number=number,
                         dots=DOTS[letter])
    s.chapters.append({"t": scene["start"], "title": f"{letter}"})

    start = s.t
    s.wait(0.7)                      # the letter bounces in
    s.play(sound, target="letter")
    s.wait(0.6)
    s.phase(scene, "meet", start)

    start = s.t
    s.cue(DOTS[letter])
    s.wait(0.8)
    s.phase(scene, "dots", start)

    start = s.t
    s.cue("listen")
    s.wait(GAP)
    for _ in range(3):
        s.play(sound, target="letter")
        s.wait(0.55)
    s.phase(scene, "listen", start)

    for say_cue in ("say", "say_again"):
        start = s.t
        s.cue(say_cue)
        s.wait(GAP)
        turn = s.t
        s.wait(max(CHILD_TURN, duration(sound) + 1.6))
        s.phase(scene, "turn", turn)
        s.phase(scene, "say", start, turn=round(turn, 3))
        start = s.t
        s.play(sound, target="letter")
        s.wait(0.5)
        s.phase(scene, "echo", start)

    start = s.t
    s.cue(PRAISE[number % len(PRAISE)])
    s.wait(0.7)
    s.phase(scene, "praise", start)
    s.close_scene(scene)


def game(s: Script, letters: Sequence[str], colors: Dict[str, str]) -> None:
    s.chapters.append({"t": round(s.t, 3), "title": "Hangisi?"})
    order = list(letters[1:]) + [letters[0]] if len(letters) > 2 else list(letters)
    for i, target in enumerate(order):
        other = _partner(target, letters)
        options = [target, other] if i % 2 == 0 else [other, target]
        scene = s.open_scene("game", options=options, target=target, colors=[colors[o] for o in options],
                             round=i + 1, rounds=len(order))
        start = s.t
        s.wait(0.6)
        s.cue("which")
        s.wait(GAP)
        s.play(name_sound(target), target="ask")
        s.phase(scene, "ask", start)
        start = s.t
        s.wait(THINK)
        s.phase(scene, "think", start)
        start = s.t
        s.cue("this_one")
        s.wait(0.2)
        s.play(name_sound(target), target="answer")
        s.wait(0.6)
        if i == len(order) - 1:
            s.cue("praise_3")
            s.wait(0.5)
        s.phase(scene, "reveal", start)
        s.close_scene(scene)


def recap(s: Script, letters: Sequence[str], colors: Dict[str, str]) -> None:
    s.chapters.append({"t": round(s.t, 3), "title": "Tekrar"})
    scene = s.open_scene("recap", letters=list(letters), colors=[colors[l] for l in letters])
    start = s.t
    s.wait(0.5)
    s.cue("recap")
    s.wait(0.5)
    s.phase(scene, "intro", start)
    for i, letter in enumerate(letters):            # model reads the row
        start = s.t
        s.play(name_sound(letter), target=f"recap:{i}")
        s.wait(0.45)
        s.phase(scene, "model", start, index=i)
    start = s.t
    s.cue("say")
    s.wait(0.4)
    s.phase(scene, "your-turn", start)
    for i, letter in enumerate(letters):            # child reads it, each echoed
        start = s.t
        s.wait(max(2.2, duration(name_sound(letter)) + 1.2))
        s.phase(scene, "turn", start, index=i)
        start = s.t
        s.play(name_sound(letter), target=f"recap:{i}")
        s.wait(0.4)
        s.phase(scene, "echo", start, index=i)
    s.close_scene(scene)


def episode(number: int, letters: Sequence[str]) -> Dict:
    colors = {l: PALETTE[(JE.index(l)) % len(PALETTE)] for l in letters}
    s = Script()

    scene = s.open_scene("besmele")
    s.wait(0.6)
    s.play(BESMELE, target="besmele", kind="quran")
    s.wait(0.8)
    s.close_scene(scene)

    scene = s.open_scene("title", episode=number, letters=list(letters), colors=[colors[l] for l in letters])
    s.wait(0.4)
    s.cue("intro")
    s.wait(1.6)
    s.close_scene(scene)

    for i, letter in enumerate(letters):
        letter_segment(s, letter, colors[letter], i)
    game(s, letters, colors)
    recap(s, letters, colors)

    scene = s.open_scene("outro", letters=list(letters), colors=[colors[l] for l in letters])
    s.chapters.append({"t": scene["start"], "title": "Aferin!"})
    s.wait(0.4)
    s.cue("outro")
    s.wait(0.5)
    s.cue("goodbye")
    s.wait(2.0)
    s.close_scene(scene)

    return {"episode": number, "letters": list(letters), "duration": round(s.t, 3), "scenes": s.scenes,
            "audio": s.audio, "beats": s.beats, "chapters": s.chapters, "cues": CUES}


if __name__ == "__main__":
    import sys

    sys.stdout.reconfigure(encoding="utf-8")
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    ep = episode(n, SERIES[n - 1])
    print(json.dumps({k: ep[k] for k in ("episode", "letters", "duration", "chapters")}, ensure_ascii=False))

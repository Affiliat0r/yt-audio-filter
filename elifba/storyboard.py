"""Turn a group of letters into a timed script for one toddler video.

The audience is about three years old, so the script follows what works at that
age rather than what an adult course does:

* one letter on screen at a time, very large, with its own colour;
* the only voice is Diyanet's human recordings of the Arabic (and the recited
  besmele) — no narrator. Prompts are visual: a reed pen (kalem) that points
  like a hoca's pointer stick, plus a few icons;
* the echo pattern — the model says it, the child gets a silent turn while the
  pen glides under the letter right to left, the model says it again;
* the dots are what tells look-alike letters apart (ب ت ث), so the pen touches
  each one as it lights up;
* a two-choice "which one?" game, always between letters that look alike, in
  one neutral ink so that colour can't give the answer away;
* short: four letters or so, under five minutes.

The output is plain data — scenes with start/end times, the audio to place, and
"beats" that tell the page when something happens. ``engine/engine.js`` draws it;
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

GAP = 0.35          # breath between two sounds
PROMPT = 1.0        # a visual prompt (icon) appears this long before what it announces
FIRST_TURN = 3.8    # the child's first go at a new letter: toddlers take ~1 s to start
CHILD_TURN = 3.2    # later turns
RECAP_TURN = 3.0
THINK = 4.0         # silent time to point at a card in the game
DOT_STEP = 0.8      # the pen touches one dot per this many seconds

#: Short words shown next to the icons, for the parent watching along. Never spoken.
LABELS = {
    "listen": "Dinle",
    "say": "Sen söyle",
    "which": "Hangisi?",
    "praise": "Aferin!",
}

#: How each letter's dots are presented.
DOTS = {
    "ا": "alif_tall", "ب": "dots_below_1", "ت": "dots_above_2", "ث": "dots_above_3",
    "ج": "dots_mid_1", "ح": "dots_none", "خ": "dots_above_1",
    "د": "dots_none", "ذ": "dots_above_1", "ر": "dots_none", "ز": "dots_above_1",
    "س": "dots_none", "ش": "dots_above_3", "ص": "dots_none", "ض": "dots_above_1",
    "ط": "dots_none", "ظ": "dots_above_1", "ع": "dots_none", "غ": "dots_above_1",
    "ف": "dots_above_1", "ق": "dots_above_2", "ك": "dots_none", "ل": "dots_none",
    "م": "dots_none", "ن": "dots_above_1", "هـ": "dots_none", "و": "dots_none", "ى": "dots_none",
}
DOT_COUNT = {"dots_below_1": 1, "dots_above_1": 1, "dots_mid_1": 1, "dots_above_2": 2, "dots_above_3": 3}

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

#: Which side the right answer is on, round by round: not a pattern a child can learn.
SIDES = [1, 0, 0, 1, 0, 1, 1, 0]


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

    def beat(self, target: str, dur: float) -> float:
        """A silent, visual-only event (a dot lighting up, the pen touching it)."""
        start = self.t
        self.beats.append({"t": round(start, 3), "dur": round(dur, 3), "target": target})
        self.t += dur
        return start

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
    """The letter in ``pool`` most easily confused with ``letter``.

    A look-alike from the same shape family, preferring the *next* one in the
    group (so ب/ت, then ت/ث — the hardest pair, two dots against three), else
    the previous one. A letter with no look-alike is paired with its neighbour.
    """
    i = list(pool).index(letter)
    fam = next((f for f in FAMILIES if letter in f), set())
    after = [o for o in pool[i + 1:] if o in fam]
    before = [o for o in reversed(pool[:i]) if o in fam]
    if after or before:
        return (after or before)[0]
    return pool[i + 1] if i + 1 < len(pool) else pool[i - 1]


def game_rounds(letters: Sequence[str]) -> List[tuple]:
    """(target, distractor) per round: a warm-up with a letter that has no
    look-alike first, then each look-alike against its nearest twin."""
    plain = [l for l in letters if not any(l in f for f in FAMILIES)]
    rest = [l for l in letters if l not in plain]
    return [(t, _partner(t, letters)) for t in plain + rest]


def letter_segment(s: Script, letter: str, color: str, number: int) -> None:
    sound = name_sound(letter)
    dots = DOTS[letter]
    scene = s.open_scene("letter", letter=letter, color=color, heavy=letter in HEAVY, number=number, dots=dots)
    s.chapters.append({"t": scene["start"], "title": f"{letter}"})

    start = s.t
    s.wait(1.1)                      # the pen lifts out of the inkwell; the letter bounces in
    s.play(sound, target="letter")
    s.wait(0.8)
    s.phase(scene, "meet", start)

    start = s.t
    s.wait(0.4)
    n = DOT_COUNT.get(dots, 0)
    for i in range(n):               # the pen touches each dot as it lights up
        s.beat(f"dot:{i}", DOT_STEP)
    if not n:
        s.wait(1.8)                  # alif stands up tall / the pen finds no dots
    s.wait(0.6)
    s.phase(scene, "dots", start)

    start = s.t
    s.wait(PROMPT)
    for _ in range(3):
        s.play(sound, target="letter")
        s.wait(0.6)
    s.phase(scene, "listen", start)

    for turn_len in (FIRST_TURN, CHILD_TURN):
        start = s.t
        s.wait(PROMPT)
        turn = s.t
        s.wait(max(turn_len, duration(sound) + 1.6))
        s.phase(scene, "turn", turn)
        s.phase(scene, "say", start, turn=round(turn, 3))
        start = s.t
        s.play(sound, target="letter")
        s.wait(0.6)
        s.phase(scene, "echo", start)

    start = s.t
    s.wait(1.8)
    s.phase(scene, "praise", start)
    s.close_scene(scene)


def game(s: Script, letters: Sequence[str]) -> None:
    s.chapters.append({"t": round(s.t, 3), "title": "Hangisi?"})
    rounds = game_rounds(letters)
    for i, (target, other) in enumerate(rounds):
        options = [target, other] if SIDES[i % len(SIDES)] == 0 else [other, target]
        scene = s.open_scene("game", options=options, target=target, dots=[DOTS[o] for o in options],
                             round=i + 1, rounds=len(rounds))
        start = s.t
        s.wait(1.0)
        s.play(name_sound(target), target="ask")
        s.wait(0.4)
        s.phase(scene, "ask", start)
        start = s.t
        s.wait(THINK)
        s.phase(scene, "think", start)
        start = s.t
        s.wait(0.5)
        s.play(name_sound(target), target="answer")
        s.wait(0.2)
        if DOT_COUNT.get(DOTS[target]):
            s.beat("reveal-dots", 1.3)     # its dots light up: that is *why* it is this one
        s.wait(0.8 if i < len(rounds) - 1 else 1.6)
        s.phase(scene, "reveal", start)
        s.close_scene(scene)


def recap(s: Script, letters: Sequence[str], colors: Dict[str, str]) -> None:
    s.chapters.append({"t": round(s.t, 3), "title": "Tekrar"})
    scene = s.open_scene("recap", letters=list(letters), colors=[colors[l] for l in letters])
    start = s.t
    s.wait(PROMPT + 0.4)
    s.phase(scene, "intro", start)
    for i, letter in enumerate(letters):            # the model reads the row, right to left
        start = s.t
        s.play(name_sound(letter), target=f"recap:{i}")
        s.wait(0.5)
        s.phase(scene, "model", start, index=i)
    start = s.t
    s.wait(PROMPT)
    s.phase(scene, "your-turn", start)
    for i, letter in enumerate(letters):            # the child reads it, each echoed
        start = s.t
        s.wait(max(RECAP_TURN, duration(name_sound(letter)) + 1.4))
        s.phase(scene, "turn", start, index=i)
        start = s.t
        s.play(name_sound(letter), target=f"recap:{i}")
        s.wait(0.45)
        s.phase(scene, "echo", start, index=i)
    s.close_scene(scene)


def episode(number: int, letters: Sequence[str]) -> Dict:
    colors = {l: PALETTE[(JE.index(l)) % len(PALETTE)] for l in letters}
    s = Script()

    scene = s.open_scene("besmele")
    s.wait(0.6)
    s.play(BESMELE, target="besmele", kind="quran")
    s.wait(1.0)
    s.close_scene(scene)

    scene = s.open_scene("title", episode=number, letters=list(letters), colors=[colors[l] for l in letters])
    s.wait(1.0 + 0.45 * len(letters) + 1.2)          # the pen touches each letter as it appears
    s.close_scene(scene)

    for i, letter in enumerate(letters):
        letter_segment(s, letter, colors[letter], i)
    game(s, letters)
    recap(s, letters, colors)

    scene = s.open_scene("outro", letters=list(letters), colors=[colors[l] for l in letters])
    s.chapters.append({"t": scene["start"], "title": "Aferin!"})
    start = s.t
    s.wait(3.6)
    s.phase(scene, "celebrate", start)
    start = s.t
    s.wait(2.2)                # the pen returns to its inkwell; everything fades out gently
    s.phase(scene, "settle", start)
    s.close_scene(scene)

    return {"episode": number, "letters": list(letters), "duration": round(s.t, 3), "scenes": s.scenes,
            "audio": s.audio, "beats": s.beats, "chapters": s.chapters, "labels": LABELS}


if __name__ == "__main__":
    import sys

    sys.stdout.reconfigure(encoding="utf-8")
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    ep = episode(n, SERIES[n - 1])
    print(json.dumps({k: ep[k] for k in ("episode", "letters", "duration", "chapters")}, ensure_ascii=False))

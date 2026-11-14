"""Turn a lesson into a timed script for one toddler video.

The audience is about three years old. What the owner asked for, after seeing
richer versions: just the letters — each one shown and said, with pauses in
between — a whole lesson in one video of five minutes at most. No prompts, no quiz.

Lessons follow the printed Diyanet Elif-Ba (2024), the owner's reference: 1. the
letters (by name), 2. the letters inside words (at the start, middle and end;
one video of about eleven minutes — Diyanet spells each word letter by letter,
and the owner wanted it in one piece rather than three), 3. harekeler — every letter with all three harakat at once,
اَ اِ اُ, as the book's table shows them. (A fetha-only lesson, the order of
Diyanet's web course, is kept as ``fetha`` but is not part of the series.)

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

from elifba.curriculum import JE, unit_damme, unit_fetha, unit_harfler, unit_kesra

ROOT = Path(__file__).resolve().parent
AUDIO = ROOT / "audio"
BESMELE = AUDIO / "quran" / "besmele_osmansahin.mp3"

APPEAR = 0.7        # the card grows in before the name is said
PAUSE = 2.5         # silence after each hearing: time to say it back
HEARINGS = 2        # how many times each name is said
MAX_SECONDS = 300   # the owner's limit ...
#: ... except where the owner chose a longer single video over several parts
LESSON_MAX_SECONDS = {"kelime": 720}

#: One warm, distinct colour per letter, cycling. Readable on the cream card.
PALETTE = ["#E4572E", "#1C7CB8", "#2A9D5B", "#8E44AD", "#E08E0B", "#C2185B", "#00897B"]

#: Diyanet's (and the printed Elif-Ba's) heavy letters, marked with a red rim.
HEAVY = set("خصضطظغق")

#: Chapter marks for the description, one per row of seven (YouTube wants >= 10 s each).
CHAPTER_EVERY = 7

#: The colour of a hareke on the card: one fixed colour, so the eye goes to the new mark.
MARK_COLOR = "#E4572E"
#: Lessons that teach a mark draw the letters themselves in one calm ink.
INK = "#2E3A59"

#: One colour per hareke, the same in every letter, so each mark is recognised by colour too.
HARAKAT_COLORS = {"fetha": "#E4572E", "kesra": "#1C7CB8", "damme": "#2A9D5B"}
TRIPLE_GAP = 1.3    # after each of the three sounds: time to say it back
WORD_GAP = 0.9      # after each spelled word (they are long already: 3.5-5 s)


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


def base_letter(text: str) -> str:
    """The letter without its harakat. A tatweel stays: Diyanet writes ha as هـ."""
    return "".join(ch for ch in text if not ("ً" <= ch <= "ْ" or ch in "ٰ۪ٖ"))


def harfler_items() -> List[Dict]:
    """Lesson 1: every letter, by name, in alphabet order."""
    return [{"text": l, "letter": l, "sound": name_sound(l)} for l in JE]


def fetha_items() -> List[Dict]:
    """Lesson 2: every letter with fetha, in Diyanet's order — thin letters, then thick.

    Diyanet's web course writes ya-with-fetha as dotless ىَ; the printed 2024
    Elif-Ba (the owner's reference) writes يَ, the normal spelling with a vowel.
    The card follows the book; the recording is the same.
    """
    folder = AUDIO / "elifba" / "fetha" / "fetha"
    items = []
    for group in unit_fetha()[0]["groups"]:
        for row in group["rows"]:
            for tile in row["tiles"]:
                text = "يَ" if tile["arabic"] == "ىَ" else tile["arabic"]
                items.append({"text": text, "letter": base_letter(tile["arabic"]) or tile["arabic"],
                              "sound": folder / f"{tile['sound']}.mp3", "group": group["title"]})
    return items


def _recordings(unit) -> Dict[str, tuple]:
    """letter -> (text as Diyanet writes it, recording) for one of Diyanet's hareke units."""
    les = unit()[0]
    folder = AUDIO / "elifba" / les["chapter"] / les["lesson"]
    out = {}
    for group in les["groups"]:
        for row in group["rows"]:
            for tile in row["tiles"]:
                out[base_letter(tile["arabic"])] = (tile["arabic"], folder / f"{tile['sound']}.mp3")
    return out


def harekeler_items() -> List[Dict]:
    """Lesson 3 of the printed Elif-Ba: every letter, in alphabet order, with fetha,
    kesra and damme together — read right to left, اَ اِ اُ. Each recording comes
    from Diyanet's own unit for that hareke. Ya is written dotted (يَ يِ يُ) as in
    the book."""
    units = {"fetha": _recordings(unit_fetha), "kesra": _recordings(unit_kesra), "damme": _recordings(unit_damme)}
    items = []
    for letter in JE:
        forms = []
        for hareke, rec in units.items():
            text, sound = rec[letter]
            if letter == "ى":
                text = "ي" + text[1:]
            forms.append({"text": text, "sound": sound, "hareke": hareke})
        items.append({"text": letter, "letter": letter, "forms": forms})
    return items


def kelime_items(part: int) -> List[Dict]:
    """Lesson 2: each letter on its own, then in three words — at the start, in the
    middle, at the end — with the letter picked out in each word.

    The words and their recordings are Diyanet's ("Harfler ve Şekilleri"); each
    recording spells its word letter by letter. The letter on its own is named
    with the same recording as lesson 1 (Diyanet's own recording of alif here
    says "hemze"). Diyanet's extra hamza rows and repeated peltek rows are left
    out: the book's table is the 28 letters. ``part`` 0 is all of them; 1-3 split
    them ا-ر, ز-غ, ف-ى for videos under five minutes (not used: the owner chose one)."""
    sek = unit_harfler()[3]
    folder = AUDIO / "elifba" / "harfler" / "sekilleri"
    rows: Dict[str, list] = {}
    for group in sek["groups"]:
        for row in group["rows"]:
            first = row["tiles"][0]["arabic"].strip("{}")
            if first == "ء" or first in rows:
                continue
            rows[first] = row["tiles"]
    letters = {0: JE, 1: JE[:10], 2: JE[10:19], 3: JE[19:]}[part]
    return [{"text": l, "letter": l, "sound": name_sound(l),
             "words": [{"text": t["arabic"], "sound": folder / f"{t['sound']}.mp3"} for t in rows[l][1:]]}
            for l in letters]


#: number, items, subtitle on the title card, whether the cards show a coloured hareke
LESSONS = {
    "harfler": (1, harfler_items, "Harfler", False),
    "kelime": (2, lambda: kelime_items(0), "Harfler ve Kelimeler", False),
    "harekeler": (3, harekeler_items, "Harekeler", True),
    "fetha": (2, fetha_items, "Fetha", True),
}


def words_segment(s: Script, item: Dict, number: int) -> None:
    """A letter, named; then the three words it appears in, each spelled by Diyanet."""
    letter = item["letter"]
    scene = s.open_scene("words", letter=letter, words=[w["text"] for w in item["words"]], color=INK,
                         rim=color_of(letter), heavy=letter in HEAVY, number=number)
    s.wait(APPEAR)
    s.play(item["sound"], target="iso")
    s.wait(1.0)
    for k, w in enumerate(item["words"]):
        s.play(w["sound"], target=f"word:{k}")
        s.wait(WORD_GAP)
    s.wait(0.3)
    s.close_scene(scene)


def triple_segment(s: Script, item: Dict, number: int) -> None:
    """One letter with its three harakat: each said in turn, right to left, a pause after each."""
    letter = item["letter"]
    forms = [{"text": f["text"], "mark": HARAKAT_COLORS[f["hareke"]]} for f in item["forms"]]
    scene = s.open_scene("triple", forms=forms, color=INK, rim=color_of(letter),
                         heavy=letter in HEAVY, number=number)
    s.wait(APPEAR)
    for k, f in enumerate(item["forms"]):
        s.play(f["sound"], target=f"form:{k}")
        s.wait(TRIPLE_GAP)
    s.wait(0.4)
    s.close_scene(scene)


def letter_segment(s: Script, item: Dict, number: int, marked: bool) -> None:
    letter = item["letter"]
    scene = s.open_scene("letter", text=item["text"], color=INK if marked else color_of(letter),
                         rim=color_of(letter), mark=MARK_COLOR if marked else None,
                         heavy=letter in HEAVY, number=number)
    s.wait(APPEAR)
    for _ in range(HEARINGS):
        s.play(item["sound"], target="letter")
        s.wait(PAUSE)
    s.close_scene(scene)


def episode(lesson: str = "harfler") -> Dict:
    number, make, subtitle, marked = LESSONS[lesson]
    items = make()
    s = Script()

    scene = s.open_scene("besmele")
    s.chapters.append({"t": 0.0, "title": "Besmele"})     # YouTube wants the first chapter at 0:00
    s.wait(0.6)
    s.play(BESMELE, target="besmele", kind="quran")
    s.wait(1.0)
    s.close_scene(scene)

    scene = s.open_scene("title", subtitle=subtitle)
    s.wait(3.0)
    s.close_scene(scene)

    for i, item in enumerate(items):
        if i % CHAPTER_EVERY == 0:
            last = items[min(i + CHAPTER_EVERY, len(items)) - 1]
            s.chapters.append({"t": round(s.t, 3), "title": f"{item['text']} – {last['text']}"})
        if "words" in item:
            words_segment(s, item, i)
        elif "forms" in item:
            triple_segment(s, item, i)
        else:
            letter_segment(s, item, i, marked)

    # the closing five seconds stay inside the last chapter: YouTube wants 10 s or more each
    scene = s.open_scene("outro", texts=[it["text"] for it in items],
                         colors=[INK if marked else color_of(it["letter"]) for it in items],
                         rims=[color_of(it["letter"]) for it in items],
                         mark=MARK_COLOR if marked and "forms" not in items[0] else None,
                         triples=[[{"text": f["text"], "mark": HARAKAT_COLORS[f["hareke"]]} for f in it["forms"]]
                                  for it in items[:4]] if "forms" in items[0] else None)
    s.wait(5.0)
    s.close_scene(scene)

    # a thumbnail name short enough for one line, and (for the words lesson) an example row
    thumb_heading = subtitle.replace("Harfler ve Kelimeler", "Kelimeler")
    if "words" in items[0]:
        ex = items[1] if len(items) > 1 else items[0]          # ب, ز or ق: a clear, dotted letter
        scene["example"] = {"letter": ex["letter"], "words": [w["text"] for w in ex["words"]],
                            "rim": color_of(ex["letter"]), "color": INK}
    return {"lesson": lesson, "number": number, "subtitle": subtitle, "thumb_heading": thumb_heading,
            "max_seconds": LESSON_MAX_SECONDS.get(lesson, MAX_SECONDS),
            "items": [it["text"] for it in items],
            "duration": round(s.t, 3), "scenes": s.scenes, "audio": s.audio, "beats": s.beats,
            "chapters": s.chapters}


if __name__ == "__main__":
    import sys

    sys.stdout.reconfigure(encoding="utf-8")
    ep = episode(sys.argv[1] if len(sys.argv) > 1 else "harfler")
    print(json.dumps({k: ep[k] for k in ("lesson", "duration", "chapters")}, ensure_ascii=False))

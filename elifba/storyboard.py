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
4. reading words of three fetha syllables; 5. reading with mixed harakat —
syllable rows, then the few words Diyanet has that use only short harakat.
1b repeats the letters out of order, as a quiz (the owner asked for it on
2026-11-21): each letter waits in silence before its name is said, so a child
can name it first. There is still no comparison quiz between letters.

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

APPEAR = 1.3        # the camera glides to the next tulip, it blooms, the letter rises
PAUSE = 2.2         # silence after each hearing: time to say it back (the last one ends
                    # with the letter flying up into the bunting)
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
GUESS = 3.0         # ders 1b: the letter waits in silence, for the child to name it first
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


#: Per spelled-word recording, when each letter name is said (see spelling.py).
SPELLING_FILE = ROOT / "data" / "spelling.json"
SPELLING: Dict[str, list] = json.loads(SPELLING_FILE.read_text(encoding="utf-8")) if SPELLING_FILE.exists() else {}


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


#: Words from Diyanet's fetha Uygulama left out of the reading lesson: their meanings
#: do not belong in a lesson for small children, even before the child understands them.
UNSUITABLE_WORDS = {"لَعَنَ", "كَفَرَ", "سَقَرَ", "غَضَبَ", "ضَرَبَ", "مَكَرَ", "جَرَمَ", "بَطَشَ", "نَكَصَ", "غَلَظَ",
                    "زَعَمَ", "رَفَثَ"}
SYLLABLE_GAP = 0.3   # between the syllables of a word as they are said one by one
JOIN = 1.2           # the syllables slide together into the joined word


def okuma_items() -> List[Dict]:
    """Lesson 4: reading words. The printed Elif-Ba's exercise page after the
    harakat: short words built from letters with harakat. Only words of exactly
    three fetha syllables are used — nothing not yet taught (no long vowels,
    tenvin or sukun). Each syllable is said with Diyanet's fetha recording for
    that letter, then the word with Diyanet's recording of the whole word."""
    import re

    fetha = _recordings(unit_fetha)
    uyg = unit_fetha()[1]
    folder = AUDIO / "elifba" / uyg["chapter"] / uyg["lesson"]
    items = []
    for row in uyg["groups"][0]["rows"]:
        for tile in row["tiles"]:
            word = tile["arabic"]
            if " " in word or word in UNSUITABLE_WORDS:
                continue
            clusters = re.findall(r"[ء-ي][ً-ْٰ]*", word)
            if len(clusters) != 3 or any(c[1:] != "َ" for c in clusters):
                continue
            letters = ["ا" if c[0] in "اأ" else c[0] for c in clusters]
            if not all(l in fetha for l in letters):
                continue
            items.append({"text": word, "letter": letters[0], "sound": folder / f"{tile['sound']}.mp3",
                          "syllables": [{"text": c, "sound": fetha[l][1]} for c, l in zip(clusters, letters)]})
    return items


def karisik_items() -> List[Dict]:
    """Ders 1b: every letter again, out of alphabet order. A fixed shuffle, so a
    re-render is the same video; no letter sits next to its alphabet neighbour,
    since a child who knows the order would otherwise just continue it."""
    import random

    letters = list(JE)
    rng = random.Random(1)
    while True:
        rng.shuffle(letters)
        if all(abs(JE.index(a) - JE.index(b)) > 1 for a, b in zip(letters, letters[1:])):
            break
    return [{"text": l, "letter": l, "sound": name_sound(l), "quiz": True} for l in letters]


#: Words in Diyanet's kesra and damme pages that a child should not chant, as in ders 4.
UNSUITABLE_WORDS_5 = {"فَفَزِعَ"}       # "and was terrified"


def harekeli_items() -> List[Dict]:
    """Ders 5: reading with all three harakat. First rows of three syllables, each
    letter of the alphabet in turn and every row holding a fetha, a kesra and a
    damme in a different order, read right to left; then the words Diyanet has
    whose syllables use only these three marks, with at least one kesra or damme
    (no long vowels or tenvin, which come later). Five words: a sixth, فَفَزِعَ,
    means "was terrified" and is left out."""
    import itertools
    import re

    units = {"fetha": _recordings(unit_fetha), "kesra": _recordings(unit_kesra), "damme": _recordings(unit_damme)}
    by_mark = {"َ": "fetha", "ِ": "kesra", "ُ": "damme"}
    orders = list(itertools.permutations(["fetha", "kesra", "damme"]))
    letters = list(JE) + list(JE[:2])                  # 28 letters: the last row wraps to ا ب
    items = []
    for r in range(0, 30, 3):
        row = letters[r:r + 3]
        forms = []
        for letter, hareke in zip(row, orders[(r // 3) % len(orders)]):
            text, sound = units[hareke][letter]
            if letter == "ى":
                text = "ي" + text[1:]
            forms.append({"text": text, "sound": sound, "hareke": hareke})
        items.append({"text": " ".join(f["text"] for f in forms), "letter": row[0], "forms": forms})
    items[0]["chapter"] = "Heceler"

    words = []
    for unit in (unit_kesra, unit_damme):
        uyg = unit()[1]
        folder = AUDIO / "elifba" / uyg["chapter"] / uyg["lesson"]
        for group in uyg["groups"]:
            for row in group["rows"]:
                for tile in row["tiles"]:
                    word = tile["arabic"]
                    clusters = re.findall(r"[ء-ي][ً-ْٰ۪]*", word)
                    if " " in word or "".join(clusters) != word or word in UNSUITABLE_WORDS_5:
                        continue
                    if not all(len(c) == 2 and c[1] in by_mark for c in clusters):
                        continue
                    if not any(by_mark[c[1]] != "fetha" for c in clusters) or not 3 <= len(clusters) <= 4:
                        continue
                    syllables = []
                    for c in clusters:
                        hareke, letter = by_mark[c[1]], "ا" if c[0] in "اأ" else c[0]
                        syllables.append({"text": c, "sound": units[hareke][letter][1], "hareke": hareke})
                    marks = {h: HARAKAT_COLORS[h] for h in HARAKAT_COLORS if any(x["hareke"] == h for x in syllables)}
                    words.append({"text": word, "letter": clusters[0][0], "sound": folder / f"{tile['sound']}.mp3",
                                  "syllables": syllables, "mark": marks,
                                  # all three harakat, none above a tall stem: in وَطُبِعَ
                                  # the damme over ط reaches into the thumbnail's title
                                  "thumb": word == "وَوُضِعَ"})
    words.sort(key=lambda w: len(w["syllables"]))             # three syllables first, then four
    words[0]["chapter"] = "Kelimeler"
    return items + words


#: number, items, subtitle on the title card, whether the cards show a coloured hareke
LESSONS = {
    "harfler": (1, harfler_items, "Harfler", False),
    "kelime": (2, lambda: kelime_items(0), "Harfler ve Kelimeler", False),
    "harekeler": (3, harekeler_items, "Harekeler", True),
    "okuma": (4, okuma_items, "Kelime Okuyalım", True),
    "karisik": ("1b", karisik_items, "Bu Harf Hangisi?", False),
    "harekeli": (5, harekeli_items, "Harekelerle Okuyalım", True),
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
    spell = []
    for k, w in enumerate(item["words"]):
        start = s.play(w["sound"], target=f"word:{k}")
        # when each letter of the word is said, so the screen can light it as it is read
        spell.append([[round(start + a, 3), round(start + b, 3)] for a, b in SPELLING[w["sound"].name]])
        s.wait(WORD_GAP)
    scene["spell"] = spell
    s.wait(0.3)
    s.close_scene(scene)


def build_length(item: Dict) -> float:
    """How long build_segment will take for ``item``."""
    syl = sum(duration(x["sound"]) + SYLLABLE_GAP for x in item["syllables"])
    return APPEAR + syl + JOIN + HEARINGS * (duration(item["sound"]) + PAUSE)


def fit_items(items: List[Dict], budget: float) -> List[Dict]:
    """The longest prefix of ``items`` whose segments fit in ``budget`` seconds."""
    out, used = [], 0.0
    for it in items:
        need = build_length(it) if "syllables" in it else 0.0
        if used + need > budget:
            break
        out.append(it)
        used += need
    return out


def build_segment(s: Script, item: Dict, number: int) -> None:
    """Three syllables, each said as it lights up; they slide together into the
    word; the word is read, a pause, read again."""
    scene = s.open_scene("build", word=item["text"], syllables=[x["text"] for x in item["syllables"]],
                         color=INK, rim=color_of(item["letter"]), mark=item.get("mark", HARAKAT_COLORS["fetha"]),
                         number=number)
    s.wait(APPEAR)
    for k, syl in enumerate(item["syllables"]):
        s.play(syl["sound"], target=f"syl:{k}")
        s.wait(SYLLABLE_GAP)
    join = s.t
    s.beats.append({"t": round(join, 3), "dur": JOIN, "target": "join"})
    s.wait(JOIN)
    for _ in range(HEARINGS):
        s.play(item["sound"], target="word")
        s.wait(PAUSE)
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
    if item.get("quiz"):
        s.wait(GUESS)                  # the child names it first
        s.play(item["sound"], target="letter")
        s.wait(PAUSE)
    else:
        for _ in range(HEARINGS):
            s.play(item["sound"], target="letter")
            s.wait(PAUSE)
    s.close_scene(scene)


def _thumb_word(items: List[Dict]) -> Optional[Dict]:
    """The word the thumbnail builds from its syllables: ders 4's fourth (بَعَثَ),
    or the first that shows all three harakat."""
    words = [it for it in items if "syllables" in it]
    if not words:
        return None
    pick = next((w for w in words if w.get("thumb")), words[min(3, len(words) - 1)])
    return {"word": pick["text"], "syllables": [x["text"] for x in pick["syllables"]], "mark": pick.get("mark")}


def episode(lesson: str = "harfler") -> Dict:
    number, make, subtitle, marked = LESSONS[lesson]
    items = make()
    if items and "syllables" in items[0]:
        # as many words as fit: besmele (~8.6 s), title (3.4 s) and outro (6.5 s) take the rest
        items = fit_items(items, LESSON_MAX_SECONDS.get(lesson, MAX_SECONDS) - 8.6 - 3.4 - 6.5 - 1.0)
    s = Script()

    scene = s.open_scene("besmele")
    s.chapters.append({"t": 0.0, "title": "Besmele"})     # YouTube wants the first chapter at 0:00
    s.wait(0.6)
    s.play(BESMELE, target="besmele", kind="quran")
    s.wait(1.0)
    s.close_scene(scene)

    scene = s.open_scene("title", subtitle=subtitle)
    s.wait(3.4)
    s.close_scene(scene)

    named = any("chapter" in it for it in items)
    for i, item in enumerate(items):
        if named:
            if "chapter" in item:
                s.chapters.append({"t": round(s.t, 3), "title": item["chapter"]})
        elif i % CHAPTER_EVERY == 0:
            last = items[min(i + CHAPTER_EVERY, len(items)) - 1]
            s.chapters.append({"t": round(s.t, 3), "title": f"{item['text']} – {last['text']}"})
        if "syllables" in item:
            build_segment(s, item, i)
        elif "words" in item:
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
                                  for it in items[:4]] if "forms" in items[0] else None,
                         build=_thumb_word(items))
    s.wait(6.5)                # night falls, lanterns rise, "Aferin!"
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

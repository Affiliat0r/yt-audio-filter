"""When each letter is said, inside Diyanet's spelled-word recordings.

In the letters-in-words lesson Diyanet reads each word letter by letter ("Bâ…
Ayn… Se" for بعث). The screen has to light up the letter being said, or a child
cannot follow what is read. The recordings carry no timing, so it is measured
here: the number of letters is known from the word, and the letter names are
separated by short dips in loudness, so the N-1 deepest dips between the first
and last sound are the boundaries.

    python -m elifba.spelling          # (re)writes elifba/data/spelling.json

The result is committed: it is small, and renders should not depend on
re-measuring. ``check()`` reports words whose split looks doubtful (a segment
far shorter than a letter name can be), so they can be listened to.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "data" / "spelling.json"
RATE = 16000
HOP = 0.01          # envelope resolution, seconds
MIN_LETTER = 0.22   # no letter name is said faster than this

ARABIC_LETTER = lambda ch: "ء" <= ch <= "ي" or ch in "ٱی"


def letters_of(word: str) -> List[str]:
    """The letters a spelled word is read as, in reading order (braces and spaces dropped)."""
    return [ch for ch in word.replace("{", "").replace("}", "") if ARABIC_LETTER(ch)]


def envelope(path: Path) -> np.ndarray:
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-ac", "1", "-ar", str(RATE), "-f", "f32le", "-"],
                         capture_output=True, check=True).stdout
    x = np.frombuffer(raw, dtype=np.float32)
    hop = int(RATE * HOP)
    n = len(x) // hop
    rms = np.sqrt(np.mean(x[: n * hop].reshape(n, hop) ** 2, axis=1) + 1e-12)
    k = 7                                                   # ~70 ms smoothing
    return np.convolve(rms, np.ones(k) / k, mode="same")


def split(path: Path, n: int) -> List[Tuple[float, float]]:
    """(start, end) in seconds of each of ``n`` letter names in the recording."""
    env = envelope(path)
    on = env > env.max() * 0.08
    idx = np.flatnonzero(on)
    first, last = int(idx[0]), int(idx[-1]) + 1
    if n == 1:
        return [(first * HOP, last * HOP)]
    # Letter names are separated by real silences; dips *inside* a name (the
    # closing consonant of "vav") are shallow and short. So the boundaries are the
    # n-1 longest quiet runs, cut at their middle. If a recording has fewer quiet
    # runs than that, the threshold is raised until it has enough.
    seg = env[first:last]
    chosen: List[int] = []
    for level in (0.03, 0.06, 0.1, 0.16, 0.24):
        quiet = seg < seg.max() * level
        runs, i = [], 0
        while i < len(seg):
            if quiet[i]:
                j = i
                while j < len(seg) and quiet[j]:
                    j += 1
                runs.append((j - i, (i + j) // 2))
                i = j
            else:
                i += 1
        # one silence can come out in fragments where the noise floor flickers: join
        # fragments under 0.1 s apart into a single run
        merged: List[List[int]] = []
        for length, mid in runs:
            a, b = mid - length // 2, mid + (length + 1) // 2
            if merged and a - merged[-1][1] < int(0.1 / HOP):
                merged[-1][1] = b
            else:
                merged.append([a, b])
        # a quiet run touching either end is the voice fading in or out, not a gap
        merged = [m for m in merged if m[0] > 0 and m[1] < len(seg)]
        runs = [(b - a, (a + b) // 2) for a, b in merged]
        runs = [r for r in runs if r[1] * HOP >= MIN_LETTER and (len(seg) - r[1]) * HOP >= MIN_LETTER]
        runs.sort(reverse=True)
        picked: List[int] = []
        for _, mid in runs:                                  # longest first, never two cuts in one name
            if all(abs(mid - p) * HOP >= 0.35 for p in picked):
                picked.append(mid)
            if len(picked) == n - 1:
                break
        if len(picked) == n - 1:
            chosen = picked
            break
    if len(chosen) < n - 1:
        # Not enough silences: a soft letter (the hum of "mim") can follow another
        # with no gap at all, only a step down in loudness. Keep the clear gaps and
        # add the steepest drops, away from them, until there are enough.
        chosen = _gaps_at_level(seg, 0.06)
        w = int(0.15 / HOP)
        drops = sorted(range(w, len(seg) - w), key=lambda i: seg[i + w // 2] - seg[i - w // 2])
        for i in drops:
            if len(chosen) >= n - 1:
                break
            if all(abs(i - p) * HOP >= 0.35 for p in chosen) and MIN_LETTER <= i * HOP <= (len(seg) * HOP - MIN_LETTER):
                chosen.append(i)
        if len(chosen) < n - 1:                             # still nothing: share the time out evenly
            chosen = [round(len(seg) * k / n) for k in range(1, n)]
    cuts = [first] + sorted(first + i for i in chosen) + [last]
    return [(round(a * HOP, 3), round(b * HOP, 3)) for a, b in zip(cuts, cuts[1:])]


def _gaps_at_level(seg: np.ndarray, level: float) -> List[int]:
    """Middles of the quiet runs (not touching either end) at one loudness level."""
    quiet = seg < seg.max() * level
    out, i = [], 0
    while i < len(seg):
        if quiet[i]:
            j = i
            while j < len(seg) and quiet[j]:
                j += 1
            if i > 0 and j < len(seg) and j - i >= int(0.12 / HOP):
                out.append((i + j) // 2)
            i = j
        else:
            i += 1
    return out


# ---------------------------------------------------------------- template alignment
# Loudness alone fails where two letter names run together (the hum of "mim"
# straight into "ra"). Diyanet's own recording of each letter's name is a
# template: laid end to end in the word's order, they are aligned to the spelled
# word by dynamic time warping on MFCCs, and the joins land on the boundaries.

AUDIO = ROOT / "audio" / "elifba" / "harfler"


def template_path(letter: str) -> Path:
    from elifba.curriculum import JE

    if letter == "ا":                 # inside these words Diyanet reads alif as "hemze"
        return AUDIO / "sekilleri" / "btn_1.mp3"
    letter = {"ة": "ت", "ي": "ى", "ه": "هـ", "ک": "ك"}.get(letter, letter)
    return AUDIO / "harfler" / f"btn_{JE.index(letter) + 1}.mp3"


def _mfcc(path: Path, trim: bool) -> np.ndarray:
    import librosa

    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-ac", "1", "-ar", str(RATE), "-f", "f32le", "-"],
                         capture_output=True, check=True).stdout
    y = np.frombuffer(raw, dtype=np.float32)
    if trim:
        y, _ = librosa.effects.trim(y, top_db=32)
    m = librosa.feature.mfcc(y=y, sr=RATE, n_mfcc=20, hop_length=int(RATE * HOP), n_fft=512)[1:]
    m = (m - m.mean(axis=1, keepdims=True)) / (m.std(axis=1, keepdims=True) + 1e-6)
    return m


def align(path: Path, letters: List[str]) -> List[Tuple[float, float]]:
    import librosa

    env = envelope(path)
    on = np.flatnonzero(env > env.max() * 0.08)
    first, last = int(on[0]), int(on[-1]) + 1
    target = _mfcc(path, trim=False)[:, first:last]
    temps = [_mfcc(template_path(l), trim=True) for l in letters]
    joins = np.cumsum([t.shape[1] for t in temps])[:-1]
    _, wp = librosa.sequence.dtw(X=np.concatenate(temps, axis=1), Y=target, metric="cosine")
    wp = wp[::-1]
    cuts = []
    for j in joins:
        ys = [y for x, y in wp if x == j]
        cut = int(np.median(ys)) if ys else int(target.shape[1] * len(cuts) / len(letters))
        # settle on the quietest moment within 0.2 s: a boundary sits in a dip
        lo, hi = max(1, cut - 20), min(len(env[first:last]) - 1, cut + 20)
        cut = lo + int(np.argmin(env[first + lo:first + hi]))
        cuts.append(first + cut)
    cuts = [first] + cuts + [last]
    return [(round(a * HOP, 3), round(b * HOP, 3)) for a, b in zip(cuts, cuts[1:])]


def build() -> Dict[str, List[Tuple[float, float]]]:
    from elifba.storyboard import kelime_items

    out: Dict[str, List[Tuple[float, float]]] = {}
    for item in kelime_items(0):
        for w in item["words"]:
            out[w["sound"].name] = align(w["sound"], letters_of(w["text"]))
    return out


def check(data: Dict[str, list]) -> List[str]:
    return [k for k, segs in data.items() if any(b - a < MIN_LETTER for a, b in segs)]


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    data = build()
    OUT.write_text(json.dumps(data, indent=1), encoding="utf-8")
    print(f"{len(data)} words timed -> {OUT}")
    doubtful = check(data)
    print("doubtful:", doubtful or "none")

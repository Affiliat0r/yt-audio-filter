"""The short Turkish cue lines a toddler video needs, spoken by a TTS voice.

Every *Arabic* sound in these videos is a human recording from Diyanet; only
these Turkish prompts ("Dinle!", "Şimdi sen söyle!" ...) are synthesised. Arabic
TTS was tested and rejected: it mangles isolated letters and harakat.

    python -m elifba.voice          # generate any cue not yet on disk

Network note: run from a normal shell; the speech service is not reachable from
the sandboxed one.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

TTS_DIR = Path(__file__).resolve().parent / "audio" / "tts"
VOICE = "tr-TR-EmelNeural"
RATE = "-12%"   # unhurried, for small children
PITCH = "+6Hz"  # a touch brighter

CUES = {
    "intro": "Haydi, harfleri öğrenelim!",
    "listen": "Dinle!",
    "say": "Şimdi sen söyle!",
    "say_again": "Bir daha söyle!",
    "praise_1": "Aferin!",
    "praise_2": "Çok güzel!",
    "praise_3": "Harika!",
    "praise_4": "Maşallah!",
    "which": "Hangisi?",
    "this_one": "İşte bu!",
    "recap": "Hepsini birlikte söyleyelim!",
    "outro": "Aferin sana! Yeni harfler öğrendin.",
    "goodbye": "Bir sonraki derste görüşmek üzere!",
    "dots_below_1": "Altında bir nokta!",
    "dots_above_2": "Üstünde iki nokta!",
    "dots_above_3": "Üstünde üç nokta!",
    "dots_mid_1": "Ortasında bir nokta!",
    "dots_above_1": "Üstünde bir nokta!",
    "dots_none": "Hiç noktası yok!",
    "alif_tall": "Dimdik duruyor!",
}


def cue_path(key: str) -> Path:
    return TTS_DIR / f"{key}.mp3"


async def _make(key: str, text: str) -> None:
    import edge_tts  # only needed when generating

    path = cue_path(key)
    for attempt in range(5):
        try:
            await edge_tts.Communicate(text, VOICE, rate=RATE, pitch=PITCH).save(str(path))
            if path.stat().st_size > 0:
                print("made", key)
                return
        except Exception as exc:  # the service drops connections now and then
            print("retry", key, attempt, type(exc).__name__)
        await asyncio.sleep(2 + attempt)
    raise RuntimeError(f"could not synthesise cue {key!r}")


async def make_all() -> None:
    TTS_DIR.mkdir(parents=True, exist_ok=True)
    for key, text in CUES.items():
        path = cue_path(key)
        if not path.exists() or path.stat().st_size == 0:
            await _make(key, text)


if __name__ == "__main__":
    asyncio.run(make_all())

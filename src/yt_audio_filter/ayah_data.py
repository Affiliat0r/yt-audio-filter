"""Ayah-level Quran data: per-surah ayah counts and EveryAyah URL builder.

Backs ayah-range repetition (M2) and gap-prompt mode (M3) from the
Quran-teacher wishlist. Two responsibilities:

1. Authoritative per-surah ayah counts (canonical Hafs numbering, total
   6236 ayat). Loaded from ``data/ayah_counts.json``.
2. Build a per-ayah MP3 URL on EveryAyah.com::

       https://everyayah.com/data/<reciter_slug>/<surah:03d><ayah:03d>.mp3

   e.g. ``001001.mp3`` for Al-Fatiha ayah 1, ``036083.mp3`` for the last
   ayah of Yaseen.

The EveryAyah folder slug is **not** the same as the quranicaudio.com slug
used by :mod:`quran_audio_source`. ``EVERYAYAH_RECITERS`` maps a stable
internal short slug (matching ``data/reciters.json`` where possible) to
both the EveryAyah folder name and its quranicaudio counterpart so Phase 2
can wire one selection across both audio sources.

Folder slugs were spot-checked against the live directory listing at
https://everyayah.com/data/ on 2026-04-19. EveryAyah hosts each reciter
at multiple bitrates (e.g. ``Alafasy_64kbps`` and ``Alafasy_128kbps``);
we pin the highest-quality complete folder for each reciter.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Dict

from .logger import get_logger

logger = get_logger()

_AYAH_COUNTS_PATH = Path(__file__).parent / "data" / "ayah_counts.json"

# Reciters confirmed available on everyayah.com whose quranicaudio.com
# slug appears in data/reciters.json. Phase 2's UI should disable
# ayah-mode for any reciter not present here.
#
# - ``everyayah_path``: folder slug under https://everyayah.com/data/
# - ``quranicaudio_slug``: matching slug in data/reciters.json
EVERYAYAH_RECITERS: Dict[str, Dict[str, str]] = {
    "alafasy": {
        "display_name": "Mishary Rashid Alafasy",
        "everyayah_path": "Alafasy_128kbps",
        "quranicaudio_slug": "alafasy",
    },
    "sudais": {
        "display_name": "Abdur-Rahman As-Sudais",
        "everyayah_path": "Abdurrahmaan_As-Sudais_192kbps",
        "quranicaudio_slug": "sudais",
    },
    "shuraim": {
        "display_name": "Saud Al-Shuraim",
        "everyayah_path": "Saood_ash-Shuraym_128kbps",
        "quranicaudio_slug": "shuraim",
    },
    "maher": {
        "display_name": "Maher Al-Muaiqly",
        "everyayah_path": "MaherAlMuaiqly128kbps",
        "quranicaudio_slug": "maher",
    },
    "ajmi": {
        "display_name": "Ahmed Al-Ajmi",
        "everyayah_path": "Ahmed_ibn_Ali_al-Ajamy_128kbps_ketaballah.net",
        "quranicaudio_slug": "ajmi",
    },
    "abdulbasit": {
        "display_name": "Abdul Basit Abdul Samad (Murattal)",
        "everyayah_path": "Abdul_Basit_Murattal_192kbps",
        "quranicaudio_slug": "abdulbasit",
    },
    "minshawi": {
        "display_name": "Muhammad Siddiq Al-Minshawi (Murattal)",
        "everyayah_path": "Minshawy_Murattal_128kbps",
        "quranicaudio_slug": "minshawi",
    },
    "husary": {
        "display_name": "Mahmoud Khalil Al-Husary",
        "everyayah_path": "Husary_128kbps",
        "quranicaudio_slug": "husary",
    },
    "juhani": {
        "display_name": "Abdullah Awad Al-Juhani",
        "everyayah_path": "Abdullaah_3awwaad_Al-Juhaynee_128kbps",
        "quranicaudio_slug": "juhani",
    },
    "rifai": {
        "display_name": "Hani Ar-Rifai",
        "everyayah_path": "Hani_Rifai_192kbps",
        "quranicaudio_slug": "rifai",
    },
    "shatri": {
        "display_name": "Abu Bakr Al-Shatri",
        "everyayah_path": "Abu_Bakr_Ash-Shaatree_128kbps",
        "quranicaudio_slug": "shatri",
    },
    "qatami": {
        "display_name": "Nasser Al-Qatami",
        "everyayah_path": "Nasser_Alqatami_128kbps",
        "quranicaudio_slug": "qatami",
    },
    "dosari": {
        "display_name": "Yasser Ad-Dosari",
        "everyayah_path": "Yasser_Ad-Dussary_128kbps",
        "quranicaudio_slug": "dosari",
    },
    "fares": {
        "display_name": "Fares Abbad",
        "everyayah_path": "Fares_Abbad_64kbps",
        "quranicaudio_slug": "fares",
    },
    "basfar": {
        "display_name": "Abdullah Basfar",
        "everyayah_path": "Abdullah_Basfar_192kbps",
        "quranicaudio_slug": "basfar",
    },
    "alijaber": {
        "display_name": "Ali Jaber",
        "everyayah_path": "Ali_Jaber_64kbps",
        "quranicaudio_slug": "alijaber",
    },
}


# Reciters in data/reciters.json that have NO confirmed everyayah folder.
# Phase 2 UI must disable ayah-mode if the user picks one of these.
RECITERS_WITHOUT_EVERYAYAH: tuple[str, ...] = (
    "ghamdi",      # only Ghamadi_40kbps exists; low quality, may be incomplete
    "abkar",       # no folder found
    "luhaidan",    # no folder found
    "thubaity",    # no folder found
)


_EVERYAYAH_BASE = "https://everyayah.com/data"


@lru_cache(maxsize=1)
def _load_ayah_counts() -> Dict[int, int]:
    """Parse ``data/ayah_counts.json`` exactly once per process."""
    with _AYAH_COUNTS_PATH.open("r", encoding="utf-8") as fh:
        raw = json.load(fh)
    counts = raw.get("counts", {})
    out: Dict[int, int] = {}
    for k, v in counts.items():
        out[int(k)] = int(v)
    return out


def ayah_count(surah_number: int) -> int:
    """Return the number of ayat in ``surah_number`` (canonical Hafs).

    Args:
        surah_number: 1..114 inclusive.

    Returns:
        Ayah count, e.g. 7 for Al-Fatiha, 286 for Al-Baqarah.

    Raises:
        ValueError: If ``surah_number`` is out of range or not an int.
    """
    # Reject bool first (bool is a subclass of int in Python).
    if isinstance(surah_number, bool) or not isinstance(surah_number, int):
        raise ValueError(
            f"surah_number must be an int in 1..114, got {type(surah_number).__name__}"
        )
    if surah_number < 1 or surah_number > 114:
        raise ValueError(
            f"surah_number {surah_number} is out of range; must be 1..114"
        )
    counts = _load_ayah_counts()
    try:
        return counts[surah_number]
    except KeyError as exc:  # pragma: no cover - JSON is canonical
        raise ValueError(
            f"No ayah-count entry for surah {surah_number}; data file is incomplete"
        ) from exc


def everyayah_url(reciter_slug: str, surah_number: int, ayah_number: int) -> str:
    """Build an EveryAyah.com per-ayah MP3 URL.

    ``reciter_slug`` is the EveryAyah folder name (e.g. ``Alafasy_128kbps``),
    NOT the short internal slug. Callers usually go via
    ``EVERYAYAH_RECITERS[<short_slug>]['everyayah_path']``.

    Args:
        reciter_slug: EveryAyah folder slug.
        surah_number: 1..114 inclusive.
        ayah_number: 1..ayah_count(surah_number) inclusive.

    Returns:
        Fully-formed HTTPS URL ending in ``<sssaaa>.mp3`` (zero-padded).

    Raises:
        ValueError: On out-of-range surah/ayah or empty/non-string slug.
    """
    if not isinstance(reciter_slug, str) or not reciter_slug.strip():
        raise ValueError("reciter_slug must be a non-empty string")
    # ayah_count() validates the surah number for us.
    max_ayah = ayah_count(surah_number)
    if isinstance(ayah_number, bool) or not isinstance(ayah_number, int):
        raise ValueError(
            f"ayah_number must be an int, got {type(ayah_number).__name__}"
        )
    if ayah_number < 1 or ayah_number > max_ayah:
        raise ValueError(
            f"ayah_number {ayah_number} is out of range for surah {surah_number} "
            f"(must be 1..{max_ayah})"
        )
    filename = f"{surah_number:03d}{ayah_number:03d}.mp3"
    return f"{_EVERYAYAH_BASE}/{reciter_slug.strip()}/{filename}"

"""Resolve Arabic Mushaf-Uthmani text + translations for Quran ayat.

Two layers:

1. **Shipped JSON** at ``data/translations/`` — Arabic Uthmani (1.4 MB) and
   Saheeh International English (~0.9 MB), pre-fetched at build time from
   the Quran.com API v4. These are loaded lazily and cached in-process so
   bulk renders touch disk once and the network never.
2. **Optional third language** — fetched on demand from
   ``https://api.quran.com/api/v4/quran/translations/<id>`` and cached on
   disk under ``cache_dir/translation_<id>.json``. Subsequent calls in the
   same surah hit disk; subsequent processes hit the same disk cache.

Public API:

* :class:`AyahText` — frozen dataclass (surah, ayah, arabic, translation_en,
  translation_extra).
* :func:`get_ayah_text` — single ayah lookup.
* :func:`get_surah_texts` — all ayat in a surah, ordered by ayah number.

Sources documented:

* Arabic text: Quran.com API v4 ``/quran/verses/uthmani`` (Mushaf Uthmani
  script).
* English: Quran.com API v4 ``/quran/translations/20`` (resource_id 20 ==
  Saheeh International). ``<sup foot_note=...>`` markers are stripped at
  build time so the screen text is render-clean.
* Third language: any Quran.com translation resource id (e.g. 235 = Dutch
  Sofyan Siregar). Fetched lazily; not shipped.

If a Dutch JSON is shipped at ``data/translations/dutch.json`` with the
same shape as ``sahih_intl.json``, it is preferred over a network fetch
when ``extra_translation_id`` is the integer ``DUTCH_SHIPPED_ID``
(documented constant: 235). This keeps the offline path clean for users
who drop the file into the package.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional

from .exceptions import OverlayError
from .logger import get_logger

logger = get_logger()

_DATA_DIR = Path(__file__).parent / "data" / "translations"
_ARABIC_PATH = _DATA_DIR / "arabic_uthmani.json"
_SAHIH_PATH = _DATA_DIR / "sahih_intl.json"
_DUTCH_PATH = _DATA_DIR / "dutch.json"

_QURAN_API = "https://api.quran.com/api/v4"
_USER_AGENT = (
    "Mozilla/5.0 (compatible; yt-audio-filter/1.0; "
    "+https://github.com/Affiliat0r/yt-audio-filter)"
)

#: Quran.com translation resource id for Dutch (Sofyan Siregar). Documented
#: so users who ship ``data/translations/dutch.json`` know which id to pass
#: as ``extra_translation_id`` to use the shipped file.
DUTCH_SHIPPED_ID = 235

# Surah -> ayah_count, derived from chapters.json so we can validate input
# without a network round-trip. Hard-coded because it's a fixed property of
# the Mushaf and never changes.
_AYAH_COUNTS = (
    7, 286, 200, 176, 120, 165, 206, 75, 129, 109, 123, 111, 43, 52, 99,
    128, 111, 110, 98, 135, 112, 78, 118, 64, 77, 227, 93, 88, 69, 60,
    34, 30, 73, 54, 45, 83, 182, 88, 75, 85, 54, 53, 89, 59, 37, 35,
    38, 29, 18, 45, 60, 49, 62, 55, 78, 96, 29, 22, 24, 13, 14, 11,
    11, 18, 12, 12, 30, 52, 52, 44, 28, 28, 20, 56, 40, 31, 50, 40,
    46, 42, 29, 19, 36, 25, 22, 17, 19, 26, 30, 20, 15, 21, 11, 8,
    8, 19, 5, 8, 8, 11, 11, 8, 3, 9, 5, 4, 7, 3, 6, 3,
    5, 4, 5, 6,
)


@dataclass(frozen=True)
class AyahText:
    """Per-ayah text bundle.

    ``translation_extra`` is ``None`` when no third language was requested
    or when the requested id could not be loaded.
    """

    surah: int
    ayah: int
    arabic: str
    translation_en: str
    translation_extra: Optional[str] = None


def _validate_surah_ayah(surah: int, ayah: int) -> None:
    if not isinstance(surah, int) or isinstance(surah, bool):
        raise OverlayError(f"surah must be an int, got {type(surah).__name__}")
    if not isinstance(ayah, int) or isinstance(ayah, bool):
        raise OverlayError(f"ayah must be an int, got {type(ayah).__name__}")
    if surah < 1 or surah > 114:
        raise OverlayError(f"surah {surah} out of range; must be 1..114")
    max_ayah = _AYAH_COUNTS[surah - 1]
    if ayah < 1 or ayah > max_ayah:
        raise OverlayError(
            f"ayah {ayah} out of range for surah {surah}; must be 1..{max_ayah}"
        )


def get_ayah_count(surah: int) -> int:
    """Return the number of ayat in ``surah`` (1..114)."""
    if not isinstance(surah, int) or isinstance(surah, bool):
        raise OverlayError(f"surah must be an int, got {type(surah).__name__}")
    if surah < 1 or surah > 114:
        raise OverlayError(f"surah {surah} out of range; must be 1..114")
    return _AYAH_COUNTS[surah - 1]


def _verse_key(surah: int, ayah: int) -> str:
    return f"{surah}:{ayah}"


@lru_cache(maxsize=1)
def _load_arabic() -> Dict[str, str]:
    """Load the shipped Arabic Uthmani text once per process."""
    if not _ARABIC_PATH.exists():
        raise OverlayError(
            f"Shipped Arabic text missing: {_ARABIC_PATH}",
            details="Re-install the package; data/translations/arabic_uthmani.json "
            "is required for Quran text rendering.",
        )
    with _ARABIC_PATH.open("r", encoding="utf-8") as fh:
        return json.load(fh)["verses"]


@lru_cache(maxsize=1)
def _load_sahih() -> Dict[str, str]:
    """Load the shipped Saheeh International translation once per process."""
    if not _SAHIH_PATH.exists():
        raise OverlayError(
            f"Shipped English translation missing: {_SAHIH_PATH}",
            details="Re-install the package; data/translations/sahih_intl.json "
            "is required for Quran text rendering.",
        )
    with _SAHIH_PATH.open("r", encoding="utf-8") as fh:
        return json.load(fh)["verses"]


@lru_cache(maxsize=8)
def _load_extra_translation(translation_id: int, cache_dir_str: str) -> Dict[str, str]:
    """Load a third-language translation, preferring shipped JSON, then disk
    cache, then network.

    Caches by (translation_id, cache_dir) to keep tests isolated.
    """
    # 1. Shipped Dutch fast path.
    if translation_id == DUTCH_SHIPPED_ID and _DUTCH_PATH.exists():
        logger.debug("Loading shipped Dutch translation from %s", _DUTCH_PATH)
        with _DUTCH_PATH.open("r", encoding="utf-8") as fh:
            return json.load(fh)["verses"]

    cache_dir = Path(cache_dir_str)
    cache_dir.mkdir(parents=True, exist_ok=True)
    cached = cache_dir / f"translation_{translation_id}.json"
    if cached.exists() and cached.stat().st_size > 0:
        logger.debug("Loading translation %d from disk cache: %s", translation_id, cached)
        with cached.open("r", encoding="utf-8") as fh:
            return json.load(fh)["verses"]

    url = f"{_QURAN_API}/quran/translations/{translation_id}"
    logger.info("Fetching translation %d from Quran.com API: %s", translation_id, url)
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=60) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError) as exc:
        raise OverlayError(
            f"Failed to fetch translation {translation_id} from {url}",
            details=str(exc),
        ) from exc

    raw = payload.get("translations") or []
    # Quran.com returns translations as an array in canonical verse order
    # (1:1, 1:2, ..., 114:6). Map each entry to its verse_key by walking
    # the canonical (surah, ayah) sequence we already know.
    keys_in_order = _canonical_verse_keys()
    if len(raw) != len(keys_in_order):
        raise OverlayError(
            f"Translation {translation_id} returned {len(raw)} entries, "
            f"expected {len(keys_in_order)}",
        )

    import re

    sup_re = re.compile(r"<sup[^>]*>\d+</sup>")
    verses = {k: sup_re.sub("", t.get("text", "")) for k, t in zip(keys_in_order, raw)}

    cached.write_text(
        json.dumps({"translation_id": translation_id, "verses": verses}, ensure_ascii=False),
        encoding="utf-8",
    )
    logger.debug("Cached translation %d at %s", translation_id, cached)
    return verses


@lru_cache(maxsize=1)
def _canonical_verse_keys() -> List[str]:
    """Return all 6236 verse_keys in canonical order, cached for the process."""
    keys: List[str] = []
    for surah in range(1, 115):
        for ayah in range(1, _AYAH_COUNTS[surah - 1] + 1):
            keys.append(f"{surah}:{ayah}")
    return keys


def get_ayah_text(
    surah: int,
    ayah: int,
    cache_dir: Path,
    extra_translation_id: Optional[int] = None,
) -> AyahText:
    """Return Arabic + Saheeh English (+ optional third language) for one ayah.

    ``cache_dir`` is used only for the optional third-language disk cache.
    Arabic and English come from the shipped JSON files.
    """
    _validate_surah_ayah(surah, ayah)
    key = _verse_key(surah, ayah)
    arabic = _load_arabic().get(key)
    english = _load_sahih().get(key)
    if arabic is None or english is None:
        raise OverlayError(f"No text data for verse {key}")

    extra: Optional[str] = None
    if extra_translation_id is not None:
        try:
            verses = _load_extra_translation(int(extra_translation_id), str(Path(cache_dir)))
            extra = verses.get(key)
        except OverlayError:
            # Degrade gracefully: render without the third language rather
            # than failing the whole pipeline.
            logger.warning(
                "Could not load extra translation %d; rendering without it",
                extra_translation_id,
            )
            extra = None

    return AyahText(
        surah=surah,
        ayah=ayah,
        arabic=arabic,
        translation_en=english,
        translation_extra=extra,
    )


def get_surah_texts(
    surah: int,
    cache_dir: Path,
    extra_translation_id: Optional[int] = None,
) -> List[AyahText]:
    """Return all ayat in ``surah``, ordered by ayah number (1..N)."""
    if not isinstance(surah, int) or isinstance(surah, bool):
        raise OverlayError(f"surah must be an int, got {type(surah).__name__}")
    if surah < 1 or surah > 114:
        raise OverlayError(f"surah {surah} out of range; must be 1..114")
    out: List[AyahText] = []
    for ayah in range(1, _AYAH_COUNTS[surah - 1] + 1):
        out.append(get_ayah_text(surah, ayah, cache_dir, extra_translation_id))
    return out

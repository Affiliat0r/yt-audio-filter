"""Resolve and download per-surah Quran MP3 audio from a stable source.

Backs the Streamlit UI and the overlay pipeline with deterministic, cached MP3
downloads so the project no longer has to extract audio from YouTube for
recitations. The manifest at ``data/reciters.json`` pins 20 well-known
reciters whose 114-surah coverage on quranicaudio.com was verified manually
at implementation time.

Source: ``https://download.quranicaudio.com/quran/<relative_path><NNN>.mp3``
(zero-padded, 3 digits; ``<relative_path>`` varies per reciter).

A handful of reciters from the original target list were unavailable on
quranicaudio.com and had to be substituted:

* Salim Bahanan - not listed on quranicaudio.com (he is primarily mirrored
  on Indonesian sites); substituted with **Abdullah Awad al-Juhani**, a
  similarly well-regarded Saudi reciter with full coverage on the source.
* Khalid Al-Jalil - not in the quranicaudio dataset; substituted with
  **AbdulBari ath-Thubaity**.
* Muhammad Thaha Al-Junayd - a child reciter not carried by quranicaudio;
  substituted with **Mahmoud Khaleel Al-Husary**.
* "Saud bin Ibraheem Al-Shuraim" is the same person as "Saud Al-Shuraim"
  (duplicate entry in the target list); the slot was reassigned to
  **Abu Bakr al-Shatri**.

See the ``notes`` field of ``data/reciters.json`` for the canonical record.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Union

from .exceptions import OverlayError, YouTubeDownloadError
from .logger import get_logger

logger = get_logger()

_MANIFEST_PATH = Path(__file__).parent / "data" / "reciters.json"

_USER_AGENT = (
    "Mozilla/5.0 (compatible; yt-audio-filter/1.0; "
    "+https://github.com/Affiliat0r/yt-audio-filter)"
)


@dataclass(frozen=True)
class Reciter:
    """A single reciter in the supported manifest."""

    slug: str
    display_name: str
    sample_url: str
    url_pattern: str
    arabic_name: str = ""


def _load_manifest() -> dict:
    """Load and parse ``data/reciters.json`` from disk."""
    with _MANIFEST_PATH.open("r", encoding="utf-8") as fh:
        return json.load(fh)


@lru_cache(maxsize=1)
def _load_reciters() -> tuple[Reciter, ...]:
    """Load reciters from the manifest exactly once per process."""
    data = _load_manifest()
    out: list[Reciter] = []
    for entry in data.get("reciters", []):
        out.append(
            Reciter(
                slug=entry["slug"],
                display_name=entry["display_name"],
                sample_url=entry["sample_url"],
                url_pattern=entry["url_pattern"],
                arabic_name=entry.get("arabic_name", ""),
            )
        )
    return tuple(out)


def list_reciters() -> list[Reciter]:
    """Return all supported reciters, loaded once from ``data/reciters.json``."""
    return list(_load_reciters())


def get_reciter(slug: str) -> Reciter:
    """Look up a reciter by slug (case-insensitive). Raises ``OverlayError``."""
    if not isinstance(slug, str) or not slug.strip():
        raise OverlayError("Reciter slug must be a non-empty string")
    needle = slug.strip().lower()
    for r in _load_reciters():
        if r.slug.lower() == needle:
            return r
    available = ", ".join(r.slug for r in _load_reciters())
    raise OverlayError(
        f"Unknown reciter slug: {slug!r}",
        details=f"Available slugs: {available}",
    )


def _coerce_reciter(reciter: Union[str, Reciter]) -> Reciter:
    """Accept either a slug or a ``Reciter`` instance and return a ``Reciter``."""
    if isinstance(reciter, Reciter):
        return reciter
    if isinstance(reciter, str):
        return get_reciter(reciter)
    raise OverlayError(
        f"reciter must be a slug string or Reciter instance, got {type(reciter).__name__}"
    )


def _validate_surah_number(surah_number: int) -> None:
    """Raise ``OverlayError`` if ``surah_number`` is not in 1..114."""
    if not isinstance(surah_number, int) or isinstance(surah_number, bool):
        raise OverlayError(
            f"surah_number must be an int in 1..114, got {type(surah_number).__name__}"
        )
    if surah_number < 1 or surah_number > 114:
        raise OverlayError(
            f"surah_number {surah_number} is out of range; must be 1..114"
        )


def get_surah_url(surah_number: int, reciter: Union[str, Reciter]) -> str:
    """Return the downloadable MP3 URL for the given surah and reciter."""
    _validate_surah_number(surah_number)
    r = _coerce_reciter(reciter)
    try:
        return r.url_pattern.format(num=surah_number)
    except (KeyError, IndexError, ValueError) as exc:
        raise OverlayError(
            f"Malformed url_pattern for reciter {r.slug!r}: {r.url_pattern!r}",
            details=str(exc),
        ) from exc


def download_surah(
    surah_number: int,
    reciter: Union[str, Reciter],
    cache_dir: Path,
    timeout: int = 120,
) -> Path:
    """Download the MP3 for a surah+reciter into ``cache_dir`` and return its path.

    The download is cached: if the target file already exists and is non-empty,
    the network request is skipped. Uses only the standard library
    (``urllib.request``) with a browser-ish User-Agent string. Raises
    ``YouTubeDownloadError`` on HTTP / network failures.
    """
    r = _coerce_reciter(reciter)
    _validate_surah_number(surah_number)

    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)

    target = cache_dir / f"audio_surah_{surah_number:03d}_{r.slug}.mp3"
    if target.exists() and target.stat().st_size > 0:
        logger.debug("Using cached surah audio: %s", target)
        return target

    url = get_surah_url(surah_number, r)
    logger.info("Downloading surah %d (%s) from %s", surah_number, r.slug, url)

    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    # Stream into a temp file first, then rename atomically so a half-written
    # file never satisfies the "cached" check on subsequent runs.
    tmp = target.with_suffix(".mp3.part")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            with tmp.open("wb") as out:
                while True:
                    chunk = resp.read(64 * 1024)
                    if not chunk:
                        break
                    out.write(chunk)
    except urllib.error.HTTPError as exc:
        tmp.unlink(missing_ok=True)
        raise YouTubeDownloadError(
            f"Failed to download surah {surah_number} for reciter {r.slug!r}",
            details=f"HTTP {exc.code} for {url}: {exc.reason}",
        ) from exc
    except urllib.error.URLError as exc:
        tmp.unlink(missing_ok=True)
        raise YouTubeDownloadError(
            f"Network error downloading surah {surah_number} for reciter {r.slug!r}",
            details=f"{url}: {exc.reason}",
        ) from exc
    except TimeoutError as exc:
        tmp.unlink(missing_ok=True)
        raise YouTubeDownloadError(
            f"Timed out downloading surah {surah_number} for reciter {r.slug!r}",
            details=f"{url}: {exc}",
        ) from exc
    except OSError as exc:
        tmp.unlink(missing_ok=True)
        raise YouTubeDownloadError(
            f"I/O error downloading surah {surah_number} for reciter {r.slug!r}",
            details=f"{url}: {exc}",
        ) from exc

    if tmp.stat().st_size == 0:
        tmp.unlink(missing_ok=True)
        raise YouTubeDownloadError(
            f"Empty response downloading surah {surah_number} for reciter {r.slug!r}",
            details=f"{url} returned zero bytes",
        )

    tmp.replace(target)
    logger.debug("Saved surah audio to %s", target)
    return target

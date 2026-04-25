"""Build ayah-range repetition audio for hifz / sabaq-style memorisation.

Implements features M2 (ayah-range repetition) and M3 (gap-prompt mode)
from ``docs/reviews/2026-04-25-quran-teacher-wishlist.md``.

Public API:

* :class:`AyahRange` - immutable spec for one (surah, start..end, repeats,
  gap_seconds) block.
* :func:`download_ayah` - download a single ayah MP3 from EveryAyah, with
  on-disk caching at ``audio_ayah_<reciter>_<sNNN><aNNN>.mp3``.
* :func:`build_ayah_audio` - orchestrate download + repeat + concat for
  any number of ranges, optionally interleaving silent gaps between
  repeats. Delegates the actual concatenation to
  :func:`audio_concat.concat_audio`.

Silence is synthesised via FFmpeg's ``anullsrc`` filter, written to a
single shared cache file per (sample_rate, channels, duration) tuple so
repeated builds (and multiple ranges with the same gap) reuse it.
"""

from __future__ import annotations

import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import List

from .ayah_data import EVERYAYAH_RECITERS, ayah_count, everyayah_url
from .audio_concat import concat_audio
from .exceptions import OverlayError, YouTubeDownloadError
from .logger import get_logger

logger = get_logger()

_USER_AGENT = (
    "Mozilla/5.0 (compatible; yt-audio-filter/1.0; "
    "+https://github.com/Affiliat0r/yt-audio-filter)"
)

# Synthesised silence parameters. Match the most common EveryAyah file
# layout (44.1 kHz stereo MP3) so concat_audio's signature-match fast
# path can stay engaged when possible.
_SILENCE_SAMPLE_RATE = 44100
_SILENCE_CHANNELS = 2


@dataclass(frozen=True)
class AyahRange:
    """Spec for one repeated ayah-range block.

    Attributes:
        surah: 1..114 inclusive.
        start: First ayah (>=1).
        end: Last ayah, inclusive. Must satisfy ``start <= end <=
            ayah_count(surah)``.
        repeats: How many times the [start..end] block is played back-to-
            back. ``1`` means play once (no actual repetition).
        gap_seconds: Silent gap inserted *between* repeats of the block.
            Zero means no gap. Ignored when ``repeats <= 1``.
    """

    surah: int
    start: int
    end: int
    repeats: int = 1
    gap_seconds: float = 0.0

    def __post_init__(self) -> None:
        # ayah_count validates the surah number.
        max_ayah = ayah_count(self.surah)
        if isinstance(self.start, bool) or not isinstance(self.start, int):
            raise ValueError(
                f"start must be int, got {type(self.start).__name__}"
            )
        if isinstance(self.end, bool) or not isinstance(self.end, int):
            raise ValueError(
                f"end must be int, got {type(self.end).__name__}"
            )
        if self.start < 1:
            raise ValueError(f"start must be >=1, got {self.start}")
        if self.end < self.start:
            raise ValueError(
                f"end ({self.end}) must be >= start ({self.start})"
            )
        if self.end > max_ayah:
            raise ValueError(
                f"end ({self.end}) exceeds ayah_count({self.surah})={max_ayah}"
            )
        if isinstance(self.repeats, bool) or not isinstance(self.repeats, int):
            raise ValueError(
                f"repeats must be int, got {type(self.repeats).__name__}"
            )
        if self.repeats < 1:
            raise ValueError(f"repeats must be >=1, got {self.repeats}")
        if not isinstance(self.gap_seconds, (int, float)) or isinstance(
            self.gap_seconds, bool
        ):
            raise ValueError(
                f"gap_seconds must be a number, got {type(self.gap_seconds).__name__}"
            )
        if self.gap_seconds < 0:
            raise ValueError(
                f"gap_seconds must be >=0, got {self.gap_seconds}"
            )


def _resolve_everyayah_path(reciter_slug: str) -> str:
    """Map an internal short slug to its EveryAyah folder name.

    Accepts either the short slug in :data:`EVERYAYAH_RECITERS` (e.g.
    ``"alafasy"``) or a literal EveryAyah folder name. The latter form
    is supported so power-users / Phase 2 can pass through unmapped
    reciters without us needing to extend the table for every variant.
    """
    if not isinstance(reciter_slug, str) or not reciter_slug.strip():
        raise OverlayError("reciter_slug must be a non-empty string")
    s = reciter_slug.strip()
    entry = EVERYAYAH_RECITERS.get(s.lower())
    if entry is not None:
        return entry["everyayah_path"]
    # Treat as a direct everyayah folder slug (e.g. "Husary_64kbps").
    return s


def download_ayah(
    reciter_slug: str,
    surah: int,
    ayah: int,
    cache_dir: Path,
    timeout: int = 60,
) -> Path:
    """Download one ayah MP3 from EveryAyah and cache it on disk.

    Cache filename pattern (under ``cache_dir``)::

        audio_ayah_<reciter_slug>_s<NNN>a<NNN>.mp3

    e.g. ``audio_ayah_alafasy_s001a001.mp3``. ``reciter_slug`` is stored
    in the filename in its lower-cased input form so a switch between
    short slug and folder slug doesn't accidentally collide.

    Args:
        reciter_slug: Either a key into :data:`EVERYAYAH_RECITERS`
            (e.g. ``"alafasy"``) or a literal EveryAyah folder name.
        surah: 1..114.
        ayah: 1..ayah_count(surah).
        cache_dir: Directory for the cached MP3. Created if missing.
        timeout: Per-request timeout in seconds.

    Returns:
        Path to the cached MP3.

    Raises:
        OverlayError: If ``reciter_slug`` is empty.
        ValueError: For surah/ayah out of range (raised by
            :func:`everyayah_url`).
        YouTubeDownloadError: On network / HTTP / empty-body failures.
    """
    everyayah_path = _resolve_everyayah_path(reciter_slug)
    # everyayah_url validates surah & ayah for us.
    url = everyayah_url(everyayah_path, surah, ayah)

    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)

    safe_slug = reciter_slug.strip().lower()
    target = cache_dir / f"audio_ayah_{safe_slug}_s{surah:03d}a{ayah:03d}.mp3"
    if target.exists() and target.stat().st_size > 0:
        logger.debug("Using cached ayah audio: %s", target)
        return target

    logger.info(
        "Downloading ayah %d:%d (%s) from %s", surah, ayah, reciter_slug, url
    )

    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
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
            f"Failed to download ayah {surah}:{ayah} for reciter {reciter_slug!r}",
            details=f"HTTP {exc.code} for {url}: {exc.reason}",
        ) from exc
    except urllib.error.URLError as exc:
        tmp.unlink(missing_ok=True)
        raise YouTubeDownloadError(
            f"Network error downloading ayah {surah}:{ayah} for reciter {reciter_slug!r}",
            details=f"{url}: {exc.reason}",
        ) from exc
    except TimeoutError as exc:
        tmp.unlink(missing_ok=True)
        raise YouTubeDownloadError(
            f"Timed out downloading ayah {surah}:{ayah} for reciter {reciter_slug!r}",
            details=f"{url}: {exc}",
        ) from exc
    except OSError as exc:
        tmp.unlink(missing_ok=True)
        raise YouTubeDownloadError(
            f"I/O error downloading ayah {surah}:{ayah} for reciter {reciter_slug!r}",
            details=f"{url}: {exc}",
        ) from exc

    if tmp.stat().st_size == 0:
        tmp.unlink(missing_ok=True)
        raise YouTubeDownloadError(
            f"Empty response downloading ayah {surah}:{ayah} for reciter {reciter_slug!r}",
            details=f"{url} returned zero bytes",
        )

    tmp.replace(target)
    logger.debug("Saved ayah audio to %s", target)
    return target


def _silence_path(cache_dir: Path, gap_seconds: float) -> Path:
    """Return the cache path for a synthesized silence MP3 of given duration."""
    # Round to 3 decimals so 1.5 and 1.500 share a file.
    millis = int(round(gap_seconds * 1000))
    return cache_dir / f"silence_{millis}ms.mp3"


def _make_silence(gap_seconds: float, cache_dir: Path, timeout: int = 60) -> Path:
    """Synthesize a silent MP3 of ``gap_seconds`` duration via ffmpeg anullsrc.

    Cached: subsequent calls with the same duration reuse the file. The
    output sample-rate / channel layout is fixed (see
    ``_SILENCE_SAMPLE_RATE`` / ``_SILENCE_CHANNELS``) so the silence
    matches the typical EveryAyah MP3 layout.

    Raises:
        OverlayError: If ``gap_seconds <= 0``.
        FFmpegError: If the ffmpeg invocation fails (propagated from the
            subprocess via this wrapper as a generic OverlayError so we
            don't have to import FFmpegError just for this branch).
    """
    if gap_seconds <= 0:
        raise OverlayError(
            f"gap_seconds must be > 0 to synthesize silence, got {gap_seconds}"
        )
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    out_path = _silence_path(cache_dir, gap_seconds)
    if out_path.exists() and out_path.stat().st_size > 0:
        return out_path

    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-y",
        "-f", "lavfi",
        "-i", f"anullsrc=r={_SILENCE_SAMPLE_RATE}:cl=stereo",
        "-t", f"{gap_seconds}",
        "-c:a", "libmp3lame",
        "-b:a", "128k",
        str(out_path),
    ]
    logger.debug("Synthesizing %.3fs silence -> %s", gap_seconds, out_path)
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise OverlayError(
            f"ffmpeg anullsrc timed out after {timeout}s"
        ) from exc
    if result.returncode != 0:
        raise OverlayError(
            "ffmpeg anullsrc failed",
            details=result.stderr,
        )
    return out_path


def _expand_range(rng: AyahRange) -> List[tuple[int, int]]:
    """Return a list of (surah, ayah) tuples for one [start..end] block."""
    return [(rng.surah, a) for a in range(rng.start, rng.end + 1)]


def build_ayah_audio(
    ranges: List[AyahRange],
    reciter_slug: str,
    cache_dir: Path,
    output: Path,
    timeout: int = 60,
) -> Path:
    """Build a single audio file for the given ranges.

    For each ``AyahRange`` we:

    1. Download every ayah in ``[start..end]`` (cached).
    2. Repeat that block ``repeats`` times, optionally inserting a
       synthesized silence MP3 of ``gap_seconds`` between repeats.

    All ranges are then concatenated into one output file via
    :func:`audio_concat.concat_audio`.

    Args:
        ranges: One or more :class:`AyahRange` specs in playback order.
        reciter_slug: EveryAyah short slug or folder name (see
            :func:`download_ayah`).
        cache_dir: Cache directory for both ayah MP3s and silence MP3s.
        output: Destination path for the merged audio (typically ``.m4a``).
        timeout: Per-download / per-ffmpeg timeout in seconds.

    Returns:
        ``output`` path.

    Raises:
        OverlayError: If ``ranges`` is empty.
        YouTubeDownloadError: On any per-ayah download failure.
        FFmpegError: On concat / silence-synthesis failure.
    """
    if not ranges:
        raise OverlayError("build_ayah_audio requires at least one AyahRange")

    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)

    concat_inputs: List[Path] = []

    for rng in ranges:
        # Step 1: download every ayah in the block exactly once. Even with
        # repeats the file is referenced N times in the concat list, so
        # we don't need redundant downloads.
        block_paths: List[Path] = []
        for surah, ayah in _expand_range(rng):
            block_paths.append(
                download_ayah(
                    reciter_slug,
                    surah,
                    ayah,
                    cache_dir,
                    timeout=timeout,
                )
            )

        # Step 2: build the silence segment if needed.
        silence_path: Path | None = None
        if rng.repeats > 1 and rng.gap_seconds > 0:
            silence_path = _make_silence(
                rng.gap_seconds, cache_dir, timeout=timeout
            )

        # Step 3: lay out repeats. Block, then (gap + block) for each
        # additional repeat. So 3 repeats = block, gap, block, gap, block.
        for i in range(rng.repeats):
            if i > 0 and silence_path is not None:
                concat_inputs.append(silence_path)
            concat_inputs.extend(block_paths)

    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    logger.info(
        "Concatenating %d audio segments across %d range(s) -> %s",
        len(concat_inputs),
        len(ranges),
        output,
    )
    return concat_audio(concat_inputs, output, timeout=max(timeout * 4, 1800))


__all__ = [
    "AyahRange",
    "build_ayah_audio",
    "download_ayah",
]

"""Build Advanced SubStation Alpha (.ass) subtitle files for ayah overlays.

The ``.ass`` format is libass / ffmpeg's preferred subtitle container for
karaoke-style highlighting, multi-line stacked layouts, and per-event
styling. We pick it over ``.srt`` because:

* SRT can't do multiple styled lines per cue (Arabic 56pt + English 28pt
  + Dutch 24pt stacked).
* SRT can't do karaoke ``\\k`` highlighting.
* ASS is rendered by libass, which ffmpeg's ``subtitles=`` filter calls
  through internally.

Layout, top-down:

::

    [...cartoon background, busy & colourful...]
              Arabic ayah, large, RTL, white-on-black-shadow
              English meaning, smaller, lighter weight
              Dutch / third language, smallest, italic
              [YouTube progress bar safe zone, ~140 px @ 1080p]

The vertical margin (MarginV) is computed from the configured "bottom safe
zone percentage" times the assumed render resolution height. At 1080p with
the default 18% safe zone that puts the baseline ~194 px above the bottom
edge, well clear of YouTube's 140 px progress overlay.

Styling decisions documented in the module are not exhaustive policy —
feel free to adjust the constants — but they were picked to be legible
on a busy cartoon background:

* White primary fill with a 4 px black outline + 2 px drop shadow.
* No background box (busy backgrounds make boxes look heavier than the
  text itself).
* ``Arial`` is the explicit font; libass will fall back to a system font
  if Arial is missing, but the relative sizing remains stable.
* ``ScaledBorderAndShadow: yes`` keeps the outline crisp at all output
  resolutions; without it the border thickness scales linearly with the
  PlayRes which produces a too-thin outline on 1080p+ output.

Karaoke implementation note: when ``karaoke=True`` and word-level
``word_segments`` are supplied per ayah, the Arabic line is split into
words on whitespace and each ``\\k<centiseconds>`` tag is prepended in
order. libass renders that as the standard "fill the word in highlight
colour as it is sung" effect. When word_segments is missing for an ayah
we silently degrade to the un-highlighted line for *that* ayah; the
others still highlight. We do not partially highlight (ayah-level pulse)
because ``\\k`` requires per-word durations and ayah-level start/end
gives us only one boundary, not enough to tag every word.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from .exceptions import OverlayError
from .logger import get_logger
from .quran_text import AyahText

logger = get_logger()


# Style sizes are tuned for a 1920x1080 PlayRes. libass scales these
# linearly to the actual render resolution, so 1080p = these px values.
_STYLE_ARABIC_SIZE = 56
_STYLE_EN_SIZE = 28
_STYLE_EXTRA_SIZE = 24

_STYLE_PRIMARY_COLOR = "&H00FFFFFF"  # white
_STYLE_HIGHLIGHT_COLOR = "&H0000FFFF"  # yellow (BGR in ASS)
_STYLE_OUTLINE_COLOR = "&H00000000"  # black outline
_STYLE_SHADOW_COLOR = "&H80000000"  # 50% black shadow

_PLAY_RES_X = 1920
_PLAY_RES_Y = 1080


@dataclass(frozen=True)
class TimedAyah:
    """Audio-aligned ayah marker.

    ``word_segments`` (optional) is a list of ``(word_index, start_s,
    end_s)`` tuples. Word indices are 1-based and reference whitespace-
    delimited words in the Arabic ``arabic`` field of the corresponding
    :class:`AyahText`. When ``None`` (or shorter than the number of
    Arabic words), karaoke highlighting silently falls back to plain
    text for this ayah.
    """

    surah: int
    ayah: int
    start_seconds: float
    end_seconds: float
    word_segments: Optional[List[Tuple[int, float, float]]] = None


def _format_ass_time(seconds: float) -> str:
    """Format ``seconds`` as ``H:MM:SS.cc`` (centisecond precision)."""
    if seconds < 0:
        seconds = 0.0
    total_cs = int(round(seconds * 100))
    cs = total_cs % 100
    total_s = total_cs // 100
    s = total_s % 60
    total_m = total_s // 60
    m = total_m % 60
    h = total_m // 60
    return f"{h:d}:{m:02d}:{s:02d}.{cs:02d}"


def _compute_margin_v(resolution_height: int, safe_zone_pct: float) -> int:
    """Pixels from the bottom of the frame to keep clear (the YouTube
    progress overlay).

    ASS ``MarginV`` is a vertical margin from the bottom for bottom-aligned
    styles. We round up so that 18% of 1080 -> 195 px, comfortably above
    YouTube's ~140 px progress bar / chapter overlay.
    """
    if safe_zone_pct < 0 or safe_zone_pct > 0.5:
        raise OverlayError(
            f"bottom_safe_zone_pct must be in 0..0.5, got {safe_zone_pct}"
        )
    return int(round(resolution_height * safe_zone_pct))


def _ass_header(
    *,
    margin_v: int,
    play_res_x: int = _PLAY_RES_X,
    play_res_y: int = _PLAY_RES_Y,
) -> str:
    """Return the ``[Script Info]`` + ``[V4+ Styles]`` header.

    Three styles, one per line of the stack: ``Arabic`` (large, centered),
    ``English`` (medium), ``Extra`` (smaller italic). All three use
    Alignment 2 (bottom-center). ``MarginV`` is shared so the three lines
    stack naturally above the safe zone — Arabic on top by virtue of
    being the first event in render order.
    """
    return (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        "Collisions: Normal\n"
        f"PlayResX: {play_res_x}\n"
        f"PlayResY: {play_res_y}\n"
        "ScaledBorderAndShadow: yes\n"
        "WrapStyle: 0\n"
        "\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Arabic,Arial,{_STYLE_ARABIC_SIZE},{_STYLE_PRIMARY_COLOR},"
        f"{_STYLE_HIGHLIGHT_COLOR},{_STYLE_OUTLINE_COLOR},{_STYLE_SHADOW_COLOR},"
        f"-1,0,0,0,100,100,0,0,1,4,2,2,40,40,{margin_v + 80},1\n"
        f"Style: English,Arial,{_STYLE_EN_SIZE},{_STYLE_PRIMARY_COLOR},"
        f"{_STYLE_HIGHLIGHT_COLOR},{_STYLE_OUTLINE_COLOR},{_STYLE_SHADOW_COLOR},"
        f"0,0,0,0,100,100,0,0,1,3,2,2,40,40,{margin_v + 40},1\n"
        f"Style: Extra,Arial,{_STYLE_EXTRA_SIZE},{_STYLE_PRIMARY_COLOR},"
        f"{_STYLE_HIGHLIGHT_COLOR},{_STYLE_OUTLINE_COLOR},{_STYLE_SHADOW_COLOR},"
        f"0,-1,0,0,100,100,0,0,1,3,2,2,40,40,{margin_v},1\n"
        "\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, "
        "MarginV, Effect, Text\n"
    )


def _escape_ass_text(text: str) -> str:
    """Escape characters that break ASS event lines."""
    # ASS uses '\N' for hard line breaks and treats braces as override blocks.
    # Replace literal CRLF/LF with \N, and protect against stray braces.
    return (
        text.replace("\r\n", "\\N")
        .replace("\n", "\\N")
        .replace("{", "\\{")
        .replace("}", "\\}")
    )


def _build_karaoke_arabic(
    arabic: str,
    word_segments: Sequence[Tuple[int, float, float]],
    ayah_start: float,
) -> Optional[str]:
    """Return Arabic text with ``\\k<cs>`` tags per word, or None if the
    segment count doesn't match the word count (degrade to plain text)."""
    words = arabic.split()
    if not words:
        return None
    # word_index in segments is 1-based per Quran.com convention.
    by_idx: Dict[int, Tuple[float, float]] = {
        int(idx): (float(s), float(e)) for idx, s, e in word_segments
    }
    # Require coverage for every word; otherwise we'd emit half-karaoke
    # that pops untimed words, which looks worse than no karaoke.
    if not all((i + 1) in by_idx for i in range(len(words))):
        return None

    parts: List[str] = []
    for i, w in enumerate(words):
        start, end = by_idx[i + 1]
        # Clamp duration to >=1 cs so libass emits a tag.
        duration_cs = max(1, int(round((end - start) * 100)))
        parts.append("{\\k" + str(duration_cs) + "}" + w)
    return " ".join(parts)


def build_ass_file(
    timed_ayat: List[TimedAyah],
    texts: Dict[Tuple[int, int], AyahText],
    output_path: Path,
    *,
    languages: Sequence[str] = ("ar", "en"),
    karaoke: bool = False,
    bottom_safe_zone_pct: float = 0.18,
    resolution_height: int = _PLAY_RES_Y,
) -> Path:
    """Write an ``.ass`` file for the given ayat and return its path.

    ``languages`` is an ordered tuple selecting which lines to emit per
    ayah. Currently understood:

    * ``"ar"`` — Arabic, large, top of the stack.
    * ``"en"`` — Saheeh International English, middle.
    * any other code (e.g. ``"nl"``) — uses ``translation_extra`` if
      present on the matching :class:`AyahText`; silently dropped if
      ``translation_extra`` is None.

    ``karaoke=True`` emits ``\\k`` tags on the Arabic line *when* the
    matching ``TimedAyah.word_segments`` covers every Arabic word.
    Otherwise the Arabic line for that ayah is rendered plain.
    """
    if not isinstance(output_path, Path):
        output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    margin_v = _compute_margin_v(resolution_height, bottom_safe_zone_pct)

    lines: List[str] = [_ass_header(margin_v=margin_v, play_res_y=resolution_height)]

    arabic_enabled = "ar" in languages
    english_enabled = "en" in languages
    extra_codes = [c for c in languages if c not in ("ar", "en")]

    for ta in timed_ayat:
        key = (ta.surah, ta.ayah)
        text = texts.get(key)
        if text is None:
            logger.warning("No text for %s; skipping subtitle event", key)
            continue
        start = _format_ass_time(ta.start_seconds)
        end = _format_ass_time(ta.end_seconds)

        if arabic_enabled:
            arabic_text = text.arabic
            event_text: Optional[str] = None
            if karaoke and ta.word_segments:
                event_text = _build_karaoke_arabic(
                    arabic_text, ta.word_segments, ta.start_seconds
                )
            if event_text is None:
                event_text = _escape_ass_text(arabic_text)
            lines.append(
                f"Dialogue: 0,{start},{end},Arabic,,0,0,0,,{event_text}\n"
            )

        if english_enabled and text.translation_en:
            lines.append(
                f"Dialogue: 0,{start},{end},English,,0,0,0,,"
                f"{_escape_ass_text(text.translation_en)}\n"
            )

        for _code in extra_codes:
            if text.translation_extra:
                lines.append(
                    f"Dialogue: 0,{start},{end},Extra,,0,0,0,,"
                    f"{_escape_ass_text(text.translation_extra)}\n"
                )
                break  # Only one extra row regardless of how many codes given.

    output_path.write_text("".join(lines), encoding="utf-8")
    logger.debug("Wrote ASS subtitle file: %s", output_path)
    return output_path

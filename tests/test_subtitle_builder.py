"""Tests for yt_audio_filter.subtitle_builder."""

from __future__ import annotations

from pathlib import Path

import pytest

from yt_audio_filter.exceptions import OverlayError
from yt_audio_filter.quran_text import AyahText
from yt_audio_filter.subtitle_builder import (
    TimedAyah,
    _compute_margin_v,
    _format_ass_time,
    build_ass_file,
)


def _ayah(
    surah: int = 1,
    ayah: int = 1,
    arabic: str = "بِسْمِ ٱللَّهِ ٱلرَّحْمَـٰنِ ٱلرَّحِيمِ",
    en: str = "In the name of Allah, the Most Merciful.",
    extra: str | None = None,
) -> AyahText:
    return AyahText(
        surah=surah,
        ayah=ayah,
        arabic=arabic,
        translation_en=en,
        translation_extra=extra,
    )


def test_format_ass_time_centiseconds() -> None:
    assert _format_ass_time(0) == "0:00:00.00"
    assert _format_ass_time(1.235) == "0:00:01.24"  # round half-up to even by default
    assert _format_ass_time(60) == "0:01:00.00"
    assert _format_ass_time(3661.5) == "1:01:01.50"


def test_compute_margin_v_clears_youtube_progress_bar_at_1080p() -> None:
    # YouTube progress bar + chapter overlay reaches ~140 px at 1080p.
    margin = _compute_margin_v(1080, 0.18)
    assert margin >= 140


def test_compute_margin_v_rejects_invalid_pct() -> None:
    with pytest.raises(OverlayError):
        _compute_margin_v(1080, -0.1)
    with pytest.raises(OverlayError):
        _compute_margin_v(1080, 0.6)


def test_build_ass_file_basic_two_languages(tmp_path: Path) -> None:
    timed = [TimedAyah(surah=1, ayah=1, start_seconds=0.0, end_seconds=4.5)]
    texts = {(1, 1): _ayah()}
    out = build_ass_file(timed, texts, tmp_path / "subs.ass")

    content = out.read_text(encoding="utf-8")
    assert "[Script Info]" in content
    assert "[V4+ Styles]" in content
    assert "[Events]" in content
    # One Dialogue per language: ar + en = 2 events
    dialogue_lines = [l for l in content.splitlines() if l.startswith("Dialogue:")]
    assert len(dialogue_lines) == 2
    assert any("Style: Arabic" in c or ",Arabic," in c for c in dialogue_lines)
    assert any(",English," in c for c in dialogue_lines)


def test_build_ass_file_three_languages(tmp_path: Path) -> None:
    timed = [TimedAyah(surah=1, ayah=1, start_seconds=0.0, end_seconds=4.5)]
    texts = {(1, 1): _ayah(extra="In de naam van Allah, de Barmhartige.")}
    out = build_ass_file(
        timed,
        texts,
        tmp_path / "subs.ass",
        languages=("ar", "en", "nl"),
    )
    content = out.read_text(encoding="utf-8")
    dialogue_lines = [l for l in content.splitlines() if l.startswith("Dialogue:")]
    # ar + en + extra = 3
    assert len(dialogue_lines) == 3
    assert any(",Arabic," in c for c in dialogue_lines)
    assert any(",English," in c for c in dialogue_lines)
    assert any(",Extra," in c for c in dialogue_lines)
    assert "Barmhartige" in content


def test_build_ass_file_three_langs_no_extra_text_skips_extra(tmp_path: Path) -> None:
    """Requesting ('ar','en','nl') but the AyahText has no translation_extra
    should silently drop the extra line, not crash."""
    timed = [TimedAyah(surah=1, ayah=1, start_seconds=0.0, end_seconds=4.5)]
    texts = {(1, 1): _ayah(extra=None)}
    out = build_ass_file(
        timed,
        texts,
        tmp_path / "subs.ass",
        languages=("ar", "en", "nl"),
    )
    content = out.read_text(encoding="utf-8")
    dialogue_lines = [l for l in content.splitlines() if l.startswith("Dialogue:")]
    assert len(dialogue_lines) == 2  # ar + en only
    assert ",Extra," not in content.replace("Style: Extra", "")


def test_build_ass_file_karaoke_emits_k_tags(tmp_path: Path) -> None:
    arabic = "كلمة1 كلمة2 كلمة3"
    timed = [
        TimedAyah(
            surah=1,
            ayah=1,
            start_seconds=10.0,
            end_seconds=13.0,
            word_segments=[(1, 10.0, 11.0), (2, 11.0, 12.0), (3, 12.0, 13.0)],
        )
    ]
    texts = {(1, 1): _ayah(arabic=arabic)}
    out = build_ass_file(
        timed,
        texts,
        tmp_path / "subs.ass",
        karaoke=True,
    )
    content = out.read_text(encoding="utf-8")
    # Three \k tags, one per word, each ~100 cs.
    assert content.count("\\k") == 3
    assert "\\k100" in content


def test_build_ass_file_karaoke_degrades_when_segments_missing(tmp_path: Path) -> None:
    """When word_segments is missing or under-covers the words, the Arabic
    line for that ayah is rendered plain (no \\k tags)."""
    arabic = "كلمة1 كلمة2 كلمة3"
    timed = [
        TimedAyah(
            surah=1,
            ayah=1,
            start_seconds=10.0,
            end_seconds=13.0,
            word_segments=[(1, 10.0, 11.0)],  # only word 1 has timing
        )
    ]
    texts = {(1, 1): _ayah(arabic=arabic)}
    out = build_ass_file(timed, texts, tmp_path / "subs.ass", karaoke=True)
    content = out.read_text(encoding="utf-8")
    assert "\\k" not in content
    assert arabic in content


def test_build_ass_file_safe_zone_position(tmp_path: Path) -> None:
    """MarginV must clear YouTube's progress bar at 1080p (>=140 px)."""
    timed = [TimedAyah(surah=1, ayah=1, start_seconds=0.0, end_seconds=4.0)]
    texts = {(1, 1): _ayah()}
    out = build_ass_file(
        timed,
        texts,
        tmp_path / "subs.ass",
        bottom_safe_zone_pct=0.18,
        resolution_height=1080,
    )
    content = out.read_text(encoding="utf-8")
    # Pull the MarginV (22nd field) from the first Style line.
    style_lines = [
        l for l in content.splitlines() if l.startswith("Style: ")
    ]
    assert style_lines, "ASS header must define styles"
    fields = style_lines[0].split(",")
    margin_v = int(fields[21])
    assert margin_v >= 140, f"MarginV {margin_v} would collide with YouTube progress bar"


def test_build_ass_file_creates_parent_dirs(tmp_path: Path) -> None:
    target = tmp_path / "deeper" / "still" / "subs.ass"
    timed = [TimedAyah(surah=1, ayah=1, start_seconds=0.0, end_seconds=1.0)]
    texts = {(1, 1): _ayah()}
    out = build_ass_file(timed, texts, target)
    assert out.exists()


def test_build_ass_file_skips_missing_text(tmp_path: Path) -> None:
    """An ayah listed in timed_ayat but missing from texts must not crash."""
    timed = [
        TimedAyah(surah=1, ayah=1, start_seconds=0.0, end_seconds=1.0),
        TimedAyah(surah=1, ayah=2, start_seconds=1.0, end_seconds=2.0),
    ]
    texts = {(1, 1): _ayah()}  # ayah 2 deliberately missing
    out = build_ass_file(timed, texts, tmp_path / "subs.ass")
    content = out.read_text(encoding="utf-8")
    dialogue_lines = [l for l in content.splitlines() if l.startswith("Dialogue:")]
    # Only ayah 1 emits events: ar + en.
    assert len(dialogue_lines) == 2


def test_build_ass_file_escapes_braces_and_newlines(tmp_path: Path) -> None:
    """Stray '{' or newlines in source text must not break the ASS event."""
    timed = [TimedAyah(surah=1, ayah=1, start_seconds=0.0, end_seconds=1.0)]
    texts = {
        (1, 1): _ayah(
            arabic="text",
            en="line1\nline2 {brace}",
        )
    }
    out = build_ass_file(timed, texts, tmp_path / "subs.ass")
    content = out.read_text(encoding="utf-8")
    # Hard line break encoded as \N (the literal two characters), brace
    # escaped as \{.
    assert "line1\\Nline2" in content
    assert "\\{brace\\}" in content

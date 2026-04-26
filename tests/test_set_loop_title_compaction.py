"""Regression tests: titles for set-loop renders must stay under
YouTube's 100-char title limit.

Root cause: ``_build_surah_numbers_auto_vars`` only compacts CONSECUTIVE
duplicate surahs. The set-loop UI feature expands ``[F, I, N]`` into
``[F, I, N, F, I, N, ...]`` (interleaved), which the consecutive-only
compactor leaves uncompressed — producing titles like
``"Al-Fatiha + Al-Ikhlas + An-Nas + Al-Fatiha + ..." × 10`` that
overflow the 100-char limit and get rejected by YouTube as
``invalidTitle``.
"""

from __future__ import annotations

from pathlib import Path

import pytest


# -- _detect_set_loop helper -------------------------------------------------


def test_detect_set_loop_no_loop() -> None:
    from yt_audio_filter.overlay_pipeline import _detect_set_loop
    assert _detect_set_loop([1, 112, 113]) == ([1, 112, 113], 1)


def test_detect_set_loop_simple_3surah_x10() -> None:
    from yt_audio_filter.overlay_pipeline import _detect_set_loop
    assert _detect_set_loop([1, 112, 113] * 10) == ([1, 112, 113], 10)


def test_detect_set_loop_picks_smallest_period() -> None:
    """``[1, 2, 1, 2, 1, 2]`` is base=[1,2] × 3, not base=[1,2,1,2] × 1."""
    from yt_audio_filter.overlay_pipeline import _detect_set_loop
    assert _detect_set_loop([1, 2, 1, 2, 1, 2]) == ([1, 2], 3)


def test_detect_set_loop_single_surah_repeated_is_not_set_loop() -> None:
    """``[1, 1, 1]`` is per-surah-repeat (handled by consecutive compact),
    not a set loop. Returning loops=1 keeps the existing per-surah path."""
    from yt_audio_filter.overlay_pipeline import _detect_set_loop
    base, loops = _detect_set_loop([1, 1, 1])
    # Either form is mathematically valid, but for title formatting we
    # want the per-surah path (loops=1) so the consecutive compactor
    # produces "Al-Fatiha (×3)" instead of "Al-Fatiha (set ×3)".
    assert loops == 1
    assert base == [1, 1, 1]


def test_detect_set_loop_with_per_surah_repeats_inside_base() -> None:
    """``[F, F, I, N, F, F, I, N]`` = base [F,F,I,N] × 2 (set ×2 of a
    per-surah-repeated block). Set-loop detection should still find it."""
    from yt_audio_filter.overlay_pipeline import _detect_set_loop
    assert _detect_set_loop([1, 1, 112, 114, 1, 1, 112, 114]) == (
        [1, 1, 112, 114],
        2,
    )


# -- _build_surah_numbers_auto_vars uses set-loop in detected_surah ---------


def test_auto_vars_uses_set_loop_format_for_alternating_pattern() -> None:
    from yt_audio_filter.overlay_pipeline import _build_surah_numbers_auto_vars
    # 10x alternating Fatiha + Ikhlas + Nas (the user's bug-trigger).
    av = _build_surah_numbers_auto_vars(
        [1, 112, 114] * 10, "Mishary Rashid Alafasy", ""
    )
    # Must NOT be the long expanded form.
    assert "+ Al-Fatiha + Al-Fatiha" not in av["detected_surah"]
    # Must include the set-loop suffix in some recognisable form.
    assert "10" in av["detected_surah"]
    # All three names must appear (once each, since they're in the base).
    assert av["detected_surah"].count("Al-Fatiha") == 1
    assert av["detected_surah"].count("Al-Ikhlas") == 1
    assert av["detected_surah"].count("An-Nas") == 1


def test_auto_vars_no_set_loop_unchanged_behaviour() -> None:
    """Non-loop input (3 distinct surahs in order) must look identical
    to the pre-fix output: no ``set ×`` suffix anywhere."""
    from yt_audio_filter.overlay_pipeline import _build_surah_numbers_auto_vars
    av = _build_surah_numbers_auto_vars(
        [1, 112, 114], "Mishary Rashid Alafasy", ""
    )
    assert av["detected_surah"] == "Al-Fatiha + Al-Ikhlas + An-Nas"
    assert "set" not in av["detected_surah"].lower()


def test_auto_vars_per_surah_repeat_only_unchanged_behaviour() -> None:
    """``[1, 1, 1]`` = 3× Al-Fatiha must keep using the consecutive
    compactor's ``"Al-Fatiha (×3)"`` form, not switch to set notation."""
    from yt_audio_filter.overlay_pipeline import _build_surah_numbers_auto_vars
    av = _build_surah_numbers_auto_vars([1, 1, 1], "Sudais", "")
    assert av["detected_surah"] == "Al-Fatiha (×3)"


def test_resulting_title_fits_under_youtube_100_char_limit() -> None:
    """Acceptance test for the original bug. The default metadata
    template + a 10× set-loop of 3 surahs must produce a title YouTube
    will accept (≤100 chars). Without the fix this is 488 chars."""
    from yt_audio_filter.metadata import load_metadata
    from yt_audio_filter.overlay_pipeline import _build_surah_numbers_auto_vars

    meta = load_metadata(Path("examples/metadata-surah-arrahman.json"))
    av = _build_surah_numbers_auto_vars(
        [1, 112, 114] * 10, "Mishary Rashid Alafasy", ""
    )
    title = meta.render_title(extra_vars=av)
    assert len(title) <= 100, f"title is {len(title)} chars: {title!r}"


# -- Defense in depth: validate title before it reaches YouTube -------------


def test_upload_with_explicit_metadata_rejects_empty_title(tmp_path: Path) -> None:
    """A defensive check at the upload boundary: refuse to call YouTube
    with an empty title. Surfaces a clear, local error instead of the
    opaque YouTube ``invalidTitle`` 400."""
    from yt_audio_filter.uploader import (
        YouTubeUploadError,
        upload_with_explicit_metadata,
    )

    # tmp_path doesn't actually need a real file; the title check must
    # fire before the file-existence check.
    fake_video = tmp_path / "x.mp4"
    fake_video.write_bytes(b"\x00" * 16)
    with pytest.raises(YouTubeUploadError, match="title"):
        upload_with_explicit_metadata(
            video_path=fake_video,
            title="",
            description="x",
            tags=[],
        )


def test_upload_with_explicit_metadata_rejects_overlong_title(tmp_path: Path) -> None:
    """YouTube enforces a 100-char title limit. Validate before calling
    so the user gets a clear local error showing the actual length."""
    from yt_audio_filter.uploader import (
        YouTubeUploadError,
        upload_with_explicit_metadata,
    )

    fake_video = tmp_path / "x.mp4"
    fake_video.write_bytes(b"\x00" * 16)
    overlong = "x" * 101
    with pytest.raises(YouTubeUploadError, match="100"):
        upload_with_explicit_metadata(
            video_path=fake_video,
            title=overlong,
            description="x",
            tags=[],
        )

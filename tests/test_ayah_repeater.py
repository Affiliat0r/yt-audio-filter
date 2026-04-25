"""Unit tests for yt_audio_filter.ayah_repeater.

All network and ffmpeg interactions are mocked; the tests assert the
orchestration shape (download call sequence, concat input order, silence
synthesis), never the bytes that come out.
"""

from __future__ import annotations

import io
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from yt_audio_filter.ayah_repeater import (
    AyahRange,
    build_ayah_audio,
    download_ayah,
)
from yt_audio_filter.exceptions import OverlayError, YouTubeDownloadError


# ---------- AyahRange validation ----------


def test_ayah_range_basic_construction() -> None:
    rng = AyahRange(surah=1, start=1, end=7)
    assert rng.surah == 1 and rng.start == 1 and rng.end == 7
    assert rng.repeats == 1
    assert rng.gap_seconds == 0.0


def test_ayah_range_validation_start_lt_one() -> None:
    with pytest.raises(ValueError):
        AyahRange(surah=1, start=0, end=3)


def test_ayah_range_validation_end_exceeds_count() -> None:
    # Al-Fatiha has 7 ayat.
    with pytest.raises(ValueError):
        AyahRange(surah=1, start=1, end=8)


def test_ayah_range_validation_end_lt_start() -> None:
    with pytest.raises(ValueError):
        AyahRange(surah=2, start=10, end=5)


def test_ayah_range_validation_bad_surah() -> None:
    with pytest.raises(ValueError):
        AyahRange(surah=0, start=1, end=1)
    with pytest.raises(ValueError):
        AyahRange(surah=115, start=1, end=1)


def test_ayah_range_validation_bad_repeats() -> None:
    with pytest.raises(ValueError):
        AyahRange(surah=1, start=1, end=3, repeats=0)
    with pytest.raises(ValueError):
        AyahRange(surah=1, start=1, end=3, repeats=-2)


def test_ayah_range_validation_bad_gap() -> None:
    with pytest.raises(ValueError):
        AyahRange(surah=1, start=1, end=3, gap_seconds=-1.0)


def test_ayah_range_is_frozen() -> None:
    rng = AyahRange(surah=1, start=1, end=7)
    with pytest.raises(Exception):
        rng.start = 2  # type: ignore[misc]


# ---------- download_ayah ----------


def _mock_response(body: bytes) -> MagicMock:
    cm = MagicMock()
    cm.__enter__.return_value = io.BytesIO(body)
    cm.__exit__.return_value = False
    return cm


def test_download_ayah_writes_file_with_expected_name(tmp_path: Path) -> None:
    body = b"fake-ayah-mp3" * 50
    with patch(
        "yt_audio_filter.ayah_repeater.urllib.request.urlopen",
        return_value=_mock_response(body),
    ) as mocked:
        path = download_ayah("alafasy", 1, 1, tmp_path)

    assert path.exists()
    assert path.read_bytes() == body
    assert path.name == "audio_ayah_alafasy_s001a001.mp3"
    assert mocked.call_count == 1


def test_download_ayah_filename_zero_pads_surah_and_ayah(tmp_path: Path) -> None:
    body = b"x" * 64
    with patch(
        "yt_audio_filter.ayah_repeater.urllib.request.urlopen",
        return_value=_mock_response(body),
    ):
        path = download_ayah("alafasy", 36, 7, tmp_path)
    assert path.name == "audio_ayah_alafasy_s036a007.mp3"


def test_download_ayah_caches_correctly(tmp_path: Path) -> None:
    body = b"cached-bytes" * 40
    # First call: real (mocked) network.
    with patch(
        "yt_audio_filter.ayah_repeater.urllib.request.urlopen",
        return_value=_mock_response(body),
    ) as mocked:
        first = download_ayah("alafasy", 2, 255, tmp_path)
    assert mocked.call_count == 1

    # Second call: must NOT hit urlopen at all.
    with patch(
        "yt_audio_filter.ayah_repeater.urllib.request.urlopen",
        side_effect=AssertionError("should not hit network on cache hit"),
    ) as mocked2:
        second = download_ayah("alafasy", 2, 255, tmp_path)
    assert mocked2.call_count == 0
    assert second == first
    assert second.read_bytes() == body


def test_download_ayah_raises_on_http_error(tmp_path: Path) -> None:
    err = urllib.error.HTTPError(
        url="https://everyayah.com/data/Alafasy_128kbps/001001.mp3",
        code=404,
        msg="Not Found",
        hdrs=None,  # type: ignore[arg-type]
        fp=None,
    )
    with patch(
        "yt_audio_filter.ayah_repeater.urllib.request.urlopen",
        side_effect=err,
    ):
        with pytest.raises(YouTubeDownloadError) as exc_info:
            download_ayah("alafasy", 1, 1, tmp_path)
    assert "404" in exc_info.value.details
    # No partial file left behind.
    assert not (tmp_path / "audio_ayah_alafasy_s001a001.mp3").exists()
    assert not (tmp_path / "audio_ayah_alafasy_s001a001.mp3.part").exists()


def test_download_ayah_raises_on_url_error(tmp_path: Path) -> None:
    with patch(
        "yt_audio_filter.ayah_repeater.urllib.request.urlopen",
        side_effect=urllib.error.URLError("connection refused"),
    ):
        with pytest.raises(YouTubeDownloadError):
            download_ayah("alafasy", 1, 1, tmp_path)


def test_download_ayah_raises_on_empty_body(tmp_path: Path) -> None:
    with patch(
        "yt_audio_filter.ayah_repeater.urllib.request.urlopen",
        return_value=_mock_response(b""),
    ):
        with pytest.raises(YouTubeDownloadError):
            download_ayah("alafasy", 1, 1, tmp_path)
    assert not (tmp_path / "audio_ayah_alafasy_s001a001.mp3").exists()


def test_download_ayah_validates_inputs(tmp_path: Path) -> None:
    with pytest.raises(OverlayError):
        download_ayah("", 1, 1, tmp_path)
    with pytest.raises(ValueError):
        download_ayah("alafasy", 0, 1, tmp_path)
    with pytest.raises(ValueError):
        download_ayah("alafasy", 1, 8, tmp_path)  # Al-Fatiha has 7 ayat


def test_download_ayah_accepts_literal_folder_slug(tmp_path: Path) -> None:
    """Power-users can pass an EveryAyah folder name directly."""
    body = b"y" * 32
    with patch(
        "yt_audio_filter.ayah_repeater.urllib.request.urlopen",
        return_value=_mock_response(body),
    ) as mocked:
        path = download_ayah("Husary_128kbps", 1, 1, tmp_path)
    assert path.exists()
    # Filename uses the lower-cased input slug.
    assert path.name == "audio_ayah_husary_128kbps_s001a001.mp3"
    # The URL passed to urlopen contains the literal folder slug.
    call_args = mocked.call_args
    request = call_args[0][0]
    assert "Husary_128kbps" in request.full_url


# ---------- build_ayah_audio orchestration ----------


def test_build_ayah_audio_orchestration(tmp_path: Path) -> None:
    """Two ranges with repeats=[3, 1] - assert call sequence and concat order.

    Range A: surah 1, ayat 1..2, repeats=3, no gap.
    Range B: surah 112, ayat 1..1, repeats=1.

    Expected downloads (one per unique ayah): 1:1, 1:2, 112:1.
    Expected concat input order: [1:1, 1:2, 1:1, 1:2, 1:1, 1:2, 112:1].
    """
    cache = tmp_path / "cache"
    out = tmp_path / "out.m4a"

    download_calls: list[tuple] = []

    def fake_download(reciter, surah, ayah, cd, timeout=60):
        download_calls.append((reciter, surah, ayah))
        # Simulate caching: same path on repeat downloads.
        p = cd / f"audio_ayah_{reciter}_s{surah:03d}a{ayah:03d}.mp3"
        cd.mkdir(parents=True, exist_ok=True)
        if not p.exists():
            p.write_bytes(b"X" * 16)
        return p

    concat_calls: list[tuple] = []

    def fake_concat(inputs, output, timeout=1800):
        concat_calls.append((list(inputs), output))
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"merged")
        return output

    ranges = [
        AyahRange(surah=1, start=1, end=2, repeats=3),
        AyahRange(surah=112, start=1, end=1, repeats=1),
    ]

    with patch("yt_audio_filter.ayah_repeater.download_ayah", side_effect=fake_download), \
         patch("yt_audio_filter.ayah_repeater.concat_audio", side_effect=fake_concat):
        result = build_ayah_audio(ranges, "alafasy", cache, out)

    assert result == out
    assert result.exists()

    # Each ayah is downloaded once per range invocation. Range A downloads
    # 1:1 and 1:2 once; range B downloads 112:1 once. Even though the
    # block repeats 3x, we don't redundant-download.
    assert download_calls == [
        ("alafasy", 1, 1),
        ("alafasy", 1, 2),
        ("alafasy", 112, 1),
    ]

    # One concat call with the full flattened input list.
    assert len(concat_calls) == 1
    inputs, output_arg = concat_calls[0]
    assert output_arg == out

    # 3 repeats of [1:1, 1:2] = 6 paths, plus [112:1] = 7 total. No silence
    # since gap_seconds=0.
    assert len(inputs) == 7
    # Verify ayah ordering: the first 6 alternate between the s001a001 and
    # s001a002 cache files, then the final input is s112a001.
    names = [p.name for p in inputs]
    assert names == [
        "audio_ayah_alafasy_s001a001.mp3",
        "audio_ayah_alafasy_s001a002.mp3",
        "audio_ayah_alafasy_s001a001.mp3",
        "audio_ayah_alafasy_s001a002.mp3",
        "audio_ayah_alafasy_s001a001.mp3",
        "audio_ayah_alafasy_s001a002.mp3",
        "audio_ayah_alafasy_s112a001.mp3",
    ]


def test_build_ayah_audio_silence_gap(tmp_path: Path) -> None:
    """gap_seconds=1.5 with repeats>1: silence file is interleaved."""
    cache = tmp_path / "cache"
    out = tmp_path / "out.m4a"

    silence_invocations: list[float] = []

    def fake_make_silence(gap_seconds, cache_dir, timeout=60):
        silence_invocations.append(gap_seconds)
        cache_dir.mkdir(parents=True, exist_ok=True)
        p = cache_dir / f"silence_{int(round(gap_seconds * 1000))}ms.mp3"
        if not p.exists():
            p.write_bytes(b"S" * 16)
        return p

    def fake_download(reciter, surah, ayah, cd, timeout=60):
        cd.mkdir(parents=True, exist_ok=True)
        p = cd / f"audio_ayah_{reciter}_s{surah:03d}a{ayah:03d}.mp3"
        if not p.exists():
            p.write_bytes(b"X" * 16)
        return p

    concat_inputs_captured: list[list[Path]] = []

    def fake_concat(inputs, output, timeout=1800):
        concat_inputs_captured.append(list(inputs))
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"merged")
        return output

    ranges = [AyahRange(surah=1, start=1, end=1, repeats=3, gap_seconds=1.5)]

    with patch("yt_audio_filter.ayah_repeater.download_ayah", side_effect=fake_download), \
         patch("yt_audio_filter.ayah_repeater._make_silence", side_effect=fake_make_silence), \
         patch("yt_audio_filter.ayah_repeater.concat_audio", side_effect=fake_concat):
        build_ayah_audio(ranges, "alafasy", cache, out)

    # _make_silence was called once with gap_seconds=1.5.
    assert silence_invocations == [1.5]

    # Concat input order: ayah, silence, ayah, silence, ayah.
    assert len(concat_inputs_captured) == 1
    inputs = concat_inputs_captured[0]
    assert len(inputs) == 5
    names = [p.name for p in inputs]
    assert names == [
        "audio_ayah_alafasy_s001a001.mp3",
        "silence_1500ms.mp3",
        "audio_ayah_alafasy_s001a001.mp3",
        "silence_1500ms.mp3",
        "audio_ayah_alafasy_s001a001.mp3",
    ]


def test_build_ayah_audio_no_silence_when_gap_zero(tmp_path: Path) -> None:
    """gap_seconds=0 must NOT invoke _make_silence."""
    cache = tmp_path / "cache"
    out = tmp_path / "out.m4a"

    def fake_download(reciter, surah, ayah, cd, timeout=60):
        cd.mkdir(parents=True, exist_ok=True)
        p = cd / f"audio_ayah_{reciter}_s{surah:03d}a{ayah:03d}.mp3"
        if not p.exists():
            p.write_bytes(b"X" * 16)
        return p

    def fake_concat(inputs, output, timeout=1800):
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"merged")
        return output

    ranges = [AyahRange(surah=1, start=1, end=2, repeats=2, gap_seconds=0.0)]
    silence_mock = MagicMock()

    with patch("yt_audio_filter.ayah_repeater.download_ayah", side_effect=fake_download), \
         patch("yt_audio_filter.ayah_repeater._make_silence", silence_mock), \
         patch("yt_audio_filter.ayah_repeater.concat_audio", side_effect=fake_concat):
        build_ayah_audio(ranges, "alafasy", cache, out)

    silence_mock.assert_not_called()


def test_build_ayah_audio_no_silence_when_repeats_one(tmp_path: Path) -> None:
    """repeats=1 with gap_seconds>0 must NOT synthesize silence (no repeat boundary)."""
    cache = tmp_path / "cache"
    out = tmp_path / "out.m4a"

    def fake_download(reciter, surah, ayah, cd, timeout=60):
        cd.mkdir(parents=True, exist_ok=True)
        p = cd / f"audio_ayah_{reciter}_s{surah:03d}a{ayah:03d}.mp3"
        if not p.exists():
            p.write_bytes(b"X" * 16)
        return p

    def fake_concat(inputs, output, timeout=1800):
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"merged")
        return output

    ranges = [AyahRange(surah=1, start=1, end=2, repeats=1, gap_seconds=3.0)]
    silence_mock = MagicMock()

    with patch("yt_audio_filter.ayah_repeater.download_ayah", side_effect=fake_download), \
         patch("yt_audio_filter.ayah_repeater._make_silence", silence_mock), \
         patch("yt_audio_filter.ayah_repeater.concat_audio", side_effect=fake_concat):
        build_ayah_audio(ranges, "alafasy", cache, out)

    silence_mock.assert_not_called()


def test_build_ayah_audio_empty_ranges_raises(tmp_path: Path) -> None:
    with pytest.raises(OverlayError):
        build_ayah_audio([], "alafasy", tmp_path, tmp_path / "out.m4a")


def test_build_ayah_audio_propagates_download_failure(tmp_path: Path) -> None:
    """A YouTubeDownloadError from download_ayah aborts the whole build."""
    def boom(*args, **kwargs):
        raise YouTubeDownloadError("simulated 404", details="HTTP 404")

    ranges = [AyahRange(surah=1, start=1, end=3)]
    with patch("yt_audio_filter.ayah_repeater.download_ayah", side_effect=boom):
        with pytest.raises(YouTubeDownloadError):
            build_ayah_audio(ranges, "alafasy", tmp_path, tmp_path / "out.m4a")


def test_build_ayah_audio_uses_anullsrc_subprocess(tmp_path: Path) -> None:
    """End-to-end (with subprocess mocked): _make_silence calls ffmpeg with anullsrc."""
    cache = tmp_path / "cache"
    out = tmp_path / "out.m4a"

    captured_cmds: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        captured_cmds.append(list(cmd))
        # Create the silence file ffmpeg "would have" produced.
        # Output path is the last arg.
        Path(cmd[-1]).parent.mkdir(parents=True, exist_ok=True)
        Path(cmd[-1]).write_bytes(b"silence-bytes" * 8)
        result = MagicMock()
        result.returncode = 0
        result.stderr = ""
        return result

    def fake_download(reciter, surah, ayah, cd, timeout=60):
        cd.mkdir(parents=True, exist_ok=True)
        p = cd / f"audio_ayah_{reciter}_s{surah:03d}a{ayah:03d}.mp3"
        if not p.exists():
            p.write_bytes(b"X" * 16)
        return p

    def fake_concat(inputs, output, timeout=1800):
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"merged")
        return output

    ranges = [AyahRange(surah=1, start=1, end=1, repeats=2, gap_seconds=2.0)]

    with patch("yt_audio_filter.ayah_repeater.subprocess.run", side_effect=fake_run), \
         patch("yt_audio_filter.ayah_repeater.download_ayah", side_effect=fake_download), \
         patch("yt_audio_filter.ayah_repeater.concat_audio", side_effect=fake_concat):
        build_ayah_audio(ranges, "alafasy", cache, out)

    # Exactly one ffmpeg invocation - the silence synth. Its command line
    # references anullsrc.
    assert len(captured_cmds) == 1
    cmd = captured_cmds[0]
    joined = " ".join(cmd)
    assert "anullsrc" in joined
    assert "-t" in cmd
    # The duration arg follows -t.
    t_index = cmd.index("-t")
    assert cmd[t_index + 1] == "2.0"

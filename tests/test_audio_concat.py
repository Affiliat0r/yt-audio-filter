"""Integration tests for yt_audio_filter.audio_concat.

These tests invoke FFmpeg directly to generate short synthetic inputs, then
drive :func:`concat_audio` end-to-end and ffprobe the resulting file. If
FFmpeg is not available on PATH the whole module is skipped.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Optional

import pytest

from yt_audio_filter.audio_concat import concat_audio
from yt_audio_filter.exceptions import FFmpegError, OverlayError


if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
    pytest.skip("ffmpeg/ffprobe not available on PATH", allow_module_level=True)


# --- helpers ---------------------------------------------------------------


def _make_tone(
    out_path: Path,
    *,
    frequency: int = 440,
    duration: float = 2.0,
    sample_rate: int = 44100,
    channels: int = 2,
    codec: str = "aac",
    container_args: Optional[list[str]] = None,
) -> Path:
    """Generate a synthetic tone file via FFmpeg.

    Defaults produce a 44.1 kHz / stereo / AAC .m4a file. Pass ``codec`` and
    ``container_args`` to force other encodings (e.g. Opus in webm).
    """
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-y",
        "-f", "lavfi",
        "-i", f"sine=frequency={frequency}:duration={duration}",
        "-ar", str(sample_rate),
        "-ac", str(channels),
        "-c:a", codec,
    ]
    if container_args:
        cmd.extend(container_args)
    cmd.append(str(out_path))

    result = subprocess.run(cmd, capture_output=True, text=True)
    assert result.returncode == 0, f"Fixture FFmpeg failed: {result.stderr}"
    assert out_path.exists()
    return out_path


def _probe(path: Path) -> dict:
    """Return the first audio stream + format info for ``path``."""
    cmd = [
        "ffprobe",
        "-v", "error",
        "-select_streams", "a:0",
        "-show_entries", "stream=codec_name,sample_rate,channels",
        "-show_entries", "format=duration",
        "-of", "json",
        str(path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    assert result.returncode == 0, f"ffprobe failed: {result.stderr}"
    data = json.loads(result.stdout)
    stream = data.get("streams", [{}])[0]
    fmt = data.get("format", {})
    return {
        "codec": stream.get("codec_name"),
        "sample_rate": int(stream.get("sample_rate", 0)),
        "channels": int(stream.get("channels", 0)),
        "duration": float(fmt.get("duration", 0.0)),
    }


# --- fixtures --------------------------------------------------------------


@pytest.fixture
def two_m4a_files(tmp_path: Path) -> tuple[Path, Path]:
    """Two same-codec AAC/m4a tone files, each ~2s."""
    a = _make_tone(tmp_path / "tone_a.m4a", frequency=440, duration=2.0)
    b = _make_tone(tmp_path / "tone_b.m4a", frequency=880, duration=2.0)
    return a, b


@pytest.fixture
def m4a_and_opus(tmp_path: Path) -> tuple[Path, Path]:
    """One AAC/m4a and one Opus/webm tone file — codecs deliberately differ."""
    m4a = _make_tone(tmp_path / "tone.m4a", frequency=440, duration=2.0)
    # libopus only supports 48000/24000/16000/12000/8000 Hz, so force 48k.
    opus = _make_tone(
        tmp_path / "tone.webm",
        frequency=660,
        duration=2.0,
        sample_rate=48000,
        codec="libopus",
    )
    return m4a, opus


# --- tests -----------------------------------------------------------------


def test_concat_happy_path_same_codec(two_m4a_files: tuple[Path, Path], tmp_path: Path) -> None:
    """Two same-codec inputs concatenate losslessly; duration is the sum."""
    a, b = two_m4a_files
    out = tmp_path / "joined.m4a"

    result = concat_audio([a, b], out)

    assert result == out
    assert out.exists()
    info = _probe(out)
    assert info["codec"] is not None
    assert info["channels"] == 2
    # Allow a small encoder-boundary wobble around the ~4.0s sum.
    assert info["duration"] == pytest.approx(4.0, abs=0.25)


def test_concat_single_input_copies(tmp_path: Path) -> None:
    """A single input is passed through via shutil.copy2 (no FFmpeg run)."""
    src = _make_tone(tmp_path / "only.m4a", duration=1.5)
    out = tmp_path / "copied.m4a"

    result = concat_audio([src], out)

    assert result == out
    assert out.exists()
    assert out.stat().st_size == src.stat().st_size
    info = _probe(out)
    assert info["duration"] == pytest.approx(1.5, abs=0.25)


def test_concat_empty_input_raises(tmp_path: Path) -> None:
    """An empty list is rejected with OverlayError."""
    with pytest.raises(OverlayError):
        concat_audio([], tmp_path / "nope.m4a")


def test_concat_missing_input_raises_before_ffmpeg(tmp_path: Path) -> None:
    """A non-existent path raises OverlayError without launching FFmpeg."""
    real = _make_tone(tmp_path / "real.m4a", duration=1.0)
    ghost = tmp_path / "does_not_exist.m4a"

    with pytest.raises(OverlayError):
        concat_audio([real, ghost], tmp_path / "out.m4a")


def test_concat_mismatched_codecs_reencodes(
    m4a_and_opus: tuple[Path, Path], tmp_path: Path
) -> None:
    """Mismatched codecs trigger the filter_complex re-encode path."""
    m4a, opus = m4a_and_opus
    out = tmp_path / "mixed.m4a"

    result = concat_audio([m4a, opus], out)

    assert result == out
    assert out.exists()
    info = _probe(out)
    # The re-encode path is hard-coded to AAC; the output should be AAC in m4a.
    assert info["codec"] == "aac"
    assert info["channels"] == 2
    assert info["duration"] == pytest.approx(4.0, abs=0.3)


def test_concat_cleans_up_list_file(two_m4a_files: tuple[Path, Path], tmp_path: Path) -> None:
    """The temporary concat list file is removed after a successful run."""
    a, b = two_m4a_files
    out = tmp_path / "joined.m4a"
    concat_audio([a, b], out)

    list_file = out.parent / f".concat_{out.stem}.txt"
    assert not list_file.exists(), "Concat list file should be cleaned up"


def test_concat_raises_ffmpeg_error_on_corrupt_input(tmp_path: Path) -> None:
    """A corrupt-but-existing input surfaces as FFmpegError, not a crash."""
    bad_a = tmp_path / "bad_a.m4a"
    bad_b = tmp_path / "bad_b.m4a"
    bad_a.write_bytes(b"not an audio file at all")
    bad_b.write_bytes(b"definitely not audio either")
    out = tmp_path / "out.m4a"

    with pytest.raises(FFmpegError):
        concat_audio([bad_a, bad_b], out)

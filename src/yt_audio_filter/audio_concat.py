"""Concatenate audio files into a single stream.

Picks the optimal FFmpeg strategy automatically:

- When all inputs share codec, sample rate, and channel count, uses the
  ``concat`` demuxer with ``-c copy`` for a zero-re-encode merge.
- Otherwise falls back to ``-filter_complex`` with ``concat=n=N:v=0:a=1`` and
  re-encodes to AAC at 192k so the resulting container is consistent.

The downstream overlay pipeline runs its own loudnorm pass, so this module
does not attempt to normalize loudness.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import List, Tuple

from .exceptions import FFmpegError, OverlayError
from .ffmpeg import ensure_ffmpeg_available, get_audio_info
from .logger import get_logger

logger = get_logger()


def _validate_inputs(inputs: List[Path]) -> None:
    """Validate the list of input paths.

    Raises:
        OverlayError: If ``inputs`` is empty or any entry does not exist.
    """
    if not inputs:
        raise OverlayError("concat_audio requires at least one input file")

    missing = [str(p) for p in inputs if not p.exists()]
    if missing:
        raise OverlayError(
            "One or more audio inputs do not exist",
            "Missing: " + ", ".join(missing),
        )


def _probe_signatures(inputs: List[Path]) -> List[Tuple[str, int, int]]:
    """Return (codec, sample_rate, channels) tuples for each input.

    Missing fields fall back to sentinel values that will compare unequal to
    valid probes, forcing the re-encode path when ffprobe fails.
    """
    signatures: List[Tuple[str, int, int]] = []
    for p in inputs:
        info = get_audio_info(p)
        codec = str(info.get("codec", "unknown"))
        sample_rate = int(info.get("sample_rate", 0))
        channels = int(info.get("channels", 0))
        signatures.append((codec, sample_rate, channels))
    return signatures


def _all_match(signatures: List[Tuple[str, int, int]]) -> bool:
    """True if every (codec, sample_rate, channels) signature matches."""
    if not signatures:
        return False
    first = signatures[0]
    # A zeroed signature means ffprobe couldn't read it; refuse the fast path.
    if first[1] == 0 or first[2] == 0 or first[0] == "unknown":
        return False
    return all(sig == first for sig in signatures[1:])


def _write_concat_list(inputs: List[Path], list_path: Path) -> None:
    """Write an FFmpeg concat-demuxer file list.

    Paths are absolute with forward slashes; any single quotes in the path
    are escaped per FFmpeg's ``file '...'`` syntax.
    """
    with open(list_path, "w", encoding="utf-8") as f:
        for p in inputs:
            abs_path = str(p.resolve()).replace("\\", "/")
            # FFmpeg concat list escapes a single quote as: '\''
            escaped = abs_path.replace("'", "'\\''")
            f.write(f"file '{escaped}'\n")


def _run_ffmpeg(cmd: List[str], timeout: int, action: str) -> None:
    """Run an FFmpeg subprocess and raise FFmpegError on failure."""
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
        raise FFmpegError(f"{action} timed out after {timeout}s") from exc

    if result.returncode != 0:
        raise FFmpegError(
            f"{action} failed",
            returncode=result.returncode,
            stderr=result.stderr,
        )


def _concat_copy(inputs: List[Path], output: Path, timeout: int) -> None:
    """Concatenate with the concat demuxer and ``-c copy`` (no re-encode)."""
    list_path = output.parent / f".concat_{output.stem}.txt"
    try:
        _write_concat_list(inputs, list_path)
        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", str(list_path),
            "-c", "copy",
            str(output),
        ]
        logger.debug(
            "Concatenating %d audio files via concat demuxer (-c copy) -> %s",
            len(inputs),
            output,
        )
        _run_ffmpeg(cmd, timeout=timeout, action="Audio concat (copy)")
    finally:
        try:
            if list_path.exists():
                list_path.unlink()
        except OSError as exc:
            logger.debug("Could not remove concat list file %s: %s", list_path, exc)


def _concat_reencode(inputs: List[Path], output: Path, timeout: int) -> None:
    """Concatenate by decoding each input and re-encoding to AAC 192k."""
    cmd: List[str] = ["ffmpeg", "-hide_banner", "-y"]
    for p in inputs:
        cmd.extend(["-i", str(p)])

    n = len(inputs)
    stream_list = "".join(f"[{i}:a]" for i in range(n))
    filter_graph = f"{stream_list}concat=n={n}:v=0:a=1[aout]"

    cmd.extend(
        [
            "-filter_complex", filter_graph,
            "-map", "[aout]",
            "-c:a", "aac",
            "-b:a", "192k",
            str(output),
        ]
    )
    logger.debug(
        "Concatenating %d audio files via filter_complex (re-encode AAC 192k) -> %s",
        n,
        output,
    )
    _run_ffmpeg(cmd, timeout=timeout, action="Audio concat (re-encode)")


def concat_audio(
    inputs: List[Path],
    output: Path,
    timeout: int = 1800,
) -> Path:
    """Concatenate audio files in order into a single file at ``output``.

    Picks the concat demuxer (``-c copy``) when all inputs share codec,
    sample rate, and channel count; otherwise falls back to
    ``-filter_complex`` with an AAC 192k re-encode.

    A single-input call short-circuits to ``shutil.copy2`` so callers can
    safely hand a one-element list without special-casing it themselves.

    Args:
        inputs: Ordered list of audio file paths to merge.
        output: Destination path. The extension determines the container;
            callers should typically pass ``.m4a`` for overlay-pipeline
            consistency.
        timeout: Per-FFmpeg-invocation timeout in seconds.

    Returns:
        The ``output`` path.

    Raises:
        OverlayError: If ``inputs`` is empty or any input file is missing.
        FFmpegError: If the FFmpeg subprocess fails or times out.
    """
    _validate_inputs(inputs)

    output.parent.mkdir(parents=True, exist_ok=True)

    if len(inputs) == 1:
        logger.debug("Single input for concat_audio; copying %s -> %s", inputs[0], output)
        shutil.copy2(inputs[0], output)
        return output

    ensure_ffmpeg_available()

    signatures = _probe_signatures(inputs)
    if _all_match(signatures):
        logger.debug(
            "All %d inputs share signature %s; using concat demuxer",
            len(inputs),
            signatures[0],
        )
        try:
            _concat_copy(inputs, output, timeout=timeout)
        except FFmpegError as copy_err:
            # Some containers (notably webm/opus straight from YouTube) match
            # signature-wise but the demuxer still rejects -c copy. Fall
            # through to the re-encode path before giving up.
            logger.warning(
                "concat-demuxer copy failed despite matching signatures; "
                "retrying with filter_complex re-encode (%s)",
                copy_err.message,
            )
            _concat_reencode(inputs, output, timeout=timeout)
    else:
        logger.debug(
            "Input signatures differ (%s); using filter_complex re-encode",
            signatures,
        )
        _concat_reencode(inputs, output, timeout=timeout)

    return output

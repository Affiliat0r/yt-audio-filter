"""FFmpeg command construction for the Quran-overlay render.

Two-pass EBU R128 loudnorm + loop + mute + optional logo overlay,
all in a single encoded output.
"""

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

from .exceptions import FFmpegError, OverlayError, PrerequisiteError
from .ffmpeg import ensure_ffmpeg_available, get_audio_info
from .logger import get_logger

logger = get_logger()


LOUDNORM_TARGETS = {"I": -16.0, "TP": -1.5, "LRA": 11.0}
LOGO_WIDTH_FRACTION = 0.30
LOGO_PADDING_PX = 20


@dataclass
class LoudnormMeasurements:
    input_i: str
    input_tp: str
    input_lra: str
    input_thresh: str
    target_offset: str


def measure_loudnorm(audio_path: Path) -> LoudnormMeasurements:
    """Run the analysis pass of loudnorm and parse the JSON measurements."""
    ensure_ffmpeg_available()

    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-nostats",
        "-i", str(audio_path),
        "-af",
        f"loudnorm=I={LOUDNORM_TARGETS['I']}:TP={LOUDNORM_TARGETS['TP']}"
        f":LRA={LOUDNORM_TARGETS['LRA']}:print_format=json",
        "-f", "null",
        "-",
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=1800,
        )
    except subprocess.TimeoutExpired:
        raise FFmpegError("loudnorm analysis pass timed out")
    except FileNotFoundError:
        raise PrerequisiteError("FFmpeg not found in system PATH")

    if result.returncode != 0:
        raise FFmpegError(
            "loudnorm analysis pass failed",
            returncode=result.returncode,
            stderr=result.stderr,
        )

    match = re.search(r"\{[^{}]*\"input_i\"[\s\S]*?\}", result.stderr)
    if not match:
        raise FFmpegError(
            "Could not locate loudnorm JSON in ffmpeg stderr",
            stderr=result.stderr[-500:],
        )
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError as e:
        raise FFmpegError(f"Failed to parse loudnorm JSON: {e}")

    required = ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset")
    for key in required:
        if key not in data:
            raise FFmpegError(f"loudnorm JSON missing required key: {key}")

    return LoudnormMeasurements(
        input_i=data["input_i"],
        input_tp=data["input_tp"],
        input_lra=data["input_lra"],
        input_thresh=data["input_thresh"],
        target_offset=data["target_offset"],
    )


def _logo_overlay_xy(position: str, padding: int = LOGO_PADDING_PX) -> Tuple[str, str]:
    mapping = {
        "top-left": (f"{padding}", f"{padding}"),
        "top-right": (f"main_w-overlay_w-{padding}", f"{padding}"),
        "bottom-left": (f"{padding}", f"main_h-overlay_h-{padding}"),
        "bottom-right": (f"main_w-overlay_w-{padding}", f"main_h-overlay_h-{padding}"),
    }
    if position not in mapping:
        raise OverlayError(f"Invalid logo position: {position!r}")
    return mapping[position]


def build_filter_graph(
    resolution: Tuple[int, int],
    measurements: LoudnormMeasurements,
    logo: Optional[Tuple[Path, str]],
) -> str:
    """Build the -filter_complex string.

    logo: (path, position) or None.
    """
    width, height = resolution
    loudnorm_clause = (
        f"loudnorm=I={LOUDNORM_TARGETS['I']}:TP={LOUDNORM_TARGETS['TP']}"
        f":LRA={LOUDNORM_TARGETS['LRA']}"
        f":measured_I={measurements.input_i}"
        f":measured_TP={measurements.input_tp}"
        f":measured_LRA={measurements.input_lra}"
        f":measured_thresh={measurements.input_thresh}"
        f":offset={measurements.target_offset}"
        f":linear=true:print_format=summary"
    )

    if logo is None:
        return (
            f"[0:v]scale={width}:{height},setsar=1[vout];"
            f"[1:a]{loudnorm_clause}[aout]"
        )

    _, position = logo
    x, y = _logo_overlay_xy(position)
    return (
        f"[0:v]scale={width}:{height},setsar=1[vscaled];"
        f"[2:v]scale=w=iw*{LOGO_WIDTH_FRACTION}:h=-1[logo];"
        f"[vscaled][logo]overlay=x={x}:y={y}[vout];"
        f"[1:a]{loudnorm_clause}[aout]"
    )


def build_render_command(
    video_path: Path,
    audio_path: Path,
    output_path: Path,
    duration_seconds: float,
    measurements: LoudnormMeasurements,
    resolution: Tuple[int, int] = (1920, 1080),
    logo: Optional[Tuple[Path, str]] = None,
    force: bool = False,
) -> List[str]:
    """Construct the full ffmpeg render argv.

    Video input is preceded by `-stream_loop -1` (input option, must appear
    before `-i`). Output is bounded by `-t` using the pre-measured audio
    duration so the video loop stops when the recitation ends.
    """
    cmd: List[str] = [
        "ffmpeg",
        "-hide_banner",
        "-y" if force else "-n",
        "-stream_loop", "-1",
        "-i", str(video_path),
        "-i", str(audio_path),
    ]

    if logo is not None:
        logo_path, _ = logo
        cmd.extend(["-i", str(logo_path)])

    cmd.extend([
        "-filter_complex", build_filter_graph(resolution, measurements, logo),
        "-map", "[vout]",
        "-map", "[aout]",
        "-c:v", "libx264",
        "-preset", "medium",
        "-crf", "18",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "192k",
        "-t", f"{duration_seconds:.3f}",
        "-movflags", "+faststart",
        str(output_path),
    ])
    return cmd


def get_audio_duration(audio_path: Path) -> float:
    info = get_audio_info(audio_path)
    duration = info.get("duration")
    if duration is None or duration <= 0:
        raise FFmpegError(f"Could not determine audio duration for {audio_path}")
    return float(duration)


def render_overlay(
    video_path: Path,
    audio_path: Path,
    output_path: Path,
    resolution: Tuple[int, int] = (1920, 1080),
    logo: Optional[Tuple[Path, str]] = None,
    max_duration: Optional[float] = None,
    force: bool = False,
) -> Path:
    """Two-pass render: loudnorm analysis, then single ffmpeg render."""
    ensure_ffmpeg_available()

    if not video_path.exists():
        raise OverlayError(f"Video input not found: {video_path}")
    if not audio_path.exists():
        raise OverlayError(f"Audio input not found: {audio_path}")
    if logo is not None and not logo[0].exists():
        raise OverlayError(f"Logo file not found: {logo[0]}")
    if output_path.exists() and not force:
        raise OverlayError(
            f"Output already exists: {output_path}",
            "Pass --force to overwrite.",
        )

    duration = get_audio_duration(audio_path)
    if max_duration is not None and duration > max_duration:
        raise OverlayError(
            f"Audio duration {duration:.1f}s exceeds --max-duration {max_duration:.0f}s",
            "Increase --max-duration or use a shorter recitation.",
        )

    logger.info(f"Measuring loudness on {audio_path.name} (pass 1/2)...")
    measurements = measure_loudnorm(audio_path)
    logger.debug(
        f"loudnorm measured: I={measurements.input_i} TP={measurements.input_tp} "
        f"LRA={measurements.input_lra} offset={measurements.target_offset}"
    )

    cmd = build_render_command(
        video_path=video_path,
        audio_path=audio_path,
        output_path=output_path,
        duration_seconds=duration,
        measurements=measurements,
        resolution=resolution,
        logo=logo,
        force=force,
    )

    logger.info(f"Rendering overlay to {output_path.name} (pass 2/2)...")
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=7200,
        )
    except subprocess.TimeoutExpired:
        raise FFmpegError("Overlay render timed out after 2 hours")
    except FileNotFoundError:
        raise PrerequisiteError("FFmpeg not found in system PATH")

    if result.returncode != 0:
        raise FFmpegError(
            "Overlay render failed",
            returncode=result.returncode,
            stderr=result.stderr,
        )

    return output_path

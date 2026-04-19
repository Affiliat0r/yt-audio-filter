"""Upscale a video to 1080p using Real-ESRGAN (realesrgan-ncnn-vulkan).

Strategy:
  1. Extract every frame from the input video as PNG via FFmpeg.
  2. Run realesrgan-ncnn-vulkan in batch mode over the frame directory.
  3. Reassemble the upscaled frames into an MP4 at the original framerate
     via FFmpeg using the NVENC or libx264 encoder.

The result is cached at ``cache/upscaled_<video_id>.mp4``. First render for a
given visual is slow (~14 fps GPU throughput for the animevideov3 model on
an RTX 3070 Ti); subsequent renders reuse the cached upscaled file and cost
nothing.

The binary is shipped at ``tools/realesrgan/realesrgan-ncnn-vulkan.exe`` in
this repo; it's small (~5 MB) and pins the model weights in
``tools/realesrgan/models/``.
"""

import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

from .exceptions import FFmpegError, OverlayError, PrerequisiteError
from .ffmpeg import check_nvenc_available, ensure_ffmpeg_available
from .logger import get_logger

logger = get_logger()


REPO_ROOT = Path(__file__).resolve().parents[2]
REALESRGAN_DIR = REPO_ROOT / "tools" / "realesrgan"
REALESRGAN_BIN = REALESRGAN_DIR / "realesrgan-ncnn-vulkan.exe"
DEFAULT_MODEL = "realesr-animevideov3-x2"
DEFAULT_SCALE = 2


def check_realesrgan_available() -> bool:
    return REALESRGAN_BIN.exists()


def ensure_realesrgan_available() -> None:
    if not check_realesrgan_available():
        raise PrerequisiteError(
            "realesrgan-ncnn-vulkan not found",
            f"Expected binary at {REALESRGAN_BIN}. Download from "
            "https://github.com/xinntao/Real-ESRGAN/releases (v0.2.5.0 or newer) "
            "and extract into tools/realesrgan/.",
        )


def _probe_framerate(video: Path) -> float:
    """Return the video's average frame rate (fps)."""
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=avg_frame_rate",
        "-of", "default=nw=1:nk=1",
        str(video),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if result.returncode != 0 or not result.stdout.strip():
        raise FFmpegError(f"ffprobe failed for {video}", stderr=result.stderr)
    num, den = result.stdout.strip().split("/")
    return float(num) / float(den) if float(den) else float(num)


def _encoder_args() -> list:
    if check_nvenc_available():
        return ["-c:v", "h264_nvenc", "-preset", "p5", "-tune", "hq", "-rc", "vbr", "-cq", "19", "-b:v", "0"]
    return ["-c:v", "libx264", "-preset", "medium", "-crf", "18"]


def upscale_video(
    src: Path,
    dst: Path,
    model: str = DEFAULT_MODEL,
    scale: int = DEFAULT_SCALE,
    timeout_per_stage: int = 7200,
) -> Path:
    """Upscale `src` into `dst` using Real-ESRGAN.

    Raises:
        OverlayError if the source is missing or dst exists.
        FFmpegError / PrerequisiteError on extract/assemble/bin issues.
    """
    src = Path(src)
    dst = Path(dst)
    if not src.exists():
        raise OverlayError(f"Source video not found: {src}")
    ensure_ffmpeg_available()
    ensure_realesrgan_available()

    dst.parent.mkdir(parents=True, exist_ok=True)
    fps = _probe_framerate(src)
    logger.info(f"Upscaling {src.name} @ {fps:.3f} fps with model={model} scale={scale}...")

    with tempfile.TemporaryDirectory(prefix="upscale_", dir=str(dst.parent)) as workdir:
        frames_in = Path(workdir) / "in"
        frames_out = Path(workdir) / "out"
        frames_in.mkdir()
        frames_out.mkdir()

        # 1. Extract frames (PNG preserves quality for the ESRGAN pass).
        extract_cmd = [
            "ffmpeg", "-hide_banner", "-y",
            "-i", str(src),
            "-vsync", "0",
            str(frames_in / "frame_%06d.png"),
        ]
        r = subprocess.run(
            extract_cmd, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout_per_stage,
        )
        if r.returncode != 0:
            raise FFmpegError(
                "Frame extraction failed", returncode=r.returncode, stderr=r.stderr
            )
        n_frames = sum(1 for _ in frames_in.iterdir())
        if n_frames == 0:
            raise FFmpegError(f"No frames extracted from {src}")
        logger.info(f"Extracted {n_frames} frames; running Real-ESRGAN batch...")

        # 2. Upscale batch. The binary takes directories when -i/-o are dirs.
        esrgan_cmd = [
            str(REALESRGAN_BIN),
            "-i", str(frames_in),
            "-o", str(frames_out),
            "-n", model,
            "-s", str(scale),
            "-f", "png",
        ]
        r = subprocess.run(
            esrgan_cmd, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout_per_stage,
        )
        if r.returncode != 0:
            raise OverlayError(
                "Real-ESRGAN upscale failed",
                (r.stderr or r.stdout)[-500:],
            )
        logger.info(f"Upscale complete; reassembling to {dst.name}...")

        # 3. Reassemble at original framerate.
        assemble_cmd = [
            "ffmpeg", "-hide_banner", "-y",
            "-framerate", f"{fps:.6f}",
            "-i", str(frames_out / "frame_%06d.png"),
        ]
        assemble_cmd.extend(_encoder_args())
        assemble_cmd.extend([
            "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
            str(dst),
        ])
        r = subprocess.run(
            assemble_cmd, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout_per_stage,
        )
        if r.returncode != 0:
            raise FFmpegError(
                "Frame reassembly failed", returncode=r.returncode, stderr=r.stderr
            )

    if not dst.exists() or dst.stat().st_size == 0:
        raise OverlayError(f"Upscaled output missing or empty: {dst}")
    logger.info(f"Upscaled → {dst.name} ({dst.stat().st_size / 1024 / 1024:.1f} MB)")
    return dst


def get_or_create_upscaled(
    visual_path: Path,
    video_id: str,
    cache_dir: Path,
) -> Path:
    """Return a cached upscaled MP4 for this visual, building it on first call."""
    cache_dir = Path(cache_dir)
    dst = cache_dir / f"upscaled_{video_id}.mp4"
    if dst.exists() and dst.stat().st_size > 0:
        logger.info(f"Using cached upscaled visual: {dst.name}")
        return dst
    return upscale_video(visual_path, dst)

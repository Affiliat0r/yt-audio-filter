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

import queue
import shutil
import subprocess
import threading
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


#: Frames one pass may hold on disk at once. Every frame is written twice —
#: once at source size, once upscaled — so ~10,000 frames at 360p is already
#: several GB in and several more out.
#:
#: This is a *peak disk* bound, not a limit on how long a video may be. Longer
#: sources are split into chunks and fed through one at a time, so the disk
#: never holds more than one chunk's worth.
MAX_UPSCALE_FRAMES = 10_000

#: Seconds of source per chunk. At 30 fps that is 1,800 frames, comfortably
#: inside a single pass even when the segment muxer overshoots to reach the
#: next keyframe.
UPSCALE_CHUNK_SECONDS = 60

#: The one length that is still refused. Chunking removes the disk wall but
#: not the clock: at roughly 14 fps of GPU throughput this is about four hours
#: of upscaling, which is past the point where anyone wants it to have started
#: silently. A 25-minute episode is ~37,000 frames, well inside it.
MAX_TOTAL_UPSCALE_FRAMES = 200_000


def _expected_frame_count(src: Path, fps: float) -> int:
    """Frames this video will produce, or 0 when ffprobe will not say."""
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "format=duration",
                "-of", "default=nw=1:nk=1",
                str(src),
            ],
            capture_output=True, text=True, timeout=30,
        )
        duration = float(result.stdout.strip())
    except Exception:  # noqa: BLE001 - a missing duration must not block a render
        return 0
    return int(duration * fps)


def _upscale_single_pass(
    src: Path,
    dst: Path,
    model: str = DEFAULT_MODEL,
    scale: int = DEFAULT_SCALE,
    timeout_per_stage: int = 7200,
) -> Path:
    """Upscale one video short enough to hold every frame on disk at once.

    The caller is responsible for keeping the input inside
    ``MAX_UPSCALE_FRAMES`` — :func:`upscale_video` does that by chunking.

    Raises:
        OverlayError if the source is missing.
        FFmpegError / PrerequisiteError on extract/assemble/bin issues.
    """
    src = Path(src)
    dst = Path(dst)
    if not src.exists():
        raise OverlayError(f"Source video not found: {src}")

    dst.parent.mkdir(parents=True, exist_ok=True)
    fps = _probe_framerate(src)
    n_expected_frames = _expected_frame_count(src, fps)
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
        try:
            r = subprocess.run(
                extract_cmd, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=timeout_per_stage,
            )
        except subprocess.TimeoutExpired as exc:
            # Windows reports TimeoutExpired.timeout as the *remaining* time,
            # which is negative once the deadline has passed — hence error
            # messages like "timed out after -73028.98 seconds". Report the
            # limit that was actually configured instead.
            raise FFmpegError(
                f"Frame extraction exceeded {timeout_per_stage}s and was abandoned",
                stderr=(
                    f"{src.name} is {n_expected_frames or 'many'} frames. Extracting "
                    f"them as PNG is only practical for short clips."
                ),
            ) from exc
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


def _segment_video(src: Path, workdir: Path, seconds: int) -> list:
    """Split ``src`` into chunk files of roughly ``seconds`` each.

    A stream copy, so the source is never re-encoded on the way in — degrading
    the picture before Real-ESRGAN sees it would defeat the point. That does
    mean the muxer can only cut on keyframes, so chunks come out uneven; the
    chunk length is chosen with enough headroom that an overshoot still fits a
    single pass.

    Audio is dropped deliberately. The chunks are reassembled from PNG frames
    and would lose it anyway; :func:`upscale_preserving_audio` puts the
    original track back on the finished video.
    """
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    pattern = str(workdir / "chunk_%04d.mp4")
    cmd = [
        "ffmpeg", "-hide_banner", "-y",
        "-i", str(src),
        "-map", "0:v:0",
        "-c", "copy",
        "-f", "segment",
        "-segment_time", str(seconds),
        "-reset_timestamps", "1",
        pattern,
    ]
    result = subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=3600
    )
    if result.returncode != 0:
        raise FFmpegError("Splitting the video into chunks failed", returncode=result.returncode, stderr=result.stderr)
    chunks = sorted(workdir.glob("chunk_*.mp4"))
    if not chunks:
        raise FFmpegError(f"Splitting produced no chunks for {src}")
    return chunks


def _concat_segments(segments: list, dst: Path) -> Path:
    """Join upscaled chunks back into one video, in the order given.

    A stream copy again: each chunk was just rebuilt frame by frame with
    identical encoder settings, so re-encoding here would be a second lossy
    pass over work that has already been paid for.
    """
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    listfile = dst.parent / f".concat_{dst.stem}.txt"
    # The concat demuxer reads paths relative to the list file's own location
    # unless they are absolute, so absolute is what goes in.
    listfile.write_text(
        "\n".join(f"file '{Path(s).resolve().as_posix()}'" for s in segments) + "\n",
        encoding="utf-8",
    )
    try:
        cmd = [
            "ffmpeg", "-hide_banner", "-y",
            "-f", "concat", "-safe", "0",
            "-i", str(listfile),
            "-c", "copy",
            "-movflags", "+faststart",
            str(dst),
        ]
        result = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=3600
        )
        if result.returncode != 0:
            raise FFmpegError("Rejoining the upscaled chunks failed", returncode=result.returncode, stderr=result.stderr)
    finally:
        listfile.unlink(missing_ok=True)
    return dst


def _probe_size(src: Path):
    """(width, height, fps) of the source."""
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height,avg_frame_rate", "-of", "csv=p=0", str(src)],
        capture_output=True, text=True, timeout=60,
    )
    if result.returncode != 0 or not result.stdout.strip():
        raise FFmpegError(f"ffprobe failed for {src}", stderr=result.stderr)
    width, height, rate = result.stdout.strip().split(",")[:3]
    num, den = rate.split("/")
    return int(width), int(height), (float(num) / float(den) if float(den) else float(num))


def _upscale_streaming(src: Path, dst: Path, upscaler, scale: int = 2) -> Path:
    """Decode, upscale and encode in one pass, with no frame touching disk.

    This is what makes the target reachable at all. The old path wrote every
    frame twice as PNG - 110 GB for an episode - and that round-trip cost 12.7
    min of the 90, enough that even a free upscaler could not have met a
    10-minute goal through it. Here the frames move through pipes as raw rgb24,
    which measured byte-identical to the PNG route.

    rgb24 specifically, because that is what the PNG path fed swscale, so the
    colour handling is unchanged rather than merely similar.
    """
    import numpy as np
    import torch

    width, height, fps = _probe_size(src)
    out_w, out_h = width * scale, height * scale
    in_bytes, out_bytes = width * height * 3, out_w * out_h * 3
    batch = getattr(upscaler, "batch", 1)

    dst.parent.mkdir(parents=True, exist_ok=True)
    decoder = subprocess.Popen(
        ["ffmpeg", "-v", "error", "-i", str(src), "-f", "rawvideo",
         "-pix_fmt", "rgb24", "-"],
        stdout=subprocess.PIPE, bufsize=in_bytes * 4,
    )
    encoder = subprocess.Popen(
        ["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
         "-s", f"{out_w}x{out_h}", "-r", f"{fps:.6f}", "-i", "-",
         *_encoder_args(), "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(dst)],
        stdin=subprocess.PIPE, bufsize=out_bytes * 2,
    )

    # Decode, infer and encode on three threads with bounded queues, so the
    # GPU is not idle while FFmpeg reads and writes. Done serially this same
    # path measured 65 fps; overlapped it measures ~99. The queues are short on
    # purpose — they exist to hide latency, not to buffer a whole episode.
    read_queue: "queue.Queue" = queue.Queue(maxsize=4)
    write_queue: "queue.Queue" = queue.Queue(maxsize=4)
    frames = 0
    failure: list = []

    def read_frames():
        try:
            while True:
                raw = decoder.stdout.read(in_bytes * batch)
                if not raw or len(raw) < in_bytes:
                    break
                n = len(raw) // in_bytes
                array = np.frombuffer(raw, np.uint8, count=n * in_bytes)
                read_queue.put(array.reshape(n, height, width, 3).copy())
        except Exception as exc:  # noqa: BLE001 - surfaced on the main thread
            failure.append(exc)
        finally:
            read_queue.put(None)

    def write_frames():
        try:
            while True:
                block = write_queue.get()
                if block is None:
                    break
                encoder.stdin.write(block)
        except Exception as exc:  # noqa: BLE001 - surfaced on the main thread
            failure.append(exc)

    reader = threading.Thread(target=read_frames, daemon=True)
    writer = threading.Thread(target=write_frames, daemon=True)
    reader.start()
    writer.start()

    try:
        with torch.no_grad():
            while True:
                array = read_queue.get()
                if array is None:
                    break
                on_gpu = torch.from_numpy(array).cuda(non_blocking=True)
                out = upscaler(on_gpu)
                write_queue.put(out.cpu().numpy().tobytes())
                frames += array.shape[0]
        write_queue.put(None)
        writer.join(timeout=600)
        reader.join(timeout=60)
        if failure:
            raise failure[0]
    finally:
        if encoder.stdin and not encoder.stdin.closed:
            encoder.stdin.close()
        encoder.wait(timeout=600)
        decoder.wait(timeout=60)

    if not dst.exists() or dst.stat().st_size == 0:
        raise OverlayError(f"Upscaled output missing or empty: {dst}")
    logger.info(f"Upscaled {frames} frames -> {dst.name} via {upscaler.backend}")
    return dst


def upscale_video(
    src: Path,
    dst: Path,
    model: str = DEFAULT_MODEL,
    scale: int = DEFAULT_SCALE,
    timeout_per_stage: int = 7200,
) -> Path:
    """Upscale ``src`` into ``dst``, splitting it up if it is long.

    Real-ESRGAN goes through the filesystem: every frame is written as PNG at
    source size and again upscaled. A 25-minute cartoon is ~37,000 frames and
    tens of gigabytes all at once, which is why this used to refuse anything
    over ``MAX_UPSCALE_FRAMES``.

    Refusing was the wrong fix. Peak disk is the constraint, not total work, so
    a long source is split into ``UPSCALE_CHUNK_SECONDS`` chunks, upscaled one
    at a time, and rejoined with a stream copy. Only the clock still bounds it:
    see ``MAX_TOTAL_UPSCALE_FRAMES``.

    Raises:
        OverlayError if the source is missing, or is past the total budget.
        FFmpegError / PrerequisiteError on split/extract/assemble/bin issues.
    """
    src = Path(src)
    dst = Path(dst)
    if not src.exists():
        raise OverlayError(f"Source video not found: {src}")
    ensure_ffmpeg_available()
    dst.parent.mkdir(parents=True, exist_ok=True)

    # The fast path first, and only demand the ncnn binary if we fall back to
    # it - a machine with TensorRT but no ncnn binary is perfectly able to
    # sharpen, and refusing there would be an own goal.
    from . import sr_backend

    width, height, _ = _probe_size(src)
    upscaler = sr_backend.make_upscaler(width, height, dst.parent, batch=1)
    if upscaler is not None:
        # No chunking: peak disk is one frame, so the length guards below do
        # not apply and neither does the 110 GB of PNG they existed to bound.
        return _upscale_streaming(src, dst, upscaler, scale=scale)

    ensure_realesrgan_available()
    fps = _probe_framerate(src)
    n_frames = _expected_frame_count(src, fps)

    if n_frames > MAX_TOTAL_UPSCALE_FRAMES:
        raise OverlayError(
            f"{src.name} is too long to upscale "
            f"({n_frames:,} frames, limit {MAX_TOTAL_UPSCALE_FRAMES:,})",
            "Real-ESRGAN runs at roughly 14 frames per second, so this would "
            "take many hours. Render without sharpening, or use a shorter "
            "source.",
        )

    # 0 means ffprobe would not give a duration. Treat that as short: one pass
    # either works or fails quickly, which beats splitting on a guess.
    if n_frames <= MAX_UPSCALE_FRAMES:
        return _upscale_single_pass(
            src, dst, model=model, scale=scale, timeout_per_stage=timeout_per_stage
        )

    logger.info(
        f"{src.name} is {n_frames:,} frames; upscaling in "
        f"{UPSCALE_CHUNK_SECONDS}s chunks to bound disk use"
    )
    with tempfile.TemporaryDirectory(prefix="chunks_", dir=str(dst.parent)) as workdir:
        raw = _segment_video(src, Path(workdir) / "in", UPSCALE_CHUNK_SECONDS)
        done = []
        for index, chunk in enumerate(raw, start=1):
            target = Path(workdir) / "out" / chunk.name
            logger.info(f"Upscaling chunk {index}/{len(raw)}: {chunk.name}")
            done.append(
                _upscale_single_pass(
                    chunk, target, model=model, scale=scale, timeout_per_stage=timeout_per_stage
                )
            )
        _concat_segments(done, dst)

    if not dst.exists() or dst.stat().st_size == 0:
        raise OverlayError(f"Upscaled output missing or empty: {dst}")
    logger.info(f"Upscaled -> {dst.name} ({dst.stat().st_size / 1024 / 1024:.1f} MB)")
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


def upscale_preserving_audio(
    src: Path,
    dst: Path,
    model: str = DEFAULT_MODEL,
    scale: int = DEFAULT_SCALE,
) -> Path:
    """Upscale ``src`` into ``dst``, carrying its audio track across.

    :func:`upscale_video` rebuilds the picture from PNG frames, so what it
    writes is silent. That is exactly right for an overlay visual, whose sound
    comes from a recitation — and exactly wrong ahead of music removal, where
    Demucs needs the original audio to separate. So the upscale runs into a
    scratch file and the source's audio is copied back on top of it.

    Both streams are copied, never re-encoded: the video was just reconstructed
    frame by frame, and putting it through another lossy pass would spend the
    GPU hour and then throw away what it bought.
    """
    src, dst = Path(src), Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    silent = dst.with_name(f"{dst.stem}_silent{dst.suffix}")

    try:
        upscale_video(src, silent, model=model, scale=scale)
        cmd = [
            "ffmpeg", "-hide_banner", "-y",
            "-i", str(silent),
            "-i", str(src),
            "-map", "0:v:0",
            # Optional on purpose: a source with no audio stream is unusual but
            # legal, and a hard map would abort the whole render over it.
            "-map", "1:a?",
            "-c", "copy",
            "-movflags", "+faststart",
            str(dst),
        ]
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=3600,
        )
        if result.returncode != 0:
            raise FFmpegError(
                "Could not put the audio back onto the upscaled video",
                returncode=result.returncode,
                stderr=result.stderr,
            )
    finally:
        silent.unlink(missing_ok=True)

    if not dst.exists() or dst.stat().st_size == 0:
        raise OverlayError(f"Upscaled output missing or empty: {dst}")
    return dst


def get_or_create_sharpened(
    src: Path,
    video_id: str,
    cache_dir: Path,
) -> Path:
    """A cached upscale of ``src`` that still has its sound, built on first call.

    Deliberately a different cache name from :func:`get_or_create_upscaled`.
    That one holds the *silent* visual the overlay pipeline wants; handing it to
    music removal instead would publish an episode with no audio at all.
    """
    cache_dir = Path(cache_dir)
    dst = cache_dir / f"sharp_{video_id}.mp4"
    if dst.exists() and dst.stat().st_size > 0:
        logger.info(f"Using cached sharpened source: {dst.name}")
        return dst
    return upscale_preserving_audio(Path(src), dst)

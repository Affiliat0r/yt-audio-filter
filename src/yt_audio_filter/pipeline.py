"""Main orchestration logic for the audio filtering pipeline."""

import multiprocessing
from pathlib import Path
from typing import Callable, Optional, Tuple

from .demucs_processor import ensure_demucs_available, isolate_vocals
from .exceptions import YTAudioFilterError
from .ffmpeg import (
    concatenate_videos,
    ensure_ffmpeg_available,
    extract_audio,
    get_audio_info,
    get_video_duration,
    remux_video,
    split_video,
)
from .logger import ProgressLogger, get_logger
from .utils import create_temp_dir, get_file_size_mb, validate_input_file

logger = get_logger()


def _process_chunk_worker(args: Tuple[Path, Path, str, str, str, Optional[int], int, bool, bool, bool, int]) -> Path:
    """
    Worker function for parallel chunk processing.

    This function is called by multiprocessing.Pool to process a single chunk.
    Each worker runs in a separate process with its own CUDA context.

    Args:
        args: Tuple of (chunk_path, output_path, device, model_name, audio_bitrate,
                       segment, shifts, watermark, fp16, compile_model, chunk_index)

    Returns:
        Path to the processed chunk
    """
    chunk_path, output_path, device, model_name, audio_bitrate, segment, shifts, watermark, fp16, compile_model, chunk_index = args

    # Import torch here to ensure each process initializes CUDA independently
    import torch
    if device != "cpu" and torch.cuda.is_available():
        torch.cuda.empty_cache()
        logger.debug(f"Worker {chunk_index}: Initialized CUDA context")

    # Process the chunk
    try:
        result = _process_single_chunk(
            input_path=chunk_path,
            output_path=output_path,
            device=device,
            model_name=model_name,
            audio_bitrate=audio_bitrate,
            segment=segment,
            shifts=shifts,
            watermark=watermark,
            fp16=fp16,
            compile_model=compile_model,
        )

        # Clear CUDA cache after processing
        if device != "cpu" and torch.cuda.is_available():
            torch.cuda.empty_cache()
            logger.debug(f"Worker {chunk_index}: Cleared CUDA cache")

        return result
    except Exception as e:
        logger.error(f"Worker {chunk_index}: Failed to process chunk: {e}")
        raise


def validate_prerequisites() -> None:
    """
    Validate that all required tools are available.

    Raises:
        PrerequisiteError: If any required tool is missing
    """
    ensure_ffmpeg_available()
    ensure_demucs_available()


def _process_single_chunk(
    input_path: Path,
    output_path: Path,
    device: str,
    model_name: str,
    audio_bitrate: str,
    segment: Optional[int],
    shifts: int,
    watermark: bool,
    fp16: bool,
    compile_model: bool,
) -> Path:
    """
    Process a single video chunk (internal helper function).

    This function processes one video chunk through the full pipeline
    without chunking logic.

    Returns:
        Path to the processed chunk
    """
    # Create temp directory for intermediate files
    with create_temp_dir() as temp_dir:
        # Stage 1: Extract audio from video
        audio_wav = temp_dir / "audio.wav"
        extract_audio(input_path, audio_wav)

        # Stage 2: Isolate vocals using Demucs AI
        vocals_wav = temp_dir / "vocals.wav"

        # Clear CUDA cache before heavy processing
        if device != "cpu":
            try:
                import torch
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
                    logger.debug("Cleared CUDA cache before vocal isolation")
            except Exception:
                pass

        isolate_vocals(
            audio_wav,
            vocals_wav,
            device=device,
            model_name=model_name,
            progress_callback=None,  # No progress for individual chunks
            segment=segment,
            shifts=shifts,
            fp16=fp16,
            compile_model=compile_model,
        )

        # Stage 3: Remux video with processed vocals
        remux_video(
            input_path,
            vocals_wav,
            output_path,
            audio_bitrate=audio_bitrate,
            watermark=watermark,
        )

        return output_path


def _process_video_chunked(
    input_path: Path,
    output_path: Path,
    device: str,
    model_name: str,
    audio_bitrate: str,
    progress_callback: Optional[Callable[[str, int], None]],
    segment: Optional[int],
    shifts: int,
    watermark: bool,
    fp16: bool,
    compile_model: bool,
    chunk_duration: int,
    parallel_chunks: int = 1,
) -> Path:
    """
    Process a video using chunked approach for consistent high-speed performance.

    This splits the video into chunks, processes each chunk (sequentially or in parallel),
    then concatenates the results.

    Args:
        parallel_chunks: Number of chunks to process in parallel (1 = sequential, 2+ = parallel)

    Returns:
        Path to the final processed video
    """
    if parallel_chunks > 1:
        logger.info(
            f"Using chunked processing with {chunk_duration}s ({chunk_duration/60:.0f} min) chunks "
            f"({parallel_chunks} chunks in parallel)"
        )
    else:
        logger.info(f"Using chunked processing with {chunk_duration}s ({chunk_duration/60:.0f} min) chunks")

    # Create temp directory for chunks and processed chunks
    with create_temp_dir(prefix="chunks_") as chunks_dir:
        # Step 1: Split video into chunks
        logger.info("Splitting video into chunks...")
        if progress_callback:
            progress_callback("Split Video", 0)

        chunk_paths = split_video(input_path, chunks_dir, chunk_duration=chunk_duration)

        if progress_callback:
            progress_callback("Split Video", 100)

        # Step 2: Process each chunk (sequential or parallel)
        processed_chunks = []
        num_chunks = len(chunk_paths)

        if parallel_chunks > 1:
            # Parallel processing using multiprocessing
            logger.info(f"Processing {num_chunks} chunks with {parallel_chunks} workers in parallel...")

            # Prepare arguments for each chunk
            chunk_args = []
            for i, chunk_path in enumerate(chunk_paths):
                processed_chunk_path = chunks_dir / f"processed_{chunk_path.name}"
                chunk_args.append((
                    chunk_path,
                    processed_chunk_path,
                    device,
                    model_name,
                    audio_bitrate,
                    segment,
                    shifts,
                    watermark,
                    fp16,
                    compile_model,
                    i + 1,  # chunk index for logging
                ))
                processed_chunks.append(processed_chunk_path)

            # Use 'spawn' method for multiprocessing to avoid CUDA context issues
            # This creates fresh processes without inheriting CUDA state
            try:
                multiprocessing.set_start_method('spawn', force=True)
            except RuntimeError:
                # Start method can only be set once; if already set, that's fine
                pass

            # Create a pool of workers
            with multiprocessing.Pool(processes=parallel_chunks) as pool:
                # Process chunks in parallel
                results = []
                for i, args in enumerate(chunk_args):
                    result = pool.apply_async(_process_chunk_worker, (args,))
                    results.append(result)

                # Wait for all chunks to complete
                for i, result in enumerate(results):
                    result.get()  # This blocks until the chunk is done
                    logger.info(f"Completed chunk {i+1}/{num_chunks}")

                    if progress_callback:
                        overall_progress = int(((i + 1) / num_chunks) * 100)
                        progress_callback("Process Chunks", overall_progress)

            logger.info(f"All {num_chunks} chunks processed")

        else:
            # Sequential processing (original behavior)
            for i, chunk_path in enumerate(chunk_paths):
                logger.info(f"Processing chunk {i+1}/{num_chunks}: {chunk_path.name}")

                if progress_callback:
                    overall_progress = int((i / num_chunks) * 100)
                    progress_callback("Process Chunks", overall_progress)

                # Process this chunk
                processed_chunk_path = chunks_dir / f"processed_{chunk_path.name}"

                _process_single_chunk(
                    input_path=chunk_path,
                    output_path=processed_chunk_path,
                    device=device,
                    model_name=model_name,
                    audio_bitrate=audio_bitrate,
                    segment=segment,
                    shifts=shifts,
                    watermark=watermark,
                    fp16=fp16,
                    compile_model=compile_model,
                )

                processed_chunks.append(processed_chunk_path)
                logger.info(f"Completed chunk {i+1}/{num_chunks}")

            if progress_callback:
                progress_callback("Process Chunks", 100)

        # Step 3: Concatenate all processed chunks
        logger.info("Concatenating processed chunks...")
        if progress_callback:
            progress_callback("Concatenate Chunks", 0)

        concatenate_videos(processed_chunks, output_path)

        if progress_callback:
            progress_callback("Concatenate Chunks", 100)

        # Clean up original chunks (processed chunks will be cleaned by temp_dir context)
        for chunk_path in chunk_paths:
            try:
                chunk_path.unlink()
            except Exception:
                pass

        # Log output info
        output_size = get_file_size_mb(output_path)
        logger.info(f"Output saved: {output_path.name} ({output_size:.1f} MB)")

        return output_path


def process_video(
    input_path: Path,
    output_path: Path,
    device: str = "auto",
    model_name: str = "htdemucs",
    audio_bitrate: str = "192k",
    progress_callback: Optional[Callable[[str, int], None]] = None,
    segment: Optional[int] = None,
    shifts: int = 1,
    watermark: bool = False,
    fp16: bool = False,
    compile_model: bool = False,
    chunk_duration: Optional[int] = None,
    parallel_chunks: int = 1,
) -> Path:
    """
    Process a video to isolate vocals and remove background music.

    This is the main entry point for the audio filtering pipeline.

    Args:
        input_path: Path to input MP4 file
        output_path: Path for output MP4 file
        device: Device for AI processing ("auto", "cpu", "cuda")
        model_name: Demucs model variant (default: "htdemucs")
        audio_bitrate: Output audio bitrate (default: "192k")
        progress_callback: Optional callback(stage_name, progress_percent)
        segment: Segment size in seconds for GPU processing (None = auto)
        shifts: Number of random shifts for augmentation (default: 1)
        watermark: Add a small watermark to help avoid Content ID (default: False)
        fp16: Use mixed precision (FP16) for faster GPU inference (default: False)
        compile_model: Compile model with torch.compile() for faster inference (default: False)
        chunk_duration: Split video into chunks of this many seconds (None = auto, 0 = disabled)
        parallel_chunks: Number of chunks to process in parallel (default: 1 = sequential)

    Returns:
        Path to the processed output file

    Raises:
        YTAudioFilterError: On any processing failure
    """
    progress = ProgressLogger()

    # Log input file info
    input_size = get_file_size_mb(input_path)
    logger.info(f"Processing: {input_path.name} ({input_size:.1f} MB)")

    # Validate prerequisites
    logger.debug("Validating prerequisites...")
    validate_prerequisites()

    # Validate input file
    validate_input_file(input_path)

    # Get audio info for logging
    audio_info = get_audio_info(input_path)
    if audio_info:
        logger.debug(
            f"Audio info: {audio_info.get('sample_rate', 'unknown')} Hz, "
            f"{audio_info.get('channels', 'unknown')} channels, "
            f"duration: {audio_info.get('duration', 0):.1f}s"
        )

    # Check if we should use chunked processing
    try:
        video_duration = get_video_duration(input_path)
        logger.debug(f"Video duration: {video_duration:.1f}s ({video_duration/60:.1f} minutes)")
    except Exception as e:
        logger.warning(f"Could not determine video duration: {e}")
        video_duration = 0

    # Determine if chunking should be used
    use_chunking = False
    effective_chunk_duration = 900  # Default: 15 minutes

    if chunk_duration is not None:
        # User explicitly set chunk_duration
        if chunk_duration == 0:
            # User disabled chunking
            use_chunking = False
        else:
            # User specified a chunk size
            use_chunking = True
            effective_chunk_duration = chunk_duration
    elif video_duration > 1800:  # Auto-enable for videos > 30 minutes
        use_chunking = True
        effective_chunk_duration = 900  # 15 minutes
        logger.info(
            f"Long video detected ({video_duration/60:.1f} min). "
            f"Auto-enabling chunked processing with {effective_chunk_duration}s chunks for consistent performance."
        )

    # If chunking is enabled, use chunked processing
    if use_chunking and video_duration > effective_chunk_duration:
        return _process_video_chunked(
            input_path=input_path,
            output_path=output_path,
            device=device,
            model_name=model_name,
            audio_bitrate=audio_bitrate,
            progress_callback=progress_callback,
            segment=segment,
            shifts=shifts,
            watermark=watermark,
            fp16=fp16,
            compile_model=compile_model,
            chunk_duration=effective_chunk_duration,
            parallel_chunks=parallel_chunks,
        )

    # Otherwise, use standard single-video processing
    # Create temp directory for intermediate files
    with create_temp_dir() as temp_dir:
        try:
            # Stage 1: Extract audio from video
            progress.start_stage("Extract Audio")
            if progress_callback:
                progress_callback("Extract Audio", 0)

            audio_wav = temp_dir / "audio.wav"
            extract_audio(input_path, audio_wav)

            if progress_callback:
                progress_callback("Extract Audio", 100)
            progress.complete_stage("Extract Audio")

            # Stage 2: Isolate vocals using Demucs AI
            progress.start_stage("Isolate Vocals")
            if progress_callback:
                progress_callback("Isolate Vocals", 0)

            vocals_wav = temp_dir / "vocals.wav"

            # Clear CUDA cache before heavy processing
            if device != "cpu":
                try:
                    import torch
                    if torch.cuda.is_available():
                        torch.cuda.empty_cache()
                        logger.debug("Cleared CUDA cache before vocal isolation")
                except Exception:
                    pass

            # Create a sub-callback for granular Demucs progress
            def demucs_progress(info: dict):
                if progress_callback:
                    progress_callback("Isolate Vocals", info.get('percent', 0), info)

            isolate_vocals(
                audio_wav,
                vocals_wav,
                device=device,
                model_name=model_name,
                progress_callback=demucs_progress,
                segment=segment,
                shifts=shifts,
                fp16=fp16,
                compile_model=compile_model,
            )

            if progress_callback:
                progress_callback("Isolate Vocals", 100)
            progress.complete_stage("Isolate Vocals")

            # Stage 3: Remux video with processed vocals
            progress.start_stage("Remux Video")
            if progress_callback:
                progress_callback("Remux Video", 0)

            if watermark:
                logger.info("Adding watermark to video...")

            remux_video(
                input_path,
                vocals_wav,
                output_path,
                audio_bitrate=audio_bitrate,
                watermark=watermark,
            )

            if progress_callback:
                progress_callback("Remux Video", 100)
            progress.complete_stage("Remux Video")

            # Log output info
            output_size = get_file_size_mb(output_path)
            logger.info(f"Output saved: {output_path.name} ({output_size:.1f} MB)")

            return output_path

        except YTAudioFilterError:
            # Re-raise our custom errors
            raise
        except Exception as e:
            # Wrap unexpected errors
            raise YTAudioFilterError(f"Unexpected error during processing: {e}")

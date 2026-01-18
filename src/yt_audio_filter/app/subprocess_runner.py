"""Subprocess runner that calls run_filter.bat for reliable processing.

This module runs video processing by calling run_filter.bat directly,
which is proven to work 100% reliably. This ensures the exact same
execution path as running from the terminal.

Supports both single-video and parallel batch processing.
"""

import os
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable, Dict, List, Optional

# Path to run_filter.bat (in the project root)
PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
RUN_FILTER_BAT = PROJECT_ROOT / "run_filter.bat"


def run_processing_subprocess(
    url: str,
    privacy: str = "public",
    device: str = "cuda",
    bitrate: str = "192k",
    progress_callback: Optional[Callable[[dict], None]] = None,
) -> dict:
    """
    Run video processing by calling run_filter.bat directly.

    This uses the exact same execution path as running from terminal,
    which is proven to work 100% reliably.

    Note: Privacy is fixed to 'public' as hardcoded in run_filter.bat.
    Device and bitrate settings are ignored - the CLI auto-detects CUDA.

    Args:
        url: YouTube video URL
        privacy: Ignored - run_filter.bat uses 'public'
        device: Ignored - CLI auto-detects CUDA
        bitrate: Ignored - CLI uses default
        progress_callback: Optional callback for progress updates

    Returns:
        dict with keys: success, video_id, error, uploaded_url
    """
    # Call run_filter.bat with just the URL
    # The bat file handles: FFmpeg path, venv activation, --upload --privacy public
    cmd = [str(RUN_FILTER_BAT), url]

    result = {
        "success": False,
        "video_id": None,
        "error": None,
        "uploaded_url": None,
    }

    try:
        # Run run_filter.bat as a subprocess
        # Use shell=True on Windows to properly execute .bat files
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=0,  # Unbuffered
            cwd=str(PROJECT_ROOT),  # Run from project root
            shell=True,  # Required for .bat files on Windows
        )

        # Read output character by character to handle tqdm's \r progress updates
        # tqdm uses \r (carriage return) to update the same line, not \n
        output_lines = []
        current_line = ""

        while True:
            char = process.stdout.read(1)
            if not char:
                break

            if char in ('\r', '\n'):
                if current_line.strip():
                    output_lines.append(current_line)
                    # Parse progress from output
                    if progress_callback:
                        progress_info = parse_cli_output(current_line)
                        if progress_info:
                            progress_callback(progress_info)
                current_line = ""
            else:
                current_line += char

        # Don't forget the last line if it doesn't end with newline
        if current_line.strip():
            output_lines.append(current_line)
            if progress_callback:
                progress_info = parse_cli_output(current_line)
                if progress_info:
                    progress_callback(progress_info)

        process.wait()
        full_output = '\n'.join(output_lines)

        if process.returncode == 0:
            result["success"] = True
            # Extract video ID from output
            video_id = extract_video_id_from_output(full_output)
            if video_id:
                result["video_id"] = video_id
                result["uploaded_url"] = f"https://youtube.com/watch?v={video_id}"
        else:
            result["error"] = full_output or "Processing failed"

    except Exception as e:
        result["error"] = str(e)

    return result


def parse_cli_output(line: str) -> Optional[dict]:
    """Parse CLI output to extract progress info."""
    line = line.strip()

    # Download progress
    if "[download]" in line and "%" in line:
        try:
            pct_str = line.split("%")[0].split()[-1]
            pct = float(pct_str)
            return {"stage": "Download", "percent": int(pct * 0.2), "detail": line}
        except:
            pass

    # Stage markers
    if "Extract Audio" in line or "Extracting audio" in line:
        return {"stage": "Extract Audio", "percent": 20, "detail": line}

    if "Isolate Vocals" in line or "Running vocal separation" in line:
        return {"stage": "Isolate Vocals", "percent": 40, "detail": line}

    # Demucs progress (e.g., "  5%|###       | 150.0/3000.0 [00:30<09:30, 5.00seconds/s]")
    if "%" in line and "/" in line and "seconds" in line.lower():
        try:
            pct_part = line.split("%")[0].strip().split()[-1]
            pct = float(pct_part)
            # Scale to 40-70 range
            scaled_pct = 40 + int(pct * 0.3)
            return {"stage": "Isolate Vocals", "percent": scaled_pct, "detail": line}
        except:
            pass

    if "Remux" in line or "Combining" in line:
        return {"stage": "Remux Video", "percent": 70, "detail": line}

    if "Upload" in line or "Uploading" in line:
        return {"stage": "Upload", "percent": 80, "detail": line}

    if "Successfully uploaded" in line or "Video uploaded" in line:
        return {"stage": "Complete", "percent": 100, "detail": line}

    return None


def extract_video_id_from_output(output: str) -> Optional[str]:
    """Extract the uploaded video ID from CLI output."""
    import re

    # Look for YouTube URL patterns
    patterns = [
        r'youtube\.com/watch\?v=([a-zA-Z0-9_-]{11})',
        r'youtu\.be/([a-zA-Z0-9_-]{11})',
        r'Video ID:\s*([a-zA-Z0-9_-]{11})',
        r'Uploaded:\s*([a-zA-Z0-9_-]{11})',
    ]

    for pattern in patterns:
        match = re.search(pattern, output)
        if match:
            return match.group(1)

    return None


def run_in_background(
    url: str,
    privacy: str,
    device: str,
    bitrate: str,
    on_progress: Callable[[dict], None],
    on_complete: Callable[[dict], None],
):
    """Run processing in a background thread using subprocess.

    Args:
        url: YouTube URL
        privacy: Upload privacy setting
        device: Processing device
        bitrate: Audio bitrate
        on_progress: Callback for progress updates
        on_complete: Callback when processing completes
    """
    def worker():
        result = run_processing_subprocess(
            url=url,
            privacy=privacy,
            device=device,
            bitrate=bitrate,
            progress_callback=on_progress,
        )
        on_complete(result)

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    return thread


class ParallelBatchProcessor:
    """Parallel batch processor that spawns multiple run_filter.bat subprocesses.

    Uses a worker pool pattern with configurable concurrency.
    Each video gets its own subprocess for 100% reliability.
    """

    def __init__(
        self,
        max_workers: int = 2,
        on_item_progress: Optional[Callable[[str, dict], None]] = None,
        on_item_complete: Optional[Callable[[str, dict], None]] = None,
        on_item_start: Optional[Callable[[str], None]] = None,
    ):
        """Initialize the batch processor.

        Args:
            max_workers: Maximum number of parallel subprocesses (default: 2)
            on_item_progress: Callback(item_id, progress_info) for progress updates
            on_item_complete: Callback(item_id, result) when an item completes
            on_item_start: Callback(item_id) when an item starts processing
        """
        self.max_workers = max_workers
        self.on_item_progress = on_item_progress
        self.on_item_complete = on_item_complete
        self.on_item_start = on_item_start
        self._stop_flag = threading.Event()
        self._executor: Optional[ThreadPoolExecutor] = None
        self._active_futures: Dict = {}
        self._lock = threading.Lock()

    def stop(self):
        """Signal the processor to stop accepting new items."""
        self._stop_flag.set()

    def is_stopped(self) -> bool:
        """Check if stop has been requested."""
        return self._stop_flag.is_set()

    def reset(self):
        """Reset the stop flag to allow reuse."""
        self._stop_flag.clear()

    def _process_single_item(self, item_id: str, url: str) -> dict:
        """Process a single item via run_filter.bat subprocess.

        Args:
            item_id: Unique identifier for this item
            url: YouTube URL to process

        Returns:
            Result dict with success, video_id, error, uploaded_url
        """
        # Notify that processing is starting
        if self.on_item_start:
            self.on_item_start(item_id)

        def progress_callback(info):
            if self.on_item_progress:
                self.on_item_progress(item_id, info)

        result = run_processing_subprocess(
            url=url,
            progress_callback=progress_callback,
        )

        if self.on_item_complete:
            self.on_item_complete(item_id, result)

        return result

    def process_batch(
        self,
        items: List[Dict[str, str]],
    ) -> Dict[str, dict]:
        """Process a batch of items in parallel.

        Args:
            items: List of dicts with 'id' and 'url' keys

        Returns:
            Dict mapping item_id to result dict
        """
        results = {}

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            self._executor = executor

            # Submit all items
            futures = {}
            for item in items:
                if self._stop_flag.is_set():
                    break
                future = executor.submit(
                    self._process_single_item,
                    item["id"],
                    item["url"],
                )
                futures[future] = item["id"]

            # Collect results as they complete
            for future in as_completed(futures):
                item_id = futures[future]
                try:
                    result = future.result()
                    results[item_id] = result
                except Exception as e:
                    results[item_id] = {
                        "success": False,
                        "video_id": None,
                        "error": str(e),
                        "uploaded_url": None,
                    }

        self._executor = None
        return results

    def start_background_processor(
        self,
        get_next_items: Callable[[], List[Dict[str, str]]],
        on_all_complete: Optional[Callable[[], None]] = None,
    ) -> threading.Thread:
        """Start a background thread that continuously processes items.

        This creates a collector pattern that:
        1. Polls for pending items via get_next_items()
        2. Spawns up to max_workers parallel subprocesses
        3. Continues until no more items or stop() is called

        Args:
            get_next_items: Function that returns list of {'id': ..., 'url': ...}
                           Should return empty list when no more items
            on_all_complete: Optional callback when all processing is done

        Returns:
            The background thread (already started)
        """
        def worker():
            active_ids = set()

            with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                self._executor = executor
                futures = {}

                while not self._stop_flag.is_set():
                    # Clean up completed futures
                    completed = [f for f in futures if f.done()]
                    for future in completed:
                        item_id = futures.pop(future)
                        active_ids.discard(item_id)

                    # Calculate available slots
                    slots_available = self.max_workers - len(futures)

                    if slots_available > 0:
                        # Get next items (excluding already active)
                        items = get_next_items()
                        new_items = [
                            item for item in items
                            if item["id"] not in active_ids
                        ][:slots_available]

                        # Submit new items
                        for item in new_items:
                            future = executor.submit(
                                self._process_single_item,
                                item["id"],
                                item["url"],
                            )
                            futures[future] = item["id"]
                            active_ids.add(item["id"])

                    # Check if done (no active and no pending)
                    if len(futures) == 0:
                        items = get_next_items()
                        if not items:
                            break

                    time.sleep(1)  # Check periodically

                # Wait for remaining futures when stopping
                for future in futures:
                    try:
                        future.result(timeout=1)
                    except Exception:
                        pass

            self._executor = None

            if on_all_complete:
                on_all_complete()

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        return thread


def create_queue_processor(
    queue_manager,
    config,
    stop_flag: threading.Event,
) -> threading.Thread:
    """Create and start a queue processor using run_filter.bat.

    This is a convenience function that integrates with QueueManager.

    Args:
        queue_manager: QueueManager instance
        config: AppConfig with max_parallel_workers
        stop_flag: Event to signal stopping

    Returns:
        Background thread (already started)
    """
    from yt_audio_filter.app.state.queue import QueueStatus

    def on_item_progress(item_id: str, info: dict):
        """Update queue item with progress."""
        stage = info.get("stage", "Processing")
        pct = info.get("percent", 0)
        queue_manager.update_status(
            item_id,
            QueueStatus.PROCESSING,
            current_stage=stage,
            progress=pct,
        )

    def on_item_complete(item_id: str, result: dict):
        """Update queue item when complete."""
        if result["success"]:
            queue_manager.update_status(
                item_id,
                QueueStatus.COMPLETED,
                progress=100,
                current_stage="Complete",
                uploaded_url=result.get("uploaded_url", ""),
            )
        else:
            queue_manager.update_status(
                item_id,
                QueueStatus.FAILED,
                error_message=result.get("error", "Unknown error"),
            )

    # Track items that are currently being processed to prevent double-pickup
    processing_ids = set()
    processing_lock = threading.Lock()

    def get_next_items():
        """Get pending items from queue."""
        if stop_flag.is_set():
            return []
        pending = queue_manager.get_next_pending_batch(config.max_parallel_workers)
        # Return only items not already being processed
        with processing_lock:
            result = [
                {"id": item.id, "url": item.url}
                for item in pending
                if item.id not in processing_ids
            ]
        return result

    def on_item_start(item_id: str):
        """Called when an item actually starts processing."""
        with processing_lock:
            processing_ids.add(item_id)
        queue_manager.update_status(
            item_id,
            QueueStatus.PROCESSING,
            current_stage="Starting...",
            progress=0,
        )

    def on_item_finish(item_id: str):
        """Called when an item finishes (success or fail)."""
        with processing_lock:
            processing_ids.discard(item_id)

    # Wrap callbacks to track start/finish
    original_on_complete = on_item_complete

    def on_item_complete_wrapper(item_id: str, result: dict):
        on_item_finish(item_id)
        original_on_complete(item_id, result)

    processor = ParallelBatchProcessor(
        max_workers=config.max_parallel_workers,
        on_item_progress=on_item_progress,
        on_item_complete=on_item_complete_wrapper,
        on_item_start=on_item_start,
    )

    def on_all_complete():
        stop_flag.set()

    # Link stop flags
    def check_stop():
        while not stop_flag.is_set():
            time.sleep(0.5)
        processor.stop()

    stop_checker = threading.Thread(target=check_stop, daemon=True)
    stop_checker.start()

    return processor.start_background_processor(
        get_next_items=get_next_items,
        on_all_complete=on_all_complete,
    )

"""
Queue Page - Batch processing queue management with parallel processing.
"""

import streamlit as st
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent.parent))

from yt_audio_filter.app.state.queue import QueueManager, QueueStatus
from yt_audio_filter.app.state.config import load_config
from yt_audio_filter.app.components.progress import render_queue_item_progress, render_queue_stats

st.set_page_config(page_title="Queue - YT Audio Filter", page_icon="\U0001f4cb", layout="wide")

# Initialize state
if "queue" not in st.session_state:
    st.session_state.queue = QueueManager()

if "config" not in st.session_state:
    st.session_state.config = load_config()

if "queue_running" not in st.session_state:
    st.session_state.queue_running = False

if "processing_thread" not in st.session_state:
    st.session_state.processing_thread = None

# Shared stop flag for background threads (can't use session_state in threads)
if "stop_flag" not in st.session_state:
    st.session_state.stop_flag = threading.Event()


def process_queue_item(queue: QueueManager, item_id: str, url: str, config):
    """Process a single queue item."""
    from yt_audio_filter.youtube import download_youtube_video
    from yt_audio_filter.pipeline import process_video
    from yt_audio_filter.uploader import upload_to_youtube
    from yt_audio_filter.utils import create_temp_dir

    try:
        queue.update_status(item_id, QueueStatus.PROCESSING, current_stage="Downloading video...", progress=0)

        with create_temp_dir(prefix="yt_queue_") as temp_dir:
            # Download
            def download_progress(info):
                pct = info.get("percent", 0)
                queue.update_status(
                    item_id,
                    QueueStatus.PROCESSING,
                    current_stage=f"Downloading... {int(pct)}%",
                    progress=int(pct * 0.2),
                )

            metadata = download_youtube_video(url, temp_dir, progress_callback=download_progress)
            queue.update_status(item_id, QueueStatus.PROCESSING, current_stage="Download complete", progress=20)

            # Process
            output_path = temp_dir / f"{metadata.video_id}_filtered.mp4"

            def pipeline_progress(stage: str, pct: int, info: dict = None):
                base = {"Extract Audio": 20, "Isolate Vocals": 40, "Remux Video": 70}.get(stage, 20)
                stage_weight = {"Extract Audio": 20, "Isolate Vocals": 30, "Remux Video": 10}.get(stage, 10)
                total_pct = base + int(pct * stage_weight / 100)

                # Build verbose stage display
                if stage == "Isolate Vocals" and info:
                    # Format like tqdm: "12.3/456.7 [00:18<01:29, 1.50s/s]"
                    current = info.get('current', 0)
                    total = info.get('total', 0)
                    elapsed = info.get('elapsed_seconds', 0)
                    remaining = info.get('remaining_seconds', 0)
                    rate = info.get('rate', 0)

                    # Format time as MM:SS or HH:MM:SS
                    def fmt_time(secs):
                        if secs <= 0:
                            return "??:??"
                        secs = int(secs)
                        if secs >= 3600:
                            return f"{secs//3600}:{(secs%3600)//60:02d}:{secs%60:02d}"
                        return f"{secs//60:02d}:{secs%60:02d}"

                    rate_str = f"{rate:.2f}s/s" if rate else "?s/s"
                    stage_display = (
                        f"AI vocals: {current:.1f}/{total:.1f}s "
                        f"[{fmt_time(elapsed)}<{fmt_time(remaining)}, {rate_str}]"
                    )
                elif stage == "Isolate Vocals" and pct == 100:
                    stage_display = "AI vocals complete!"
                elif stage == "Isolate Vocals":
                    stage_display = "AI processing vocals..."
                else:
                    stage_display = {
                        "Extract Audio": f"Extracting audio... {pct}%",
                        "Remux Video": f"Remuxing video... {pct}%",
                    }.get(stage, f"{stage}... {pct}%")

                queue.update_status(
                    item_id,
                    QueueStatus.PROCESSING,
                    current_stage=stage_display,
                    progress=total_pct,
                )

            process_video(
                metadata.file_path,
                output_path,
                device=config.device,
                model_name=config.model_name,
                audio_bitrate=config.audio_bitrate,
                progress_callback=pipeline_progress,
            )

            # Upload
            queue.update_status(item_id, QueueStatus.PROCESSING, current_stage="Uploading to YouTube...", progress=80)

            video_id = upload_to_youtube(
                video_path=output_path,
                original_metadata=metadata,
                privacy=config.default_privacy,
            )

            uploaded_url = f"https://youtube.com/watch?v={video_id}"
            queue.update_status(
                item_id,
                QueueStatus.COMPLETED,
                progress=100,
                current_stage="Complete - Uploaded!",
                output_path=str(output_path),
                uploaded_url=uploaded_url,
            )

    except Exception as e:
        queue.update_status(item_id, QueueStatus.FAILED, current_stage="Failed", error_message=str(e))


def run_queue_processor(queue: QueueManager, config, stop_flag: threading.Event):
    """Background thread to process queue items in parallel."""
    max_workers = config.max_parallel_workers

    # Track active futures to manage parallel jobs
    active_item_ids = set()

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {}

        while not stop_flag.is_set():
            # Clean up completed futures
            completed_futures = [f for f in futures if f.done()]
            for future in completed_futures:
                item_id = futures.pop(future)
                active_item_ids.discard(item_id)

            # Calculate available slots
            slots_available = max_workers - len(futures)

            if slots_available > 0:
                # Get pending items that aren't already being processed
                pending_items = queue.get_next_pending_batch(slots_available + len(active_item_ids))
                new_items = [
                    item for item in pending_items
                    if item.id not in active_item_ids
                ][:slots_available]

                if new_items:
                    # Submit new jobs
                    for item in new_items:
                        future = executor.submit(process_queue_item, queue, item.id, item.url, config)
                        futures[future] = item.id
                        active_item_ids.add(item.id)

            # Check if we should stop (no active jobs and no pending)
            if len(futures) == 0 and queue.stats()["pending"] == 0:
                stop_flag.set()  # Signal we're done
                break

            time.sleep(1)  # Check status periodically

        # Wait for remaining jobs when stopping
        for future in futures:
            try:
                future.result(timeout=1)
            except Exception:
                pass


def main():
    st.title("\U0001f4cb Processing Queue")
    st.caption("Manage and monitor your video processing queue")

    # Queue controls
    col1, col2, col3, col4 = st.columns(4)

    # Check if processing is still active
    is_running = (
        st.session_state.queue_running and
        not st.session_state.stop_flag.is_set()
    )

    with col1:
        if is_running:
            if st.button("\u23f8\ufe0f Pause Queue", use_container_width=True):
                st.session_state.stop_flag.set()  # Signal thread to stop
                st.session_state.queue_running = False
                st.rerun()
        else:
            pending = st.session_state.queue.stats()["pending"]
            if pending > 0:
                if st.button("\u25b6\ufe0f Start Queue", type="primary", use_container_width=True):
                    st.session_state.stop_flag.clear()  # Reset stop flag
                    st.session_state.queue_running = True
                    # Pass queue, config, and stop_flag to thread
                    thread = threading.Thread(
                        target=run_queue_processor,
                        args=(
                            st.session_state.queue,
                            st.session_state.config,
                            st.session_state.stop_flag,
                        ),
                        daemon=True,
                    )
                    thread.start()
                    st.session_state.processing_thread = thread
                    st.rerun()
            else:
                st.button("\u25b6\ufe0f Start Queue", disabled=True, use_container_width=True)

    with col2:
        if st.button("\U0001f5d1\ufe0f Clear Completed", use_container_width=True):
            count = st.session_state.queue.clear_completed()
            if count > 0:
                st.success(f"Cleared {count} items")
                st.rerun()

    with col3:
        if st.button("\u274c Cancel All", use_container_width=True):
            st.session_state.stop_flag.set()  # Signal thread to stop
            st.session_state.queue_running = False
            count = st.session_state.queue.cancel_all()
            if count > 0:
                st.warning(f"Cancelled {count} items")
                st.rerun()

    with col4:
        if st.button("\U0001f504 Refresh", use_container_width=True):
            st.rerun()

    st.divider()

    # Queue statistics
    stats = st.session_state.queue.stats()
    render_queue_stats(stats)

    st.divider()

    # Queue items
    items = st.session_state.queue.get_all()

    if not items:
        st.info("Queue is empty. Add videos from the Process or Channel pages.")
    else:
        # Group by status
        processing = [i for i in items if i.status == QueueStatus.PROCESSING]
        pending = [i for i in items if i.status == QueueStatus.PENDING]
        completed = [i for i in items if i.status == QueueStatus.COMPLETED]
        failed = [i for i in items if i.status == QueueStatus.FAILED]
        cancelled = [i for i in items if i.status == QueueStatus.CANCELLED]

        # Currently processing
        if processing:
            max_workers = st.session_state.config.max_parallel_workers
            st.subheader(f"\U0001f504 Processing ({len(processing)}/{max_workers} workers)")
            for item in processing:
                render_queue_item_progress(
                    item_id=item.id,
                    title=item.title,
                    thumbnail_url=item.thumbnail_url,
                    status=item.status.value,
                    progress=item.progress,
                    current_stage=item.current_stage,
                    on_cancel=lambda id: (
                        st.session_state.queue.update_status(id, QueueStatus.CANCELLED),
                        st.rerun(),
                    ),
                )

        # Pending
        if pending:
            st.subheader(f"\U0001f7e1 Pending ({len(pending)})")
            for item in pending:
                render_queue_item_progress(
                    item_id=item.id,
                    title=item.title,
                    thumbnail_url=item.thumbnail_url,
                    status=item.status.value,
                    progress=0,
                    current_stage="",
                    on_remove=lambda id: (
                        st.session_state.queue.remove(id),
                        st.rerun(),
                    ),
                )

        # Completed
        if completed:
            with st.expander(f"\u2705 Completed ({len(completed)})", expanded=False):
                for item in completed:
                    render_queue_item_progress(
                        item_id=item.id,
                        title=item.title,
                        thumbnail_url=item.thumbnail_url,
                        status=item.status.value,
                        progress=100,
                        current_stage="Complete",
                        uploaded_url=item.uploaded_url,
                    )

        # Failed
        if failed:
            with st.expander(f"\u274c Failed ({len(failed)})", expanded=True):
                for item in failed:
                    render_queue_item_progress(
                        item_id=item.id,
                        title=item.title,
                        thumbnail_url=item.thumbnail_url,
                        status=item.status.value,
                        progress=0,
                        current_stage="",
                        error_message=item.error_message,
                    )
                    # Retry button
                    if st.button(f"Retry", key=f"retry_{item.id}"):
                        # Reset to pending
                        st.session_state.queue.update_status(
                            item.id,
                            QueueStatus.PENDING,
                            progress=0,
                            error_message="",
                        )
                        st.rerun()

        # Cancelled
        if cancelled:
            with st.expander(f"\u26aa Cancelled ({len(cancelled)})", expanded=False):
                for item in cancelled:
                    render_queue_item_progress(
                        item_id=item.id,
                        title=item.title,
                        thumbnail_url=item.thumbnail_url,
                        status=item.status.value,
                        progress=0,
                        current_stage="",
                    )

    # Auto-refresh while processing (check if thread finished)
    if st.session_state.stop_flag.is_set() and st.session_state.queue_running:
        st.session_state.queue_running = False  # Sync state

    if is_running or stats["processing"] > 0:
        time.sleep(1)  # Refresh every second for better progress visibility
        st.rerun()

    # Sidebar
    with st.sidebar:
        st.header("Queue Actions")

        if st.button("Add More Videos", use_container_width=True):
            st.switch_page("pages/2_Channel.py")

        if st.button("Process Single Video", use_container_width=True):
            st.switch_page("pages/1_Process.py")

        st.divider()

        st.subheader("Processing Status")
        if is_running:
            workers = st.session_state.config.max_parallel_workers
            active = stats["processing"]
            st.success(f"\u25b6\ufe0f Running ({active}/{workers} active)")
        else:
            st.info("\u23f8\ufe0f Queue is paused")

        st.divider()
        st.caption(f"Max parallel workers: {st.session_state.config.max_parallel_workers}")
        st.caption("Change in Settings page")


if __name__ == "__main__":
    main()

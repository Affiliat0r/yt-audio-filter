"""
Queue Page - Batch processing queue management with parallel processing.

Uses run_filter.bat via subprocess for 100% reliable CUDA processing.
"""

import streamlit as st
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent.parent))

from yt_audio_filter.app.state.queue import QueueManager, QueueStatus
from yt_audio_filter.app.state.config import load_config
from yt_audio_filter.app.components.progress import render_queue_item_progress, render_queue_stats
from yt_audio_filter.app.subprocess_runner import create_queue_processor

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


# Note: Queue processing now uses run_filter.bat via subprocess for 100% reliability
# See subprocess_runner.create_queue_processor() for the implementation


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
                    # Use subprocess-based processor for 100% reliable CUDA
                    thread = create_queue_processor(
                        queue_manager=st.session_state.queue,
                        config=st.session_state.config,
                        stop_flag=st.session_state.stop_flag,
                    )
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

        st.divider()
        st.subheader("Processing Mode")
        st.info(
            "**Subprocess mode** - Each video runs via run_filter.bat "
            "for 100% reliable CUDA processing."
        )


if __name__ == "__main__":
    main()

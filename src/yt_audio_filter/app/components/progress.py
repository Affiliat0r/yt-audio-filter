"""Progress display components for Streamlit."""

import streamlit as st
from typing import Dict, List, Optional


# Pipeline stages in order
PIPELINE_STAGES = [
    "Download",
    "Extract Audio",
    "Isolate Vocals",
    "Remux Video",
    "Upload",
]


def render_progress_bar(
    progress: int,
    label: str = "",
    status: str = "running",
) -> None:
    """
    Render a single progress bar with status icon.

    Args:
        progress: Progress percentage (0-100)
        label: Stage label
        status: Status (pending, running, completed, failed)
    """
    # Status icons
    icons = {
        "pending": "\u23f3",    # Hourglass
        "running": "\U0001f504",  # Spinning arrows
        "completed": "\u2705",  # Green checkmark
        "failed": "\u274c",     # Red X
    }

    icon = icons.get(status, "\u2022")
    progress_pct = min(max(progress, 0), 100)

    col1, col2, col3 = st.columns([0.5, 4, 0.8])
    with col1:
        st.write(icon)
    with col2:
        st.progress(progress_pct / 100, text=label)
    with col3:
        st.write(f"{progress_pct}%")


def _is_stage_match(stage_name: str, current_display: str) -> bool:
    """Check if current_display represents the given stage (handles verbose strings)."""
    if not current_display:
        return False
    if stage_name == current_display:
        return True
    # Handle verbose progress strings
    if stage_name == "Isolate Vocals" and (
        current_display.startswith("AI vocals") or
        current_display.startswith("AI processing")
    ):
        return True
    if stage_name == "Download" and current_display.startswith("Downloading"):
        return True
    if stage_name == "Extract Audio" and current_display.startswith("Extracting"):
        return True
    if stage_name == "Remux Video" and current_display.startswith("Remuxing"):
        return True
    if stage_name == "Upload" and current_display.startswith("Uploading"):
        return True
    return False


def render_pipeline_progress(
    current_stage: str,
    stage_progress: int = 0,
    completed_stages: Optional[List[str]] = None,
    failed_stage: Optional[str] = None,
    title: str = "",
) -> None:
    """
    Render the full pipeline progress view.

    Args:
        current_stage: Name of the currently running stage (can be verbose display string)
        stage_progress: Progress within current stage (0-100)
        completed_stages: List of completed stage names
        failed_stage: Name of failed stage (if any)
        title: Video title being processed
    """
    completed_stages = completed_stages or []

    if title:
        st.subheader(f"Processing: {title}")

    st.divider()

    for stage in PIPELINE_STAGES:
        if failed_stage and _is_stage_match(stage, failed_stage):
            status = "failed"
            progress = 0
            label = stage
        elif stage in completed_stages:
            status = "completed"
            progress = 100
            label = stage
        elif _is_stage_match(stage, current_stage):
            status = "running"
            progress = stage_progress
            # Show verbose progress string if available
            label = current_stage if current_stage != stage else stage
        else:
            status = "pending"
            progress = 0
            label = stage

        render_progress_bar(progress, label, status)

    st.divider()


def render_queue_item_progress(
    item_id: str,
    title: str,
    thumbnail_url: str,
    status: str,
    progress: int,
    current_stage: str,
    error_message: str = "",
    uploaded_url: str = "",
    on_cancel: Optional[callable] = None,
    on_remove: Optional[callable] = None,
    on_view: Optional[callable] = None,
) -> None:
    """
    Render a queue item with its progress.

    Args:
        item_id: Queue item ID
        title: Video title
        thumbnail_url: Thumbnail URL
        status: Item status (pending, processing, completed, failed, cancelled)
        progress: Progress percentage
        current_stage: Current processing stage
        error_message: Error message if failed
        uploaded_url: YouTube URL if uploaded
        on_cancel: Callback for cancel button
        on_remove: Callback for remove button
        on_view: Callback for view button
    """
    with st.container():
        cols = st.columns([1.5, 4, 1.5, 1.5])

        # Thumbnail
        with cols[0]:
            if thumbnail_url:
                st.image(thumbnail_url, width=100)

        # Info and progress
        with cols[1]:
            st.write(f"**{title}**")
            if status == "processing":
                # More verbose progress display
                st.progress(progress / 100, text=f"{current_stage} ({progress}%)")
            elif status == "completed":
                st.success("Completed")
                if uploaded_url:
                    st.markdown(f"[View on YouTube]({uploaded_url})")
            elif status == "failed":
                st.error(f"Failed: {error_message}")
            elif status == "cancelled":
                st.warning("Cancelled")
            else:
                st.info("Queued - Waiting to start")

        # Status badge
        with cols[2]:
            status_colors = {
                "pending": "\U0001f7e1",     # Yellow circle
                "processing": "\U0001f535",  # Blue circle
                "completed": "\U0001f7e2",   # Green circle
                "failed": "\U0001f534",      # Red circle
                "cancelled": "\u26aa",       # White circle
            }
            st.write(f"{status_colors.get(status, '')} {status.title()}")

        # Actions
        with cols[3]:
            if status == "pending":
                if on_remove and st.button("Remove", key=f"remove_{item_id}"):
                    on_remove(item_id)
            elif status == "processing":
                if on_cancel and st.button("Cancel", key=f"cancel_{item_id}"):
                    on_cancel(item_id)
            elif status == "completed":
                if on_view and uploaded_url:
                    if st.button("View", key=f"view_{item_id}"):
                        on_view(uploaded_url)

        st.divider()


def render_queue_stats(stats: Dict) -> None:
    """
    Render queue statistics.

    Args:
        stats: Dict with total, pending, processing, completed, failed, cancelled counts
    """
    cols = st.columns(5)

    with cols[0]:
        st.metric("Total", stats.get("total", 0))
    with cols[1]:
        st.metric("Pending", stats.get("pending", 0))
    with cols[2]:
        st.metric("Processing", stats.get("processing", 0))
    with cols[3]:
        st.metric("Completed", stats.get("completed", 0))
    with cols[4]:
        st.metric("Failed", stats.get("failed", 0))

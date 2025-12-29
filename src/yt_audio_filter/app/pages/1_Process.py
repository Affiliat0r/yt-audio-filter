"""
Process Page - Single video processing with real-time progress.
"""

import streamlit as st
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent.parent))

from yt_audio_filter.app.state.queue import QueueManager, QueueStatus
from yt_audio_filter.app.state.config import load_config
from yt_audio_filter.app.components.video_card import render_video_preview
from yt_audio_filter.app.components.progress import render_pipeline_progress

st.set_page_config(page_title="Process Video - YT Audio Filter", page_icon="\U0001f3ac", layout="wide")

# Initialize state
if "queue" not in st.session_state:
    st.session_state.queue = QueueManager()

if "config" not in st.session_state:
    st.session_state.config = load_config()

if "processing_item" not in st.session_state:
    st.session_state.processing_item = None

if "processing_logs" not in st.session_state:
    st.session_state.processing_logs = []


def validate_youtube_url(url: str) -> bool:
    """Check if URL is a valid YouTube URL."""
    if not url:
        return False
    patterns = ["youtube.com/watch", "youtu.be/", "youtube.com/shorts/"]
    return any(p in url for p in patterns)


def process_video_async(queue, item_id: str, url: str, config):
    """Process a video in the background.

    Note: queue must be passed explicitly because st.session_state is not accessible
    from background threads in Streamlit.
    """
    import traceback
    print(f"[DEBUG] process_video_async started for {url}")

    from yt_audio_filter.youtube import download_youtube_video
    from yt_audio_filter.pipeline import process_video
    from yt_audio_filter.uploader import upload_to_youtube
    from yt_audio_filter.utils import create_temp_dir

    print(f"[DEBUG] Queue obtained, item_id={item_id}")

    try:
        print("[DEBUG] Setting initial status to Download...")
        queue.update_status(item_id, QueueStatus.PROCESSING, current_stage="Download", progress=0)

        with create_temp_dir(prefix="yt_app_") as temp_dir:
            # Stage 1: Download
            def download_progress(info):
                pct = info.get("percent", 0)
                queue.update_status(item_id, QueueStatus.PROCESSING, current_stage="Download", progress=int(pct * 0.2))

            print(f"[DEBUG] Starting download to {temp_dir}...")
            metadata = download_youtube_video(url, temp_dir, progress_callback=download_progress)
            print(f"[DEBUG] Download complete: {metadata.file_path}")

            # Update title with actual video title
            queue.update_status(item_id, QueueStatus.PROCESSING, progress=20)

            # Stage 2-4: Process video
            output_path = temp_dir / f"{metadata.video_id}_filtered.mp4"

            def pipeline_progress(stage: str, pct: int, info: dict = None):
                base = {"Extract Audio": 20, "Isolate Vocals": 40, "Remux Video": 70}.get(stage, 20)
                stage_weight = {"Extract Audio": 20, "Isolate Vocals": 30, "Remux Video": 10}.get(stage, 10)
                total_pct = base + int(pct * stage_weight / 100)

                # Build verbose stage display for Isolate Vocals
                if stage == "Isolate Vocals" and info:
                    current = info.get('current', 0)
                    total = info.get('total', 0)
                    elapsed = info.get('elapsed_seconds', 0)
                    remaining = info.get('remaining_seconds', 0)
                    rate = info.get('rate', 0)

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
                else:
                    stage_display = stage

                queue.update_status(item_id, QueueStatus.PROCESSING, current_stage=stage_display, progress=total_pct)

            process_video(
                metadata.file_path,
                output_path,
                device=config.device,
                model_name=config.model_name,
                audio_bitrate=config.audio_bitrate,
                progress_callback=pipeline_progress,
            )

            # Stage 5: Upload
            queue.update_status(item_id, QueueStatus.PROCESSING, current_stage="Upload", progress=80)

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
                current_stage="Complete",
                output_path=str(output_path),
                uploaded_url=uploaded_url,
            )

    except Exception as e:
        print(f"[DEBUG] EXCEPTION: {e}")
        print(traceback.format_exc())
        queue.update_status(item_id, QueueStatus.FAILED, error_message=str(e))


def main():
    st.title("\U0001f3ac Process Video")
    st.caption("Process a single YouTube video and upload it")

    # URL input
    url = st.text_input(
        "YouTube URL",
        placeholder="https://www.youtube.com/watch?v=...",
        key="process_url",
    )

    if url:
        if validate_youtube_url(url):
            video_info = render_video_preview(url)

            st.divider()

            # Processing options
            col1, col2, col3 = st.columns(3)
            with col1:
                privacy = st.selectbox(
                    "Privacy",
                    ["public", "unlisted", "private"],
                    index=["public", "unlisted", "private"].index(st.session_state.config.default_privacy),
                )
            with col2:
                device = st.selectbox(
                    "Device",
                    ["auto", "cpu", "cuda"],
                    index=["auto", "cpu", "cuda"].index(st.session_state.config.device),
                )
            with col3:
                bitrate = st.selectbox(
                    "Audio Bitrate",
                    ["128k", "192k", "256k", "320k"],
                    index=["128k", "192k", "256k", "320k"].index(st.session_state.config.audio_bitrate),
                )

            # Start processing
            if st.button("\U0001f680 Process & Upload", type="primary"):
                # Add to queue and start processing
                item = st.session_state.queue.add(
                    url=url,
                    title=video_info.get("title", "Unknown"),
                    thumbnail_url=video_info.get("thumbnail_url", ""),
                )
                st.session_state.processing_item = item

                # Update config for this run
                config = st.session_state.config
                config.default_privacy = privacy
                config.device = device
                config.audio_bitrate = bitrate

                # Start processing in background
                # Pass queue explicitly since st.session_state isn't accessible from threads
                thread = threading.Thread(
                    target=process_video_async,
                    args=(st.session_state.queue, item.id, url, config),
                    daemon=True,
                )
                thread.start()

                st.rerun()
        else:
            st.error("Please enter a valid YouTube URL")

    # Show current processing status
    if st.session_state.processing_item:
        st.divider()
        item = st.session_state.queue.get_by_id(st.session_state.processing_item.id)

        if item:
            # Determine completed stages
            completed = []
            current = item.current_stage
            stages = ["Download", "Extract Audio", "Isolate Vocals", "Remux Video", "Upload"]

            # Helper to check if current stage matches (handles verbose display strings)
            def is_current_stage(stage_name: str, current_display: str) -> bool:
                if not current_display:
                    return False
                if stage_name == current_display:
                    return True
                # Handle verbose progress strings
                if stage_name == "Isolate Vocals" and current_display.startswith("AI vocals"):
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

            for stage in stages:
                if is_current_stage(stage, current):
                    break
                completed.append(stage)

            failed = current if item.status == QueueStatus.FAILED else None

            # Calculate stage progress based on total progress and stage ranges
            # Download: 0-20, Extract: 20-40, Vocals: 40-70, Remux: 70-80, Upload: 80-100
            stage_ranges = {
                "Download": (0, 20),
                "Extract Audio": (20, 40),
                "Isolate Vocals": (40, 70),
                "Remux Video": (70, 80),
                "Upload": (80, 100),
            }

            stage_progress = 0
            for stage_name, (start, end) in stage_ranges.items():
                if is_current_stage(stage_name, current):
                    if item.progress >= end:
                        stage_progress = 100
                    elif item.progress <= start:
                        stage_progress = 0
                    else:
                        stage_progress = int((item.progress - start) / (end - start) * 100)
                    break

            render_pipeline_progress(
                current_stage=current,
                stage_progress=stage_progress,
                completed_stages=completed,
                failed_stage=failed,
                title=item.title,
            )

            if item.status == QueueStatus.COMPLETED:
                st.success("Processing complete!")
                st.markdown(f"[View on YouTube]({item.uploaded_url})")
                st.session_state.processing_item = None

            elif item.status == QueueStatus.FAILED:
                st.error(f"Processing failed: {item.error_message}")
                if st.button("Retry"):
                    st.session_state.processing_item = None
                    st.rerun()

            elif item.status == QueueStatus.PROCESSING:
                # Auto-refresh while processing
                time.sleep(1)
                st.rerun()

    # Show queue summary in sidebar
    with st.sidebar:
        st.header("Quick Actions")
        if st.button("View Queue"):
            st.switch_page("pages/3_Queue.py")
        if st.button("Settings"):
            st.switch_page("pages/4_Settings.py")

        st.divider()

        stats = st.session_state.queue.stats()
        st.metric("Queued", stats["pending"])
        st.metric("Completed", stats["completed"])


if __name__ == "__main__":
    main()

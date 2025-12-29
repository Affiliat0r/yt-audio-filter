"""
YT Audio Filter Web Application

Main entry point for the Streamlit app - Dashboard/Landing page.
Run with: streamlit run src/yt_audio_filter/app/main.py
"""

import streamlit as st
import sys
from pathlib import Path

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

from yt_audio_filter.app.state.config import load_config
from yt_audio_filter.app.state.queue import QueueManager
from yt_audio_filter.app.components.progress import render_queue_stats

# Page config
st.set_page_config(
    page_title="YT Audio Filter",
    page_icon="\U0001f507",  # Muted speaker
    layout="wide",
    initial_sidebar_state="expanded",
)

# Initialize session state
if "config" not in st.session_state:
    st.session_state.config = load_config()

if "queue" not in st.session_state:
    st.session_state.queue = QueueManager()


def main():
    """Main app entry point - Dashboard."""
    # Header
    st.title("\U0001f507 YT Audio Filter")
    st.caption("Remove background music from YouTube videos using AI")

    # Sidebar - Navigation
    with st.sidebar:
        st.header("Navigation")
        st.page_link("pages/1_Process.py", label="\U0001f3ac Process Single Video", icon="\U0001f4f9")
        st.page_link("pages/2_Channel.py", label="\U0001f4fa Channel Scraper", icon="\U0001f50d")
        st.page_link("pages/3_Queue.py", label="\U0001f4cb Queue Manager", icon="\u2699\ufe0f")
        st.page_link("pages/4_Settings.py", label="\u2699\ufe0f Settings", icon="\U0001f527")

        st.divider()

        # Auth status
        st.subheader("YouTube Connection")
        from yt_audio_filter.uploader import check_credentials_configured
        if check_credentials_configured():
            st.success("\u2705 Connected")
        else:
            st.warning("\u26a0\ufe0f Not configured")
            st.caption("Go to Settings to set up YouTube API")

    # Main content - Dashboard
    st.header("Dashboard")

    # Queue statistics
    stats = st.session_state.queue.stats()

    col1, col2, col3, col4, col5 = st.columns(5)
    with col1:
        st.metric("Total in Queue", stats["total"])
    with col2:
        st.metric("Pending", stats["pending"])
    with col3:
        st.metric("Processing", stats["processing"])
    with col4:
        st.metric("Completed", stats["completed"])
    with col5:
        st.metric("Failed", stats["failed"])

    st.divider()

    # Quick actions
    st.subheader("Quick Actions")

    col1, col2, col3 = st.columns(3)

    with col1:
        st.markdown("### \U0001f3ac Process Video")
        st.write("Process a single YouTube video URL")
        if st.button("Go to Process", key="go_process", use_container_width=True):
            st.switch_page("pages/1_Process.py")

    with col2:
        st.markdown("### \U0001f4fa Scrape Channel")
        st.write("Scrape videos from a YouTube channel")
        if st.button("Go to Channel Scraper", key="go_channel", use_container_width=True):
            st.switch_page("pages/2_Channel.py")

    with col3:
        st.markdown("### \U0001f4cb Manage Queue")
        st.write("View and manage the processing queue")
        if st.button("Go to Queue", key="go_queue", use_container_width=True):
            st.switch_page("pages/3_Queue.py")

    st.divider()

    # Recent activity / Current processing
    if stats["processing"] > 0:
        st.subheader("Currently Processing")
        items = st.session_state.queue.get_all()
        processing = [i for i in items if i.status.value == "processing"]
        for item in processing:
            col1, col2 = st.columns([1, 3])
            with col1:
                if item.thumbnail_url:
                    st.image(item.thumbnail_url, width=120)
            with col2:
                st.write(f"**{item.title}**")
                st.progress(item.progress / 100, text=item.current_stage)

    elif stats["pending"] > 0:
        st.info(f"{stats['pending']} videos pending in queue. Go to Queue to start processing.")

    else:
        st.info("No videos in queue. Use Channel Scraper or Process Video to add videos.")


if __name__ == "__main__":
    main()

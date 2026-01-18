"""
Channel Page - Scrape and select videos from YouTube channels.
"""

import streamlit as st
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent.parent))

from yt_audio_filter.app.state.queue import QueueManager
from yt_audio_filter.app.state.config import load_config, save_config, add_recent_channel
from yt_audio_filter.app.components.video_card import render_video_grid
from yt_audio_filter.uploader import get_uploaded_source_ids, check_credentials_configured

st.set_page_config(page_title="Channel Scraper - YT Audio Filter", page_icon="\U0001f4fa", layout="wide")

# Initialize state
if "queue" not in st.session_state:
    st.session_state.queue = QueueManager()

if "config" not in st.session_state:
    st.session_state.config = load_config()

if "channel_videos" not in st.session_state:
    st.session_state.channel_videos = []

if "selected_videos" not in st.session_state:
    st.session_state.selected_videos = set()

if "current_channel" not in st.session_state:
    st.session_state.current_channel = ""

if "uploaded_source_ids" not in st.session_state:
    st.session_state.uploaded_source_ids = {}


def filter_and_sort_videos(videos, search_query, sort_by, min_duration, max_duration):
    """Filter and sort the video list."""
    filtered = videos

    # Filter by search query
    if search_query:
        query_lower = search_query.lower()
        filtered = [v for v in filtered if query_lower in v["title"].lower()]

    # Filter by duration
    if min_duration > 0:
        filtered = [v for v in filtered if v["duration"] >= min_duration * 60]
    if max_duration < 999:
        filtered = [v for v in filtered if v["duration"] <= max_duration * 60]

    # Sort
    if sort_by == "Recent":
        filtered = sorted(filtered, key=lambda x: x.get("upload_date", ""), reverse=True)
    elif sort_by == "Oldest":
        filtered = sorted(filtered, key=lambda x: x.get("upload_date", ""))
    elif sort_by == "Most Views":
        filtered = sorted(filtered, key=lambda x: x.get("view_count", 0), reverse=True)
    elif sort_by == "Least Views":
        filtered = sorted(filtered, key=lambda x: x.get("view_count", 0))
    elif sort_by == "Longest":
        filtered = sorted(filtered, key=lambda x: x.get("duration", 0), reverse=True)
    elif sort_by == "Shortest":
        filtered = sorted(filtered, key=lambda x: x.get("duration", 0))

    return filtered


def main():
    st.title("\U0001f4fa Channel Scraper")
    st.caption("Scrape videos from a YouTube channel and add them to the processing queue")

    # Check for duplicates on page load (if credentials configured and not loaded yet)
    if check_credentials_configured() and not st.session_state.uploaded_source_ids:
        with st.spinner("Checking for already-uploaded videos..."):
            try:
                st.session_state.uploaded_source_ids = get_uploaded_source_ids()
            except Exception as e:
                st.warning(f"Could not check for duplicates: {e}")

    # Channel input
    col1, col2 = st.columns([3, 1])

    with col1:
        channel = st.text_input(
            "Channel Handle",
            placeholder="@niloyatv",
            value=st.session_state.current_channel,
            help="Enter the YouTube channel handle (e.g., @niloyatv) or full URL",
        )

    with col2:
        # Recent channels dropdown
        recent = st.session_state.config.recent_channels
        if recent:
            st.write("")  # Spacing
            selected_recent = st.selectbox(
                "Recent Channels",
                ["Select recent..."] + recent,
                key="recent_select",
            )
            if selected_recent and selected_recent != "Select recent...":
                channel = selected_recent

    # Scrape options
    st.subheader("Scrape Options")
    col1, col2, col3 = st.columns(3)

    with col1:
        max_videos = st.slider(
            "Max Videos",
            min_value=10,
            max_value=500,
            value=50,
            step=10,
            help="Maximum number of videos to fetch",
        )

    with col2:
        include_shorts = st.checkbox(
            "Include Shorts",
            value=False,
            help="Include YouTube Shorts (< 60 seconds)",
        )

    with col3:
        st.write("")  # Spacing
        fetch_button = st.button("\U0001f50d Fetch Videos", type="primary", use_container_width=True)

    # Fetch videos
    if fetch_button and channel:
        with st.spinner(f"Fetching videos from {channel}..."):
            try:
                from yt_audio_filter.scraper import get_channel_videos

                videos = list(get_channel_videos(
                    channel_url=channel,
                    max_videos=max_videos,
                    include_shorts=include_shorts,
                ))

                # Convert to dict format for display
                st.session_state.channel_videos = [
                    {
                        "video_id": v.video_id,
                        "title": v.title,
                        "url": v.url,
                        "duration": v.duration,
                        "view_count": v.view_count,
                        "upload_date": v.upload_date,
                        "thumbnail_url": v.thumbnail_url,
                    }
                    for v in videos
                ]
                st.session_state.current_channel = channel
                st.session_state.selected_videos = set()

                # Add to recent channels
                config = add_recent_channel(st.session_state.config, channel)
                save_config(config)
                st.session_state.config = config

                st.success(f"Found {len(videos)} videos from {channel}")

            except Exception as e:
                st.error(f"Failed to fetch videos: {e}")

    elif fetch_button and not channel:
        st.warning("Please enter a channel handle")

    # Display video grid
    if st.session_state.channel_videos:
        st.divider()

        # Header with channel info
        col1, col2 = st.columns([3, 1])
        with col1:
            st.subheader(f"\U0001f4fa {st.session_state.current_channel}")
            st.caption(f"{len(st.session_state.channel_videos)} videos found")
        with col2:
            if st.button("Clear", use_container_width=True):
                st.session_state.channel_videos = []
                st.session_state.selected_videos = set()
                st.session_state.current_channel = ""
                st.rerun()

        # Filtering and Sorting options
        st.subheader("\U0001f50e Filter & Sort")
        col1, col2, col3, col4, col5 = st.columns([3, 2, 1.5, 1.5, 1.5])

        with col1:
            search_query = st.text_input(
                "Search titles",
                placeholder="Type to filter by title...",
                key="search_filter",
                label_visibility="collapsed",
            )

        with col2:
            sort_by = st.selectbox(
                "Sort by",
                ["Recent", "Oldest", "Most Views", "Least Views", "Longest", "Shortest"],
                key="sort_option",
            )

        with col3:
            min_duration = st.number_input(
                "Min (min)",
                min_value=0,
                max_value=999,
                value=0,
                help="Minimum duration in minutes",
            )

        with col4:
            max_duration = st.number_input(
                "Max (min)",
                min_value=0,
                max_value=999,
                value=999,
                help="Maximum duration in minutes",
            )

        with col5:
            hide_uploaded = st.checkbox(
                "Hide uploaded",
                value=True,
                help="Hide videos already uploaded to your channel",
            )

        # Apply filters
        filtered_videos = filter_and_sort_videos(
            st.session_state.channel_videos,
            search_query,
            sort_by,
            min_duration,
            max_duration,
        )

        # Apply "hide uploaded" filter
        if hide_uploaded and st.session_state.uploaded_source_ids:
            filtered_videos = [
                v for v in filtered_videos
                if v.get("video_id", v.get("id", "")) not in st.session_state.uploaded_source_ids
            ]

        if len(filtered_videos) != len(st.session_state.channel_videos):
            st.info(f"Showing {len(filtered_videos)} of {len(st.session_state.channel_videos)} videos")

        st.divider()

        # Video selection grid
        def on_selection_change(selected: set):
            st.session_state.selected_videos = selected

        st.session_state.selected_videos = render_video_grid(
            videos=filtered_videos,
            selected_ids=st.session_state.selected_videos,
            on_selection_change=on_selection_change,
            page_size=st.session_state.config.videos_per_page,
            key_prefix="channel_",
            uploaded_source_ids=st.session_state.uploaded_source_ids if not hide_uploaded else None,
        )

        # Add to queue section
        if st.session_state.selected_videos:
            st.divider()

            col1, col2, col3 = st.columns([2, 1, 1])

            with col1:
                st.write(f"**{len(st.session_state.selected_videos)} videos selected**")

            with col2:
                privacy = st.selectbox(
                    "Privacy",
                    ["public", "unlisted", "private"],
                    index=["public", "unlisted", "private"].index(
                        st.session_state.config.default_privacy
                    ),
                    key="batch_privacy",
                )

            with col3:
                if st.button(
                    "\U0001f4e5 Add to Queue",
                    type="primary",
                    use_container_width=True,
                ):
                    # Get selected videos (from original list, not filtered)
                    selected_videos = [
                        v for v in st.session_state.channel_videos
                        if v["video_id"] in st.session_state.selected_videos
                    ]

                    # Add to queue
                    items = st.session_state.queue.add_batch(selected_videos)

                    st.success(f"Added {len(items)} videos to queue!")
                    st.session_state.selected_videos = set()

                    # Option to go to queue
                    if st.button("View Queue"):
                        st.switch_page("pages/3_Queue.py")

    else:
        st.info("Enter a channel handle and click 'Fetch Videos' to see available videos")

    # Sidebar with quick actions
    with st.sidebar:
        st.header("Quick Actions")

        stats = st.session_state.queue.stats()
        st.metric("Queue Size", stats["total"])
        st.metric("Pending", stats["pending"])

        st.divider()

        if st.button("Go to Queue", use_container_width=True):
            st.switch_page("pages/3_Queue.py")

        if st.button("Process Single Video", use_container_width=True):
            st.switch_page("pages/1_Process.py")

        st.divider()

        # Duplicate check status
        st.subheader("Duplicate Detection")
        if st.session_state.uploaded_source_ids:
            st.success(f"{len(st.session_state.uploaded_source_ids)} videos tracked")
        else:
            st.info("Not loaded")

        if st.button("Refresh Duplicates", use_container_width=True):
            with st.spinner("Checking uploads..."):
                try:
                    from yt_audio_filter.uploader import clear_upload_cache
                    clear_upload_cache()
                    st.session_state.uploaded_source_ids = get_uploaded_source_ids(force_refresh=True)
                    st.rerun()
                except Exception as e:
                    st.error(f"Failed: {e}")


if __name__ == "__main__":
    main()

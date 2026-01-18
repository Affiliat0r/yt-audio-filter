"""Video card display components for Streamlit."""

import streamlit as st
from datetime import datetime
from typing import Callable, Dict, List, Optional, Set


def format_duration(seconds) -> str:
    """Format duration in MM:SS or HH:MM:SS."""
    # Convert to int in case it's a float
    seconds = int(seconds or 0)
    if seconds < 0:
        return "0:00"
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    if hours > 0:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def format_view_count(count: int) -> str:
    """Format view count (e.g., 1.2M, 980K)."""
    if count >= 1_000_000:
        return f"{count / 1_000_000:.1f}M"
    elif count >= 1_000:
        return f"{count / 1_000:.1f}K"
    return str(count)


def format_relative_date(date_str: str) -> str:
    """Format date as relative time (e.g., '2 days ago')."""
    if not date_str:
        return ""
    try:
        # Handle YYYYMMDD format
        if len(date_str) == 8 and date_str.isdigit():
            date = datetime.strptime(date_str, "%Y%m%d")
        else:
            date = datetime.fromisoformat(date_str.replace("Z", "+00:00"))

        now = datetime.now()
        diff = now - date

        if diff.days == 0:
            return "Today"
        elif diff.days == 1:
            return "Yesterday"
        elif diff.days < 7:
            return f"{diff.days} days ago"
        elif diff.days < 30:
            weeks = diff.days // 7
            return f"{weeks} week{'s' if weeks > 1 else ''} ago"
        elif diff.days < 365:
            months = diff.days // 30
            return f"{months} month{'s' if months > 1 else ''} ago"
        else:
            years = diff.days // 365
            return f"{years} year{'s' if years > 1 else ''} ago"
    except Exception:
        return date_str


def render_video_card(
    video: Dict,
    selected: bool = False,
    on_select: Optional[Callable[[str, bool], None]] = None,
    show_checkbox: bool = True,
    key_prefix: str = "",
    already_uploaded_info: Optional[Dict] = None,
) -> bool:
    """
    Render a single video card.

    Args:
        video: Dict with video_id, title, url, duration, view_count, upload_date, thumbnail_url
        selected: Whether this video is currently selected
        on_select: Callback when selection changes
        show_checkbox: Whether to show selection checkbox
        key_prefix: Prefix for Streamlit widget keys
        already_uploaded_info: If set, dict with info about already uploaded version
            {"uploaded_id": str, "title": str, "url": str}

    Returns:
        Current selection state
    """
    video_id = video.get("video_id", video.get("id", ""))
    title = video.get("title", "Unknown")
    url = video.get("url", "")
    duration = video.get("duration", 0)
    view_count = video.get("view_count", 0)
    upload_date = video.get("upload_date", "")
    thumbnail_url = video.get("thumbnail_url", "")

    # Default thumbnail if missing
    if not thumbnail_url and video_id:
        thumbnail_url = f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"

    # Create card container with border
    with st.container():
        cols = st.columns([0.5, 2, 6, 1.5])

        # Checkbox column
        with cols[0]:
            if show_checkbox:
                # Disable checkbox if already uploaded
                new_selected = st.checkbox(
                    "Select",
                    value=selected if not already_uploaded_info else False,
                    key=f"{key_prefix}select_{video_id}",
                    label_visibility="hidden",
                    disabled=already_uploaded_info is not None,
                )
                if on_select and new_selected != selected and not already_uploaded_info:
                    on_select(video_id, new_selected)
                selected = new_selected if not already_uploaded_info else False

        # Thumbnail column
        with cols[1]:
            if thumbnail_url:
                st.image(thumbnail_url, width=120)
            else:
                st.write("No thumbnail")

        # Info column
        with cols[2]:
            # Show "Already Uploaded" badge if applicable
            if already_uploaded_info:
                st.markdown(f":white_check_mark: **Already Uploaded** - {title}")
                st.caption(f"[View uploaded version]({already_uploaded_info['url']})")
            else:
                st.markdown(f"**{title}**")

            meta_parts = []
            if view_count:
                meta_parts.append(f"Views: {format_view_count(view_count)}")
            if upload_date:
                meta_parts.append(format_relative_date(upload_date))
            if meta_parts:
                st.caption(" | ".join(meta_parts))
            if url and not already_uploaded_info:
                st.caption(url)

        # Duration column
        with cols[3]:
            st.write(format_duration(duration))

        st.divider()

    return selected


def render_video_grid(
    videos: List[Dict],
    selected_ids: Set[str],
    on_selection_change: Optional[Callable[[Set[str]], None]] = None,
    page_size: int = 20,
    key_prefix: str = "",
    uploaded_source_ids: Optional[Dict[str, Dict]] = None,
) -> Set[str]:
    """
    Render a grid of video cards with selection.

    Args:
        videos: List of video dictionaries
        selected_ids: Set of currently selected video IDs
        on_selection_change: Callback when selection changes
        page_size: Number of videos per page
        key_prefix: Prefix for widget keys
        uploaded_source_ids: Dict mapping source video IDs to upload info
            {source_id: {"uploaded_id": str, "title": str, "url": str}}

    Returns:
        Updated set of selected video IDs
    """
    if not videos:
        st.info("No videos found")
        return selected_ids

    uploaded_source_ids = uploaded_source_ids or {}

    # Count already uploaded
    already_uploaded_count = sum(
        1 for v in videos
        if v.get("video_id", v.get("id", "")) in uploaded_source_ids
    )

    # Selection controls
    col1, col2, col3 = st.columns([1, 1, 2])
    with col1:
        if st.button("Select All (New)", key=f"{key_prefix}select_all"):
            # Only select videos that haven't been uploaded yet
            selected_ids = {
                v.get("video_id", v.get("id", ""))
                for v in videos
                if v.get("video_id", v.get("id", "")) not in uploaded_source_ids
            }
            if on_selection_change:
                on_selection_change(selected_ids)
    with col2:
        if st.button("Deselect All", key=f"{key_prefix}deselect_all"):
            selected_ids = set()
            if on_selection_change:
                on_selection_change(selected_ids)
    with col3:
        if already_uploaded_count > 0:
            st.write(f"Selected: {len(selected_ids)} | Already uploaded: {already_uploaded_count}")
        else:
            st.write(f"Selected: {len(selected_ids)} videos")

    st.divider()

    # Pagination
    total_pages = (len(videos) + page_size - 1) // page_size
    if total_pages > 1:
        page = st.selectbox(
            "Page",
            range(1, total_pages + 1),
            key=f"{key_prefix}page",
            format_func=lambda x: f"Page {x} of {total_pages}",
        )
    else:
        page = 1

    # Display videos for current page
    start_idx = (page - 1) * page_size
    end_idx = min(start_idx + page_size, len(videos))
    page_videos = videos[start_idx:end_idx]

    def handle_select(video_id: str, is_selected: bool):
        nonlocal selected_ids
        if is_selected:
            selected_ids = selected_ids | {video_id}
        else:
            selected_ids = selected_ids - {video_id}
        if on_selection_change:
            on_selection_change(selected_ids)

    for video in page_videos:
        video_id = video.get("video_id", video.get("id", ""))
        already_uploaded_info = uploaded_source_ids.get(video_id)
        render_video_card(
            video=video,
            selected=video_id in selected_ids,
            on_select=handle_select,
            key_prefix=f"{key_prefix}{video_id}_",
            already_uploaded_info=already_uploaded_info,
        )

    return selected_ids


def render_video_preview(url: str) -> Optional[Dict]:
    """
    Render a preview of a single video from URL.

    Args:
        url: YouTube video URL

    Returns:
        Video metadata dict if successful
    """
    from ..state.queue import QueueItem

    # Extract video ID from URL
    video_id = ""
    if "v=" in url:
        video_id = url.split("v=")[-1].split("&")[0]
    elif "youtu.be/" in url:
        video_id = url.split("youtu.be/")[-1].split("?")[0]
    elif "/shorts/" in url:
        video_id = url.split("/shorts/")[-1].split("?")[0]

    if not video_id:
        st.error("Could not extract video ID from URL")
        return None

    thumbnail_url = f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"

    col1, col2 = st.columns([1, 2])
    with col1:
        st.image(thumbnail_url, width=200)
    with col2:
        st.write(f"**Video ID:** {video_id}")
        st.write(f"**URL:** {url}")
        st.caption("Click 'Process Video' to start")

    return {
        "video_id": video_id,
        "url": url,
        "thumbnail_url": thumbnail_url,
        "title": f"Video {video_id}",  # Will be updated during download
    }

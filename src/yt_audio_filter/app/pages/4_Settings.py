"""
Settings Page - Application configuration and YouTube authentication.
"""

import streamlit as st
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent.parent))

from yt_audio_filter.app.state.config import load_config, save_config, AppConfig

st.set_page_config(page_title="Settings - YT Audio Filter", page_icon="\u2699\ufe0f", layout="wide")

# Initialize state
if "config" not in st.session_state:
    st.session_state.config = load_config()


def main():
    st.title("\u2699\ufe0f Settings")
    st.caption("Configure processing options and YouTube authentication")

    config = st.session_state.config

    # Tab layout
    tab1, tab2, tab3 = st.tabs([
        "\U0001f3a5 Processing",
        "\U0001f4e4 Upload",
        "\U0001f511 Authentication",
    ])

    # Processing settings
    with tab1:
        st.subheader("Processing Options")

        col1, col2 = st.columns(2)

        with col1:
            device = st.selectbox(
                "Processing Device",
                ["auto", "cpu", "cuda"],
                index=["auto", "cpu", "cuda"].index(config.device),
                help="Device for AI processing. 'auto' will use GPU if available.",
            )

            model = st.selectbox(
                "Demucs Model",
                ["htdemucs", "htdemucs_ft"],
                index=["htdemucs", "htdemucs_ft"].index(config.model_name)
                if config.model_name in ["htdemucs", "htdemucs_ft"]
                else 0,
                help="htdemucs is faster, htdemucs_ft may have better quality.",
            )

        with col2:
            bitrate = st.selectbox(
                "Audio Bitrate",
                ["128k", "192k", "256k", "320k"],
                index=["128k", "192k", "256k", "320k"].index(config.audio_bitrate),
                help="Higher bitrate = better audio quality but larger file size.",
            )

            parallel_workers = st.slider(
                "Parallel Workers",
                min_value=1,
                max_value=4,
                value=config.max_parallel_workers,
                help="Number of videos to process simultaneously. Higher = faster but uses more resources.",
            )

            videos_per_page = st.slider(
                "Videos Per Page",
                min_value=10,
                max_value=50,
                value=config.videos_per_page,
                help="Number of videos to show per page in the channel scraper.",
            )

        st.divider()
        st.subheader("Output Options")

        col1, col2 = st.columns(2)

        with col1:
            keep_local = st.checkbox(
                "Keep Local Copies",
                value=config.keep_local_copies,
                help="Keep processed videos on disk after upload.",
            )

        with col2:
            auto_delete = st.checkbox(
                "Auto-Delete After Upload",
                value=config.auto_delete_after_upload,
                help="Automatically delete local files after successful upload.",
                disabled=keep_local,
            )

    # Upload settings
    with tab2:
        st.subheader("Upload Defaults")

        col1, col2 = st.columns(2)

        with col1:
            privacy = st.selectbox(
                "Default Privacy",
                ["public", "unlisted", "private"],
                index=["public", "unlisted", "private"].index(config.default_privacy),
                help="Default privacy setting for uploaded videos.",
            )

        with col2:
            add_footer = st.checkbox(
                "Add Attribution Footer",
                value=config.add_attribution_footer,
                help="Add a small footer to video description with original video link.",
            )

        st.divider()
        st.subheader("Recent Channels")

        if config.recent_channels:
            st.write("Your recently used channels:")
            for i, channel in enumerate(config.recent_channels):
                col1, col2 = st.columns([4, 1])
                with col1:
                    st.write(f"{i+1}. {channel}")
                with col2:
                    if st.button("\U0001f5d1\ufe0f", key=f"del_channel_{i}"):
                        config.recent_channels.remove(channel)
                        save_config(config)
                        st.session_state.config = config
                        st.rerun()

            if st.button("Clear All Recent Channels"):
                config.recent_channels = []
                save_config(config)
                st.session_state.config = config
                st.rerun()
        else:
            st.info("No recent channels. Channels will appear here after you scrape them.")

    # Authentication settings
    with tab3:
        st.subheader("YouTube API Authentication")

        from yt_audio_filter.uploader import (
            check_credentials_configured,
            CLIENT_SECRETS_FILE,
            OAUTH_TOKEN_FILE,
            setup_credentials_guide,
        )

        if check_credentials_configured():
            st.success("\u2705 YouTube API is configured")

            col1, col2 = st.columns(2)
            with col1:
                st.write(f"**Client Secrets:** {CLIENT_SECRETS_FILE}")
            with col2:
                if OAUTH_TOKEN_FILE.exists():
                    st.write(f"**OAuth Token:** {OAUTH_TOKEN_FILE}")
                else:
                    st.warning("OAuth token not found - will authenticate on first upload")

            # Test connection
            if st.button("Test Connection"):
                try:
                    from yt_audio_filter.uploader import list_playlists
                    playlists = list_playlists()
                    if playlists:
                        st.success(f"Connected! Found {len(playlists)} playlists.")
                        with st.expander("Your Playlists"):
                            for pl in playlists:
                                st.write(f"- {pl['title']} (`{pl['id']}`)")
                    else:
                        st.warning("Connected, but no playlists found.")
                except Exception as e:
                    st.error(f"Connection failed: {e}")

            st.divider()

            # Re-authenticate option
            if st.button("Re-authenticate"):
                if OAUTH_TOKEN_FILE.exists():
                    OAUTH_TOKEN_FILE.unlink()
                    st.success("OAuth token deleted. You'll be prompted to re-authenticate on next upload.")
                    st.rerun()

        else:
            st.warning("\u26a0\ufe0f YouTube API is not configured")

            st.markdown(setup_credentials_guide())

            st.divider()

            # File uploader for client_secrets.json
            st.subheader("Upload Client Secrets")
            uploaded_file = st.file_uploader(
                "Upload client_secrets.json",
                type=["json"],
                help="Upload the OAuth client secrets file from Google Cloud Console",
            )

            if uploaded_file:
                try:
                    import json
                    content = uploaded_file.read()
                    # Validate JSON
                    data = json.loads(content)
                    if "installed" in data or "web" in data:
                        # Save to credentials directory
                        CLIENT_SECRETS_FILE.parent.mkdir(parents=True, exist_ok=True)
                        with open(CLIENT_SECRETS_FILE, "wb") as f:
                            f.write(content)
                        st.success(f"Saved to {CLIENT_SECRETS_FILE}")
                        st.rerun()
                    else:
                        st.error("Invalid client secrets file format")
                except Exception as e:
                    st.error(f"Failed to process file: {e}")

    # Save button
    st.divider()

    if st.button("Save Settings", type="primary"):
        # Update config with new values
        new_config = AppConfig(
            device=device,
            audio_bitrate=bitrate,
            model_name=model,
            max_parallel_workers=parallel_workers,
            keep_local_copies=keep_local,
            auto_delete_after_upload=auto_delete if not keep_local else False,
            default_privacy=privacy,
            add_attribution_footer=add_footer,
            recent_channels=config.recent_channels,
            videos_per_page=videos_per_page,
        )

        save_config(new_config)
        st.session_state.config = new_config
        st.success("Settings saved!")

    # Sidebar
    with st.sidebar:
        st.header("Quick Links")

        if st.button("Process Video", use_container_width=True):
            st.switch_page("pages/1_Process.py")

        if st.button("Scrape Channel", use_container_width=True):
            st.switch_page("pages/2_Channel.py")

        if st.button("View Queue", use_container_width=True):
            st.switch_page("pages/3_Queue.py")


if __name__ == "__main__":
    main()

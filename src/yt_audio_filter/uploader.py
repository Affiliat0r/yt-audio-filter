"""YouTube upload integration using google-api-python-client or youtubeuploader binary."""

import json
import os
import pickle
import subprocess
import sys
from pathlib import Path
from typing import List, Optional, TYPE_CHECKING

from .exceptions import PrerequisiteError, YTAudioFilterError
from .logger import get_logger

if TYPE_CHECKING:
    from .youtube import VideoMetadata

logger = get_logger()

# OAuth2 credentials file location
CREDENTIALS_DIR = Path.home() / ".yt-audio-filter"
CLIENT_SECRETS_FILE = CREDENTIALS_DIR / "client_secrets.json"
OAUTH_TOKEN_FILE = CREDENTIALS_DIR / "oauth_token.pickle"

# youtubeuploader binary support
YOUTUBEUPLOADER_TOKEN_FILE = CREDENTIALS_DIR / "request.token"


class YouTubeUploadError(YTAudioFilterError):
    """YouTube upload failures."""

    pass


# SEO keywords for musicless/no background music content
SEO_KEYWORDS = [
    "no background music",
    "musicless",
    "no music",
    "vocals only",
    "speech only",
    "no bgm",
    "music removed",
    "background music removed",
    "clean audio",
    "voice only",
    "talking only",
    "no soundtrack",
]


def generate_seo_title(original_title: str) -> str:
    """
    Generate an SEO-optimized title for musicless video.

    Args:
        original_title: Original video title

    Returns:
        SEO-optimized title with musicless keywords
    """
    # Add "No Background Music" or similar to title
    # Keep it under 100 chars for YouTube
    suffix = " [No Background Music]"
    max_original_len = 100 - len(suffix)

    if len(original_title) > max_original_len:
        original_title = original_title[:max_original_len - 3] + "..."

    return f"{original_title}{suffix}"


def generate_seo_description(
    original_title: str,
    original_description: str,
    original_channel: str,
    original_video_id: str,
) -> str:
    """
    Generate description for musicless video, preserving original description.

    The original description is kept intact at the top, with a small footer
    added for attribution and discoverability.

    Args:
        original_title: Original video title
        original_description: Original video description
        original_channel: Original channel name
        original_video_id: Original YouTube video ID

    Returns:
        Description with original content preserved plus attribution footer
    """
    original_url = f"https://youtube.com/watch?v={original_video_id}"

    # Keep original description intact, just add attribution footer
    if original_description:
        # Truncate if needed to leave room for footer (YouTube limit: 5000 chars)
        max_orig_len = 4500
        if len(original_description) > max_orig_len:
            description = original_description[:max_orig_len] + "..."
        else:
            description = original_description
    else:
        description = original_title

    # Add small attribution footer (doesn't override original content)
    footer = f"""

---
🔇 Background music removed version
📺 Original: {original_url} | 👤 {original_channel}
🔍 #NoBackgroundMusic #Musicless #VocalsOnly"""

    return description + footer


def generate_seo_tags(original_tags: List[str]) -> List[str]:
    """
    Generate SEO-optimized tags combining original tags with musicless keywords.

    Args:
        original_tags: Original video tags

    Returns:
        Combined and optimized tag list
    """
    # Start with musicless-specific tags (high priority)
    tags = SEO_KEYWORDS.copy()

    # Add original tags (limited to avoid YouTube's 500 char tag limit)
    remaining_chars = 400  # Leave room for our SEO tags
    for tag in original_tags:
        if len(tag) + 1 <= remaining_chars:  # +1 for comma separator
            if tag.lower() not in [t.lower() for t in tags]:  # Avoid duplicates
                tags.append(tag)
                remaining_chars -= len(tag) + 1

    return tags[:30]  # YouTube allows max 30 tags


def check_upload_dependencies() -> bool:
    """
    Check if YouTube upload dependencies are installed.

    Returns:
        True if all dependencies are available
    """
    try:
        import google.oauth2.credentials
        import google_auth_oauthlib.flow
        import googleapiclient.discovery
        import googleapiclient.http

        return True
    except ImportError:
        return False


def ensure_upload_dependencies() -> None:
    """
    Ensure YouTube upload dependencies are installed.

    Raises:
        PrerequisiteError: If dependencies are not installed
    """
    if not check_upload_dependencies():
        raise PrerequisiteError(
            "YouTube upload dependencies not installed",
            "Install with: pip install google-api-python-client google-auth-oauthlib",
        )


def check_credentials_configured() -> bool:
    """
    Check if OAuth2 credentials are configured.

    Returns:
        True if client_secrets.json exists
    """
    return CLIENT_SECRETS_FILE.exists()


def find_youtubeuploader_binary() -> Optional[Path]:
    """
    Find youtubeuploader binary in common locations.

    Returns:
        Path to binary if found, None otherwise
    """
    import shutil
    import sys

    # Determine binary names based on platform
    if sys.platform == "win32":
        binary_names = ["youtubeuploader.exe"]
    else:
        binary_names = ["youtubeuploader"]

    # Check common locations
    base_locations = [
        # In the package directory (where cli.py lives)
        Path(__file__).parent.parent.parent,
        # In project root
        Path.cwd(),
        # In credentials directory
        CREDENTIALS_DIR,
    ]

    for base in base_locations:
        for name in binary_names:
            loc = base / name
            if loc.exists():
                return loc

    # Also check system PATH
    for name in binary_names:
        found = shutil.which(name)
        if found:
            return Path(found)

    return None


def upload_with_youtubeuploader(
    video_path: Path,
    title: str,
    description: str = "",
    tags: Optional[List[str]] = None,
    privacy: str = "unlisted",
    secrets_file: Optional[Path] = None,
    token_file: Optional[Path] = None,
) -> str:
    """
    Upload video using youtubeuploader binary.

    Args:
        video_path: Path to video file
        title: Video title
        description: Video description
        tags: List of tags
        privacy: Privacy setting
        secrets_file: Path to client_secrets.json
        token_file: Path to token cache file

    Returns:
        YouTube video ID

    Raises:
        YouTubeUploadError: If upload fails
    """
    binary = find_youtubeuploader_binary()
    if not binary:
        raise YouTubeUploadError(
            "youtubeuploader binary not found",
            "Download from https://github.com/porjo/youtubeuploader/releases",
        )

    secrets = secrets_file or CLIENT_SECRETS_FILE
    token = token_file or YOUTUBEUPLOADER_TOKEN_FILE

    if not secrets.exists():
        raise YouTubeUploadError(
            "YouTube API not configured",
            f"Place client_secrets.json at {secrets}",
        )

    cmd = [
        str(binary),
        "-filename", str(video_path),
        "-title", title,
        "-description", description,
        "-privacy", privacy,
        "-secrets", str(secrets),
        "-cache", str(token),
    ]

    # Add tags if provided
    if tags:
        cmd.extend(["-tags", ",".join(tags)])

    logger.info(f"Uploading with youtubeuploader: {title}")

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=1800,  # 30 min timeout for large videos
        )

        if result.returncode != 0:
            raise YouTubeUploadError(
                f"youtubeuploader failed: {result.stderr}"
            )

        # Parse video ID from output
        output = result.stdout + result.stderr
        for line in output.split("\n"):
            if "Video ID:" in line:
                video_id = line.split("Video ID:")[-1].strip()
                logger.info(f"Upload complete! Video ID: {video_id}")
                return video_id

        raise YouTubeUploadError("Could not parse video ID from output")

    except subprocess.TimeoutExpired:
        raise YouTubeUploadError("Upload timed out (30 min limit)")
    except Exception as e:
        if isinstance(e, YouTubeUploadError):
            raise
        raise YouTubeUploadError(f"Upload failed: {e}")


def setup_credentials_guide() -> str:
    """
    Return instructions for setting up YouTube API credentials.

    Returns:
        String with setup instructions
    """
    return f"""
YouTube API Setup Required
==========================

1. Go to https://console.cloud.google.com/
2. Create a new project (or select existing)
3. Enable "YouTube Data API v3":
   - Go to "APIs & Services" > "Library"
   - Search for "YouTube Data API v3"
   - Click "Enable"

4. Create OAuth 2.0 credentials:
   - Go to "APIs & Services" > "Credentials"
   - Click "Create Credentials" > "OAuth client ID"
   - Application type: "Desktop app"
   - Download the JSON file

5. Save the JSON file as:
   {CLIENT_SECRETS_FILE}

6. Run the upload command again - a browser will open for authentication.

Note: First-time setup requires one-time browser authentication.
After that, uploads work automatically.
"""


def authenticate_youtube():
    """
    Authenticate with YouTube API using OAuth2.

    Returns:
        Authenticated YouTube API service object

    Raises:
        YouTubeUploadError: If authentication fails
    """
    ensure_upload_dependencies()

    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]

    credentials = None

    # Load saved credentials if they exist
    if OAUTH_TOKEN_FILE.exists():
        try:
            with open(OAUTH_TOKEN_FILE, "rb") as token:
                credentials = pickle.load(token)
        except Exception as e:
            logger.debug(f"Failed to load saved credentials: {e}")

    # If no valid credentials, authenticate
    if not credentials or not credentials.valid:
        if not check_credentials_configured():
            raise YouTubeUploadError(
                "YouTube API not configured", setup_credentials_guide()
            )

        try:
            flow = InstalledAppFlow.from_client_secrets_file(
                str(CLIENT_SECRETS_FILE), SCOPES
            )
            credentials = flow.run_local_server(port=0)

            # Save credentials for next time
            CREDENTIALS_DIR.mkdir(parents=True, exist_ok=True)
            with open(OAUTH_TOKEN_FILE, "wb") as token:
                pickle.dump(credentials, token)
            logger.info("YouTube authentication successful - credentials saved")

        except Exception as e:
            raise YouTubeUploadError(f"YouTube authentication failed: {e}")

    return build("youtube", "v3", credentials=credentials)


def upload_to_youtube(
    video_path: Path,
    original_metadata: Optional["VideoMetadata"] = None,
    privacy: str = "unlisted",
    playlist_id: Optional[str] = None,
) -> str:
    """
    Upload a video to YouTube with SEO-optimized metadata.

    Tries Python google-api-python-client first, falls back to youtubeuploader binary
    if that fails (e.g., due to network/firewall issues).

    Args:
        video_path: Path to the video file
        original_metadata: Original video metadata for SEO optimization
        privacy: Privacy setting (public, unlisted, private)
        playlist_id: Optional playlist ID to add video to

    Returns:
        YouTube video ID of uploaded video

    Raises:
        YouTubeUploadError: If upload fails
    """
    if not video_path.exists():
        raise YouTubeUploadError(f"Video file not found: {video_path}")

    # Generate SEO-optimized metadata
    if original_metadata:
        title = generate_seo_title(original_metadata.title)
        description = generate_seo_description(
            original_title=original_metadata.title,
            original_description=original_metadata.description,
            original_channel=original_metadata.channel,
            original_video_id=original_metadata.video_id,
        )
        tags = generate_seo_tags(original_metadata.tags)
        logger.info(f"Using SEO-optimized metadata from original video")
    else:
        # Fallback for local files without metadata
        title = f"{video_path.stem} [No Background Music]"
        description = """🔇 Background Music Removed

This video has been processed to remove background music while preserving speech and vocals clearly.

🛠️ Processed with YT Audio Filter (AI-powered background music removal using Demucs)
"""
        tags = SEO_KEYWORDS.copy()

    # Try youtubeuploader binary first (more reliable on some networks)
    binary = find_youtubeuploader_binary()
    if binary and YOUTUBEUPLOADER_TOKEN_FILE.exists():
        logger.info("Using youtubeuploader binary for upload")
        try:
            return upload_with_youtubeuploader(
                video_path=video_path,
                title=title,
                description=description,
                tags=tags,
                privacy=privacy,
            )
        except Exception as e:
            logger.warning(f"youtubeuploader failed: {e}, trying Python API")

    # Fall back to Python API
    if not check_upload_dependencies():
        # If no Python deps and binary failed, give clear error
        if binary:
            raise YouTubeUploadError(
                "Upload failed with youtubeuploader binary",
                "Check network connection or re-authenticate",
            )
        raise PrerequisiteError(
            "YouTube upload dependencies not installed",
            "Install with: pip install google-api-python-client google-auth-oauthlib",
        )

    from googleapiclient.http import MediaFileUpload

    logger.info(f"Uploading to YouTube: {title}")

    try:
        youtube = authenticate_youtube()

        # Video metadata with SEO optimization
        body = {
            "snippet": {
                "title": title,
                "description": description,
                "tags": tags,
                "categoryId": "22",  # People & Blogs
            },
            "status": {
                "privacyStatus": privacy,
                "selfDeclaredMadeForKids": False,
            },
        }

        # Upload the video
        media = MediaFileUpload(
            str(video_path),
            mimetype="video/mp4",
            resumable=True,
            chunksize=1024 * 1024,  # 1MB chunks
        )

        request = youtube.videos().insert(
            part="snippet,status", body=body, media_body=media
        )

        response = None
        while response is None:
            status, response = request.next_chunk()
            if status:
                progress = int(status.progress() * 100)
                logger.info(f"Upload progress: {progress}%")

        video_id = response["id"]
        logger.info(f"Upload complete! Video ID: {video_id}")
        logger.info(f"Video URL: https://youtube.com/watch?v={video_id}")

        # Add to playlist if specified
        if playlist_id:
            add_to_playlist(youtube, video_id, playlist_id)

        return video_id

    except Exception as e:
        if isinstance(e, YouTubeUploadError):
            raise
        # If Python API fails, try binary as last resort
        if binary:
            logger.warning(f"Python API failed: {e}, trying youtubeuploader binary")
            return upload_with_youtubeuploader(
                video_path=video_path,
                title=title,
                description=description,
                tags=tags,
                privacy=privacy,
            )
        raise YouTubeUploadError(f"Upload failed: {e}")


def add_to_playlist(youtube, video_id: str, playlist_id: str) -> None:
    """
    Add a video to a YouTube playlist.

    Args:
        youtube: Authenticated YouTube API service
        video_id: YouTube video ID
        playlist_id: YouTube playlist ID
    """
    try:
        youtube.playlistItems().insert(
            part="snippet",
            body={
                "snippet": {
                    "playlistId": playlist_id,
                    "resourceId": {"kind": "youtube#video", "videoId": video_id},
                }
            },
        ).execute()
        logger.info(f"Added to playlist: {playlist_id}")
    except Exception as e:
        logger.warning(f"Failed to add to playlist: {e}")


def list_playlists() -> list:
    """
    List user's YouTube playlists.

    Returns:
        List of dictionaries with playlist id and title
    """
    try:
        youtube = authenticate_youtube()
        response = youtube.playlists().list(part="snippet", mine=True, maxResults=50).execute()

        playlists = []
        for item in response.get("items", []):
            playlists.append(
                {"id": item["id"], "title": item["snippet"]["title"]}
            )
        return playlists
    except Exception as e:
        logger.error(f"Failed to list playlists: {e}")
        return []


def create_playlist(title: str, description: str = "", privacy: str = "unlisted") -> Optional[str]:
    """
    Create a new YouTube playlist.

    Args:
        title: Playlist title
        description: Playlist description
        privacy: Privacy setting

    Returns:
        Playlist ID if successful, None otherwise
    """
    try:
        youtube = authenticate_youtube()
        response = youtube.playlists().insert(
            part="snippet,status",
            body={
                "snippet": {"title": title, "description": description},
                "status": {"privacyStatus": privacy},
            },
        ).execute()
        playlist_id = response["id"]
        logger.info(f"Created playlist: {title} (ID: {playlist_id})")
        return playlist_id
    except Exception as e:
        logger.error(f"Failed to create playlist: {e}")
        return None

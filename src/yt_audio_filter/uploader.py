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
YOUTUBEUPLOADER_TOKEN_FILE = CREDENTIALS_DIR / "youtubeuploader_token.json"


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
    Generate a transformative title for musicless video.

    Args:
        original_title: Original video title

    Returns:
        Transformative title with extracted keywords
    """
    # Extract keywords from original title (remove common filler words)
    import re
    
    # Remove special characters, emojis, and extra whitespace
    cleaned = re.sub(r'[^\w\s]', ' ', original_title)
    words = cleaned.split()
    
    # Common filler words to remove (Turkish and English)
    filler_words = {
        'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for',
        'of', 'with', 'by', 'from', 'is', 'are', 'was', 'were', 'be', 'been',
        'being', 'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would',
        'could', 'should', 'may', 'might', 'must', 'shall', 'can', 'this',
        'that', 'these', 'those', 'i', 'you', 'he', 'she', 'it', 'we', 'they',
        've', 'bir', 'bu', 'su', 'ile', 'icin', 'için', 'da', 'de', 'mi',
        've', 'ama', 'ile', 'den', 'dan', 'en', 'çok', 'cok', 'daha',
    }
    
    # Keep meaningful words (longer than 2 chars and not filler)
    keywords = [w for w in words if len(w) > 2 and w.lower() not in filler_words]
    
    # Take top keywords, limit to ~5-6 for readability
    keywords = keywords[:6]
    
    if keywords:
        keyword_str = ' '.join(keywords)
    else:
        # Fallback if no keywords extracted
        keyword_str = original_title[:30]
    
    # Format: "🔇 Music Removed - keyword1 keyword2 keyword3"
    prefix = "🔇 Music Removed - "
    max_len = 100 - len(prefix)
    
    if len(keyword_str) > max_len:
        keyword_str = keyword_str[:max_len - 3] + "..."
    
    return f"{prefix}{keyword_str}"


def generate_seo_description(
    original_title: str,
    original_description: str,
    original_channel: str,
    original_video_id: str,
) -> str:
    """
    Generate a description for musicless video.

    Creates description focused on accessibility benefits.

    Args:
        original_title: Original video title
        original_description: Original video description
        original_channel: Original channel name
        original_video_id: Original YouTube video ID

    Returns:
        Description with attribution
    """
    # Create description focused on accessibility purpose
    description = f"""🔇 Background Music Removed

Audio processed with AI to remove background music. Speech, dialogue, and sound effects preserved.

Great for:
• Sensory-sensitive viewers
• Dialogue-focused watching
• Reduced audio distractions

📺 From: {original_channel}

#NoBackgroundMusic #Musicless #AccessibleContent #SensoryFriendly #NoMusic"""

    return description


def sanitize_youtube_tag(tag: str) -> Optional[str]:
    """
    Sanitize a single tag for YouTube requirements.
    
    YouTube tag rules:
    - No < or > characters
    - Max 30 characters per tag
    - No leading/trailing whitespace
    - Cannot be empty
    - Only alphanumeric, spaces, and basic punctuation
    
    Args:
        tag: Raw tag string
        
    Returns:
        Sanitized tag or None if invalid
    """
    import re
    import unicodedata
    
    if not tag or not isinstance(tag, str):
        return None
    
    # Normalize unicode characters (e.g., combining characters)
    tag = unicodedata.normalize('NFC', tag)
    
    # Strip whitespace
    tag = tag.strip()
    
    # Remove < and > characters (YouTube rejects these)
    tag = re.sub(r'[<>]', '', tag)
    
    # Remove other potentially problematic characters
    tag = re.sub(r'[\x00-\x1f\x7f]', '', tag)  # Control characters
    
    # Remove hashtags (YouTube doesn't allow # in tags)
    tag = tag.replace('#', '')
    
    # Remove quotes and other problematic punctuation
    tag = re.sub(r'["\'\[\]{}|\\^`~]', '', tag)
    
    # Remove zero-width characters and other invisible unicode
    tag = re.sub(r'[\u200b-\u200f\u2028-\u202f\u205f-\u206f\ufeff]', '', tag)
    
    # Collapse multiple spaces
    tag = re.sub(r'\s+', ' ', tag).strip()
    
    # Check if still valid after cleaning
    if not tag or len(tag) < 2:
        return None
    
    # Truncate to max 30 characters (YouTube limit per tag)
    if len(tag) > 30:
        tag = tag[:30].strip()
    
    return tag


def generate_seo_tags(original_tags: List[str]) -> List[str]:
    """
    Generate SEO-optimized tags combining original tags with musicless keywords.

    Args:
        original_tags: Original video tags

    Returns:
        Combined and optimized tag list
    """
    # Start with musicless-specific tags (high priority) - these are safe ASCII
    tags = SEO_KEYWORDS.copy()
    total_chars = sum(len(t) for t in tags)
    MAX_TOTAL_CHARS = 450  # YouTube limit is 500, leave buffer

    # Add original tags (limited to avoid YouTube's 500 char tag limit)
    # Skip original tags entirely for now as they often cause "invalid keywords" errors
    # YouTube API is very strict about what characters it accepts in tags
    # Original tags often have unicode/special chars that work in Studio but not API
    
    # Uncomment below to try adding original tags (may cause issues):
    # for tag in original_tags:
    #     sanitized = sanitize_youtube_tag(tag)
    #     if sanitized and total_chars + len(sanitized) <= MAX_TOTAL_CHARS:
    #         if sanitized.lower() not in [t.lower() for t in tags]:
    #             tags.append(sanitized)
    #             total_chars += len(sanitized)

    final_tags = tags[:30]  # YouTube allows max 30 tags
    
    # Log final tags for debugging
    total_len = sum(len(t) for t in final_tags)
    logger.debug(f"Final tags for upload ({len(final_tags)} tags, {total_len} chars): {final_tags}")
    
    return final_tags


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
    # Check common locations
    locations = [
        # In the package directory (where cli.py lives)
        Path(__file__).parent.parent.parent / "youtubeuploader.exe",
        # In project root
        Path.cwd() / "youtubeuploader.exe",
        # In credentials directory
        CREDENTIALS_DIR / "youtubeuploader.exe",
    ]

    for loc in locations:
        if loc.exists():
            return loc

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

    SCOPES = [
        "https://www.googleapis.com/auth/youtube.upload",
        "https://www.googleapis.com/auth/youtube.readonly",
    ]

    credentials = None

    # Load saved credentials if they exist
    if OAUTH_TOKEN_FILE.exists():
        try:
            with open(OAUTH_TOKEN_FILE, "rb") as token:
                credentials = pickle.load(token)
                # Check if credentials have all required scopes
                if credentials and hasattr(credentials, "scopes"):
                    required_scopes = set(SCOPES)
                    current_scopes = set(credentials.scopes or [])
                    if not required_scopes.issubset(current_scopes):
                        logger.info("Credentials missing required scopes, re-authenticating...")
                        credentials = None
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
    logger.debug(f"Upload tags ({len(tags)}): {tags}")

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


# Cache for uploaded source video IDs
_uploaded_source_ids_cache: Optional[dict] = None


def _extract_source_video_id(description: str) -> Optional[str]:
    """
    Extract the original source video ID from an uploaded video's description.

    The description footer contains: 📺 Original: https://youtube.com/watch?v={video_id}

    Args:
        description: Video description text

    Returns:
        Original video ID if found, None otherwise
    """
    import re

    # Match youtube.com/watch?v= or youtu.be/ patterns
    patterns = [
        r"Original:\s*https?://(?:www\.)?youtube\.com/watch\?v=([a-zA-Z0-9_-]{11})",
        r"Original:\s*https?://youtu\.be/([a-zA-Z0-9_-]{11})",
    ]

    for pattern in patterns:
        match = re.search(pattern, description)
        if match:
            return match.group(1)

    return None


def get_uploaded_source_ids(force_refresh: bool = False) -> dict:
    """
    Get a mapping of source video IDs to uploaded video info.

    Queries the user's YouTube channel for videos with "[No Background Music]" in the title,
    then extracts the original source video ID from each description.

    Args:
        force_refresh: Force refresh of the cache

    Returns:
        Dict mapping source_video_id -> {"uploaded_id": str, "title": str, "url": str}
    """
    global _uploaded_source_ids_cache

    if _uploaded_source_ids_cache is not None and not force_refresh:
        return _uploaded_source_ids_cache

    if not check_upload_dependencies():
        logger.warning("Upload dependencies not installed, cannot check for duplicates")
        return {}

    try:
        youtube = authenticate_youtube()

        # Get user's channel ID
        channels_response = youtube.channels().list(part="contentDetails", mine=True).execute()

        if not channels_response.get("items"):
            logger.warning("Could not find user's channel")
            return {}

        uploads_playlist_id = channels_response["items"][0]["contentDetails"]["relatedPlaylists"][
            "uploads"
        ]

        # Fetch all uploaded videos
        source_ids = {}
        next_page_token = None

        while True:
            playlist_response = youtube.playlistItems().list(
                part="snippet",
                playlistId=uploads_playlist_id,
                maxResults=50,
                pageToken=next_page_token,
            ).execute()

            for item in playlist_response.get("items", []):
                snippet = item["snippet"]
                title = snippet.get("title", "")
                description = snippet.get("description", "")
                video_id = snippet["resourceId"]["videoId"]

                # Only check videos that look like our processed videos
                if "[No Background Music]" in title:
                    source_id = _extract_source_video_id(description)
                    if source_id:
                        source_ids[source_id] = {
                            "uploaded_id": video_id,
                            "title": title,
                            "url": f"https://youtube.com/watch?v={video_id}",
                        }

            next_page_token = playlist_response.get("nextPageToken")
            if not next_page_token:
                break

        _uploaded_source_ids_cache = source_ids
        logger.info(f"Found {len(source_ids)} already-processed videos on channel")
        return source_ids

    except Exception as e:
        logger.error(f"Failed to fetch uploaded videos: {e}")
        return {}


def is_video_already_uploaded(source_video_id: str, force_refresh: bool = False) -> Optional[dict]:
    """
    Check if a source video has already been processed and uploaded.

    Args:
        source_video_id: The original YouTube video ID to check
        force_refresh: Force refresh the cache of uploaded videos

    Returns:
        Dict with uploaded video info if already uploaded, None otherwise
        {"uploaded_id": str, "title": str, "url": str}
    """
    uploaded = get_uploaded_source_ids(force_refresh=force_refresh)
    return uploaded.get(source_video_id)


def clear_upload_cache() -> None:
    """Clear the uploaded videos cache, forcing a refresh on next check."""
    global _uploaded_source_ids_cache
    _uploaded_source_ids_cache = None
    logger.debug("Upload cache cleared")

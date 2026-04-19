"""pytubefix-backed downloader.

Application-less alternative to the YTDownloader.exe GUI fallback. Tries a
cascade of pytubefix "clients" (ANDROID_VR, IOS, WEB, ...) until one yields
downloadable streams, then fetches the best video-only or audio-only track.

Why a cascade: YouTube's bot-detection flags different clients for different
videos unpredictably. ANDROID_VR tends to work best for non-protected content;
WEB/MWEB need a working JS interpreter (Deno/Node) for signature decoding;
IOS often returns HTTP 400 for certain videos. We try them in order of
real-world reliability and stop on the first that returns usable streams.
"""

from pathlib import Path
from typing import List, Literal, Optional

from .exceptions import YouTubeDownloadError
from .logger import get_logger

logger = get_logger()

PytubeMode = Literal["video-only", "audio-only"]

# Order matters: ANDROID_VR is the most permissive client as of early 2026,
# followed by ANDROID/IOS (plain), then WEB variants (need JS interp).
_CLIENT_CASCADE = ("ANDROID_VR", "IOS", "ANDROID", "MWEB", "TV", "WEB")


def check_pytubefix_available() -> bool:
    try:
        import pytubefix  # noqa: F401
        return True
    except ImportError:
        return False


def download_with_pytubefix(
    url: str,
    output_dir: Path,
    mode: PytubeMode,
    filename_prefix: str,
    video_id: str,
) -> Path:
    """Download the best matching stream via pytubefix.

    Raises YouTubeDownloadError if every client in the cascade failed.
    """
    if not check_pytubefix_available():
        raise YouTubeDownloadError(
            "pytubefix not installed",
            "Install with: pip install pytubefix",
        )

    from pytubefix import YouTube
    from pytubefix.exceptions import PytubeFixError

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    errors: List[str] = []
    for client in _CLIENT_CASCADE:
        try:
            yt = YouTube(url, client=client)
            # Touching .title triggers availability check; pytubefix raises
            # here if the client got bot-detected or login-required.
            _ = yt.title
            if mode == "video-only":
                stream = yt.streams.filter(only_video=True).order_by("resolution").desc().first()
            else:
                stream = yt.streams.filter(only_audio=True).order_by("abr").desc().first()
            if stream is None:
                errors.append(f"[{client}] no {mode} streams found")
                continue

            ext = (stream.mime_type or "video/mp4").split("/")[-1]
            target_name = f"{filename_prefix}_{video_id}.{ext}"
            logger.info(
                f"pytubefix [{client}] {mode}: itag={stream.itag} "
                f"{getattr(stream, 'resolution', None) or getattr(stream, 'abr', None)} "
                f"{stream.video_codec or stream.audio_codec}"
            )
            result_path = Path(
                stream.download(output_path=str(output_dir), filename=target_name)
            )
            if not result_path.exists() or result_path.stat().st_size == 0:
                errors.append(f"[{client}] download produced empty file")
                continue
            return result_path
        except PytubeFixError as e:
            errors.append(f"[{client}] {type(e).__name__}: {str(e)[:120]}")
        except Exception as e:
            errors.append(f"[{client}] {type(e).__name__}: {str(e)[:120]}")

    raise YouTubeDownloadError(
        f"pytubefix failed on every client for {url}",
        "Tried clients " + ", ".join(_CLIENT_CASCADE) + ":\n  - " + "\n  - ".join(errors),
    )

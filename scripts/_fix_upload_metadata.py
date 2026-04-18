"""One-off: patch a broken-title live YouTube video with corrected metadata.

Run once to fix QxOv5GXTKYY which was uploaded before the surah regex fix
(Qaf regex boundary failed on underscore). User authorized this update.
"""

from yt_audio_filter.logger import setup_logger
from yt_audio_filter.metadata import load_metadata
from yt_audio_filter.overlay_pipeline import _build_auto_vars
from yt_audio_filter.surah_detector import detect_reciter, detect_surah
from yt_audio_filter.uploader import authenticate_youtube
from yt_audio_filter.yt_metadata import fetch_yt_metadata

VIDEO_ID = "QxOv5GXTKYY"
AUDIO_URL = "https://www.youtube.com/watch?v=Sjnf-GzVcoc"


def main() -> None:
    setup_logger(verbose=True)

    meta = load_metadata("examples/metadata-surah-arrahman.json")
    audio_meta = fetch_yt_metadata(AUDIO_URL)
    surah = detect_surah(audio_meta.title) or detect_surah(audio_meta.description)
    reciter = detect_reciter(audio_meta.title) or detect_reciter(audio_meta.description)
    auto = _build_auto_vars(audio_meta, surah, reciter)

    title = meta.render_title(extra_vars=auto)
    description = meta.render_description(extra_vars=auto)
    print(f"NEW TITLE:       {title!r}")
    print(f"NEW DESCRIPTION: {description!r}")

    yt = authenticate_youtube()
    current = yt.videos().list(part="snippet,status", id=VIDEO_ID).execute()
    if not current.get("items"):
        raise SystemExit(f"Video {VIDEO_ID} not found on channel")

    snippet = current["items"][0]["snippet"]
    snippet["title"] = title
    snippet["description"] = description
    snippet["tags"] = meta.tags
    snippet["categoryId"] = meta.category_id

    yt.videos().update(
        part="snippet",
        body={"id": VIDEO_ID, "snippet": snippet},
    ).execute()

    print(f"Updated https://youtube.com/watch?v={VIDEO_ID}")


if __name__ == "__main__":
    main()

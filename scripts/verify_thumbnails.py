"""Check whether published videos really show their source's thumbnail.

The backfill's own log says what it *believed* it did. This asks YouTube
instead, and compares the pictures rather than the bytes: a thumbnail is
re-encoded on upload, so the file that comes back is never byte-identical to
the one that went up even when the image is the same.

The comparison downscales both to 16x16 greyscale and measures mean absolute
difference. Re-encoding moves that by a couple of levels; a different frame of
the same cartoon moves it by tens. The threshold sits between the two, and
every score is printed so a borderline case can be eyeballed rather than
trusted.
"""

from __future__ import annotations

import argparse
import io
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

from yt_audio_filter import uploader  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from backfill_thumbnails import recoverable_sources  # noqa: E402

#: Mean absolute difference, 0-255, below which two images are the same
#: picture. JPEG re-encoding alone lands around 1-3; a different frame of the
#: same cartoon is comfortably above 10.
SAME_PICTURE_THRESHOLD = 6.0


def fetch(video_id: str):
    """The best thumbnail YouTube currently serves for a video, as an image."""
    for name in ("maxresdefault", "sddefault", "hqdefault"):
        try:
            request = urllib.request.Request(
                f"https://i.ytimg.com/vi/{video_id}/{name}.jpg",
                headers={"Cache-Control": "no-cache"},
            )
            with urllib.request.urlopen(request, timeout=20) as response:
                data = response.read()
            if data:
                return Image.open(io.BytesIO(data))
        except Exception:  # noqa: BLE001 - a missing size is normal
            continue
    return None


def difference(a, b) -> float:
    """Mean absolute difference between two images, ignoring size and colour."""
    fa = np.asarray(a.convert("L").resize((16, 16), Image.LANCZOS), dtype=float)
    fb = np.asarray(b.convert("L").resize((16, 16), Image.LANCZOS), dtype=float)
    return float(np.abs(fa - fb).mean())


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=0, help="Only check the first N.")
    args = parser.parse_args(argv)

    rows = sorted(recoverable_sources().items(), key=lambda kv: str(kv[1].get("title", "")))
    if args.limit:
        rows = rows[: args.limit]

    match = differ = unknown = 0
    mismatched = []
    for source_id, record in rows:
        uploaded_id = record.get("uploaded_id") or record.get("video_id") or ""
        title = str(record.get("title", ""))[:46]
        source_image, uploaded_image = fetch(source_id), fetch(uploaded_id)
        if source_image is None or uploaded_image is None:
            print(f"  ?    {title:<46}  could not fetch both thumbnails")
            unknown += 1
            continue
        score = difference(source_image, uploaded_image)
        if score <= SAME_PICTURE_THRESHOLD:
            print(f"  same {title:<46}  diff {score:5.2f}")
            match += 1
        else:
            print(f"  DIFF {title:<46}  diff {score:5.2f}  -> {uploaded_id}")
            differ += 1
            mismatched.append((source_id, uploaded_id, title))

    print(f"\n{match} match, {differ} differ, {unknown} unknown, of {len(rows)} checked.")
    if mismatched:
        print("\nStill to do:")
        for source_id, uploaded_id, title in mismatched:
            print(f"  {source_id} -> {uploaded_id}  {title}")
    return 1 if differ else 0


if __name__ == "__main__":
    raise SystemExit(main())

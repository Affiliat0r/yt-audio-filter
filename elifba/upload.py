#!/usr/bin/env python
"""Upload a rendered elifba lesson to YouTube.

Metadata is built from the timeline the video was actually rendered from, so
the chapter marks cannot drift from the picture: each letter's chapter is that
letter's own naming beat, read out of ``elifba/scene/timeline.js``.

Uploads private by default. A lesson is ten minutes of a brand-new series;
landing it on the channel unlisted-to-the-world lets it be watched through
before subscribers get it, and flipping it to public afterwards is one click
in Studio.

    python elifba/upload.py --video elifba/out/elifba-full-30-letters.mp4
    python elifba/upload.py --video ... --privacy public --playlist "Elif Ba"
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

# The metadata is Turkish and the chapter list is Arabic, but a Windows console
# is cp1252 by default -- printing the title would raise UnicodeEncodeError and
# kill the run before a byte was uploaded.
for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):  # already wrapped, or not a TTY
        pass

from yt_audio_filter.uploader import (  # noqa: E402
    upload_with_explicit_metadata,
)

TIMELINE = REPO / "elifba" / "scene" / "timeline.js"

TAGS = [
    "elif ba",
    "elifba",
    "elif ba öğreniyorum",
    "harekeler",
    "üstün esre ötre",
    "arapça harfler",
    "kuran öğreniyorum",
    "çocuklar için",
    "okul öncesi",
    "diyanet elif ba",
    "müziksiz",
    "elif ba dersleri",
]


def load_timeline() -> dict:
    """Read the generated scene timeline back into Python."""
    raw = TIMELINE.read_text(encoding="utf-8")
    body = re.sub(r"^[\s\S]*?window\.__timeline\s*=\s*", "", raw).rstrip()
    body = body.rstrip(";").rstrip()
    return json.loads(body)


def timestamp(seconds: float) -> str:
    total = int(seconds)
    return f"{total // 60}:{total % 60:02d}"


def build_chapters(timeline: dict) -> list[str]:
    """One chapter per letter, at the beat where that letter is named.

    YouTube only renders chapters when the first one is at 0:00, so the
    opening title beat supplies it rather than the first letter.
    """
    lines = ["0:00 Başlıyoruz"]
    for seg in timeline["segments"]:
        if seg["kind"] == "letter":
            lines.append(f"{timestamp(seg['start'])} {seg['say']} {seg['glyph']}")
    return lines


def build_description(timeline: dict, chapters: list[str]) -> str:
    letters = [s["say"] for s in timeline["segments"] if s["kind"] == "letter"]
    return "\n".join(
        [
            "Elif Ba öğreniyoruz! Bu derste bütün harfleri ve üç harekeyi "
            "(üstün, esre, ötre) birlikte çalışıyoruz.",
            "",
            "Her harf için sıra şöyle:",
            "1. Harfi tanıyoruz",
            "2. Üstün ile okuyoruz",
            "3. Esre ile okuyoruz",
            "4. Ötre ile okuyoruz",
            "5. \"Şimdi sen söyle!\" — çocuğun tekrar etmesi için sessiz bir an",
            "",
            f"Toplam {len(letters)} harf: " + ", ".join(letters),
            "",
            "Harflerin sesleri Arapça telaffuzla okunmuştur; harf isimleri "
            "Diyanet Elif Ba kitabındaki gibi Türkçedir.",
            "",
            "🎵 Müziksiz — arka planda müzik yoktur.",
            "",
            "BÖLÜMLER",
            *chapters,
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video", required=True, type=Path)
    parser.add_argument(
        "--title",
        default="Elif Ba Öğreniyorum | Harfler ve Harekeler | Üstün Esre Ötre | Müziksiz",
    )
    parser.add_argument("--privacy", default="private",
                        choices=["private", "unlisted", "public"])
    parser.add_argument("--playlist-id", default=None)
    parser.add_argument("--dry-run", action="store_true",
                        help="print the metadata and exit without uploading")
    args = parser.parse_args()

    if not args.video.exists():
        parser.error(f"no such video: {args.video}")

    timeline = load_timeline()
    chapters = build_chapters(timeline)
    description = build_description(timeline, chapters)

    print(f"file        {args.video}  ({args.video.stat().st_size / 1e6:.1f} MB)")
    print(f"title       {args.title}  ({len(args.title)} chars)")
    print(f"privacy     {args.privacy}")
    print(f"chapters    {len(chapters)}")
    print(f"tags        {', '.join(TAGS)}")
    print("-" * 70)
    print(description)
    print("-" * 70)

    if args.dry_run:
        print("dry run — nothing uploaded")
        return 0

    video_id = upload_with_explicit_metadata(
        video_path=args.video,
        title=args.title,
        description=description,
        tags=TAGS,
        category_id="27",  # Education
        privacy=args.privacy,
        playlist_id=args.playlist_id,
    )
    print(f"\nuploaded: https://youtu.be/{video_id}")
    print(f"studio:   https://studio.youtube.com/video/{video_id}/edit")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

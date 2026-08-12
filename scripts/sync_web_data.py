"""Regenerate the static JSON the Next.js Studio ships with.

The web app runs on Vercel and cannot import the Python package, so the surah
list, reciter list, render presets, and channel list are baked into
``web/data/`` at build time. This script is the only thing that should write
those files — run it whenever the Python source of truth changes:

    python scripts/sync_web_data.py

Verify afterwards with ``git diff web/data``.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

WEB_DATA = REPO_ROOT / "web" / "data"


def _write(name: str, payload: object) -> None:
    WEB_DATA.mkdir(parents=True, exist_ok=True)
    path = WEB_DATA / name
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    count = len(payload) if isinstance(payload, list) else "-"
    print(f"  {name:16} {count} entries")


def sync_surahs() -> None:
    from yt_audio_filter import ayah_data, surah_detector

    seen: dict[int, str] = {}
    for name, number, _patterns in surah_detector._SURAHS:
        if number is None or number in seen:
            continue
        seen[number] = name

    surahs = []
    for number in sorted(seen):
        try:
            ayah_count = int(ayah_data.ayah_count(number))
        except Exception:
            ayah_count = 0
        surahs.append(
            {"number": number, "name": seen[number], "ayahCount": ayah_count}
        )
    _write("surahs.json", surahs)


def sync_reciters() -> None:
    from yt_audio_filter import ayah_data, quran_audio_source

    everyayah = {e["quranicaudio_slug"] for e in ayah_data.EVERYAYAH_RECITERS.values()}
    excluded = set(getattr(ayah_data, "RECITERS_WITHOUT_EVERYAYAH", ()) or ())

    reciters = [
        {
            "slug": r.slug,
            "displayName": r.display_name,
            "arabicName": getattr(r, "arabic_name", ""),
            "sampleUrl": r.sample_url,
            "supportsAyah": r.slug in everyayah and r.slug not in excluded,
        }
        for r in quran_audio_source.list_reciters()
    ]
    _write("reciters.json", reciters)


def sync_presets() -> None:
    from yt_audio_filter import render_presets

    presets = [
        {
            "slug": p.slug,
            "label": p.display_name,
            "width": p.resolution[0],
            "height": p.resolution[1],
        }
        for p in render_presets.list_presets()
    ]
    _write("presets.json", presets)


def sync_channels() -> None:
    src = REPO_ROOT / "config" / "channels.json"
    WEB_DATA.mkdir(parents=True, exist_ok=True)
    shutil.copy(src, WEB_DATA / "channels.json")
    payload = json.loads(src.read_text(encoding="utf-8"))
    print(f"  {'channels.json':16} {len(payload['channels'])} entries")


def main() -> int:
    print(f"Syncing web data -> {WEB_DATA}")
    sync_surahs()
    sync_reciters()
    sync_presets()
    sync_channels()
    print("Done. Review with: git diff web/data")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

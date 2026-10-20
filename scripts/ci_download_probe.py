"""Can a GitHub-hosted runner download this channel's sources?

The local worker downloads from a residential IP, which is why YouTube's bot
check leaves it alone. A GitHub runner is a datacenter IP, so before any of the
pipeline moves there this measures the one thing that would sink it: whether
``youtube.download_stream`` — the exact chain the worker uses, unchanged —
still returns bytes from one.

Each case is ``mode:video_id``. Every file is probed with ffprobe and then
deleted: the repo is public, so nothing downloaded here is kept as an
artifact::

    python scripts/ci_download_probe.py
    python scripts/ci_download_probe.py video-only:U_EhMEOolI0 audio-only:0VIXkx8oSJM

Exits non-zero if any case fails, and writes a table to the job summary when
``GITHUB_STEP_SUMMARY`` is set.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import List, Optional

from yt_audio_filter import youtube

#: Two of the nine sources ``youtube.YTDLP_CLIENT_ATTEMPTS`` was measured on,
#: one recitation, and one music-removal download. Together they cover all
#: three stream modes and the metadata lookup the auto-SEO upload needs.
DEFAULT_CASES = [
    "video-only:U_EhMEOolI0",
    "video-only:itBhCjx-6fc",
    "audio-only:0VIXkx8oSJM",
    "video+audio:ezmsrB59mj8",
]

MODES = ("video-only", "audio-only", "video+audio")


def probe(path: Path) -> dict:
    """Duration, size and per-stream codec/resolution of a downloaded file."""
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration,size:stream=codec_type,codec_name,width,height",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    data = json.loads(result.stdout or "{}")
    streams = []
    for s in data.get("streams", []):
        if s.get("codec_type") == "video":
            streams.append(f"{s.get('codec_name')} {s.get('width')}x{s.get('height')}")
        elif s.get("codec_type") == "audio":
            streams.append(s.get("codec_name", "audio"))
    fmt = data.get("format", {})
    return {
        "duration": float(fmt.get("duration") or 0),
        "size_mb": int(fmt.get("size") or path.stat().st_size) / 1e6,
        "streams": ", ".join(streams) or "none",
    }


def run_case(case: str, workdir: Path) -> dict:
    mode, _, video_id = case.partition(":")
    if mode not in MODES or not video_id:
        raise SystemExit(f"Bad case {case!r}; expected one of {MODES} then ':<video_id>'")

    url = f"https://www.youtube.com/watch?v={video_id}"
    started = time.monotonic()
    row = {"case": case, "ok": False, "detail": ""}
    try:
        if mode == "video+audio":
            # The music-removal entry point, so the metadata lookup is tested too.
            meta = youtube.download_video_with_metadata(url, workdir, use_cache=False)
            path: Optional[Path] = meta.file_path
            row["detail"] = f"title: {meta.title[:60]}"
        else:
            path = youtube.download_stream(url, workdir, mode=mode, use_cache=False)
        row.update(probe(path))
        row["ok"] = row["size_mb"] > 0
        path.unlink(missing_ok=True)
    except Exception as exc:  # noqa: BLE001 - every failure is a result here
        message = getattr(exc, "message", None) or str(exc)
        row["detail"] = f"{type(exc).__name__}: {message}"[:300]
    row["seconds"] = time.monotonic() - started
    return row


def summary_table(rows: List[dict]) -> str:
    lines = [
        "| case | result | duration | size | streams | took | detail |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        duration = f"{r['duration'] / 60:.1f} min" if r.get("duration") else ""
        size = f"{r['size_mb']:.1f} MB" if r.get("size_mb") else ""
        detail = r["detail"].replace("|", "\\|").replace("\n", " ")
        lines.append(
            f"| `{r['case']}` | {'ok' if r['ok'] else '**FAILED**'} | {duration} | {size} "
            f"| {r.get('streams', '')} | {r['seconds']:.0f} s | {detail} |"
        )
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("cases", nargs="*", help="mode:video_id (default: a fixed set)")
    args = parser.parse_args(argv)

    workdir = Path(tempfile.mkdtemp(prefix="download-probe-", dir=os.environ.get("RUNNER_TEMP")))
    try:
        rows = [run_case(case, workdir) for case in (args.cases or DEFAULT_CASES)]
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    cookies = "yes" if youtube.cookies_file() else "none"
    table = f"Cookies: {cookies}\n\n" + summary_table(rows)
    print(table)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write("## Download probe\n\n" + table + "\n")
    return 0 if all(r["ok"] for r in rows) else 1


if __name__ == "__main__":
    sys.exit(main())

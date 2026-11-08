"""Screen a YouTube video for shirk/sihr elements before it is rendered.

Compilations hide holiday episodes behind a title that names only the first
one, and captions alone miss decorations and unlisted words, so this does both:

  1. Turkish auto-captions, scanned for holiday/magic keywords with timestamps.
  2. Episode split on the ~0.24s black frame between episodes, then sampled
     frames per episode laid out on contact sheets to look at.

Usage: python scripts/screen_video.py <video-id-or-url> [frames_per_episode]
Prints the episode boundaries, the keyword hits mapped to episodes, and the
contact sheet paths (cache/_screen/<id>_N.jpg) to open and look at.
"""

import re
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw

from yt_audio_filter.youtube import download_stream

CACHE = Path("cache")
OUT = CACHE / "_screen"
KEYWORDS = re.compile(
    r"cadılar|halloween|hayalet|iskelet|balkabağ|balk kabağ|kabak|noel|yılbaşı|"
    r"\bçam\b|hediye|kostüm|ölülerin günü|muertos|paskalya|sevgililer|"
    r"cadı|büyücü|sihirli|büyülü|\bperi\b|\bcin\b|dilek|doğum günü",
    re.IGNORECASE,
)
# The channel is "Muziksiz": stories about musicians, singing or instruments are
# out too, and music removal keeps vocals, so a sung song would survive it.
SINGING = re.compile(r"\[(?:şarkı|ilahi|şarkı söyleme|şarkı söylüyor|koro)[^\]]*\]", re.IGNORECASE)
MUSIC = re.compile(
    r"şarkı|ninni|müzik|mızık|enstrüman|gitar|piyano|keman|davul|orkestra|koro\b|"
    r"konser|nağme|melodi|flüt|trompet|şarkıcı|\bmarş\b",
    re.IGNORECASE,
)


def video_id(arg: str) -> str:
    match = re.search(r"(?:v=|youtu\.be/)([\w-]{11})", arg)
    return match.group(1) if match else arg


def caption_hits(vid: str) -> list:
    """Keyword hits, or None when no captions could be fetched.

    None is not "clean": zero hits over zero cues proves nothing, so the caller
    has to say so out loud.
    """
    base = OUT / f"cap_{vid}"
    run = subprocess.run(
        [sys.executable, "-m", "yt_dlp", "--write-auto-sub", "--skip-download", "--sub-lang", "tr",
         "--no-warnings", "--extractor-args", "youtubepot-bgutilscript:script_path=__disabled__",
         "-o", f"{base}.%(ext)s", f"https://www.youtube.com/watch?v={vid}"],
        capture_output=True, text=True,
    )
    vtt = Path(f"{base}.tr.vtt")
    if not vtt.exists():
        tail = (run.stderr or run.stdout).strip().splitlines()[-1:] or ["no output"]
        print(f"WARNING: no captions fetched ({tail[0][:120]})")
        return None
    hits, seen = [], set()
    cues = re.findall(r"(\d\d):(\d\d):(\d\d)\.\d+ --> .*?\n(.*?)(?=\n\n|\Z)",
                      vtt.read_text(encoding="utf-8"), flags=re.S)
    print(f"captions: scanned {len(cues)} cues")
    for h, m, s, body in cues:
        text = re.sub(r"<[^>]+>", "", body).replace("\n", " ").strip()
        # "[Müzik]", "[Alkış]", "[kahkaha]" are sound-effect tags YouTube adds,
        # not words anyone says; matching them buried every real hit in noise.
        # "[Şarkı söyleme]" is different: it means a character is singing, and
        # music removal keeps voices, so it is reported rather than dropped.
        for sung in SINGING.finditer(text):
            key = (int(h) * 3600 + int(m) * 60 + int(s)) // 20, "singing"
            if key not in seen:
                seen.add(key)
                hits.append((int(h) * 3600 + int(m) * 60 + int(s), "MUSIC:singing", sung.group(0)))
        text = re.sub(r"\[[^\]]*\]", " ", text).replace("&gt;&gt;", " ").strip()
        for kind, pattern in (("shirk", KEYWORDS), ("MUSIC", MUSIC)):
            for found in pattern.finditer(text):
                key = (int(h) * 3600 + int(m) * 60 + int(s)) // 20, found.group(0).lower()
                if key not in seen:
                    seen.add(key)
                    hits.append((int(h) * 3600 + int(m) * 60 + int(s),
                                 f"{kind}:{found.group(0).lower()}", text[:80]))
    return hits


def episode_bounds(path: Path) -> list:
    out = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", str(path), "-vf", "blackdetect=d=0.04:pix_th=0.12",
         "-an", "-f", "null", "-"], capture_output=True, text=True).stderr
    dur = re.search(r"Duration: (\d+):(\d+):([\d.]+)", out)
    total = int(dur[1]) * 3600 + int(dur[2]) * 60 + float(dur[3])
    blacks = [(float(a), float(b)) for a, b in re.findall(r"black_start:([\d.]+) black_end:([\d.]+)", out)]
    cuts = [(a + b) / 2 for a, b in blacks if 5 < a < total - 3]
    return [0.0] + cuts + [total]


def sheets(vid: str, path: Path, bounds: list, per_episode: int) -> list:
    frames = []
    for i, (start, end) in enumerate(zip(bounds[:-1], bounds[1:]), start=1):
        for k in range(per_episode):
            t = start + (end - start) * (k + 1) / (per_episode + 1)
            jpg = OUT / f"{vid}_f{i:02d}_{k}.jpg"
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{t:.2f}", "-i", str(path),
                            "-frames:v", "1", "-vf", "scale=320:180", str(jpg)], check=True)
            frames.append((i, t, jpg))
    paths, per_sheet = [], 30
    for n in range(0, len(frames), per_sheet):
        chunk = frames[n:n + per_sheet]
        rows = (len(chunk) + 5) // 6
        img = Image.new("RGB", (6 * 320, rows * 180), "black")
        draw = ImageDraw.Draw(img)
        for j, (i, t, jpg) in enumerate(chunk):
            x, y = (j % 6) * 320, (j // 6) * 180
            img.paste(Image.open(jpg), (x, y))
            draw.rectangle([x, y, x + 90, y + 16], fill="black")
            draw.text((x + 3, y + 2), f"E{i} {int(t // 60)}:{int(t % 60):02d}", fill="yellow")
        out = OUT / f"{vid}_{n // per_sheet + 1}.jpg"
        img.save(out, quality=85)
        paths.append(out)
    return paths


def main() -> None:
    vid = video_id(sys.argv[1])
    per_episode = int(sys.argv[2]) if len(sys.argv) > 2 else 2
    OUT.mkdir(parents=True, exist_ok=True)

    hits = caption_hits(vid)
    video = download_stream(url=f"https://www.youtube.com/watch?v={vid}", output_dir=CACHE, mode="video-only")
    bounds = episode_bounds(Path(video))
    print(f"{len(bounds) - 1} episode(s): " + ", ".join(f"{int(b // 60)}:{int(b % 60):02d}" for b in bounds))

    def episode_of(t: float) -> int:
        return next((i for i, e in enumerate(bounds[1:], start=1) if t < e), len(bounds) - 1)

    if hits is None:
        print("NO CAPTIONS: the keyword scan did not run, only the frames can clear this video")
        hits = []
    print(f"{len(hits)} caption keyword hit(s):")
    for t, word, text in hits:
        print(f"  E{episode_of(t)} {int(t // 60)}:{int(t % 60):02d}  {word:<14} {text}")
    print("contact sheets:")
    for p in sheets(vid, Path(video), bounds, per_episode):
        print("  ", p)


if __name__ == "__main__":
    main()

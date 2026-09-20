"""One-off generator for the channel avatar + banner, matching the existing
in-video watermark's exact colors (assets/muziksiz_logo.png) rather than
inventing a new palette. Run once; outputs go to the path given on argv.
"""

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ORANGE = (234, 124, 42, 255)
RED = (220, 40, 40, 255)
WHITE = (255, 255, 255, 255)
SHADOW = (40, 30, 20, 140)

FONT_BOLD = "C:/Windows/Fonts/arialbd.ttf"


def draw_icon(size: int) -> Image.Image:
    """Redraw the crossed-out music note at high resolution (vector-crisp,
    rather than upscaling the small source logo's raster crop)."""
    scale = 4  # supersample then downsize for clean anti-aliasing
    s = size * scale
    im = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)

    cx, cy = s / 2, s / 2
    r = s * 0.46
    ring_w = s * 0.09

    # Note head (filled ellipse) + stem + flag, in white with a thin dark
    # outline, sized to sit inside the prohibition circle.
    head_w, head_h = s * 0.30, s * 0.22
    head_cx, head_cy = s * 0.40, s * 0.66
    stem_x = head_cx + head_w * 0.42
    stem_top = s * 0.16
    stem_w = s * 0.07

    outline_w = max(2, int(s * 0.012))
    d.ellipse(
        [head_cx - head_w / 2, head_cy - head_h / 2, head_cx + head_w / 2, head_cy + head_h / 2],
        fill=WHITE,
        outline=SHADOW,
        width=outline_w,
    )
    d.rectangle(
        [stem_x - stem_w / 2, stem_top, stem_x + stem_w / 2, head_cy],
        fill=WHITE,
        outline=SHADOW,
        width=outline_w,
    )
    d.polygon(
        [
            (stem_x + stem_w / 2, stem_top),
            (stem_x + stem_w / 2 + s * 0.20, stem_top + s * 0.10),
            (stem_x + stem_w / 2, stem_top + s * 0.20),
        ],
        fill=WHITE,
        outline=SHADOW,
    )

    # Prohibition ring + slash on top of the note.
    d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=RED, width=int(ring_w))
    import math

    angle = math.radians(45)
    dx, dy = math.cos(angle) * r, math.sin(angle) * r
    d.line([(cx - dx, cy + dy), (cx + dx, cy - dy)], fill=RED, width=int(ring_w))

    im = im.resize((size, size), Image.LANCZOS)
    return im


def make_avatar(out_path: Path) -> None:
    size = 800
    im = Image.new("RGBA", (size, size), ORANGE)
    icon = draw_icon(int(size * 0.86))
    off = (size - icon.width) // 2
    im.alpha_composite(icon, (off, off))
    im.convert("RGB").save(out_path, "PNG")
    print(f"avatar -> {out_path} {im.size}")


def make_banner(out_path: Path) -> None:
    W, H = 2560, 1440
    im = Image.new("RGBA", (W, H), ORANGE)
    d = ImageDraw.Draw(im)

    # Safe area shown on every device: centered 1546x423.
    safe_w, safe_h = 1546, 423
    safe_x0 = (W - safe_w) // 2
    safe_y0 = (H - safe_h) // 2

    icon_size = int(safe_h * 0.92)
    icon = draw_icon(icon_size)
    icon_x = safe_x0
    icon_y = safe_y0 + (safe_h - icon_size) // 2
    im.alpha_composite(icon, (icon_x, icon_y))

    text_x = icon_x + icon_size + 50
    title_font = ImageFont.truetype(FONT_BOLD, 108)
    sub_font = ImageFont.truetype(FONT_BOLD, 70)

    def draw_text_with_shadow(xy, text, font, fill):
        x, y = xy
        d.text((x + 4, y + 4), text, font=font, fill=SHADOW)
        d.text((x, y), text, font=font, fill=fill)

    line1 = "MUZIKSIZ"
    line2 = "ÇİZGİ FİLİMLER"
    b1 = d.textbbox((0, 0), line1, font=title_font)
    b2 = d.textbbox((0, 0), line2, font=sub_font)
    h1, h2 = b1[3] - b1[1], b2[3] - b2[1]
    gap = 22
    block_h = h1 + gap + h2
    text_y0 = safe_y0 + (safe_h - block_h) // 2

    draw_text_with_shadow((text_x, text_y0 - b1[1]), line1, title_font, WHITE)
    draw_text_with_shadow((text_x, text_y0 + h1 + gap - b2[1]), line2, sub_font, WHITE)

    im.convert("RGB").save(out_path, "PNG")
    print(f"banner -> {out_path} {im.size}")


if __name__ == "__main__":
    out_dir = Path(sys.argv[1])
    out_dir.mkdir(parents=True, exist_ok=True)
    make_avatar(out_dir / "avatar_preview.png")
    make_banner(out_dir / "banner_preview.png")

"""Painting primitives for the Surah Nuh film.

Every frame is a float32 RGB array, H x W x 3, values roughly 0..1 in display
(sRGB-like) space. Nothing here loads an image or a font: shapes are numpy
fields, cv2 rasterisation, or pycairo paths.
"""

from __future__ import annotations

import functools
import math

import cairo
import cv2
import numpy as np

W, H = 1920, 1080
FPS = 24

YS = np.arange(H, dtype=np.float32)[:, None]  # pixel row, as a column
XS = np.arange(W, dtype=np.float32)[None, :]  # pixel column, as a row


# --------------------------------------------------------------------------
# small maths
# --------------------------------------------------------------------------


def hexc(s: str) -> np.ndarray:
    s = s.lstrip("#")
    return np.array([int(s[i : i + 2], 16) / 255.0 for i in (0, 2, 4)], np.float32)


def clamp01(x):
    return np.clip(x, 0.0, 1.0)


def smooth(e0, e1, x):
    """smoothstep, works on scalars and arrays"""
    t = np.clip((np.asarray(x, np.float32) - e0) / (e1 - e0 + 1e-9), 0.0, 1.0)
    return t * t * (3 - 2 * t)


def sm(e0, e1, x) -> float:
    return float(smooth(e0, e1, x))


def ease(t):
    t = min(max(t, 0.0), 1.0)
    return t * t * (3 - 2 * t)


def ease_out(t):
    t = min(max(t, 0.0), 1.0)
    return 1 - (1 - t) ** 3


def ease_in(t):
    t = min(max(t, 0.0), 1.0)
    return t**3


def lerp(a, b, t):
    return a + (b - a) * t


def mix(c1, c2, t):
    return (np.asarray(c1, np.float32) * (1 - t) + np.asarray(c2, np.float32) * t).astype(np.float32)


def blend(img, color, alpha):
    """img <- img*(1-alpha) + color*alpha ; alpha HxW or HxWx1 or scalar"""
    a = alpha if np.ndim(alpha) != 2 else alpha[..., None]
    img *= 1 - a
    img += np.asarray(color, np.float32) * a
    return img


# --------------------------------------------------------------------------
# noise
# --------------------------------------------------------------------------


class Noise1D:
    """Smooth 1-D value noise with fBm; deterministic per seed."""

    def __init__(self, seed: int, n: int = 4096):
        r = np.random.default_rng(seed)
        self.v = r.random(n).astype(np.float32) * 2 - 1
        self.n = n

    def __call__(self, x, octaves=5, lac=2.0, gain=0.5):
        x = np.asarray(x, np.float64)
        total = np.zeros_like(x, dtype=np.float32)
        amp, freq, norm = 1.0, 1.0, 0.0
        for o in range(octaves):
            xx = x * freq + o * 37.7
            i = np.floor(xx).astype(np.int64)
            f = (xx - i).astype(np.float32)
            u = f * f * (3 - 2 * f)
            a = self.v[i % self.n]
            b = self.v[(i + 1) % self.n]
            total += (a + (b - a) * u) * amp
            norm += amp
            amp *= gain
            freq *= lac
        return total / norm


@functools.lru_cache(maxsize=32)
def fbm_tex(w: int, h: int, seed: int, base: int = 4, octaves: int = 6, gain: float = 0.5) -> np.ndarray:
    """Seamlessly tileable fBm texture (spectral synthesis), values 0..1, (h, w).
    `base` = feature cycles across the width; `octaves` sets the finest detail."""
    r = np.random.default_rng(seed)
    kx = np.fft.fftfreq(w) * w
    ky = np.fft.fftfreq(h) * h * (w / h)
    k = np.sqrt(kx[None, :] ** 2 + ky[:, None] ** 2)
    beta = -math.log2(gain)  # gain .5 -> amplitude ~ 1/k
    amp = np.where(k > 0, (k + 1e-9) ** (-beta - 1.0), 0.0)
    lo, hi = base * 0.7, base * (2 ** octaves)
    amp *= 1 / (1 + (lo / (k + 1e-9)) ** 6)
    amp *= 1 / (1 + (k / hi) ** 4)
    spec = amp * (r.normal(size=(h, w)) + 1j * r.normal(size=(h, w)))
    out = np.real(np.fft.ifft2(spec)).astype(np.float32)
    out -= out.min()
    out /= out.max() + 1e-9
    # spread to a fuller 0..1 range (spectral noise is Gaussian-ish)
    m, sd = float(out.mean()), float(out.std()) + 1e-9
    out = np.clip(0.5 + (out - m) / (sd * 5.0), 0, 1)
    return out


def tex_sample(tex: np.ndarray, ox: float, oy: float, scale: float, w: int = W, h: int = H) -> np.ndarray:
    """Sample a tileable texture as a w x h field, offset (ox,oy) in texture px,
    `scale` = texture px per screen px."""
    th, tw = tex.shape[:2]
    m = np.array([[scale, 0, ox], [0, scale, oy]], np.float32)
    return cv2.warpAffine(
        tex, m, (w, h), flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP, borderMode=cv2.BORDER_WRAP
    )


# --------------------------------------------------------------------------
# fields and shapes
# --------------------------------------------------------------------------


def vgradient(stops) -> np.ndarray:
    """Vertical gradient. stops: [(y0..1, color), ...]. Returns H x W x 3."""
    ys = np.linspace(0, 1, H, dtype=np.float32)
    pos = np.array([s[0] for s in stops], np.float32)
    cols = np.stack([np.asarray(s[1], np.float32) for s in stops])
    col = np.stack([np.interp(ys, pos, cols[:, c]) for c in range(3)], 1).astype(np.float32)
    # smooth the interpolation a touch so stops don't band
    return np.ascontiguousarray(np.broadcast_to(col[:, None, :], (H, W, 3)))


def ridge(img, heights, color, soft: float = 1.2, fade=None):
    """Fill everything below the per-column curve `heights` (pixel rows, len W).
    `fade`: optional (H,) or (H,W) multiplier of the fill alpha."""
    top = int(max(0, math.floor(float(np.min(heights)) - 2)))
    if top >= H:
        return img
    ys = YS[top:]
    a = np.clip((ys - heights[None, :]) / soft + 0.5, 0, 1)
    if fade is not None:
        a = a * (fade[top:] if np.ndim(fade) else fade)
    sub = img[top:]
    if np.ndim(color) == 3:
        col = color[top:]
        sub += (col - sub) * a[..., None]
    else:
        sub += (np.asarray(color, np.float32) - sub) * a[..., None]
    return img


def ridge_alpha(heights, soft: float = 1.2) -> np.ndarray:
    return np.clip((YS - heights[None, :]) / soft + 0.5, 0, 1).astype(np.float32)


def glow(img, cx, cy, r, color, strength=1.0, kind="gauss", reach=4.0):
    """Additive light. kind gauss: tight; 'halo': long 1/(1+d^2) tail."""
    R = r * reach
    x0, x1 = int(max(0, cx - R)), int(min(W, cx + R + 1))
    y0, y1 = int(max(0, cy - R)), int(min(H, cy + R + 1))
    if x0 >= x1 or y0 >= y1:
        return img
    dx = XS[:, x0:x1] - cx
    dy = YS[y0:y1] - cy
    d2 = (dx * dx + dy * dy) / (r * r)
    if kind == "gauss":
        f = np.exp(-d2)
    else:
        f = 1.0 / (1.0 + d2) - 1.0 / (1.0 + reach * reach)
        f = np.maximum(f, 0)
    img[y0:y1, x0:x1] += f[..., None] * (np.asarray(color, np.float32) * strength)
    return img


def disk(img, cx, cy, r, color, alpha=1.0, soft=1.0):
    x0, x1 = int(max(0, cx - r - 2)), int(min(W, cx + r + 3))
    y0, y1 = int(max(0, cy - r - 2)), int(min(H, cy + r + 3))
    if x0 >= x1 or y0 >= y1:
        return img
    d = np.sqrt((XS[:, x0:x1] - cx) ** 2 + (YS[y0:y1] - cy) ** 2)
    a = np.clip((r - d) / soft + 0.5, 0, 1) * alpha
    sub = img[y0:y1, x0:x1]
    sub += (np.asarray(color, np.float32) - sub) * a[..., None]
    return img


def over(img, rgba, x=0, y=0, alpha=1.0, tint=None):
    """Composite a premultiplied float RGBA layer (h,w,4) at integer (x,y)."""
    h, w = rgba.shape[:2]
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(W, x + w), min(H, y + h)
    if x0 >= x1 or y0 >= y1:
        return img
    src = rgba[y0 - y : y1 - y, x0 - x : x1 - x]
    a = src[..., 3:4] * alpha
    c = src[..., :3] * alpha
    if tint is not None:
        c = c * np.asarray(tint, np.float32)
    dst = img[y0:y1, x0:x1]
    dst *= 1 - a
    dst += c
    return img


def warp_layer(rgba, m, size=(W, H)):
    """Affine-warp a premultiplied RGBA layer onto a full frame (W,H)."""
    return cv2.warpAffine(rgba, m, size, flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0)


def over_full(img, rgba_full, alpha=1.0, tint=None):
    a = rgba_full[..., 3:4] * alpha
    c = rgba_full[..., :3] * alpha
    if tint is not None:
        c = c * np.asarray(tint, np.float32)
    img *= 1 - a
    img += c
    return img


# --------------------------------------------------------------------------
# cairo
# --------------------------------------------------------------------------


def cairo_canvas(w=W, h=H):
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
    ctx = cairo.Context(surf)
    ctx.set_antialias(cairo.ANTIALIAS_BEST)
    return surf, ctx


def cairo_rgba(surf) -> np.ndarray:
    """Cairo ARGB32 (premultiplied BGRA bytes) -> premultiplied float RGBA."""
    surf.flush()
    w, h = surf.get_width(), surf.get_height()
    stride = surf.get_stride()
    buf = np.ndarray((h, stride // 4, 4), np.uint8, surf.get_data())[:, :w]
    out = np.empty((h, w, 4), np.float32)
    out[..., 0] = buf[..., 2]
    out[..., 1] = buf[..., 1]
    out[..., 2] = buf[..., 0]
    out[..., 3] = buf[..., 3]
    out *= 1.0 / 255.0
    return out


def rgb(ctx, c, a=1.0):
    ctx.set_source_rgba(float(c[0]), float(c[1]), float(c[2]), a)


# --------------------------------------------------------------------------
# finishing
# --------------------------------------------------------------------------


@functools.lru_cache(maxsize=1)
def _vignette():
    x = (XS - W / 2) / (W / 2)
    y = (YS - H / 2) / (H / 2)
    d = np.sqrt(x * x * 0.8 + y * y)
    return (1 - 0.38 * smooth(0.55, 1.45, d)).astype(np.float32)[..., None]


@functools.lru_cache(maxsize=1)
def _grain():
    r = np.random.default_rng(7)
    g = r.normal(0, 1, (8, H // 2, W // 2)).astype(np.float32)
    return [cv2.resize(x, (W, H), interpolation=cv2.INTER_LINEAR) for x in g]


def finish(img, frame: int, grain=0.018, vignette=1.0, bloom=0.35, exposure=1.0, warmth=0.0) -> np.ndarray:
    """Bloom, vignette, grain, soft tone curve. Returns uint8 RGB."""
    img = img * exposure
    if bloom > 0:
        small = cv2.resize(img, (W // 4, H // 4), interpolation=cv2.INTER_AREA)
        br = np.maximum(small - 0.62, 0)
        b1 = cv2.GaussianBlur(br, (0, 0), 6)
        b2 = cv2.GaussianBlur(br, (0, 0), 22)
        b = cv2.resize(b1 * 0.6 + b2 * 0.9, (W, H), interpolation=cv2.INTER_LINEAR)
        img = img + b * bloom
    if warmth:
        img = img * np.array([1 + warmth, 1 + warmth * 0.3, 1 - warmth * 0.6], np.float32)
    if vignette:
        v = _vignette()
        img = img * (1 - vignette + vignette * v)
    # soft shoulder: keep highlights from clipping hard
    img = np.where(img > 0.8, 0.8 + 0.2 * np.tanh((img - 0.8) / 0.2), img)
    if grain:
        g = _grain()[frame % 8]
        img = img + g[..., None] * grain * (0.35 + 0.65 * np.clip(1 - img, 0, 1))
    return (np.clip(img, 0, 1) * 255 + 0.5).astype(np.uint8)


def fade_black(img, a):
    if a < 1:
        img *= max(a, 0.0)
    return img

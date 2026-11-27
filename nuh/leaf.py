"""Macro: one leaf hanging from a branch at night in the rain, the moon behind.

Scale: the leaf is ~85 mm long and fills most of the frame, so 1 mm ~ 16 px.
That fixes the lens physics:

* Focus is a few centimetres away. The moon (0.5 degrees, ~50 px here) at
  infinity is blurred into a defocus disc far larger than itself: a uniform
  disc whose soft edge is the width of the moon, with the slightly bright rim
  and faint onion rings of a real lens, and halation around it.
* Point highlights behind the leaf (moonlight catching wet leaves) become
  bokeh shaped like the aperture (9 rounded blades), sized by their depth,
  cut to a cat's eye toward the frame corners.
* Depth of field is millimetres. The leaf turns away from the camera along
  its far half and toward its tip, which go soft.
* Shutter 1/48 s. A raindrop at ~7 m/s crosses the frame within one
  exposure: incoming drops are faint streaks, out-of-focus drops in front of
  or behind the leaf are soft capsules. A drop leaving the tip is gone in
  two or three frames.
* A drop sitting on the leaf is a magnifier: it shows the leaf under it
  enlarged, a highlight aimed at the moon, a tiny inverted image of the
  bright sky near its far edge, and it throws a caustic onto the leaf.

The leaf is a damped spring: impacts jolt it down, a drop letting go of the
tip lets it spring back.

`frame(T)` is a pure function of film time. numpy + OpenCV + pycairo only.
"""

from __future__ import annotations

import functools
import json
import math
from pathlib import Path

import cv2
import numpy as np

from .paint import H, W, XS, YS, cairo_canvas, cairo_rgba, clamp01, fbm_tex, smooth

CUTS = json.loads((Path(__file__).parent / "data" / "cuts.json").read_text())
FPS = 24
SHUTTER = 1 / 48
PX_PER_MM = 16.0
G_PX = 9810 * PX_PER_MM


def lin(h: str) -> np.ndarray:
    h = h.lstrip("#")
    c = np.array([int(h[i : i + 2], 16) / 255.0 for i in (0, 2, 4)], np.float32)
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4).astype(np.float32)


def to_srgb(x):
    x = np.clip(x, 0, 1)
    return np.where(x <= 0.0031308, x * 12.92, 1.055 * np.power(x, 1 / 2.4) - 0.055)


def aces(x):
    a, b, c, d, e = 2.51, 0.03, 2.43, 0.59, 0.14
    return np.clip((x * (a * x + b)) / (x * (c * x + d) + e), 0, 1)


MOON = lin("#d2e2ff")
SKY = lin("#050a1c")

# --------------------------------------------------------------------------
# geometry: the leaf hangs on a diagonal, tip down to the right
# --------------------------------------------------------------------------

BASE = np.array([352.0, 318.0])
TIP = np.array([1600.0, 842.0])
LEN = float(np.linalg.norm(TIP - BASE))
DIR = (TIP - BASE) / LEN
NRM = np.array([-DIR[1], DIR[0]])  # +v: the lower-left half (nearer the camera)
HALF_W = 292.0
BOW = -34.0
NEAR, FAR = 1.12, 0.80  # the leaf is turned: its lower half faces us, its upper half recedes

MOON_C = (1452.0, 262.0)
MOON_R = 215.0


def half_width(u, side):
    u = np.clip(u, 0, 1)
    body = np.clip(np.sin(np.pi * u**0.8), 0, None) ** 0.85
    acum = 1 - smooth(0.84, 1.0, u) * 0.6
    base = 1 - 0.25 * smooth(0.1, 0.0, u)
    k = np.where(side > 0, NEAR, FAR)
    return HALF_W * body * acum * base * k


def leaf_uv(x, y):
    px, py = x - BASE[0], y - BASE[1]
    u = (px * DIR[0] + py * DIR[1]) / LEN
    d = px * NRM[0] + py * NRM[1] - BOW * np.sin(np.pi * np.clip(u, 0, 1))
    return u, d


def leaf_point(u, v):
    side = 1.0 if v >= 0 else -1.0
    d = BOW * math.sin(math.pi * min(max(u, 0), 1)) + v * float(half_width(np.float32(u), side))
    return BASE + DIR * u * LEN + NRM * d


# --------------------------------------------------------------------------
# cues from the recitation
# --------------------------------------------------------------------------


def rain_rate(T: float) -> float:
    s = {x["ayah"]: (x["start"], x["end"]) for x in CUTS["shots"]}

    def span(a, b, ramp=6.0):
        t0, t1 = s[a][0], s[b][1]
        return float(smooth(t0 - ramp, t0 + ramp, T) * (1 - smooth(t1 - ramp, t1 + ramp, T)))

    return 0.9 + 0.5 * span(11, 12) + 0.6 * span(25, 27) - 0.5 * span(28, 28, 10)


def fade(T: float) -> float:
    audio = CUTS["audio_seconds"]
    last = CUTS["shots"][-1]["start"] + CUTS["events"]["28"]["end"]
    return float(smooth(0.0, 5.0, T) * (1 - smooth(last + 0.3, audio, T)))


# --------------------------------------------------------------------------
# events
# --------------------------------------------------------------------------


@functools.lru_cache(maxsize=1)
def events():
    r = np.random.default_rng(4001)
    audio = CUTS["audio_seconds"]
    impacts = []
    t = 1.2
    while t < audio:
        rate = 0.75 * rain_rate(t)
        t += r.exponential(1.0 / rate) + 0.15
        impacts.append(dict(t=t, u=r.uniform(0.2, 0.9), v=r.uniform(-0.7, 0.75), size=r.uniform(0.7, 1.3), seed=int(r.integers(1 << 30))))
    arrivals = []
    for im in impacts:
        if r.random() < 0.55:
            speed = r.uniform(0.25, 0.6)
            delay = r.uniform(0.2, 0.9)
            dur = (1.0 - im["u"]) / speed
            im["slide"] = (im["t"] + delay, dur)
            arrivals.append((im["t"] + delay + dur, 0.35 * im["size"]))
        else:
            im["slide"] = None
    arrivals.sort()
    releases = []
    vol, last = 0.2, 0.0
    for ta, dv in arrivals:
        vol += 0.04 * (ta - last)
        last = ta
        vol += dv
        if vol > 1.0:
            releases.append(ta + r.uniform(0.05, 0.4))
            vol = r.uniform(0.15, 0.3)
    return impacts, arrivals, releases


def tip_volume(T: float) -> float:
    impacts, arrivals, releases = events()
    vol, last = 0.2, 0.0
    ev = sorted([(t, 0, dv) for t, dv in arrivals] + [(t, 1, 0.0) for t in releases])
    for t, kind, dv in ev:
        if t > T:
            break
        vol += 0.04 * (t - last)
        last = t
        vol = vol + dv if kind == 0 else 0.22
    return min(vol + 0.04 * (T - last), 1.05)


OMEGA = 2 * math.pi * 4.2
ZETA = 0.11


def leaf_deflection(T: float) -> float:
    impacts, arrivals, releases = events()
    wd = OMEGA * math.sqrt(1 - ZETA**2)
    y = 0.0
    for im in impacts:
        dt = T - im["t"]
        if 0 <= dt < 4.0:
            y += 70.0 * im["size"] * (0.3 + 0.7 * im["u"]) * math.exp(-ZETA * OMEGA * dt) * math.sin(wd * dt)
    for tr in releases:
        dt = T - tr
        if 0 <= dt < 4.0:
            y -= 26.0 * math.exp(-ZETA * OMEGA * dt) * math.sin(wd * dt)  # the weight lets go
    y += 14.0 * tip_volume(T)
    y += 6.0 * math.sin(T * 0.9) * math.sin(T * 0.37 + 1.0)
    return y


def bend(u):
    return np.clip(u, 0, 1.2) ** 1.7


# --------------------------------------------------------------------------
# the leaf (drawn at 2x)
# --------------------------------------------------------------------------

SS = 2


@functools.lru_cache(maxsize=1)
def leaf_layers():
    """Linear-light colour, alpha, depth from the focal plane (mm) of leaf and
    branch, at output size."""
    w, h = W * SS, H * SS
    ys = (np.arange(h, dtype=np.float32) / SS)[:, None]
    xs = (np.arange(w, dtype=np.float32) / SS)[None, :]
    u, d = leaf_uv(xs, ys)
    side = np.where(d >= 0, 1.0, -1.0).astype(np.float32)
    hw = half_width(u, side)
    v = d / np.maximum(hw, 1e-3)
    av = np.abs(v)
    au = np.clip(u, 0, 1)
    inside = (u > 0) & (u < 1.0)
    edge_n = cv2.resize(fbm_tex(2048, 64, 4101, base=40, octaves=3), (w, h))
    nick = cv2.resize(fbm_tex(4096, 32, 4108, base=90, octaves=2), (w, h))
    marg = np.abs(d) - hw * (1 + 0.016 * (edge_n - 0.5) - 0.018 * smooth(0.78, 0.95, nick))
    a_leaf = (clamp01(-marg * SS + 0.5) * inside).astype(np.float32)

    # ---- venation
    mid_w = 7.0 - 5.0 * au
    mid = np.exp(-(d / mid_w) ** 2)
    mid_edge = np.exp(-(((np.abs(d) - mid_w) / 1.6) ** 2)) * (1 - au)
    r = np.random.default_rng(4105)
    sec = np.zeros_like(mid)
    for sgn in (-1, 1):
        uk = 0.05 + (0.04 if sgn < 0 else 0.0)
        while uk < 0.9:
            curve = 0.17 + r.uniform(-0.02, 0.03)
            path = uk + curve * av**1.15 + 0.10 * smooth(0.62, 0.92, av)  # sweep to the tip, loop near the margin
            dist = (u - path) * LEN
            width = (2.6 - 1.9 * av) * (1 - 0.45 * uk)
            reach = smooth(0.95, 0.80, av) * (1 - smooth(0.0, 0.02, -av * 0))
            m = (side == sgn) & (av > 0.01)
            sec += np.exp(-(dist / np.maximum(width, 0.6)) ** 2) * reach * m
            # a weaker intersecondary between this one and the next
            p2 = path + 0.042
            sec += 0.45 * np.exp(-(((u - p2) * LEN) / np.maximum(width * 0.6, 0.5)) ** 2) * smooth(0.75, 0.45, av) * smooth(0.05, 0.25, av) * m
            uk += r.uniform(0.072, 0.095)
    sec = np.clip(sec, 0, 1)
    # areoles: the fine network, and the cells it encloses
    seeds = np.full((h // 2, w // 2), 255, np.uint8)
    n = 6200
    seeds[(r.random(n) * (h // 2)).astype(int), (r.random(n) * (w // 2)).astype(int)] = 0
    dist_c, lab = cv2.distanceTransformWithLabels(seeds, cv2.DIST_L2, 5, labelType=cv2.DIST_LABEL_CCOMP)
    e = np.zeros(lab.shape, np.float32)
    e[:, 1:] += lab[:, 1:] != lab[:, :-1]
    e[1:, :] += lab[1:, :] != lab[:-1, :]
    tert = cv2.GaussianBlur(cv2.resize((e > 0).astype(np.float32), (w, h)), (0, 0), 0.9)
    tert = np.clip(tert, 0, 1)
    cell_c = cv2.resize(cv2.distanceTransform(255 - (e > 0).astype(np.uint8) * 255, cv2.DIST_L2, 3), (w, h)) * 1.0
    cell = smooth(0.0, 6.0, cell_c)  # bright in the middle of each areole
    micro = cv2.resize(fbm_tex(2048, 1024, 4103, base=180, octaves=3), (w, h))
    blot = cv2.resize(fbm_tex(512, 256, 4106, base=5, octaves=5), (w, h))

    # ---- shape for shading: cupped blade, raised midrib, sunken veins
    height = (-(v**2) * 60.0 + 70.0 * smooth(0.78, 1.0, av) ** 2 + 5.0 * mid - 1.6 * sec - 0.5 * tert + 0.5 * micro).astype(np.float32)
    gx = cv2.Sobel(height, cv2.CV_32F, 1, 0, ksize=3) / (8.0 / SS)
    gy = cv2.Sobel(height, cv2.CV_32F, 0, 1, ksize=3) / (8.0 / SS)
    nl = np.sqrt(gx * gx + gy * gy + 1)
    nx, ny, nz = -gx / nl, -gy / nl, 1 / nl

    # ---- light. The moon is behind, up and to the right: the leaf is backlit.
    toward = np.exp(-(((xs - MOON_C[0]) / 1100.0) ** 2 + ((ys - MOON_C[1]) / 900.0) ** 2))
    through = (0.06 + 1.9 * toward**1.4) * (0.7 + 0.3 * smooth(0.25, 0.75, blot))
    thin = 1 + 0.6 * smooth(0.55, 1.0, av)  # thinner toward the margin
    lamina = lin("#5c9a1c") * (0.78 + 0.22 * cell)[..., None] * (0.82 + 0.3 * micro)[..., None]
    vein_c = lin("#b4d872")
    veins = np.clip(0.85 * mid + 1.0 * sec, 0, 1)
    trans = lamina * (1 - veins[..., None]) + vein_c * veins[..., None] * 0.8
    trans *= (1 - 0.16 * tert)[..., None]
    trans *= (through * thin * 0.055)[..., None]
    # the front face only sees dim sky: dark, with a broad wet sheen
    alb = lin("#16300e")
    sky_dir = np.array([0.35, -0.7, 0.62])
    sky_dir /= np.linalg.norm(sky_dir)
    ndl = np.clip(nx * sky_dir[0] + ny * sky_dir[1] + nz * sky_dir[2], 0, 1)
    front = alb * (0.004 + 0.006 * ndl)[..., None]
    hv = np.array([0.42, -0.5, 0.76])
    hv /= np.linalg.norm(hv)
    ndh = np.clip(nx * hv[0] + ny * hv[1] + nz * hv[2], 0, 1)
    wet = 0.6 + 0.4 * smooth(0.3, 0.8, blot)
    streak_n = cv2.resize(fbm_tex(1024, 256, 4109, base=3, octaves=4), (w, h))
    sheen = (ndh**70 * 0.10 + ndh**10 * 0.035) * (0.3 + 0.7 * toward) * wet * (0.5 + 0.8 * streak_n)
    leaf = trans + front + sheen[..., None] * MOON
    # midrib: pale and raised, lit along its upper flank
    leaf += (mid_edge * (d < 0) * 0.02 * (0.3 + toward))[..., None] * MOON
    # the rim: light leaking round the thin margin, strongest toward the moon
    rim = np.exp(-((marg + 1.2) / 1.7) ** 2) * inside
    rim_var = smooth(0.35, 0.8, cv2.resize(fbm_tex(2048, 64, 4110, base=24, octaves=3), (w, h)))
    leaf += (rim * 0.16 * toward**1.6 * (side < 0) * (0.3 + 0.9 * rim_var))[..., None] * lin("#e4f2a8")
    thick_line = np.exp(-((marg + 3.4) / 1.0) ** 2) * inside
    leaf *= (1 - 0.35 * thick_line)[..., None]

    # ---- petiole and branch
    surf, ctx = cairo_canvas(w, h)
    J = np.array([228.0, 186.0])  # where the petiole meets the branch
    ctx.set_line_cap(1)
    ctx.move_to(J[0] * SS, J[1] * SS)
    ctx.curve_to((J[0] + 50) * SS, (J[1] + 40) * SS, (BASE[0] - 70) * SS, (BASE[1] - 45) * SS, (BASE[0] + 6) * SS, (BASE[1] + 2) * SS)
    ctx.set_line_width(24 * SS)
    ctx.set_source_rgba(1, 1, 1, 1)
    ctx.stroke()
    pet_a = cairo_rgba(surf)[..., 3]
    surf, ctx = cairo_canvas(w, h)
    ctx.move_to(-120 * SS, 520 * SS)
    ctx.curve_to(60 * SS, 330 * SS, 180 * SS, 220 * SS, 520 * SS, -120 * SS)
    ctx.set_line_width(48 * SS)
    ctx.set_source_rgba(1, 1, 1, 1)
    ctx.stroke()
    br_a = cairo_rgba(surf)[..., 3]
    bark = cv2.resize(fbm_tex(256, 2048, 4107, base=6, octaves=6), (w, h))
    stem_col = lin("#3a4020") * (0.016 + 0.014 * bark)[..., None]
    stem_col += (np.exp(-((xs - 220) / 160.0) ** 2) * 0.004 * toward)[..., None] * MOON
    a_stem = np.clip(pet_a + br_a, 0, 1) * (1 - a_leaf)
    # a thin lit edge on the stems' upper-right side
    edge = np.clip(a_stem - cv2.GaussianBlur(a_stem, (0, 0), 3 * SS), 0, 1)
    stem_col += (edge * 0.018)[..., None] * MOON

    lum = leaf.mean(2, keepdims=True)
    leaf = (leaf * 0.62 + lum * 0.38) * np.array([0.82, 0.92, 1.12], np.float32) * 0.8
    col = leaf * a_leaf[..., None] + stem_col * a_stem[..., None]
    alpha = np.clip(a_leaf + a_stem, 0, 1)
    # depth from the focal plane (mm): focus on the midrib, centre of the blade
    depth = (u - 0.42) * 11.0 + np.where(side < 0, -v * 3.5, -v * 1.2)
    depth = np.where(br_a > 0.5, 5.0, np.where(pet_a > 0.5, 1.8, depth))

    def down(a):
        return cv2.resize(np.ascontiguousarray(a, dtype=np.float32), (W, H), interpolation=cv2.INTER_AREA)

    return down(col), down(alpha), down(depth)


# --------------------------------------------------------------------------
# the far field
# --------------------------------------------------------------------------

APERTURE_BLADES = 9


def bokeh(img, cx, cy, R, color, ss=1.0):
    """One defocused point: the aperture's shape (9 rounded blades), a bright
    rim and onion rings (spherical aberration), cut to a cat's eye off axis,
    with a faint green/magenta fringe (longitudinal CA)."""
    x0, x1 = int(max(0, cx - R - 4)), int(min(W, cx + R + 5))
    y0, y1 = int(max(0, cy - R - 4)), int(min(H, cy + R + 5))
    if x0 >= x1 or y0 >= y1:
        return
    dx = XS[:, x0:x1] - cx
    dy = YS[y0:y1] - cy
    d = np.sqrt(dx * dx + dy * dy)
    th = np.arctan2(dy, dx) + 0.3
    Rt = R * (1 - 0.018 * (np.cos(APERTURE_BLADES * th) * 0.5 + 0.5))
    disc = clamp01((Rt - d) / 2.2 + 0.5)
    ox, oy = (cx - W / 2) / (W / 2), (cy - H / 2) / (H / 2)
    off = math.hypot(ox, oy)
    if off > 0.3:
        sh = (off - 0.3) * R * 1.0
        d2 = np.sqrt((dx + ox / off * sh) ** 2 + (dy + oy / off * sh) ** 2)
        disc = disc * clamp01((R - d2) / 1.4 + 0.5)
    q = d / R
    rim = 0.85 + 0.25 * smooth(0.6, 0.98, q)
    rings = 1 + 0.02 * np.sin(q * 22.0 + ss)
    col = np.asarray(color, np.float32)
    outer = clamp01((Rt + 1.5 - d) / 1.4 + 0.5) - clamp01((Rt - d) / 1.4 + 0.5)
    sub = img[y0:y1, x0:x1]
    sub += (disc * rim * rings)[..., None] * col


@functools.lru_cache(maxsize=1)
def far_field():
    """Static: sky, moon disc with halation, the tree behind in three depths.
    Returns (image, list of glint points with depth and brightness)."""
    img = np.zeros((H, W, 3), np.float32)
    mx, my = MOON_C
    d = np.sqrt((XS - mx) ** 2 + (YS - my) ** 2)
    yn = (YS / H)[..., None]
    img[:] = SKY * (0.5 + 0.7 * (1 - yn))
    # the moon's light scattering in the rain-filled air
    img += (np.exp(-d / 180.0) * 0.06 + np.exp(-d / 520.0) * 0.03 + np.exp(-d / 1400.0) * 0.012)[..., None] * MOON
    # the moon, defocused: uniform disc, edge as soft as the moon is wide,
    # bright rim, faint onion rings
    q = d / MOON_R
    disc = smooth(1.0 + 0.12, 1.0 - 0.12, q)
    tex = 1 + 0.025 * np.sin(q * 34.0) * smooth(0.2, 0.9, q) + 0.12 * smooth(0.78, 0.98, q) * smooth(1.08, 0.98, q)
    img += (disc * tex * 0.24)[..., None] * MOON

    r = np.random.default_rng(4201)
    glints = []
    for blur, nleaf, depth in ((16.0, 18, 0.35), (34.0, 14, 0.6), (64.0, 9, 1.0)):
        surf, ctx = cairo_canvas()
        shapes = []
        for _ in range(nleaf):
            cx, cy = r.uniform(-250, W + 250), r.uniform(-200, H + 200)
            ln = r.uniform(260, 680) * (1.5 if depth > 0.9 else 1.0)
            ang = r.uniform(0, 2 * math.pi)
            shapes.append((cx, cy, ln, ang))
            ctx.save()
            ctx.translate(cx, cy)
            ctx.rotate(ang)
            ctx.move_to(-ln / 2, 0)
            ctx.curve_to(-ln / 4, -ln * 0.23, ln / 4, -ln * 0.21, ln / 2, 0)
            ctx.curve_to(ln / 4, ln * 0.21, -ln / 4, ln * 0.23, -ln / 2, 0)
            ctx.close_path()
            ctx.set_source_rgba(0, 0, 0, 1)
            ctx.fill()
            ctx.restore()
        a = cv2.GaussianBlur(cairo_rgba(surf)[..., 3], (0, 0), blur)
        near_moon = np.exp(-d / 650.0)
        lit = lin("#1c2e12") * (0.004 + 0.035 * near_moon)[..., None]
        img = img * (1 - a[..., None] * 0.97) + lit * a[..., None]
        # glints: moonlight catching the wet upper-right edges of those leaves
        for (cx, cy, ln, ang) in shapes:
            for _ in range(int(r.integers(1, 4))):
                t = r.uniform(-0.4, 0.4)
                px = cx + math.cos(ang) * ln * t - math.sin(ang) * ln * 0.2 * (1 - (2 * t) ** 2) * 0.9
                py = cy + math.sin(ang) * ln * t + math.cos(ang) * ln * 0.2 * (1 - (2 * t) ** 2) * -0.9
                if not (-40 < px < W + 40 and -40 < py < H + 40):
                    continue
                nm = math.exp(-math.hypot(px - mx, py - my) / 700.0)
                b = (0.0004 + 0.07 * nm**1.6) * r.uniform(0.2, 1.0) ** 2
                glints.append((px, py, 14 + 70 * depth * r.uniform(0.8, 1.2), b, r.uniform(0, 6.28), r.uniform(0.3, 1.0)))
    return img, glints


@functools.lru_cache(maxsize=1)
def glint_sprites():
    """Each glint's bokeh, rendered once (unit brightness)."""
    _, glints = far_field()
    out = []
    for (px, py, R, b, ph, fr) in glints:
        pad = int(R) + 6
        tmp = np.zeros((H, W, 3), np.float32)
        bokeh(tmp, px, py, R, MOON, ss=ph)
        x0, y0 = int(px) - pad, int(py) - pad
        xa, ya = max(0, x0), max(0, y0)
        xb, yb = min(W, int(px) + pad), min(H, int(py) + pad)
        if xa >= xb or ya >= yb:
            continue
        spr = np.zeros((2 * pad, 2 * pad, 3), np.float32)
        spr[ya - y0 : yb - y0, xa - x0 : xb - x0] = tmp[ya:yb, xa:xb]
        out.append((x0, y0, spr, b, ph, fr))
    return out


def capsule(img, x, y0, y1, R, b, tint=None):
    """A defocused raindrop smeared by the shutter: a disc swept vertically."""
    xa, xb = int(max(0, x - R - 2)), int(min(W, x + R + 3))
    ya, yb = int(max(0, min(y0, y1) - R - 2)), int(min(H, max(y0, y1) + R + 3))
    if xa >= xb or ya >= yb:
        return
    yy = np.clip(YS[ya:yb], min(y0, y1), max(y0, y1))
    dd = np.sqrt((XS[:, xa:xb] - x) ** 2 + (YS[ya:yb] - yy) ** 2)
    m = clamp01((R - dd) / 1.5 + 0.5) * (1 + 0.3 * smooth(0.6 * R, R, dd))
    img[ya:yb, xa:xb] += m[..., None] * (MOON if tint is None else tint) * b


# --------------------------------------------------------------------------
# drops on the leaf
# --------------------------------------------------------------------------


@functools.lru_cache(maxsize=1)
def resting_beads():
    r = np.random.default_rng(4401)
    out = []
    for _ in range(44):
        u = r.uniform(0.14, 0.92)
        v = r.uniform(-0.8, 0.85)
        rad = (r.random() ** 2.6) * 34 + 3.5
        out.append((u, v, rad, r.uniform(0, 6.28), r.uniform(0.85, 1.15)))
    return out


def draw_drop(img, leafcol, behind, cx, cy, rad, wob=0.0, shape=1.0, light=1.0, backlit=0.0, seed=0):
    k = 2
    flat = smooth(10, 34, rad)  # big drops sag into flat lenses
    rx = rad * (1 + 0.06 * wob) * shape
    ry = rad * (0.9 - 0.06 * wob) / shape
    ang = math.atan2(DIR[1], DIR[0])
    pad = int(rad * 1.8) + 4
    x0, x1 = int(cx) - pad, int(cx) + pad
    y0, y1 = int(cy) - pad, int(cy) + pad
    if x0 < 0 or y0 < 0 or x1 > W or y1 > H:
        return
    gy, gx = np.mgrid[0 : (y1 - y0) * k, 0 : (x1 - x0) * k].astype(np.float32)
    px = x0 + (gx + 0.5) / k - cx
    py = y0 + (gy + 0.5) / k - cy
    ca, sa = math.cos(ang), math.sin(ang)
    qx = (px * ca + py * sa) / (rx * (1 + 0.08 * flat))
    qy = (-px * sa + py * ca) / ry
    e = np.sqrt(qx * qx + qy * qy)
    inside = clamp01((1 - e) * rad * k / 1.2 + 0.5)
    z = np.sqrt(np.clip(1 - e * e, 0, 1))
    # the leaf beneath, magnified through the dome
    mag = 0.42 + 0.25 * flat
    under = cv2.remap(leafcol, (cx + px * mag).astype(np.float32), (cy + py * mag).astype(np.float32), cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
    # the scene beyond, inverted and minified (the drop as a fisheye)
    mini = 7.0 - 3.0 * flat
    world = cv2.remap(behind, (cx - px * mini).astype(np.float32), (cy - py * mini).astype(np.float32), cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
    wmix = (0.65 * z * (1 - 0.5 * flat))[..., None]
    body = under * (1.15 + 0.5 * z[..., None]) * (1 - wmix) + world * 1.3 * wmix
    # rounded edge: Fresnel darkening into a dark meniscus at the contact line
    edge_k = rs_edge = 0.45 + 0.3 * ((seed * 0.6180339) % 1.0)
    body *= (1 - edge_k * smooth(0.72, 1.0, e))[..., None]
    meniscus = np.exp(-((e - 1.0) / 0.06) ** 2)
    # highlight aimed at the moon, its size and place set by the drop's curvature
    vx, vy = MOON_C[0] - cx, MOON_C[1] - cy
    vl = math.hypot(vx, vy) + 1e-3
    vx, vy = vx / vl, vy / vl
    rs = np.random.default_rng(seed)
    hs = (0.07 + 0.13 * flat) * rs.uniform(0.8, 1.25)
    off = (0.62 - 0.2 * flat) * rs.uniform(0.85, 1.1)
    hx, hy = px / rad - vx * off, py / rad - vy * off
    glint = np.exp(-((hx / hs) ** 2 + (hy / (hs * rs.uniform(0.55, 0.9))) ** 2)) * (1.5 - 0.7 * flat)
    hx2, hy2 = px / rad - vx * off * 0.35 + vy * 0.2, py / rad - vy * off * 0.35 - vx * 0.2
    glint2 = np.exp(-((hx2 / (hs * 0.6)) ** 2 + (hy2 / (hs * 0.45)) ** 2)) * 0.35
    ix, iy = px / rad + vx * 0.5, py / rad + vy * 0.5
    inv_moon = np.exp(-((ix * ix + iy * iy) / (0.018 * (1 - 0.5 * flat)))) * 0.5
    body += ((glint + glint2 + inv_moon) * light)[..., None] * MOON * 0.5
    if backlit:
        body += (z**0.5 * backlit * 0.025)[..., None] * lin("#cfe4ff")
        ix, iy = px / rad + vx * 0.35, py / rad + vy * 0.35
        body += (np.exp(-((ix * ix + iy * iy) / 0.02)) * backlit * 0.6)[..., None] * MOON
    patch = cv2.resize(body * inside[..., None], (x1 - x0, y1 - y0), interpolation=cv2.INTER_AREA)
    a = cv2.resize(inside, (x1 - x0, y1 - y0), interpolation=cv2.INTER_AREA)[..., None]
    men = cv2.resize(meniscus, (x1 - x0, y1 - y0), interpolation=cv2.INTER_AREA)[..., None]
    sub = img[y0:y1, x0:x1]
    # the caustic: light through the drop focused on the leaf, away from the moon
    cxs, cys = cx - vx * rad * 0.75, cy - vy * rad * 0.75
    caus = np.exp(-(((XS[:, x0:x1] - cxs) / (rad * 0.36)) ** 2 + ((YS[y0:y1] - cys) / (rad * 0.28)) ** 2))
    sub += (caus * 0.12 * light * (0.5 + 0.5 * (1 - flat)))[..., None] * lin("#e8f8b0")
    sub *= 1 - a
    sub += patch
    sub *= 1 - 0.3 * men


# --------------------------------------------------------------------------
# the frame
# --------------------------------------------------------------------------


def frame_linear(T: float, f: int) -> np.ndarray:
    base, glints = far_field()
    sx = 4.0 * math.sin(T * 0.31) + 1.5 * math.sin(T * 0.83)
    sy = 2.5 * math.sin(T * 0.23 + 1)
    img = cv2.warpAffine(base, np.float32([[1, 0, sx], [0, 1, sy]]), (W, H), borderMode=cv2.BORDER_REFLECT)
    rate = rain_rate(T)
    rr = np.random.default_rng(f * 31 + 9)
    # glints flicker as rain strikes the leaves behind
    for (x0, y0, spr, b, ph, fr) in glint_sprites():
        hit = math.exp(-((T * fr * 1.7 + ph) % 3.1) * 6.0) * 1.8
        life = smooth(0.15, 0.4, 0.5 + 0.5 * math.sin(T * fr * 0.21 + ph * 3))
        k = b * (0.35 + 0.45 * math.sin(T * fr + ph) ** 2 + hit) * life
        if k <= 1e-5:
            continue
        xi = int(round(x0 + sx + 14 * math.sin(T * 0.4 * fr + ph)))
        yi = int(round(y0 + sy + 9 * math.sin(T * 0.3 * fr + ph * 2)))
        h_, w_ = spr.shape[:2]
        xa, ya = max(0, xi), max(0, yi)
        xb, yb = min(W, xi + w_), min(H, yi + h_)
        if xa < xb and ya < yb:
            img[ya:yb, xa:xb] += spr[ya - yi : yb - yi, xa - xi : xb - xi] * k
    # rain between us and the far field: out of focus, smeared by the shutter
    lightL = cv2.resize(cv2.GaussianBlur(cv2.resize(img, (W // 8, H // 8)), (0, 0), 3).mean(2), (W, H))
    for blur, n, Lr, wid, gain in ((4.0, 55, (300, 700), 4, 1.1), (11.0, 22, (500, 1000), 10, 0.8)):
        lay = np.zeros((H, W), np.float32)
        for _ in range(int(n * rate)):
            x, y = rr.uniform(0, W), rr.uniform(-700, H)
            L = rr.uniform(*Lr)
            sl = rr.normal(0.06, 0.025)
            cv2.line(lay, (int(x * 16), int(y * 16)), (int((x + L * sl) * 16), int((y + L) * 16)), rr.uniform(0.3, 1.0), wid, cv2.LINE_AA, shift=4)
        lay = cv2.GaussianBlur(lay, (0, 0), blur)
        img += (lay * (0.0015 + 0.5 * lightL) * gain)[..., None] * MOON
    behind = img.copy()

    col, alpha, depth = leaf_layers()
    defl = leaf_deflection(T)
    u, _ = leaf_uv(XS, YS)
    dy = (defl * bend(u)).astype(np.float32)
    mapx = np.ascontiguousarray(XS + 0 * YS, dtype=np.float32)
    mapy = np.ascontiguousarray(YS - dy, dtype=np.float32)
    defl2 = leaf_deflection(T + SHUTTER)
    mapy2 = np.ascontiguousarray(YS - (defl2 * bend(u)).astype(np.float32), dtype=np.float32)
    mapy_m = np.ascontiguousarray(YS - ((defl + defl2) / 2 * bend(u)).astype(np.float32), dtype=np.float32)
    colw = (cv2.remap(col, mapx, mapy, cv2.INTER_LINEAR) + cv2.remap(col, mapx, mapy_m, cv2.INTER_LINEAR) + cv2.remap(col, mapx, mapy2, cv2.INTER_LINEAR)) / 3
    aw = (cv2.remap(alpha, mapx, mapy, cv2.INTER_LINEAR) + cv2.remap(alpha, mapx, mapy_m, cv2.INTER_LINEAR) + cv2.remap(alpha, mapx, mapy2, cv2.INTER_LINEAR)) / 3
    dw = cv2.remap(depth, mapx, mapy_m, cv2.INTER_LINEAR)
    # light wrap: the bright background bleeds round the leaf's edge
    bgb = cv2.GaussianBlur(img, (0, 0), 9)
    wrap = bgb * (aw * (1 - cv2.GaussianBlur(aw, (0, 0), 6)))[..., None] * 1.6 * smooth(0.02, 0.08, bgb.mean(2))[..., None]
    img = img * (1 - aw[..., None]) + colw + wrap

    def pos(uu, vv):
        p = leaf_point(uu, vv)
        return p[0], p[1] + defl * float(bend(np.float32(uu)))

    impacts, arrivals, releases = events()
    shake = 0.0
    for im in impacts:
        dt = T - im["t"]
        if 0 <= dt < 2:
            shake += math.exp(-dt * 3) * math.sin(dt * 60)
    for (bu, bv, rad, ph, shp) in resting_beads():
        x, y = pos(bu, bv)
        draw_drop(img, colw, behind, x, y, rad, wob=0.25 * shake * math.sin(ph), shape=shp, seed=int(ph * 1000))
    for im in impacts:
        dt = T - im["t"]
        if dt < 0 or dt > 30:
            continue
        rad = 9 + 7 * im["size"]
        if im["slide"] is None:
            if dt > 25:
                continue
            uu, vv = im["u"], im["v"]
        else:
            t0, dur = im["slide"]
            if T > t0 + dur:
                continue
            q = float(np.clip((T - t0) / dur, 0, 1))
            q = q * q * (3 - 2 * q)
            uu = im["u"] + (1.0 - im["u"]) * q
            vv = im["v"] * (1 - q) ** 0.6
            rad *= 1 - 0.3 * q
        # a fresh drop first spreads as a flat film, then pulls into a bead
        grow = smooth(0.0, 0.15, dt)
        wob = math.exp(-dt * 4) * math.sin(dt * 45)
        x, y = pos(uu, vv)
        # satellite droplets the splash leaves around the strike
        if dt < 20:
            rs2 = np.random.default_rng(im["seed"] + 1)
            for _ in range(int(10 + 8 * im["size"])):
                ang_ = rs2.uniform(0, 2 * math.pi)
                dist_ = rs2.uniform(1.3, 4.5) * (9 + 7 * im["size"])
                su = im["u"] + math.cos(ang_) * dist_ / LEN
                sv = im["v"] + math.sin(ang_) * dist_ / HALF_W
                srad = rs2.uniform(1.8, 4.5)
                sseed = int(rs2.integers(1 << 20))
                if not (0.08 < su < 0.97 and -0.9 < sv < 0.9):
                    continue
                appear = smooth(0.02, 0.06, dt)
                sx_, sy_ = pos(su, sv)
                draw_drop(img, colw, behind, sx_, sy_, srad * appear + 0.01, seed=sseed)
        draw_drop(img, colw, behind, x, y, rad * (0.45 + 0.55 * grow), wob=wob, shape=1.0 + 0.5 * (1 - grow), seed=im["seed"])
    vol = tip_volume(T)
    tx, ty = pos(1.0, 0.0)
    trad = 8 + 15 * vol**0.5
    draw_drop(img, colw, behind, tx + 2, ty + trad * 0.75, trad, shape=0.85, backlit=1.0, seed=7)

    streaks = np.zeros((H, W), np.float32)
    spray = np.zeros((H, W), np.float32)
    blurred_spray = np.zeros((H, W), np.float32)
    for im in impacts:
        dt = T - im["t"]
        x, y = pos(im["u"], im["v"])
        if -SHUTTER < dt <= 1e-6:
            cv2.line(streaks, (int(x * 16), 0), (int(x * 16), int(y * 16)), 0.25, 3, cv2.LINE_AA, shift=4)
        if 0 <= dt < 3 / FPS:
            # the crown: droplets thrown up and out, short and slowing
            rs = np.random.default_rng(im["seed"])
            for kk in range(int(9 + 6 * im["size"])):
                a = rs.uniform(math.pi * 1.05, math.pi * 1.95)
                sp = rs.uniform(1500, 7000)
                lift = rs.uniform(0.0, 1.0)  # how far out of the leaf plane (toward the lens)
                tgt = blurred_spray if lift > 0.6 else spray
                pts = []
                for j in range(7):
                    tt = dt + SHUTTER * j / 6
                    pts.append((x + math.cos(a) * sp * tt, y + math.sin(a) * sp * tt * (1 - 0.4 * lift) + 0.5 * G_PX * tt * tt))
                v = 0.55 * math.exp(-dt * 25)
                for j in range(6):
                    w_ = max(1, int((2 + 9 * rs.uniform(0.5, 1.0)) * (j + 1) / 6))  # tapers toward the tail
                    cv2.line(tgt, (int(pts[j][0] * 16), int(pts[j][1] * 16)), (int(pts[j + 1][0] * 16), int(pts[j + 1][1] * 16)), v * 0.25 * (j + 1) / 6, w_, cv2.LINE_AA, shift=4)
                cv2.circle(tgt, (int(pts[-1][0] * 16), int(pts[-1][1] * 16)), int(rs.uniform(2, 4) * 16 * (0.5 if tgt is blurred_spray else 1.0)), v * (0.5 if tgt is blurred_spray else 0.9), -1, cv2.LINE_AA, shift=4)
            if dt < 1 / FPS:
                cv2.circle(streaks, (int(x * 16), int(y * 16)), int((10 + 6 * im["size"]) * 16), 0.35, -1, cv2.LINE_AA, shift=4)
    for tr in releases:
        dt = T - tr
        if 0 <= dt < 0.12:
            dr_ = 7.0
            y1 = ty + trad * 1.4 + 0.5 * G_PX * dt * dt
            y2 = ty + trad * 1.4 + 0.5 * G_PX * (dt + SHUTTER) ** 2
            xx = tx + 2
            body_ = np.zeros((H, W), np.float32)
            cv2.line(body_, (int(xx * 16), int(y1 * 16)), (int(xx * 16), int(y2 * 16)), 0.12, int(dr_ * 2), cv2.LINE_AA, shift=4)
            cv2.line(body_, (int((xx + dr_ * 0.45) * 16), int(y1 * 16)), (int((xx + dr_ * 0.45) * 16), int(y2 * 16)), 0.9, 2, cv2.LINE_AA, shift=4)
            if dt < 2 / FPS:  # the neck still joins it to the pendant
                cv2.line(body_, (int(xx * 16), int((ty + trad) * 16)), (int(xx * 16), int(y1 * 16)), 0.25 * (1 - dt * FPS / 2), 2, cv2.LINE_AA, shift=4)
            img += (cv2.GaussianBlur(body_, (0, 0), 1.0))[..., None] * MOON * 0.35
    streaks = cv2.GaussianBlur(streaks, (0, 0), 0.8)
    img += (streaks * (0.03 + 0.7 * lightL))[..., None] * MOON
    # splash fragments are lit from behind by the moon: they sparkle
    img += (cv2.GaussianBlur(spray, (0, 0), 1.6) * 0.10 + cv2.GaussianBlur(blurred_spray, (0, 0), 7.0) * 0.12)[..., None] * MOON

    # depth of field on the leaf
    coc = np.abs(dw) * aw
    b1 = cv2.GaussianBlur(img, (0, 0), 3.0)
    b2 = cv2.GaussianBlur(img, (0, 0), 9.0)
    k1 = clamp01(coc / 2.0)[..., None]
    k2 = clamp01((coc - 2.0) / 3.0)[..., None]
    img = img * (1 - k1) + b1 * k1
    img = img * (1 - k2) + b2 * k2

    return img * fade(T)


EXPOSURE = 2.8


@functools.lru_cache(maxsize=1)
def _lens():
    x = (XS - W / 2) / (W / 2)
    y = (YS - H / 2) / (H / 2)
    vig = (1 - 0.45 * smooth(0.3, 1.5, np.sqrt(x * x * 0.8 + y * y)))[..., None].astype(np.float32)
    s = 1.1
    mx = np.ascontiguousarray(XS + x * s + 0 * YS, dtype=np.float32)
    my = np.ascontiguousarray(YS + y * s * H / W + 0 * XS, dtype=np.float32)
    mx2 = np.ascontiguousarray(XS - x * s * 0.6 + 0 * YS, dtype=np.float32)
    my2 = np.ascontiguousarray(YS - y * s * 0.6 * H / W + 0 * XS, dtype=np.float32)
    return vig, mx, my, mx2, my2


def develop(img, f):
    small = cv2.resize(img, (W // 4, H // 4), interpolation=cv2.INTER_AREA)
    glare = cv2.GaussianBlur(small, (0, 0), 2.0) * 0.05 + cv2.GaussianBlur(small, (0, 0), 10) * 0.05 + cv2.GaussianBlur(small, (0, 0), 45) * 0.08 + cv2.GaussianBlur(small, (0, 0), 140) * 0.10
    img = img + cv2.resize(glare, (W, H), interpolation=cv2.INTER_LINEAR)
    # a faint ghost of the aperture, mirrored through the optical centre
    gx_, gy_ = W / 2 - (MOON_C[0] - W / 2) * 0.55, H / 2 - (MOON_C[1] - H / 2) * 0.55
    gd = np.sqrt((XS[:, int(gx_) - 70 : int(gx_) + 70] - gx_) ** 2 + (YS[int(gy_) - 70 : int(gy_) + 70] - gy_) ** 2)
    img[int(gy_) - 70 : int(gy_) + 70, int(gx_) - 70 : int(gx_) + 70] += (smooth(62, 52, gd) * 0.006)[..., None] * np.array([0.6, 1.0, 0.8], np.float32)
    vig, mx, my, mx2, my2 = _lens()
    out = aces(img * vig * EXPOSURE).astype(np.float32)
    r_ = cv2.remap(np.ascontiguousarray(out[..., 0]), mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
    b_ = cv2.remap(np.ascontiguousarray(out[..., 2]), mx2, my2, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
    s_ = to_srgb(np.stack([r_, out[..., 1], b_], -1))
    # night sensor noise: luminance grain strongest in the shadows, a little chroma
    r = np.random.default_rng(f * 7 + 5)
    g = cv2.GaussianBlur(r.normal(0, 1, (H, W)).astype(np.float32), (0, 0), 0.7)
    cn = cv2.GaussianBlur(r.normal(0, 1, (H // 2, W // 2, 3)).astype(np.float32), (0, 0), 1.0)
    cn = cv2.resize(cn, (W, H))
    amt = 0.010 + 0.022 * (1 - s_.mean(2, keepdims=True)) ** 2
    s_ = s_ + g[..., None] * amt + cn * 0.004
    return (np.clip(s_, 0, 1) * 255 + 0.5).astype(np.uint8)


def develop_render(img, f, exposure=1.3, sat=0.78, T=61.0):
    """Grade a path-traced linear frame like a night exposure: rain streaks lit
    by what is behind them, restrained glare, a deep black point, muted cool
    colour, CA and grain."""
    rr = np.random.default_rng(f * 31 + 9)
    lightL = cv2.resize(cv2.GaussianBlur(cv2.resize(img, (W // 8, H // 8)), (0, 0), 3).mean(2), (W, H))
    for blur, n, Lr, wid, gain in ((3.0, 60, (300, 700), 3, 1.0), (9.0, 22, (500, 1000), 8, 0.7)):
        lay = np.zeros((H, W), np.float32)
        for _ in range(n):
            x, y = rr.uniform(0, W), rr.uniform(-700, H)
            L = rr.uniform(*Lr)
            sl = rr.normal(0.05, 0.02)
            cv2.line(lay, (int(x * 16), int(y * 16)), (int((x + L * sl) * 16), int((y + L) * 16)), rr.uniform(0.3, 1.0), wid, cv2.LINE_AA, shift=4)
        lay = cv2.GaussianBlur(lay, (0, 0), blur)
        img = img + (lay * (0.0008 + 0.35 * np.minimum(lightL, 0.6)) * gain)[..., None] * MOON
    lum = img.mean(2, keepdims=True)
    img = (img * sat + lum * (1 - sat)) * np.array([0.92, 0.98, 1.08], np.float32)
    small = cv2.resize(img, (W // 4, H // 4), interpolation=cv2.INTER_AREA)
    glare = cv2.GaussianBlur(small, (0, 0), 2.0) * 0.04 + cv2.GaussianBlur(small, (0, 0), 10) * 0.04 + cv2.GaussianBlur(small, (0, 0), 40) * 0.035
    img = img + cv2.resize(glare, (W, H), interpolation=cv2.INTER_LINEAR)
    img = np.maximum(img - 0.0012, 0)  # black point
    vig, mx, my, mx2, my2 = _lens()
    out = aces(img * vig * exposure).astype(np.float32)
    r_ = cv2.remap(np.ascontiguousarray(out[..., 0]), mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
    b_ = cv2.remap(np.ascontiguousarray(out[..., 2]), mx2, my2, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
    s_ = to_srgb(np.stack([r_, out[..., 1], b_], -1))
    r = np.random.default_rng(f * 7 + 5)
    g = cv2.GaussianBlur(r.normal(0, 1, (H, W)).astype(np.float32), (0, 0), 0.7)
    s_ = s_ + g[..., None] * (0.008 + 0.014 * (1 - s_.mean(2, keepdims=True)) ** 2)
    return (np.clip(s_, 0, 1) * 255 + 0.5).astype(np.uint8)


def frame(T: float, f: int | None = None) -> np.ndarray:
    f = int(round(T * FPS)) if f is None else f
    return develop(frame_linear(T, f), f)

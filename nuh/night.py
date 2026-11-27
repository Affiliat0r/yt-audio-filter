"""One quasi-static scene for the whole recitation: rain pouring off the
edge of a wooden roof at night, a full moon behind it, a fence below.

The camera never moves. What moves: rain in four depths (lit by whatever is
behind it), water streaming and dripping off the eave, droplets glinting on
the wet roof, thin cloud drifting across the moon. Static parts are drawn
at twice the output size and reduced once.

`frame(T)` is a pure function of the film time T (seconds).
"""

from __future__ import annotations

import functools
import json
import math
from pathlib import Path

import cv2
import numpy as np

from .paint import H, W, XS, YS, clamp01, fbm_tex, glow, hexc, mix, smooth

CUTS = json.loads((Path(__file__).parent / "data" / "cuts.json").read_text())
SS = 2

# composition --------------------------------------------------------------
MX, MY, MR = 1262.0, 262.0, 158.0  # the moon
SLOPE = 0.372  # the eave runs down to the right


def edge_y(x):
    return 168.0 + x * SLOPE


def roof_top_y(x):
    # the far side of the visible roof surface: wide on the left, narrow on the right
    return edge_y(x) - (150.0 - x * 0.052)


def fascia_h(x):
    return 34.0 - x * 0.0085


C_SKY_TOP = hexc("#01030a")
C_SKY_MID = hexc("#040c1e")
C_SKY_LOW = hexc("#08142c")
C_MOONLIGHT = hexc("#a8c4ff")
C_RAIN = hexc("#bcd2ff")


def cue(T: float) -> dict:
    s = {x["ayah"]: (x["start"], x["end"]) for x in CUTS["shots"]}

    def span(a, b, ramp=6.0):
        t0, t1 = s[a][0], s[b][1]
        return float(smooth(t0 - ramp, t0 + ramp, T) * (1 - smooth(t1 - ramp, t1 + ramp, T)))

    rain = 0.8 + 0.2 * span(11, 12) + 0.25 * span(25, 27) - 0.3 * span(28, 28, 10)
    veil = 1.0 - 0.7 * span(16, 16, 3.0)  # "the moon a light": the cloud clears off it
    audio = CUTS["audio_seconds"]
    last = CUTS["shots"][-1]["start"] + CUTS["events"]["28"]["end"]
    fade = float(smooth(0.0, 5.0, T) * (1 - smooth(last + 0.3, audio, T)))
    return dict(rain=rain, veil=veil, fade=fade)


# --------------------------------------------------------------------------
# the moon
# --------------------------------------------------------------------------


@functools.lru_cache(maxsize=1)
def moon_sprite():
    """A full moon: maria, craters, ray systems, a soft spherical falloff.
    Built at 4x and reduced; returns premultiplied RGBA."""
    k = 4
    R = MR * k
    n = int(R * 2 + 16)
    c = n / 2
    yy, xx = np.mgrid[0:n, 0:n].astype(np.float32)
    px, py = (xx - c) / R, (yy - c) / R
    rr = np.sqrt(px * px + py * py)
    inside = rr < 1.0
    # sphere coordinates, so features crowd toward the limb
    zz = np.sqrt(np.clip(1 - rr * rr, 0, 1))
    lon = np.arctan2(px, zz)
    lat = np.arcsin(np.clip(py, -1, 1))
    u = (lon / math.pi + 0.5) * 1024
    v = (lat / math.pi + 0.5) * 1024
    m1 = fbm_tex(1024, 1024, 2001, base=3, octaves=6, gain=0.55)
    m2 = fbm_tex(1024, 1024, 2002, base=12, octaves=5, gain=0.5)
    mare = cv2.remap(m1, u.astype(np.float32), v.astype(np.float32), cv2.INTER_LINEAR, borderMode=cv2.BORDER_WRAP)
    fine = cv2.remap(m2, u.astype(np.float32), v.astype(np.float32), cv2.INTER_LINEAR, borderMode=cv2.BORDER_WRAP)
    mare = cv2.GaussianBlur(mare, (0, 0), 6)
    albedo = 0.9 - 0.30 * smooth(0.48, 0.70, mare) - 0.06 * smooth(0.62, 0.85, mare)
    albedo *= 0.94 + 0.12 * fine
    # craters
    r = np.random.default_rng(2003)
    cr = np.zeros((n, n), np.float32)
    for _ in range(520):
        s = (r.random() ** 3.2) * 0.09 + 0.006
        a = r.uniform(0, 2 * math.pi)
        d = math.sqrt(r.random()) * 0.97
        cx, cy = c + math.cos(a) * d * R, c + math.sin(a) * d * R
        rad = s * R * max(0.35, math.sqrt(max(0.0, 1 - d * d)))
        x0, x1 = int(max(0, cx - rad * 2)), int(min(n, cx + rad * 2 + 1))
        y0, y1 = int(max(0, cy - rad * 2)), int(min(n, cy + rad * 2 + 1))
        if x1 <= x0 or y1 <= y0:
            continue
        dd = np.sqrt((xx[y0:y1, x0:x1] - cx) ** 2 + (yy[y0:y1, x0:x1] - cy) ** 2) / rad
        bowl = -0.10 * np.exp(-(dd / 0.75) ** 4)
        rim = 0.16 * np.exp(-((dd - 1.0) / 0.14) ** 2)
        cr[y0:y1, x0:x1] += (bowl + rim) * r.uniform(0.5, 1.0)
    # two bright ray craters, the way the full moon shows them
    for (ax, ay, L) in ((0.18, 0.62, 0.9), (-0.45, -0.2, 0.55)):
        cx, cy = c + ax * R, c + ay * R
        ang = np.arctan2(yy - cy, xx - cx)
        dist = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2) / R
        rays = np.zeros_like(dist)
        for k2 in range(14):
            a0 = r.uniform(-math.pi, math.pi)
            wdt = r.uniform(0.02, 0.06)
            da = np.angle(np.exp(1j * (ang - a0)))
            rays += np.exp(-(da / wdt) ** 2) * np.exp(-dist / (L * r.uniform(0.4, 1.0)))
        cr += 0.18 * rays + 0.35 * np.exp(-(dist / 0.03) ** 2)
    albedo = albedo + cr * 0.45
    limb = 0.82 + 0.18 * zz ** 0.6
    lum = np.clip(albedo * limb, 0, 1.4)
    a = np.clip((1 - rr) * R / 2.0, 0, 1)
    tint = np.stack([0.80 + 0.18 * lum, 0.88 + 0.12 * lum, np.ones_like(lum)], -1)
    col = (lum[..., None] * tint) * 0.50
    rgba = np.concatenate([col * a[..., None], a[..., None]], 2).astype(np.float32)
    small = cv2.resize(rgba, (n // k, n // k), interpolation=cv2.INTER_AREA)
    small = cv2.GaussianBlur(small, (0, 0), 0.9)  # a touch of lens softness
    return small


# --------------------------------------------------------------------------
# static set: sky, roof, fascia, fence (drawn at 2x)
# --------------------------------------------------------------------------


@functools.lru_cache(maxsize=1)
def static_set():
    """Returns (base image float HxWx3 without moon, set alpha HxW, spec HxW)."""
    w, h = W * SS, H * SS
    ys = (np.arange(h, dtype=np.float32) / SS)[:, None]
    xs = (np.arange(w, dtype=np.float32) / SS)[None, :]

    # sky
    yn = ys / H
    sky = np.zeros((h, w, 3), np.float32)
    for c in range(3):
        sky[..., c] = np.interp(yn[:, 0], [0, 0.35, 0.8], [C_SKY_TOP[c], C_SKY_MID[c], C_SKY_LOW[c]])[:, None]
    d = np.sqrt((xs - MX) ** 2 + (ys - MY) ** 2)
    sky += (np.exp(-d / 230.0) * 0.16 + np.exp(-d / 650.0) * 0.06 + np.exp(-d / 1600.0) * 0.02)[..., None] * C_MOONLIGHT

    ey = edge_y(xs)
    ty = roof_top_y(xs)
    fh = fascia_h(xs)
    roof = ((ys >= ty) & (ys < ey)).astype(np.float32)
    fasc = ((ys >= ey) & (ys < ey + fh)).astype(np.float32)
    below = (ys >= ey + fh).astype(np.float32)

    def soft(a, b):  # 1 between a and b, antialiased (in output px)
        return clamp01((ys - a) * SS + 0.5) * clamp01((b - ys) * SS + 0.5)

    roof = soft(ty, ey)
    fasc = soft(ey, ey + fh)
    below = clamp01((ys - (ey + fh)) * SS + 0.5)

    # ---- roof surface: wet boards running along the eave
    q = clamp01((ey - ys) / (ey - ty))  # 0 at the edge, 1 at the far side
    s_along = xs + (ys - ey) / SLOPE * 0.0
    g1 = fbm_tex(640, 2160, 2101, base=2, octaves=6)
    g1 = cv2.resize(g1, (w * 2, h * 2), interpolation=cv2.INTER_LINEAR)
    rot = cv2.getRotationMatrix2D((w, h), -math.degrees(math.atan(SLOPE)), 1.0)
    rot[0, 2] -= w / 2
    rot[1, 2] -= h / 2
    grain = cv2.warpAffine(g1, rot, (w, h), borderMode=cv2.BORDER_REFLECT)
    seam_n = 4
    qq = q ** 0.8 * seam_n
    seam = np.exp(-((qq - np.round(qq)) / 0.05) ** 2) * (np.round(qq) > 0)
    bevel = np.exp(-((qq - np.round(qq) - 0.06) / 0.03) ** 2)
    joints = np.zeros_like(seam)
    rr = np.random.default_rng(2102)
    for k in range(seam_n):
        for _ in range(0):
            jx = rr.uniform(0, W)
            joints += np.exp(-((xs - jx) / 1.2) ** 2) * ((qq > k) & (qq < k + 1))
    wood = hexc("#2a2622")
    roof_col = wood * (0.35 + 0.9 * grain[..., None])
    roof_col *= (1 - 0.6 * seam[..., None]) * (1 - 0.5 * np.clip(joints, 0, 1)[..., None])
    # wet sheen: the moon's reflection runs as a broad bright band along the boards
    spec_x = np.exp(-((xs - (MX - 160)) / 420.0) ** 2)
    sheen = spec_x * (1 - q) ** 1.2 * (0.25 + smooth(0.3, 0.9, grain)) + 0.015
    roof_col += (sheen * 0.5)[..., None] * C_MOONLIGHT
    roof_col += (bevel * spec_x * 0.25)[..., None] * C_MOONLIGHT
    # darker toward the far side
    roof_col *= (1 - 0.55 * q ** 1.5)[..., None]

    # ---- fascia: the edge board, its wet lip catching the light
    fq = clamp01((ys - ey) / fh)
    fg = cv2.resize(fbm_tex(128, 2048, 2103, base=3, octaves=6), (w, h))
    fasc_col = hexc("#0c1018") * (0.7 + 0.6 * fg[..., None])
    lip = np.exp(-((ys - ey) / 1.6) ** 2) * (0.12 + 0.8 * spec_x)
    fasc_col += (lip * 0.75)[..., None] * C_MOONLIGHT
    fasc_col += (np.exp(-((ys - ey - fh) / 1.5) ** 2) * 0.15 * spec_x)[..., None] * C_MOONLIGHT

    # ---- the fence: vertical planks in the shade of the eave
    pw = 88.0
    off = 23.0
    pidx = np.floor((xs + off) / pw)
    pos = ((xs + off) / pw) - pidx
    gap = smooth(0.0, 0.035, pos) * (1 - smooth(0.93, 0.975, pos))
    rnd = np.random.default_rng(2104)
    tones = rnd.uniform(0.75, 1.15, 64).astype(np.float32)
    tone = tones[(pidx.astype(int) % 64)]
    g2 = fbm_tex(4096, 160, 2105, base=24, octaves=6)
    vgrain = cv2.resize(g2, (w, h), interpolation=cv2.INTER_LINEAR)
    plank = hexc("#5a4838")
    fence = plank * (tone * (0.62 + 0.55 * vgrain))[..., None]
    # rounded plank faces
    fence *= (0.78 + 0.32 * np.sin(np.clip(pos, 0, 1) * math.pi))[..., None]
    fence *= gap[..., None] * 0.95 + 0.05
    # light: the eave's shadow at the top, soft light rising from below-left
    under = ys - (ey + fh)
    shade = smooth(0, 520, under) ** 1.8
    light = 0.05 + 0.95 * shade * (0.45 + 0.8 * np.exp(-((xs - 250) / 750.0) ** 2)) * (0.35 + 0.9 * (ys / H) ** 1.4)
    fence *= light[..., None]
    # cool moonlight bounced onto the planks
    fence += (shade * 0.02)[..., None] * C_MOONLIGHT
    # wet streaks running down beneath the drips
    wet = smooth(0.55, 0.9, cv2.resize(fbm_tex(512, 64, 2106, base=40, octaves=3), (w, h)))
    fence *= (1 - 0.25 * wet[..., None])
    fence += (wet * 0.06 * shade)[..., None] * C_MOONLIGHT

    img = sky * (1 - roof - fasc - below)[..., None] + roof_col * roof[..., None] + fasc_col * fasc[..., None] + fence * below[..., None]
    set_a = np.clip(roof + fasc + below, 0, 1)
    spec = (spec_x * (roof * (1 - q) + fasc)).astype(np.float32)
    img = cv2.resize(img, (W, H), interpolation=cv2.INTER_AREA)
    set_a = cv2.resize(set_a, (W, H), interpolation=cv2.INTER_AREA)
    spec = cv2.resize(spec, (W, H), interpolation=cv2.INTER_AREA)
    # depth of field: the fence is a little soft
    fy = (YS > edge_y(XS) + fascia_h(XS) + 2).astype(np.float32)[..., None]
    img = img * (1 - fy) + cv2.GaussianBlur(img, (0, 0), 1.4) * fy
    return img, set_a, spec


# --------------------------------------------------------------------------
# moving parts
# --------------------------------------------------------------------------


@functools.lru_cache(maxsize=1)
def wisp_tex():
    return fbm_tex(2048, 512, 2201, base=3, octaves=6, gain=0.55)


def wisps(T: float, veil: float):
    """Thin cloud drifting across the sky, lit by the moon."""
    A = wisp_tex()
    hh = 620
    ys, xs = np.mgrid[0:hh // 2, 0:W // 2].astype(np.float32)
    u = (xs * 0.55 - T * 3.2) % A.shape[1]
    v = (ys * 1.6 + 120 + 30 * np.sin(xs / 300.0 + T * 0.02)) % A.shape[0]
    D = cv2.remap(A, u, v, cv2.INTER_LINEAR, borderMode=cv2.BORDER_WRAP)
    D = cv2.resize(D, (W, hh), interpolation=cv2.INTER_CUBIC)
    a = smooth(0.45, 0.85, D) * 0.75 * veil
    d = np.sqrt((XS - MX) ** 2 + (YS[:hh] - MY) ** 2)
    lit = np.exp(-d / 330.0)
    col = hexc("#1a2a4a")[None, None] + (lit[..., None] * 0.95 + 0.05) * C_MOONLIGHT * 0.9
    return a, col, hh


@functools.lru_cache(maxsize=4)
def rain_tile(seed, n, length, thick, blur, slant, w=W, h=H):
    r = np.random.default_rng(seed)
    tile = np.zeros((h, w), np.float32)
    for _ in range(n):
        x = r.random() * w
        y = r.random() * h
        L = length * r.uniform(0.55, 1.3)
        v = r.uniform(0.3, 1.0)
        x2, y2 = x + L * slant, y + L
        th = max(1, int(round(thick * r.uniform(0.7, 1.3))))
        for oy in (0, -h, h):
            for ox in (0, -w, w):
                cv2.line(tile, (int((x + ox) * 16), int((y + oy) * 16)), (int((x2 + ox) * 16), int((y2 + oy) * 16)), v, th, cv2.LINE_AA, shift=4)
    if blur:
        tile = cv2.GaussianBlur(tile, (0, 0), blur)
    return tile


RAIN_LAYERS = [
    # seed, count, length, thickness, blur, slant, speed px/s, gain
    (2301, 5200, 26, 1, 0.0, 0.035, 900.0, 0.28),
    (2302, 1900, 85, 1, 0.45, 0.04, 1500.0, 0.55),
    (2303, 420, 210, 2, 1.1, 0.045, 2300.0, 0.6),
    (2304, 60, 420, 4, 3.8, 0.05, 3400.0, 0.55),
]


def rain(T: float, amount: float) -> np.ndarray:
    out = np.zeros((H, W), np.float32)
    for seed, n, L, th, bl, sl, sp, g in RAIN_LAYERS:
        tile = rain_tile(seed, n, L, th, bl, sl)
        oy = int(T * sp) % H
        ox = int(T * sp * sl) % W
        out += np.roll(tile, (oy, ox), (0, 1)) * g
    return out * amount


@functools.lru_cache(maxsize=1)
def drip_points():
    r = np.random.default_rng(2401)
    pts = []
    x = 8.0
    while x < W - 40:
        strength = r.uniform(0.4, 1.0)
        pts.append(dict(x=x, period=r.uniform(0.18, 0.55), phase=r.uniform(0, 5), thread=r.random() < 0.45,
                        strength=strength, sway=r.uniform(-0.03, 0.03), stream=r.random() < 0.3))
        x += r.uniform(12, 34)
    return pts


def drips(T: float, amount: float) -> np.ndarray:
    """Water running and dripping off the eave: threads at the lip, drops
    that stretch into streaks as they fall, fading with the fall."""
    layer = np.zeros((H, W), np.float32)
    g = 2600.0  # px / s^2
    exp = 1 / 30.0  # motion-blur exposure
    for p in drip_points():
        x0 = p["x"]
        y0 = edge_y(x0) + fascia_h(x0) - 2
        st = p["strength"] * min(1.0, amount)
        if p["thread"]:
            ph = ((T + p["phase"]) / (p["period"] * 3)) % 1.0
            L = 6 + 46 * ph ** 1.6
            cv2.line(layer, (int(x0 * 16), int(y0 * 16)), (int((x0 + p["sway"] * L) * 16), int((y0 + L) * 16)), 0.55 * st, 1, cv2.LINE_AA, shift=4)
            cv2.circle(layer, (int((x0 + p["sway"] * L) * 16), int((y0 + L) * 16)), 24, 0.9 * st, -1, cv2.LINE_AA, shift=4)
        if p["stream"]:
            # a steady pour: a continuous wavering line that breaks up lower down
            Ls = 140 + 160 * p["strength"]
            pts = []
            for k in range(24):
                u = k / 23
                yy = y0 + u * Ls
                xx = x0 + p["sway"] * u * Ls + 1.2 * math.sin(T * 9 + k * 0.7 + p["phase"])
                pts.append((xx, yy))
            for k in range(23):
                u = k / 23
                if u > 0.45 and math.sin(T * 31 + k * 2.1 + p["phase"] * 5) > 0.2:
                    continue
                v = st * 0.7 * (1 - u) ** 1.2
                cv2.line(layer, (int(pts[k][0] * 16), int(pts[k][1] * 16)), (int(pts[k + 1][0] * 16), int(pts[k + 1][1] * 16)), v, 2, cv2.LINE_AA, shift=4)
        fall = H - y0 + 40
        tfall = math.sqrt(2 * fall / g)
        n_in_air = int(tfall / p["period"]) + 2
        q = (T + p["phase"]) / p["period"]
        base = math.floor(q)
        for k in range(n_in_air):
            age = (q - (base - k)) * p["period"]
            if age < 0 or age > tfall:
                continue
            y = y0 + 0.5 * g * age * age
            v = g * age
            L = max(4.0, v * exp)
            x = x0 + p["sway"] * (y - y0)
            fade = math.exp(-(y - y0) / 420.0)
            val = st * (0.35 + 0.65 * fade)
            # the streak, brightest at its leading end
            cv2.line(layer, (int(x * 16), int((y - L) * 16)), (int(x * 16), int((y - L * 0.3) * 16)), val * 0.45, 1, cv2.LINE_AA, shift=4)
            cv2.line(layer, (int(x * 16), int((y - L * 0.3) * 16)), (int(x * 16), int(y * 16)), val, 2 if fade > 0.4 else 1, cv2.LINE_AA, shift=4)
    return cv2.GaussianBlur(layer, (0, 0), 0.6)


@functools.lru_cache(maxsize=1)
def glints():
    """Water beads on the roof and the lip of the eave."""
    r = np.random.default_rng(2501)
    n = 2600
    xs = r.uniform(0, W, n)
    q = r.random(n) ** 2.2  # crowd toward the edge
    ey = edge_y(xs)
    ty = roof_top_y(xs)
    ys = ey - q * (ey - ty)
    # a row of beads right on the lip
    m = 500
    lx = r.uniform(0, W, m)
    ly = edge_y(lx) + r.uniform(-1, 2, m)
    xs = np.concatenate([xs, lx])
    ys = np.concatenate([ys, ly])
    mag = np.concatenate([r.random(n) ** 3, r.random(m) ** 2])
    ph = r.uniform(0, 6.28, n + m)
    fr = r.uniform(0.6, 3.0, n + m)
    return xs.astype(np.float32), ys.astype(np.float32), mag.astype(np.float32), ph.astype(np.float32), fr.astype(np.float32)


def glint_layer(T: float, spec: np.ndarray, amount: float) -> np.ndarray:
    xs, ys, mag, ph, fr = glints()
    xi = np.clip(xs.astype(int), 0, W - 1)
    yi = np.clip(ys.astype(int), 0, H - 1)
    sp = spec[yi, xi] + 0.08
    tw = np.clip(np.sin(T * fr + ph), 0, 1) ** 4
    v = mag * sp * (0.25 + 0.75 * tw)
    lay = np.zeros((H, W), np.float32)
    np.add.at(lay, (yi, xi), v)
    # splashes: rain landing on the roof, a few per frame
    r = np.random.default_rng(int(T * 24) + 77)
    k = int(60 * amount)
    sx = r.uniform(0, W, k)
    sq = r.random(k) ** 1.5
    sy = edge_y(sx) - sq * (edge_y(sx) - roof_top_y(sx))
    np.add.at(lay, (np.clip(sy.astype(int), 0, H - 1), np.clip(sx.astype(int), 0, W - 1)), r.uniform(0.4, 1.0, k))
    lay = cv2.GaussianBlur(lay, (0, 0), 0.8) * 5.0 + cv2.GaussianBlur(lay, (0, 0), 3.0) * 6.0
    return lay


# --------------------------------------------------------------------------
# the frame
# --------------------------------------------------------------------------


def frame_float(T: float) -> np.ndarray:
    c = cue(T)
    base, set_a, spec = static_set()
    img = base.copy()

    # moon behind the set
    sp = moon_sprite()
    n = sp.shape[0]
    x0, y0 = int(round(MX - n / 2)), int(round(MY - n / 2))
    sub = img[y0 : y0 + n, x0 : x0 + n]
    sa = sp[..., 3:4] * (1 - set_a[y0 : y0 + n, x0 : x0 + n, None])
    sub *= 1 - sa
    sub += sp[..., :3] * (1 - set_a[y0 : y0 + n, x0 : x0 + n, None])
    sky_a = (1 - set_a)[..., None]
    glow_l = np.zeros_like(img)
    glow(glow_l, MX, MY, MR * 1.18, C_MOONLIGHT, 0.16)
    glow(glow_l, MX, MY, MR * 2.4, C_MOONLIGHT, 0.11, kind="halo", reach=8)
    img += glow_l * sky_a

    # thin cloud over the sky
    a, col, hh = wisps(T, c["veil"])
    aa = (a * (1 - set_a[:hh]))[..., None]
    img[:hh] = img[:hh] * (1 - aa * 0.75) + col * aa * 0.75 + col * aa * 0.25

    # glints on the wet roof
    img += glint_layer(T, spec, c["rain"])[..., None] * hexc("#d8e6ff")

    # water off the eave and rain, lit by what lies behind them
    bg = cv2.resize(img, (W // 8, H // 8), interpolation=cv2.INTER_AREA)
    bg = cv2.GaussianBlur(bg, (0, 0), 4)
    bgl = cv2.resize(bg.mean(2), (W, H), interpolation=cv2.INTER_LINEAR)
    dr = drips(T, c["rain"])
    img += (dr * (0.22 + 2.6 * bgl))[..., None] * C_RAIN
    rn = rain(T, c["rain"])
    img += (rn * (0.015 + 1.5 * np.minimum(bgl, 0.32)))[..., None] * C_RAIN
    # a faint veil of spray in the air
    img += (bgl * 0.06 * c["rain"])[..., None] * C_MOONLIGHT
    return img * c["fade"]


def finish(img: np.ndarray, f: int) -> np.ndarray:
    small = cv2.resize(img, (W // 4, H // 4), interpolation=cv2.INTER_AREA)
    br = np.maximum(small - 0.40, 0)
    b = cv2.GaussianBlur(br, (0, 0), 3) * 0.5 + cv2.GaussianBlur(br, (0, 0), 14) * 0.9 + cv2.GaussianBlur(br, (0, 0), 40) * 0.6
    img = img + cv2.resize(b, (W, H), interpolation=cv2.INTER_LINEAR) * 0.6
    # cool grade, deep blacks with a little lift
    img = img * np.array([0.92, 0.98, 1.06], np.float32) + np.array([0.004, 0.008, 0.018], np.float32)
    x = (XS - W * 0.6) / (W / 2)
    y = (YS - H * 0.45) / (H / 2)
    v = 1 - 0.38 * smooth(0.5, 1.6, np.sqrt(x * x * 0.7 + y * y))
    img = img * v[..., None]
    img = np.where(img > 0.78, 0.78 + 0.22 * np.tanh((img - 0.78) / 0.22), img)
    r = np.random.default_rng(f % 24)
    g = cv2.resize(r.normal(0, 1, (H // 2, W // 2)).astype(np.float32), (W, H), interpolation=cv2.INTER_LINEAR)
    img = img + g[..., None] * 0.005
    return (np.clip(img, 0, 1) * 255 + 0.5).astype(np.uint8)


def frame(T: float, f: int | None = None) -> np.ndarray:
    return finish(frame_float(T), int(round(T * 24)) if f is None else f)

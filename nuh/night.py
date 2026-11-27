"""One quasi-static scene for the whole recitation: rain pouring off the
edge of a shingled roof at night, the full moon behind it, a fence below.

Rendered in linear light (scene radiance), then through a filmic tone curve,
lens glare, depth of field, slight chromatic aberration and sensor grain, so
it reads as a photograph. The moon carries the real maria and ray craters at
their selenographic positions, projected on a sphere.

The camera never moves. What moves: rain in four depths, water running and
dripping off the eave, glints on the wet shingles, thin cloud over the moon.

`frame(T)` is a pure function of the film time T (seconds). No image files,
no image models: numpy, OpenCV and pycairo only.
"""

from __future__ import annotations

import functools
import json
import math
from pathlib import Path

import cv2
import numpy as np

from .paint import H, W, XS, YS, clamp01, fbm_tex, smooth

CUTS = json.loads((Path(__file__).parent / "data" / "cuts.json").read_text())
SS = 2  # supersampling for the static set

# --------------------------------------------------------------------------
# colour
# --------------------------------------------------------------------------


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


MOON_TINT = lin("#c8dcff")  # the grade: a cold blue moonlight
SKY_TOP = lin("#02040b") * 0.6
SKY_LOW = lin("#0a1834") * 0.9
RAIN_TINT = lin("#d4e2ff")

# --------------------------------------------------------------------------
# composition
# --------------------------------------------------------------------------

MX, MY, MR = 1268.0, 258.0, 160.0
SLOPE = 0.372


def edge_y(x):
    return 176.0 + x * SLOPE


def roof_top_y(x):
    return edge_y(x) - (250.0 - x * 0.07)


def fascia_h(x):
    return 30.0 - x * 0.006


def cue(T: float) -> dict:
    s = {x["ayah"]: (x["start"], x["end"]) for x in CUTS["shots"]}

    def span(a, b, ramp=6.0):
        t0, t1 = s[a][0], s[b][1]
        return float(smooth(t0 - ramp, t0 + ramp, T) * (1 - smooth(t1 - ramp, t1 + ramp, T)))

    rain = 0.8 + 0.2 * span(11, 12) + 0.25 * span(25, 27) - 0.3 * span(28, 28, 10)
    veil = 1.0 - 0.7 * span(16, 16, 3.0)
    audio = CUTS["audio_seconds"]
    last = CUTS["shots"][-1]["start"] + CUTS["events"]["28"]["end"]
    fade = float(smooth(0.0, 5.0, T) * (1 - smooth(last + 0.3, audio, T)))
    return dict(rain=rain, veil=veil, fade=fade)


# --------------------------------------------------------------------------
# the moon: the real near side, on a sphere
# --------------------------------------------------------------------------

R_MOON_KM = 1737.4
# (lat, lon, radius km) caps; several caps make the irregular maria
MARIA = [
    # Oceanus Procellarum
    (26, -52, 640), (12, -58, 600), (-2, -50, 420), (40, -50, 380), (20, -40, 420), (32, -62, 380), (0, -62, 380),
    (7, -31, 250),  # Insularum
    (-10, -23, 190),  # Cognitum
    (-21, -17, 360), (-16, -12, 200),  # Nubium
    (-24, -39, 200),  # Humorum
    (33, -16, 560), (40, -22, 300), (28, -8, 300),  # Imbrium
    (28, 17, 345),  # Serenitatis
    (8.5, 31, 400), (15, 25, 220), (2, 23, 200),  # Tranquillitatis
    (17, 59, 285),  # Crisium
    (-8, 51, 330), (-2, 53, 220), (-14, 50, 230),  # Fecunditatis
    (-15, 35, 170),  # Nectaris
    (13, 4, 120),  # Vaporum
    (2, 1, 90),  # Sinus Medii
    (57, -35, 130), (56, -15, 150), (56, 5, 150), (55, 25, 150), (55, 42, 110),  # Frigoris
    (1, 87, 190), (13, 86, 200),  # Smythii, Marginis
    (-5, -68, 95),  # Grimaldi
    (51.6, -9.4, 50),  # Plato
]
RAYS = [
    # lat, lon, crater km, ray length km, n rays, strength
    (-43.3, -11.2, 43, 1500, 22, 1.0),  # Tycho
    (9.6, -20.1, 47, 700, 18, 0.65),  # Copernicus
    (8.1, -38.0, 16, 400, 12, 0.5),  # Kepler
    (23.7, -47.4, 20, 300, 10, 0.55),  # Aristarchus
    (16.1, 46.8, 14, 350, 10, 0.4),  # Proclus
    (-8.6, 60.9, 9, 200, 8, 0.3),  # Langrenus area
    (-33.4, -66.2, 40, 250, 6, 0.25),
]


@functools.lru_cache(maxsize=1)
def moon_sprite():
    """Linear radiance of the disc (relative albedo), premultiplied RGBA."""
    k = 4
    R = MR * k
    n = int(R * 2 + 16)
    c = n / 2
    yy, xx = np.mgrid[0:n, 0:n].astype(np.float32)
    px, py = (xx - c) / R, (yy - c) / R
    rr = np.sqrt(px * px + py * py)
    zz = np.sqrt(np.clip(1 - rr * rr, 0, 1))
    lat = np.arcsin(np.clip(-py, -1, 1))
    lon = np.arctan2(px, np.maximum(zz, 1e-4))
    slat, clat = np.sin(lat), np.cos(lat)

    def sph(tex):
        u = ((lon / (2 * math.pi) + 0.5) * tex.shape[1]) % tex.shape[1]
        v = ((lat / math.pi + 0.5) * tex.shape[0]) % tex.shape[0]
        return cv2.remap(tex, u.astype(np.float32), v.astype(np.float32), cv2.INTER_LINEAR, borderMode=cv2.BORDER_WRAP)

    t1 = sph(fbm_tex(1024, 512, 3001, base=6, octaves=6))
    t2 = sph(fbm_tex(1024, 512, 3002, base=24, octaves=5))
    t3 = sph(fbm_tex(2048, 1024, 3003, base=60, octaves=4))

    def gdist(lat0, lon0):
        la0, lo0 = math.radians(lat0), math.radians(lon0)
        cd = slat * math.sin(la0) + clat * math.cos(la0) * np.cos(lon - lo0)
        return np.arccos(np.clip(cd, -1, 1))

    mare = np.zeros_like(rr)
    for lat0, lon0, rkm in MARIA:
        a = rkm / R_MOON_KM
        d = gdist(lat0, lon0) + (t1 - 0.5) * (a * 0.9 + 0.06) + (t2 - 0.5) * (a * 0.35 + 0.02)
        mare = np.maximum(mare, smooth(a * 1.15, a * 0.7, d))
    mare = cv2.GaussianBlur(mare, (0, 0), 7.0)
    high = 1.0 * (0.82 + 0.28 * t2) * (0.92 + 0.16 * t3)
    low = 0.48 * (0.84 + 0.26 * t1) * (0.94 + 0.1 * t3)
    alb = high * (1 - mare) + low * mare
    # small fresh craters: bright specks
    r = np.random.default_rng(3004)
    specks = np.zeros_like(rr)
    for _ in range(700):
        la = math.degrees(math.asin(r.uniform(-1, 1)))
        lo = r.uniform(-90, 90)
        kmr = (r.random() ** 3) * 25 + 2
        a = kmr / R_MOON_KM
        la0, lo0 = math.radians(la), math.radians(lo)
        # bounding box in pixels for speed
        cx = c + math.cos(la0) * math.sin(lo0) * R
        cy = c - math.sin(la0) * R
        bb = int(a * R * 3.5) + 3
        ya, yb = max(0, int(cy) - bb), min(n, int(cy) + bb)
        xa, xb = max(0, int(cx) - bb), min(n, int(cx) + bb)
        if ya >= yb or xa >= xb:
            continue
        cd = slat[ya:yb, xa:xb] * math.sin(la0) + clat[ya:yb, xa:xb] * math.cos(la0) * np.cos(lon[ya:yb, xa:xb] - lo0)
        d = np.arccos(np.clip(cd, -1, 1))
        specks[ya:yb, xa:xb] += np.exp(-(d / a) ** 2) * 0.5 * r.uniform(0.3, 1.0)
    alb += specks * (0.6 + 0.4 * (1 - mare))
    # ray systems along great circles
    for lat0, lon0, ckm, lkm, nr, st in RAYS:
        la0, lo0 = math.radians(lat0), math.radians(lon0)
        d = gdist(lat0, lon0)
        bearing = np.arctan2(np.sin(lon - lo0) * clat, math.cos(la0) * slat - math.sin(la0) * clat * np.cos(lon - lo0))
        a = ckm / R_MOON_KM
        L = lkm / R_MOON_KM
        ray = np.zeros_like(rr)
        for _ in range(nr):
            b0 = r.uniform(-math.pi, math.pi)
            w = r.uniform(0.02, 0.07)
            ln = L * r.uniform(0.35, 1.0)
            da = np.angle(np.exp(1j * (bearing - b0)))
            ray += np.exp(-(da / w) ** 2) * np.exp(-d / ln) * (0.5 + 0.8 * t2)
        ray *= smooth(a * 0.8, a * 2.0, d)
        nimb = np.exp(-((d - a * 1.4) / (a * 1.2)) ** 2) * 0.6
        core = np.exp(-(d / (a * 0.6)) ** 2) * 0.8
        alb += st * (0.28 * ray + nimb + core)
    shade = 0.78 + 0.22 * zz**0.35
    radiance = alb * shade
    col = np.stack([radiance * (1.0 - 0.04 * mare), radiance, radiance * (1.0 + 0.05 * mare)], -1)
    a = np.clip((1 - rr) * R / 1.5, 0, 1)
    rgba = np.concatenate([col * a[..., None], a[..., None]], 2).astype(np.float32)
    small = cv2.resize(rgba, (n // k, n // k), interpolation=cv2.INTER_AREA)
    return cv2.GaussianBlur(small, (0, 0), 0.7)


# --------------------------------------------------------------------------
# static set
# --------------------------------------------------------------------------


@functools.lru_cache(maxsize=1)
def static_set():
    """Linear radiance of the set (no moon), its alpha, a specular map."""
    w, h = W * SS, H * SS
    ys = (np.arange(h, dtype=np.float32) / SS)[:, None]
    xs = (np.arange(w, dtype=np.float32) / SS)[None, :]
    ey = edge_y(xs)
    ty = roof_top_y(xs)
    fh = fascia_h(xs)

    def band(a, b):
        return clamp01((ys - a) * SS + 0.5) * clamp01((b - ys) * SS + 0.5)

    roof = band(ty, ey)
    fasc = band(ey, ey + fh)
    below = clamp01((ys - (ey + fh)) * SS + 0.5)
    sky_a = clamp01(1 - roof - fasc - below)

    yn = ys / H
    sky = SKY_TOP * (1 - yn[..., None] ** 0.8) + SKY_LOW * yn[..., None] ** 0.8
    sky = np.broadcast_to(sky, (h, w, 3)).copy()

    # ---- roof: asphalt shingles, wet. q = 0 at the edge, 1 at the far side
    q = clamp01((ey - ys) / (ey - ty))
    rows = 9
    rq = (1 - (1 - q) ** 1.35) * rows
    ri = np.floor(rq)
    rf = rq - ri
    tabw = 92.0 * (1 - 0.35 * q)
    tpos = (xs / tabw + 0.5 * (ri % 2)) % 1.0
    slot = np.exp(-((tpos - 0.5) / 0.012) ** 2) * smooth(0.25, 0.9, rf)
    course_shadow = smooth(0.0, 0.18, rf)
    lip = np.exp(-((rf - 0.97) / 0.03) ** 2)
    gran = cv2.resize(fbm_tex(1024, 1024, 3101, base=180, octaves=2), (w, h), interpolation=cv2.INTER_LINEAR)
    gran2 = cv2.resize(fbm_tex(1024, 512, 3102, base=8, octaves=5), (w, h), interpolation=cv2.INTER_LINEAR)
    tone = 0.85 + 0.3 * ((np.sin(ri * 12.9898 + np.floor(xs / tabw) * 78.233) * 43758.5453) % 1.0)
    shingle = lin("#3a3a3e") * (tone * (0.55 + 0.7 * gran) * (0.8 + 0.4 * gran2))[..., None]
    shingle *= (0.35 + 0.65 * course_shadow)[..., None]
    shingle *= (1 - 0.8 * slot)[..., None]
    shingle *= 0.3
    shingle *= (1 - 0.8 * q**1.4)[..., None]
    glare_x = np.exp(-((xs - (MX - 120)) / 380.0) ** 2) + 0.25 * np.exp(-((xs - (MX - 120)) / 900.0) ** 2)
    sheen = glare_x * (1 - q) ** 1.1 * (0.35 + 0.65 * gran2)
    shingle += (sheen * 0.10 + lip * glare_x * (1 - q) * 0.10)[..., None] * MOON_TINT
    spec = glare_x * (1 - q) ** 0.9 * roof

    # ---- fascia board and the metal drip edge on top of it
    fgr = cv2.resize(fbm_tex(128, 2048, 3103, base=3, octaves=6), (w, h))
    fasc_col = lin("#2a2420") * (0.12 * (0.6 + 0.8 * fgr))[..., None]
    drip_edge = np.exp(-((ys - ey - 2.0) / 2.2) ** 2)
    fasc_col += (drip_edge * (0.02 + 0.55 * glare_x))[..., None] * MOON_TINT
    fasc_col += (np.exp(-((ys - (ey + fh)) / 1.4) ** 2) * 0.04 * glare_x)[..., None] * MOON_TINT
    spec = spec + drip_edge * glare_x * fasc

    # ---- fence: weathered vertical boards in the eave's shadow
    pw = 92.0
    off = 31.0
    pidx = np.floor((xs + off) / pw)
    pos = (xs + off) / pw - pidx
    gap = smooth(0.0, 0.03, pos) * (1 - smooth(0.94, 0.975, pos))
    ptone = 0.75 + 0.45 * ((np.sin(pidx * 12.9898 + 4.1) * 43758.5453) % 1.0)
    warp = cv2.resize(fbm_tex(512, 256, 3104, base=4, octaves=5), (w, h))
    fib_tex = cv2.resize(fbm_tex(4096, 128, 3105, base=60, octaves=4), (w, h))
    fib = 0.5 + 0.5 * np.sin((pos * 38 + warp * 9 + pidx * 3.1) * math.pi)
    grain = 0.72 + 0.18 * fib + 0.25 * (fib_tex - 0.5)
    knots = np.zeros((h, w), np.float32)
    rk = np.random.default_rng(3106)
    for _ in range(26):
        kx, ky = rk.uniform(0, W), rk.uniform(H * 0.45, H)
        kr = rk.uniform(5, 11)
        x0, x1 = int(max(0, (kx - kr * 4) * SS)), int(min(w, (kx + kr * 4) * SS))
        y0, y1 = int(max(0, (ky - kr * 6) * SS)), int(min(h, (ky + kr * 6) * SS))
        dd = np.sqrt(((xs[:, x0:x1] - kx) / kr) ** 2 + ((ys[y0:y1] - ky) / (kr * 1.6)) ** 2)
        knots[y0:y1, x0:x1] += np.exp(-(dd**2)) * 0.9 + np.exp(-(((dd - 1.6) / 0.5) ** 2)) * 0.3
    wood = lin("#7a6450") * (ptone * grain * (1 - 0.55 * np.clip(knots, 0, 1)))[..., None]
    wood *= (0.7 + 0.3 * np.sin(np.clip(pos, 0, 1) * math.pi) ** 0.5)[..., None]
    wood *= (gap * 0.97 + 0.03)[..., None]
    under = ys - (ey + fh)
    shade = smooth(10, 560, under) ** 2.0
    warm = np.exp(-(((xs - 180) / 900.0) ** 2 + ((ys - H * 1.05) / 520.0) ** 2))
    light = 0.004 + 0.11 * shade * (0.25 + warm) + 0.008 * shade
    wood *= light[..., None]
    streak = smooth(0.5, 0.85, cv2.resize(fbm_tex(1024, 48, 3107, base=50, octaves=3), (w, h)))
    wood *= (1 - 0.35 * streak)[..., None]
    wood += (streak * shade * 0.012 * (0.3 + gap))[..., None] * MOON_TINT
    wood += (np.exp(-(((pos - 0.06) / 0.02) ** 2)) * shade * 0.006)[..., None] * MOON_TINT

    img = sky * sky_a[..., None] + shingle * roof[..., None] + fasc_col * fasc[..., None] + wood * below[..., None]
    alpha = 1 - sky_a
    dof = np.maximum(q * roof, smooth(30, 500, under) * below)

    def down(a):
        return cv2.resize(np.ascontiguousarray(a, dtype=np.float32), (W, H), interpolation=cv2.INTER_AREA)

    img, alpha, spec, dof = down(img), down(alpha), down(spec), down(dof)
    b1 = cv2.GaussianBlur(img, (0, 0), 1.2)
    b2 = cv2.GaussianBlur(img, (0, 0), 3.0)
    dd = dof[..., None]
    img = img * (1 - np.clip(dd * 2, 0, 1)) + b1 * np.clip(dd * 2, 0, 1)
    img = img * (1 - np.clip(dd * 2 - 1, 0, 1)) + b2 * np.clip(dd * 2 - 1, 0, 1)
    return img.astype(np.float32), alpha.astype(np.float32), spec.astype(np.float32)


# --------------------------------------------------------------------------
# moving parts
# --------------------------------------------------------------------------


@functools.lru_cache(maxsize=1)
def wisp_tex():
    return fbm_tex(2048, 512, 3201, base=3, octaves=6, gain=0.55)


def wisps(T: float, veil: float):
    A = wisp_tex()
    hh = 640
    ys, xs = np.mgrid[0 : hh // 2, 0 : W // 2].astype(np.float32)
    u = (xs * 0.5 - T * 2.8) % A.shape[1]
    v = (ys * 1.5 + 120 + 24 * np.sin(xs / 280.0 + T * 0.02)) % A.shape[0]
    D = cv2.remap(A, u, v, cv2.INTER_LINEAR, borderMode=cv2.BORDER_WRAP)
    D = cv2.resize(D, (W, hh), interpolation=cv2.INTER_CUBIC)
    return smooth(0.45, 0.9, D) * veil, hh


@functools.lru_cache(maxsize=4)
def rain_tile(seed, n, length, thick, blur, slant):
    r = np.random.default_rng(seed)
    tile = np.zeros((H, W), np.float32)
    for _ in range(n):
        x = r.random() * W
        y = r.random() * H
        L = length * r.uniform(0.55, 1.3)
        v = r.uniform(0.25, 1.0)
        x2, y2 = x + L * slant, y + L
        th = max(1, int(round(thick * r.uniform(0.7, 1.3))))
        xm, ym = x + (x2 - x) * 0.4, y + (y2 - y) * 0.4
        for oy in (0, -H, H):
            for ox in (0, -W, W):
                cv2.line(tile, (int((x + ox) * 16), int((y + oy) * 16)), (int((xm + ox) * 16), int((ym + oy) * 16)), v * 0.55, th, cv2.LINE_AA, shift=4)
                cv2.line(tile, (int((xm + ox) * 16), int((ym + oy) * 16)), (int((x2 + ox) * 16), int((y2 + oy) * 16)), v, th, cv2.LINE_AA, shift=4)
    if blur:
        tile = cv2.GaussianBlur(tile, (0, 0), blur)
    return tile


RAIN_LAYERS = [
    (3301, 6000, 22, 1, 0.0, 0.03, 800.0, 0.22),
    (3302, 2200, 80, 1, 0.5, 0.035, 1450.0, 0.5),
    (3303, 460, 220, 2, 1.3, 0.04, 2300.0, 0.55),
    (3304, 55, 460, 5, 5.0, 0.045, 3500.0, 0.6),
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
    r = np.random.default_rng(3401)
    pts = []
    x = 6.0
    while x < W - 10:
        pts.append(dict(x=x, period=r.uniform(0.16, 0.5), phase=r.uniform(0, 5), thread=r.random() < 0.5,
                        strength=r.uniform(0.35, 1.0), sway=r.uniform(-0.02, 0.02), stream=r.random() < 0.22))
        x += r.uniform(10, 30)
    return pts


def drips(T: float, amount: float) -> np.ndarray:
    layer = np.zeros((H, W), np.float32)
    g = 2600.0
    exp = 1 / 30.0
    for p in drip_points():
        x0 = p["x"]
        y0 = edge_y(x0) + fascia_h(x0) - 1
        st = p["strength"] * min(1.0, amount)
        ph = ((T + p["phase"]) / p["period"]) % 1.0
        br = 0.8 + 1.8 * ph
        cv2.circle(layer, (int(x0 * 16), int((y0 + br) * 16)), int(br * 16), 0.9 * st, -1, cv2.LINE_AA, shift=4)
        if p["thread"]:
            ph3 = ((T + p["phase"]) / (p["period"] * 3)) % 1.0
            L = 6 + 44 * ph3**1.6
            cv2.line(layer, (int(x0 * 16), int(y0 * 16)), (int((x0 + p["sway"] * L) * 16), int((y0 + L) * 16)), 0.5 * st, 1, cv2.LINE_AA, shift=4)
            cv2.circle(layer, (int((x0 + p["sway"] * L) * 16), int((y0 + L) * 16)), 22, 0.9 * st, -1, cv2.LINE_AA, shift=4)
        if p["stream"]:
            Ls = 160 + 200 * p["strength"]
            prev = None
            for k in range(28):
                u = k / 27
                yy = y0 + u * Ls
                xx = x0 + p["sway"] * u * Ls + 1.0 * math.sin(T * 9 + k * 0.7 + p["phase"])
                if prev is not None and not (u > 0.4 and math.sin(T * 31 + k * 2.1 + p["phase"] * 5) > 0.1):
                    cv2.line(layer, (int(prev[0] * 16), int(prev[1] * 16)), (int(xx * 16), int(yy * 16)), st * 0.75 * (1 - u) ** 1.1, 2, cv2.LINE_AA, shift=4)
                prev = (xx, yy)
        fall = H - y0 + 40
        tfall = math.sqrt(2 * fall / g)
        q = (T + p["phase"]) / p["period"]
        base = math.floor(q)
        for k in range(int(tfall / p["period"]) + 2):
            age = (q - (base - k)) * p["period"]
            if age < 0 or age > tfall:
                continue
            y = y0 + 0.5 * g * age * age
            L = max(4.0, g * age * exp)
            x = x0 + p["sway"] * (y - y0)
            fade = math.exp(-(y - y0) / 450.0)
            val = st * (0.3 + 0.7 * fade)
            cv2.line(layer, (int(x * 16), int((y - L) * 16)), (int(x * 16), int((y - L * 0.3) * 16)), val * 0.4, 1, cv2.LINE_AA, shift=4)
            cv2.line(layer, (int(x * 16), int((y - L * 0.3) * 16)), (int(x * 16), int(y * 16)), val, 2 if fade > 0.45 else 1, cv2.LINE_AA, shift=4)
    return cv2.GaussianBlur(layer, (0, 0), 0.55)


@functools.lru_cache(maxsize=1)
def glints():
    r = np.random.default_rng(3501)
    n = 6000
    xs = r.uniform(0, W, n)
    q = r.random(n) ** 1.8
    ey = edge_y(xs)
    ty = roof_top_y(xs)
    ys = ey - q * (ey - ty)
    m = 900
    lx = r.uniform(0, W, m)
    ly = edge_y(lx) + r.uniform(0.5, 3.5, m)
    xs = np.concatenate([xs, lx])
    ys = np.concatenate([ys, ly])
    mag = np.concatenate([r.random(n) ** 4, r.random(m) ** 2 * 1.5])
    return xs.astype(np.float32), ys.astype(np.float32), mag.astype(np.float32), r.uniform(0, 6.28, n + m).astype(np.float32), r.uniform(0.5, 4.0, n + m).astype(np.float32)


def glint_layer(T: float, spec: np.ndarray, amount: float) -> np.ndarray:
    xs, ys, mag, ph, fr = glints()
    xi = np.clip(xs.astype(int), 0, W - 1)
    yi = np.clip(ys.astype(int), 0, H - 1)
    tw = np.clip(np.sin(T * fr + ph), 0, 1) ** 6
    v = mag * (spec[yi, xi] ** 1.5 + 0.01) * (0.15 + 0.85 * tw)
    lay = np.zeros((H, W), np.float32)
    np.add.at(lay, (yi, xi), v)
    rr = np.random.default_rng(int(T * 24) + 77)
    k = int(90 * amount)
    sx = rr.uniform(0, W, k)
    sq = rr.random(k) ** 1.5
    sy = edge_y(sx) - sq * (edge_y(sx) - roof_top_y(sx))
    iy, ix = np.clip(sy.astype(int), 0, H - 1), np.clip(sx.astype(int), 0, W - 1)
    np.add.at(lay, (iy, ix), rr.uniform(0.2, 1.0, k) * (spec[iy, ix] + 0.05))
    return cv2.GaussianBlur(lay, (0, 0), 0.7) * 9.0


@functools.lru_cache(maxsize=1)
def spray_tex():
    return fbm_tex(1024, 512, 3601, base=4, octaves=4)


@functools.lru_cache(maxsize=1)
def below_edge():
    return (np.exp(-np.clip(YS - edge_y(XS) - 30, 0, None) / 180.0) * (YS > edge_y(XS))).astype(np.float32)


# --------------------------------------------------------------------------
# the frame
# --------------------------------------------------------------------------

MOON_L = 0.30
EXPOSURE = 3.2


def frame_linear(T: float) -> np.ndarray:
    c = cue(T)
    base, alpha, spec = static_set()
    sky_a = (1 - alpha)[..., None]

    moon = np.zeros((H, W, 3), np.float32)
    sp = moon_sprite()
    n = sp.shape[0]
    x0, y0 = int(round(MX - n / 2)), int(round(MY - n / 2))
    moon[y0 : y0 + n, x0 : x0 + n] = sp[..., :3] * MOON_L
    moon_a = np.zeros((H, W), np.float32)
    moon_a[y0 : y0 + n, x0 : x0 + n] = sp[..., 3]
    moon *= MOON_TINT

    dens, hh = wisps(T, c["veil"])
    trans = np.ones((H, W), np.float32)
    trans[:hh] = np.exp(-dens * 2.2)
    img = base * (1 - moon_a[..., None] * sky_a) + moon * trans[..., None] * sky_a
    d = np.sqrt((XS - MX) ** 2 + (YS[:hh] - MY) ** 2)
    scat = (np.exp(-d / 150.0) * 0.5 + np.exp(-d / 480.0) * 0.08 + 0.004) * MOON_L
    img[:hh] += (dens * (1 - np.exp(-dens * 2.2)) * scat)[..., None] * MOON_TINT * sky_a[:hh]

    # light scattered by the rain-filled air around the moon
    src = moon * trans[..., None]
    small = cv2.resize(src, (W // 4, H // 4), interpolation=cv2.INTER_AREA)
    halo = cv2.GaussianBlur(small, (0, 0), 8) * 0.35 + cv2.GaussianBlur(small, (0, 0), 35) * 0.28 + cv2.GaussianBlur(small, (0, 0), 120) * 0.3
    halo = cv2.resize(halo, (W, H), interpolation=cv2.INTER_LINEAR)
    img += halo * (sky_a * 0.85 + 0.15) * (0.6 + 0.4 * c["rain"])

    img += glint_layer(T, spec, c["rain"])[..., None] * MOON_TINT * 0.25

    bg = cv2.resize(img, (W // 8, H // 8), interpolation=cv2.INTER_AREA)
    bg = cv2.GaussianBlur(bg, (0, 0), 5)
    bgl = cv2.resize(bg.mean(2), (W, H), interpolation=cv2.INTER_LINEAR)
    lightL = np.minimum(bgl, 0.35)
    dr = drips(T, c["rain"])
    img += (dr * (0.03 + 0.75 * lightL + 0.02 * spec))[..., None] * RAIN_TINT
    rn = rain(T, c["rain"])
    img += (rn * (0.0025 + 0.55 * lightL))[..., None] * RAIN_TINT
    st = spray_tex()
    m = cv2.remap(st, ((XS * 0.6 - T * 20) % 1024 + 0 * YS).astype(np.float32), ((YS * 0.6 - T * 40) % 512 + 0 * XS).astype(np.float32), cv2.INTER_LINEAR, borderMode=cv2.BORDER_WRAP)
    img += (below_edge() * smooth(0.4, 0.9, m) * (0.004 + 0.05 * lightL) * c["rain"])[..., None] * RAIN_TINT
    return img * c["fade"]


@functools.lru_cache(maxsize=1)
def _lens():
    x = (XS - W / 2) / (W / 2)
    y = (YS - H / 2) / (H / 2)
    vig = (1 - 0.35 * smooth(0.4, 1.5, np.sqrt(x * x * 0.8 + y * y)))[..., None].astype(np.float32)
    s = 0.9
    mx = np.ascontiguousarray((XS + x * s) + 0 * YS, dtype=np.float32)
    my = np.ascontiguousarray((YS + y * s * H / W) + 0 * XS, dtype=np.float32)
    mx2 = np.ascontiguousarray((XS - x * s * 0.6) + 0 * YS, dtype=np.float32)
    my2 = np.ascontiguousarray((YS - y * s * 0.6 * H / W) + 0 * XS, dtype=np.float32)
    return vig, mx, my, mx2, my2


def develop(img: np.ndarray, f: int) -> np.ndarray:
    """Lens glare, vignette, filmic tone curve, CA, grain -> uint8 sRGB."""
    small = cv2.resize(img, (W // 4, H // 4), interpolation=cv2.INTER_AREA)
    glare = cv2.GaussianBlur(small, (0, 0), 2.5) * 0.04 + cv2.GaussianBlur(small, (0, 0), 12) * 0.035 + cv2.GaussianBlur(small, (0, 0), 50) * 0.03
    img = img + cv2.resize(glare, (W, H), interpolation=cv2.INTER_LINEAR)
    vig, mx, my, mx2, my2 = _lens()
    out = aces(img * vig * EXPOSURE).astype(np.float32)
    r_ = cv2.remap(np.ascontiguousarray(out[..., 0]), mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
    b_ = cv2.remap(np.ascontiguousarray(out[..., 2]), mx2, my2, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
    out = np.stack([r_, out[..., 1], b_], -1)
    s_ = to_srgb(out)
    r = np.random.default_rng(f * 7 + 3)
    g = cv2.GaussianBlur(r.normal(0, 1, (H, W)).astype(np.float32), (0, 0), 0.6)
    s_ = s_ + g[..., None] * 0.012 * (1 - s_ * 0.7)
    return (np.clip(s_, 0, 1) * 255 + 0.5).astype(np.uint8)


def frame(T: float, f: int | None = None) -> np.ndarray:
    return develop(frame_linear(T), int(round(T * 24)) if f is None else f)

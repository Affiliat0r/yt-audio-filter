"""The recurring world of the film: sky, land, village, lamp, clouds, rain,
water and the five stones. Shots compose these with their own cameras."""

from __future__ import annotations

import functools
import math

import cv2
import numpy as np

from .paint import (
    H,
    W,
    XS,
    YS,
    Noise1D,
    blend,
    cairo_canvas,
    cairo_rgba,
    clamp01,
    disk,
    fbm_tex,
    glow,
    hexc,
    lerp,
    mix,
    over,
    rgb,
    ridge,
    smooth,
    tex_sample,
    vgradient,
)

# --------------------------------------------------------------------------
# palette: time of day
# --------------------------------------------------------------------------
# tod: 0 = deep night, 1 = pre-dawn grey, 2 = dawn, 3 = morning, 4 = noon,
#      5 = late afternoon, 6 = dusk, 7 = night again. Fractions interpolate.

SKY_KEYS = {
    #      zenith      upper       horizon     ground-light  ambient (land tint)
    0: ("#04060f", "#0a1024", "#151a33", "#0b0d18", "#1a2140"),
    1: ("#0b1020", "#1d2438", "#4a4a5c", "#1c1c26", "#3a3d55"),
    2: ("#1d2a52", "#5a5f88", "#e8a07a", "#3a2a2a", "#8a6f7a"),
    3: ("#3d6aa8", "#7fa6cf", "#e9d6b4", "#6a5a48", "#d8c8b0"),
    4: ("#2f6db5", "#6aa3da", "#cfe3ef", "#8c7a5e", "#ffffff"),
    5: ("#3a62a0", "#8aa6c8", "#f1c88e", "#7a5f45", "#f0d2a8"),
    6: ("#1a2048", "#5a3f6a", "#e0764a", "#3a2422", "#b07a6a"),
    7: ("#04060f", "#0a1024", "#151a33", "#0b0d18", "#1a2140"),
}


def sky_colors(tod: float):
    tod = tod % 7.0 if tod >= 7.0 else max(tod, 0.0)
    i = int(math.floor(tod))
    f = tod - i
    f = f * f * (3 - 2 * f)
    a = SKY_KEYS[i]
    b = SKY_KEYS[min(i + 1, 7)]
    return [mix(hexc(a[k]), hexc(b[k]), f) for k in range(5)]


def star_alpha(tod: float) -> float:
    # stars at night, gone by dawn
    if tod <= 1.0:
        return 1.0 - 0.6 * tod
    if tod <= 2.0:
        return 0.4 * (2.0 - tod)
    if tod >= 6.0:
        return smooth(6.0, 6.8, tod) * 1.0
    return 0.0


# --------------------------------------------------------------------------
# sky
# --------------------------------------------------------------------------


def sky(tod: float, horizon: float = 0.62, extra=None):
    zen, up, hor, _, _ = sky_colors(tod)
    stops = [(0.0, zen), (horizon * 0.55, up), (horizon, hor), (1.0, hor)]
    img = vgradient(stops)
    if extra is not None:
        img += extra
    return img


@functools.lru_cache(maxsize=4)
def star_field(seed=3, n=2600, w=W * 2, h=H):
    r = np.random.default_rng(seed)
    xs = r.random(n) * w
    ys = (r.random(n) ** 1.3) * h
    mags = r.random(n) ** 6
    phase = r.random(n) * 6.28
    return xs.astype(np.float32), ys.astype(np.float32), mags.astype(np.float32), phase.astype(np.float32)


def stars(img, alpha: float, t: float, horizon_px: float, pan: float = 0.0, rot: float = 0.0, seed=3, scale=1.0):
    if alpha <= 0.01:
        return img
    xs, ys, mags, ph = star_field(seed)
    cx, cy = W * 0.5, horizon_px * 1.4
    if rot:
        c, s = math.cos(rot), math.sin(rot)
        x0 = xs - W - pan
        y0 = ys - cy
        x = cx + x0 * c - y0 * s
        y = cy + x0 * s + y0 * c
    else:
        x = (xs - pan) % (W * 2) - W * 0.5
        y = ys
    keep = (x > 0) & (x < W - 1) & (y > 0) & (y < horizon_px - 4)
    x, y, m, p = x[keep], y[keep], mags[keep], ph[keep]
    tw = 0.75 + 0.25 * np.sin(t * 2.3 + p * 3.0)
    fade = np.clip((horizon_px - y) / (horizon_px * 0.35), 0, 1)
    v = (0.25 + 0.75 * m) * tw * fade * alpha * scale
    xi = x.astype(np.int32)
    yi = y.astype(np.int32)
    layer = np.zeros((H, W), np.float32)
    np.add.at(layer, (yi, xi), v)
    layer = cv2.GaussianBlur(layer, (0, 0), 0.7) * 2.6
    big = m > 0.55
    for xx, yy, vv in zip(x[big], y[big], v[big]):
        glow(img, float(xx), float(yy), 3.0, (0.75, 0.8, 1.0), float(vv) * 0.35, reach=3)
    img += layer[..., None] * np.array([0.9, 0.93, 1.0], np.float32)
    return img


def clouds(img, t, tod, top=0.0, bottom=0.55, density=0.5, speed=8.0, seed=11, color=None, dark=0.0, scale=1.0, alpha=1.0):
    """Soft painterly cloud band. density 0..1 grows coverage; dark 0..1
    makes them storm-heavy."""
    tex = fbm_tex(1024, 512, seed, base=3, octaves=4, gain=0.45)
    y0, y1 = int(H * top), int(H * bottom)
    if y1 <= y0:
        return img
    f = tex_sample(tex, t * speed, 40, 0.55 / scale, W, y1 - y0)
    thr = 1.0 - density
    a = smooth(thr, thr + 0.28, f)
    # fade at band edges
    yy = np.linspace(0, 1, y1 - y0, dtype=np.float32)[:, None]
    a *= smooth(0.0, 0.25, yy) * (1 - smooth(0.7, 1.0, yy))
    a *= alpha
    zen, up, hor, _, amb = sky_colors(tod)
    base = mix(up, hor, 0.5) * 0.9 + 0.08 if color is None else np.asarray(color, np.float32)
    lit = mix(base, hor * 1.1 + 0.05, 0.4)
    shade = mix(base, zen, 0.6)
    if dark:
        shade = mix(shade, np.array([0.05, 0.05, 0.08], np.float32), dark)
        lit = mix(lit, np.array([0.25, 0.26, 0.3], np.float32), dark)
    # shading from a second offset sample (light from below/horizon)
    f2 = tex_sample(tex, t * speed + 6, 40 - 10, 0.55 / scale, W, y1 - y0)
    s = clamp01((f - f2) * 4 + 0.5)[..., None]
    col = shade * (1 - s) + lit * s
    sub = img[y0:y1]
    sub += (col - sub) * a[..., None]
    return img


# --------------------------------------------------------------------------
# land
# --------------------------------------------------------------------------

_N = {k: Noise1D(k) for k in range(1, 12)}


def land_layers(img, tod, horizon_px, cam_x=0.0, zoom=1.0, layers=None, haze=None):
    """Draw distant mountains to near plain as stacked ridges.
    layers: list of (depth 0..1 (0 far), base_height_px_above_horizon, amp_px, freq, seed, color_dark)"""
    zen, up, hor, gnd, amb = sky_colors(tod)
    if layers is None:
        layers = DEFAULT_LAYERS
    for depth, base, amp, freq, seed, col in layers:
        par = 0.1 + depth * 0.9
        xw = (XS[0] - W / 2) / (W * zoom) + cam_x * par
        hgt = base * zoom ** (0.3 * depth) + amp * _N[seed](xw * freq + seed * 10.0, octaves=5)
        hts = horizon_px - hgt * (0.7 + 0.3 * zoom)
        c = hexc(col) if isinstance(col, str) else col
        # atmospheric perspective: far layers lean to horizon colour
        h_amt = (1 - depth) * 0.75 if haze is None else haze * (1 - depth)
        lit = c * (0.35 + 0.65 * amb)
        fill = mix(lit, hor * 0.85, h_amt)
        ridge(img, hts.astype(np.float32), fill, soft=1.3)
    return img


DEFAULT_LAYERS = [
    (0.0, 95, 55, 2.2, 1, "#5a5f78"),
    (0.25, 55, 40, 3.5, 2, "#4a4a5c"),
    (0.5, 18, 22, 5.0, 3, "#4b4436"),
]


def ground(img, tod, top_px, color="#6b5a42", far_haze=0.6, tint=None):
    """Flat plain from top_px to bottom with a perspective gradient."""
    zen, up, hor, gnd, amb = sky_colors(tod)
    c = hexc(color) * (0.25 + 0.75 * amb)
    if tint is not None:
        c = c * tint
    ys = np.linspace(0, 1, H - int(top_px), dtype=np.float32)[:, None, None]
    near = c * 0.75
    far = mix(c, hor * 0.8, far_haze)
    g = far * (1 - ys ** 0.6) + near * ys ** 0.6
    img[int(top_px) :] = g
    return img


# --------------------------------------------------------------------------
# village
# --------------------------------------------------------------------------


@functools.lru_cache(maxsize=8)
def village_layout(seed=5, n=34, width=1500.0):
    """Flat-roofed mud-brick houses: list of dicts in village-local px
    (x across, depth row, w, h, windows)."""
    r = np.random.default_rng(seed)
    houses = []
    for i in range(n):
        row = r.integers(0, 4)
        x = r.random() * width - width / 2
        w = r.uniform(70, 150) * (1 - row * 0.15)
        h = r.uniform(50, 95) * (1 - row * 0.15)
        nwin = int(r.integers(1, 3))
        wins = []
        for k in range(nwin):
            wx = r.uniform(0.15, 0.8)
            wy = r.uniform(0.25, 0.5)
            wins.append((wx, wy))
        door = r.random() < 0.6
        houses.append(dict(x=x, row=int(row), w=w, h=h, wins=wins, door=door, dx=r.uniform(0.2, 0.7), stair=r.random() < 0.3, lit_order=r.random()))
    houses.sort(key=lambda d: -d["row"])  # far rows first
    return houses


def draw_village(img, tod, cx, base_y, scale=1.0, lit=None, seed=5, n=34, width=1500.0, light_dir=1.0, warm_light=0.0):
    """Draw the village centered at (cx, base_y). lit: callable(house)->0..1
    window light."""
    zen, up, hor, gnd, amb = sky_colors(tod)
    houses = village_layout(seed, n, width)
    mud = hexc("#8a6a4a")
    wall = mud * (0.22 + 0.78 * amb)
    side = wall * 0.62
    roof = wall * 0.8
    warm = hexc("#ffb85a")
    surf, ctx = cairo_canvas(W, H)
    windows = []
    for hs in houses:
        rowk = 1 - hs["row"] * 0.12
        s = scale * rowk
        x = cx + hs["x"] * scale * (1 - hs["row"] * 0.08)
        y = base_y - hs["row"] * 22 * scale
        w, h = hs["w"] * s, hs["h"] * s
        dep = w * 0.25
        # haze far rows
        hz = hs["row"] * 0.08
        fw = mix(wall, hor * 0.7, hz)
        fs = mix(side, hor * 0.6, hz)
        fr = mix(roof, hor * 0.75, hz)
        # side face (depth to the right)
        rgb(ctx, fs)
        ctx.move_to(x + w, y)
        ctx.line_to(x + w + dep, y - dep * 0.45)
        ctx.line_to(x + w + dep, y - h - dep * 0.45)
        ctx.line_to(x + w, y - h)
        ctx.close_path()
        ctx.fill()
        # roof
        rgb(ctx, fr)
        ctx.move_to(x, y - h)
        ctx.line_to(x + dep, y - h - dep * 0.45)
        ctx.line_to(x + w + dep, y - h - dep * 0.45)
        ctx.line_to(x + w, y - h)
        ctx.close_path()
        ctx.fill()
        # parapet lip
        rgb(ctx, fw * 1.05)
        ctx.rectangle(x - 1, y - h - 3 * s, w + 2, 4 * s)
        ctx.fill()
        # front
        rgb(ctx, fw)
        ctx.rectangle(x, y - h, w, h)
        ctx.fill()
        # door
        if hs["door"]:
            dw, dh = 16 * s, 30 * s
            dx = x + hs["dx"] * (w - dw)
            rgb(ctx, fw * 0.35)
            ctx.move_to(dx, y)
            ctx.line_to(dx, y - dh + dw / 2)
            ctx.arc(dx + dw / 2, y - dh + dw / 2, dw / 2, math.pi, 0)
            ctx.line_to(dx + dw, y)
            ctx.close_path()
            ctx.fill()
        for (wx, wy) in hs["wins"]:
            ww, wh = 10 * s, 13 * s
            px, py = x + wx * (w - ww), y - h + wy * h
            rgb(ctx, fw * 0.3)
            ctx.rectangle(px, py, ww, wh)
            ctx.fill()
            windows.append((px, py, ww, wh, hs))
    layer = cairo_rgba(surf)
    # light: ambient warm rim from the lamp side
    if warm_light > 0:
        layer[..., :3] *= 1 + warm_light * np.array([0.5, 0.25, 0.0], np.float32)
    over(img, layer)
    if lit is not None:
        for px, py, ww, wh, hs in windows:
            v = float(lit(hs))
            if v <= 0.01:
                continue
            x0, y0 = int(px), int(py)
            img[y0 : y0 + max(1, int(wh)), x0 : x0 + max(1, int(ww))] = (
                img[y0 : y0 + max(1, int(wh)), x0 : x0 + max(1, int(ww))] * (1 - v) + warm * v * 1.1
            )
            glow(img, px + ww / 2, py + wh / 2, max(ww, 6) * 1.6, warm, 0.22 * v)
    return img


# --------------------------------------------------------------------------
# the lamp on the hill
# --------------------------------------------------------------------------


def lamp(img, x, y, intensity, t, scale=1.0, halo=1.0, stone_color=None):
    """A clay oil lamp on a stone. (x,y) = flame base."""
    s = scale
    sc = hexc("#2a2420") if stone_color is None else stone_color
    surf, ctx = cairo_canvas(W, H)
    # stone plinth
    rgb(ctx, sc)
    ctx.move_to(x - 26 * s, y + 30 * s)
    ctx.curve_to(x - 24 * s, y + 12 * s, x - 14 * s, y + 9 * s, x, y + 9 * s)
    ctx.curve_to(x + 14 * s, y + 9 * s, x + 24 * s, y + 12 * s, x + 27 * s, y + 30 * s)
    ctx.close_path()
    ctx.fill()
    # lamp body (low clay dish with spout)
    rgb(ctx, sc * 1.6 + 0.04)
    ctx.move_to(x - 13 * s, y + 9 * s)
    ctx.curve_to(x - 12 * s, y + 2 * s, x + 2 * s, y + 1 * s, x + 6 * s, y + 3 * s)
    ctx.line_to(x + 12 * s, y + 1 * s)
    ctx.line_to(x + 11 * s, y + 6 * s)
    ctx.curve_to(x + 6 * s, y + 9 * s, x, y + 10 * s, x - 13 * s, y + 9 * s)
    ctx.close_path()
    ctx.fill()
    over(img, cairo_rgba(surf))
    if intensity <= 0:
        return img
    fl = 1.0 + 0.06 * math.sin(t * 13.0) + 0.04 * math.sin(t * 29.0 + 1.0)
    fx, fy = x + 11 * s, y - 1 * s
    warm = hexc("#ffb050")
    hot = hexc("#fff1c8")
    glow(img, fx, fy - 4 * s, 120 * s * halo, warm, 0.55 * intensity * fl, kind="halo", reach=7)
    glow(img, fx, fy - 4 * s, 28 * s, warm, 0.6 * intensity * fl)
    # flame
    for k, (rr, cc, aa) in enumerate([(7.0, warm, 0.9), (4.2, hot, 1.0)]):
        h = (16 if k == 0 else 10) * s * fl * intensity ** 0.5
        sway = math.sin(t * 7.0) * 1.5 * s
        surf, ctx = cairo_canvas(W, H)
        rgb(ctx, cc, aa * min(1, intensity * 1.5))
        ctx.move_to(fx - rr * s * 0.6, fy)
        ctx.curve_to(fx - rr * s, fy - h * 0.4, fx + sway, fy - h * 0.8, fx + sway * 1.5, fy - h)
        ctx.curve_to(fx + sway, fy - h * 0.8, fx + rr * s, fy - h * 0.4, fx + rr * s * 0.6, fy)
        ctx.close_path()
        ctx.fill()
        over(img, cairo_rgba(surf))
    return img


# --------------------------------------------------------------------------
# rain
# --------------------------------------------------------------------------


@functools.lru_cache(maxsize=4)
def rain_tile(seed=21, n=1600, length=34, slant=0.12, w=W, h=H):
    r = np.random.default_rng(seed)
    tile = np.zeros((h, w), np.float32)
    for _ in range(n):
        x = r.random() * w
        y = r.random() * h
        L = length * r.uniform(0.5, 1.3)
        v = r.uniform(0.25, 1.0)
        x2, y2 = x + L * slant, y + L
        for dy in (0, h):  # wrap vertically
            cv2.line(tile, (int(x * 4), int((y - dy) * 4)), (int(x2 * 4), int((y2 - dy) * 4)), v, 1, cv2.LINE_AA, shift=2)
    return cv2.GaussianBlur(tile, (0, 0), 0.6)


def rain(img, t, amount, speed=1400.0, color=(0.75, 0.8, 0.9), seed=21, slant=0.12, length=34):
    if amount <= 0.01:
        return img
    for k, (sp, sc, a) in enumerate([(1.0, 1.0, 1.0), (0.7, 0.7, 0.55)]):
        tile = rain_tile(seed + k, length=int(length * sc), slant=slant)
        oy = (t * speed * sp) % H
        ox = (t * speed * sp * slant) % W
        f = np.roll(np.roll(tile, int(oy), 0), int(ox), 1)
        img += f[..., None] * (np.asarray(color, np.float32) * 0.28 * amount * a)
    return img


# --------------------------------------------------------------------------
# water
# --------------------------------------------------------------------------


def water(img, tod, level_px, t, sky_img=None, rough=0.2, tint=None, red=0.0, alpha=None, wave_amp=None):
    """Water from level_px down to the bottom. Reflects `sky_img` (the frame
    before water) mirrored at the level line, with ripple distortion."""
    lv = int(max(0, min(H - 1, level_px)))
    hh = H - lv
    if hh <= 1:
        return img
    zen, up, hor, gnd, amb = sky_colors(tod)
    src = img if sky_img is None else sky_img
    ys = np.arange(hh, dtype=np.float32)[:, None]
    xs = np.arange(W, dtype=np.float32)[None, :]
    depth = (ys + 1) / hh
    amp = (2 + 10 * rough) * (0.3 + depth * 2.0) if wave_amp is None else wave_amp * (0.3 + depth * 2.0)
    n1, n2 = persp_noise(hh, t, rough)
    dx = amp * n1 * 1.6
    dy = amp * 0.7 * n2
    mapx = (xs + dx).astype(np.float32)
    mapy = (lv - 1 - ys * 1.0 - dy).astype(np.float32)
    mapy = np.clip(mapy, 0, lv - 1 if lv > 1 else 0)
    refl = cv2.remap(src, np.broadcast_to(mapx, (hh, W)).astype(np.float32), np.broadcast_to(mapy, (hh, W)).astype(np.float32), cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
    deep = mix(zen, hexc("#0b1a26"), 0.5) * (0.4 + 0.6 * amb)
    if tint is not None:
        deep = deep * tint
    fres = 0.75 - 0.45 * depth  # more reflective near the horizon line
    col = refl * fres[..., None] + deep * (1 - fres[..., None])
    # wave crests: bright where the surface tilts toward the sky
    crest = clamp01(n2 * 1.4 + 0.5)
    col += (crest ** 6)[..., None] * (hor * 0.22 * (0.3 + rough))
    if red > 0:
        fire = hexc("#d0401a")
        glowband = np.exp(-depth * 2.5)[..., None]
        col = col * (1 - 0.3 * red) + fire * red * (0.12 + 0.35 * glowband) * (0.5 + 0.8 * crest[..., None])
    if alpha is None:
        img[lv:] = col
    else:
        img[lv:] = img[lv:] * (1 - alpha) + col * alpha
    # bright line at the waterline
    img[lv : lv + 2] = img[lv : lv + 2] * 0.6 + hor * 0.5
    return img


def persp_noise(hh, t, rough):
    """Two wave-noise fields over the water rows, foreshortened toward the
    horizon and drifting with time. Values ~ -1..1."""
    tex = fbm_tex(1024, 1024, 171, base=6, octaves=5)
    tex2 = fbm_tex(1024, 1024, 173, base=10, octaves=4)
    ys = np.arange(hh, dtype=np.float32)[:, None]
    depth = (ys + 1) / hh
    z = 1.0 / (0.04 + depth)
    u = (XS - W / 2) / W * (z ** 0.55) * 260 + 512
    v = z * 22 + t * (14 + 30 * rough)
    mu = np.broadcast_to(u, (hh, W)).astype(np.float32)
    mv = np.broadcast_to(v, (hh, W)).astype(np.float32)
    n1 = cv2.remap(tex, mu + t * 9, mv, cv2.INTER_LINEAR, borderMode=cv2.BORDER_WRAP)
    n2 = cv2.remap(tex2, mu * 1.7 - t * 13, mv * 1.7 + 300, cv2.INTER_LINEAR, borderMode=cv2.BORDER_WRAP)
    return (n1 - 0.5) * 2, (n2 - 0.5) * 2


# --------------------------------------------------------------------------
# standing stones
# --------------------------------------------------------------------------


@functools.lru_cache(maxsize=4)
def stone_shapes(seed=13):
    r = np.random.default_rng(seed)
    shapes = []
    for i in range(5):
        hgt = r.uniform(1.6, 2.3)
        wid = r.uniform(0.55, 0.8)
        pts = []
        n = 14
        for k in range(n):
            a = math.pi * k / (n - 1)  # 0..pi : right side up and over to left
            rx = wid * (0.5 + 0.08 * r.standard_normal())
            ry = 1.0
            x = math.cos(a) * rx
            y = -hgt + (1 - math.sin(a)) * 0.0 - math.sin(a) * 0.25 * r.uniform(0.6, 1.2)
            pts.append((x, y))
        shapes.append(dict(h=hgt, w=wid, pts=pts, tilt=r.uniform(-0.06, 0.06), notch=r.uniform(0.2, 0.8)))
    return shapes


def stone_positions(n=5, rx=1.0, rz=0.45):
    out = []
    for i in range(n):
        a = -math.pi / 2 + 2 * math.pi * i / n + 0.3
        out.append((math.cos(a) * rx, math.sin(a) * rz))
    return out


def draw_stones(img, tod, cx, base_y, scale, lights=None, sink=0.0, color="#5c5650", rim=None, rim_side=1.0):
    """Five uncarved standing stones in a ring. lights: list of 5 flash
    amounts. sink: 0..1 water level in stone heights (for drawing only the
    part above water we leave it to the water pass)."""
    zen, up, hor, gnd, amb = sky_colors(tod)
    shapes = stone_shapes()
    pos = stone_positions()
    order = sorted(range(5), key=lambda i: pos[i][1])  # far (negative z) first
    surf, ctx = cairo_canvas(W, H)
    base = hexc(color) * (0.2 + 0.8 * amb)
    for i in order:
        px, pz = pos[i]
        persp = 1.0 / (1.0 - pz * 0.35)
        sx = cx + px * scale * 2.2 * persp
        sy = base_y + pz * scale * 0.9
        s = scale * persp
        sh = shapes[i]
        L = 0.0 if lights is None else float(lights[i])
        col = base * (1 + 3.0 * L) + np.array([0.5, 0.55, 0.7], np.float32) * L * 0.4
        col = mix(col, hor * 0.7, max(0.0, -pz) * 0.25)
        hgt, wid = sh["h"] * s, sh["w"] * s
        ctx.save()
        ctx.translate(sx, sy)
        ctx.rotate(sh["tilt"])
        # body
        rgb(ctx, col)
        ctx.move_to(-wid * 0.55, 0)
        ctx.curve_to(-wid * 0.6, -hgt * 0.5, -wid * 0.5, -hgt * 0.95, -wid * 0.1, -hgt)
        ctx.curve_to(wid * 0.25, -hgt * 1.02, wid * 0.5, -hgt * 0.9, wid * 0.52, -hgt * 0.6)
        ctx.curve_to(wid * 0.56, -hgt * 0.3, wid * 0.6, -hgt * 0.1, wid * 0.58, 0)
        ctx.close_path()
        ctx.fill_preserve()
        # shading: darker side away from light
        lg = __import__("cairo").LinearGradient(-wid * 0.6 * rim_side, 0, wid * 0.6 * rim_side, 0)
        lg.add_color_stop_rgba(0, 0, 0, 0, 0.0)
        lg.add_color_stop_rgba(1, 0, 0, 0, 0.45)
        ctx.set_source(lg)
        ctx.fill()
        if rim is not None:
            rgb(ctx, rim, 0.8)
            ctx.set_line_width(2.0)
            ctx.move_to(-wid * 0.1, -hgt)
            ctx.curve_to(-wid * 0.5, -hgt * 0.95, -wid * 0.6, -hgt * 0.5, -wid * 0.55, 0)
            ctx.stroke()
        ctx.restore()
        # ground shadow
    over(img, cairo_rgba(surf))
    return img

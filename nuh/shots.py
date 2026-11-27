"""The 29 shots. Each is `fn(t, d, ev) -> float32 frame`, where t is the time
inside the shot, d its duration, ev a dict of word times (seconds, shot-local)
for the few shots that act on a word."""

from __future__ import annotations

import functools
import math

import cairo
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
    ease,
    ease_in,
    ease_out,
    fbm_tex,
    glow,
    hexc,
    lerp,
    mix,
    over,
    rgb,
    ridge,
    sm,
    smooth,
    tex_sample,
    vgradient,
)
from .world import (
    clouds,
    draw_stones,
    draw_village,
    ground,
    lamp,
    land_layers,
    rain,
    sky,
    sky_colors,
    star_alpha,
    stars,
    village_layout,
    water,
)

HZ = int(H * 0.64)  # horizon row for the land shots
GOLD = hexc("#ffb85a")


def camera(img, zoom=1.0, fx=W / 2, fy=H / 2, dx=0.0, dy=0.0, rot=0.0):
    if zoom == 1.0 and dx == 0 and dy == 0 and rot == 0:
        return img
    m = cv2.getRotationMatrix2D((fx, fy), math.degrees(rot), zoom)
    m[0, 2] += dx
    m[1, 2] += dy
    return cv2.warpAffine(img, m, (W, H), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)


def ev_at(ev, key, default):
    v = ev.get(key)
    return default if v is None else v


# --------------------------------------------------------------------------
# shared scene pieces
# --------------------------------------------------------------------------


def sun_moon(img, tod, horizon_px, moon=True, sun=True, x_shift=0.0, scale=1.0):
    zen, up, hor, gnd, amb = sky_colors(tod)
    # sun: rises at tod 2 (left), sets at tod 6 (right)
    if sun and 1.7 < tod < 6.3:
        a = (tod - 2.0) / 4.0
        x = W * (0.08 + 0.84 * a) + x_shift
        y = horizon_px - math.sin(math.pi * min(max(a, -0.1), 1.1)) * horizon_px * 0.8 + 20
        warm = mix(hexc("#fff4dc"), hexc("#ff9a50"), 1 - math.sin(math.pi * min(max(a, 0), 1)) ** 0.5)
        glow(img, x, y, 260 * scale, warm, 0.35, kind="halo", reach=6)
        glow(img, x, y, 60 * scale, warm, 0.6)
        disk(img, x, y, 26 * scale, warm * 1.15 + 0.1, 1.0, soft=1.5)
    if moon:
        mt = (tod + 3.5) % 7.0  # moon rises near dusk
        if 1.7 < mt < 6.3:
            a = (mt - 2.0) / 4.0
            x = W * (0.12 + 0.76 * a) + x_shift
            y = horizon_px - math.sin(math.pi * min(max(a, 0), 1)) * horizon_px * 0.72 + 20
            dim = 1.0 - smooth(1.6, 2.6, tod) * (tod < 6)
            if dim > 0.02:
                mc = hexc("#e9ecf5")
                glow(img, x, y, 160 * scale, hexc("#8090c0"), 0.25 * dim, kind="halo", reach=6)
                disk(img, x, y, 20 * scale, mc * dim + img[int(min(max(y, 0), H - 1)), int(min(max(x, 0), W - 1))] * (1 - dim), 1.0)
    return img


def hill_heights(hx, top, width, seed=4, base=None):
    n = Noise1D(seed)
    base = HZ + 220 if base is None else base
    x = XS[0]
    bump = np.exp(-(((x - hx) / width) ** 2))
    return (base - (base - top) * bump + 8 * n(x / 300.0, octaves=4)).astype(np.float32)


def village_world(t, tod, cam_x=0.0, lamp_on=1.0, lit=None, hill=True, hill_x=330, village_x=None, village_scale=1.3,
                  cloud_density=0.3, warm=0.0, star_rot=0.0, lamp_scale=1.0, moon=True, sun=True):
    img = sky(tod, HZ / H)
    stars(img, star_alpha(tod), t, HZ, pan=cam_x * 300, rot=star_rot)
    sun_moon(img, tod, HZ, moon=moon, sun=sun, x_shift=-cam_x * 60)
    clouds(img, t, tod, 0.04, 0.5, density=cloud_density, speed=6)
    land_layers(img, tod, HZ, cam_x=cam_x)
    ground(img, tod, HZ, "#6b5a42")
    vx = W * 0.64 - cam_x * 500 if village_x is None else village_x - cam_x * 500
    draw_village(img, tod, vx, HZ + 95, scale=village_scale, lit=lit, warm_light=warm)
    if hill:
        zen, up, hor, gnd, amb = sky_colors(tod)
        hx = hill_x - cam_x * 900
        hts = hill_heights(hx, HZ - 40, 420)
        hc = hexc("#3a3028") * (0.25 + 0.6 * amb)
        ridge(img, hts, hc, soft=1.5)
        top = float(hts[int(min(max(hx, 0), W - 1))])
        lamp(img, hx, top - 12, lamp_on, t, scale=lamp_scale, stone_color=hc * 0.8)
    return img


def lit_all(v):
    return lambda hs: v


# --------------------------------------------------------------------------
# 1  basmala: black, then the first grey of dawn over a dark plain
# --------------------------------------------------------------------------


def s01(t, d, ev):
    p = t / d
    tod = lerp(0.15, 1.35, ease(p))
    img = sky(tod, HZ / H)
    stars(img, star_alpha(tod), t, HZ)
    clouds(img, t, tod, 0.35, 0.62, density=0.35, speed=4)
    land_layers(img, tod, HZ, cam_x=p * 0.05)
    ground(img, tod, HZ, "#4f4434", far_haze=0.7)
    img = camera(img, 1.0 + 0.04 * p)
    img *= sm(0.0, 0.45, p)
    return img


# --------------------------------------------------------------------------
# 2-5  ayat 1-4: one lamp on a hill above a village of dark rooftops
# --------------------------------------------------------------------------


def s02(t, d, ev):
    p = t / d
    # ayah 1 opens the film: black, the dark plain under the first grey, then the lamp is lit
    tod = lerp(0.05, 0.4, ease(sm(0.0, 0.5, p)))
    ign = sm(0.34, 0.5, p)
    lit = lambda hs: 0.65 * sm(0.5, 0.7, p) if hs["lit_order"] < 0.5 else 0.0
    img = village_world(t, tod, cam_x=0.02 * p, lamp_on=ign, lit=lit)
    img = camera(img, 1.0 + 0.1 * ease(p), fx=330, fy=HZ - 60)
    img *= sm(0.0, 0.22, p)
    return img


def s03(t, d, ev):
    p = t / d
    # closer: lamp large in the foreground, light reaching the rooftops
    lit = lambda hs: 0.6 if hs["lit_order"] < 0.5 else 0.0
    img = village_world(t, 0.4, cam_x=0.10 + 0.06 * p, lamp_on=1.0, lit=lit, hill_x=520, lamp_scale=2.4,
                        warm=0.25 * ease(p), village_scale=1.15)
    return camera(img, 1.06 + 0.06 * p, fx=W * 0.55, fy=HZ)


def s04(t, d, ev):
    p = t / d
    # from the hill looking down: a warm path from the lamp to the village
    img = village_world(t, 0.45, cam_x=-0.05, lamp_on=1.0, lit=lambda hs: 0.55 if hs["lit_order"] < 0.45 else 0.0,
                        hill_x=1500, village_x=W * 0.30 + 0.05 * 500, lamp_scale=1.8, star_rot=0.04 * p)
    # the path: dotted warm light running downhill toward the houses
    path = []
    for k in range(60):
        u = k / 59
        x = lerp(1420, 760, u) + 60 * math.sin(u * 6.0)
        y = lerp(HZ + 40, HZ + 130, u ** 0.8)
        path.append((x, y))
    reach = ease(min(1, p * 1.4))
    for k, (x, y) in enumerate(path):
        u = k / 59
        if u > reach:
            break
        glow(img, x, y, 7 + 4 * (1 - u), GOLD, 0.25 * (1 - 0.5 * u))
    return camera(img, 1.04, fx=W * 0.6, fy=HZ)


def s05(t, d, ev):
    p = t / d
    # time running out: the moon crosses the whole sky and sets, the lamp burns on
    tod_moon = lerp(4.0, 6.6, ease(p))  # drive the moon arc via its own phase
    img = sky(0.3, HZ / H)
    stars(img, 1.0, t, HZ, rot=0.25 * p)
    # moon arc
    a = ease(p)
    mx = W * (0.1 + 0.85 * a)
    my = HZ - math.sin(math.pi * a) * HZ * 0.75 + 30
    glow(img, mx, my, 160, hexc("#8090c0"), 0.28, kind="halo", reach=6)
    disk(img, mx, my, 22, hexc("#e9ecf5"))
    # long moon-shadow sweep over the land: tint the ground
    clouds(img, t, 0.3, 0.05, 0.45, density=0.25, speed=10)
    land_layers(img, 0.3, HZ)
    ground(img, 0.3, HZ, "#6b5a42")
    draw_village(img, 0.3, W * 0.64, HZ + 95, lit=lambda hs: 0.5 * (1 - sm(0.3, 0.9, p)) if hs["lit_order"] < 0.5 else 0.0)
    hts = hill_heights(330, HZ - 40, 420)
    hc = hexc("#3a3028") * 0.35
    ridge(img, hts, hc)
    lamp(img, 330, float(hts[330]) - 12, 1.0, t, stone_color=hc * 0.8)
    # moonlight on the plain grows and slides with the moon
    glow(img, mx, HZ + 200, 500, hexc("#5060a0"), 0.06 * math.sin(math.pi * a), kind="halo")
    return img


# --------------------------------------------------------------------------
# 6-10  ayat 5-9
# --------------------------------------------------------------------------


def s06(t, d, ev):
    p = t / d
    # night and day, faster and faster
    cycles = 3.2
    phase = cycles * (p ** 1.7)
    tod = (0.4 + phase * 7.0) % 7.0
    night = 1 - sm(1.5, 2.6, tod) + sm(5.8, 6.8, tod) if tod > 1.5 else 1.0
    night = min(max(night, 0), 1)
    lit = lambda hs: 0.6 * night if hs["lit_order"] < 0.4 else 0.0
    img = village_world(t, tod, cam_x=0.0, lamp_on=1.0, lit=lit, cloud_density=0.35)
    return img


def s07(t, d, ev):
    p = t / d
    # their windows go dark one by one: they flee from the call
    def lit(hs):
        off = 0.1 + hs["lit_order"] * 0.75
        return 0.75 * (1 - sm(off, off + 0.06, p))
    img = village_world(t, 0.3, cam_x=0.05, lamp_on=1.0, lit=lit, village_scale=1.35, village_x=W * 0.6, hill_x=230)
    return camera(img, 1.05 + 0.04 * p, fx=W * 0.6, fy=HZ + 60)


@functools.lru_cache(maxsize=1)
def _plaster():
    return fbm_tex(1024, 512, 77, base=8, octaves=6)


def s08(t, d, ev):
    p = t / d
    # house fronts at dusk: shutters close, cloth drawn over windows, doors shut
    tod = 6.2
    zen, up, hor, gnd, amb = sky_colors(tod)
    img = sky(tod, 0.3)
    wall = hexc("#9a7650")
    tex = tex_sample(_plaster(), 30 + t * 2, 10, 0.6)
    ground_y = int(H * 0.86)
    surf, ctx = cairo_canvas()
    houses = [(-40, 470, 640), (520, 420, 720), (1020, 560, 600), (1660, 440, 680)]
    lamp_x = -300
    events = []
    for hi, (x, w, h) in enumerate(houses):
        top = ground_y - h
        shade = 0.9 - 0.12 * (hi % 2)
        rgb(ctx, wall * shade * (0.35 + 0.65 * amb))
        ctx.rectangle(x, top, w, h)
        ctx.fill()
        # parapet
        rgb(ctx, wall * shade * 0.75 * (0.35 + 0.65 * amb))
        ctx.rectangle(x - 6, top - 14, w + 12, 18)
        ctx.fill()
        # door
        dw, dh = 120, 230
        dx = x + w * 0.5 - dw / 2 + (hi - 1.5) * 20
        dy = ground_y - dh
        rgb(ctx, hexc("#1c120c"))
        ctx.move_to(dx, ground_y)
        ctx.line_to(dx, dy + dw / 2)
        ctx.arc(dx + dw / 2, dy + dw / 2, dw / 2, math.pi, 0)
        ctx.line_to(dx + dw, ground_y)
        ctx.close_path()
        ctx.fill()
        # the door leaf swings shut
        k = sm(0.15 + hi * 0.18, 0.27 + hi * 0.18, p)
        leaf_w = dw * k
        rgb(ctx, hexc("#5a3a22") * (0.4 + 0.6 * amb))
        ctx.save()
        ctx.move_to(dx, ground_y)
        ctx.line_to(dx, dy + dw / 2)
        ctx.arc(dx + dw / 2, dy + dw / 2, dw / 2, math.pi, 0)
        ctx.line_to(dx + dw, ground_y)
        ctx.close_path()
        ctx.clip()
        ctx.rectangle(dx, dy, leaf_w, dh + 2)
        ctx.fill()
        for pl in range(1, 5):
            rgb(ctx, hexc("#2a1a10"), 0.7)
            ctx.rectangle(dx + leaf_w * pl / 5, dy, 2, dh)
            ctx.fill()
        ctx.restore()
        # two windows: one with shutters, one with cloth
        for wi, wx in enumerate((x + w * 0.18, x + w * 0.66)):
            ww, wh = 92, 120
            wy = top + h * 0.22
            # warm interior
            rgb(ctx, GOLD * 0.9)
            ctx.rectangle(wx, wy, ww, wh)
            ctx.fill()
            start = 0.08 + 0.17 * hi + 0.08 * wi
            k = sm(start, start + 0.1, p)
            if (hi + wi) % 2 == 0:
                # shutters swing in from both sides
                rgb(ctx, hexc("#6b4428") * (0.4 + 0.6 * amb))
                ctx.rectangle(wx, wy, ww / 2 * k, wh)
                ctx.rectangle(wx + ww - ww / 2 * k, wy, ww / 2 * k, wh)
                ctx.fill()
                rgb(ctx, hexc("#2a1a10"), 0.6)
                for sl in range(1, 7):
                    ctx.rectangle(wx, wy + wh * sl / 7, ww / 2 * k, 2)
                    ctx.rectangle(wx + ww - ww / 2 * k, wy + wh * sl / 7, ww / 2 * k, 2)
                ctx.fill()
            else:
                # a cloth drops over it, with folds
                drop = wh * k
                rgb(ctx, hexc("#7a5a8a") * (0.35 + 0.65 * amb))
                ctx.move_to(wx - 6, wy - 6)
                ctx.line_to(wx + ww + 6, wy - 6)
                for f in range(9, -1, -1):
                    fx = wx - 6 + (ww + 12) * f / 9
                    ctx.line_to(fx, wy - 6 + drop + 8 * math.sin(f * 1.7 + t * 2) * k)
                ctx.close_path()
                ctx.fill()
            # frame
            rgb(ctx, wall * 0.5 * (0.35 + 0.65 * amb))
            ctx.set_line_width(8)
            ctx.rectangle(wx - 4, wy - 4, ww + 8, wh + 8)
            ctx.stroke()
    layer = cairo_rgba(surf)
    over(img, layer)
    # plaster texture on everything above ground
    img[:ground_y] *= (0.88 + 0.24 * tex[:ground_y, :, None])
    # ground
    img[ground_y:] = hexc("#3a2c20") * (0.4 + 0.6 * amb)
    # warm lamplight raking from the left, cooling as the doors shut
    warm = 1 - 0.5 * sm(0.2, 1.0, p)
    falloff = np.exp(-((XS - lamp_x) / 1500.0) ** 2)
    img *= (1 + warm * 0.35 * falloff[..., None] * np.array([1.0, 0.6, 0.2], np.float32))
    # window light spills
    return camera(img, 1.02 + 0.03 * p, fx=W * 0.5, fy=H * 0.6, dx=-30 * p)


def courtyard(tod, t, p, shadow_len=0.12):
    """An open square in one-point perspective."""
    zen, up, hor, gnd, amb = sky_colors(tod)
    hz = int(H * 0.42)
    img = sky(tod, hz / H)
    clouds(img, t, tod, 0.02, 0.35, density=0.25, speed=5)
    vx, vy = W / 2, hz
    surf, ctx = cairo_canvas()
    stone = hexc("#c8a77c") * (0.3 + 0.7 * amb)
    # floor
    rgb(ctx, stone * 0.92)
    ctx.rectangle(0, hz, W, H - hz)
    ctx.fill()
    # pavement joints converging to the vanishing point
    rgb(ctx, stone * 0.7, 0.8)
    ctx.set_line_width(1.6)
    for k in range(-14, 15):
        ctx.move_to(vx + k * 18, hz + 60)
        ctx.line_to(vx + k * 260, H + 10)
    for k in range(1, 16):
        y = hz + 60 + (H - hz - 60) * (k / 15) ** 2.2
        ctx.move_to(0, y)
        ctx.line_to(W, y)
    ctx.stroke()
    # back wall and side walls
    bw0, bw1 = W * 0.18, W * 0.82
    back_top = hz - 160
    rgb(ctx, stone * 1.0)
    ctx.rectangle(bw0, back_top, bw1 - bw0, hz + 60 - back_top)
    ctx.fill()
    for side in (-1, 1):
        x_in = bw0 if side < 0 else bw1
        x_out = -80 if side < 0 else W + 80
        rgb(ctx, stone * (0.82 if side < 0 else 0.62))
        ctx.move_to(x_in, back_top)
        ctx.line_to(x_out, back_top - 360)
        ctx.line_to(x_out, H * 0.98)
        ctx.line_to(x_in, hz + 60)
        ctx.close_path()
        ctx.fill()
        # closed doors along the side walls
        for k in range(3):
            u0, u1 = 0.18 + k * 0.27, 0.3 + k * 0.27
            xa, xb = lerp(x_in, x_out, u0), lerp(x_in, x_out, u1)
            ya_b, yb_b = lerp(hz + 60, H * 0.98, u0), lerp(hz + 60, H * 0.98, u1)
            ya_t, yb_t = lerp(back_top + 60, back_top - 210, u0), lerp(back_top + 60, back_top - 210, u1)
            rgb(ctx, hexc("#4a3222") * (0.4 + 0.6 * amb))
            ctx.move_to(xa, ya_b)
            ctx.line_to(xa, lerp(ya_b, ya_t, 0.55))
            ctx.line_to(xb, lerp(yb_b, yb_t, 0.55))
            ctx.line_to(xb, yb_b)
            ctx.close_path()
            ctx.fill()
    # back wall arches
    for k in range(5):
        ax = lerp(bw0 + 60, bw1 - 120, k / 4)
        rgb(ctx, hexc("#4a3222") * (0.4 + 0.6 * amb))
        ctx.move_to(ax, hz + 60)
        ctx.line_to(ax, hz - 30)
        ctx.arc(ax + 30, hz - 30, 30, math.pi, 0)
        ctx.line_to(ax + 60, hz + 60)
        ctx.close_path()
        ctx.fill()
    over(img, cairo_rgba(surf))
    # hard shadow of the left wall, only on the floor between the side walls
    yy = np.clip((YS - (hz + 60)) / (H * 0.98 - (hz + 60)), 0, 1)
    x_left = bw0 + (-80 - bw0) * yy
    x_right = bw1 + (W + 80 - bw1) * yy
    on_floor = (YS > hz + 60) & (XS > x_left) & (XS < x_right)
    edge = x_left + shadow_len * W * (0.3 + yy)
    shadow = on_floor * clamp01((edge - XS) / 4.0)
    img *= (1 - 0.4 * shadow * float(amb.mean()))[..., None]
    return img


def s09(t, d, ev):
    p = t / d
    img = courtyard(4.0, t, p, shadow_len=0.03)
    sun = hexc("#fff4dc")
    glow(img, W * 0.5, -60, 700, sun, 0.25, kind="halo", reach=4)
    return camera(img, 1.0 + 0.08 * ease(p), fx=W / 2, fy=H * 0.5)


def lit_window(t, p, open_amt=1.0):
    tod = 0.2
    zen, up, hor, gnd, amb = sky_colors(tod)
    img = vgradient([(0, hexc("#07080c")), (1, hexc("#0d0c10"))])
    tex = tex_sample(_plaster(), 100, 60, 0.7)
    wall = hexc("#2a2018")
    img[:] = wall * (0.6 + 0.6 * tex[..., None])
    # moonlight from upper right, faint
    img *= (0.7 + 0.5 * np.exp(-(((XS - W) / 1400) ** 2 + ((YS) / 900) ** 2)))[..., None]
    wx, wy, ww, wh = W * 0.5 - 90, H * 0.36, 180, 230
    breath = 0.9 + 0.1 * math.sin(t * 1.3)
    glow(img, wx + ww / 2, wy + wh / 2, 380, GOLD, 0.25 * breath * open_amt, kind="halo", reach=5)
    surf, ctx = cairo_canvas()
    # arch window
    rgb(ctx, GOLD * 1.05 * breath, open_amt)
    ctx.move_to(wx, wy + wh)
    ctx.line_to(wx, wy + ww / 2)
    ctx.arc(wx + ww / 2, wy + ww / 2, ww / 2, math.pi, 0)
    ctx.line_to(wx + ww, wy + wh)
    ctx.close_path()
    ctx.fill()
    # lattice
    rgb(ctx, hexc("#1a120c"))
    ctx.set_line_width(5)
    ctx.move_to(wx + ww / 2, wy)
    ctx.line_to(wx + ww / 2, wy + wh)
    ctx.move_to(wx, wy + wh * 0.55)
    ctx.line_to(wx + ww, wy + wh * 0.55)
    ctx.stroke()
    rgb(ctx, hexc("#120c08"))
    ctx.set_line_width(14)
    ctx.move_to(wx, wy + wh)
    ctx.line_to(wx, wy + ww / 2)
    ctx.arc(wx + ww / 2, wy + ww / 2, ww / 2, math.pi, 0)
    ctx.line_to(wx + ww, wy + wh)
    ctx.close_path()
    ctx.stroke()
    # sill
    rgb(ctx, hexc("#3a2c20"))
    ctx.rectangle(wx - 24, wy + wh + 4, ww + 48, 18)
    ctx.fill()
    over(img, cairo_rgba(surf))
    # light falling on the sill and wall below
    glow(img, wx + ww / 2, wy + wh + 60, 150, GOLD, 0.12 * breath * open_amt)
    return img


def s10(t, d, ev):
    p = t / d
    # announced in public... and confided in private: the square dims to one lit window
    cut = ev_at(ev, "asrartu", 0.48 * d) / d
    if p < cut - 0.06:
        q = p / max(cut, 1e-3)
        img = courtyard(lerp(4.0, 5.6, q), t, q, shadow_len=lerp(0.03, 0.35, q))
        return camera(img, 1.08 + 0.04 * q)
    a = sm(cut - 0.06, cut + 0.04, p)
    win = lit_window(t, p)
    win = camera(win, 1.0 + 0.05 * (p - cut))
    if a >= 1:
        return win
    q = p / max(cut, 1e-3)
    sq = camera(courtyard(lerp(4.0, 5.6, q), t, q, shadow_len=lerp(0.03, 0.35, q)), 1.08 + 0.04 * q)
    return sq * (1 - a) + win * a


# --------------------------------------------------------------------------
# 11-13  ayat 10-12: rain on cracked earth, gardens, rivers
# --------------------------------------------------------------------------


@functools.lru_cache(maxsize=1)
def crack_plane():
    """Cracked mud in perspective. Returns (crack mask, cell noise, ground row)."""
    tw, th = 2600, 1600
    r = np.random.default_rng(31)
    pts = np.zeros((th, tw), np.uint8) + 255
    n = 900
    xs = (r.random(n) * tw).astype(int)
    ys = (r.random(n) * th).astype(int)
    pts[ys, xs] = 0
    dist, lab = cv2.distanceTransformWithLabels(pts, cv2.DIST_L2, 5, labelType=cv2.DIST_LABEL_CCOMP)
    edge = np.zeros_like(dist)
    edge[:, 1:] += lab[:, 1:] != lab[:, :-1]
    edge[1:, :] += lab[1:, :] != lab[:-1, :]
    edge = (edge > 0).astype(np.float32)
    # wobble the cracks
    nz = fbm_tex(1024, 512, 41, base=8, octaves=4)
    nz = cv2.resize(nz, (tw, th))
    mx, my = np.meshgrid(np.arange(tw, dtype=np.float32), np.arange(th, dtype=np.float32))
    edge = cv2.remap(edge, mx + (nz - 0.5) * 30, my + (nz.T[: th, : tw] if False else (nz - 0.5) * 30), cv2.INTER_LINEAR)
    edge = cv2.dilate(edge, np.ones((5, 5), np.uint8))
    edge = cv2.GaussianBlur(edge, (0, 0), 1.6)
    cell = (dist / (dist.max() + 1e-9)).astype(np.float32)
    gy = int(H * 0.45)
    src = np.float32([[0, 0], [tw, 0], [tw, th], [0, th]])
    dst = np.float32([[-W * 2.2, gy], [W * 3.2, gy], [W * 1.25, H * 1.25], [-W * 0.25, H * 1.25]])
    M = cv2.getPerspectiveTransform(src, dst)
    crack = cv2.warpPerspective(edge, M, (W, H), flags=cv2.INTER_AREA)
    cellw = cv2.warpPerspective(cell, M, (W, H), flags=cv2.INTER_LINEAR)
    grain = cv2.warpPerspective(cv2.resize(fbm_tex(1024, 512, 43, base=16, octaves=5), (tw, th)), M, (W, H))
    return np.clip(crack, 0, 1), cellw, grain, gy


def cracked_earth(t, tod, wet=0.0, cloud=0.3, dark=0.0, rain_amt=0.0, sky_override=None):
    crack, cell, grain, gy = crack_plane()
    zen, up, hor, gnd, amb = sky_colors(tod)
    img = sky(tod, gy / H) if sky_override is None else sky_override.copy()
    clouds(img, t, tod, 0.0, gy / H + 0.02, density=cloud, speed=10, dark=dark)
    land_layers(img, tod, gy, layers=[(0.0, 50, 30, 2.5, 1, "#6a6470"), (0.4, 16, 14, 5.0, 3, "#6b5844")])
    skyimg = img.copy()
    dry = hexc("#b08a5c")
    wetc = hexc("#4a3424")
    albedo = mix(dry, wetc, 0)
    base = dry[None, None] * (1 - wet * 0.65) + wetc[None, None] * (wet * 0.65)
    g = base * (0.75 + 0.45 * grain[..., None]) * (0.85 + 0.3 * cell[..., None])
    g = g * (0.25 + 0.75 * amb)
    # cracks: dark when dry, sky-reflecting water when wet
    refl = cv2.flip(skyimg[: H - gy], 0)
    refl = cv2.resize(refl, (W, H - gy))
    crk = crack[gy:, :, None]
    dark_c = g[gy:] * 0.25
    water_c = refl * 0.7 + hor * 0.15
    cc = dark_c * (1 - wet) + water_c * wet
    region = g[gy:] * (1 - crk) + cc * crk
    # far haze
    yy = np.linspace(0, 1, H - gy, dtype=np.float32)[:, None, None]
    region = region * (1 - (1 - yy) ** 6 * 0.7) + hor * 0.8 * (1 - yy) ** 6 * 0.7
    img[gy:] = region
    return img


def s11(t, d, ev):
    p = t / d
    # "ask forgiveness": clouds gather over the dry land; the light softens
    tod = lerp(4.5, 5.2, p)
    img = cracked_earth(t, tod, wet=0.0, cloud=lerp(0.25, 0.75, ease(p)), dark=0.35 * ease(p))
    img *= (1 - 0.25 * ease(p))
    return camera(img, 1.0 + 0.05 * p, fy=H * 0.6)


def s12(t, d, ev):
    p = t / d
    # "He will send the sky upon you in abundance": rain, the cracks fill
    tod = 5.2
    wet = ease(sm(0.08, 0.85, p))
    img = cracked_earth(t, tod, wet=wet, cloud=0.85, dark=0.5)
    img *= 0.75
    r = np.random.default_rng(int(t * 24))
    crack, cell, grain, gy = crack_plane()
    # splashes on the ground
    amt = sm(0.0, 0.15, p)
    for _ in range(int(140 * amt)):
        y = gy + (r.random() ** 0.6) * (H - gy)
        x = r.random() * W
        s = 0.5 + 3.5 * (y - gy) / (H - gy)
        glow(img, x, y, 2.0 * s, (0.6, 0.65, 0.75), 0.25)
    img = rain(img, t, amt * 1.2, speed=1700, slant=0.06)
    return camera(img, 1.06 - 0.03 * p, fy=H * 0.6)


@functools.lru_cache(maxsize=1)
def garden_layout():
    r = np.random.default_rng(51)
    trees = []
    for _ in range(140):
        z = r.random() ** 0.7  # 0 far .. 1 near
        x = r.random() * W * 1.6 - W * 0.3
        trees.append((x, z, r.uniform(0.7, 1.3), r.random()))
    trees.sort(key=lambda a: a[1])
    return trees


def river_x(z, t=0.0):
    """river centre x at depth z (0 horizon .. 1 bottom)"""
    return W * 0.55 + (W * 0.45) * z ** 1.3 * math.sin(z * 5.0 + 0.6) - 120 * (1 - z)


def s13(t, d, ev):
    p = t / d
    # "wealth and children, gardens and rivers": the plain greens, a river finds its bed
    tod = 3.2
    zen, up, hor, gnd, amb = sky_colors(tod)
    gy = int(H * 0.5)
    img = sky(tod, gy / H)
    sun_moon(img, 3.3, gy, moon=False)
    clouds(img, t, tod, 0.05, 0.45, density=0.3, speed=8)
    land_layers(img, tod, gy, layers=[(0.0, 70, 35, 2.5, 1, "#6a7488"), (0.4, 24, 16, 5.0, 3, "#5a6a48")])
    skyimg = img.copy()
    nz = fbm_tex(1024, 512, 61, base=6, octaves=5)
    f = cv2.resize(nz, (W, H - gy))
    dry = hexc("#a88a5e")
    green = hexc("#4f7a3a")
    green2 = hexc("#7a9a48")
    grow = ease(sm(0.1, 0.85, p))
    yy = np.linspace(0, 1, H - gy, dtype=np.float32)[:, None]
    # greening spreads from the river outward and from near to far
    zz = yy
    rx = np.array([river_x(float(z)) for z in np.linspace(0, 1, H - gy)], np.float32)[:, None]
    dist = np.abs(XS - rx) / (40 + 900 * zz)
    gmask = smooth(grow * 2.2 - 0.4, grow * 2.2, (dist * 0.5 + (1 - f) * 0.9))
    gmask = 1 - gmask
    col = dry * (1 - gmask[..., None]) + (green * (1 - f[..., None]) + green2 * f[..., None]) * gmask[..., None]
    col = col * (0.85 + 0.25 * f[..., None]) * (0.3 + 0.7 * amb)
    haze = (1 - zz) ** 5
    col = col * (1 - haze[..., None] * 0.7) + hor * haze[..., None] * 0.7 * 0.9
    img[gy:] = col
    # river: reveal from horizon toward the viewer
    reveal = ease(sm(0.0, 0.55, p))
    width = (6 + 230 * zz ** 1.4)
    rmask = clamp01((width - np.abs(XS - rx)) / 2.5) * (zz < reveal + 0.02) * smooth(reveal + 0.02, reveal - 0.02, zz)
    refl = cv2.resize(cv2.flip(skyimg[:gy], 0), (W, H - gy))
    ripple = 0.9 + 0.1 * np.sin(XS * 0.05 + YS[gy:] * 0.3 + t * 3)
    wcol = refl * 0.8 * ripple[..., None] + hexc("#20405a") * 0.2
    img[gy:] = img[gy:] * (1 - rmask[..., None]) + wcol * rmask[..., None]
    # trees rise
    surf, ctx = cairo_canvas()
    for (x0, z, sz, ph) in garden_layout():
        y = gy + (H - gy) * z ** 1.4 + 4
        x = x0
        rxz = river_x(min(max((y - gy) / (H - gy), 0), 1))
        if abs(x - rxz) < (40 + 260 * z):
            continue
        appear = sm(0.15 + 0.6 * (1 - z) * 0.3 + ph * 0.45, 0.3 + ph * 0.45 + 0.2, p)
        if appear <= 0:
            continue
        s = (6 + 70 * z ** 1.3) * sz * ease_out(appear)
        trunk = hexc("#4a3424") * (0.3 + 0.7 * amb)
        leaf = mix(hexc("#355a2a"), hexc("#6a8a3a"), ph) * (0.3 + 0.7 * amb)
        leaf = mix(leaf, hor * 0.8, (1 - z) ** 3 * 0.6)
        rgb(ctx, trunk)
        ctx.rectangle(x - s * 0.07, y - s * 0.9, s * 0.14, s * 0.9)
        ctx.fill()
        for (ox, oy, rr, sh) in ((0, -1.25, 0.62, 1.0), (-0.35, -1.0, 0.45, 0.85), (0.38, -1.05, 0.48, 0.92), (0.05, -1.55, 0.4, 1.12)):
            rgb(ctx, leaf * sh)
            ctx.arc(x + ox * s, y + oy * s, rr * s, 0, 2 * math.pi)
            ctx.fill()
    over(img, cairo_rgba(surf))
    return camera(img, 1.0 + 0.06 * ease(p), fy=H * 0.55)


# --------------------------------------------------------------------------
# 14-17  ayat 13-16: the sky
# --------------------------------------------------------------------------


@functools.lru_cache(maxsize=1)
def milky_tex():
    return fbm_tex(1024, 1024, 71, base=5, octaves=7)


def night_sky(t, rot, horizon_px, milky=1.0):
    img = vgradient([(0, hexc("#02030a")), (horizon_px / H * 0.7, hexc("#070b1c")), (horizon_px / H, hexc("#16182e")), (1, hexc("#16182e"))])
    if milky > 0:
        tex = milky_tex()
        m = cv2.getRotationMatrix2D((512, 512), math.degrees(rot) + 35, 1.0)
        tx = cv2.warpAffine(tex, m, (1024, 1024), borderMode=cv2.BORDER_REFLECT)
        tx = cv2.resize(tx, (W, W))[(W - H) // 2 : (W - H) // 2 + H]
        band = np.exp(-(((XS - W * 0.5) * 0.6 + (YS - H * 0.35) * 0.8) / 260.0) ** 2)
        band = cv2.warpAffine(band.astype(np.float32), cv2.getRotationMatrix2D((W / 2, H * 0.6), math.degrees(rot), 1.0), (W, H))
        neb = (band * smooth(0.35, 0.9, tx))[..., None]
        img += neb * hexc("#3a3a60") * 0.6 * milky + (band * tx * 0.25)[..., None] * hexc("#6a5a80") * milky
    stars(img, 1.0, t, horizon_px, rot=rot, scale=1.25)
    stars(img, 0.8, t * 1.3, horizon_px, rot=rot, seed=4, scale=0.9)
    return img


def s14(t, d, ev):
    p = t / d
    # grandeur: a vast turning sky, the village a thread of rooftops at the bottom
    hz = int(H * 0.88)
    img = night_sky(t, 0.06 * p, hz)
    zen, up, hor, gnd, amb = sky_colors(0.2)
    land_layers(img, 0.2, hz, layers=[(0.0, 40, 22, 2.0, 1, "#1a1c2a"), (0.5, 12, 8, 6, 3, "#0e0e14")])
    img[hz:] = hexc("#0c0c12")
    draw_village(img, 0.1, W * 0.55, hz + 6, scale=0.32, lit=lambda hs: 0.6 if hs["lit_order"] < 0.15 else 0.0)
    lamp(img, W * 0.3, hz - 8, 1.0, t, scale=0.35, halo=1.5)
    return camera(img, 1.0 + 0.05 * p, fy=hz)


def s15(t, d, ev):
    p = t / d
    # "created you in stages": a drop, rings, layer on layer
    img = vgradient([(0, hexc("#0a1418")), (0.4, hexc("#12262c")), (1, hexc("#08100f"))])
    glow(img, W * 0.5, H * 0.1, 900, hexc("#2a4a50"), 0.35, kind="halo")
    cx, cy = W * 0.5, H * 0.6
    t_hit = 0.18 * d
    surf, ctx = cairo_canvas()
    if t < t_hit:
        u = t / t_hit
        y = lerp(-40, cy, u * u)
        rgb(ctx, hexc("#cfe8f0"), 0.9)
        ctx.save()
        ctx.translate(cx, y)
        ctx.scale(1, 1.6)
        ctx.arc(0, 0, 9, 0, 2 * math.pi)
        ctx.restore()
        ctx.fill()
        glow(img, cx, y, 30, hexc("#8ac0d0"), 0.3)
    else:
        tt = t - t_hit
        # rings spawn steadily; early ones fade, later ones stay as strata
        n_rings = 7
        span = (d - t_hit) * 0.75
        for k in range(n_rings):
            t0 = k * span / n_rings
            if tt < t0:
                continue
            age = tt - t0
            final = 90 + k * 70
            rr = final * ease_out(min(1, age / 2.5))
            a = 0.9 * (0.55 + 0.45 * math.exp(-age * 0.6))
            hue = mix(hexc("#9fd6e0"), hexc("#e8c890"), k / (n_rings - 1))
            rgb(ctx, hue, a)
            ctx.set_line_width(3.0 + 2.5 * math.exp(-age))
            ctx.save()
            ctx.translate(cx, cy)
            ctx.scale(1, 0.34)
            ctx.arc(0, 0, rr, 0, 2 * math.pi)
            ctx.restore()
            ctx.stroke()
            # inner shadow line
            rgb(ctx, hexc("#04090a"), 0.5 * a)
            ctx.set_line_width(2.0)
            ctx.save()
            ctx.translate(cx, cy + 3)
            ctx.scale(1, 0.34)
            ctx.arc(0, 0, max(rr - 6, 1), 0, 2 * math.pi)
            ctx.restore()
            ctx.stroke()
        # splash crown fading
        if tt < 0.6:
            g = 1 - tt / 0.6
            glow(img, cx, cy - 10, 40, hexc("#cfe8f0"), 0.6 * g)
    over(img, cairo_rgba(surf))
    glow(img, cx, cy, 120, hexc("#4a8a90"), 0.15 * sm(0.18, 0.4, p))
    return camera(img, 1.0 + 0.1 * ease(p), fy=cy)


def s16(t, d, ev):
    p = t / d
    # seven skies stacked in bands, revealed as we rise through them
    img = vgradient([(0, hexc("#03040c")), (1, hexc("#0c1026"))])
    stars(img, 0.8, t, H, scale=0.9)
    cx = W * 0.5
    cy = H * (2.25 - 0.55 * ease(p))
    cols = ["#1a2a5a", "#24376e", "#33478a", "#4a5aa0", "#6a6aa8", "#9a86a8", "#d0a890"]
    for k in range(7):
        appear = sm(0.05 + k * 0.11, 0.17 + k * 0.11, p)
        if appear <= 0:
            continue
        r0 = H * (0.95 + k * 0.23)
        r1 = r0 + H * 0.19
        dd = np.sqrt((XS - cx) ** 2 + (YS - cy) ** 2)
        band = smooth(r0, r0 + 14, dd) * (1 - smooth(r1 - 14, r1, dd))
        edge = np.exp(-((dd - r1) / 5.0) ** 2) * 0.6
        c = hexc(cols[k])
        a = band * 0.55 * appear
        img = img * (1 - a[..., None]) + c * (0.5 + 0.5 * (1 - (dd - r0) / (r1 - r0)).clip(0, 1))[..., None] * a[..., None]
        img += edge[..., None] * c * 1.2 * appear
    return img


def s17(t, d, ev):
    p = t / d
    # moon a light; sun a lamp
    sw = ev_at(ev, "shams", 0.5 * d) / d
    hz = int(H * 0.6)
    q = sm(sw - 0.08, sw + 0.25, p)
    tod = lerp(0.4, 2.6, q)
    img = sky(tod, hz / H)
    stars(img, star_alpha(tod), t, hz)
    zen, up, hor, gnd, amb = sky_colors(tod)
    # moon high left, fading as the sun rises
    mdim = 1 - sm(sw, sw + 0.2, p)
    if mdim > 0:
        mx, my = W * 0.3, H * 0.22
        glow(img, mx, my, 220, hexc("#8090c0"), 0.35 * mdim, kind="halo", reach=6)
        disk(img, mx, my, 34, hexc("#eef0f8") * mdim + img[int(my), int(mx)] * (1 - mdim))
    # sun rises at right of centre
    if q > 0:
        sy = lerp(hz + 40, hz - 210, ease_out(q))
        sx = W * 0.62
        warm = mix(hexc("#ff9a50"), hexc("#fff0d0"), q)
        glow(img, sx, sy, 380, warm, 0.45 * q, kind="halo", reach=6)
        glow(img, sx, sy, 90, warm, 0.8 * q)
        disk(img, sx, sy, 40, warm * 1.2 + 0.1, min(1, q * 3))
    land_layers(img, tod, hz, layers=[(0.0, 40, 25, 2.5, 1, "#3a3a4a")])
    # water reflecting it all
    water(img, tod, hz, t, rough=0.05)
    return camera(img, 1.02 + 0.03 * p)


# --------------------------------------------------------------------------
# 18-19  ayat 17-18: grown from the earth, returned to it, brought out again
# --------------------------------------------------------------------------


@functools.lru_cache(maxsize=1)
def sprout_layout():
    r = np.random.default_rng(81)
    out = []
    for k in range(9):
        x = W * (0.08 + 0.84 * k / 8) + r.uniform(-40, 40)
        out.append(dict(x=x, depth=r.uniform(60, 130), h=r.uniform(170, 330), lean=r.uniform(-0.25, 0.25), ph=r.random()))
    return out


def soil_scene(t, tod):
    gy = int(H * 0.55)
    img = sky(tod, gy / H)
    clouds(img, t, tod, 0.05, 0.45, density=0.3, speed=6)
    sun_moon(img, tod, gy, moon=False)
    zen, up, hor, gnd, amb = sky_colors(tod)
    tex = fbm_tex(1024, 512, 91, base=6, octaves=6)
    f = tex_sample(tex, 20, 0, 0.6)
    yy = np.linspace(0, 1, H - gy, dtype=np.float32)[:, None]
    strata = 0.5 + 0.5 * np.sin(yy * 26 + f[gy:] * 5)
    soil = mix(hexc("#5a3e28"), hexc("#3a2818"), 0.5)
    col = soil * (0.75 + 0.35 * f[gy:, :, None]) * (0.85 + 0.15 * strata[..., None]) * (1 - 0.45 * yy[..., None])
    col *= 0.45 + 0.55 * amb
    img[gy:] = col
    # surface line with grass tufts
    img[gy : gy + 3] = img[gy : gy + 3] * 0.5 + hexc("#7a6a40") * 0.5
    return img, gy


def draw_sprouts(img, gy, grow_fn, wither_fn, t, amb):
    surf, ctx = cairo_canvas()
    for i, s in enumerate(sprout_layout()):
        g = grow_fn(i, s)
        w = wither_fn(i, s)
        seed_y = gy + s["depth"]
        x = s["x"]
        # seed
        rgb(ctx, hexc("#c8a060") * (0.5 + 0.5 * amb))
        ctx.save()
        ctx.translate(x, seed_y)
        ctx.scale(1.4, 1)
        ctx.arc(0, 0, 9, 0, 2 * math.pi)
        ctx.restore()
        ctx.fill()
        if g <= 0.001:
            continue
        # roots
        rgb(ctx, hexc("#e0c8a0") * (0.5 + 0.5 * amb), 0.8)
        ctx.set_line_width(2)
        for k in range(4):
            ang = -0.9 + k * 0.6
            L = 70 * min(1, g * 1.5)
            ctx.move_to(x, seed_y)
            ctx.curve_to(x + math.sin(ang) * L * 0.3, seed_y + L * 0.4, x + math.sin(ang) * L * 0.8, seed_y + L * 0.7, x + math.sin(ang) * L, seed_y + L)
            ctx.stroke()
        top_y = seed_y - (s["depth"] + s["h"]) * g
        tx = x + s["lean"] * s["h"] * g + math.sin(t * 1.2 + s["ph"] * 6) * 4 * g
        green = mix(hexc("#5a9a3a"), hexc("#8a7a3a"), w) * (0.45 + 0.55 * amb)
        rgb(ctx, green)
        ctx.set_line_width(6 * (0.5 + 0.5 * g))
        ctx.move_to(x, seed_y)
        ctx.curve_to(x, seed_y - (seed_y - top_y) * 0.4, tx - s["lean"] * 40, top_y + (seed_y - top_y) * 0.3, tx, top_y)
        ctx.stroke()
        # leaves unfold above ground
        if top_y < gy - 20:
            lf = min(1, (gy - 20 - top_y) / 120) * (1 - 0.6 * w)
            for side in (-1, 1):
                L = 70 * lf
                droop = w * 40
                ctx.move_to(tx, top_y + 10)
                ctx.curve_to(tx + side * L * 0.5, top_y - L * 0.3 + droop, tx + side * L, top_y - L * 0.1 + droop, tx + side * L * 1.1, top_y + 8 + droop)
                ctx.curve_to(tx + side * L * 0.7, top_y + 18 + droop, tx + side * L * 0.3, top_y + 18, tx, top_y + 10)
                rgb(ctx, green * 1.15)
                ctx.fill()
    over(img, cairo_rgba(surf))
    return img


def s18(t, d, ev):
    p = t / d
    img, gy = soil_scene(t, 3.0)
    amb = float(sky_colors(3.0)[4].mean())
    draw_sprouts(img, gy, lambda i, s: ease(sm(0.08 + s["ph"] * 0.25, 0.65 + s["ph"] * 0.25, p)), lambda i, s: 0.0, t, amb)
    return camera(img, 1.0 + 0.05 * p, fy=gy)


def s19(t, d, ev):
    p = t / d
    turn = ev_at(ev, "yukhrijukum", 0.5 * d) / d
    tod = lerp(3.0, 5.4, sm(0.0, turn, p)) if p < turn else lerp(5.4, 2.6, sm(turn, 1.0, p))
    img, gy = soil_scene(t, tod)
    amb = float(sky_colors(tod)[4].mean())
    if p < turn:
        q = p / turn
        grow = lambda i, s: 1.0 - ease(sm(0.15 + s["ph"] * 0.2, 0.75 + s["ph"] * 0.2, q))
        wither = lambda i, s: sm(0.0, 0.4, q)
    else:
        q = (p - turn) / (1 - turn)
        grow = lambda i, s: ease(sm(0.05 + s["ph"] * 0.15, 0.6 + s["ph"] * 0.2, q))
        wither = lambda i, s: 0.0
    draw_sprouts(img, gy, grow, wither, t, amb)
    return camera(img, 1.05, fy=gy)


# --------------------------------------------------------------------------
# 20-21  ayat 19-20: the earth spread like a carpet, crossed by wide paths
# --------------------------------------------------------------------------


@functools.lru_cache(maxsize=1)
def fields_layer():
    LW, LH = 3400, 2200
    r = np.random.default_rng(101)
    rects = []

    def split(x0, y0, x1, y1, depth):
        w, h = x1 - x0, y1 - y0
        if depth > 6 or (w < 260 and h < 260) or (depth > 3 and r.random() < 0.25):
            rects.append((x0, y0, x1, y1))
            return
        if w > h:
            c = x0 + w * r.uniform(0.3, 0.7)
            split(x0, y0, c, y1, depth + 1)
            split(c, y0, x1, y1, depth + 1)
        else:
            c = y0 + h * r.uniform(0.3, 0.7)
            split(x0, y0, x1, c, depth + 1)
            split(x0, c, x1, y1, depth + 1)

    split(0, 0, LW, LH, 0)
    pal = [hexc(c) for c in ("#8a6a3a", "#6a7a3a", "#a07a48", "#4f6a34", "#9a8a50", "#7a4a2a", "#5a5a30", "#b09060")]
    surf, ctx = cairo_canvas(LW, LH)
    rgb(ctx, hexc("#3a3020"))
    ctx.paint()
    for (x0, y0, x1, y1) in rects:
        c = pal[int(r.integers(0, len(pal)))]
        m = 6
        rgb(ctx, c)
        ctx.rectangle(x0 + m, y0 + m, x1 - x0 - 2 * m, y1 - y0 - 2 * m)
        ctx.fill()
        # furrows, like the weave of a carpet
        rgb(ctx, c * 0.82, 0.8)
        ctx.set_line_width(3)
        horiz = r.random() < 0.5
        step = r.uniform(14, 26)
        if horiz:
            y = y0 + m + step
            while y < y1 - m:
                ctx.move_to(x0 + m, y)
                ctx.line_to(x1 - m, y)
                y += step
        else:
            x = x0 + m + step
            while x < x1 - m:
                ctx.move_to(x, y0 + m)
                ctx.line_to(x, y1 - m)
                x += step
        ctx.stroke()
        # some fields carry a diamond, the medallion of a rug
        if r.random() < 0.18 and (x1 - x0) > 200 and (y1 - y0) > 200:
            cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
            s = min(x1 - x0, y1 - y0) * 0.3
            rgb(ctx, mix(c, hexc("#d0b070"), 0.35))
            ctx.move_to(cx, cy - s)
            ctx.line_to(cx + s, cy)
            ctx.line_to(cx, cy + s)
            ctx.line_to(cx - s, cy)
            ctx.close_path()
            ctx.fill()
    layer = cairo_rgba(surf)[..., :3].copy()
    nz = cv2.resize(fbm_tex(1024, 512, 103, base=8, octaves=6), (LW, LH))
    layer *= (0.82 + 0.3 * nz[..., None])
    # a winding river across the land
    return layer, rects


@functools.lru_cache(maxsize=1)
def path_network():
    LW, LH = 3400, 2200
    r = np.random.default_rng(111)
    paths = []
    hubs = [(LW * 0.5, LH * 0.5)]
    for k in range(12):
        a = r.uniform(0, 2 * math.pi)
        sx, sy = hubs[0] if k < 6 else (LW * r.uniform(0.2, 0.8), LH * r.uniform(0.2, 0.8))
        pts = [(sx, sy)]
        x, y = sx, sy
        for s in range(40):
            a += r.normal(0, 0.07)
            x += math.cos(a) * 60
            y += math.sin(a) * 60
            pts.append((x, y))
        paths.append((pts, r.uniform(0, 0.4)))
    return paths


def aerial(t, p, zoom, rot, paths_progress=None):
    layer, _ = fields_layer()
    LH, LW = layer.shape[:2]
    m = cv2.getRotationMatrix2D((LW / 2, LH / 2), math.degrees(rot), zoom)
    m[0, 2] += W / 2 - LW / 2
    m[1, 2] += H / 2 - LH / 2
    if paths_progress is not None:
        layer = layer.copy()
        surf, ctx = cairo_canvas(LW, LH)
        for pts, delay in path_network():
            q = sm(delay, delay + 0.55, paths_progress)
            if q <= 0:
                continue
            n = max(2, int(len(pts) * q))
            for lw, col, a in ((30, hexc("#5a4630"), 0.35), (20, hexc("#cdb58a"), 0.95), (8, hexc("#ddc89e"), 0.6)):
                rgb(ctx, col, a)
                ctx.set_line_width(lw)
                ctx.set_line_cap(cairo.LINE_CAP_ROUND)
                ctx.set_line_join(cairo.LINE_JOIN_ROUND)
                ctx.move_to(*pts[0])
                for x, y in pts[1:n]:
                    ctx.line_to(x, y)
                ctx.stroke()
        pl = cairo_rgba(surf)
        layer = layer * (1 - pl[..., 3:4]) + pl[..., :3]
    img = cv2.warpAffine(layer, m, (W, H), flags=cv2.INTER_AREA if zoom < 1 else cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
    # soft morning light from the upper left
    img *= (0.8 + 0.35 * np.exp(-(((XS - W * 0.2) / 1600) ** 2 + ((YS - H * 0.1) / 1100) ** 2)))[..., None]
    # thin cloud shadows drifting over the land
    tex = fbm_tex(1024, 512, 121, base=3, octaves=5)
    cs = tex_sample(tex, t * 12, 0, 0.6)
    img *= (1 - 0.18 * smooth(0.55, 0.8, cs))[..., None]
    return img


def s20(t, d, ev):
    p = t / d
    return aerial(t, p, zoom=lerp(1.0, 0.62, ease(p)), rot=0.05 * p)


def s21(t, d, ev):
    p = t / d
    return aerial(t, p, zoom=lerp(0.62, 0.55, p), rot=0.05 + 0.03 * p, paths_progress=ease(min(1, p * 1.1)))


# --------------------------------------------------------------------------
# 22-25  ayat 21-24: five plain stones under a darkening sky
# --------------------------------------------------------------------------

STONE_X, STONE_Y = W * 0.5, int(H * 0.74)


def stones_scene(t, tod, cloud=0.4, dark=0.0, scale=95.0, lights=None, swirl=0.0, fog=0.0, rim=None, vill=0.0):
    hz = int(H * 0.6)
    img = sky(tod, hz / H)
    stars(img, star_alpha(tod) * (1 - cloud), t, hz)
    sun_moon(img, tod, hz, moon=False)
    if swirl > 0:
        tex = fbm_tex(1024, 512, 131, base=4, octaves=4)
        pol = cv2.resize(tex, (720, 720))
        rot = cv2.warpAffine(pol, cv2.getRotationMatrix2D((360, 360), t * 6.0, 1.0), (720, 720), borderMode=cv2.BORDER_REFLECT)
        sw = cv2.warpPolar(rot, (W, W), (W / 2, W / 2), W * 0.8, cv2.WARP_INVERSE_MAP + cv2.WARP_POLAR_LINEAR)
        sw = sw[int(W / 2 - H * 0.35) : int(W / 2 - H * 0.35) + hz]
        a = smooth(0.4, 0.7, sw) * swirl
        c = mix(hexc("#3a3a48"), hexc("#0c0c12"), dark)
        img[:hz] = img[:hz] * (1 - a[..., None]) + c * (0.6 + 0.6 * sw[..., None]) * a[..., None]
    clouds(img, t, tod, 0.0, 0.58, density=cloud, speed=14 + 20 * dark, dark=dark)
    land_layers(img, tod, hz, layers=[(0.0, 46, 30, 2.5, 1, "#4a4858"), (0.4, 14, 10, 5.0, 3, "#3a3428")])
    ground(img, tod, hz, "#5a4a36", far_haze=0.6)
    if vill > 0:
        draw_village(img, tod, W * 0.17, hz + 18, scale=0.38, lit=lambda hs: vill if hs["lit_order"] < 0.3 else 0.0)
    if rim is None and tod > 6.4:
        rim = hexc("#9aa8c8") * 0.7
    draw_stones(img, tod, STONE_X, STONE_Y, scale, lights=lights, rim=rim)
    if fog > 0:
        tex = fbm_tex(1024, 512, 141, base=4, octaves=3)
        f = tex_sample(tex, t * 25, 0, 0.5)
        fa = (smooth(0.3, 0.9, f) * smooth(hz - 120, H, YS) * fog)[..., None]
        zen, up, hor, gnd, amb = sky_colors(tod)
        img = img * (1 - fa) + (hor * 0.5 + 0.03) * fa
    return img


def s22(t, d, ev):
    p = t / d
    # they disobeyed: the stones at dusk, the village dim beyond
    tod = lerp(5.6, 6.3, p)
    img = stones_scene(t, tod, cloud=0.35, dark=0.2, vill=0.5)
    return camera(img, 1.0 + 0.08 * ease(p), fy=STONE_Y)


def s23(t, d, ev):
    p = t / d
    # a tremendous plot: the sky coils into a storm
    tod = lerp(6.3, 6.8, p)
    img = stones_scene(t, tod, cloud=lerp(0.45, 0.8, p), dark=lerp(0.3, 0.75, p), swirl=ease(p))
    img *= 1.25
    return camera(img, 1.08 + 0.05 * p, fy=STONE_Y, rot=0.01 * math.sin(t))


def s24(t, d, ev):
    p = t / d
    # five names: lightning finds each stone in turn
    names = ev.get("names") or [d * (0.45 + 0.1 * k) for k in range(5)]
    lights = []
    flash = 0.0
    for k in range(5):
        dt = t - names[k]
        v = math.exp(-max(dt, 0) * 2.2) if dt >= 0 else 0.0
        v2 = 0.4 * math.exp(-max(dt - 0.35, 0) * 6) if dt >= 0.35 else 0.0
        lights.append(min(1.0, v + v2))
        if 0 <= dt < 0.25:
            flash = max(flash, 1 - dt / 0.25)
    img = stones_scene(t, 6.9, cloud=0.85, dark=0.85, swirl=0.8, lights=lights, scale=105)
    # the bolt for the most recent name
    last = max([k for k in range(5) if t >= names[k]], default=None)
    if last is not None and t - names[last] < 0.3:
        from .world import stone_positions

        px, pz = stone_positions()[last]
        persp = 1.0 / (1.0 - pz * 0.35)
        sx = STONE_X + px * 105 * 2.2 * persp
        sy = STONE_Y + pz * 105 * 0.9 - 105 * persp * 2.0
        bolt(img, sx, sy, seed=last * 7 + 1, a=1 - (t - names[last]) / 0.3)
    img += flash * 0.25 * hexc("#c8d0ff")
    img *= 1.3
    return camera(img, 1.13, fy=STONE_Y)


def bolt(img, x_end, y_end, seed=1, a=1.0):
    r = np.random.default_rng(seed)
    surf, ctx = cairo_canvas()

    def seg(x0, y0, x1, y1, w, depth):
        if depth == 0:
            ctx.set_line_width(w)
            ctx.move_to(x0, y0)
            ctx.line_to(x1, y1)
            ctx.stroke()
            return
        mx = (x0 + x1) / 2 + r.normal(0, abs(y1 - y0) * 0.12)
        my = (y0 + y1) / 2
        seg(x0, y0, mx, my, w, depth - 1)
        seg(mx, my, x1, y1, w, depth - 1)
        if depth == 3 and r.random() < 0.7:
            seg(mx, my, mx + r.normal(0, 120), my + abs(y1 - y0) * 0.3, w * 0.5, 2)

    x0 = x_end + r.normal(0, 200)
    for lw, al in ((10, 0.25 * a), (3.5, 0.95 * a)):
        rgb(ctx, hexc("#e8ecff"), al)
        r = np.random.default_rng(seed)
        seg(x0, -20, x_end, y_end, lw, 6)
    over(img, cairo_rgba(surf))
    glow(img, x_end, y_end, 140, hexc("#a0b0ff"), 0.5 * a)


def s25(t, d, ev):
    p = t / d
    # they have misled many: fog rolls in, the stones go dim
    img = stones_scene(t, 6.95, cloud=0.8, dark=0.85, fog=lerp(0.2, 1.2, ease(p)))
    img *= 1 - 0.35 * ease(p)
    img *= 1.3
    return camera(img, 1.13 - 0.05 * p, fy=STONE_Y)


# --------------------------------------------------------------------------
# 26-28  ayat 25-27: the flood, the storm, the stillness
# --------------------------------------------------------------------------


def s26(t, d, ev):
    p = t / d
    # drowned; and the Fire
    fire_t = ev_at(ev, "naran", 0.55 * d) / d
    tod = 6.95
    img = stones_scene(t, tod, cloud=0.9, dark=0.9, swirl=0.6, scale=100)
    skyimg = img.copy()
    # water rises from below the frame over the stones
    # first the plain floods to the stones' feet, then the water climbs them
    feet, tops = STONE_Y + 30, STONE_Y - 100 * 2.7
    if p < 0.12:
        lvl = lerp(H + 20, feet, ease(p / 0.12))
    else:
        lvl = lerp(feet, tops, ease(sm(0.12, fire_t, p)))
    # the Fire: a red glow under the water that swells on the word and ebbs
    red = 0.6 * sm(fire_t - 0.02, fire_t + 0.06, p) * (1 - 0.65 * sm(fire_t + 0.12, fire_t + 0.4, p))
    img = water(img, tod, lvl, t, sky_img=skyimg, rough=0.6, red=red)
    if red > 0:
        # a red glow over the water and on the clouds' undersides
        img += (np.exp(-((YS - lvl) / 260.0) ** 2) * red * 0.18)[..., None] * hexc("#ff4a1a")
    img = rain(img, t, lerp(0.6, 1.0, p), speed=1900, slant=0.18)
    img *= 1.35
    return camera(img, 1.1 - 0.06 * p, fy=STONE_Y, rot=0.012 * math.sin(t * 0.7))


def sea(t, tod, rough, rain_amt, flash=0.0, still=0.0, mist=0.0, cloud=0.9, dark=0.9):
    hz = int(H * 0.55)
    img = sky(tod, hz / H)
    clouds(img, t, tod, 0.0, 0.56, density=cloud, speed=18 * (1 - still) + 3, dark=dark)
    skyimg = img.copy()
    img = water(img, tod, hz, t, sky_img=skyimg, rough=rough, wave_amp=2 + 26 * rough)
    # rolling swells: dark ridges moving across
    zen, up, hor, gnd, amb = sky_colors(tod)
    if rough > 0.05:
        n = Noise1D(151)
        for k in range(7):
            z = (k + (t * 0.18 * rough) % 1) / 7
            y = hz + (H - hz) * z ** 1.6
            amp = (4 + 70 * z ** 1.5) * rough
            hts = y - amp * (0.5 + 0.5 * np.sin(XS[0] * (0.004 / (0.2 + z)) + t * (1.5 + k * 0.2) + k)) - amp * 0.6 * n(XS[0] / 300 + k * 13 + t * 0.3, octaves=3)
            c = mix(hexc("#0a1418"), hor * 0.4, 0.15 * (1 - z)) * (0.4 + 0.6 * amb)
            al = ridge_alpha_band(hts, y + amp * 1.5)
            img = img * (1 - al[..., None] * 0.8) + c * al[..., None] * 0.8
            # foam on the crest
            foam = np.exp(-((YS - hts[None, :]) / (1.5 + 3 * z)) ** 2) * smooth(0.5, 1.0, np.sin(XS * 0.01 + k + t)) * rough
            img += foam[..., None] * 0.35 * (0.5 + 0.5 * amb)
    img = rain(img, t, rain_amt, speed=2100, slant=0.25)
    if mist > 0:
        tex = fbm_tex(1024, 512, 161, base=3, octaves=3)
        f = tex_sample(tex, t * 8, 0, 0.6)
        ma = (smooth(0.2, 0.9, f) * np.exp(-((YS - hz) / 140.0) ** 2) * mist)[..., None]
        img = img * (1 - ma) + (hor * 0.7 + 0.04) * ma
    if flash:
        img += flash * 0.3 * hexc("#c8d0ff")
    return img


def ridge_alpha_band(top, bottom):
    a = np.clip((YS - top[None, :]) / 1.5 + 0.5, 0, 1)
    b = np.clip((bottom - YS) / 30.0, 0, 1)
    return (a * b).astype(np.float32)


def s27(t, d, ev):
    p = t / d
    # leave none standing: the storm at its height
    fl = 0.0
    for tf in (0.18, 0.47, 0.71):
        dt = p * d - tf * d
        if 0 <= dt < 0.4:
            fl = max(fl, 1 - dt / 0.4)
    img = sea(t, 6.95, rough=1.0, rain_amt=1.0, flash=fl)
    if fl > 0.3:
        bolt(img, W * (0.3 + 0.5 * ((int(p * 3.3) * 0.37) % 1)), int(H * 0.55), seed=int(p * 3.3) + 40, a=fl)
    return camera(img, 1.08, rot=0.02 * math.sin(t * 0.9), dy=10 * math.sin(t * 1.3))


def s28(t, d, ev):
    p = t / d
    # then everything goes still
    q = ease(p)
    tod = lerp(6.95, 7.0, q)
    img = sea(t, 0.15 + 0.6 * q, rough=lerp(0.8, 0.0, q), rain_amt=lerp(0.8, 0.0, ease(min(1, p * 1.6))), still=q,
              mist=q, cloud=lerp(0.85, 0.35, q), dark=lerp(0.85, 0.2, q))
    stars(img, 0.6 * q, t, int(H * 0.55))
    return camera(img, 1.08 - 0.05 * p, rot=0.02 * (1 - q) * math.sin(t * 0.9))


# --------------------------------------------------------------------------
# 29  ayah 28: one doorway lit gold; the water goes down; dawn
# --------------------------------------------------------------------------


def s29(t, d, ev):
    p = t / d
    hz = int(H * 0.6)
    tod = lerp(0.8, 2.9, ease(sm(0.15, 1.0, p)))
    img = sky(tod, hz / H)
    stars(img, star_alpha(tod), t, hz)
    sun_moon(img, tod, hz, moon=False)
    clouds(img, t, tod, 0.05, 0.5, density=lerp(0.45, 0.3, p), speed=4)
    zen, up, hor, gnd, amb = sky_colors(tod)
    land_layers(img, tod, hz, layers=[(0.0, 60, 30, 2.5, 1, "#4a4a5c"), (0.5, 20, 16, 5.0, 3, "#4a4030")])
    # the rise with the house
    hx = W * 0.6
    hts = hill_heights(hx, hz - 70, 520, seed=6, base=hz + 40)
    ridge(img, hts, hexc("#4a3c2c") * (0.25 + 0.75 * amb))
    top = float(hts[int(hx)])
    # house
    s = 1.0
    surf, ctx = cairo_canvas()
    wall = hexc("#8a6a4a") * (0.2 + 0.8 * amb)
    bx, by, bw, bh = hx - 120, top + 6, 240, 150
    rgb(ctx, wall * 0.62)
    ctx.move_to(bx + bw, by)
    ctx.line_to(bx + bw + 50, by - 22)
    ctx.line_to(bx + bw + 50, by - bh - 22)
    ctx.line_to(bx + bw, by - bh)
    ctx.close_path()
    ctx.fill()
    rgb(ctx, wall * 0.8)
    ctx.move_to(bx, by - bh)
    ctx.line_to(bx + 50, by - bh - 22)
    ctx.line_to(bx + bw + 50, by - bh - 22)
    ctx.line_to(bx + bw, by - bh)
    ctx.close_path()
    ctx.fill()
    rgb(ctx, wall)
    ctx.rectangle(bx, by - bh, bw, bh)
    ctx.fill()
    dw, dh = 54, 96
    dx = bx + bw / 2 - dw / 2
    door = sm(0.02, 0.2, p)
    breath = 0.92 + 0.08 * math.sin(t * 1.1)
    rgb(ctx, mix(wall * 0.3, GOLD * 1.1, door * breath))
    ctx.move_to(dx, by)
    ctx.line_to(dx, by - dh + dw / 2)
    ctx.arc(dx + dw / 2, by - dh + dw / 2, dw / 2, math.pi, 0)
    ctx.line_to(dx + dw, by)
    ctx.close_path()
    ctx.fill()
    over(img, cairo_rgba(surf))
    glow(img, dx + dw / 2, by - dh / 2, 260, GOLD, 0.35 * door * breath, kind="halo", reach=6)
    glow(img, dx + dw / 2, by - dh / 2, 50, GOLD, 0.5 * door * breath)
    # light spilling down the slope
    glow(img, dx + dw / 2, by + 40, 120, GOLD, 0.15 * door)
    # the water recedes: level drops from just below the house to the bottom
    skyimg = img.copy()
    lvl = lerp(top + 30, H * 0.95, ease(sm(0.1, 0.95, p)))
    img = water(img, tod, lvl, t, sky_img=skyimg, rough=0.05)
    # wet sheen where water was
    # final fade to black after the last word
    end = ev_at(ev, "end", d)
    img *= 1 - sm(end - 0.2, d, t) if end < d else 1.0
    return camera(img, 1.06 - 0.06 * ease(p), fx=hx, fy=top)


SHOTS = [s02, s03, s04, s05, s06, s07, s08, s09, s10, s11, s12, s13, s14, s15, s16, s17, s18, s19, s20, s21, s22, s23, s24, s25, s26, s27, s28, s29]

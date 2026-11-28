"""Render the seamless core loop of the leaf shot with Cycles.

    python -m nuh.cycles_loop --frames 0:192 [--samples 128] [--scale 100]

The camera never moves, so the film is one loop of LOOP_FRAMES core frames
cycled under the whole recitation. Every motion inside the loop is a
periodic function of loop time, so the last frame flows into the first:

  0.90 s  a raindrop strikes the leaf: the leaf jolts and rings down,
          a crown of droplets flies off, a fresh bead wobbles
  1.6-3.6 the bead slides to the tip and merges with the pendant drop
  5.60 s  the swollen pendant lets go and falls; the leaf springs up
  -> 8 s  the pendant refills to where it started; the leaf is at rest

Rain falls throughout on drops whose recurrence divides the loop period.
Each frame is written as linear .npy (for grading) under output/nuh/loop/.
"""

from __future__ import annotations

import argparse
import math
import os
import sys
import time
from pathlib import Path

import bpy
import numpy as np
from mathutils import Vector

from . import cycles_leaf as C
from .paint import smooth

FPS = 24
LOOP_S = 8.0
LOOP_FRAMES = int(LOOP_S * FPS)
OUT = Path(__file__).resolve().parent.parent / "output" / "nuh" / "loop"

T_IMP = 0.90
IMP_UV = (0.55, 0.35)
SLIDE = (1.6, 3.6)
T_REL = 5.6
OMEGA = 2 * math.pi * 4.2
ZETA = 0.11
WD = OMEGA * math.sqrt(1 - ZETA**2)
RAIN_V = 7.0  # m/s

# ---------------------------------------------------------------- the story, as functions of loop time


def tip_volume(t: float) -> float:
    """0..1.05; continuous and periodic."""
    if t < SLIDE[1]:
        return 0.45 + 0.25 * t / SLIDE[1]
    if t < T_REL:
        return 0.98 + 0.07 * (t - SLIDE[1]) / (T_REL - SLIDE[1])
    return 0.2 + 0.25 * (t - T_REL) / (LOOP_S - T_REL)


def ring(dt: float) -> float:
    return math.exp(-ZETA * OMEGA * dt) * math.sin(WD * dt) if dt >= 0 else 0.0


def deflection(t: float) -> float:
    """Tip deflection in metres (positive: the leaf bends down/away)."""
    d = 0.0011 * ring(t - T_IMP) - 0.0008 * ring(t - T_REL)
    d += 0.0011 * ring(t - T_IMP + LOOP_S) - 0.0008 * ring(t - T_REL + LOOP_S)  # tails from the previous loop
    d += 0.0006 * tip_volume(t)
    d += 0.00015 * math.sin(2 * math.pi * t / LOOP_S) * math.sin(2 * math.pi * 3 * t / LOOP_S + 1)  # breeze
    return d


# ---------------------------------------------------------------- the leaf surface, vectorised


def leaf_points(U, V, defl):
    b, ax, perp = C.leaf_frame()
    u = np.clip(U, 0, 1)
    x = u * 40
    i = np.floor(x).astype(int) % 511
    f = x - np.floor(x)
    f = f * f * (3 - 2 * f)
    en40 = C._EN[i] * (1 - f) + C._EN[i + 1] * f
    x2 = u * 200
    i2 = x2.astype(int) % 511
    nk = C._NK[i2] * np.sin(np.pi * (x2 - np.floor(x2)))
    av = np.abs(V)
    hw = C.half_width(u) * (1 + 0.012 * en40 * av**6 - 0.02 * nk * av**10)
    s = np.sin(np.pi * u)
    lift = -0.0012 * s
    cup = -0.0045 * V * V * (0.3 + 0.7 * s)
    curl = 0.0035 * smooth(0.78, 1.0, av) ** 2 * s
    turn = -0.008 * np.maximum(-V, 0) * s**0.5
    tilt = -0.006 * u
    sag = -defl * u**1.7
    x9 = u * 9 + 3
    i9 = np.floor(x9).astype(int) % 511
    f9 = x9 - np.floor(x9)
    f9 = f9 * f9 * (3 - 2 * f9)
    en9 = C._EN[i9] * (1 - f9) + C._EN[i9 + 1] * f9
    rib = 0.00035 * np.exp(-((V * hw) / 0.0007) ** 2) * (1 - 0.7 * u)
    wave = 0.0004 * av**4 * en9
    X = b[0] + ax[0] * u * C.LEAF_LEN + perp[0] * V * hw
    Y = b[1] + ax[1] * u * C.LEAF_LEN + perp[1] * V * hw + sag * 0.8
    Z = lift + cup + curl + turn + tilt + sag * 0.6 + rib + wave
    return np.stack([X, Y, Z], -1)


def point_normal(u, v, defl):
    e = 1e-3
    P = leaf_points(np.array([u, min(u + e, 1), u]), np.array([v, v, min(v + e, 1)]), defl)
    p = Vector(P[0])
    n = (Vector(P[1]) - p).cross(Vector(P[2]) - p).normalized()
    if n.z < 0:
        n = -n
    return p, n


# ---------------------------------------------------------------- scene state per frame


class Loop:
    def __init__(self, scale, samples):
        self.sc = C.build_scene(0.0, scale, samples)
        sc = self.sc
        sc.render.motion_blur_position = "START"
        self.leaf = bpy.data.objects["leaf"]
        me = self.leaf.data
        nv1 = 141
        nu1 = 421
        J, I = np.divmod(np.arange(len(me.vertices)), nu1)
        self.U = I / (nu1 - 1)
        self.Vv = -1 + 2 * J / (nv1 - 1)
        # resting drops: recover their (u, v) from the same seed build_scene used
        r = np.random.default_rng(6401)
        self.rest = []
        for k in range(40):
            u = float(r.uniform(0.12, 0.92))
            v = float(r.uniform(-0.8, 0.85))
            rad = (float(r.random()) ** 2.6) * 0.0021 + 0.00025
            self.rest.append((bpy.data.objects[f"d{k}"], u, v, rad))
        self.tip = bpy.data.objects["tip"]
        self.wm = bpy.data.materials["water"]
        # dynamic water
        self.bead = C.drop("bead", Vector((0, 0, 10)), Vector((0, 0, 1)), 0.0016, self.wm, flat=0.75)
        self.falling = C.drop("falling", Vector((0, 0, 10)), Vector((0, -1, 0)), 0.0015, self.wm, flat=1.1)
        self.strike = C.drop("strike", Vector((0, 0, 10)), Vector((0, 0, 1)), 0.0014, self.wm, flat=1.0)
        rs = np.random.default_rng(7101)
        self.crown = []
        for k in range(14):
            o = C.drop(f"crown{k}", Vector((0, 0, 10)), Vector((0, 0, 1)), float(rs.uniform(0.00022, 0.0005)), self.wm, flat=1.0)
            ang = float(rs.uniform(-math.pi * 0.95, -math.pi * 0.05))  # thrown up and out (screen up is +y)
            spd = float(rs.uniform(0.35, 1.2))
            out = float(rs.uniform(0.0, 0.6))  # toward the lens
            self.crown.append((o, ang, spd, out))
        # the kicker lights only the pendant and the falling drop (light linking)
        rec = bpy.data.collections.new("kick_receivers")
        for o in (self.tip, self.falling):
            rec.objects.link(o)
        bpy.data.objects["kicker"].light_linking.receiver_collection = rec
        # rain: periodic recurrences
        self.rain = []
        rr = np.random.default_rng(7201)
        for o in [ob for ob in bpy.data.objects if ob.name.startswith("rain")]:
            o.animation_data_clear()
            z = o.location.z
            k = int(rr.integers(6, 40))
            self.rain.append((o, z, k, float(rr.uniform(0, LOOP_S)), int(rr.integers(1 << 30))))

    def rain_pos(self, o, z, k, phase, seed, t):
        per = LOOP_S / k
        q = (t + phase) / per
        n = int(math.floor(q)) % k
        age = (q - math.floor(q)) * per
        span_x = (C.CAM_Z - z) * C.HFOV_T * 1.1
        span_y = (C.CAM_Z - z) * C.VFOV_T * 1.3
        h = np.random.default_rng(seed + n * 7919)
        x = float(h.uniform(-span_x, span_x))
        y = span_y - RAIN_V * age
        if y < -span_y - 0.05:
            return None
        return Vector((x + 0.02 * RAIN_V * age, y, z))

    def key(self, o, p0, p1, f):
        far = Vector((0, 0, 50))
        o.location = p0 if p0 is not None else far
        o.keyframe_insert("location", frame=f)
        o.location = p1 if p1 is not None else far
        o.keyframe_insert("location", frame=f + 1)

    def set_frame(self, fi):
        t = fi / FPS
        dt = 1 / FPS
        f = fi + 10
        defl = deflection(t)
        # leaf
        P = leaf_points(self.U, self.Vv, defl).astype(np.float32)
        self.leaf.data.vertices.foreach_set("co", P.ravel())
        self.leaf.data.update()
        shake = math.exp(-max(t - T_IMP, 0) * 5) * (t >= T_IMP)
        for o, u, v, rad in self.rest:
            p, n = point_normal(u, v, defl)
            flat = 0.62 + 0.25 * (1 - min(rad / 0.0022, 1))
            w = 1 + 0.08 * shake * math.sin((t - T_IMP) * 70 + u * 20)
            o.location = p + n * rad * flat * 0.32
            o.rotation_quaternion = Vector((0, 0, 1)).rotation_difference(n)
            o.scale = (w * (1.0 + 0.15 * min(rad / 0.002, 1)), w, flat / w)
        # the pendant
        vol = tip_volume(t)
        tp, tn = point_normal(1.0, 0.0, defl)
        trad = 0.0008 + 0.0011 * vol ** (1 / 3)
        self.tip.location = tp + Vector((0.0004, -trad * 0.8, 0.0004))
        self.tip.scale = (trad / 0.0017, trad / 0.0017 * (1 + 0.25 * vol), trad / 0.0017)
        # the strike: one exposure, a streak ending on the leaf
        ip, inn = point_normal(*IMP_UV, deflection(T_IMP))
        if T_IMP - 2 * dt <= t < T_IMP:
            self.key(self.strike, ip + Vector((0, RAIN_V * (T_IMP - t), 0.001)), ip + Vector((0, RAIN_V * (T_IMP - t - dt / 2), 0.001)), f)
        else:
            self.key(self.strike, None, None, f)
        # crown droplets
        g = 9.81
        for o, ang, spd, out in self.crown:
            a0 = t - T_IMP
            if 0 <= a0 < 0.25:
                def cp(a):
                    return ip + Vector((math.cos(ang) * spd * a * 0.6, -math.sin(ang) * spd * a - 0.5 * g * a * a, out * spd * a))
                self.key(o, cp(a0), cp(a0 + dt), f)
            else:
                self.key(o, None, None, f)
        # the new bead: appears, wobbles, slides to the tip, merges
        a0 = t - T_IMP
        if 0 <= a0 and t < SLIDE[1]:
            q = float(np.clip((t - SLIDE[0]) / (SLIDE[1] - SLIDE[0]), 0, 1))
            q = q * q * (3 - 2 * q)
            u = IMP_UV[0] + (0.985 - IMP_UV[0]) * q
            v = IMP_UV[1] * (1 - q) ** 0.7
            p, n = point_normal(u, v, defl)
            grow = smooth(0.0, 0.06, a0)
            wob = math.exp(-a0 * 4) * math.sin(a0 * 45)
            rad = 0.0016 * (0.5 + 0.5 * grow) * (1 - 0.25 * q)
            self.bead.location = p + n * rad * 0.75 * 0.32
            self.bead.rotation_quaternion = Vector((0, 0, 1)).rotation_difference(n)
            sx = rad / 0.0016 * (1 + 0.12 * wob + 0.3 * q)
            self.bead.scale = (sx, rad / 0.0016 * (1 - 0.1 * wob), 0.75 * rad / 0.0016)
        else:
            self.bead.location = (0, 0, 50)
        # the pendant lets go
        a0 = t - T_REL
        if 0 <= a0 < 0.12:
            def fp(a):
                return tp + Vector((0.0004, -0.0026 - 0.5 * g * a * a, 0.0004))
            self.key(self.falling, fp(a0), fp(a0 + dt), f)
        else:
            self.key(self.falling, None, None, f)
        # rain
        for o, z, k, phase, seed in self.rain:
            p0 = self.rain_pos(o, z, k, phase, seed, t)
            p1 = None if p0 is None else p0 + Vector((0.02 * RAIN_V * dt, -RAIN_V * dt, 0))
            self.key(o, p0, p1, f)
        self.sc.frame_set(f)


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--frames", default=f"0:{LOOP_FRAMES}")
    ap.add_argument("--samples", type=int, default=128)
    ap.add_argument("--scale", type=int, default=100)
    a = ap.parse_args(argv)
    a0, a1 = map(int, a.frames.split(":"))
    OUT.mkdir(parents=True, exist_ok=True)
    loop = Loop(a.scale, a.samples)
    for fi in range(a0, a1):
        npy = OUT / f"f{fi:04d}.npy"
        if npy.exists():
            continue
        t0 = time.time()
        loop.set_frame(fi)
        exr = OUT / f"f{fi:04d}.exr"
        loop.sc.render.filepath = str(exr)
        bpy.ops.render.render(write_still=True)
        C.exr_to_npy(str(exr), str(npy))
        os.remove(exr)
        print(f"loop frame {fi} in {time.time() - t0:.0f}s", flush=True)


if __name__ == "__main__":
    main(sys.argv[1:])

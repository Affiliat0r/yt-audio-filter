"""The leaf in the rain, built and run in Blender.

    python -m nuh.blend_shot --save                     # build output/nuh/blend/nuh_leaf.blend
    python -m nuh.blend_shot --raw --frames 0:192       # the loop as linear EXR, for PhotoCraft
    python -m nuh.blend_shot --still 60                 # one frame with Blender's own preview grade
    python -m nuh.blend_shot --mechanics                # print the physics numbers

Blender does the scene, the motion (keyframes on a hinge, the drops and a
shape-keyed hanging drop, every value from the physics in nuh.drops), the
rain and the splash (Blender particle simulations under real gravity) and
the optics (Cycles' thin-lens camera). With --raw it writes each frame as
linear 32-bit EXR and stops there: PhotoCraft develops it (nuh.photocraft).
Without --raw, a preview grade in Blender's compositor is applied. The saved
.blend opens and plays in Blender as it is.

World axes are Blender's: z up, gravity -z, metres. The geometry is written
in a frame where y is up and the camera looks down -z, then hung under one
root empty turned 90 degrees, so both read the same.

The loop's story (8 s on screen, 4x slow motion: 2 s of real time, seamless):
  ~1.0 s  a raindrop strikes the leaf: the stalk takes its momentum and the
          leaf swings and rings down; spray flies off; a bead is left
  then    the bead slides down the steep leaf to its lower edge and along it
          to the tip, jerking where veins pin it
  merge   the hanging drop, already near the largest shape surface tension
          can hold, takes the bead, necks and lets go; 62 % falls, the rest
          stays; the leaf, lighter at its tip, springs up
  rest    water running down the leaf from above (a film too thin to see)
          swells the hanging drop back to where the loop began
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path

import bpy
import cv2
import numpy as np
from mathutils import Matrix, Quaternion, Vector

from . import cycles_leaf as C
from . import drops as W
from .paint import smooth

ROOT_DIR = Path(__file__).resolve().parent.parent
OUT = ROOT_DIR / "output" / "nuh"
BLEND_DIR = OUT / "blend"
BLEND = BLEND_DIR / "nuh_leaf.blend"
FRAMES = OUT / "frames"
RAW = OUT / "raw"  # linear EXR straight from Cycles

FPS = 24
LOOP_S = 8.0
LOOP_FRAMES = int(LOOP_S * FPS)
SLOW = 4  # shot at 96 fps, played at 24: one loop is 2 s of real time
FPSP = FPS * SLOW  # frames per second of real (physical) time
LOOP_P = LOOP_S / SLOW  # the loop in physical seconds
F0 = 1  # scene frame of loop frame 0

# ---------------------------------------------------------------- where the leaf hangs
H = np.array([-0.0585, 0.03, -0.0095])  # where the stalk leaves the branch
STEEPEN = math.radians(34.0)  # the leaf hangs a little steeper than the old frame
TILT = math.radians(50.0)  # its face turned up to the sky, so rain can land on it
TIP_PX = (1185, 650)  # where the tip sits in the frame
MOON_PX = (1235, 735)  # the moon sits behind the hanging drop: the drop shows it refracted, and falls across it
FSTOP = 8.0  # stopped down from 5.6: tighter bokeh, so rain behind the leaf reads
SHUTTER = 0.25  # of a frame: 90 degrees at 96 fps, 1/384 s

# ---------------------------------------------------------------- water
THETA = math.radians(95)  # contact angle on the waxy leaf
TIP_RC = 0.0006  # contact radius of the hanging drop, around the drip tip
TIP_U = 0.985  # it hangs from 1.3 mm up the tip: the tip's end is inside the drop
NECK_S = 0.03  # past critical, the drop stretches and necks for this long (s) before it snaps
NECK_A = 0.5 * 9.81  # how fast the bulb pulls away while it necks (m/s^2)
RAIN_V = 6.8  # a raindrop's terminal speed, m/s
RAIN_RATE = 2000.0  # drops per m^2 per s through a horizontal plane: a downpour, ~40 mm/h
SPLASH_KEEP = 0.6  # share of it left on the leaf as a bead
LEAF_AREAL = 0.19  # leaf mass per area, kg/m^2 (0.25 mm thick)
LEAF_FREQ = 1.8  # Hz, the leaf swinging on its stalk
LEAF_ZETA = 0.06
# raindrops that strike the leaf in one loop: loop frame (it lands a quarter frame in),
# where on the leaf (u along, v across), and its radius. Each leaves a bead that runs to the tip;
# the last bead takes the hanging drop past what surface tension can hold.
STRIKES = [
    dict(frame=23, uv=(0.82, 0.25), r=0.0011),
    dict(frame=120, uv=(0.90, 0.30), r=0.0010),
]


def rot(axis, ang):
    a = np.asarray(axis, float)
    a = a / np.linalg.norm(a)
    K = np.array([[0, -a[2], a[1]], [a[2], 0, -a[0]], [-a[1], a[0], 0]])
    return np.eye(3) + math.sin(ang) * K + (1 - math.cos(ang)) * K @ K


_b, _ax, _perp = C.leaf_frame()
_RZ = rot((0, 0, 1), -STEEPEN)
R0 = rot(_RZ @ np.array([_ax[0], _ax[1], 0.0]), -TILT) @ _RZ


# ---------------------------------------------------------------- the leaf surface


def leaf_old(U, V):
    """The leaf at rest in its build frame (y up, camera on +z), as in nuh.cycles_leaf."""
    b, ax, perp = _b, _ax, _perp
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
    x9 = u * 9 + 3
    i9 = np.floor(x9).astype(int) % 511
    f9 = x9 - np.floor(x9)
    f9 = f9 * f9 * (3 - 2 * f9)
    en9 = C._EN[i9] * (1 - f9) + C._EN[i9 + 1] * f9
    rib = 0.00035 * np.exp(-((V * hw) / 0.0007) ** 2) * (1 - 0.7 * u)
    wave = 0.0004 * av**4 * en9
    X = b[0] + ax[0] * u * C.LEAF_LEN + perp[0] * V * hw
    Y = b[1] + ax[1] * u * C.LEAF_LEN + perp[1] * V * hw
    Z = lift + cup + curl + turn + tilt + rib + wave
    return np.stack([X, Y, Z], -1)


def local(U, V):
    """Hinge-local position: what the leaf's vertices are under the hinge."""
    return leaf_old(U, V) - H


def world(U, V, R=R0):
    """Position in the y-up frame for a hinge rotation R."""
    return H + local(U, V) @ R.T


def frame_at(u, v, R=R0):
    """Point, unit normal (toward the camera side) and tangents at (u, v)."""
    e = 1e-4
    p = world(np.array(u), np.array(v), R)
    pu = (world(np.array(min(u + e, 1.0)), np.array(v), R) - world(np.array(max(u - e, 0.0)), np.array(v), R)) / (min(u + e, 1.0) - max(u - e, 0.0))
    pv = (world(np.array(u), np.array(min(v + e, 1.0)), R) - world(np.array(u), np.array(max(v - e, -1.0)), R)) / (min(v + e, 1.0) - max(v - e, -1.0))
    n = np.cross(pu, pv)
    n /= np.linalg.norm(n)
    if n[2] < 0:
        n = -n
    return p, n, pu, pv


DOWN = np.array([0.0, -1.0, 0.0])


# ---------------------------------------------------------------- the camera


def camera_xy():
    tip = world(np.array(1.0), np.array(0.0))
    d = C.CAM_Z - tip[2]
    sx = (TIP_PX[0] - 960) / 960 * C.HFOV_T * d
    sy = (540 - TIP_PX[1]) / 540 * C.VFOV_T * d
    return float(tip[0] - sx), float(tip[1] - sy)


# ---------------------------------------------------------------- mechanics


def bead_path(u0, v0, ds=5e-5):
    """Steepest descent over the leaf from (u0, v0); then along the lower
    edge, downhill, to the tip. Returns a list of (u, v)."""
    u, v = u0, v0
    pts = [(u, v)]
    for _ in range(4000):
        p, n, pu, pv = frame_at(u, v)
        gt = DOWN - DOWN.dot(n) * n
        J = np.stack([pu, pv], 1)
        d, *_ = np.linalg.lstsq(J, gt, rcond=None)
        step = ds / max(np.linalg.norm(J @ d), 1e-12)
        u, v = u + d[0] * step, v + d[1] * step
        pts.append((u, v))
        if abs(v) >= 0.94 or u >= 0.995:
            break
    if u < 0.995:  # along the edge to the tip, the edge's lowest point (asserted)
        sgn = math.copysign(1.0, v)
        uu = np.linspace(u, 0.996, max(int((0.996 - u) / 0.0008), 2))
        vv = np.full_like(uu, sgn * 0.94)
        y = world(uu, vv)[:, 1]
        drop_ = y[0] - y
        assert drop_[-1] > 0 and np.all(drop_[len(drop_) // 3 :] > -2e-4), "the tip is not downhill along the edge"
        pts += list(zip(uu.tolist(), vv.tolist())) + [(1.0, 0.0)]
    return pts


def strike_slide(uv, v_bead, seed):
    """A bead's path from the strike to the hanging drop, and its slide along it."""
    path = bead_path(*uv)
    P = np.array([world(np.array(u), np.array(v)) for u, v in path])
    seg = np.linalg.norm(np.diff(P, axis=0), axis=1)
    s_at = np.concatenate([[0], np.cumsum(seg)])
    L = float(s_at[-1])
    gal = np.concatenate([[-W.G * (P[1, 1] - P[0, 1]) / max(seg[0], 1e-9)], -W.G * np.diff(P[:, 1]) / np.maximum(seg, 1e-9)])
    on_edge = np.array([abs(v) > 0.9 or u > 0.97 for u, v in path], float)
    veins = np.random.default_rng(seed).uniform(-1, 1, 400)

    def along_g(s):
        return float(np.interp(s, s_at, gal))

    def hyst(s):
        k = s / 0.0014
        i = int(k) % 399
        f = k - int(k)
        nz = veins[i] * (1 - f) + veins[i + 1] * f
        base = np.interp(s, s_at, 5.2 - 1.9 * on_edge)
        return min(base * (1 + 0.32 * nz), 0.8 * along_g(s))

    reach = L - 0.0025  # it merges when it touches the hanging drop
    ts, ss = W.slide(reach, along_g, start_speed=0.06, hyst=hyst, drag=160.0)
    return dict(path=path, P=P, s_at=s_at, L=L, ts=ts, ss=ss, v=v_bead)


def mechanics(verbose=False):
    """Every number the loop moves by. Times are physical seconds into the loop."""
    strikes = []
    for i, sk in enumerate(STRIKES):
        v_rain = 4 / 3 * math.pi * sk["r"] ** 3
        t_imp = (sk["frame"] + 0.25) / FPSP
        b = strike_slide(sk["uv"], SPLASH_KEEP * v_rain, 8101 + i)
        p, n, _, _ = frame_at(*sk["uv"])
        b.update(t_imp=t_imp, t_merge=t_imp + float(b["ts"][-1]), v_rain=v_rain, r=sk["r"], p_imp=p, n_imp=n, frame=sk["frame"])
        strikes.append(b)
    last = max(strikes, key=lambda b: b["t_merge"])
    t_c = last["t_merge"]  # the hanging drop passes critical
    # it snaps about NECK_S later; the instant is put where the shutter is closed (the next 0.6 of a
    # frame), so no exposure straddles it
    t_b = (math.floor((t_c + NECK_S) * FPSP) + 0.6) / FPSP
    fam = W.pendant_family(TIP_RC)
    v_crit = fam[-1][0]
    v_total = v_crit + 0.6 * last["v"]
    v_fall = W.FALL_FRACTION * v_total
    v_rest = v_total - v_fall
    others = sum(b["v"] for b in strikes if b is not last)
    q = (v_crit - 0.4 * last["v"] - others - v_rest) / (LOOP_P - (t_b - t_c))  # runoff from higher up the leaf
    assert q >= 0, "the beads alone overfill the hanging drop"

    def pendant_v(t):
        """Volume hanging at the tip; during the neck, the whole drop."""
        t = t % LOOP_P
        if t_c <= t < t_b:
            return v_total
        since = (t - t_b) % LOOP_P
        v = v_rest + q * since
        for b in strikes:
            if b is not last and (b["t_merge"] - t_b) % LOOP_P <= since:
                v += b["v"]
        return v

    def bead_s(b, t):
        t = t % LOOP_P
        if t < b["t_imp"] or t >= b["t_merge"]:
            return None
        return float(np.interp(t - b["t_imp"], b["ts"], b["ss"]))

    for b in strikes:
        if b is not last:
            assert pendant_v(b["t_merge"] + 1e-4) < v_crit, "an earlier bead would already take it past critical"
    # the leaf on its stalk
    U, V = np.meshgrid(np.linspace(0, 1, 161), np.linspace(-1, 1, 81))
    Pg = world(U, V)
    du, dv = 1 / 160, 2 / 80
    Pu = np.gradient(Pg, du, axis=1)
    Pv = np.gradient(Pg, dv, axis=0)
    dA = np.linalg.norm(np.cross(Pu, Pv), axis=-1) * du * dv
    dm = dA * LEAF_AREAL
    centroid = (Pg * dm[..., None]).sum((0, 1)) / dm.sum()
    _, n_c, _, _ = frame_at(0.55, 0.0)
    A = np.cross(centroid - H, n_c)
    A /= np.linalg.norm(A)
    r = Pg - H
    rperp = r - (r @ A)[..., None] * A
    inertia = float((dm * (rperp**2).sum(-1)).sum())
    tip = world(np.array(TIP_U), np.array(0.0))

    def torque_of(m, p):
        return float(A @ np.cross(p - H, m * W.G * DOWN))

    def torque(t):
        tq = torque_of(W.RHO * pendant_v(t), tip)
        for b in strikes:
            s = bead_s(b, t)
            if s is not None:
                tq += torque_of(W.RHO * b["v"], b["P"][min(np.searchsorted(b["s_at"], s), len(b["P"]) - 1)])
        return tq

    tq_mean = float(np.mean([torque(t) for t in np.linspace(0, LOOP_P, 1601)[:-1]]))
    kicks = []
    for b in strikes:
        vel = RAIN_V * DOWN
        vn = vel.dot(b["n_imp"]) * b["n_imp"]
        J = W.RHO * b["v_rain"] * (vn + 0.3 * (vel - vn))  # momentum the leaf takes: all the normal part, some of the rest
        b["kick"] = float(A @ np.cross(b["p_imp"] - H, J))
        kicks.append((b["t_imp"], b["kick"]))
    times = np.arange(LOOP_FRAMES + 1) / FPSP
    fine = np.arange(LOOP_FRAMES * 4 + 1) / (FPSP * 4)  # quarter frames, for the motion-blur keys
    th, k = W.swing(fine, inertia, LEAF_FREQ, LEAF_ZETA, lambda t: torque(t) - tq_mean, kicks)
    mech = dict(
        strikes=strikes, t_c=t_c, t_b=t_b, neck=t_b - t_c, v_crit=v_crit, v_total=v_total, v_fall=v_fall, v_rest=v_rest, q=q, fam=fam,
        A=A, inertia=inertia, k=k, theta=th, fine=fine, times=times, pendant_v=pendant_v, bead_s=bead_s,
    )
    if verbose:
        tipdisp = np.abs(th) * np.linalg.norm(tip - H)
        for i, b in enumerate(strikes):
            inc = math.degrees(math.acos(abs(b["n_imp"] @ DOWN)))
            print(f"strike {i}: {b['v_rain'] * 1e9:.1f} mm3 at {RAIN_V} m/s at {b['t_imp']:.2f} s (screen {b['t_imp'] * SLOW:.1f} s), {inc:.0f} deg off the normal; "
                  f"bead {b['v'] * 1e9:.2f} mm3 runs {b['L'] * 1e3:.1f} mm, merges at {b['t_merge']:.2f} s (screen {b['t_merge'] * SLOW:.1f} s)")
        print(f"hanging drop: critical {v_crit * 1e9:.2f} mm3, taken to {v_total * 1e9:.2f}; necks {(t_b - t_c) * 1e3:.0f} ms and snaps at {t_b:.2f} s (screen {t_b * SLOW:.1f} s); "
              f"falls {v_fall * 1e9:.2f}, stays {v_rest * 1e9:.2f}; runoff {q * 1e9:.2f} mm3/s")
        print(f"leaf: inertia {inertia:.3g} kg m2, stiffness {k:.3g} N m/rad, mass {dm.sum() * 1e3:.2f} g")
        print(f"swing: max {math.degrees(np.abs(th).max()):.2f} deg, tip moves up to {tipdisp.max() * 1e3:.2f} mm; loop end {math.degrees(th[-1]):.3f} vs start {math.degrees(th[0]):.3f}")
        for lab, t0 in [(f"strike {i}", b["t_imp"]) for i, b in enumerate(strikes)] + [("snap", t_b)]:
            w = (fine >= t0) & (fine < t0 + 0.6)
            print(f"  after {lab}: tip swings {np.ptp(th[w]) * np.linalg.norm(tip - H) * 1e3:.2f} mm peak to peak")
    return mech


# ---------------------------------------------------------------- textures, written once as files Blender loads


def textures():
    tex = BLEND_DIR / "tex"
    meta = tex / "meta.json"
    if not meta.exists():
        tex.mkdir(parents=True, exist_ok=True)
        veins, height, trans, front = C.leaf_textures()
        lo, hi = float(height.min()), float(height.max())
        for name, a in (("leaf_trans", trans), ("leaf_front", front), ("leaf_height", (height - lo) / (hi - lo))):
            a = np.clip(a, 0, 1)
            if a.ndim == 2:
                a = np.repeat(a[..., None], 3, 2)
            cv2.imwrite(str(tex / f"{name}.png"), cv2.cvtColor((a * 65535 + 0.5).astype(np.uint16), cv2.COLOR_RGB2BGR))
        meta.write_text(json.dumps({"height_range": hi - lo}))
    return tex, json.loads(meta.read_text())


def load_image(path):
    img = bpy.data.images.load(str(path))
    img.colorspace_settings.name = "Non-Color"
    return img


def leaf_material():
    tex, meta = textures()
    m = bpy.data.materials.new("leaf")
    m.use_nodes = True
    nt = m.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    ts = {}
    for name in ("leaf_trans", "leaf_front", "leaf_height"):
        t = nt.nodes.new("ShaderNodeTexImage")
        t.image = load_image(tex / f"{name}.png")
        t.interpolation = "Cubic"
        ts[name] = t
    bump = nt.nodes.new("ShaderNodeBump")
    bump.inputs["Strength"].default_value = 1.0
    bump.inputs["Distance"].default_value = 0.00015 * meta["height_range"]
    nt.links.new(ts["leaf_height"].outputs["Color"], bump.inputs["Height"])
    pr = nt.nodes.new("ShaderNodeBsdfPrincipled")
    nt.links.new(ts["leaf_front"].outputs["Color"], pr.inputs["Base Color"])
    pr.inputs["Roughness"].default_value = 0.28
    pr.inputs["Coat Weight"].default_value = 1.0
    pr.inputs["Coat Roughness"].default_value = 0.13
    nt.links.new(bump.outputs["Normal"], pr.inputs["Normal"])
    nt.links.new(bump.outputs["Normal"], pr.inputs["Coat Normal"])
    tr = nt.nodes.new("ShaderNodeBsdfTranslucent")
    nt.links.new(ts["leaf_trans"].outputs["Color"], tr.inputs["Color"])
    nt.links.new(bump.outputs["Normal"], tr.inputs["Normal"])
    mix = nt.nodes.new("ShaderNodeMixShader")
    mix.inputs["Fac"].default_value = 0.38
    nt.links.new(pr.outputs["BSDF"], mix.inputs[1])
    nt.links.new(tr.outputs["BSDF"], mix.inputs[2])
    nt.links.new(mix.outputs["Shader"], out.inputs["Surface"])
    return m


# ---------------------------------------------------------------- building


def link(o, parent=None):
    bpy.context.scene.collection.objects.link(o)
    if parent is not None:
        o.parent = parent
    return o


def mesh_object(name, verts, faces, mat, parent=None, smooth_=True):
    me = bpy.data.meshes.new(name)
    me.from_pydata([tuple(map(float, v)) for v in verts], [], faces)
    if smooth_:
        for p in me.polygons:
            p.use_smooth = True
    me.materials.append(mat)
    o = bpy.data.objects.new(name, me)
    o.visible_shadow = False  # water on the front of the leaf must not shade the light coming through it
    return link(o, parent)


def sphere_mesh(name, r, segs=32, rings=16):
    verts = [(0, 0, r)]
    for i in range(1, rings):
        a = math.pi * i / rings
        for j in range(segs):
            f = 2 * math.pi * j / segs
            verts.append((r * math.sin(a) * math.cos(f), r * math.sin(a) * math.sin(f), r * math.cos(a)))
    verts.append((0, 0, -r))
    faces = [(0, 1 + j, 1 + (j + 1) % segs) for j in range(segs)]
    for i in range(rings - 2):
        r0, r1 = 1 + i * segs, 1 + (i + 1) * segs
        faces += [(r0 + j, r1 + j, r1 + (j + 1) % segs, r0 + (j + 1) % segs) for j in range(segs)]
    last, c = 1 + (rings - 2) * segs, len(verts) - 1
    faces += [(last + j, c, last + (j + 1) % segs) for j in range(segs)]
    return verts, faces


def surface_rotation(n, downhill):
    """Rotation taking local z to n and local x to the downhill direction."""
    z = Vector(n).normalized()
    x = Vector(downhill) - z * Vector(downhill).dot(z)
    if x.length < 1e-9:
        x = z.orthogonal()
    x.normalize()
    y = z.cross(x)
    return Matrix((x, y, z)).transposed().to_quaternion()


def local_frame(u, v):
    """Hinge-local point, normal and downhill direction (for the rest pose)."""
    p, n, _, _ = frame_at(u, v)
    down_l = R0.T @ DOWN
    n_l = R0.T @ n
    return p, local(np.array(u), np.array(v)), n_l, down_l - down_l.dot(n_l) * n_l, n


def build(samples=96, scale=100):
    C.clear()
    sc = bpy.context.scene
    sc.render.engine = "CYCLES"
    sc.cycles.device = "CPU"
    sc.cycles.samples = samples
    sc.cycles.use_denoising = True
    sc.cycles.denoiser = "OPENIMAGEDENOISE"
    sc.cycles.denoising_prefilter = "ACCURATE"
    sc.cycles.denoising_input_passes = "RGB_ALBEDO_NORMAL"
    sc.cycles.max_bounces = 16
    sc.cycles.transmission_bounces = 16
    sc.cycles.glossy_bounces = 6
    sc.cycles.caustics_refractive = True
    sc.cycles.caustics_reflective = True
    sc.cycles.blur_glossy = 0.3
    sc.render.resolution_x, sc.render.resolution_y = 1920, 1080
    sc.render.resolution_percentage = scale
    sc.render.fps = FPS
    sc.render.use_motion_blur = True
    sc.render.motion_blur_shutter = SHUTTER
    sc.render.motion_blur_position = "START"
    sc.frame_start, sc.frame_end = F0, F0 + LOOP_FRAMES - 1
    sc.gravity = (0, 0, -W.G)
    sc.view_settings.view_transform = "AgX"
    sc.view_settings.look = "AgX - Medium High Contrast"
    sc.view_settings.exposure = 0.9
    sc.render.image_settings.file_format = "PNG"
    sc.render.image_settings.color_depth = "16"
    sc.render.image_settings.color_mode = "RGB"

    root = bpy.data.objects.new("root", None)  # y-up build frame -> Blender's z-up
    root.rotation_euler = (math.pi / 2, 0, 0)
    link(root)

    cx, cy = camera_xy()
    cam_o = Vector((cx, cy, C.CAM_Z))
    md = C.ndc_dir(*MOON_PX)  # the moon's direction from the camera, build frame
    md_world = Vector((md.x, -md.z, md.y))

    # world: a dim night sky, brighter toward the moon
    w = bpy.data.worlds.new("night")
    sc.world = w
    w.use_nodes = True
    wnt = w.node_tree
    bg = wnt.nodes["Background"]
    tc = wnt.nodes.new("ShaderNodeTexCoord")
    dot = wnt.nodes.new("ShaderNodeVectorMath")
    dot.operation = "DOT_PRODUCT"
    dot.inputs[1].default_value = tuple(md_world)
    wnt.links.new(tc.outputs["Generated"], dot.inputs[0])
    mx_ = wnt.nodes.new("ShaderNodeMath")
    mx_.operation = "MAXIMUM"
    mx_.inputs[1].default_value = 0.0
    pw = wnt.nodes.new("ShaderNodeMath")
    pw.operation = "POWER"
    pw.inputs[1].default_value = 60.0
    wnt.links.new(dot.outputs["Value"], mx_.inputs[0])
    wnt.links.new(mx_.outputs[0], pw.inputs[0])
    mixc = wnt.nodes.new("ShaderNodeMix")
    mixc.data_type = "RGBA"
    mixc.inputs["A"].default_value = (0.0025, 0.0045, 0.012, 1)
    mixc.inputs["B"].default_value = (0.03, 0.045, 0.08, 1)
    wnt.links.new(pw.outputs[0], mixc.inputs["Factor"])
    wnt.links.new(mixc.outputs["Result"], bg.inputs["Color"])

    # camera: 100 mm macro, focused on the bead's path
    mech = mechanics()
    path0 = mech["strikes"][0]["path"]
    mid_u, mid_v = path0[len(path0) // 2]
    focus_pt = world(np.array(mid_u), np.array(mid_v))
    cam_d = bpy.data.cameras.new("macro")
    cam_d.lens = C.FOCAL_MM
    cam_d.sensor_width = C.SENSOR_MM
    cam_d.sensor_fit = "HORIZONTAL"
    cam_d.clip_start = 0.01
    cam_d.clip_end = 500
    cam_d.dof.use_dof = True
    cam_d.dof.focus_distance = float(C.CAM_Z - focus_pt[2])
    cam_d.dof.aperture_fstop = FSTOP
    cam_d.dof.aperture_blades = 0
    cam = link(bpy.data.objects.new("cam", cam_d), root)
    cam.location = cam_o
    sc.camera = cam

    # the moon, at its true angular size
    moon_r = 120.0 * math.tan(math.radians(0.26))
    mv, mf = sphere_mesh("moon", moon_r, 192, 96)
    moon = mesh_object("moon", mv, mf, C.simple_material("moon", None, emission=(0.82, 0.88, 1.0), strength=14.0), root)
    moon.location = cam_o + md * 120.0
    sun_d = bpy.data.lights.new("moonlight", "SUN")
    sun_d.energy = 3.0
    sun_d.color = (0.78, 0.86, 1.0)
    sun_d.angle = math.radians(0.52)
    sun = link(bpy.data.objects.new("moonlight", sun_d), root)
    sun.rotation_mode = "QUATERNION"
    sun.rotation_quaternion = Vector((0, 0, 1)).rotation_difference(md)
    # a streetlamp 2.5 m behind the tree, off frame upper left. The tree's canopy shades the leaf and
    # the water around it from the lamp (light linking stands in for that shadow): it lights the rain.
    st_d = bpy.data.lights.new("streetlamp", "AREA")
    st_d.energy, st_d.size, st_d.color = 20000.0, 0.3, (1.0, 0.78, 0.5)
    street = link(bpy.data.objects.new("streetlamp", st_d), root)
    street.location = (cx - 0.9, cy + 0.7, -2.5)
    street.rotation_mode = "QUATERNION"
    street.rotation_quaternion = Vector((0, 0, 1)).rotation_difference((street.location - Vector((cx, cy, -0.5))).normalized())

    # the tree behind: wet leaves at real distances
    r = np.random.default_rng(6201)
    back_mat = bpy.data.materials.new("backleaf")
    back_mat.use_nodes = True
    nt = back_mat.node_tree
    nt.nodes.clear()
    o_ = nt.nodes.new("ShaderNodeOutputMaterial")
    pr = nt.nodes.new("ShaderNodeBsdfPrincipled")
    pr.inputs["Base Color"].default_value = (0.01, 0.02, 0.006, 1)
    pr.inputs["Roughness"].default_value = 0.25
    pr.inputs["Coat Weight"].default_value = 1.0
    tl = nt.nodes.new("ShaderNodeBsdfTranslucent")
    tl.inputs["Color"].default_value = (0.05, 0.12, 0.02, 1)
    mxs = nt.nodes.new("ShaderNodeMixShader")
    mxs.inputs["Fac"].default_value = 0.5
    nt.links.new(pr.outputs[0], mxs.inputs[1])
    nt.links.new(tl.outputs[0], mxs.inputs[2])
    nt.links.new(mxs.outputs[0], o_.inputs["Surface"])
    water = C.water_material()
    pv_, pf_ = [(-0.5, -0.5, 0), (0.5, -0.5, 0), (0.5, 0.5, 0), (-0.5, 0.5, 0)], [(0, 1, 2, 3)]
    bead_v, bead_f = sphere_mesh("bb", 1.0, 16, 8)
    k = tries = 0
    while k < 46 and tries < 2000:
        tries += 1
        D = float(r.uniform(0.25, 2.5))
        z = -D
        sx = (C.CAM_Z - z) * C.HFOV_T * 1.3
        sy = (C.CAM_Z - z) * C.VFOV_T * 1.3
        x, y = cx + float(r.uniform(-sx, sx)), cy + float(r.uniform(-sy, sy))
        if (Vector((x, y, z)) - cam_o).normalized().angle(md) < 0.16:
            continue
        k += 1
        size = float(r.uniform(0.05, 0.11)) * (1 + D * 0.3)
        o = mesh_object(f"back{k}", pv_, pf_, back_mat, root)
        o.location = (x, y, z)
        o.scale = (size, size * 0.38, 1)
        o.rotation_euler = (float(r.uniform(-0.9, 0.9)), float(r.uniform(-0.9, 0.9)), float(r.uniform(0, math.pi)))
        o.modifiers.new("sub", "SUBSURF")
        for j in range(int(r.integers(3, 9))):
            bo = mesh_object(f"back{k}_bead{j}", bead_v, bead_f, water, root)
            bo.location = (x + float(r.uniform(-0.3, 0.3)) * size, y + float(r.uniform(-0.1, 0.1)) * size, z + 0.006)
            bo.scale = (float(r.uniform(0.003, 0.008)),) * 3

    # the branch, fixed
    bark = C.simple_material("bark", (0.03, 0.028, 0.018), rough=0.6, coat=0.6)
    bnt = bark.node_tree
    nz = bnt.nodes.new("ShaderNodeTexNoise")
    nz.inputs["Scale"].default_value = 900.0
    nz.inputs["Detail"].default_value = 8.0
    bb = bnt.nodes.new("ShaderNodeBump")
    bb.inputs["Strength"].default_value = 0.8
    bb.inputs["Distance"].default_value = 0.0004
    bnt.links.new(nz.outputs["Fac"], bb.inputs["Height"])
    bnt.links.new(bb.outputs["Normal"], [n for n in bnt.nodes if n.bl_idname == "ShaderNodeBsdfPrincipled"][0].inputs["Normal"])
    curve = bpy.data.curves.new("branch", "CURVE")
    curve.dimensions = "3D"
    curve.bevel_depth = 0.0028
    sp = curve.splines.new("BEZIER")
    sp.bezier_points.add(2)
    for bp, p in zip(sp.bezier_points, [(-0.075, 0.005, -0.012), (-0.058, 0.03, -0.01), (-0.045, 0.06, -0.008)]):
        bp.co = p
        bp.handle_left_type = bp.handle_right_type = "AUTO"
    br = link(bpy.data.objects.new("branch", curve), root)
    br.data.materials.append(bark)

    # the hinge: the stalk's flex. Leaf, stalk and the water on the leaf ride it.
    hinge = link(bpy.data.objects.new("hinge", None), root)
    hinge.location = tuple(H)
    hinge.rotation_mode = "QUATERNION"
    hinge.rotation_quaternion = Matrix(R0.tolist()).to_quaternion()
    pet = bpy.data.curves.new("petiole", "CURVE")
    pet.dimensions = "3D"
    pet.bevel_depth = 0.0007
    sp = pet.splines.new("BEZIER")
    sp.bezier_points.add(2)
    base = leaf_old(np.array(0.0), np.array(0.0))
    inside = leaf_old(np.array(0.04), np.array(0.0))
    mid_pt = np.array([(-0.0585 + base[0]) / 2 + 0.002, (0.03 + base[1]) / 2 + 0.003, (-0.0095 + base[2]) / 2])
    for bp, p, rad in zip(sp.bezier_points, [H, mid_pt, inside], (1.25, 0.95, 0.35)):
        bp.co = tuple(np.asarray(p) - H)
        bp.radius = rad
        bp.handle_left_type = bp.handle_right_type = "AUTO"
    po = link(bpy.data.objects.new("petiole", pet), hinge)
    pmat = C.simple_material("petiole", (0.03, 0.05, 0.014), rough=0.45, coat=0.4)
    po.data.materials.append(pmat)

    nu, nv = 420, 140
    Ug, Vg = np.meshgrid(np.linspace(0, 1, nu + 1), np.linspace(-1, 1, nv + 1))
    verts = local(Ug, Vg).reshape(-1, 3)
    faces = []
    for j in range(nv):
        for i in range(nu):
            a = j * (nu + 1) + i
            faces.append((a, a + 1, a + nu + 2, a + nu + 1))
    me = bpy.data.meshes.new("leaf")
    me.vertices.add(len(verts))
    me.vertices.foreach_set("co", verts.astype(np.float32).ravel())
    me.loops.add(len(faces) * 4)
    me.loops.foreach_set("vertex_index", np.array(faces, np.int32).ravel())
    me.polygons.add(len(faces))
    me.polygons.foreach_set("loop_start", np.arange(0, len(faces) * 4, 4, dtype=np.int32))
    me.update()
    uv = me.uv_layers.new(name="UVMap")
    vi = np.array(faces, np.int32).ravel()
    J_, I_ = np.divmod(vi, nu + 1)
    uv.data.foreach_set("uv", np.stack([I_ / nu, J_ / nv], 1).astype(np.float32).ravel())
    me.polygons.foreach_set("use_smooth", np.ones(len(faces), bool))
    me.materials.append(leaf_material())
    leaf = link(bpy.data.objects.new("leaf", me), hinge)
    col = leaf.modifiers.new("collision", "COLLISION")
    leaf.collision.use_particle_kill = True

    # drops resting on the leaf: small enough that hysteresis pins them, none on the track the bead clears
    path_p = np.array([local(np.array(u), np.array(v)) for b in mech["strikes"] for u, v in b["path"]])
    r = np.random.default_rng(6401)
    made = 0
    for k in range(400):
        if made >= 44:
            break
        u = float(r.uniform(0.2, 0.95))
        v = float(r.uniform(-0.85, 0.85))
        vol = (float(r.random()) ** 2.2) * 2.3e-9 + 0.03e-9
        R = W.cap_radius(vol, THETA)
        _, pl, nl, downl, _ = local_frame(u, v)
        if np.min(np.linalg.norm(path_p - pl, axis=1)) < R + 0.0015:
            continue
        if np.linalg.norm(pl - local(np.array(TIP_U), np.array(0.0))) < 0.0045:  # beside the hanging drop it would have merged into it
            continue
        slope = float(np.linalg.norm(downl))
        dv_, df_ = W.sessile_mesh(vol, THETA, slope)
        o = mesh_object(f"drop{made}", dv_, df_, water, hinge)
        o.location = tuple(pl)
        o.rotation_mode = "QUATERNION"
        o.rotation_quaternion = surface_rotation(nl, downl)
        made += 1

    # the moving water
    beads, raindrops = [], []
    for i, st_ in enumerate(mech["strikes"]):
        bv, bf = W.sessile_mesh(st_["v"], THETA, 0.9)
        bo = mesh_object(f"bead{i}", bv, bf, water, hinge)
        bo.rotation_mode = "QUATERNION"
        beads.append(bo)
        rv, rf = sphere_mesh(f"raindrop{i}", st_["r"])
        raindrops.append(mesh_object(f"raindrop{i}", rv, rf, water, root))
    fam = mech["fam"]
    vols = [f[0] for f in fam]
    lo_i = max(0, int(np.searchsorted(vols, mech["v_rest"] * 0.9)) - 1)
    pick = np.unique(np.linspace(lo_i, len(fam) - 1, 14).astype(int))
    profiles = [W.resample_profile(fam[i][1], fam[i][2], 72) for i in pick]
    xc, zc = profiles[-1]
    necks = [neck_profile(xc, zc, k / 3) for k in (1, 2, 3)]
    pf, shapes = W.pendant_mesh(profiles + necks)
    pend = mesh_object("pendant", shapes[0], pf, water, root)
    pend.shape_key_add(name="Basis")
    for i, sv in zip(list(pick) + ["neck1", "neck2", "neck3"], shapes):
        name = i if isinstance(i, str) else f"v{fam[i][0] * 1e9:.2f}"
        sk = pend.shape_key_add(name=name)
        sk.data.foreach_set("co", np.array(sv, np.float32).ravel())
    pend["volumes_mm3"] = [float(fam[i][0] * 1e9) for i in pick]
    r_fall = (3 * mech["v_fall"] / (4 * math.pi)) ** (1 / 3)
    fv, ff = sphere_mesh("falling", r_fall, 48, 24)
    falling = mesh_object("falling", fv, ff, water, root)
    sv_, sf_ = sphere_mesh("satellite", 0.0003, 16, 8)
    satellite = mesh_object("satellite", sv_, sf_, water, root)

    animate(sc, mech, hinge, beads, pend, falling, satellite, raindrops, (xc, zc))
    particles(sc, root, mech, cx, cy, water)
    lit = bpy.data.collections.new("streetlamp_reaches")
    for o in bpy.data.objects:
        if o.name == "rain_source" or o.name.startswith("raindrop"):
            lit.objects.link(o)
    street.light_linking.receiver_collection = lit
    compositor(sc)
    sc.frame_set(F0)
    return sc


# ---------------------------------------------------------------- keyframes from the mechanics


def _key(o, path, frame, value):
    setattr(o, path, value)
    o.keyframe_insert(path, frame=frame)


def hinge_rotation(theta):
    return Matrix(rot(MECH_A, theta).tolist()).to_quaternion() @ Matrix(R0.tolist()).to_quaternion()


MECH_A = None


def neck_profile(x, z, stage):
    """The critical hanging drop, stage (0..1) of the way through necking: the bulb
    pulls down, the water above it thins to a thread. Contact stays put."""
    tau = stage * NECK_S
    d = 0.5 * NECK_A * tau * tau
    thin = 0.93 * stage**1.3
    ie = int(np.argmax(x))
    x2, z2 = x.copy(), z.copy()
    z2[: ie + 1] -= d
    w = (z[ie + 1 :] - z[ie]) / (z[-1] - z[ie])
    z2[ie + 1 :] = z[ie + 1 :] - d * (1 - w)
    x2[ie + 1 :] = np.maximum(x[ie + 1 :] * (1 - thin * np.sin(np.pi * np.clip(w, 0, 1)) ** 0.6), 4e-5)
    x2[-1] = x[-1]
    return x2, z2


def animate(sc, mech, hinge, beads, pend, falling, satellite, raindrops, crit):
    global MECH_A
    MECH_A = mech["A"]
    fine, th = mech["fine"], mech["theta"]
    # the hinge, keyed every quarter frame so the shutter sees the swing as it is
    for t, a in zip(fine, th):
        _key(hinge, "rotation_quaternion", F0 + t * FPSP, hinge_rotation(a))
    vols = np.array(pend["volumes_mm3"]) * 1e-9
    kb = pend.data.shape_keys.key_blocks
    vol_keys = [k for k in kb[1:] if not k.name.startswith("neck")]
    neck_keys = [kb["neck1"], kb["neck2"], kb["neck3"]]
    hang_l = local(np.array(TIP_U), np.array(0.0))
    R_at = lambda t: rot(MECH_A, float(np.interp(t % LOOP_P, fine, th))) @ R0
    hang_at = lambda t: H + R_at(t) @ hang_l
    t_c, t_b = mech["t_c"], mech["t_b"]

    def weights(t):
        t = t % LOOP_P
        w = {k.name: 0.0 for k in kb[1:]}
        if t_c <= t < t_b:  # necking: from the critical shape through the three neck stages
            x = (t - t_c) / (t_b - t_c) * 3
            stages = [vol_keys[-1]] + neck_keys
            for i, k in enumerate(stages):
                w[k.name] = max(0.0, 1 - abs(x - i))
            return w
        x = float(np.clip(np.interp(mech["pendant_v"](t), vols, np.arange(len(vols))), 0, len(vols) - 1))
        for i, k in enumerate(vol_keys):
            w[k.name] = max(0.0, 1 - abs(x - i))
        return w

    # the hanging drop: keyed every frame, and at each instant something changes between frames
    events = [t_c] + [t_c + (t_b - t_c) * k / 3 for k in (1, 2)] + [t_b - 1e-5, t_b + 1e-5]
    for b in mech["strikes"]:
        events += [b["t_merge"] - 1e-5, b["t_merge"] + 1e-5]
    samples = sorted(set([fi / FPSP for fi in range(LOOP_FRAMES + 1)] + [e % LOOP_P for e in events]))
    for t in samples:
        f = F0 + t * FPSP
        _key(pend, "location", f, Vector(hang_at(t)))
        for name, val in weights(t).items():
            kb[name].value = val
            kb[name].keyframe_insert("value", frame=f)
    # the water left on the tip snaps back and rings (~65 Hz: a frame or two at 96 fps)
    fb = F0 + t_b * FPSP
    _key(pend, "scale", fb - 0.01, (1, 1, 1))
    for i, sq in enumerate([(1.12, 0.74, 1.12), (0.93, 1.12, 0.93), (1.04, 0.95, 1.04), (0.99, 1.02, 0.99), (1, 1, 1)]):
        _key(pend, "scale", fb + 0.01 + i, sq)
    # the drop that falls: it leaves from the bottom of the necked shape with the bulb's speed,
    # stretched, then falls freely and rings between prolate and oblate
    xc, zc = crit
    end = neck_profile(xc, zc, 1.0)
    r_fall = (3 * mech["v_fall"] / (4 * math.pi)) ** (1 / 3)
    apex_y = end[1][0] - zc[-1]  # apex below the contact, after necking
    p0 = hang_at(t_b) + np.array([0, apex_y + r_fall * 1.15, 0])
    v_tip = (hang_at(t_b + 0.005) - hang_at(t_b - 0.005)) / 0.01
    v0 = v_tip + DOWN * NECK_A * mech["neck"]
    rng = np.random.default_rng(9101)
    _key(falling, "scale", fb - 0.01, (0, 0, 0))
    _key(falling, "location", fb - 0.01, Vector(p0))
    for a in range(0, 16):
        tt = a / FPSP
        pos = p0 + v0 * tt + 0.5 * tt * tt * W.G * DOWN
        e = 0.28 * math.exp(-tt * 40) * math.cos(2 * math.pi * 62 * tt) + 0.04 * float(rng.uniform(-1, 1)) * math.exp(-tt * 10)
        _key(falling, "location", fb + 0.01 + a, Vector(pos))
        _key(falling, "scale", fb + 0.01 + a, (1 / math.sqrt(1 + e), 1 + e, 1 / math.sqrt(1 + e)))
    _key(falling, "scale", fb + 16, (0, 0, 0))
    # the thread breaks into a satellite droplet, which trails the drop
    neck_y = float(np.interp(0.55, (end[1] - end[1][0]) / (end[1][-1] - end[1][0]), end[1])) - zc[-1]
    s0 = hang_at(t_b) + np.array([0, neck_y, 0])
    _key(satellite, "scale", fb - 0.01, (0, 0, 0))
    _key(satellite, "location", fb - 0.01, Vector(s0))
    for a in range(0, 16):
        tt = a / FPSP
        _key(satellite, "location", fb + 0.01 + a, Vector(s0 + (v_tip + DOWN * 0.6 * NECK_A * mech["neck"]) * tt + 0.5 * tt * tt * W.G * DOWN))
        _key(satellite, "scale", fb + 0.01 + a, (1, 1, 1))
    _key(satellite, "scale", fb + 16, (0, 0, 0))
    # each strike: the raindrop arrives, and leaves a bead that runs down to the hanging drop
    for b, bead, rd in zip(mech["strikes"], beads, raindrops):
        fi_imp = F0 + b["t_imp"] * FPSP
        for fi in range(LOOP_FRAMES + 1):
            f = F0 + fi
            t = fi / FPSP
            s_ = mech["bead_s"](b, t)
            if s_ is None:
                _key(bead, "scale", f, (0.0, 0.0, 0.0))
                continue
            k = min(int(np.searchsorted(b["s_at"], s_)), len(b["path"]) - 1)
            u, v = b["path"][k]
            _, pl, nl, downl, _ = local_frame(u, v)
            _key(bead, "location", f, Vector(pl))
            _key(bead, "rotation_quaternion", f, surface_rotation(nl, downl))
            speed = float(np.interp(t - b["t_imp"] + 0.02, b["ts"], b["ss"]) - np.interp(t - b["t_imp"], b["ts"], b["ss"])) / 0.02
            st = 1 + min(speed / 0.05, 1) * 0.18
            _key(bead, "scale", f, (st, 1 / math.sqrt(st), 1 / math.sqrt(st)))
        _key(bead, "scale", fi_imp - 0.01, (0, 0, 0))
        _key(bead, "scale", fi_imp + 0.1, (1, 1, 1))
        fm = F0 + b["t_merge"] * FPSP
        _key(bead, "scale", fm - 0.01, (1, 1, 1))
        _key(bead, "scale", fm + 0.01, (0, 0, 0))
        p = b["p_imp"]
        for df in (-3.0, 0.0):
            _key(rd, "location", fi_imp + df, Vector(p - DOWN * RAIN_V * (-df) / FPSP + b["n_imp"] * b["r"]))
            _key(rd, "scale", fi_imp + df, (1, 1, 1))
        _key(rd, "scale", fi_imp + 0.05, (0, 0, 0))
        _key(rd, "scale", fi_imp - 3.05, (0, 0, 0))
    for o in [pend, falling, satellite] + beads + raindrops:
        if o.animation_data and o.animation_data.action:
            for fc in fcurves(o):
                if fc.data_path == "scale":
                    for kp in fc.keyframe_points:
                        kp.interpolation = "LINEAR"
    for fc in fcurves(pend.data.shape_keys):
        for kp in fc.keyframe_points:
            kp.interpolation = "LINEAR"


def fcurves(idblock):
    """F-curves of an ID's action (Blender 5 keeps them in layered channelbags)."""
    act = idblock.animation_data.action
    if hasattr(act, "fcurves"):
        return list(act.fcurves)
    out = []
    for layer in act.layers:
        for strip in layer.strips:
            for bag in strip.channelbags:
                out.extend(bag.fcurves)
    return out


# ---------------------------------------------------------------- rain and splash: Blender particles


def particles(sc, root, mech, cx, cy, water):
    unit_v, unit_f = sphere_mesh("unit", 1.0, 16, 10)
    drop_src = mesh_object("rain_source", unit_v, unit_f, water, root)
    drop_src.location = (0, 50, 0)  # the instanced shape; out of sight itself
    spray_src = mesh_object("spray_source", unit_v, unit_f, water, root)  # the same, for spray under the canopy
    spray_src.location = (0, 52, 0)
    rate = RAIN_RATE  # drops per m^2 per s

    # the leaf is sheltered by its own tree: no rain falls straight onto it (the strikes come through gaps)
    Pl = world(*np.meshgrid(np.linspace(0, 1, 60), np.linspace(-1, 1, 30)))
    hole = (Pl[..., 0].min() - 0.004, Pl[..., 0].max() + 0.004, Pl[..., 2].min() - 0.004, Pl[..., 2].max() + 0.004)

    def rects(x0, x1, z0, z1):
        hx0, hx1, hz0, hz1 = hole
        if hx1 <= x0 or hx0 >= x1 or hz1 <= z0 or hz0 >= z1:
            return [(x0, x1, z0, z1)]
        out = []
        if hz0 > z0:
            out.append((x0, x1, z0, hz0))
        if hz1 < z1:
            out.append((x0, x1, hz1, z1))
        zz0, zz1 = max(z0, hz0), min(z1, hz1)
        if hx0 > x0:
            out.append((x0, hx0, zz0, zz1))
        if hx1 < x1:
            out.append((hx1, x1, zz0, zz1))
        return out

    def emitter(name, x0, x1, z0, z1, y, frames, life, seed):
        verts, faces, area = [], [], 0.0
        for rx0, rx1, rz0, rz1 in rects(x0, x1, z0, z1):
            k = len(verts)
            verts += [(rx0, y, rz0), (rx1, y, rz0), (rx1, y, rz1), (rx0, y, rz1)]
            faces.append((k, k + 3, k + 2, k + 1))
            area += (rx1 - rx0) * (rz1 - rz0)
        me = bpy.data.meshes.new(name)
        me.from_pydata(verts, [], faces)
        o = link(bpy.data.objects.new(name, me), root)
        o.show_instancer_for_render = False
        o.show_instancer_for_viewport = False
        ps = o.modifiers.new(name, "PARTICLE_SYSTEM").particle_system
        st = ps.settings
        st.count = int(rate * area * (frames[1] - frames[0]) / FPSP)
        st.frame_start, st.frame_end = frames
        st.lifetime = life
        st.emit_from = "FACE"
        st.use_emit_random = True
        st.use_even_distribution = True
        st.physics_type = "NEWTON"
        st.timestep = 1 / FPSP  # slow motion: each frame advances 1/96 s of real time
        st.normal_factor = 0.0
        st.object_align_factor = (0.0, -RAIN_V, 0.0)
        st.factor_random = 0.15
        st.effector_weights.gravity = 0.0  # at terminal speed drag balances gravity
        st.render_type = "OBJECT"
        st.instance_object = drop_src
        st.particle_size = 0.001
        st.size_random = 0.5
        st.use_rotations = False
        ps.seed = seed
        ps.point_cache.frame_start = frames[0]
        ps.point_cache.frame_end = F0 + LOOP_FRAMES + 2
        return o

    d_far = 6.0
    wf = (C.CAM_Z + d_far) * C.HFOV_T * 1.15
    frames = (F0 - 60, F0 + LOOP_FRAMES + 2)
    emitter("rain_back", cx - wf, cx + wf, -d_far, 0.0, cy + 0.85, frames, 12 * SLOW, 11)
    wn = C.CAM_Z * C.HFOV_T * 1.2
    emitter("rain_front", cx - wn, cx + wn, 0.0, 0.25, cy + 0.07, frames, 4 * SLOW, 23)

    # the splash: spray off each strike, thrown forward and up, falling under gravity
    for i, b in enumerate(mech["strikes"]):
        p, n = b["p_imp"], b["n_imp"]
        downl = DOWN - DOWN.dot(n) * n
        me = bpy.data.meshes.new(f"splash{i}")
        s = 0.0006
        me.from_pydata([(-s, -s, 0), (s, -s, 0), (s, s, 0), (-s, s, 0)], [], [(0, 1, 2, 3)])
        sp = link(bpy.data.objects.new(f"splash{i}", me), root)
        sp.location = tuple(p + n * 0.0002)
        sp.rotation_mode = "QUATERNION"
        sp.rotation_quaternion = surface_rotation(n, downl)
        sp.show_instancer_for_render = False
        ps = sp.modifiers.new(f"splash{i}", "PARTICLE_SYSTEM").particle_system
        st = ps.settings
        f_hit = F0 + b["t_imp"] * FPSP
        st.count = int(26 * (b["r"] / 0.0011) ** 3)
        st.frame_start = st.frame_end = f_hit
        st.lifetime = 40
        st.emit_from = "FACE"
        st.physics_type = "NEWTON"
        st.timestep = 1 / FPSP
        st.normal_factor = 0.55
        st.object_align_factor = (1.1, 0.0, 0.0)
        st.factor_random = 0.9
        st.drag_factor = 0.15
        st.render_type = "OBJECT"
        st.instance_object = spray_src
        st.particle_size = 0.00026
        st.size_random = 0.65
        ps.seed = 17 + i
        ps.point_cache.frame_start = F0
        ps.point_cache.frame_end = F0 + LOOP_FRAMES + 2
    sc.frame_start = F0 - 60
    override = {"scene": sc}
    with bpy.context.temp_override(**override):
        bpy.ops.ptcache.bake_all(bake=True)
    sc.frame_start = F0


# ---------------------------------------------------------------- the grade, in the compositor


def compositor(sc):
    ng = bpy.data.node_groups.new("grade", "CompositorNodeTree")
    ng.interface.new_socket("Image", in_out="OUTPUT", socket_type="NodeSocketColor")
    sc.compositing_node_group = ng
    sc.render.compositor_device = "CPU"
    sc.render.compositor_precision = "FULL"
    N, L = ng.nodes, ng.links
    rl = N.new("CompositorNodeRLayers")
    glare = N.new("CompositorNodeGlare")
    glare.inputs["Type"].default_value = "Fog Glow"
    glare.inputs["Threshold"].default_value = 0.6
    glare.inputs["Strength"].default_value = 0.35
    glare.inputs["Size"].default_value = 0.6
    lens = N.new("CompositorNodeLensdist")
    lens.inputs["Distortion"].default_value = -0.004
    lens.inputs["Dispersion"].default_value = 0.012
    hs = N.new("CompositorNodeHueSat")
    hs.inputs["Saturation"].default_value = 0.82
    cb = N.new("CompositorNodeColorBalance")
    [i for i in cb.inputs if i.name == "Gain" and i.bl_idname == "NodeSocketColor"][0].default_value = (0.94, 0.98, 1.06, 1)
    noise = N.new("ShaderNodeTexWhiteNoise")
    noise.noise_dimensions = "4D"
    tm = N.new("CompositorNodeSceneTime")
    coords = N.new("CompositorNodeImageCoordinates")
    L.new(rl.outputs["Image"], coords.inputs["Image"])
    L.new(coords.outputs["Uniform"], noise.inputs["Vector"])
    L.new(tm.outputs["Frame"], noise.inputs["W"])
    grain = N.new("ShaderNodeMix")
    grain.data_type = "RGBA"
    grain.blend_type = "OVERLAY"
    grain.inputs["Factor"].default_value = 0.035
    vig = N.new("CompositorNodeEllipseMask")
    vig.inputs["Size"].default_value = (1.0, 1.0)
    vblur = N.new("CompositorNodeBlur")
    vblur.inputs["Size"].default_value = (500, 500)
    vmix = N.new("ShaderNodeMix")
    vmix.data_type = "RGBA"
    vmix.blend_type = "MULTIPLY"
    out = N.new("NodeGroupOutput")
    L.new(rl.outputs["Image"], glare.inputs["Image"])
    L.new(glare.outputs["Image"], lens.inputs["Image"])
    L.new(lens.outputs["Image"], hs.inputs["Image"])
    L.new(hs.outputs["Image"], cb.inputs["Image"])
    L.new(cb.outputs["Image"], grain.inputs["A"])
    L.new(noise.outputs["Color"], grain.inputs["B"])
    L.new(vig.outputs["Mask"], vblur.inputs["Image"])
    vmap = N.new("ShaderNodeMapRange")
    vmap.inputs["To Min"].default_value = 0.62
    L.new(vblur.outputs["Image"], vmap.inputs["Value"])
    L.new(grain.outputs["Result"], vmix.inputs["A"])
    L.new(vmap.outputs["Result"], vmix.inputs["B"])
    vmix.inputs["Factor"].default_value = 1.0
    L.new(vmix.outputs["Result"], out.inputs["Image"])


# ---------------------------------------------------------------- entry


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--mechanics", action="store_true")
    ap.add_argument("--save", action="store_true")
    ap.add_argument("--still", type=int)
    ap.add_argument("--frames")
    ap.add_argument("--samples", type=int, default=96)
    ap.add_argument("--scale", type=int, default=100)
    ap.add_argument("--out")
    ap.add_argument("--raw", action="store_true", help="linear EXR, no grade: PhotoCraft develops it (nuh.photocraft)")
    ap.add_argument("--out-dir", help="where frames go (default output/nuh/raw or output/nuh/frames)")
    a = ap.parse_args(argv)
    if a.mechanics:
        mechanics(verbose=True)
        return
    t0 = time.time()
    sc = build(a.samples, a.scale)
    print(f"built in {time.time() - t0:.0f}s", flush=True)
    if a.save:
        BLEND_DIR.mkdir(parents=True, exist_ok=True)
        bpy.ops.wm.save_as_mainfile(filepath=str(BLEND), relative_remap=True)
        print("saved", BLEND)
    todo = []
    if a.still is not None:
        todo = [a.still]
    elif a.frames:
        a0, a1 = map(int, a.frames.split(":"))
        todo = list(range(a0, a1))
    out_dir, ext = FRAMES, "png"
    if a.raw:
        sc.render.use_compositing = False
        sc.render.image_settings.file_format = "OPEN_EXR"
        sc.render.image_settings.color_depth = "32"
        sc.render.image_settings.exr_codec = "ZIP"
        sc.render.image_settings.color_mode = "RGB"
        out_dir, ext = RAW, "exr"
    if a.out_dir:
        out_dir = Path(a.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for fi in todo:
        dst = Path(a.out) if a.out else out_dir / f"f{fi:04d}.{ext}"
        if a.frames and dst.exists():
            continue
        t1 = time.time()
        sc.frame_set(F0 + fi)
        sc.render.filepath = str(dst)
        bpy.ops.render.render(write_still=True)
        print(f"frame {fi} in {time.time() - t1:.0f}s -> {dst}", flush=True)


if __name__ == "__main__":
    main(sys.argv[1:])

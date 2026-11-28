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

# ---------------------------------------------------------------- water
THETA = math.radians(95)  # contact angle on the waxy leaf
TIP_RC = 0.0004  # contact radius of the hanging drop on the drip tip
RAIN_R = 0.0011  # the raindrop that strikes: 2.2 mm across
RAIN_V = 6.8  # its terminal speed, m/s
SPLASH_KEEP = 0.6  # share of it left on the leaf as a bead
LEAF_AREAL = 0.19  # leaf mass per area, kg/m^2 (0.25 mm thick)
LEAF_FREQ = 2.6  # Hz, the leaf swinging on its stalk
LEAF_ZETA = 0.08
F_IMP = 23  # loop frame of the strike (it lands a quarter frame in)
IMP_UV = (0.82, 0.25)


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


def mechanics(verbose=False):
    """Every number the loop moves by. Times are loop seconds."""
    dt_f = 1 / FPSP
    t_imp = (F_IMP + 0.25) * dt_f
    v_rain = 4 / 3 * math.pi * RAIN_R**3
    v_bead = SPLASH_KEEP * v_rain
    m_bead = W.RHO * v_bead
    # the bead's path and its slide
    path = bead_path(*IMP_UV)
    P = np.array([world(np.array(u), np.array(v)) for u, v in path])
    seg = np.linalg.norm(np.diff(P, axis=0), axis=1)
    s_at = np.concatenate([[0], np.cumsum(seg)])
    L = float(s_at[-1])
    gal = np.concatenate([[-W.G * (P[1, 1] - P[0, 1]) / max(seg[0], 1e-9)], -W.G * np.diff(P[:, 1]) / np.maximum(seg, 1e-9)])
    on_edge = np.array([abs(v) > 0.9 or u > 0.97 for u, v in path], float)
    rng = np.random.default_rng(8101)
    veins = rng.uniform(-1, 1, 400)

    def along_g(s):
        return float(np.interp(s, s_at, gal))

    def hyst(s):
        k = s / 0.0014
        i = int(k) % 399
        f = k - int(k)
        nz = veins[i] * (1 - f) + veins[i + 1] * f
        base = np.interp(s, s_at, 5.2 - 1.9 * on_edge)
        return min(base * (1 + 0.32 * nz), 0.8 * along_g(s))

    ts, ss = W.slide(L - 0.002, along_g,  # it merges when it touches the hanging drop
                      start_speed=0.06, hyst=hyst, drag=160.0)
    t_merge = t_imp + float(ts[-1])
    f_merge = math.ceil(t_merge * FPSP)
    t_drip = (f_merge + 1) / FPSP  # one frame of necking, then it goes
    # the hanging drop
    fam = W.pendant_family(TIP_RC)
    v_crit = fam[-1][0]
    v_before = v_crit - 0.5 * v_bead
    v_total = v_before + v_bead
    v_fall = W.FALL_FRACTION * v_total
    v_rest = v_total - v_fall
    q = (v_before - v_rest) / (LOOP_P - (t_drip - t_merge))  # inflow from the leaf above, m^3/s

    def pendant_v(t):
        t = t % LOOP_P
        if t_merge <= t < t_drip:
            return v_total
        return v_rest + q * ((t - t_drip) % LOOP_P)

    def bead_s(t):
        t = t % LOOP_P
        if t < t_imp or t >= t_merge:
            return None
        return float(np.interp(t - t_imp, ts, ss))

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
    tip = world(np.array(1.0), np.array(0.0))

    def torque_of(m, p):
        return float(A @ np.cross(p - H, m * W.G * DOWN))

    def torque(t):
        tq = torque_of(W.RHO * pendant_v(t), tip)
        s = bead_s(t)
        if s is not None:
            tq += torque_of(m_bead, P[min(np.searchsorted(s_at, s), len(P) - 1)])
        return tq

    tq_mean = float(np.mean([torque(t) for t in np.linspace(0, LOOP_P, 801)[:-1]]))
    p_imp, n_imp, _, _ = frame_at(*IMP_UV)
    vel = RAIN_V * DOWN
    vn = vel.dot(n_imp) * n_imp
    J = W.RHO * v_rain * (vn + 0.3 * (vel - vn))
    kick = float(A @ np.cross(p_imp - H, J))
    times = np.arange(LOOP_FRAMES + 1) / FPSP
    fine = np.arange(LOOP_FRAMES * 4 + 1) / (FPSP * 4)  # quarter frames, for the motion-blur keys
    th, k = W.swing(fine, inertia, LEAF_FREQ, LEAF_ZETA, lambda t: torque(t) - tq_mean, [(t_imp, kick)])
    mech = dict(
        t_imp=t_imp, t_merge=t_merge, t_drip=t_drip, f_merge=f_merge, path=path, s_at=s_at, ts=ts, ss=ss,
        v_bead=v_bead, v_crit=v_crit, v_before=v_before, v_fall=v_fall, v_rest=v_rest, q=q, fam=fam,
        A=A, inertia=inertia, k=k, theta=th, fine=fine, times=times, pendant_v=pendant_v, bead_s=bead_s,
        p_imp=p_imp, n_imp=n_imp, kick=kick, path_len=L,
    )
    if verbose:
        tipdisp = np.abs(th) * np.linalg.norm(tip - H)
        print(f"raindrop {v_rain * 1e9:.1f} mm3 at {RAIN_V} m/s, strikes at {t_imp:.2f} s, {math.degrees(math.acos(abs(n_imp @ DOWN))):.0f} deg from the leaf's normal")
        print(f"bead {v_bead * 1e9:.2f} mm3 slides {L * 1e3:.1f} mm in {ts[-1]:.2f} s (mean {L / ts[-1] * 100:.2f} cm/s), path ends at u={path[-1][0]:.3f} v={path[-1][1]:.2f}")
        print(f"hanging drop: critical {v_crit * 1e9:.2f} mm3; before merge {v_before * 1e9:.2f}, falls {v_fall * 1e9:.2f}, stays {v_rest * 1e9:.2f}; refills at {q * 1e9:.2f} mm3/s")
        print(f"merge at {t_merge:.2f} s, lets go at {t_drip:.2f} s")
        print(f"leaf: inertia {inertia:.3g} kg m2, stiffness {k:.3g} N m/rad, mass {dm.sum() * 1e3:.2f} g; kick {kick:.3g} N m s")
        print(f"swing: max {math.degrees(np.abs(th).max()):.2f} deg, tip moves up to {tipdisp.max() * 1e3:.2f} mm; at loop end {math.degrees(th[-1]):.3f} vs start {math.degrees(th[0]):.3f}")
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
    sc.render.motion_blur_shutter = 0.5  # 180 degrees: 1/48 s
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
    md = C.ndc_dir(1452, 262)  # the moon's direction from the camera, build frame
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
    mid_u, mid_v = mech["path"][len(mech["path"]) // 2]
    focus_pt = world(np.array(mid_u), np.array(mid_v))
    cam_d = bpy.data.cameras.new("macro")
    cam_d.lens = C.FOCAL_MM
    cam_d.sensor_width = C.SENSOR_MM
    cam_d.sensor_fit = "HORIZONTAL"
    cam_d.clip_start = 0.01
    cam_d.clip_end = 500
    cam_d.dof.use_dof = True
    cam_d.dof.focus_distance = float(C.CAM_Z - focus_pt[2])
    cam_d.dof.aperture_fstop = C.FSTOP
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
    sun_d.energy = 1.1
    sun_d.color = (0.78, 0.86, 1.0)
    sun_d.angle = math.radians(0.52)
    sun = link(bpy.data.objects.new("moonlight", sun_d), root)
    sun.rotation_mode = "QUATERNION"
    sun.rotation_quaternion = Vector((0, 0, 1)).rotation_difference(md)
    tip_w = Vector(world(np.array(1.0), np.array(0.0)))
    for name, energy, size, loc, aim in (("fill", 3.0, 0.12, (-0.22, 0.30, 0.42), (0, 0, 0)), ("kicker", 1.6, 0.03, (tip_w.x + 0.047, tip_w.y - 0.09, 0.14), tuple(tip_w))):
        ld = bpy.data.lights.new(name, "AREA")
        ld.energy, ld.size, ld.color = energy, size, (0.82, 0.9, 1.0)
        lo = link(bpy.data.objects.new(name, ld), root)
        lo.location = loc
        lo.rotation_mode = "QUATERNION"
        lo.rotation_quaternion = Vector((0, 0, 1)).rotation_difference((Vector(loc) - Vector(aim)).normalized())

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
    path_p = np.array([local(np.array(u), np.array(v)) for u, v in mech["path"]])
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
        if np.linalg.norm(pl - path_p[-1]) < 0.004:  # beside the hanging drop it would have merged into it
            continue
        slope = float(np.linalg.norm(downl))
        dv_, df_ = W.sessile_mesh(vol, THETA, slope)
        o = mesh_object(f"drop{made}", dv_, df_, water, hinge)
        o.location = tuple(pl)
        o.rotation_mode = "QUATERNION"
        o.rotation_quaternion = surface_rotation(nl, downl)
        made += 1

    # the moving water
    v_bead = mech["v_bead"]
    bv, bf = W.sessile_mesh(v_bead, THETA, 0.9)
    bead = mesh_object("bead", bv, bf, water, hinge)
    bead.rotation_mode = "QUATERNION"
    fam = mech["fam"]
    vols = [f[0] for f in fam]
    lo_i = max(0, int(np.searchsorted(vols, mech["v_rest"] * 0.9)) - 1)
    pick = np.unique(np.linspace(lo_i, len(fam) - 1, 14).astype(int))
    profiles = [W.resample_profile(fam[i][1], fam[i][2], 64) for i in pick]
    pf, shapes = W.pendant_mesh(profiles)
    pend = mesh_object("pendant", shapes[0], pf, water, root)
    pend.shape_key_add(name="Basis")
    for i, sv in zip(pick, shapes):
        sk = pend.shape_key_add(name=f"v{fam[i][0] * 1e9:.2f}")
        sk.data.foreach_set("co", np.array(sv, np.float32).ravel())
    pend["volumes_mm3"] = [float(fam[i][0] * 1e9) for i in pick]
    r_fall = (3 * mech["v_fall"] / (4 * math.pi)) ** (1 / 3)
    fv, ff = sphere_mesh("falling", r_fall)
    falling = mesh_object("falling", fv, ff, water, root)
    rv, rf = sphere_mesh("raindrop", RAIN_R)
    raindrop = mesh_object("raindrop", rv, rf, water, root)
    rec = bpy.data.collections.new("kick_receivers")
    for o in (pend, falling):
        rec.objects.link(o)
    bpy.data.objects["kicker"].light_linking.receiver_collection = rec

    animate(sc, mech, hinge, bead, pend, falling, raindrop, pick)
    particles(sc, root, mech, cx, cy, water)
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


def animate(sc, mech, hinge, bead, pend, falling, raindrop, pick):
    global MECH_A
    MECH_A = mech["A"]
    fine, th = mech["fine"], mech["theta"]
    # the hinge, keyed every quarter frame so the shutter sees the swing as it is
    for t, a in zip(fine, th):
        _key(hinge, "rotation_quaternion", F0 + t * FPSP, hinge_rotation(a))
    vols = np.array(pend["volumes_mm3"]) * 1e-9
    keys = pend.data.shape_keys.key_blocks
    tip_l = local(np.array(1.0), np.array(0.0))
    R_at = lambda t: rot(MECH_A, float(np.interp(t % LOOP_P, fine, th))) @ R0
    t_imp, t_merge, t_drip = mech["t_imp"], mech["t_merge"], mech["t_drip"]
    f_drip = round(t_drip * FPSP)
    for fi in range(LOOP_FRAMES + 1):
        f = F0 + fi
        t = fi / FPSP
        # the hanging drop: its shape for its volume, at the tip
        tip_w = H + R_at(t) @ tip_l
        _key(pend, "location", f, Vector(tip_w + np.array([0.0, 0.00015, 0.0])))
        vol = mech["pendant_v"](t)
        x = float(np.clip(np.interp(vol, vols, np.arange(len(vols))), 0, len(vols) - 1))
        for i, kb in enumerate(keys[1:]):
            kb.value = max(0.0, 1 - abs(x - i))
            kb.keyframe_insert("value", frame=f)
        sq = (1.0, 1.0, 1.0)
        if fi == mech["f_merge"] % LOOP_FRAMES:
            sq = (0.86, 1.28, 0.86)  # past critical: it necks
        a = fi - f_drip
        if 0 <= a < 4:
            sq = [(1.1, 0.78, 1.1), (0.94, 1.1, 0.94), (1.03, 0.96, 1.03), (1.0, 1.0, 1.0)][a]  # the rest snaps back and rings
        _key(pend, "scale", f, sq)
        # the bead
        s = mech["bead_s"](t)
        vis = s is not None
        if vis:
            k = min(int(np.searchsorted(mech["s_at"], s)), len(mech["path"]) - 1)
            u, v = mech["path"][k]
            _, pl, nl, downl, _ = local_frame(u, v)
            _key(bead, "location", f, Vector(pl))
            _key(bead, "rotation_quaternion", f, surface_rotation(nl, downl))
            speed = float(np.interp(t - t_imp + 0.02, mech["ts"], mech["ss"]) - np.interp(t - t_imp, mech["ts"], mech["ss"])) / 0.02
            st = 1 + min(speed / 0.05, 1) * 0.18
            _key(bead, "scale", f, (st, 1 / math.sqrt(st), 1 / math.sqrt(st)))
        else:
            _key(bead, "scale", f, (0.0, 0.0, 0.0))
    # the strike: the raindrop arrives on the leaf a quarter frame into F_IMP
    fi_imp = F0 + t_imp * FPSP
    p = mech["p_imp"]
    for df in (-3.0, 0.0):
        _key(raindrop, "location", fi_imp + df, Vector(p - DOWN * RAIN_V * (-df) / FPSP + mech["n_imp"] * RAIN_R))
        _key(raindrop, "scale", fi_imp + df, (1, 1, 1))
    _key(raindrop, "scale", fi_imp + 0.05, (0, 0, 0))
    _key(raindrop, "scale", fi_imp - 3.05, (0, 0, 0))
    _key(bead, "scale", fi_imp - 0.01, (0, 0, 0))
    _key(bead, "scale", fi_imp + 0.1, (1, 1, 1))
    # the falling drop: free fall from where the hanging drop necked, with the tip's own velocity
    h_crit = float(-np.array(pend.data.shape_keys.key_blocks[-1].data[0].co)[1])
    r_fall = (3 * mech["v_fall"] / (4 * math.pi)) ** (1 / 3)
    tip0 = H + R_at(t_drip) @ tip_l
    tip1 = H + R_at(t_drip + 0.01) @ tip_l
    v0 = (tip1 - tip0) / 0.01 + np.array([0, -0.04, 0])
    p0 = tip0 + np.array([0, -(h_crit - r_fall), 0])
    rng = np.random.default_rng(9101)
    _key(falling, "scale", f_drip + F0 - 0.01, (0, 0, 0))
    for a in range(0, 12):
        tt = a / FPSP
        pos = p0 + v0 * tt + 0.5 * tt * tt * W.G * DOWN
        wob = 0.12 * math.exp(-tt * 12) * float(rng.uniform(-1, 1))
        _key(falling, "location", f_drip + F0 + a, Vector(pos))
        _key(falling, "scale", f_drip + F0 + a, (1 - wob, 1 + wob, 1 - wob))
    _key(falling, "scale", f_drip + F0 + 12, (0, 0, 0))
    for o in (hinge, pend, bead, falling, raindrop):
        if o.animation_data and o.animation_data.action:
            for fc in fcurves(o):
                if fc.data_path in ("scale",) and o is not pend:
                    for kp in fc.keyframe_points:
                        kp.interpolation = "LINEAR"
    if pend.data.shape_keys.animation_data:
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
    rate = 250.0  # drops per m^2 per s: moderate rain, ~5 mm/h

    def emitter(name, x0, x1, z0, z1, y, frames, life):
        verts = [(x0, y, z0), (x1, y, z0), (x1, y, z1), (x0, y, z1)]
        me = bpy.data.meshes.new(name)
        me.from_pydata(verts, [], [(0, 3, 2, 1)])
        o = link(bpy.data.objects.new(name, me), root)
        o.show_instancer_for_render = False
        o.show_instancer_for_viewport = False
        ps = o.modifiers.new(name, "PARTICLE_SYSTEM").particle_system
        st = ps.settings
        area = (x1 - x0) * (z1 - z0)
        st.count = int(rate * area * (frames[1] - frames[0]) / FPSP)
        st.frame_start, st.frame_end = frames
        st.lifetime = life
        st.emit_from = "FACE"
        st.use_emit_random = True
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
        ps.seed = hash(name) % 1000
        ps.point_cache.frame_start = frames[0]
        ps.point_cache.frame_end = F0 + LOOP_FRAMES + 2
        return o

    d_far = 6.0
    wf = (C.CAM_Z + d_far) * C.HFOV_T * 1.15
    emitter("rain_back", cx - wf, cx + wf, -d_far, -0.015, cy + 0.85, (F0 - 60, F0 + LOOP_FRAMES + 2), 12 * SLOW)
    wn = C.CAM_Z * C.HFOV_T * 1.2
    emitter("rain_front", cx - wn, cx + wn, 0.015, 0.25, cy + 0.07, (F0 - 60, F0 + LOOP_FRAMES + 2), 4 * SLOW)

    # the splash: spray off the strike, thrown forward and up, falling under gravity
    p, n = mech["p_imp"], mech["n_imp"]
    downl = DOWN - DOWN.dot(n) * n
    me = bpy.data.meshes.new("splash")
    s = 0.0006
    me.from_pydata([(-s, -s, 0), (s, -s, 0), (s, s, 0), (-s, s, 0)], [], [(0, 1, 2, 3)])
    sp = link(bpy.data.objects.new("splash", me), root)
    sp.location = tuple(p + n * 0.0002)
    sp.rotation_mode = "QUATERNION"
    sp.rotation_quaternion = surface_rotation(n, downl)
    sp.show_instancer_for_render = False
    ps = sp.modifiers.new("splash", "PARTICLE_SYSTEM").particle_system
    st = ps.settings
    f_hit = F0 + mech["t_imp"] * FPSP
    st.count = 26
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
    st.instance_object = drop_src
    st.particle_size = 0.00026
    st.size_random = 0.65
    ps.seed = 17
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

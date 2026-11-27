"""Path-traced macro of a leaf in the rain at night (Blender Cycles via bpy).

Run with a Python that has the `bpy` module (pip install bpy==5.3.0):

    python -m nuh.cycles_leaf --still 61.0 --out /tmp/leaf.exr [--scale 50] [--samples 128]

Everything is built from code: meshes, procedural textures (numpy arrays
handed to Blender in memory, never files), shaders, the moon, the rain. The
optics are the renderer's: a 100 mm lens on a 36 mm sensor focused 33 cm
away at f/5.6 with nine blades, so depth of field, bokeh and the moon's
defocus disc, refraction through each drop and motion blur at a 1/48 s
shutter are all computed, not drawn. Output is linear EXR; nuh.develop turns
it into the graded frame.

Units are metres.
"""

from __future__ import annotations

import argparse
import math
import sys

import bpy
import numpy as np
from mathutils import Vector

from .paint import fbm_tex, smooth

# ---------------------------------------------------------------- lens
FOCAL_MM = 100.0
SENSOR_MM = 36.0
FSTOP = 5.6
FOCUS = 0.333
CAM_Z = FOCUS
HFOV_T = (SENSOR_MM / 2) / FOCAL_MM  # tan(half horizontal fov)
VFOV_T = HFOV_T * 1080 / 1920

# ---------------------------------------------------------------- leaf
LEAF_LEN = 0.086
LEAF_HALF_W = 0.0185
BASE_XY = (-0.0405, 0.0205)
TIP_XY = (0.0425, -0.0165)


def ndc_dir(px, py):
    """Direction from the camera toward image pixel (px, py) of a 1920x1080 frame."""
    x = (px - 960) / 960 * HFOV_T
    y = (540 - py) / 540 * VFOV_T
    v = Vector((x, y, -1.0))
    return v.normalized()


MOON_DIR = ndc_dir(1452, 262)


def clear():
    bpy.ops.wm.read_factory_settings(use_empty=True)


def np_image(name, arr):
    """A float RGBA image from a numpy array (H,W,{1,3,4}), kept in memory."""
    h, w = arr.shape[:2]
    if arr.ndim == 2:
        arr = arr[..., None]
    if arr.shape[2] == 1:
        arr = np.repeat(arr, 3, 2)
    if arr.shape[2] == 3:
        arr = np.concatenate([arr, np.ones((h, w, 1), np.float32)], 2)
    img = bpy.data.images.new(name, w, h, alpha=True, float_buffer=True)
    img.colorspace_settings.name = "Non-Color"  # before the pixels: changing it later resets them
    img.pixels.foreach_set(np.ascontiguousarray(arr[::-1], dtype=np.float32).ravel())
    img.update()
    return img


# ---------------------------------------------------------------- textures in leaf (u, v) space


def leaf_textures(nu=4096, nv=2048):
    """u along the leaf 0..1 (columns), v across -1..1 (rows). Returns
    (vein mask, height, translucency colour, front colour)."""
    u = np.linspace(0, 1, nu, dtype=np.float32)[None, :]
    v = np.linspace(-1, 1, nv, dtype=np.float32)[:, None]
    av = np.abs(v)
    hw = half_width(u)
    # distances in metres, for widths
    d = v * hw
    mid = np.exp(-(d / (0.00045 - 0.0003 * u)) ** 2)
    r = np.random.default_rng(5105)
    sec = np.zeros((nv, nu), np.float32)
    for sgn in (-1, 1):
        uk = 0.05 + (0.04 if sgn < 0 else 0.0)
        while uk < 0.9:
            curve = 0.17 + r.uniform(-0.02, 0.03)
            path = uk + curve * av**1.15 + 0.10 * smooth(0.62, 0.92, av)
            dist = (u - path) * LEAF_LEN
            width = (0.00016 - 0.00011 * av) * (1 - 0.45 * uk)
            m = (np.sign(v) == sgn) & (av > 0.01)
            sec += np.exp(-(dist / np.maximum(width, 0.00004)) ** 2) * smooth(0.95, 0.8, av) * m
            p2 = path + 0.042
            sec += 0.45 * np.exp(-(((u - p2) * LEAF_LEN) / np.maximum(width * 0.6, 0.00003)) ** 2) * smooth(0.75, 0.45, av) * smooth(0.05, 0.25, av) * m
            uk += r.uniform(0.072, 0.095)
    sec = np.clip(sec, 0, 1)
    # areoles: a Voronoi network
    import cv2

    seeds = np.full((nv, nu), 255, np.uint8)
    n = 9000
    seeds[(r.random(n) * nv).astype(int), (r.random(n) * nu).astype(int)] = 0
    dist_c, lab = cv2.distanceTransformWithLabels(seeds, cv2.DIST_L2, 5, labelType=cv2.DIST_LABEL_CCOMP)
    e = np.zeros(lab.shape, np.float32)
    e[:, 1:] += lab[:, 1:] != lab[:, :-1]
    e[1:, :] += lab[1:, :] != lab[:-1, :]
    tert = cv2.GaussianBlur((e > 0).astype(np.float32), (0, 0), 0.8)
    micro = cv2.resize(fbm_tex(1024, 512, 5103, base=120, octaves=3), (nu, nv))
    blot = cv2.resize(fbm_tex(512, 256, 5106, base=5, octaves=5), (nu, nv))
    veins = np.clip(mid + 0.9 * sec + 0.55 * tert, 0, 1)
    cells = cv2.resize(fbm_tex(2048, 1024, 5108, base=420, octaves=2), (nu, nv))
    height = (0.6 * mid - 0.35 * sec - 0.25 * tert + 0.12 * micro + 0.10 * cells).astype(np.float32)
    lam = np.array([0.10, 0.26, 0.03], np.float32)
    vein_c = np.array([0.24, 0.36, 0.09], np.float32)
    # light through the leaf is uneven: thicker and thinner tissue, and the shade of leaves above
    glow = (0.45 + 0.55 * u) * (0.75 + 0.35 * smooth(0.0, -1.0, v)) * (0.9 + 0.15 * blot)
    trans = lam * (0.82 + 0.3 * micro)[..., None] * (0.8 + 0.3 * blot)[..., None] * glow[..., None]
    trans = trans * (1 - veins[..., None] * 0.9) + vein_c * veins[..., None] * 0.9
    trans *= (1 + 0.5 * smooth(0.6, 1.0, av))[..., None]  # thinner margin
    front = np.array([0.025, 0.06, 0.012], np.float32) * (0.8 + 0.4 * micro)[..., None]
    front = front * (1 - veins[..., None] * 0.4) + np.array([0.06, 0.09, 0.03], np.float32) * veins[..., None] * 0.4
    return veins, height, trans, front


def half_width(u):
    u = np.clip(u, 0, 1)
    body = np.clip(np.sin(np.pi * u**0.8), 0, None) ** 0.85
    acum = 1 - smooth(0.84, 1.0, u) * 0.6
    base = 1 - 0.25 * smooth(0.1, 0.0, u)
    return LEAF_HALF_W * body * acum * base


def leaf_frame():
    b = np.array(BASE_XY, np.float64)
    t = np.array(TIP_XY, np.float64)
    ax = (t - b) / np.linalg.norm(t - b)
    perp = np.array([-ax[1], ax[0]])
    return b, ax, perp


_RNG = np.random.default_rng(5201)
_EN = _RNG.uniform(-1, 1, 512)
_NK = (_RNG.random(512) > 0.93).astype(float)


def _edge_noise(x):
    i = int(math.floor(x)) % 511
    f = x - math.floor(x)
    f = f * f * (3 - 2 * f)
    return _EN[i] * (1 - f) + _EN[i + 1] * f


def _nicks(u):
    x = u * 200
    i = int(x) % 511
    return _NK[i] * math.sin(math.pi * (x - int(x)))


def leaf_point(u, v, defl=0.0):
    """World position and normal of the leaf at (u, v). defl = tip
    deflection in metres (down = negative z-ish, i.e. the leaf bends away)."""
    b, ax, perp = leaf_frame()
    hw = float(half_width(np.float32(u))) * (1 + 0.012 * _edge_noise(u * 40) * abs(v) ** 6 - 0.02 * _nicks(u) * abs(v) ** 10)
    along = u * LEAF_LEN
    # the leaf: arches, cups, curls at the edges, turns away along its upper half
    lift = -0.0012 * math.sin(math.pi * u)
    cup = -0.0045 * v * v * (0.3 + 0.7 * math.sin(math.pi * min(u, 1)))
    curl = 0.0035 * smooth(0.78, 1.0, abs(v)) ** 2 * math.sin(math.pi * min(u, 1))
    turn = -0.008 * max(-v, 0) * math.sin(math.pi * min(u, 1)) ** 0.5  # upper half recedes
    tilt = -0.006 * u  # tip slightly farther
    sag = -defl * u**1.7
    xy = b + ax * along + perp * (v * hw)
    rib = 0.00035 * math.exp(-((v * hw) / 0.0007) ** 2) * (1 - 0.7 * u)
    wave = 0.0004 * abs(v) ** 4 * _edge_noise(u * 9 + 3)
    z = lift + cup + curl + turn + tilt + sag * 0.6 + rib + wave
    y_sag = sag * 0.8
    return Vector((xy[0], xy[1] + y_sag, z))


def build_leaf(defl=0.0, nu=420, nv=140):
    verts, faces, uvs = [], [], []
    for j in range(nv + 1):
        v = -1 + 2 * j / nv
        for i in range(nu + 1):
            u = i / nu
            p = leaf_point(u, v, defl)
            verts.append(p)
    for j in range(nv):
        for i in range(nu):
            a = j * (nu + 1) + i
            faces.append((a, a + 1, a + nu + 2, a + nu + 1))
    mesh = bpy.data.meshes.new("leaf")
    mesh.from_pydata(verts, [], faces)
    uv = mesh.uv_layers.new(name="UVMap")
    for poly in mesh.polygons:
        for li in poly.loop_indices:
            vi = mesh.loops[li].vertex_index
            j, i = divmod(vi, nu + 1)
            uv.data[li].uv = (i / nu, j / nv)
    for p in mesh.polygons:
        p.use_smooth = True
    obj = bpy.data.objects.new("leaf", mesh)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def leaf_material():
    veins, height, trans, front = leaf_textures()
    m = bpy.data.materials.new("leaf")
    m.use_nodes = True
    nt = m.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    tex_t = nt.nodes.new("ShaderNodeTexImage")
    tex_t.image = np_image("leaf_trans", trans)
    tex_f = nt.nodes.new("ShaderNodeTexImage")
    tex_f.image = np_image("leaf_front", front)
    tex_h = nt.nodes.new("ShaderNodeTexImage")
    tex_h.image = np_image("leaf_height", height)
    for t in (tex_t, tex_f, tex_h):
        t.interpolation = "Cubic"
    bump = nt.nodes.new("ShaderNodeBump")
    bump.inputs["Strength"].default_value = 1.0
    bump.inputs["Distance"].default_value = 0.00015
    nt.links.new(tex_h.outputs["Color"], bump.inputs["Height"])
    pr = nt.nodes.new("ShaderNodeBsdfPrincipled")
    nt.links.new(tex_f.outputs["Color"], pr.inputs["Base Color"])
    pr.inputs["Roughness"].default_value = 0.28
    pr.inputs["Coat Weight"].default_value = 1.0  # waxy cuticle, wet
    pr.inputs["Coat Roughness"].default_value = 0.08
    nt.links.new(bump.outputs["Normal"], pr.inputs["Normal"])
    nt.links.new(bump.outputs["Normal"], pr.inputs["Coat Normal"])
    tr = nt.nodes.new("ShaderNodeBsdfTranslucent")
    nt.links.new(tex_t.outputs["Color"], tr.inputs["Color"])
    nt.links.new(bump.outputs["Normal"], tr.inputs["Normal"])
    mix = nt.nodes.new("ShaderNodeMixShader")
    mix.inputs["Fac"].default_value = 0.38
    nt.links.new(pr.outputs["BSDF"], mix.inputs[1])
    nt.links.new(tr.outputs["BSDF"], mix.inputs[2])
    nt.links.new(mix.outputs["Shader"], out.inputs["Surface"])
    return m


def water_material():
    m = bpy.data.materials.new("water")
    m.use_nodes = True
    nt = m.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    g = nt.nodes.new("ShaderNodeBsdfGlass")
    g.inputs["IOR"].default_value = 1.333
    g.inputs["Roughness"].default_value = 0.0
    g.inputs["Color"].default_value = (1, 1, 1, 1)
    nt.links.new(g.outputs["BSDF"], out.inputs["Surface"])
    return m


def simple_material(name, color, rough=0.5, coat=0.0, emission=None, strength=1.0):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    nt = m.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    if emission is not None:
        e = nt.nodes.new("ShaderNodeEmission")
        e.inputs["Color"].default_value = (*emission, 1)
        e.inputs["Strength"].default_value = strength
        nt.links.new(e.outputs["Emission"], out.inputs["Surface"])
        return m
    pr = nt.nodes.new("ShaderNodeBsdfPrincipled")
    pr.inputs["Base Color"].default_value = (*color, 1)
    pr.inputs["Roughness"].default_value = rough
    pr.inputs["Coat Weight"].default_value = coat
    pr.inputs["Coat Roughness"].default_value = 0.05
    nt.links.new(pr.outputs["BSDF"], out.inputs["Surface"])
    return m


def drop(name, center, normal, r, mat, flat=0.8, stretch=None):
    bpy.ops.mesh.primitive_uv_sphere_add(segments=48, ring_count=24, radius=r, location=center)
    o = bpy.context.active_object
    o.name = name
    # orient z to the surface normal, flatten along it
    o.rotation_mode = "QUATERNION"
    o.rotation_quaternion = Vector((0, 0, 1)).rotation_difference(normal)
    o.scale = (1.0, 1.0, flat) if stretch is None else (stretch, 1.0, flat)
    bpy.ops.object.shade_smooth()
    o.data.materials.append(mat)
    o.visible_shadow = False  # it sits on the front of the leaf: it must not shade the light coming through
    return o


def leaf_normal(u, v, defl=0.0):
    e = 1e-3
    p = leaf_point(u, v, defl)
    pu = leaf_point(min(u + e, 1), v, defl) - p
    pv = leaf_point(u, min(v + e, 1), defl) - p
    n = pu.cross(pv).normalized()
    if n.z < 0:
        n = -n
    return p, n


def build_scene(T: float, scale: int, samples: int):
    clear()
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
    sc.render.resolution_x = 1920
    sc.render.resolution_y = 1080
    sc.render.resolution_percentage = scale
    sc.render.use_motion_blur = True
    sc.render.motion_blur_shutter = 0.5  # 180 degrees: 1/48 s at 24 fps
    sc.render.fps = 24
    sc.view_settings.view_transform = "Standard"
    sc.render.image_settings.file_format = "OPEN_EXR"
    sc.render.image_settings.color_depth = "32"

    # world: the night sky, very dim blue
    w = bpy.data.worlds.new("night")
    sc.world = w
    w.use_nodes = True
    wnt = w.node_tree
    bg = wnt.nodes["Background"]
    tc = wnt.nodes.new("ShaderNodeTexCoord")
    dot = wnt.nodes.new("ShaderNodeVectorMath")
    dot.operation = "DOT_PRODUCT"
    dot.inputs[1].default_value = tuple(MOON_DIR)
    wnt.links.new(tc.outputs["Generated"], dot.inputs[0])
    pw = wnt.nodes.new("ShaderNodeMath")
    pw.operation = "POWER"
    pw.inputs[1].default_value = 60.0
    clampn = wnt.nodes.new("ShaderNodeMath")
    clampn.operation = "MAXIMUM"
    clampn.inputs[1].default_value = 0.0
    wnt.links.new(dot.outputs["Value"], clampn.inputs[0])
    wnt.links.new(clampn.outputs[0], pw.inputs[0])
    mixc = wnt.nodes.new("ShaderNodeMix")
    mixc.data_type = "RGBA"
    mixc.inputs["A"].default_value = (0.0025, 0.0045, 0.012, 1)
    mixc.inputs["B"].default_value = (0.03, 0.045, 0.08, 1)
    wnt.links.new(pw.outputs[0], mixc.inputs["Factor"])
    wnt.links.new(mixc.outputs["Result"], bg.inputs["Color"])
    bg.inputs["Strength"].default_value = 1.0

    # camera
    cam_d = bpy.data.cameras.new("macro")
    cam_d.lens = FOCAL_MM
    cam_d.sensor_width = SENSOR_MM
    cam_d.sensor_fit = "HORIZONTAL"
    cam_d.clip_start = 0.01
    cam_d.clip_end = 500
    cam_d.dof.use_dof = True
    cam_d.dof.focus_distance = FOCUS
    cam_d.dof.aperture_fstop = FSTOP
    cam_d.dof.aperture_blades = 0  # rounded blades: a circular pupil
    cam_d.dof.aperture_rotation = math.radians(10)
    cam = bpy.data.objects.new("cam", cam_d)
    cam.location = (0, 0, CAM_Z)
    sc.collection.objects.link(cam)
    sc.camera = cam

    # the moon: true angular size, far away
    md = MOON_DIR
    moon_dist = 120.0
    moon_r = moon_dist * math.tan(math.radians(0.26))
    bpy.ops.mesh.primitive_uv_sphere_add(radius=moon_r, location=tuple(Vector((0, 0, CAM_Z)) + md * moon_dist))
    moon = bpy.context.active_object
    moon.data.materials.append(simple_material("moon", None, emission=(0.82, 0.88, 1.0), strength=14.0))
    moon.visible_shadow = False
    # moonlight: a sun lamp from the moon's direction
    sun_d = bpy.data.lights.new("moonlight", "SUN")
    sun_d.energy = 1.1
    sun_d.color = (0.78, 0.86, 1.0)
    sun_d.angle = math.radians(0.52)
    sun = bpy.data.objects.new("moonlight", sun_d)
    sun.rotation_mode = "QUATERNION"
    sun.rotation_quaternion = Vector((0, 0, 1)).rotation_difference(md)  # light travels along -md
    sc.collection.objects.link(sun)

    # a soft cool light off frame, upper left (a distant window): catchlights in the drops
    fill_d = bpy.data.lights.new("fill", "AREA")
    fill_d.energy = 3.0
    fill_d.size = 0.12
    fill_d.color = (0.80, 0.88, 1.0)
    fill = bpy.data.objects.new("fill", fill_d)
    fill.location = (-0.22, 0.30, 0.42)
    fill.rotation_mode = "QUATERNION"
    fill.rotation_quaternion = Vector((0, 0, 1)).rotation_difference((Vector((-0.22, 0.30, 0.42)) - Vector((0, 0, 0))).normalized())
    sc.collection.objects.link(fill)

    # the tree behind: wet leaves at real distances, catching moonlight
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
    mx = nt.nodes.new("ShaderNodeMixShader")
    mx.inputs["Fac"].default_value = 0.5
    nt.links.new(pr.outputs[0], mx.inputs[1])
    nt.links.new(tl.outputs[0], mx.inputs[2])
    nt.links.new(mx.outputs[0], o_.inputs["Surface"])
    k = 0
    tries = 0
    while k < 46 and tries < 2000:
        tries += 1
        D = float(r.uniform(0.25, 2.5))  # metres behind the leaf
        z = -D
        span_x = (CAM_Z - z) * HFOV_T * 1.3
        span_y = (CAM_Z - z) * VFOV_T * 1.3
        x, y = float(r.uniform(-span_x, span_x)), float(r.uniform(-span_y, span_y))
        # keep most of the moon clear: only a couple of leaves cross it
        to = Vector((x, y, z - CAM_Z)).normalized()
        if to.angle(md) < 0.16:
            continue
        k += 1
        size = float(r.uniform(0.05, 0.11)) * (1 + D * 0.3)
        bpy.ops.mesh.primitive_plane_add(size=1.0, location=(x, y, z))
        o = bpy.context.active_object
        o.scale = (size, size * 0.38, 1)
        o.rotation_euler = (float(r.uniform(-0.9, 0.9)), float(r.uniform(-0.9, 0.9)), float(r.uniform(0, math.pi)))
        bpy.ops.object.modifier_add(type="SUBSURF")
        o.data.materials.append(back_mat)
        o.visible_shadow = False  # the tree behind doesn't shade our leaf
        # wet beads on it: glints become bokeh
        for _ in range(int(r.integers(3, 9))):
            bx = x + float(r.uniform(-0.3, 0.3)) * size
            by = y + float(r.uniform(-0.1, 0.1)) * size
            bpy.ops.mesh.primitive_uv_sphere_add(segments=16, ring_count=8, radius=float(r.uniform(0.003, 0.008)), location=(bx, by, z + 0.006))
            bo = bpy.context.active_object
            bo.data.materials.append(water_material())
            bo.visible_shadow = False
    # the branch and petiole
    bark = simple_material("bark", (0.03, 0.028, 0.018), rough=0.6, coat=0.6)
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
    pts = [(-0.075, 0.005, -0.012), (-0.058, 0.03, -0.01), (-0.045, 0.06, -0.008)]
    for bp, p in zip(sp.bezier_points, pts):
        bp.co = p
        bp.handle_left_type = bp.handle_right_type = "AUTO"
    br = bpy.data.objects.new("branch", curve)
    br.data.materials.append(bark)
    sc.collection.objects.link(br)
    pet = bpy.data.curves.new("petiole", "CURVE")
    pet.dimensions = "3D"
    pet.bevel_depth = 0.0007
    sp = pet.splines.new("BEZIER")
    sp.bezier_points.add(2)
    base = leaf_point(0.0, 0.0)
    inside = leaf_point(0.04, 0.0)
    mid_pt = ((-0.0585 + base.x) / 2 + 0.002, (0.03 + base.y) / 2 + 0.003, (-0.0095 + base.z) / 2)
    for bp, p, rad in zip(sp.bezier_points, [(-0.0585, 0.03, -0.0095), mid_pt, tuple(inside)], (1.25, 0.95, 0.35)):
        bp.co = p
        bp.radius = rad
        bp.handle_left_type = bp.handle_right_type = "AUTO"
    po = bpy.data.objects.new("petiole", pet)
    po.data.materials.append(simple_material("petiole", (0.05, 0.08, 0.02), rough=0.4, coat=0.5))
    sc.collection.objects.link(po)

    # the leaf
    leaf = build_leaf(0.0)
    leaf.data.materials.append(leaf_material())
    # drops resting on it
    wm = water_material()
    r = np.random.default_rng(6401)
    for k in range(40):
        u = float(r.uniform(0.12, 0.92))
        v = float(r.uniform(-0.8, 0.85))
        rad = (float(r.random()) ** 2.6) * 0.0021 + 0.00025
        p, n = leaf_normal(u, v)
        flat = 0.62 + 0.25 * (1 - min(rad / 0.0022, 1))
        drop(f"d{k}", p + n * rad * flat * 0.32, n, rad, wm, flat=flat, stretch=1.0 + 0.15 * min(rad / 0.002, 1))
    # the pendant drop at the tip
    p, n = leaf_normal(1.0, 0.0)
    drop("tip", p + Vector((0.0004, -0.0011, 0.0)), Vector((0, -1, 0.15)).normalized(), 0.0014, wm, flat=1.15)

    # rain: drops falling through the scene, blurred by the shutter
    sc.frame_set(1)
    r = np.random.default_rng(6601 + int(T * 24))
    rain_mat = water_material()
    for k in range(420):
        z = float(r.uniform(-1.5, 0.25))
        span_x = (CAM_Z - z) * HFOV_T * 1.1
        span_y = (CAM_Z - z) * VFOV_T * 1.3
        x, y = float(r.uniform(-span_x, span_x)), float(r.uniform(-span_y, span_y))
        if Vector((x, y, z - CAM_Z)).normalized().angle(md) < 0.12:
            continue
        bpy.ops.mesh.primitive_uv_sphere_add(segments=12, ring_count=8, radius=float(r.uniform(0.0008, 0.0016)), location=(x, y, z))
        o = bpy.context.active_object
        o.data.materials.append(rain_mat)
        o.visible_shadow = False
        vel = 7.0 / 24.0  # metres per frame
        o.keyframe_insert("location", frame=1)
        o.location = (x + 0.02 * vel, y - vel, z)
        o.keyframe_insert("location", frame=2)
    for k in range(5):
        z = float(r.uniform(0.15, 0.27))  # 6-18 cm in front of the lens
        span_x = (CAM_Z - z) * HFOV_T
        x, y = float(r.uniform(-span_x, span_x)), float(r.uniform(-span_x * 0.56, span_x * 0.56))
        bpy.ops.mesh.primitive_uv_sphere_add(segments=24, ring_count=12, radius=float(r.uniform(0.0012, 0.002)), location=(x, y, z))
        o = bpy.context.active_object
        o.data.materials.append(rain_mat)
        o.visible_shadow = False
        o.keyframe_insert("location", frame=1)
        o.location = (x + 0.006, y - 7.0 / 24.0, z)
        o.keyframe_insert("location", frame=2)
    sc.frame_set(1)
    return sc


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--still", type=float, default=61.0)
    ap.add_argument("--out", required=True)
    ap.add_argument("--scale", type=int, default=100)
    ap.add_argument("--samples", type=int, default=128)
    a = ap.parse_args(argv)
    sc = build_scene(a.still, a.scale, a.samples)
    sc.render.filepath = a.out
    bpy.ops.render.render(write_still=True)


if __name__ == "__main__":
    main(sys.argv[1:])


def exr_to_npy(src: str, dst: str) -> None:
    """Load a linear EXR through Blender and save it as a float32 .npy (H, W, 3)."""
    img = bpy.data.images.load(src)
    w, h = img.size
    a = np.empty(w * h * 4, np.float32)
    img.pixels.foreach_get(a)
    np.save(dst, a.reshape(h, w, 4)[::-1, :, :3].copy())

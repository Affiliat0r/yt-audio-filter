"""Water on a leaf, by the physics: shapes and the numbers that set the motion.

No Blender here; nuh.blend_shot turns these into meshes and keyframes.

* A hanging drop takes the Young-Laplace shape for its volume, pinned to the
  leaf tip. Past the largest volume that shape allows (the critical volume)
  there is no equilibrium: the drop necks and lets go. What falls is about
  62 % of it and the rest stays on the tip (Harkins-Brown).
* A drop resting on the leaf is a spherical cap at the leaf's contact angle,
  sagged downhill by gravity.
* A bead slides when gravity along the leaf beats contact-angle hysteresis,
  and slides slowly, held back by friction where the leaf meets the drop's
  edge. It jerks forward and slows wherever the leaf's veins pin it.
* The leaf swings on its stalk as a damped spring, loaded by the water on it
  and kicked by the raindrop's momentum.

SI units throughout.
"""

from __future__ import annotations

import math

import numpy as np

RHO = 1000.0  # water, kg/m^3
GAMMA = 0.072  # surface tension, N/m
G = 9.81
LC = math.sqrt(GAMMA / (RHO * G))  # capillary length, 2.7 mm

FALL_FRACTION = 0.62  # Harkins-Brown: share of a critical pendant that falls


# ---------------------------------------------------------------- the hanging drop


def _yl_rhs(st, b):
    x, z, phi, _v = st
    sx = max(x, 1e-12)
    return np.array([math.cos(phi), math.sin(phi), 2.0 / b - z / LC**2 - math.sin(phi) / sx, math.pi * x * x * math.sin(phi)])


def pendant_profile(b: float, rc: float, ds: float = 2e-6):
    """Integrate the Young-Laplace equation up from the apex of a drop hanging
    with apex radius of curvature b, until it narrows back to the contact
    radius rc. z is height above the apex. Returns (x, z, volume) or None
    when no such shape exists."""
    s0 = ds
    st = np.array([s0, s0 * s0 / (2 * b), s0 / b, 0.0])
    xs, zs = [0.0, st[0]], [0.0, st[1]]
    for _ in range(200000):
        k1 = _yl_rhs(st, b)
        k2 = _yl_rhs(st + 0.5 * ds * k1, b)
        k3 = _yl_rhs(st + 0.5 * ds * k2, b)
        k4 = _yl_rhs(st + ds * k3, b)
        st = st + ds / 6 * (k1 + 2 * k2 + 2 * k3 + k4)
        xs.append(st[0])
        zs.append(st[1])
        if st[2] > math.pi / 2 and st[0] <= rc:
            return np.array(xs), np.array(zs), st[3]
        if st[2] > math.pi * 1.05 or st[0] <= 0:
            return None
    return None


def pendant_family(rc: float, n: int = 90):
    """Volumes of the static hanging shapes on a tip of contact radius rc,
    from small to critical. Returns a list of (volume, x, z)."""
    fam = []
    for b in np.geomspace(0.12 * LC, 1.6 * LC, n):
        p = pendant_profile(float(b), rc)
        if p is None:
            break
        x, z, v = p
        if fam and v < fam[-1][0]:
            break  # past the critical shape: the stable branch ends
        fam.append((v, x, z))
    return fam


def resample_profile(x, z, n: int):
    """n points evenly in arc length from apex to contact."""
    s = np.concatenate([[0], np.cumsum(np.hypot(np.diff(x), np.diff(z)))])
    t = np.linspace(0, s[-1], n)
    return np.interp(t, s, x), np.interp(t, s, z)


# ---------------------------------------------------------------- drops sitting on the leaf


def cap_radius(volume: float, theta: float) -> float:
    """Sphere radius of a cap of this volume at contact angle theta."""
    c = math.cos(theta)
    return (3 * volume / (math.pi * (2 + c) * (1 - c) ** 2)) ** (1 / 3)


def sessile_mesh(volume: float, theta: float, slope: float, rings: int = 18, segs: int = 40):
    """A drop resting on a surface whose downhill direction is local +x, at
    contact angle theta. slope = sine of the surface's tilt from horizontal.
    Gravity sags the drop downhill: the Bond number sets how far. Returns
    (verts, faces) with the base on z = 0."""
    R = cap_radius(volume, theta)
    h = R * (1 - math.cos(theta))
    bond = (R / LC) ** 2
    shear = 0.55 * bond * slope  # top shifts downhill by this fraction of R
    verts = [(0.0, 0.0, h)]
    for i in range(1, rings + 1):
        a = theta * i / rings
        for j in range(segs):
            f = 2 * math.pi * j / segs
            x, y, z = R * math.sin(a) * math.cos(f), R * math.sin(a) * math.sin(f), R * math.cos(a) - R * math.cos(theta)
            x += shear * R * (z / h) * (1 - 0.3 * z / h)
            z *= 1 - 0.25 * bond * (1 - slope)
            verts.append((x, y, z))
    verts.append((0.0, 0.0, -0.00002))
    faces = [(0, 1 + (j + 1) % segs, 1 + j) for j in range(segs)]
    for i in range(rings - 1):
        r0, r1 = 1 + i * segs, 1 + (i + 1) * segs
        for j in range(segs):
            faces.append((r0 + j, r0 + (j + 1) % segs, r1 + (j + 1) % segs, r1 + j))
    last, c = 1 + (rings - 1) * segs, len(verts) - 1
    faces += [(last + j, last + (j + 1) % segs, c) for j in range(segs)]
    return verts, faces


def pendant_mesh(profiles, segs: int = 48):
    """One mesh topology for every hanging shape: profiles is a list of
    (x, z) resampled to the same length. The contact ring sits at the origin
    and the drop hangs toward -y. Returns (faces, [verts per profile])."""
    n = len(profiles[0][0])
    faces = [(0, 1 + (j + 1) % segs, 1 + j) for j in range(segs)]
    for i in range(n - 2):
        r0, r1 = 1 + i * segs, 1 + (i + 1) * segs
        for j in range(segs):
            faces.append((r0 + j, r1 + j, r1 + (j + 1) % segs, r0 + (j + 1) % segs))
    last, c = 1 + (n - 2) * segs, 1 + (n - 1) * segs
    faces += [(last + j, c, last + (j + 1) % segs) for j in range(segs)]
    shapes = []
    for x, z in profiles:
        top = z[-1]
        vs = [(0.0, z[0] - top, 0.0)]
        for i in range(1, n):
            for j in range(segs):
                f = 2 * math.pi * j / segs
                vs.append((x[i] * math.cos(f), z[i] - top, x[i] * math.sin(f)))
        vs.append((0.0, 0.0, 0.0))
        shapes.append(vs)
    return faces, shapes


# ---------------------------------------------------------------- a bead sliding down


def slide(path_len, along_g, start_speed, hyst, drag, dt=1e-4, t_max=6.0):
    """Integrate a bead along its path. along_g(s): gravity along the path
    (m/s^2); hyst(s): the pinning deceleration there; drag: the friction rate
    (1/s). Returns times and arc positions until it reaches path_len."""
    t, s, v = 0.0, 0.0, start_speed
    ts, ss = [0.0], [0.0]
    while s < path_len and t < t_max:
        a = along_g(s) - drag * v
        if v > 0 or a - hyst(s) > 0:
            a -= hyst(s)
        else:
            a = 0.0
        v = max(v + a * dt, 0.0)
        s += v * dt
        t += dt
        ts.append(t)
        ss.append(s)
    return np.array(ts), np.array(ss)


# ---------------------------------------------------------------- the leaf on its stalk


def swing(times, inertia, freq, zeta, torque, kicks, loops=4):
    """theta(t) of a damped torsional spring over one loop of length
    times[-1], driven by torque(t) (periodic, N m) and angular-momentum kicks
    [(t, N m s)]. Several loops are run so the start-up dies away and the
    returned loop is periodic."""
    period = float(times[-1])
    w = 2 * math.pi * freq
    k = inertia * w * w
    c = 2 * zeta * inertia * w
    dt = 1 / 4800
    th, om = 0.0, 0.0
    out = np.zeros(len(times))
    n = int(round(period / dt))
    for lp in range(loops):
        kq = sorted(kicks)
        ki = 0
        j = 0
        for i in range(n + 1):
            t = i * dt
            while ki < len(kq) and kq[ki][0] <= t:
                om += kq[ki][1] / inertia
                ki += 1
            if lp == loops - 1:
                while j < len(times) and times[j] <= t + 1e-9:
                    out[j] = th
                    j += 1
            a = (torque(t) - k * th - c * om) / inertia
            om += a * dt
            th += om * dt
    return out, k

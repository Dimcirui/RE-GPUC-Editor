"""The preview simulation: an approximation of the game's cloth on the parts' particles,
for a feel of the values; not the game's solver (physsim and Wilds-Dynamics run that). Only the particles move:
nothing is written to the file, the part mesh or the armature; the display draws the result.

Each frame is one step of 1/60 s (a timer, independent of the timeline; the game's FrameRate 60, so the header's
Calculate Mode never splits it). The particles' weight is the effective w = min(1, Config SimulationStrength x
the simulation weight):
  0. the root follow: the Part's root joint's move and turn since the last frame carried into the particles by
     the Config's BlendAmounts (Overwrite) or its curves at min(1, speed / curve speed), with the speed limits
     only the motion beyond them;
  1. integrate: the velocity (the last step's move) cut to the header's Max Velocity (m a step), x (1 - the
     Config's VelocityDamping, or its SecondVelocityDamping when Second damping is on and the speed is over
     SecondDampingSpeed), plus the force objects (props.REE_GPUC_Force, along each one's +Z): the constant ones x
     the Config's GravityFactor, the winds swinging between Low and High, each particle's x |normal . wind
     direction| x its inverse mass / WIND_MASS;
  2. LoopCount times: the links, batch by batch (the generator's colouring: no two links of a batch share a
     particle), each pulling its pair back to the rest length by its shape's stiffness (x the multiplier while
     lo < rest / length < hi), the correction shared by the ends' w as the game does; then the colliders push
     particles out (collide(): the side judged from the Config's continuous type, Info State bit 0), the
     backstops push them out of their spheres, and limit() holds each near its skinned place by the Config Info
     State (a sphere of the max distance; with SpaceLimitSphere also cut along the normal; with
     SpaceLimitCylinder a cylinder: the max distance across the normal, the space limits along it);
  3. w: 0 follows the skinned place (the posed bones, or the rest pose), between 0 and 1 the place is blended
     towards it.
The per-iteration stiffness is 1 - (1 - k)^(1 / LoopCount), so LoopCount spreads the correction but leaves its
total to k. The display (shown()) takes the Config's BlendWeight of the simulated offset from the skinned place
(the game blends the cloth bones' matrices so; the state itself is not blended)."""
import math

import bpy
import numpy as np

from . import canon, collision, draw, props
from .modules import gpuc_build

DT = 1.0 / 60.0
_state: dict = {}            # part name -> {"x", "prev", "key"}
_batches: dict = {}          # (rows, cols, closed) -> (links (n x 4) ordered by batch, batch sizes)
_clock = {"t": 0.0, "running": False}
_inputs: dict = {}           # Config name -> the curves' inputs last frame (translation, rotation), for the graph


def curve_inputs(cfg_obj):
    return _inputs.get(cfg_obj.name) if running() else None


def running() -> bool:
    return _clock["running"]


def positions(obj):
    """The part's simulated particles (world space), None when it is not being simulated."""
    st = _state.get(obj.name)
    return None if st is None else st["x"]


def invalidate(obj):
    """Start this Part from its new rest geometry on the next simulation step."""
    _state.pop(obj.name, None)


def shown(obj):
    """Where the part's particles are drawn while simulated: the Config's BlendWeight of the simulated offset from
    the skinned place. None when it is not being simulated."""
    st = _state.get(obj.name)
    if st is None:
        return None
    cfg_obj = obj.ree_gpuc_part.config
    bw = cfg_obj.ree_gpuc_config.blend_weight if props.is_config(cfg_obj) else 1.0
    return st["skin"] + min(max(bw, 0.0), 1.0) * (st["x"] - st["skin"])


def batches(d: dict):
    """The links coloured into batches: (a, b, type, shape, index into canon.links) rows, batch sizes."""
    ln = draw.links(d)
    key = ("L", hash(ln.tobytes())) if d["locked"] else (d["rows"], d["cols"], d["closed"])
    if key not in _batches:
        rows = np.column_stack([ln, np.arange(len(ln))])
        ordered, sizes = gpuc_build._colour([tuple(r) for r in rows.tolist()], key=lambda r: (r[0], r[1]))
        _batches[key] = (np.array(ordered, np.int64).reshape(-1, 5), sizes)
    return _batches[key]


def colliders(scene) -> list:
    """Every visible collider, world space, each ending in (source, index): ("SPHERE", centre, radius, ...),
    ("CAPSULE", a, b, radius at a, radius at b, ...), ("PLANE", origin, unit normal, drawn size, x axis, ...),
    ("BOX", centre, unit axes (3 x 3, columns), half sizes, ...). source OWN for the .gpuc's own capsules
    (collision.py; UNBOUND while an end has no bone), ("CLSP", flag A, flag B) for the Reference CLSP's shapes, TEST for the preview's test
    colliders; index: the shape's place among its kind (the own ones first, then the CLSP's, as the game
    orders them, then the test ones, each by name), the bit of the Part's collision bits that takes it."""
    from . import collision
    cloth = scene.ree_gpuc.target
    out = [("CAPSULE", a, b, ra, rb, "OWN" if collision.bound(root) else "UNBOUND") for root, a, b, ra, rb in
           sorted(collision.geometry(scene), key=lambda g: g[0].name)
           if cloth is None or root.name in cloth.all_objects]           # the active cloth's own only
    for _, kind, a, b, ra, rb, fa, fb in collision.clsp_shapes(collision.reference_clsp(scene)):
        source = ("CLSP", fa, fb)
        out.append(("SPHERE", a, ra, source) if kind == "SPHERE" else ("CAPSULE", a, b, ra, rb, source))
    for obj in sorted((o for o in scene.objects if props.is_collider(o) and o.visible_get() and ours(scene, o)),
                      key=lambda o: o.name):
        c = obj.ree_gpuc_collider
        m = np.array(obj.matrix_world, float)
        scale = float(np.cbrt(abs(np.linalg.det(m[:3, :3]))))
        r = c.radius * scale
        if c.kind == "SPHERE":
            out.append(("SPHERE", m[:3, 3].copy(), r, "TEST"))
        elif c.kind == "CAPSULE":
            half = 0.5 * c.length
            a = m[:3, :3] @ np.array((0.0, 0.0, -half)) + m[:3, 3]
            b = m[:3, :3] @ np.array((0.0, 0.0, half)) + m[:3, 3]
            out.append(("CAPSULE", a, b, r, r, "TEST"))
        elif c.kind == "PLANE":
            out.append(("PLANE", m[:3, 3].copy(), _unit(m[:3, 2]), c.plane_size * scale, _unit(m[:3, 0]), "TEST"))
        else:
            lengths = np.linalg.norm(m[:3, :3], axis=0)
            axes = m[:3, :3] / np.maximum(lengths, 1e-12)
            out.append(("BOX", m[:3, 3].copy(), axes, 0.5 * np.array(c.size) * lengths, "TEST"))
    count: dict = {}
    indexed = []
    for shape in out:
        i = count.get(shape[0], 0)
        count[shape[0]] = i + 1
        indexed.append(shape + (i,))
    return indexed


BIT_KINDS = {"PLANE": "plane_bits", "SPHERE": "sphere_bits", "CAPSULE": "capsule_bits", "BOX": "obb_bits"}


def taken(shape, part) -> bool:
    """Whether the Part's collision bits take the shape (bit = its index among its kind; past bit 31 never) and,
    for a CLSP shape, its flags meet the Part's CLSP Flags (A with A or B with B)."""
    index, source = shape[-1], shape[-2]
    if isinstance(source, tuple) and not ((source[1] & part.clsp_flags_a) or (source[2] & part.clsp_flags_b)):
        return False
    return index < 32 and bool(((getattr(part, BIT_KINDS[shape[0]]) & 0xFFFFFFFF) >> index) & 1)


def _near(shape, p):
    """A sphere's centre or a capsule's nearest axis point to each of p, and the radius there (a tapered
    capsule's between its ends' radii)."""
    if shape[0] == "SPHERE":
        return np.broadcast_to(shape[1], p.shape), np.full(len(p), shape[2])
    a, b, ra, rb = shape[1], shape[2], shape[3], shape[4]
    ab = b - a
    t = np.clip(((p - a) @ ab) / max(float(ab @ ab), 1e-12), 0.0, 1.0)
    return a + t[:, None] * ab, ra + t * (rb - ra)


def _units(v, *fallbacks) -> np.ndarray:
    """v's rows as unit vectors; a row too short for a direction takes the first fallback's that is long enough."""
    out = np.zeros_like(v)
    todo = np.ones(len(v), bool)
    for f in (v,) + fallbacks:
        n = np.linalg.norm(f, axis=1)
        ok = todo & (n > 1e-9)
        out[ok] = f[ok] / n[ok, None]
        todo &= ~ok
    return out


def collide(x, radius, shapes, w, ref, before=None, normal=None, friction: float = 0.0) -> None:
    """Push particles out of the shapes (in place), the game's Legacy collision: a particle
    with w > 0 whose centre is nearer a shape than its radius goes out along the push direction, by the depth x
    w. The direction is judged from ref: the shape (a capsule's nearest axis point, a box's nearest face) towards
    ref. Skinning: ref the skinned places, against the shapes now (before None); Previous: ref the last step's
    places, against the shapes a frame ago (before, matching shapes), so a shape's move along it counts in the
    depth. Planes push along their normal. On planes and boxes the friction then takes the particle that share
    of the way to ref. normal: the particles' normals, the direction where ref gives none (on a capsule's axis)."""
    movable = w > 0
    rows = np.arange(len(x))
    for i, shape in enumerate(shapes):
        then = before[i] if before is not None else shape
        if shape[0] == "PLANE":
            n = np.broadcast_to(shape[2], x.shape)
            depth = radius - (x - shape[1]) @ shape[2]
            hit = movable & (depth > 0)
        elif shape[0] == "BOX":
            local = (x - shape[1]) @ shape[2]                     # the box's own coordinates
            inside = (np.abs(local) < shape[3] + radius[:, None]).all(axis=1)
            side = (ref - then[1]) @ then[2]                      # ref in the box's coordinates (then)
            axis = np.argmax(np.abs(side) - then[3], axis=1)      # the face ref is nearest outside of
            sign = np.where(side[rows, axis] >= 0, 1.0, -1.0)
            n = then[2].T[axis] * sign[:, None]
            depth = shape[3][axis] + radius - local[rows, axis] * sign
            hit = movable & inside & (depth > 0)
        else:
            near, r = _near(shape, x)
            reach = r + radius
            inside = np.linalg.norm(x - near, axis=1) < reach
            near_ref, _ = _near(then, ref)
            fallback = (normal,) if normal is not None else ()
            n = _units(ref - near_ref, x - near, *fallback)
            depth = reach - np.einsum("ij,ij->i", x - near, n)
            hit = movable & inside & (depth > 0)
        if hit.any():
            x[hit] += n[hit] * (depth[hit] * w[hit])[:, None]
            if shape[0] in {"PLANE", "BOX"} and friction > 0:
                x[hit] += friction * (ref[hit] - x[hit])


def _skinned(obj, d, follow_pose: bool) -> np.ndarray:
    return draw.world(obj, d, follow_pose)[0]


def reset(obj, scene) -> None:
    d = canon.load(obj)
    x = _skinned(obj, d, scene.ree_gpuc_sim.follow_pose)
    _state[obj.name] = {"x": x.copy(), "prev": x.copy(), "skin": x.copy()}


WIND_MASS = 100.0          # the inverse mass a wind's acceleration is given for (Hope's, ch03_001_0002's, ~100)


def root_matrix(obj) -> np.ndarray:
    """The Part's root joint in world space (posed), else its armature's (or its own) matrix."""
    dg = bpy.context.evaluated_depsgraph_get()
    part = obj.ree_gpuc_part
    arm = part.armature
    if arm is None or arm.type != "ARMATURE":
        return np.array(obj.evaluated_get(dg).matrix_world, float)
    from . import reference
    owner = reference.bone_owner(reference.cloth_of(obj), arm, part.root_joint) if part.use_root_joint else None
    ev = (owner or arm).evaluated_get(dg)            # a root joint the armature lacks: the reference skeleton's
    m = np.array(ev.matrix_world, float)
    pb = ev.pose.bones.get(part.root_joint) if owner is not None else None
    return m @ np.array(pb.matrix, float) if pb is not None else m


def _rotate(p, q, pivot) -> np.ndarray:
    return (np.array(q.to_matrix(), float) @ (p - pivot).T).T + pivot


def follow(st, root, cfg, x, prev):
    """The root's motion since the last frame carried into the particles by the Config's BlendAmounts (Overwrite)
    or its curves at min(1, speed / curve speed); with a speed limit only the motion beyond it. x and prev move
    alike, so what follows keeps no extra velocity: what does not follow swings out. Returns the curves' inputs."""
    from mathutils import Matrix, Quaternion
    last = st.get("root")
    st["root"] = root
    if last is None or cfg is None:
        return None
    a, b = last[:3, 3], root[:3, 3]
    move = b - a
    q = (Matrix(root[:3, :3].tolist()).to_quaternion() @ Matrix(last[:3, :3].tolist()).to_quaternion().inverted())
    angle = q.angle if q.angle <= math.pi else q.angle - math.tau
    speed, turn = float(np.linalg.norm(move)) / DT, abs(math.degrees(angle)) / DT
    xt = min(1.0, speed / cfg.translation_speed) if cfg.translation_speed > 0 else 1.0
    xr = min(1.0, turn / cfg.rotation_speed) if cfg.rotation_speed > 0 else 1.0
    bt = cfg.blend_translation if cfg.overwrite_translation else float(cfg.translation_curve.value(xt))
    br = cfg.blend_rotation if cfg.overwrite_rotation else float(cfg.rotation_curve.value(xr))
    if cfg.use_speed_limit and speed > 0:
        bt *= max(0.0, speed - cfg.speed_limit) / speed
    if cfg.use_angular_speed_limit and turn > 0:
        br *= max(0.0, turn - cfg.angular_speed_limit) / turn
    part_q = Quaternion(q.axis, angle * br)
    pivot = a + bt * move
    for p in (x, prev):
        p += bt * move
        p[:] = _rotate(p, part_q, pivot)
    return xt, xr


def limit(x, skin, normal, max_d, lower, upper, state, movable) -> None:
    """Hold each particle near its skinned place (in place), by the Config Info State: the max distance in 3D
    (SkinningCollision alone), then along the normal lower inward / upper outward (SpaceLimitSphere), or the max
    distance across the normal only and the normal limits along it (SpaceLimitCylinder)."""
    off = x - skin
    dn = np.einsum("ij,ij->i", off, normal)
    if state & 4:
        side = off - dn[:, None] * normal
        far = np.linalg.norm(side, axis=1)
        over = movable & (far > max_d) & (far > 0)
        side[over] *= (max_d[over] / far[over])[:, None]
        dn = np.where(movable, np.clip(dn, -lower, upper), dn)
        x[:] = skin + side + dn[:, None] * normal
        return
    far = np.linalg.norm(off, axis=1)
    over = movable & (far > max_d) & (far > 0)
    x[over] = skin[over] + off[over] * (max_d[over] / far[over])[:, None]
    if state & 2:
        dn = np.einsum("ij,ij->i", x - skin, normal)
        fix = movable & ((dn < -lower) | (dn > upper))
        x[fix] += (np.clip(dn[fix], -lower[fix], upper[fix]) - dn[fix])[:, None] * normal[fix]


def backstop(x, skin, normal, r, offset, movable) -> None:
    """Push particles out of their backstop spheres (in place): radius r, the centre inside along the normal so
    the surface is offset below the skinned place."""
    on = movable & (r > 0)
    if not on.any():
        return
    centre = skin[on] - normal[on] * (r[on] + offset[on])[:, None]
    d = x[on] - centre
    dist = np.linalg.norm(d, axis=1)
    inside = dist < r[on]
    if inside.any():
        d = np.where((dist > 1e-9)[:, None], d, normal[on])
        dist = np.maximum(dist, 1e-9)
        pushed = centre + d * (r[on] / dist)[:, None]
        sub = x[on]
        sub[inside] = pushed[inside]
        x[on] = sub


def step(obj, scene, t: float) -> None:
    """One frame of the part's particles."""
    s = scene.ree_gpuc_sim
    d = canon.load(obj)
    n = canon.count(d)
    st = _state.get(obj.name)
    if st is None or len(st["x"]) != n:
        reset(obj, scene)
        st = _state[obj.name]
    vals = canon.values(obj, d)
    radius = vals["radius"].astype(float)
    max_d = vals["max_distance"].astype(float)
    lower, upper = vals["space_limit_lower"].astype(float), vals["space_limit_upper"].astype(float)
    back_r, back_off = vals["backstop_radius"].astype(float), vals["backstop_offset"].astype(float)
    skin = _skinned(obj, d, s.follow_pose)
    normal = draw.particle_normals(obj, d, skin)
    cfg_obj = obj.ree_gpuc_part.config
    cfg = cfg_obj.ree_gpuc_config if props.is_config(cfg_obj) else None
    state = cfg.info_state if cfg else 1
    strength_k = cfg.simulation_strength if cfg else 1.0
    w = np.minimum(1.0, max(strength_k, 0.0) * np.clip(vals["sim_weight"].astype(float), 0.0, 1.0))
    hdr = props.header(scene.ree_gpuc.target)
    max_v = hdr.ree_gpuc_header.max_velocity if hdr is not None else 0.0
    constant, wind = forces(scene, t)
    gravity = (cfg.gravity_factor if cfg else 1.0) * constant
    movable = w > 0.0
    x, prev = st["x"], st["prev"]
    inputs = follow(st, root_matrix(obj), cfg, x, prev)
    if inputs is not None and cfg_obj is not None:
        _inputs[cfg_obj.name] = inputs
    v = x - prev
    if max_v > 0:                    # the header's Max Velocity: the most a particle moves a step
        speed = np.linalg.norm(v, axis=1)
        fast = speed > max_v
        v[fast] *= (max_v / speed[fast])[:, None]
    v *= 1.0 - np.clip(damping(cfg, np.linalg.norm(v, axis=1) / DT), 0.0, 1.0)[:, None]
    st["prev"] = x.copy()
    strength = float(np.linalg.norm(wind))
    if strength > 0:                 # a sail: face-on full, edge-on none; x the inverse mass over WIND_MASS
        facing = np.abs(normal @ (wind / strength))
        blown = wind[None, :] * (facing * vals["inverse_mass"].astype(float) / WIND_MASS)[:, None]
    else:
        blown = 0.0
    x = x + v + (gravity + blown) * DT * DT
    x[~movable] = skin[~movable]

    links, sizes = batches(d)
    part = obj.ree_gpuc_part
    tails = canon.link_tails(obj, d)[links[:, 4]]                                       # link x (k, k2, lo, hi)
    loops = s.iterations
    at_rest = draw.world(obj, d, False)[0]                                                    # world space, scaled
    rest = np.linalg.norm(at_rest[links[:, 0]] - at_rest[links[:, 1]], axis=1)
    k, k2, lo, hi = (tails[:, c] for c in range(4))
    shares = w[links[:, 0]] + w[links[:, 1]] + 0.01
    wa, wb = w[links[:, 0]] / shares, w[links[:, 1]] / shares
    starts = np.cumsum([0] + list(sizes))
    shapes = [sh for sh in colliders(scene) if taken(sh, part)]
    begin = st["prev"]                                  # where the particles were at the frame's start
    keys = [(sh[0], sh[-1]) for sh in shapes]
    last = st.get("shapes", {})
    if previous(cfg):                                   # the side from the last step, against the shapes then
        ref, before = begin, [last.get(key, sh) for key, sh in zip(keys, shapes)]
    else:                                               # Skinning: from the skinned places, the shapes now
        ref, before = skin, None
    st["shapes"] = dict(zip(keys, shapes))
    rub = min(max(part.friction, 0.0), 1.0)
    for _ in range(loops):
        for b0, b1 in zip(starts[:-1], starts[1:]):
            a, b = links[b0:b1, 0], links[b0:b1, 1]
            dvec = x[b] - x[a]
            length = np.linalg.norm(dvec, axis=1) + 1e-9
            r = rest[b0:b1] / length
            kk = np.where((lo[b0:b1] < r) & (r < hi[b0:b1]), k[b0:b1] * k2[b0:b1], k[b0:b1])
            ke = 1.0 - np.power(1.0 - np.clip(kk, 0.0, 1.0), 1.0 / loops)
            corr = dvec * (ke * (length - rest[b0:b1]) / length)[:, None]
            x[a] += corr * wa[b0:b1, None]
            x[b] -= corr * wb[b0:b1, None]
        collide(x, radius, shapes, w, ref, before, normal, rub)
        backstop(x, skin, normal, back_r, back_off, movable)
        limit(x, skin, normal, max_d, lower, upper, state, movable)
    x = skin + w[:, None] * (x - skin)
    st["x"], st["skin"] = x, skin


def damping(cfg, speed) -> np.ndarray:
    """Each particle's velocity damping: the Config's VelocityDamping, its SecondVelocityDamping with Second
    damping on and the speed (m/s) over SecondDampingSpeed."""
    if cfg is None:
        return np.zeros_like(speed)
    if not cfg.second_damping:
        return np.full_like(speed, cfg.velocity_damping)
    return np.where(speed > cfg.second_damping_speed, cfg.second_velocity_damping, cfg.velocity_damping)


def previous(cfg) -> bool:
    """Whether the Config collides by Previous (its Info State lacks SkinningCollision, bit 0, the component's
    type left at the vanilla 1), else Skinning."""
    return cfg is not None and not cfg.info_state & 1


def ours(scene, obj) -> bool:
    """Whether a preview object (a force, a test collider) acts on the active cloth: it lies in that cloth's
    collection, or in no cloth's (another cloth's forces and colliders are that cloth's)."""
    owners = [c for c in bpy.data.collections if props.is_gpuc_collection(c) and obj.name in c.all_objects]
    return not owners or scene.ree_gpuc.target in owners


def force_objects(scene) -> list:
    return [o for o in scene.objects if props.is_force(o) and o.visible_get() and ours(scene, o)]


def force_object(scene, kind: str):
    """The active cloth's force object of a kind (the gravity, the wind), shown or not; None before it is made."""
    return next((o for o in scene.objects if props.is_force(o) and o.ree_gpuc_force.kind == kind and ours(scene, o)),
                None)


def direction(obj) -> np.ndarray:
    """A force object's +Z in world space."""
    return _unit(np.array(obj.matrix_world, float)[:3, 2])


def gain(obj) -> float:
    """A force object's scale along its +Z (world, parents included): the strength is the panel's value x this,
    so scaling the arrow scales the force."""
    return float(np.linalg.norm(np.array(obj.matrix_world, float)[:3, 2]))


def forces(scene, t: float):
    """The constant forces' and the winds' accelerations now (world space, m/s²)."""
    constant, wind = np.zeros(3), np.zeros(3)
    for obj in force_objects(scene):
        f = obj.ree_gpuc_force
        a = f.strength(t) * gain(obj) * direction(obj)
        if f.kind == "CONSTANT":
            constant += a
        else:
            wind += a
    return constant, wind


def time() -> float:
    return _clock["t"]


def _unit(v) -> np.ndarray:
    v = np.array(v, float)
    n = np.linalg.norm(v)
    return v / n if n > 0 else v


def parts(scene) -> list:
    return [o for o in props.parts(scene.ree_gpuc.target) if o.visible_get() and canon.has(o)]


def tick(scene) -> None:
    """One frame of every visible part of the target cloth; the parts of a cloth no longer active drop their
    state (shown at rest again)."""
    _clock["t"] += DT
    objs = [o for o in parts(scene) if o.mode != "EDIT"]
    for name in set(_state) - {o.name for o in objs}:
        del _state[name]
    for obj in objs:
        step(obj, scene, _clock["t"])
    weld(scene)


def weld(scene) -> None:
    """The cloth's welds (weld.welds: zero-length, full-stiffness links joining two parts) after the parts' own
    steps: each pair onto one point, weighed by their simulation weights (a pinned one holds the other). Once a
    frame, not each solver loop: the parts step one by one."""
    weights = {}
    from . import weld as welds
    for _, a, ia, b, ib in welds.welds(scene.ree_gpuc.target):
        sa, sb = _state.get(a.name), _state.get(b.name)
        if sa is None or sb is None or ia >= len(sa["x"]) or ib >= len(sb["x"]):
            continue
        for o in (a, b):
            if o.name not in weights:
                weights[o.name] = np.clip(canon.load(o)["sim_weight"], 0.0, 1.0)
        wa, wb = float(weights[a.name][ia]), float(weights[b.name][ib])
        if wa + wb <= 0.0:
            continue
        p = (wb * sa["x"][ia] + wa * sb["x"][ib]) / (wa + wb)          # wa 0: a's place; wb 0: b's
        sa["x"][ia] = sb["x"][ib] = p


def _timer():
    if not _clock["running"]:
        return None
    scene = bpy.context.scene
    try:
        tick(scene)
    except Exception as e:  # noqa: BLE001 - a broken frame stops the preview, said in the panel
        _clock["running"] = False
        scene["ree_gpuc_sim_error"] = str(e)
        return None
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == "VIEW_3D":
                area.tag_redraw()
    return DT


def add_force(scene, kind: str, location=(0.0, 0.0, 0.0), coll=None) -> bpy.types.Object:
    """A force object: a constant one pointing down (Earth's gravity), a wind blowing towards +Y."""
    obj = bpy.data.objects.new("GPUC_GRAVITY" if kind == "CONSTANT" else "GPUC_WIND", None)
    obj["TYPE"] = props.FORCE_TYPE
    obj.empty_display_type, obj.empty_display_size = "SINGLE_ARROW", 0.3
    obj.hide_render = True
    obj.location = location
    obj.rotation_euler = (math.pi, 0.0, 0.0) if kind == "CONSTANT" else (-math.pi / 2, 0.0, 0.0)
    (collision.sim_collection(coll) if coll is not None else scene.collection).objects.link(obj)
    obj.ree_gpuc_force.kind = kind
    return obj


def start(scene) -> None:
    if props.is_re4(scene.ree_gpuc.target):
        raise ValueError("RE4 currently supports shape editing only; simulation preview is not supported")
    if scene.ree_gpuc_sim.use_gravity and force_object(scene, "CONSTANT") is None:
        add_force(scene, "CONSTANT", coll=scene.ree_gpuc.target)
    _state.clear()
    for obj in parts(scene):
        reset(obj, scene)
    _clock["t"] = 0.0
    _clock["running"] = True
    if "ree_gpuc_sim_error" in scene.keys():
        del scene["ree_gpuc_sim_error"]
    if not bpy.app.timers.is_registered(_timer):
        bpy.app.timers.register(_timer, first_interval=DT)


def stop() -> None:
    _clock["running"] = False
    _state.clear()
    _inputs.clear()


@bpy.app.handlers.persistent
def _on_load(*_):
    stop()


def register():
    bpy.app.handlers.load_pre.append(_on_load)


def unregister():
    stop()
    if bpy.app.timers.is_registered(_timer):
        bpy.app.timers.unregister(_timer)
    if _on_load in bpy.app.handlers.load_pre:
        bpy.app.handlers.load_pre.remove(_on_load)

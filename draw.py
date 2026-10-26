"""Draws the Parts' particles, links, limits, welds, colliders and forces in the 3D view, the Config curves as
graphs, and the particle values on the grid UV in the UV editor.

Maintenance constraints:
- Drawing only reads data. Follow the pose computes posed positions here from the particles' skin; the mesh
  itself stays in the rest pose.
- Particles are drawn in front of every mesh by default without writing depth, so spheres are sorted back to
  front and drawn with back-face culling."""
import math

import bmesh
import bpy
import gpu
import numpy as np
from gpu_extras.batch import batch_for_shader

from . import canon, props, sim
from .modules import grid

# Blender's weight paint colours, so a particle looks as its group does in Weight Paint: 0 blue, 1 red
WEIGHT_STOPS = np.array([0.0, 0.25, 0.5, 0.75, 1.0])
WEIGHT_COLOURS = np.array([(0.0, 0.0, 1.0), (0.0, 1.0, 1.0), (0.0, 1.0, 0.0), (1.0, 1.0, 0.0), (1.0, 0.0, 0.0)])
PLAIN_PARTICLE, PLAIN_LINK = (0.35, 1.0, 0.55), (0.3, 0.75, 0.45)
SHAPE_COLOURS = np.array([(0.3, 0.9, 0.4), (1.0, 0.6, 0.15), (0.25, 0.55, 1.0), (1.0, 0.4, 0.75)])
WELD_COLOUR = (1.0, 0.95, 0.2, 1.0)          # the welds joining two parts (yellow)
WELD_SELECTED = (1.0, 1.0, 0.75, 1.0)        # a selected weld object's
LIGHT = np.array((0.3, 0.4, 0.87))               # the spheres' shading direction (view space)
_links_cache: dict = {}
_handles = []


def ramp(t) -> np.ndarray:
    """Weights 0-1 in the weight paint colours."""
    t = np.clip(np.asarray(t, float), 0.0, 1.0)
    return np.column_stack([np.interp(t, WEIGHT_STOPS, WEIGHT_COLOURS[:, k]) for k in range(3)])


def particle_colours(obj, d: dict, field: str) -> np.ndarray:
    """Each particle's colour by a value, as its group's weight shows in Weight Paint (the radius and the max
    distance through the Part's range). ``d``: the canonical data, or ``values()`` merged over it."""
    n = canon.count(d)
    if field not in canon.FIELD_NAMES:
        return np.tile(PLAIN_PARTICLE, (n, 1))
    weights = canon.group_weights(obj, n)[field]
    mapped = canon.mapping_weights(obj, field, d)
    return ramp(mapped if weights is None else np.where(np.isfinite(weights), weights, mapped))


def links(d: dict) -> np.ndarray:
    return canon.links(d)


def _unit_sphere(levels: int = 1):
    """An icosphere: unit vertices and triangles."""
    t = (1 + math.sqrt(5)) / 2
    v = [(-1, t, 0), (1, t, 0), (-1, -t, 0), (1, -t, 0), (0, -1, t), (0, 1, t), (0, -1, -t), (0, 1, -t),
         (t, 0, -1), (t, 0, 1), (-t, 0, -1), (-t, 0, 1)]
    f = [(0, 11, 5), (0, 5, 1), (0, 1, 7), (0, 7, 10), (0, 10, 11), (1, 5, 9), (5, 11, 4), (11, 10, 2), (10, 7, 6),
         (7, 1, 8), (3, 9, 4), (3, 4, 2), (3, 2, 6), (3, 6, 8), (3, 8, 9), (4, 9, 5), (2, 4, 11), (6, 2, 10),
         (8, 6, 7), (9, 8, 1)]
    verts = [np.array(p, float) / np.linalg.norm(p) for p in v]
    for _ in range(levels):
        mid, out = {}, []
        for a, b, c in f:
            m = []
            for i, j in ((a, b), (b, c), (c, a)):
                key = (min(i, j), max(i, j))
                if key not in mid:
                    p = verts[i] + verts[j]
                    verts.append(p / np.linalg.norm(p))
                    mid[key] = len(verts) - 1
                m.append(mid[key])
            out += [(a, m[0], m[2]), (b, m[1], m[0]), (c, m[2], m[1]), (m[0], m[1], m[2])]
        f = out
    return np.array(verts), np.array(f, np.int32)


SPHERE_V, SPHERE_F = _unit_sphere()


def spheres(centres, radii, colours, alpha, view_rot=None):
    """Triangles (positions, colours, indices) of a sphere per particle, sorted back to front along the view
    (``view_rot``: the view matrix' 3x3) and shaded by the light, front faces only when culling."""
    centres, radii = np.asarray(centres, float), np.asarray(radii, float)
    n, nv = len(centres), len(SPHERE_V)
    if view_rot is not None:
        depth = centres @ view_rot[2]
        order = np.argsort(depth)                    # far (most negative view z) first
        centres, radii, colours = centres[order], radii[order], np.asarray(colours)[order]
    pos = (centres[:, None, :] + radii[:, None, None] * SPHERE_V[None]).reshape(-1, 3)
    normal = SPHERE_V if view_rot is None else SPHERE_V @ view_rot.T
    shade = 0.55 + 0.45 * np.clip(normal @ LIGHT, 0.0, 1.0)
    rgb = (np.asarray(colours, float)[:, None, :] * shade[None, :, None]).reshape(-1, 3)
    cols = np.column_stack([rgb, np.full(len(rgb), alpha)])
    idx = (SPHERE_F[None] + nv * np.arange(n)[:, None, None]).reshape(-1, 3)
    return pos.astype(np.float32), cols.astype(np.float32), idx.astype(np.int32)


def posed(d: dict, arm) -> np.ndarray:
    """The particles where the posed bones carry them (armature space): each one's rest position through its
    skin bones' deform matrices (pose x rest inverse), blended by the skin weights."""
    deform = []
    for name in d["nodes"]:
        pb = arm.pose.bones.get(name)
        deform.append(np.eye(4) if pb is None else
                      np.array(pb.matrix, float) @ np.linalg.inv(np.array(pb.bone.matrix_local, float)))
    deform = np.array(deform)                                       # nodes x 4 x 4
    co = np.column_stack([d["co"], np.ones(len(d["co"]))])            # particles x 4
    w = np.asarray(d["skin_w"], float)
    w = w / np.maximum(w.sum(axis=1, keepdims=True), 1e-9)           # a locked Part's u8 weights: shares
    m = np.einsum("pk,pkij->pij", w, deform[np.clip(d["skin"], 0, len(deform) - 1)])   # particles x 4 x 4
    return np.einsum("pij,pj->pi", m, co)[:, :3]


def world(obj, d, follow_pose: bool):
    """The particles in world space (rest, or carried by the pose) and the object's scale."""
    ev = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    m = np.array(ev.matrix_world, float)
    arm = obj.ree_gpuc_part.armature
    co = d["co"].astype(float)
    if obj.mode == "EDIT":
        bm = bmesh.from_edit_mesh(obj.data)
        if len(bm.verts) == len(co):
            co = np.array([tuple(v.co) for v in bm.verts], float)
    elif follow_pose and arm is not None and arm.type == "ARMATURE":
        co = posed(d, arm.evaluated_get(bpy.context.evaluated_depsgraph_get()))
    return co @ m[:3, :3].T + m[:3, 3], float(np.cbrt(abs(np.linalg.det(m[:3, :3]))))


def particle_normals(obj, d, pos) -> np.ndarray:
    """Unit normals of the particles at ``pos`` (world), outward: the grid's (``grid.normals``), turned by the rest
    pose's sign (away from the cloth's axis, or for a flat piece from its root joint) and the Part's Flip Normals."""
    part = obj.ree_gpuc_part
    if d["locked"]:
        return _triangle_normals(d, pos) * (-1.0 if part.flip_normals else 1.0)
    rows, cols, closed = d["rows"], d["cols"], d["closed"]
    rest = d["co"].astype(float)
    inside = None
    arm = part.armature
    if d["centre"] is None and arm is not None and arm.type == "ARMATURE":
        bone = arm.data.bones.get(part.root_joint)
        inside = np.array(bone.head_local, float) if bone is not None else np.zeros(3)
    sign = grid.normal_sign(rest, grid.normals(rest, rows, cols, closed), d["centre"], inside)
    return grid.normals(pos, rows, cols, closed) * (-sign if part.flip_normals else sign)


def _triangle_normals(d, pos) -> np.ndarray:
    """A locked Part's particle normals at pos: its triangles' (area weighted), turned to agree with the
    file's normals at rest."""
    def at(p):
        t = d["tris"]
        n = np.cross(p[t[:, 1]] - p[t[:, 0]], p[t[:, 2]] - p[t[:, 0]])
        out = np.zeros_like(p)
        for k in range(3):
            np.add.at(out, t[:, k], n)
        return out / np.maximum(np.linalg.norm(out, axis=1, keepdims=True), 1e-12)
    if "_sign" not in d:
        d["_sign"] = -1.0 if float(np.einsum("ij,ij->", at(d["co"].astype(float)), d["normal"])) < 0 else 1.0
    return at(np.asarray(pos, float)) * d["_sign"]


def space_limit_on(obj) -> bool:
    """Whether the Part's Config turns a space limit on (Config Info State: SpaceLimitSphere or Cylinder)."""
    cfg = obj.ree_gpuc_part.config
    return props.is_config(cfg) and bool(cfg.ree_gpuc_config.info_state & 0b110)


SPACE_IN, SPACE_OUT = (0.35, 0.6, 1.0, 0.9), (1.0, 0.55, 0.2, 0.9)
BACKSTOP_COLOUR = (0.75, 0.5, 1.0)


def space_limit_lines(pos, normal, live, scale, cylinder=False):
    """Line pairs and colours: inward by the lower limit, outward by the upper; with the sphere limit no longer
    than the max distance (the particle cannot go further anyway; the cylinder's max distance is across the
    normal only)."""
    lower, upper = live["space_limit_lower"].astype(float), live["space_limit_upper"].astype(float)
    if not cylinder:
        maxd = live["max_distance"].astype(float)
        lower, upper = np.minimum(lower, maxd), np.minimum(upper, maxd)
    lower, upper = lower * scale, upper * scale
    inner = np.stack([pos, pos - normal * lower[:, None]], axis=1).reshape(-1, 3)
    outer = np.stack([pos, pos + normal * upper[:, None]], axis=1).reshape(-1, 3)
    cols = np.vstack([np.tile(SPACE_IN, (len(inner), 1)), np.tile(SPACE_OUT, (len(outer), 1))])
    return np.vstack([inner, outer]), cols


LIMIT_KEYS = ("max_distance", "space_limit_lower", "space_limit_upper")


def shown(sel, selected_only: bool):
    """Which particles a part draws: with Draw Selected Only in edit mode and particles selected, those (a link
    when either end is); else None, all of them."""
    return sel if selected_only and sel is not None and sel.any() else None


def backstops(pos, normal, live, scale, only=None):
    """Centres and radii of the backstop spheres (radius 0: none): inside along the normal, the surface the
    offset below the particle's place; only: the particles to draw (None all)."""
    r = live["backstop_radius"].astype(float) * scale
    off = live["backstop_offset"].astype(float) * scale
    keep = r > 0
    if only is not None:
        keep &= only
    return (pos - normal * (r + off)[:, None])[keep], r[keep]


def _selection(obj, n):
    if obj.mode != "EDIT":
        return None
    bm = bmesh.from_edit_mesh(obj.data)
    if len(bm.verts) != n:
        return None
    return np.array([v.select for v in bm.verts], bool)


COLLIDER_COLOUR = (0.2, 0.78, 1.0)         # the .gpuc's own capsules (the chain editor's colliders' blue)
TEST_COLOUR = (0.55, 0.6, 0.7)              # the preview's test colliders
UNBOUND_COLOUR = (1.0, 0.55, 0.15)          # an own capsule with an end on no bone (an import's unknown joint)


def capsule(a, b, ra, rb, colour, alpha, view_rot, segments: int = 16):
    """Triangles of a capsule (tapered when the end radii differ): a sphere at each end and the tube between."""
    pos, cols, idx = spheres(np.array([a, b]), np.array([ra, rb]), np.array([colour, colour]), alpha, view_rot)
    axis = np.asarray(b, float) - np.asarray(a, float)
    length = np.linalg.norm(axis)
    if length < 1e-9:
        return pos, cols, idx
    z = axis / length
    x = np.cross(z, (1.0, 0.0, 0.0) if abs(z[0]) < 0.9 else (0.0, 1.0, 0.0))
    x /= np.linalg.norm(x)
    y = np.cross(z, x)
    ang = np.linspace(0, math.tau, segments, endpoint=False)
    ring = np.cos(ang)[:, None] * x + np.sin(ang)[:, None] * y
    tube = np.vstack([a + ra * ring, b + rb * ring])
    normal = np.vstack([ring, ring]) if view_rot is None else np.vstack([ring, ring]) @ view_rot.T
    shade = 0.55 + 0.45 * np.clip(normal @ LIGHT, 0.0, 1.0)
    tcols = np.column_stack([np.outer(shade, colour), np.full(len(tube), alpha)])
    k = np.arange(segments)
    k1 = (k + 1) % segments
    tris = np.vstack([np.column_stack([k, k1, segments + k]), np.column_stack([k1, segments + k1, segments + k])])
    return (np.vstack([pos, tube]).astype(np.float32), np.vstack([cols, tcols]).astype(np.float32),
            np.vstack([idx, tris + len(pos)]).astype(np.int32))


def plane(origin, normal, x_axis, size, colour, alpha):
    """Two triangles of a square (size across) on the plane, both sides."""
    x = np.asarray(x_axis, float)
    y = np.cross(normal, x)
    h = 0.5 * size
    pos = np.array([origin + h * (sx * x + sy * y) for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1))])
    cols = np.tile((*colour, alpha), (4, 1))
    idx = np.array([(0, 1, 2), (0, 2, 3), (0, 2, 1), (0, 3, 2)])
    return pos.astype(np.float32), cols.astype(np.float32), idx.astype(np.int32)


_BOX_FACES = np.array([(0, 2, 1), (0, 3, 2), (4, 5, 6), (4, 6, 7), (0, 1, 5), (0, 5, 4), (2, 3, 7), (2, 7, 6),
                       (1, 2, 6), (1, 6, 5), (0, 4, 7), (0, 7, 3)])
_BOX_CORNERS = np.array([(-1, -1, -1), (1, -1, -1), (1, 1, -1), (-1, 1, -1),
                         (-1, -1, 1), (1, -1, 1), (1, 1, 1), (-1, 1, 1)], float)


def box(centre, axes, half, colour, alpha, view_rot):
    """Triangles of an oriented box, each face shaded by its normal (front faces only when culling)."""
    pos, cols, idx = [], [], []
    corners = centre + (_BOX_CORNERS * half) @ axes.T
    for k, (a, b, c) in enumerate(_BOX_FACES):
        tri = corners[[a, b, c]]
        n = np.cross(tri[1] - tri[0], tri[2] - tri[0])
        n /= max(np.linalg.norm(n), 1e-12)
        shade = 0.55 + 0.45 * max(float((n @ view_rot.T) @ LIGHT), 0.0)
        pos.append(tri)
        cols.append(np.tile((*(np.asarray(colour) * shade), alpha), (3, 1)))
        idx.append((3 * k, 3 * k + 1, 3 * k + 2))
    return np.vstack(pos).astype(np.float32), np.vstack(cols).astype(np.float32), np.array(idx, np.int32)


def _draw_colliders(ctx):
    view = np.array(ctx.region_data.view_matrix, float)[:3, :3]
    parts = []
    for shape in sim.colliders(ctx.scene):
        if isinstance(shape[-2], tuple):                 # a CLSP's: the chain editor draws its own objects
            continue
        colour = {"OWN": COLLIDER_COLOUR, "UNBOUND": UNBOUND_COLOUR}.get(shape[-2], TEST_COLOUR)
        if shape[0] == "SPHERE":
            parts.append(spheres([shape[1]], [shape[2]], [colour], 0.3, view))
        elif shape[0] == "CAPSULE":
            parts.append(capsule(shape[1], shape[2], shape[3], shape[4], colour, 0.3, view))
        elif shape[0] == "PLANE":
            parts.append(plane(shape[1], shape[2], shape[4], shape[3], colour, 0.2))
        else:
            parts.append(box(shape[1], shape[2], shape[3], colour, 0.3, view))
    if not parts:
        return
    pos, cols, idx, base = [], [], [], 0
    for p_, c_, i_ in parts:
        pos.append(p_)
        cols.append(c_)
        idx.append(i_ + base)
        base += len(p_)
    shader = gpu.shader.from_builtin("SMOOTH_COLOR")
    gpu.state.face_culling_set("BACK")
    batch_for_shader(shader, "TRIS", {"pos": np.vstack(pos), "color": np.vstack(cols)},
                     indices=np.vstack(idx)).draw(shader)
    gpu.state.face_culling_set("NONE")


# the force arrows' colours: the active one, the others, the disabled ones
ARROW_ACTIVE, ARROW_IDLE, ARROW_OFF = (1.0, 0.62, 0.15, 1.0), (0.35, 0.75, 1.0, 0.85), (0.4, 0.4, 0.4, 0.35)
ARROW_SCALE = 0.03            # metres of arrow per m/s²


def arrow(origin, direction, length, head=0.25):
    """Line pairs of an arrow: the shaft and a four-barbed head."""
    origin, direction = np.asarray(origin, float), np.asarray(direction, float)
    tip = origin + direction * length
    side = np.cross(direction, (1.0, 0.0, 0.0) if abs(direction[0]) < 0.9 else (0.0, 1.0, 0.0))
    side /= np.linalg.norm(side)
    other = np.cross(direction, side)
    base = tip - direction * length * head
    rim = [base + (side * math.cos(a) + other * math.sin(a)) * length * head * 0.4
           for a in np.arange(4) * math.pi / 2]
    pairs = [origin, tip]
    for k, corner in enumerate(rim):
        pairs += [tip, corner, corner, rim[(k + 1) % 4]]
    return np.array(pairs)


def _draw_forces(ctx):
    segs, cols = [], []
    t = sim.time() if sim.running() else 0.0
    for obj in sim.force_objects(ctx.scene):
        f = obj.ree_gpuc_force
        length = max(ARROW_SCALE * f.strength(t) * sim.gain(obj), 0.02)
        pts = arrow(np.array(obj.matrix_world, float)[:3, 3], sim.direction(obj), length)
        colour = ARROW_OFF if not f.enabled else ARROW_ACTIVE if obj == ctx.active_object else ARROW_IDLE
        segs.append(pts)
        cols.append(np.tile(colour, (len(pts), 1)))
    if segs:
        shader = gpu.shader.from_builtin("POLYLINE_FLAT_COLOR")
        shader.uniform_float("viewportSize", gpu.state.viewport_get()[2:])
        shader.uniform_float("lineWidth", 2.5)
        batch_for_shader(shader, "LINES", {"pos": np.concatenate(segs).astype(np.float32),
                                           "color": np.concatenate(cols).astype(np.float32)}).draw(shader)


def _draw_3d():
    ctx = bpy.context
    s = ctx.scene.ree_gpuc
    gpu.state.blend_set("ALPHA")
    gpu.state.depth_test_set("NONE" if s.colliders_in_front else "LESS_EQUAL")
    gpu.state.depth_mask_set(False)
    if s.show_colliders:
        _draw_colliders(ctx)
    gpu.state.depth_test_set("NONE")
    _draw_forces(ctx)
    gpu.state.depth_test_set("NONE")
    gpu.state.blend_set("NONE")
    objs = [o for o in ctx.scene.objects if props.is_part(o) and canon.has(o) and o.visible_get()
            and (not s.selected_only_object or o.select_get())]
    if not objs or not (s.show_particles or s.show_links or s.show_space_limit or s.show_backstop or s.show_base_positions):
        return
    centres, radii, pcols, segs, lcols, base_segs = [], [], [], [], [], []
    limit_segs, limit_cols, back_c, back_r = [], [], [], []
    shapes = [k for k, x in enumerate(props.LINK_SHAPES) if getattr(s, "show_" + x[0])]
    drawn = {}
    base_points = []
    for obj in objs:
        d = canon.load(obj)
        live = {**d, **canon.values(obj, d)}
        pos, scale = world(obj, d, s.follow_pose)
        simulated = sim.shown(obj) if sim.running() and obj.mode != "EDIT" else None
        if simulated is not None and len(simulated) == len(pos):
            pos = simulated
        drawn[obj.name] = pos
        sel = _selection(obj, len(pos))
        keep = shown(sel, s.selected_only_edit)
        every = np.ones(len(pos), bool) if keep is None else keep
        if s.show_base_positions and not d["locked"]:
            base_d = {**d, "co": d["base_co"]}
            base_pos = world(obj, base_d, s.follow_pose)[0] if obj.mode != "EDIT" else (
                d["base_co"] @ np.array(obj.matrix_world, float)[:3, :3].T + np.array(obj.matrix_world, float)[:3, 3])
            # The reference lines compare rest positions, never the simulation's deformation.
            rest_pos = world(obj, d, s.follow_pose)[0]
            base_points.append(base_pos[every])
            base_segs.append(np.stack([base_pos[every], rest_pos[every]], axis=1).reshape(-1, 3))
        if s.show_space_limit and space_limit_on(obj) or s.show_backstop:
            normal = particle_normals(obj, d, pos)
            if s.show_space_limit and space_limit_on(obj):
                cylinder = bool(obj.ree_gpuc_part.config.ree_gpuc_config.info_state & 4)
                p_, c_ = space_limit_lines(pos[every], normal[every], {k: live[k][every] for k in LIMIT_KEYS},
                                           scale, cylinder)
                limit_segs.append(p_)
                limit_cols.append(c_)
            if s.show_backstop:
                c_, r_ = backstops(pos, normal, live, scale, keep)
                back_c.append(c_)
                back_r.append(r_)
        if s.show_particles:
            c = particle_colours(obj, live, s.colour_by)
            if sel is not None:
                c = np.where(sel[:, None], 0.45 * c + 0.55, 0.8 * c)
            centres.append(pos[every])
            radii.append(live["radius"].astype(float)[every] * scale)
            pcols.append(c[every])
        if s.show_links:
            ln = links(d)
            ln = ln[np.isin(ln[:, 3], shapes) & (every[ln[:, 0]] | every[ln[:, 1]])]
            segs.append(np.stack([pos[ln[:, 0]], pos[ln[:, 1]]], axis=1).reshape(-1, 3))
            if s.link_colour == "SHAPE":
                rgb = SHAPE_COLOURS[ln[:, 3]]
            elif s.link_colour == "STIFFNESS":
                part = obj.ree_gpuc_part
                k = np.array([getattr(part, name).k for name, _, _ in props.LINK_SHAPES])
                rgb = ramp(k[ln[:, 3]])
            else:
                rgb = np.tile(PLAIN_LINK, (len(ln), 1))
            lcols.append(np.repeat(np.column_stack([rgb, np.full(len(rgb), 0.9)]), 2, axis=0))
    weld_pts, weld_big = [], []
    if s.show_links:                                   # the welds joining two parts: a point (a line once apart)
        from . import weld
        for obj, a, ia, b, ib in weld.welds(ctx.scene.ree_gpuc.target):
            pa, pb = drawn.get(a.name), drawn.get(b.name)
            if pa is not None and pb is not None and ia < len(pa) and ib < len(pb):
                (weld_big if obj.select_get() else weld_pts).extend([pa[ia], pb[ib]])
                segs.append(np.array([pa[ia], pb[ib]]))
                lcols.append(np.tile(WELD_COLOUR, (2, 1)))
    gpu.state.blend_set("ALPHA")
    gpu.state.depth_mask_set(False)
    gpu.state.depth_test_set("NONE" if s.limits_in_front else "LESS_EQUAL")
    if back_c and sum(len(c) for c in back_c):
        cen = np.concatenate(back_c)
        view = np.array(ctx.region_data.view_matrix, float)[:3, :3]
        pos, cols, idx = spheres(cen, np.concatenate(back_r), np.tile(BACKSTOP_COLOUR, (len(cen), 1)), 0.08, view)
        shader = gpu.shader.from_builtin("SMOOTH_COLOR")
        gpu.state.face_culling_set("BACK")
        batch_for_shader(shader, "TRIS", {"pos": pos, "color": cols}, indices=idx).draw(shader)
        gpu.state.face_culling_set("NONE")
    if limit_segs:
        shader = gpu.shader.from_builtin("POLYLINE_FLAT_COLOR")
        shader.uniform_float("viewportSize", gpu.state.viewport_get()[2:])
        shader.uniform_float("lineWidth", 2.0)
        batch_for_shader(shader, "LINES", {"pos": np.concatenate(limit_segs).astype(np.float32),
                                           "color": np.concatenate(limit_cols).astype(np.float32)}).draw(shader)
    gpu.state.depth_test_set("NONE" if s.links_in_front else "LESS_EQUAL")
    if segs:
        shader = gpu.shader.from_builtin("POLYLINE_FLAT_COLOR")
        shader.uniform_float("viewportSize", gpu.state.viewport_get()[2:])
        shader.uniform_float("lineWidth", 1.5)
        batch_for_shader(shader, "LINES", {"pos": np.concatenate(segs).astype(np.float32),
                                           "color": np.concatenate(lcols).astype(np.float32)}).draw(shader)
    for pts, size, colour in ((weld_pts, 9.0, WELD_COLOUR), (weld_big, 14.0, WELD_SELECTED)):
        if pts:                                    # the selected weld object's pairs larger and brighter
            shader = gpu.shader.from_builtin("POINT_FLAT_COLOR")
            gpu.state.point_size_set(size)
            batch_for_shader(shader, "POINTS", {"pos": np.array(pts, np.float32),
                                                "color": np.tile(colour, (len(pts), 1)).astype(np.float32)}
                             ).draw(shader)
            gpu.state.point_size_set(1.0)
    gpu.state.depth_test_set("NONE" if s.in_front else "LESS_EQUAL")
    if base_segs:
        pts = np.concatenate(base_segs).astype(np.float32)
        shader = gpu.shader.from_builtin("POLYLINE_FLAT_COLOR")
        shader.uniform_float("viewportSize", gpu.state.viewport_get()[2:])
        shader.uniform_float("lineWidth", 1.5)
        batch_for_shader(shader, "LINES", {"pos": pts, "color": np.tile((0.65, 0.7, 1.0, 0.7), (len(pts), 1))}
                         ).draw(shader)
    if base_points:
        pts = np.concatenate(base_points).astype(np.float32)
        shader = gpu.shader.from_builtin("POINT_FLAT_COLOR")
        gpu.state.point_size_set(5.0)
        batch_for_shader(shader, "POINTS", {"pos": pts, "color": np.tile((0.65, 0.7, 1.0, 1.0), (len(pts), 1))}).draw(shader)
        gpu.state.point_size_set(1.0)
    if centres:
        cen, col = np.concatenate(centres), np.concatenate(pcols)
        if s.particle_style == "SPHERE":
            view = np.array(ctx.region_data.view_matrix, float)[:3, :3]
            pos, cols, idx = spheres(cen, np.concatenate(radii), col, s.sphere_alpha, view)
            shader = gpu.shader.from_builtin("SMOOTH_COLOR")
            gpu.state.face_culling_set("BACK")
            batch_for_shader(shader, "TRIS", {"pos": pos, "color": cols}, indices=idx).draw(shader)
            gpu.state.face_culling_set("NONE")
        else:
            shader = gpu.shader.from_builtin("POINT_FLAT_COLOR")
            gpu.state.point_size_set(s.point_size)
            batch_for_shader(shader, "POINTS", {"pos": cen.astype(np.float32),
                                                "color": np.column_stack([col, np.ones(len(col))]).astype(np.float32)}
                             ).draw(shader)
            gpu.state.point_size_set(1.0)
    gpu.state.depth_mask_set(True)
    gpu.state.depth_test_set("NONE")
    gpu.state.blend_set("NONE")


GRAPH_W, GRAPH_H, GRAPH_GAP, GRAPH_LEFT, GRAPH_BOTTOM = 200, 100, 16, 70, 40
CURVE_LINE, CURVE_OFF = (1.0, 0.75, 0.3, 1.0), (0.6, 0.6, 0.6, 0.6)


def graph_config(context):
    """The Config whose curves the graph shows: the active object if it is one, else the active Part's."""
    obj = context.active_object
    if props.is_part(obj):
        obj = obj.ree_gpuc_part.config
    return obj if props.is_config(obj) else None


def curve_points(curve, x0, y0, w, h, samples: int = 64):
    """The curve in a w x h box at (x0, y0), pixels: (line points, key points, the value range shown)."""
    xs = np.linspace(0.0, 1.0, samples)
    ys = np.asarray(curve.value(xs), float)
    kv = np.array([(k.time, k.value) for k in curve.keys], float).reshape(-1, 2)
    lo = min(0.0, float(ys.min()), *(kv[:, 1] if len(kv) else [0.0]))
    hi = max(1.0, float(ys.max()), *(kv[:, 1] if len(kv) else [1.0]))
    to = lambda x, y: np.column_stack([x0 + np.asarray(x) * w, y0 + (np.asarray(y) - lo) / (hi - lo) * h,  # noqa: E731
                                       np.zeros(len(np.atleast_1d(x)))])
    return to(xs, ys).astype(np.float32), to(kv[:, 0], kv[:, 1]).astype(np.float32), (lo, hi)


def _draw_graphs():
    import blf
    ctx = bpy.context
    if not ctx.scene.ree_gpuc.show_curves:
        return
    obj = graph_config(ctx)
    if obj is None:
        return
    cfg = obj.ree_gpuc_config
    flat = gpu.shader.from_builtin("UNIFORM_COLOR")
    line = gpu.shader.from_builtin("POLYLINE_UNIFORM_COLOR")
    line.uniform_float("viewportSize", gpu.state.viewport_get()[2:])
    gpu.state.blend_set("ALPHA")
    ui = ctx.preferences.system.ui_scale
    w, h, gap = GRAPH_W * ui, GRAPH_H * ui, GRAPH_GAP * ui
    for k, (name, label, switch, _) in enumerate(props.CURVES):
        curve = getattr(cfg, name)
        x0, y0 = GRAPH_LEFT * ui + k * (w + gap), GRAPH_BOTTOM * ui
        box = np.array([(x0, y0, 0), (x0 + w, y0, 0), (x0 + w, y0 + h, 0), (x0, y0 + h, 0)], np.float32)
        flat.uniform_float("color", (0.0, 0.0, 0.0, 0.4))
        batch_for_shader(flat, "TRIS", {"pos": box}, indices=((0, 1, 2), (0, 2, 3))).draw(flat)
        off = getattr(cfg, switch)
        pts, keys, (lo, hi) = curve_points(curve, x0, y0, w, h)
        line.uniform_float("lineWidth", 1.0)
        line.uniform_float("color", (1.0, 1.0, 1.0, 0.25))
        batch_for_shader(line, "LINE_STRIP", {"pos": np.vstack([box, box[:1]])}).draw(line)
        line.uniform_float("lineWidth", 2.0)
        line.uniform_float("color", CURVE_OFF if off else CURVE_LINE)
        batch_for_shader(line, "LINE_STRIP", {"pos": pts}).draw(line)
        inputs = sim.curve_inputs(obj)
        if inputs is not None and not off:                  # where the root's speed reads the curve now
            mx = x0 + inputs[k] * w
            line.uniform_float("lineWidth", 1.5)
            line.uniform_float("color", (0.4, 1.0, 0.6, 0.9))
            batch_for_shader(line, "LINES", {"pos": np.array([(mx, y0, 0), (mx, y0 + h, 0)], np.float32)}).draw(line)
        if len(keys):
            dots = gpu.shader.from_builtin("POINT_UNIFORM_COLOR")
            gpu.state.point_size_set(6.0 * ui)
            dots.uniform_float("color", (1.0, 1.0, 1.0, 0.5 if off else 0.9))
            batch_for_shader(dots, "POINTS", {"pos": keys}).draw(dots)
            if 0 <= curve.active_key < len(keys):
                gpu.state.point_size_set(9.0 * ui)
                dots.uniform_float("color", (1.0, 0.95, 0.4, 1.0))
                batch_for_shader(dots, "POINTS", {"pos": keys[curve.active_key:curve.active_key + 1]}).draw(dots)
            gpu.state.point_size_set(1.0)
        blf.size(0, 11 * ui)
        blf.color(0, 1.0, 1.0, 1.0, 0.9)
        blf.position(0, x0 + 4 * ui, y0 + h + 4 * ui, 0)
        blf.draw(0, f"{label} ({obj.name})" + (" - overwritten" if off else ""))
        blf.color(0, 1.0, 1.0, 1.0, 0.5)
        for value, y in ((hi, y0 + h - 12 * ui), (lo, y0 + 3 * ui)):
            blf.position(0, x0 + 4 * ui, y, 0)
            blf.draw(0, f"{value:.2g}")
    gpu.state.blend_set("NONE")


def _draw_uv():
    ctx = bpy.context
    s = ctx.scene.ree_gpuc
    if not s.show_uv_values or ctx.mode != "EDIT_MESH" or s.colour_by == "NONE":
        return
    region = ctx.region
    pts, cols = [], []
    for obj in ctx.objects_in_mode:
        if not props.is_part(obj) or not canon.has(obj):
            continue
        d = canon.load(obj)
        uv = canon.particle_uv(d)
        c = particle_colours(obj, {**d, **canon.values(obj, d)}, s.colour_by)
        if not d["locked"] and d["closed"]:          # the seam column also at u = tile + 1
            seam = np.arange(0, d["rows"] * d["cols"], d["cols"])
            uv = np.vstack([uv, uv[seam] + (1.0, 0.0)])
            c = np.vstack([c, c[seam]])
        px = [region.view2d.view_to_region(float(u), float(v), clip=False) for u, v in uv]
        pts.append(np.array([(x, y, 0.0) for x, y in px], np.float32))
        cols.append(c)
    if not pts:
        return
    shader = gpu.shader.from_builtin("POINT_FLAT_COLOR")
    gpu.state.blend_set("ALPHA")
    gpu.state.point_size_set(s.point_size + 4.0)
    c = np.concatenate(cols)
    batch_for_shader(shader, "POINTS", {"pos": np.concatenate(pts),
                                        "color": np.column_stack([c, np.full(len(c), 0.85)]).astype(np.float32)}
                     ).draw(shader)
    gpu.state.point_size_set(1.0)
    gpu.state.blend_set("NONE")


def register():
    _handles.append((bpy.types.SpaceView3D, bpy.types.SpaceView3D.draw_handler_add(
        _draw_3d, (), "WINDOW", "POST_VIEW")))
    _handles.append((bpy.types.SpaceView3D, bpy.types.SpaceView3D.draw_handler_add(
        _draw_graphs, (), "WINDOW", "POST_PIXEL")))
    _handles.append((bpy.types.SpaceImageEditor, bpy.types.SpaceImageEditor.draw_handler_add(
        _draw_uv, (), "WINDOW", "POST_PIXEL")))


def unregister():
    while _handles:
        space, handle = _handles.pop()
        space.draw_handler_remove(handle, "WINDOW")

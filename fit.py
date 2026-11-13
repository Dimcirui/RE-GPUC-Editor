"""Fit to Mesh: move a cloth's particles from the mesh they sit on to another mesh of the same topology (the same
mesh sculpted, or converted for another character or game), and optionally onto that mesh's armature.

Each particle is bound to the nearest triangles of the source meshes, as Blender's Surface Deform binds: for each
triangle the barycentric coordinates of the particle's foot on the triangle's plane and its height above it, weighted
by inverse squared distance. The same triangles of the target meshes give the new place, the height kept. Source and target
meshes pair by the name after "__" (RE Mesh Editor's material), else by the whole name (without Blender's ".001"),
and must have the same faces vertex for vertex."""
import re

import bpy
import numpy as np
from mathutils.bvhtree import BVHTree

from . import canon, fileio, follow, generate, props

NEAREST = 4            # triangles a particle is bound to
FAR = 0.1              # m: particles farther from every source triangle stay where they are


def mesh_objects(mesh_coll) -> list:
    """The meshes of the first "Main Mesh LOD" of an RE Mesh Editor collection, else all its meshes."""
    lods = sorted((c for c in mesh_coll.children if "Main Mesh LOD" in c.name), key=lambda c: c.name)
    objs = lods[0].all_objects if lods else mesh_coll.all_objects
    return [o for o in objs if o.type == "MESH" and not props.is_part(o)]


def _key(obj) -> str:
    """The material part of an RE Mesh Editor mesh name, without Blender's ".001" suffix."""
    name = re.sub(r"\.\d{3}$", "", obj.name)
    return name.split("__", 1)[1] if "__" in name else name


def _same_faces(a, b) -> bool:
    if len(a.data.vertices) != len(b.data.vertices) or len(a.data.polygons) != len(b.data.polygons):
        return False
    na, nb = np.empty(len(a.data.loops), np.int32), np.empty(len(b.data.loops), np.int32)
    a.data.loops.foreach_get("vertex_index", na)
    b.data.loops.foreach_get("vertex_index", nb)
    return np.array_equal(na, nb)


def pair_meshes(source, target) -> tuple:
    """(pairs [(source object, target object)], source meshes left out with the reason)."""
    by_key = {}
    for o in mesh_objects(target):
        by_key.setdefault(_key(o), []).append(o)
    pairs, left = [], []
    for o in mesh_objects(source):
        cands = by_key.get(_key(o), [])
        match = next((t for t in cands if _same_faces(o, t)), None)
        if match is not None:
            pairs.append((o, match))
        else:
            left.append((o.name, "no mesh of the target has its name" if not cands else "its faces differ"))
    return pairs, left


def _surface(objs) -> tuple:
    """World-space vertices (V, 3) and triangles (T, 3) of the meshes, in the order given."""
    verts, tris = [], []
    for o in objs:
        me = o.data
        me.calc_loop_triangles()
        co = np.empty(3 * len(me.vertices), np.float32)
        me.vertices.foreach_get("co", co)
        mw = np.array(o.matrix_world, float)
        co = co.reshape(-1, 3) @ mw[:3, :3].T + mw[:3, 3]
        t = np.empty(3 * len(me.loop_triangles), np.int32)
        me.loop_triangles.foreach_get("vertices", t)
        tris.append(t.reshape(-1, 3) + sum(len(v) for v in verts))
        verts.append(co)
    if not verts:
        return np.zeros((0, 3)), np.zeros((0, 3), int)
    return np.concatenate(verts), np.concatenate(tris)


def _frame(a, b, c):
    """An orthonormal frame (rows: edge, in-plane, normal) of a triangle; None when it has no area."""
    e1, n = b - a, np.cross(b - a, c - a)
    ln, le = np.linalg.norm(n), np.linalg.norm(e1)
    if ln < 1e-12 or le < 1e-12:
        return None
    e1, n = e1 / le, n / ln
    return np.array([e1, np.cross(n, e1), n])


def _barycentric(p, a, b, c):
    m = np.array([b - a, c - a]).T
    u, v = np.linalg.lstsq(m, p - a, rcond=None)[0]
    return 1.0 - u - v, u, v


def bind(points, src_v, tris) -> tuple:
    """Bind world-space points to the surface (``src_v``, ``tris``): each to its ``NEAREST`` triangles, as (triangle
    index, -1 for none; barycentric (a, b, c) of its foot on the triangle's plane; its height above it; weight by
    inverse squared distance), arrays (n, NEAREST[, 3]); and each point's distance to the surface. A point farther
    than ``FAR`` binds to none."""
    points = np.asarray(points, float)
    n = len(points)
    tri = np.full((n, NEAREST), -1, np.int64)
    bary = np.zeros((n, NEAREST, 3))
    height = np.zeros((n, NEAREST))
    weight = np.zeros((n, NEAREST))
    dist = np.full(n, np.inf)
    if not len(tris):
        return (tri, bary, height, weight), dist
    bvh = BVHTree.FromPolygons(src_v.tolist(), tris.tolist(), all_triangles=True)
    for i, p in enumerate(points):
        loc, _, _, d0 = bvh.find_nearest(p)
        if loc is None:
            continue
        dist[i] = d0
        if d0 > FAR:
            continue
        hits = sorted(bvh.find_nearest_range(p, d0 + max(d0, 0.005)), key=lambda h: h[3])[:NEAREST]
        for k, (_, _, fi, d) in enumerate(hits):
            a, b, c = src_v[tris[fi]]
            fs = _frame(a, b, c)
            if fs is None:
                continue
            h = float(np.dot(p - a, fs[2]))           # the distance from the triangle's plane is kept
            tri[i, k] = fi
            bary[i, k] = _barycentric(p - h * fs[2], a, b, c)   # outside the triangle: on its plane
            height[i, k] = h
            weight[i, k] = 1.0 / (d * d + 1e-8)
    return (tri, bary, height, weight), dist


def place(binding, dst_v, tris, points) -> tuple:
    """The bound points' places on a surface of the same triangles (``bind``): (places, the given ``points`` where
    unbound; whether each is bound)."""
    tri, bary, height, weight = binding
    out = np.array(points, float)
    bound = np.zeros(len(out), bool)
    for i in range(len(out)):
        acc, wsum = np.zeros(3), 0.0
        for k in range(tri.shape[1]):
            fi = tri[i, k]
            if fi < 0 or fi >= len(tris):
                continue
            ta, tb, tc = dst_v[tris[fi]]
            ft = _frame(ta, tb, tc)
            if ft is None:
                continue
            wa, wb, wc = bary[i, k]
            acc += weight[i, k] * (wa * ta + wb * tb + wc * tc + height[i, k] * ft[2])
            wsum += weight[i, k]
        if wsum:
            out[i] = acc / wsum
            bound[i] = True
    return out, bound


def map_points(points, src_v, tris, dst_v) -> tuple:
    """New places of world-space points bound to the source surface, on the target surface of the same triangles;
    returns (places (n, 3), whether each point was bound, its distance to the source)."""
    binding, dist = bind(points, src_v, tris)
    out, bound = place(binding, dst_v, tris, points)
    return out, bound, dist


def _fittable(obj) -> str:
    """Why a Part's rest positions cannot be set, else ""."""
    if not canon.has(obj):
        return "it has no particles"
    if canon.attached(obj) or canon.editable_part(obj):
        return ""
    return "its particles keep their file's places (an imported RE4 cloth, or one simulated in several LODs)"


def _set_rest(obj, co_local) -> None:
    if canon.attached(obj):
        canon.set_positions(obj, co_local)
        canon.build_mesh(obj)
        return
    obj.data.vertices.foreach_set("co", np.asarray(co_local, np.float32).ravel())
    obj.data.update()
    canon.restore(obj)


def _world_rest(arm) -> dict:
    """Each bone's rest matrix in world space."""
    if arm is None or arm.type != "ARMATURE":
        return {}
    mw = np.array(arm.matrix_world, float)
    return {b.name: mw @ np.array(b.matrix_local, float) for b in arm.data.bones}


def move_cloth_to(coll, arm) -> None:
    """Put the cloth on another armature: its header (so its Configs and Parts, in armature space), each Part's skin,
    its collision ends and its reference skeleton."""
    from . import collision, reference
    head = props.header(coll)
    if head is not None and head.parent != arm:
        props.place_under(head, arm)
    for obj in props.parts(coll):
        obj.ree_gpuc_part.armature = arm
        if canon.has(obj):
            canon.keep_modifier(obj, canon.load(obj))
    if reference.armature(coll) is not None:
        reference.ensure(coll, arm)
    for root in collision.roots(bpy.context.scene):
        if root.name in coll.all_objects:
            collision.apply(root.ree_gpuc_capsule)


def fit_cloth(coll, source, target, use_target_armature=True, game="") -> str:
    """Move the cloth's particles from the source mesh collection onto the target's (see the module); with
    ``use_target_armature`` and another armature on the target, the cloth goes onto it; a ``game`` other than the
    header's becomes its Game. Returns the notice; raises ``ValueError`` when no mesh pairs."""
    if source == target:
        raise ValueError("the source and target mesh collections are the same")
    pairs, left = pair_meshes(source, target)
    if not pairs:
        why = "; ".join(f"{n}: {w}" for n, w in left[:3])
        raise ValueError(f"no mesh of {source.name} has the same faces in {target.name} ({why})")
    src_v, tris = _surface([a for a, _ in pairs])
    dst_v, _ = _surface([b for _, b in pairs])
    parts = [o for o in props.parts(coll)]
    skipped = [(o.name, _fittable(o)) for o in parts if _fittable(o)]
    parts = [o for o in parts if not _fittable(o)]
    bpy.context.view_layer.update()
    world = {o: np.asarray(canon.load(o)["co"], float) @ np.array(o.matrix_world)[:3, :3].T
             + np.array(o.matrix_world)[:3, 3] for o in parts}
    new_arm = next((o for o in target.all_objects if o.type == "ARMATURE"), None) if use_target_armature else None
    old_arm = generate._armature(coll)
    skins = {o: canon.load(o) for o in parts}
    old_rest = _world_rest(old_arm)
    if new_arm is not None and new_arm != old_arm:
        move_cloth_to(coll, new_arm)
        for o in parts:
            if canon.attached(o):
                generate.regenerate(o)           # the bone grid from the new bones; the offsets set below
        bpy.context.view_layer.update()
    else:
        new_arm = None
    moved, far, mm = 0, 0, []
    new_rest = _world_rest(new_arm) if new_arm is not None else None
    for o in parts:
        places, bound, dist = map_points(world[o], src_v, tris, dst_v)
        if new_rest is not None and not bound.all():       # off the mesh: carried by their skin to the new bones
            d = skins[o]
            places[~bound] = follow.carried(world[o], d["nodes"], d["skin"], d["skin_w"], old_rest, new_rest)[~bound]
        inv = np.linalg.inv(np.array(o.matrix_world))
        _set_rest(o, places @ inv[:3, :3].T + inv[:3, 3])
        moved += int(bound.sum())
        far += int((~bound).sum())
        mm += (np.linalg.norm(places - world[o], axis=1)[bound] * 1000).tolist()
        follow.record(o)
    bpy.context.view_layer.update()
    from . import anchors
    for o in parts:
        anchors.record(o, target)                     # where they now sit on the target mesh
    text = (f"{moved} particles moved onto {target.name} ({len(pairs)} meshes"
            f"{f', median {np.median(mm):.1f} mm' if mm else ''})")
    if far:
        text += (f"; {far} more than {FAR * 100:.0f} cm from {source.name} "
                 + ("went with their skin bones" if new_rest is not None else "stay where they were"))
    if new_arm is not None:
        text += f"; the cloth is now on {new_arm.name}"
    head = props.header(coll)
    if game and head is not None and head.ree_gpuc_header.game != game:
        head.ree_gpuc_header.game = game
        text += f"; Game set to {fileio.game_name(game)}"
    if skipped:
        text += "; not moved: " + ", ".join(f"{n} ({w})" for n, w in skipped[:3])
    if left:
        text += f"; {len(left)} meshes of {source.name} found no match"
    moved = fileio.moved_joints(coll, generate._armature(coll))
    if moved:
        text += f"; the export binds {len(moved)} of the file's driven joints again at their bones' rest"
    return text

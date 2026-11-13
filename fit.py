"""Fit to Mesh: move a cloth's particles from the mesh they sit on to another mesh (the same mesh sculpted, or
converted for another character or game), and optionally onto that mesh's armature.

Each particle is bound to the nearest triangles of the source meshes, as Blender's Surface Deform binds: for each
triangle the barycentric coordinates of the particle's foot on the triangle's plane and its height above it, weighted
by inverse squared distance. Source and target meshes pair by the name after "__" (RE Mesh Editor's material), else
by the whole name (without Blender's ".001"). A pair with the same faces vertex for vertex gives the new place on the
same triangles; one with other faces, at the same UV (overlapping UV islands: the place nearest the old one); the
height kept. A particle no pair places, near a source mesh, goes by a displacement field through the points that do
map (``rbf``: paired vertices, the bones' heads and tails); one far from it, by its skin bones."""
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
    """(pairs [(source object, target object, "FACES" when their faces are the same vertex for vertex, else "UV"
    when both have a UV map)], source meshes left out with the reason)."""
    by_key = {}
    for o in mesh_objects(target):
        by_key.setdefault(_key(o), []).append(o)
    pairs, left = [], []
    for o in mesh_objects(source):
        cands = by_key.get(_key(o), [])
        same = next((t for t in cands if _same_faces(o, t)), None)
        if same is not None:
            pairs.append((o, same, "FACES"))
            continue
        uv = next((t for t in cands if t.data.uv_layers and o.data.uv_layers), None)
        if uv is not None:
            pairs.append((o, uv, "UV"))
        else:
            left.append((o.name, "no mesh of the target has its name" if not cands
                         else "its faces differ and a UV map is missing"))
    return pairs, left


class _Mesh:
    """A mesh object's world-space vertices, triangles (loop triangles) and their corners' UVs."""

    def __init__(self, obj):
        me = obj.data
        me.calc_loop_triangles()
        co = np.empty(3 * len(me.vertices), np.float32)
        me.vertices.foreach_get("co", co)
        mw = np.array(obj.matrix_world, float)
        self.co = co.reshape(-1, 3) @ mw[:3, :3].T + mw[:3, 3]
        t = np.empty(3 * len(me.loop_triangles), np.int32)
        me.loop_triangles.foreach_get("vertices", t)
        self.tris = t.reshape(-1, 3)
        self.uv = None
        self._bvh = None
        if me.uv_layers:
            loops = np.empty(3 * len(me.loop_triangles), np.int32)
            me.loop_triangles.foreach_get("loops", loops)
            uv = np.empty(2 * len(me.loops), np.float32)
            me.uv_layers.active.data.foreach_get("uv", uv)
            self.uv = uv.reshape(-1, 2)[loops].reshape(-1, 3, 2).astype(float)

    def uv_bvh(self):
        if self._bvh is None:
            flat = np.c_[self.uv.reshape(-1, 2), np.zeros(len(self.uv) * 3)]
            self._bvh = BVHTree.FromPolygons(flat.tolist(), np.arange(len(flat)).reshape(-1, 3).tolist(),
                                             all_triangles=True)
        return self._bvh

    def at_uv(self, uv, near):
        """The surface point with UV ``uv`` nearest to ``near`` (overlapping UV islands, as mirrored halves have):
        (point, its triangle), or (None, None) off the UV map."""
        bvh = self.uv_bvh()
        hits = bvh.find_nearest_range((float(uv[0]), float(uv[1]), 0.0), UV_NEAR)
        if not hits:
            loc, _, fi, d = bvh.find_nearest((float(uv[0]), float(uv[1]), 0.0))
            if loc is None or d > UV_FAR:
                return None, None
            hits = [(loc, None, fi, d)]
        best, best_d, best_t = None, np.inf, None
        for _, _, fi, _ in hits:
            ua, ub, uc = self.uv[fi]
            m = np.array([ub - ua, uc - ua]).T
            try:
                s, t = np.linalg.solve(m, np.asarray(uv, float) - ua)
            except np.linalg.LinAlgError:
                continue
            a, b, c = self.co[self.tris[fi]]
            p = (1 - s - t) * a + s * b + t * c
            d = float(np.linalg.norm(p - near))
            if d < best_d:
                best, best_d, best_t = p, d, fi
        return best, best_t


UV_NEAR = 1e-5          # UV distance within which overlapping islands hold the same UV
UV_FAR = 0.002          # a UV farther than this from every target triangle is off its map


def transfer(points, pairs) -> tuple:
    """Places of world-space points on the target meshes (``pair_meshes``): each bound to the nearest triangles of
    the source meshes (``bind``), a triangle of a mesh with the same faces placed on the same triangle of the
    target, one of a mesh with other faces placed at the same UV on the target; the height above the triangle kept.
    Returns (places (the given point where none places it), whether each is placed, how: 0 faces, 1 UV, -1 none,
    distance to the source)."""
    points = np.asarray(points, float)
    src = [_Mesh(a) for a, _, _ in pairs]
    dst = [_Mesh(b) for _, b, _ in pairs]
    if not src:
        return points.copy(), np.zeros(len(points), bool), np.full(len(points), -1), np.full(len(points), np.inf)
    sizes = [len(m.tris) for m in src]
    start = np.concatenate([[0], np.cumsum(sizes)])
    src_v = np.concatenate([m.co for m in src])
    src_t = np.concatenate([m.tris + sum(len(x.co) for x in src[:k]) for k, m in enumerate(src)])
    (tri, bary, height, weight), dist = bind(points, src_v, src_t)
    out, placed, how = points.copy(), np.zeros(len(points), bool), np.full(len(points), -1)
    for i in range(len(points)):
        acc, wsum, used = np.zeros(3), 0.0, set()
        for k in range(tri.shape[1]):
            fi = tri[i, k]
            if fi < 0:
                continue
            o = int(np.searchsorted(start, fi, side="right")) - 1
            lt = fi - start[o]
            wa, wb, wc = bary[i, k]
            if pairs[o][2] == "FACES":
                t = dst[o].tris[lt]
                p = wa * dst[o].co[t[0]] + wb * dst[o].co[t[1]] + wc * dst[o].co[t[2]]
                ft = _frame(*dst[o].co[t])
                used.add(0)
            else:
                if src[o].uv is None or dst[o].uv is None:
                    continue
                uv = wa * src[o].uv[lt][0] + wb * src[o].uv[lt][1] + wc * src[o].uv[lt][2]
                a, b, c = src[o].co[src[o].tris[lt]]
                p, ti = dst[o].at_uv(uv, wa * a + wb * b + wc * c)
                if p is None:
                    continue
                ft = _frame(*dst[o].co[dst[o].tris[ti]])
                used.add(1)
            if ft is None:
                continue
            acc += weight[i, k] * (p + height[i, k] * ft[2])
            wsum += weight[i, k]
        if wsum:
            out[i] = acc / wsum
            placed[i] = True
            how[i] = 0 if used == {0} else 1
    return out, placed, how, dist


CONTROLS = 1200         # control points an RBF field takes at most


def bone_points(arm) -> dict:
    """Each bone's (head, tail) in world space."""
    if arm is None or arm.type != "ARMATURE":
        return {}
    mw = np.array(arm.matrix_world, float)
    return {b.name: tuple((mw @ np.r_[np.array(x, float), 1.0])[:3] for x in (b.head_local, b.tail_local))
            for b in arm.data.bones}


def controls(pairs, old_bones=None, new_bones=None) -> tuple:
    """Points whose places on the target are known, (source (m, 3), target (m, 3)): a sample of each paired mesh's
    vertices (by vertex, or at its UV) and the heads and tails of the bones both armatures name (``bone_points``)."""
    src, dst = [], []
    per = max(CONTROLS // max(len(pairs), 1), 1)
    for a, b, how in pairs:
        ma, mb = _Mesh(a), _Mesh(b)
        if how == "FACES":
            take = np.linspace(0, len(ma.co) - 1, min(per, len(ma.co))).astype(int)
            src.append(ma.co[take])
            dst.append(mb.co[take])
            continue
        if ma.uv is None or mb.uv is None:
            continue
        corners = np.linspace(0, len(ma.tris) * 3 - 1, min(per, len(ma.tris) * 3)).astype(int)
        for c in corners:
            t, k = divmod(int(c), 3)
            p = ma.co[ma.tris[t][k]]
            q, _ = mb.at_uv(ma.uv[t][k], p)
            if q is not None:
                src.append(p[None])
                dst.append(q[None])
    for name in sorted(set(old_bones or ()) & set(new_bones or ())):
        src.append(np.array(old_bones[name]))
        dst.append(np.array(new_bones[name]))
    if not src:
        return np.zeros((0, 3)), np.zeros((0, 3))
    src, dst = np.concatenate(src), np.concatenate(dst)
    _, keep = np.unique(np.round(src, 5), axis=0, return_index=True)
    return src[np.sort(keep)], dst[np.sort(keep)]


def rbf(src, dst):
    """The displacement field through the control points (``controls``), a function of points (n, 3) giving their
    places: a radial basis (|r|) with an affine part, so a move, turn or scale of the whole is kept exactly; None with
    fewer than four points."""
    src, dst = np.asarray(src, float), np.asarray(dst, float)
    m = len(src)
    if m < 4:
        return None
    kernel = np.linalg.norm(src[:, None] - src[None], axis=2)
    poly = np.c_[np.ones(m), src]
    a = np.zeros((m + 4, m + 4))
    a[:m, :m] = kernel + 1e-9 * np.eye(m)
    a[:m, m:], a[m:, :m] = poly, poly.T
    rhs = np.zeros((m + 4, 3))
    rhs[:m] = dst - src
    coef = np.linalg.lstsq(a, rhs, rcond=None)[0]
    w, aff = coef[:m], coef[m:]

    def field(points):
        p = np.asarray(points, float)
        k = np.linalg.norm(p[:, None] - src[None], axis=2)
        return p + k @ w + np.c_[np.ones(len(p)), p] @ aff
    return field


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
    header's becomes its Game. Each particle goes by the first that places it: the same faces, the same UV
    (``transfer``), a displacement field through what does map (``rbf``), its skin bones (another armature), else
    it stays. Returns the notice; raises ``ValueError`` when nothing maps."""
    if source == target:
        raise ValueError("the source and target mesh collections are the same")
    pairs, left = pair_meshes(source, target)
    new_arm = next((o for o in target.all_objects if o.type == "ARMATURE"), None) if use_target_armature else None
    old_arm = generate._armature(coll)
    if new_arm == old_arm:
        new_arm = None
    if not pairs and new_arm is None:
        why = "; ".join(f"{n}: {w}" for n, w in left[:3])
        raise ValueError(f"no mesh of {source.name} pairs with one in {target.name} ({why})")
    parts = [o for o in props.parts(coll)]
    skipped = [(o.name, _fittable(o)) for o in parts if _fittable(o)]
    parts = [o for o in parts if not _fittable(o)]
    bpy.context.view_layer.update()
    world = {o: np.asarray(canon.load(o)["co"], float) @ np.array(o.matrix_world)[:3, :3].T
             + np.array(o.matrix_world)[:3, 3] for o in parts}
    skins = {o: canon.load(o) for o in parts}
    old_rest, old_bones = _world_rest(old_arm), bone_points(old_arm)
    new_bones = bone_points(new_arm) if new_arm is not None else None
    field = rbf(*controls(pairs, old_bones, new_bones))
    if new_arm is not None:
        move_cloth_to(coll, new_arm)
        for o in parts:
            if canon.attached(o):
                generate.regenerate(o)           # the bone grid from the new bones; the offsets set below
        bpy.context.view_layer.update()
    new_rest = _world_rest(new_arm) if new_arm is not None else None
    count = {"faces": 0, "uv": 0, "field": 0, "skin": 0, "stay": 0}
    mm = []
    src_v, src_t = _surface(mesh_objects(source))
    near_bvh = BVHTree.FromPolygons(src_v.tolist(), src_t.tolist(), all_triangles=True) if len(src_t) else None
    for o in parts:
        places, placed, how, _ = transfer(world[o], pairs)
        dist = np.array([near_bvh.find_nearest(p)[3] if near_bvh is not None else np.inf for p in world[o]], float)
        count["faces"] += int((how == 0).sum())
        count["uv"] += int((how == 1).sum())
        rest = ~placed
        near = rest & (dist <= FAR)
        if field is not None and near.any():          # on the mesh, but no mesh of the target takes it
            places[near] = field(world[o][near])
            count["field"] += int(near.sum())
            rest &= ~near
        if new_rest is not None and rest.any():       # off the mesh: carried by their skin to the new bones
            d = skins[o]
            places[rest] = follow.carried(world[o], d["nodes"], d["skin"], d["skin_w"], old_rest, new_rest)[rest]
            count["skin"] += int(rest.sum())
            rest[:] = False
        count["stay"] += int(rest.sum())
        inv = np.linalg.inv(np.array(o.matrix_world))
        _set_rest(o, places @ inv[:3, :3].T + inv[:3, 3])
        mm += (np.linalg.norm(places - world[o], axis=1)[placed] * 1000).tolist()
        follow.record(o)
    bpy.context.view_layer.update()
    from . import anchors
    for o in parts:
        anchors.record(o, target)                     # where they now sit on the target mesh
    said = [f"{count['faces']} by the same faces" if count["faces"] else "",
            f"{count['uv']} by their UVs" if count["uv"] else "",
            f"{count['field']} by how the meshes and bones around them moved" if count["field"] else "",
            f"{count['skin']} with their skin bones" if count["skin"] else "",
            f"{count['stay']} stay where they were" if count["stay"] else ""]
    text = (f"Particles moved onto {target.name} ({len(pairs)} meshes"
            f"{f', median {np.median(mm):.1f} mm' if mm else ''}): " + ", ".join(x for x in said if x))
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

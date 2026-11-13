"""Project onto Mesh: move a Part made from bones onto the cloth mesh it stands for, as rest offsets from its bone
grid (so it still regenerates, and Reset returns it to the bones).

The particles go to the mesh's mid-surface along the grid's normals, not to the nearest point: a ray each way, the
middle of an outer layer and its lining when both are hit. Only faces skinned to the Part's own bones are taken (other
layers, accessories and the body skin to other bones). A hit is refused when it is too far, its face turns away from
the grid, or it lands far from its neighbours' (another fold, a button): the game's proxies follow the mesh's shape,
not its details. A refused particle takes its neighbours' offset."""
import numpy as np
from mathutils import Vector
from mathutils.bvhtree import BVHTree

from . import canon, fileio
from .modules import topology

SHARE = 0.3          # a mesh vertex counts as the Part's when this much of its weight is on the Part's bones
TURN = 0.5           # cos 60 deg: a face turned further from the grid's normal is another side of the shape
THICK = 0.03         # m: two layers this close are one cloth's outside and lining


def _neighbours(faces, n) -> list:
    """Each particle's neighbours along its quads' edges."""
    out = [set() for _ in range(n)]
    for f in faces:
        for k in range(len(f)):
            a, b = f[k], f[(k + 1) % len(f)]
            out[a].add(b)
            out[b].add(a)
    return [sorted(x) for x in out]


def _surface(obj, bones) -> tuple:
    """The mesh faces skinned to ``bones`` (vertices with ``SHARE`` of their weight on them), in the Part's space:
    (vertices, triangles); none when the cloth's armature has no mesh collection."""
    arm = obj.ree_gpuc_part.armature
    mesh_coll = fileio.mesh_collection(arm) if arm is not None else None
    if mesh_coll is None:
        return None, None
    from . import fit
    inv = np.linalg.inv(np.array(obj.matrix_world, float))
    verts, tris = [], []
    for o in fit.mesh_objects(mesh_coll):
        me = o.data
        names = {g.index: g.name for g in o.vertex_groups}
        share = np.zeros(len(me.vertices))
        for v in me.vertices:
            tot = sum(g.weight for g in v.groups)
            if tot > 0:
                share[v.index] = sum(g.weight for g in v.groups if names.get(g.group) in bones) / tot
        if not (share >= SHARE).any():
            continue
        me.calc_loop_triangles()
        t = np.empty(3 * len(me.loop_triangles), np.int32)
        me.loop_triangles.foreach_get("vertices", t)
        t = t.reshape(-1, 3)
        t = t[(share[t] >= SHARE).all(1)]
        if not len(t):
            continue
        co = np.empty(3 * len(me.vertices), np.float32)
        me.vertices.foreach_get("co", co)
        m = inv @ np.array(o.matrix_world, float)
        co = co.reshape(-1, 3) @ m[:3, :3].T + m[:3, 3]
        tris.append(t + sum(len(v) for v in verts))
        verts.append(co)
    if not verts:
        return None, None
    return np.concatenate(verts), np.concatenate(tris)


def _hit(bvh, p, n, reach):
    """The mid-surface point along ``n`` from ``p``, within ``reach``: each way the first hit, and the middle of it and
    the next one past it when that lies within ``THICK`` (a cloth with a lining); between two hits, one each way,
    their middle; else the nearer side's. (point, its face normal), or (None, None) when nothing is hit."""
    sides = []
    for s in (1.0, -1.0):
        d = Vector(s * n)
        loc, nrm, _, dist = bvh.ray_cast(Vector(p), d, reach)
        if loc is None:
            continue
        a = np.array(loc)
        loc2, _, _, dist2 = bvh.ray_cast(loc + d * 1e-4, d, THICK)
        mid = (a + np.array(loc2)) / 2 if loc2 is not None else a
        sides.append((dist, a, mid, np.array(nrm)))
    if not sides:
        return None, None
    if len(sides) == 2 and sides[0][0] + sides[1][0] < THICK:
        return (sides[0][1] + sides[1][1]) / 2, sides[0][3]
    dist, _, mid, nrm = min(sides, key=lambda x: x[0])
    return mid, nrm


def project_part(obj) -> str:
    """Move a Part that follows its bones onto its mesh (see the module); returns the notice, raises ``ValueError``
    when it cannot."""
    if not canon.attached(obj):
        raise ValueError("only a Part that follows its bones is projected onto its mesh")
    d = canon.recipe(obj)
    verts, tris = _surface(obj, set(d["nodes"]))
    if verts is None:
        raise ValueError("no mesh skinned to its bones: the armature's mesh collection has none")
    base = np.asarray(d["base_co"], float)
    rows, cols = d["rows"], d["cols"]
    nb = _neighbours(canon.faces(d), len(base))
    normal = topology.normals(base, canon.load(obj)["tris"])
    gaps = [np.linalg.norm(base[i] - base[j]) for i, js in enumerate(nb) for j in js]
    spacing = float(np.median(gaps)) if gaps else 0.05
    reach = max(spacing, 0.02)
    bvh = BVHTree.FromPolygons(verts.tolist(), tris.tolist(), all_triangles=True)
    off = np.zeros_like(base)
    ok = np.zeros(len(base), bool)
    for i, (p, n) in enumerate(zip(base, normal)):
        loc, face = _hit(bvh, p, n, reach)
        if loc is None or abs(float(np.dot(face, n))) < TURN:
            continue
        off[i], ok[i] = loc - p, True
    # a hit far from its neighbours' landed in another fold
    for i in np.where(ok)[0]:
        mine = [off[j] for j in nb[i] if ok[j]]
        if len(mine) >= 2 and np.linalg.norm(off[i] - np.median(mine, axis=0)) > spacing / 2:
            ok[i] = False
    taken = int(ok.sum())
    filled = ok.copy()
    for _ in range(max(rows, cols)):              # the refused take their neighbours' offsets
        grew = False
        for i in np.where(~filled)[0]:
            mine = [off[j] for j in nb[i] if filled[j]]
            if mine:
                off[i] = np.mean(mine, axis=0)
                filled[i], grew = True, True
        if not grew:
            break
    off[~filled] = 0.0
    # no triangle turned over: pull the particles of a turned one back towards the bones
    t = canon.load(obj)["tris"]
    before = np.cross(base[t[:, 1]] - base[t[:, 0]], base[t[:, 2]] - base[t[:, 0]])
    for _ in range(6):
        co = base + off
        after = np.cross(co[t[:, 1]] - co[t[:, 0]], co[t[:, 2]] - co[t[:, 0]])
        bad = np.where(np.sum(before * after, axis=1) <= 0)[0]
        if not len(bad):
            break
        off[np.unique(t[bad])] *= 0.5
    co = base + off
    canon.set_positions(obj, co)
    canon.build_mesh(obj)
    from . import anchors
    anchors.record(obj)                               # where it now sits on the mesh
    moved = np.linalg.norm(off, axis=1) * 1000
    text = f"Projected onto the mesh: {taken} particles hit it (median {np.median(moved[ok]) if taken else 0:.1f} mm)"
    if int(filled.sum()) > taken:
        text += f", {int(filled.sum()) - taken} took their neighbours' offset"
    if int((~filled).sum()):
        text += f", {int((~filled).sum())} stay on the bones"
    return text

"""Anchors: where a Part's particles sit on the render mesh, to tell when the mesh changed under them and move them
with it.

Each particle is bound to the nearest triangles of the mesh collection's first LOD as Fit to Mesh binds
(``fit.bind``: barycentric coordinates, height, weight), and the particles' places then are kept with it
(``ANCHOR_KEY``). The mesh moved under a particle by how far the binding now places it from that place, so a particle
edited since does not count as moved; Refit moves each particle by its mesh's move, an edit kept. The meshes are named
with their vertex and triangle counts: other counts mean other faces, and the anchors no longer hold.

Anchors are taken on import with a mesh collection, by Fit to Mesh, Project onto Mesh, Part from Mesh and Anchor
to Mesh; a face edit keeps the anchors of the particles it keeps (``remap``)."""
import numpy as np

from . import canon, fileio, fit

ANCHOR_KEY = "ree_gpuc_anchor"     # on a Part: {"meshes", "counts", "tri", "bary", "height", "weight", "co"}
MOVED = 0.005                      # m: a mesh that moved less under a particle has not moved


def _world(obj) -> np.ndarray:
    mw = np.array(obj.matrix_world, float)
    return canon.load(obj)["co"].astype(float) @ mw[:3, :3].T + mw[:3, 3]


def _meshes(obj, mesh_coll=None):
    if mesh_coll is None:
        arm = obj.ree_gpuc_part.armature
        mesh_coll = fileio.mesh_collection(arm) if arm is not None and arm.type == "ARMATURE" else None
    return fit.mesh_objects(mesh_coll) if mesh_coll is not None else []


def _counts(objs) -> list:
    out = []
    for o in objs:
        o.data.calc_loop_triangles()
        out.append([len(o.data.vertices), len(o.data.loop_triangles)])
    return out


def record(obj, mesh_coll=None) -> int:
    """Anchor the Part's particles to the mesh collection's (else its armature's) mesh as they are now; returns how
    many are anchored (none: the anchors dropped)."""
    objs = _meshes(obj, mesh_coll)
    if not objs or not canon.has(obj):
        drop(obj)
        return 0
    src_v, tris = fit._surface(objs)
    co = _world(obj)
    (tri, bary, height, weight), _ = fit.bind(co, src_v, tris)
    if not (tri >= 0).any():
        drop(obj)
        return 0
    obj[ANCHOR_KEY] = {"meshes": [o.name for o in objs], "counts": [x for c in _counts(objs) for x in c],
                       "tri": [int(x) for x in tri.ravel()], "bary": [float(x) for x in bary.ravel()],
                       "height": [float(x) for x in height.ravel()], "weight": [float(x) for x in weight.ravel()],
                       "co": [float(x) for x in co.ravel()]}
    return int((tri >= 0).any(axis=1).sum())


def drop(obj) -> None:
    if ANCHOR_KEY in obj.keys():
        del obj[ANCHOR_KEY]


def _load(obj):
    c = obj.get(ANCHOR_KEY)
    if c is None:
        return None
    k = fit.NEAREST
    tri = np.array(list(c["tri"]), np.int64).reshape(-1, k)
    return {"meshes": [str(n) for n in c["meshes"]], "counts": np.array(list(c["counts"]), np.int64).reshape(-1, 2),
            "binding": (tri, np.array(list(c["bary"]), float).reshape(-1, k, 3),
                        np.array(list(c["height"]), float).reshape(-1, k),
                        np.array(list(c["weight"]), float).reshape(-1, k)),
            "co": np.array(list(c["co"]), float).reshape(-1, 3)}


def moved(obj) -> tuple:
    """(each anchored particle's place on the mesh now minus its place when anchored (world, n x 3; None when the
    anchors no longer hold), why they do not ("" when they do or the Part has none))."""
    a = _load(obj)
    if a is None:
        return None, ""
    import bpy
    objs = [bpy.data.objects.get(n) for n in a["meshes"]]
    gone = [n for n, o in zip(a["meshes"], objs) if o is None or o.type != "MESH"]
    if gone:
        return None, f"its anchor mesh {gone[0]} is gone"
    if not np.array_equal(np.array(_counts(objs), np.int64).reshape(-1, 2), a["counts"]):
        changed = next(o.name for o, c, now in zip(objs, a["counts"], _counts(objs)) if list(c) != now)
        return None, f"{changed} has other faces than when its particles were anchored to it"
    if len(a["co"]) != canon.count(canon.load(obj)):
        return None, "its particles changed since they were anchored"
    dst_v, tris = fit._surface(objs)
    now, bound = fit.place(a["binding"], dst_v, tris, a["co"])
    delta = now - a["co"]
    delta[~bound] = 0.0
    return delta, ""


def problems(obj) -> list:
    """What the anchors say of the Part: the mesh moved under its particles, or the anchors no longer hold."""
    delta, why = moved(obj)
    if why:
        return [f"{why}: Anchor to Mesh anchors them again, or Fit to Mesh moves them from another copy"]
    if delta is None:
        return []
    far = np.linalg.norm(delta, axis=1)
    n = int((far > MOVED).sum())
    if not n:
        return []
    return [f"the mesh moved under {n} particles since they were anchored (up to {far.max() * 1000:.1f} mm): "
            f"Refit to Mesh moves them with it, Anchor to Mesh keeps them where they are"]


def refit(obj) -> int:
    """Move each anchored particle by its mesh's move since it was anchored, then anchor it again; returns how many
    moved. Raises ``ValueError`` when the anchors no longer hold or the particles cannot move."""
    why = fit._fittable(obj)
    if why:
        raise ValueError(why)
    delta, why = moved(obj)
    if why:
        raise ValueError(why)
    if delta is None:
        raise ValueError("it has no anchors: Anchor to Mesh first")
    far = np.linalg.norm(delta, axis=1) > 1e-7
    if far.any():
        co = _world(obj) + delta
        inv = np.linalg.inv(np.array(obj.matrix_world, float))
        fit._set_rest(obj, co @ inv[:3, :3].T + inv[:3, 3])
    record(obj, None)
    return int(far.sum())


def remap(obj, keep) -> None:
    """Keep the anchors of the particles a face edit keeps (``keep``: each new particle's old index, -1 for a new
    one, which has none)."""
    a = _load(obj)
    if a is None:
        return
    keep = np.asarray(keep, np.int64)
    if (keep >= len(a["co"])).any():
        drop(obj)
        return
    tri, bary, height, weight = a["binding"]
    new = keep < 0
    take = np.where(new, 0, keep)
    tri, bary, height, weight, co = tri[take], bary[take], height[take], weight[take], a["co"][take]
    tri[new] = -1
    co[new] = _world(obj)[new] if len(_world(obj)) == len(keep) else 0.0
    obj[ANCHOR_KEY] = {"meshes": a["meshes"], "counts": [int(x) for x in a["counts"].ravel()],
                       "tri": [int(x) for x in tri.ravel()], "bary": [float(x) for x in bary.ravel()],
                       "height": [float(x) for x in height.ravel()], "weight": [float(x) for x in weight.ravel()],
                       "co": [float(x) for x in co.ravel()]}

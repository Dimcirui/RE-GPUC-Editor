"""A Part that does not follow a bone grid (from its file, from a mesh, edited as a mesh) follows its bones by its
skin: it records its skin bones' rest matrices when its particles are placed (``REST_KEY``), and when they move
each particle goes by linear blend skinning, as the armature would carry it: p' = sum_j w_j M_j' M_j^-1 p. A Part
that follows a bone grid holds no record: Regenerate places it.

The record is taken when a Part is first checked (``ensure``) and again by whatever places its particles (Edit as
Mesh, Fit to Mesh, ``follow``). Skin bones the armature lacks stay put."""
import numpy as np

from . import canon

REST_KEY = "ree_gpuc_rest_bones"      # on a Part: {"names": [bone], "mats": 16 floats each, armature space}
MOVED = 1e-5                          # m: a skin bone that moved less stays as it was


def _rest(arm, names) -> dict:
    bones = arm.data.bones
    return {n: np.array(bones[n].matrix_local, float) for n in names if n in bones}


def record(obj) -> None:
    """Remember the rest matrices of the Part's skin bones as they are now."""
    arm = obj.ree_gpuc_part.armature
    if canon.attached(obj) or arm is None or arm.type != "ARMATURE" or not canon.has(obj):
        if REST_KEY in obj.keys():
            del obj[REST_KEY]
        return
    mats = _rest(arm, canon.load(obj)["nodes"])
    obj[REST_KEY] = {"names": list(mats), "mats": [float(x) for m in mats.values() for x in m.ravel()]}


def recorded(obj) -> dict:
    c = obj.get(REST_KEY)
    if c is None:
        return {}
    flat = np.array(list(c["mats"]), float).reshape(-1, 4, 4)       # a copy: the record may be replaced
    return dict(zip([str(n) for n in c["names"]], flat))


def ensure(obj) -> None:
    """Record a Part that has no record yet; drop the record of one that follows a bone grid."""
    if canon.attached(obj):
        if REST_KEY in obj.keys():
            del obj[REST_KEY]
    elif REST_KEY not in obj.keys():
        record(obj)


def moved(obj) -> tuple:
    """(how many of the Part's skin bones moved off their recorded rest, the most a head moved in m)."""
    arm = obj.ree_gpuc_part.armature
    old = recorded(obj)
    if not old or arm is None or arm.type != "ARMATURE":
        return 0, 0.0
    new = _rest(arm, old)
    far = [float(np.abs(new[n] - old[n]).max()) for n in new]
    heads = [float(np.linalg.norm(new[n][:3, 3] - old[n][:3, 3])) for n in new]
    count = sum(1 for x in far if x > MOVED)
    return count, max(heads, default=0.0)


def carried(co, nodes, skin, skin_w, old: dict, new: dict) -> np.ndarray:
    """Points (armature space) skinned to ``nodes`` carried from bones at ``old`` to ``new`` (name: 4x4 rest
    matrix), by their skin; a bone missing from either stays put."""
    co = np.asarray(co, float)
    delta = {n: new[n] @ np.linalg.inv(old[n]) for n in nodes if n in old and n in new}
    out = np.zeros_like(co)
    total = np.zeros(len(co))
    h = np.c_[co, np.ones(len(co))]
    for j, name in enumerate(nodes):
        m = delta.get(name)
        if m is None:
            m = np.eye(4)
        w = np.where(np.asarray(skin) == j, np.asarray(skin_w, float), 0.0).sum(axis=1)
        if not w.any():
            continue
        out += w[:, None] * (h @ m.T)[:, :3]
        total += w
    none = total <= 0
    out[~none] /= total[~none, None]
    out[none] = co[none]
    return out


def follow(obj) -> int:
    """Move the Part's particles with its skin bones from their recorded rest to the armature's rest; returns how
    many moved. Raises ``ValueError`` for a Part whose particles cannot move."""
    from . import fit
    why = fit._fittable(obj)
    if why:
        raise ValueError(why)
    arm = obj.ree_gpuc_part.armature
    old = recorded(obj)
    if canon.attached(obj) or not old or arm is None or arm.type != "ARMATURE":
        return 0
    new = _rest(arm, old)
    d = canon.load(obj)
    to_arm = np.linalg.inv(np.array(arm.matrix_world, float)) @ np.array(obj.matrix_world, float)
    co = d["co"].astype(float) @ to_arm[:3, :3].T + to_arm[:3, 3]
    moved_co = carried(co, d["nodes"], d["skin"], d["skin_w"], old, new)
    back = np.linalg.inv(to_arm)
    count = int((np.linalg.norm(moved_co - co, axis=1) > MOVED).sum())
    if count:
        fit._set_rest(obj, moved_co @ back[:3, :3].T + back[:3, 3])
    record(obj)
    return count

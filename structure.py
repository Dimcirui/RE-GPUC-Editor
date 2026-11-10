"""Take an editable Keep Structure Part's mesh, after its faces, rest positions or skin groups changed, as its new
structure: particles, faces, links and their tails, skin, normals and particle values.

Maintenance constraints:
- ``canon.PARTICLE_ID`` names the particle each vertex was; a vertex whose ID is out of range, or repeated and
  not the copy nearest the particle's last position, is a new particle. Kept particles keep their file index
  (``src``, which the export's driven joints follow), exact values, skin and link tails; welds follow them too.
- Links come from the faces (``topology.links_of``); a new link takes the commonest tail of its shape on the Part,
  a new triangle tangent flags 0.
- Every change ends in ``canon.build_mesh``, which numbers the particles again."""
import collections

import numpy as np

from . import canon, reference, weld
from .modules import gpuc_build, gpuc_params as gp, topology


def _read(obj) -> tuple:
    """(rest positions, faces, face corner UVs, particle IDs) of the mesh; raises ``ValueError`` when the faces are
    no cloth."""
    me = obj.data
    n = len(me.vertices)
    co = np.empty(3 * n, np.float32)
    me.vertices.foreach_get("co", co)
    sizes = np.empty(len(me.polygons), np.int32)
    starts = np.empty(len(me.polygons), np.int32)
    me.polygons.foreach_get("loop_total", sizes)
    me.polygons.foreach_get("loop_start", starts)
    loops = np.empty(len(me.loops), np.int32)
    me.loops.foreach_get("vertex_index", loops)
    if not len(sizes):
        raise ValueError("the Part has no faces")
    if (sizes > 4).any():
        k = int((sizes > 4).sum())
        raise ValueError(f"{k} face{'s have' if k > 1 else ' has'} more than 4 corners: split them into quads or "
                         f"triangles")
    uv_layer = me.uv_layers.get(canon.UV_LAYER) or (me.uv_layers[0] if me.uv_layers else None)
    uv = np.zeros(2 * len(me.loops), np.float32)
    if uv_layer is not None:
        uv_layer.data.foreach_get("uv", uv)
    uv = uv.reshape(-1, 2)
    faces = [tuple(int(v) for v in loops[s:s + k]) for s, k in zip(starts, sizes)]
    face_uv = np.concatenate([uv[s:s + k] for s, k in zip(starts, sizes)])
    attr = me.attributes.get(canon.PARTICLE_ID)
    ids = np.full(n, -1, np.int64)
    if attr is not None and attr.domain == "POINT" and attr.data_type == "INT":
        got = np.empty(n, np.int32)
        attr.data.foreach_get("value", got)
        ids = got.astype(np.int64)
    return co.reshape(-1, 3), faces, face_uv, ids


def _check(n, faces, co) -> None:
    used = {v for f in faces for v in f}
    if len(used) != n:
        raise ValueError(f"{n - len(used)} particles are on no face: delete them or give them faces")
    if len({frozenset(f) for f in faces}) != len(faces):
        raise ValueError("two faces share all their corners")
    per_edge = collections.Counter(tuple(sorted((f[i], f[(i + 1) % len(f)]))) for f in faces for i in range(len(f)))
    many = sum(1 for k in per_edge.values() if k > 2)
    if many:
        raise ValueError(f"{many} edges have more than two faces")
    try:
        gpuc_build.validate_geometry(co, topology.triangles(faces, np.zeros((sum(map(len, faces)), 2)))[0])
    except ValueError as e:
        raise ValueError(f"{e} (merge or move those particles)") from e


def _kept(ids, co, old_co) -> np.ndarray:
    """The particle each vertex keeps, -1 for a new one."""
    keep = np.full(len(ids), -1, np.int64)
    by_id = collections.defaultdict(list)
    for v, i in enumerate(ids):
        if 0 <= i < len(old_co):
            by_id[int(i)].append(v)
    for i, vs in by_id.items():
        keep[min(vs, key=lambda v: float(np.linalg.norm(co[v] - old_co[i])))] = i
    return keep


def _skin(obj, keep, old, slots) -> tuple:
    """(bone names, skin as indices into them, weights in 255ths): kept particles whose groups still hold their
    weights keep them exactly."""
    names, w = canon.skin_groups(obj)
    have = canon.skin_dense(old)
    rows = []
    for v in range(len(w)):
        i = keep[v]
        if i >= 0:
            mine = {name: have[i, k] for k, name in enumerate(old["nodes"]) if have[i, k] > 0}
            now = {names[k]: w[v, k] for k in np.flatnonzero(w[v] > 0)}
            if mine.keys() == now.keys() and all(abs(mine[b] - now[b]) <= 0.5 / 255 for b in mine):
                rows.append([(old["nodes"][old["skin"][i, k]], int(round(old["skin_w"][i, k] * 255)))
                             for k in range(old["skin"].shape[1]) if old["skin_w"][i, k] > 0])
                continue
        order = [k for k in np.argsort(-w[v], kind="stable") if w[v, k] > 0][:slots]
        if not order:
            raise ValueError(f"particle {v} has no skin weights: paint it in a bone's group")
        x = w[v, order] / w[v, order].sum()
        u8 = np.floor(x * 255 + 0.5).astype(int)
        u8[int(np.argmax(u8))] += 255 - int(u8.sum())
        rows.append([(names[k], int(u)) for k, u in zip(order, u8) if u > 0])
    used = sorted({b for row in rows for b, _ in row})
    at = {b: k for k, b in enumerate(used)}
    skin = np.zeros((len(rows), slots), np.int64)
    skin_w = np.zeros((len(rows), slots), np.float32)
    for v, row in enumerate(rows):
        for k, (b, u) in enumerate(row):
            skin[v, k], skin_w[v, k] = at[b], u / 255.0
    return used, skin, skin_w


def _links(obj, faces, co, keep, old) -> tuple:
    """(links (a, b, type, shape), their tails) of the faces; a link both of whose particles are kept, between them
    in the file too, keeps its tail."""
    ln = topology.links_of(faces, obj.ree_gpuc_part.diagonal_bend)
    before = {(int(a), int(b), int(t)): (int(s), tuple(map(float, tail)))
              for (a, b, t, s), tail in zip(old["links"], old["tails"])}
    common = {}
    for s in range(len(gp.SHAPES)):
        tails = collections.Counter(tail for (shape, tail) in before.values() if shape == s)
        if tails:
            common[s] = tails.most_common(1)[0][0]
    shape_tail = [getattr(obj.ree_gpuc_part, n).tail() for n in gp.SHAPES]
    out, tails = [], []
    for a, b, t in ln.tolist():
        ka, kb = keep[a], keep[b]
        was = before.get((min(ka, kb), max(ka, kb), t)) if ka >= 0 and kb >= 0 else None
        if was is not None:
            shape, tail = was
        else:
            shape = 2 if t == 1 else 3 if t != 0 else (0 if gpuc_build.direction(co[a], co[b]) == "vert" else 1)
            tail = common.get(shape, shape_tail[shape])
        out.append((a, b, t, shape))
        tails.append(tail)
    return np.asarray(out, np.int64).reshape(-1, 4), np.asarray(tails, np.float32).reshape(-1, 4)


def _values(obj, keep, old) -> dict:
    """Each particle value from its group; a kept particle whose weight is unchanged keeps its exact value."""
    weights = canon.group_weights(obj, len(keep))
    out = {}
    for f in canon.FIELD_NAMES:
        w = weights[f]
        was = canon.mapping_weights(obj, f, old)
        v = np.full(len(keep), canon.DEFAULTS[f], np.float32)
        for i in range(len(keep)):
            k = keep[i]
            if w is not None and not np.isnan(w[i]) and not (k >= 0 and np.float32(w[i]) == was[k]):
                v[i] = canon.to_value(obj, f, [w[i]])[0]
            elif k >= 0:
                v[i] = old[f][k]
        out[f] = v
    return out


def _welds(obj, keep) -> int:
    """Move the welds of the Part onto its kept particles; return how many pairs were dropped."""
    coll = reference.cloth_of(obj)
    new = {int(k): v for v, k in enumerate(keep) if k >= 0}
    dropped = 0
    for w in weld.objects(coll):
        a, b = w.ree_gpuc_weld.part_a, w.ree_gpuc_weld.part_b
        if obj not in (a, b) or weld.PAIRS_KEY not in w.keys():
            continue
        flat = list(w[weld.PAIRS_KEY])
        out = []
        for ia, ib in zip(flat[0::2], flat[1::2]):
            ia = new.get(int(ia), -1) if a == obj else int(ia)
            ib = new.get(int(ib), -1) if b == obj else int(ib)
            if ia < 0 or ib < 0:
                dropped += 1
                continue
            out += [ia, ib]
        w[weld.PAIRS_KEY] = out
    return dropped


def adopt(obj) -> str:
    """Take the Part's mesh as its new structure and rebuild the mesh; return the notice. Raises ``ValueError``
    (the caller restores the last structure) when the mesh is no cloth."""
    old = canon.load(obj)
    co, faces, face_uv, ids = _read(obj)
    n = len(co)
    _check(n, faces, co)
    keep = _kept(ids, co, old["co"])
    names, skin, skin_w = _skin(obj, keep, old, old["skin"].shape[1])
    links, tails = _links(obj, faces, co, keep, old)
    values = _values(obj, keep, old)
    tris, tri_uv = topology.triangles(faces, face_uv)
    face_uv = face_uv - (old["tile"], 0.0)
    normal = topology.normals(co, tris)
    sign = float(np.einsum("ij,ij->", topology.normals(old["co"], old["tris"]), old["normal"]))
    if sign < 0:
        normal = -normal
    src = np.where(keep >= 0, old["src"][np.clip(keep, 0, None)], -1)
    flag = {frozenset(t): int(x) for t, x in zip(topology.triangles(old["faces"], old["face_uv"])[0].tolist(),
                                                   old["tri_flags"])}
    tri_flags = [flag.get(frozenset(int(keep[v]) for v in t), 0) if (keep[list(t)] >= 0).all() else 0
                 for t in tris.tolist()]
    data = {**old, "count": n, "co": co, "tris": tris, "tri_uv": tri_uv - (old["tile"], 0.0), "faces": faces,
            "face_uv": face_uv, "links": links, "tails": tails, "nodes": names, "skin": skin, "skin_w": skin_w,
            "normal": normal, "src": src, "tri_flags": tri_flags, "editable": True, "edited": True, **values}
    dropped = _welds(obj, keep)
    if canon.MAPPING_KEY in obj.keys():
        del obj[canon.MAPPING_KEY]
    canon.fit_ranges(obj, values)
    canon.store(obj, data)
    canon.build_mesh(obj)
    from . import sim
    sim.invalidate(obj)
    added = int((keep < 0).sum())
    gone = len(old["co"]) - int((keep >= 0).sum())
    text = f"Structure changed: {n} particles ({added} new, {gone} removed), {len(links)} links"
    if dropped:
        text += f"; {dropped} welded pairs lost a particle and were dropped"
    return text + "; the export writes the file again from the Parts"

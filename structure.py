"""Take an editable Keep Structure Part's mesh, after its faces, rest positions or skin groups changed, as its new
structure: particles, faces, links and their tails, skin, normals and particle values.

Maintenance constraints:
- ``canon.PARTICLE_ID`` names the particle each vertex was; a vertex whose ID is out of range, or repeated and
  not the copy nearest the particle's last position, is a new particle. Kept particles keep their file index
  (``src``, which the export's driven joints follow), exact values, skin and link tails; welds follow them too.
- Links come from the faces (``topology.links_of``); a new link takes the commonest tail of its shape on the Part,
  a new triangle the export's tangent flags for its UVs (``gpuc_build.tangent_flags``).
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


def _u8(w, total: int) -> np.ndarray:
    """Weights in 255ths summing to ``total``, the rounding left to the heaviest."""
    u8 = np.floor(np.asarray(w, float) / w.sum() * total + 0.5).astype(int)
    u8[int(np.argmax(u8))] += total - int(u8.sum())
    return u8


def normalize_skin(obj, only=None) -> tuple:
    """Normalize the bone groups of an editable Part as the export takes them: each vertex keeps its heaviest
    weights up to the Part's skin slots, summing to 1 in 255ths; locked groups keep theirs and the others share
    what is left. ``only``: the vertex indices to change, else all. Returns (vertices changed, weights removed,
    vertices with no skin weight)."""
    d = canon.load(obj)
    slots = d["skin"].shape[1]
    names, w = canon.skin_groups(obj)
    groups = [obj.vertex_groups[n] for n in names]
    locked = np.array([g.lock_weight for g in groups], bool)
    changed = removed = empty = 0
    for v in (range(len(w)) if only is None else only):
        row = w[v]
        fixed = [k for k in np.flatnonzero(row > 0) if locked[k]]
        free = [k for k in np.argsort(-row, kind="stable") if row[k] > 0 and not locked[k]]
        keep = free[:max(slots - len(fixed), 0)]
        total = 255 - int(round(row[fixed].sum() * 255))
        if not keep or total <= 0:
            empty += not fixed and not free
            drop = free
            new = {}
        else:
            drop = free[len(keep):]
            new = dict(zip(keep, _u8(row[keep], total) / 255.0))
        hit = bool(drop)
        for k in drop:
            groups[k].remove([v])
        for k, x in new.items():
            if x <= 0:
                groups[k].remove([v])
                removed += 1
                hit = True
            elif abs(x - row[k]) > 1e-6:
                groups[k].add([v], float(x), "REPLACE")
                hit = True
        removed += len(drop)
        changed += hit
    return changed, removed, empty


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
        rows.append([(names[k], int(u)) for k, u in zip(order, _u8(w[v, order], 255)) if u > 0])
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


def _tri_flags(faces, face_uv, tris, keep, old) -> list:
    """Each triangle's tangent flags: a triangle of kept particles that was one keeps its flags; any other takes
    the export's flags for a new triangle."""
    corners, at = [], 0
    for f in faces:
        corners.append([tuple(map(float, p)) for p in np.clip(face_uv[at:at + len(f)], 0.0, 1.0)])
        at += len(f)
    fresh = gpuc_build.tangent_flags(faces, corners)
    was = topology.carry_flags(topology.triangles(old["faces"], old["face_uv"])[0].tolist(), old["tri_flags"],
                               keep[tris].tolist())
    return [f if f is not None else x for f, x in zip(was, fresh)]


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
        names = list(w.get(weld.BONES_KEY, ()))
        out, kept = [], []
        for k, (ia, ib) in enumerate(zip(flat[0::2], flat[1::2])):
            ia = new.get(int(ia), -1) if a == obj else int(ia)
            ib = new.get(int(ib), -1) if b == obj else int(ib)
            if ia < 0 or ib < 0:
                dropped += 1
                continue
            out += [ia, ib]
            kept += names[k:k + 1]
        w[weld.PAIRS_KEY] = out
        if names:
            w[weld.BONES_KEY] = kept
    return dropped


def _game_slots(obj, coll=None) -> int:
    from . import props
    from .modules import gpuc
    head = props.header(coll if coll is not None else reference.cloth_of(obj))
    layout = gpuc.LAYOUTS.get(int(head.ree_gpuc_header.game[1:])) if head is not None else None
    return gpuc.skin_slots(layout) if layout is not None else 8


def edit_as_mesh(obj) -> None:
    """Stop a Part following its bone grid, so that it takes topology and skin edits: its mesh as it is, its skin
    widened to the game's slots in 255ths, in one group per bone. The grid stays, with the values of now, for
    ``back_to_bones``; its welds keep their pairs."""
    from . import fileio
    canon.restore(obj)
    if not canon.attached(obj):
        raise ValueError(f"{obj.name} does not follow its bones")
    g = canon.recipe(obj)
    idx, u8 = fileio.grid_skin(g, max(_game_slots(obj), g["skin"].shape[1]))
    pairs = []                         # (weld, its pairs, their bones when both Parts follow their bones)
    for w in weld.objects(reference.cloth_of(obj)):
        a, b = w.ree_gpuc_weld.part_a, w.ree_gpuc_weld.part_b
        if obj in (a, b) and not weld.problems(w):
            names = None
            if canon.attached(a) and canon.attached(b):          # the order weld.shared_pairs gives
                names = sorted(set(weld.node_particles(weld._layout(a))) & set(weld.node_particles(weld._layout(b))))
            pairs.append((w, weld.pairs(w), names))
    c, r = obj[canon.KEY], obj[canon.RECIPE_KEY]
    for f in canon.FIELD_NAMES:                       # the grid's values to go back to
        r[f] = list(c[f])
    c["skin"] = [int(x) for x in idx.ravel()]
    c["skin_w"] = [float(x) for x in (u8 / 255.0).ravel()]
    del obj[canon.ATTACHED_KEY]
    for w, p, names in pairs:
        w[weld.PAIRS_KEY] = [int(i) for pair in p for i in pair]
        if names is not None:
            w[weld.BONES_KEY] = names
    canon.build_mesh(obj)
    from . import sim
    sim.invalidate(obj)


def back_to_bones(obj) -> None:
    """Make a Part edited as a mesh follow its bone grid again, its edits dropped: the grid's values and settings
    from when it was edited as a mesh are back."""
    if not canon.from_bones(obj):
        raise ValueError(f"{obj.name} was not made from bones")
    g = canon.recipe(obj)
    if canon.MAPPING_KEY in obj.keys():
        del obj[canon.MAPPING_KEY]
    canon.store(obj, g)
    canon.fit_ranges(obj, canon.load(obj))
    canon.build_mesh(obj)
    for w in weld.objects(reference.cloth_of(obj)):
        a, b = w.ree_gpuc_weld.part_a, w.ree_gpuc_weld.part_b
        if obj in (a, b) and canon.attached(a) and canon.attached(b):
            for key in (weld.PAIRS_KEY, weld.BONES_KEY):
                if key in w.keys():
                    del w[key]
    from . import sim
    sim.invalidate(obj)


def _read_source(src, arm) -> tuple:
    """(rest positions in the armature's space, faces, face corner UVs, bone names, weights: vertices x bones) of
    the mesh ``src`` with its modifiers but not its Armature modifiers; raises ``ValueError``."""
    import bpy
    posed = [m for m in src.modifiers if m.type == "ARMATURE" and m.show_viewport]
    for m in posed:
        m.show_viewport = False
    try:
        ev = src.evaluated_get(bpy.context.evaluated_depsgraph_get())
        me = ev.to_mesh()
        try:
            n = len(me.vertices)
            co = np.empty(3 * n, np.float32)
            me.vertices.foreach_get("co", co)
            sizes = [p.loop_total for p in me.polygons]
            if any(k > 4 for k in sizes):
                k = sum(1 for s in sizes if s > 4)
                raise ValueError(f"{k} face{'s have' if k > 1 else ' has'} more than 4 corners: split them into "
                                 f"quads or triangles")
            faces = [tuple(p.vertices) for p in me.polygons]
            layer = me.uv_layers.active or (me.uv_layers[0] if me.uv_layers else None)
            if layer is None:
                raise ValueError("it has no UV map: unwrap it (the UVs give the particles' tangents)")
            uv = np.empty(2 * len(me.loops), np.float32)
            layer.data.foreach_get("uv", uv)
            uv = uv.reshape(-1, 2)
            face_uv = np.concatenate([uv[p.loop_start:p.loop_start + p.loop_total] for p in me.polygons]) \
                if len(me.polygons) else np.zeros((0, 2), np.float32)
            groups = {g.index: g.name for g in src.vertex_groups if g.name in arm.data.bones}
            names = sorted(set(groups.values()))
            col = {i: names.index(name) for i, name in groups.items()}
            w = np.zeros((n, len(names)), np.float32)
            for v in me.vertices:
                for ge in v.groups:
                    k = col.get(ge.group)
                    if k is not None:
                        w[v.index, k] = ge.weight
        finally:
            ev.to_mesh_clear()
    finally:
        for m in posed:
            m.show_viewport = True
    m = np.linalg.inv(np.array(arm.matrix_world, float)) @ np.array(src.matrix_world, float)
    co = (co.reshape(-1, 3).astype(float) @ m[:3, :3].T + m[:3, 3]).astype(np.float32)
    return co, faces, face_uv, names, w


def from_mesh(src, coll, config=None) -> tuple:
    """A new editable Part of the cloth made of the mesh ``src`` (its modifiers applied, its Armature modifiers
    not): its vertices the particles at rest, its faces, its UV map, its weights to the cloth's armature's bones
    as the skin (the heaviest up to the game's bones, in 255ths), links from the faces with the Part's link
    settings, default values, Driven Bones Listed and empty. Returns (Part, notice); raises ``ValueError`` when the
    mesh is no cloth."""
    from . import generate, props
    arm = generate._armature(coll)
    if arm is None or arm.type != "ARMATURE":
        raise ValueError("the cloth has no armature")
    co, faces, face_uv, names, w = _read_source(src, arm)
    n = len(co)
    if not faces:
        raise ValueError("it has no faces")
    _check(n, faces, co)
    slots = _game_slots(None, coll)
    rows, empty = [], 0
    for v in range(n):
        order = [k for k in np.argsort(-w[v], kind="stable") if w[v, k] > 0][:slots]
        if not order:
            empty += 1
            continue
        rows.append([(k, int(u)) for k, u in zip(order, _u8(w[v, order], 255)) if u > 0])
    if empty:
        raise ValueError(f"{empty} vert{'ices have' if empty > 1 else 'ex has'} no weight to a bone of {arm.name}: "
                         f"transfer or paint skin weights first")
    used = sorted({k for row in rows for k, _ in row})
    at = {k: i for i, k in enumerate(used)}
    skin = np.zeros((n, slots), np.int64)
    skin_w = np.zeros((n, slots), np.float32)
    for v, row in enumerate(rows):
        for s, (k, u) in enumerate(row):
            skin[v, s], skin_w[v, s] = at[k], u / 255.0
    outside = int(((face_uv < 0) | (face_uv > 1)).any(axis=1).sum())
    face_uv = (np.round(np.clip(face_uv, 0.0, 1.0).astype(float) * 65535) / 65535).astype(np.float32)
    corners, k = [], 0
    for f in faces:
        corners.append([tuple(map(float, p)) for p in face_uv[k:k + len(f)]])
        k += len(f)
    tris, tri_uv = topology.triangles(faces, face_uv)

    existing = props.parts(coll)
    tile = max((p.ree_gpuc_part.uv_tile for p in existing), default=-1) + 1
    name = generate.unique_name("GPUC_PART_", "_" + src.name)
    obj = bpy_new_part(coll, arm, name, config)
    p = obj.ree_gpuc_part
    p.uv_tile = tile
    p.part_name = f"_p{len(existing):03d}"
    heaviest = names[used[int(np.argmax([sum(u for row in rows for kk, u in row if kk == k) for k in used]))]]
    parent = arm.data.bones[heaviest].parent
    p.root_joint = parent.name if parent is not None else heaviest
    p.link_preset = generate.LINK_PRESET
    p.driven_bones = "LISTED"
    shape_tail = [getattr(p, s).tail() for s in gp.SHAPES]
    links = []
    for a, b, t in topology.links_of(faces, p.diagonal_bend).tolist():
        links.append((a, b, t, 2 if t == 1 else 3 if t != 0 else
                      (0 if gpuc_build.direction(co[a], co[b]) == "vert" else 1)))
    links = np.asarray(links, np.int64).reshape(-1, 4)
    values = {f: np.full(n, canon.DEFAULTS[f], np.float32) for f in canon.FIELD_NAMES}
    data = {"locked": True, "count": n, "tile": tile, "co": co, "tris": tris, "tri_uv": tri_uv, "links": links,
            "tails": np.asarray([shape_tail[s] for s in links[:, 3]], np.float32).reshape(-1, 4),
            "nodes": [names[k] for k in used], "skin": skin, "skin_w": skin_w, "normal": topology.normals(co, tris),
            "faces": faces, "face_uv": face_uv, "src": np.arange(n), "tri_flags": gpuc_build.tangent_flags(faces, corners),
            "editable": True, **values}
    obj[canon.MESH_KEY] = 1
    obj[canon.EDITED_KEY] = [s for s in gp.SHAPES]          # the Part's link settings apply to all its links
    canon.fit_ranges(obj, values)
    canon.store(obj, data)
    canon.build_mesh(obj)
    text = (f"Made from {src.name}: {n} particles, {len(links)} links. List the bones it drives (Driven Bones) and "
            f"paint Simulation weight 0 where it is held")
    if outside:
        text += f"; {outside} UV corners outside 0-1 were clamped"
    return obj, text


def bpy_new_part(coll, arm, name, config=None):
    """An empty Part object of the cloth under a Config (the one given if it is the cloth's, else the cloth's first,
    else a new one)."""
    import bpy
    from . import generate, props
    obj = bpy.data.objects.new(name, bpy.data.meshes.new(name))
    obj["TYPE"] = props.PART_TYPE
    coll.objects.link(obj)
    obj.hide_render = True
    obj.show_wire = True
    obj.show_in_front = bpy.context.scene.ree_gpuc.in_front
    if props.header(coll) is None:
        generate.new_header(coll, arm)
    mine = props.configs(coll)
    config = config if config in mine else (mine or [None])[0]
    if config is None:
        config = generate.new_config(coll, arm)
    if config.parent is None:
        props.place_under(config, props.header(coll) or arm)
    obj.ree_gpuc_part.armature = arm
    obj.ree_gpuc_part.config = config
    return obj


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
    tri_flags = _tri_flags(faces, face_uv, tris, keep, old)
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
    return text if canon.made_here(obj) else text + "; the export writes the file again from the Parts"

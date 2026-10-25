"""A Part's canonical data: what Generate made, kept on the object as an ID property. The
mesh shows it and holds the particle values: valid generated-particle rest-position changes are committed as
offsets when leaving edit mode; changes to topology, particle IDs or grid UV are undone (`restore`). Keep
Structure positions remain locked. The particle values are vertex groups (gpuc_sim_weight, gpuc_radius,
gpuc_max_distance; weight 0-1 mapped onto the Part's range, not normalised, for no modifier), painted or set
freely, and copied back here as the last good values (`sync`) whenever the structure is intact.

Stored: the bone grid (node_rows x node_cols bone names, row by row, row 0 at the top) and its bones' rest heads
(armature space); how many particles go between bones (sub_rows down, sub_cols across; 0 and 0: one particle
per bone); the particle grid's size and whether it closes; every particle's rest position, its skin (4 bones,
flat node indices, and their weights) and the particle values below.

A locked Part (imported with Keep Structure) keeps a .gpuc part's own structure instead of a bone grid: its
particles' rest positions, triangles and their UVs, links (each with its type, shape and own tail), skin (6 bones
by name, u8 weights) and the file's normals; it is not regenerated. count(), faces(), face_uvs(), links() and
particle_uv() serve both kinds."""
import bpy
import numpy as np

from .modules import grid

KEY = "ree_gpuc_canon"
UV_LAYER = "grid"
ROW, COL = "gpuc_row", "gpuc_col"
PARTICLE_ID = "gpuc_particle_id"
MAPPING_KEY = "ree_gpuc_value_mapping"


def mapping_weights(obj, field, data):
    """Reuse the exact painted mask, including when Min == Max; imported values stay lossless."""
    state = obj.get(MAPPING_KEY, {}).get(field)
    if (state is not None and tuple(state["range"]) == field_range(obj, field)
            and np.array_equal(f32(state["values"]), data[field])
            and len(state["weights"]) == count(data)):
        return f32(state["weights"])
    return to_weight(obj, field, data[field]).astype(np.float32)


def remember_mapping(obj, weights=None):
    d = load(obj)
    weights = group_weights(obj, count(d)) if weights is None else weights
    if MAPPING_KEY not in obj:
        obj[MAPPING_KEY] = {}
    cache = obj[MAPPING_KEY]
    for field, w in weights.items():
        if w is None or len(w) != count(d) or not np.isfinite(w).all():
            continue
        w = f32(w)
        previous = cache.get(field)
        bounds = field_range(obj, field)
        if (previous is not None and tuple(previous["range"]) == bounds
                and np.array_equal(f32(previous["weights"]), w)
                and np.array_equal(f32(previous["values"]), d[field])):
            continue
        cache[field] = {"range": list(bounds), "weights": w.tolist(), "values": d[field].tolist()}


def change_range(obj, field):
    """Keep the painted mask and update only this field's final physical values."""
    state = obj.get(MAPPING_KEY, {}).get(field)
    if state is not None and tuple(state["range"]) == field_range(obj, field):
        return False  # Reconfirming a value must not round-trip untouched file data.
    d = load(obj)
    w = group_weights(obj, count(d))[field]
    if w is None or not np.isfinite(w).all():
        return False  # Invalid structure is restored by the existing canonical handler.
    obj[KEY][field] = to_value(obj, field, w).tolist()
    remember_mapping(obj, {field: w})
    from . import sim
    sim.invalidate(obj)
    return True


def fit_ranges(obj, values):
    """Import/migration only: fit mappings to exact supplied values without rescaling those values."""
    from . import props
    props._loading_ranges += 1
    try:
        for field in FIELD_NAMES[1:]:
            if field not in values:
                continue
            v = np.asarray(values[field], float)
            lo, hi = field_range(obj, field)
            setattr(obj.ree_gpuc_part, field + "_range", (min(lo, float(v.min())), max(hi, float(v.max()))))
    finally:
        props._loading_ranges -= 1

# the particle record's values the editor sets: (field, label, record offset, default)
PARTICLE_FIELDS = (
    ("sim_weight", "Simulation weight", 0x00, 1.0),
    ("radius", "Collision radius", 0x04, 0.02),
    ("max_distance", "Max distance", 0x0C, 1.0),
    ("inverse_mass", "Inverse mass", 0x08, 17.267),         # wind response only (ch03_100_1005's: these defaults)
    ("space_limit_lower", "Space limit lower", 0x10, 2.0),  # inward along the normal (Config Info State's limit)
    ("space_limit_upper", "Space limit upper", 0x14, 0.225),
    ("backstop_radius", "Backstop radius", 0x18, 0.0),      # 0 off
    ("backstop_offset", "Backstop offset", 0x1C, 0.0),
)
DEFAULTS = {f[0]: f[3] for f in PARTICLE_FIELDS}
FIELD_NAMES = tuple(f[0] for f in PARTICLE_FIELDS)


def f32(values) -> np.ndarray:
    return np.asarray(values, np.float32)


def has(obj) -> bool:
    return KEY in obj.keys()


def load(obj) -> dict:
    c = obj[KEY]
    if c.get("locked"):
        return _load_locked(c)
    rows, cols = int(c["rows"]), int(c["cols"])
    co = f32(c["co"]).reshape(-1, 3)
    if "nodes" in c:
        nodes, nr, nc = [str(b) for b in c["nodes"]], int(c["node_rows"]), int(c["node_cols"])
        sr, sc = int(c["sub_rows"]), int(c["sub_cols"])
        node_co = f32(c["node_co"]).reshape(-1, 3)
        skin, skin_w = np.asarray(c["skin"], np.int64).reshape(-1, 4), f32(c["skin_w"]).reshape(-1, 4)
        centre = f32(c["centre"]) if len(c["centre"]) else None
    else:                                    # v0.1: one particle per bone
        nodes, nr, nc, sr, sc, node_co = [str(b) for b in c["bones"]], rows, cols, 0, 0, co
        skin = np.repeat(np.arange(rows * cols)[:, None], 4, axis=1)
        skin_w = np.tile(f32([1, 0, 0, 0]), (rows * cols, 1))
        centre = None
    base = f32(c.get("base_co", co)).reshape(-1, 3)
    offset = f32(c.get("rest_offset", np.zeros_like(co))).reshape(-1, 3)
    return {"locked": False, "count": rows * cols, "base_co": base, "rest_offset": offset,
            "rows": rows, "cols": cols, "closed": bool(c["closed"]), "tile": int(c["tile"]), "co": co,
            "nodes": nodes, "node_rows": nr, "node_cols": nc, "sub_rows": sr, "sub_cols": sc, "node_co": node_co,
            "skin": skin, "skin_w": skin_w, "centre": centre,
            **{n: f32(c[n]) if n in c else np.full(rows * cols, DEFAULTS[n], np.float32) for n in FIELD_NAMES}}


def _load_locked(c) -> dict:
    n = int(c["count"])
    links = np.asarray(c["links"], np.int64).reshape(-1, 4)
    return {"locked": True, "count": n, "rows": 0, "cols": 0, "closed": False, "centre": None,
            "tile": int(c["tile"]), "co": f32(c["co"]).reshape(-1, 3),
            "tris": np.asarray(c["tris"], np.int64).reshape(-1, 3), "tri_uv": f32(c["tri_uv"]).reshape(-1, 3, 2),
            "links": links, "tails": f32(c["tails"]).reshape(-1, 4), "nodes": [str(b) for b in c["nodes"]],
            "skin": np.asarray(c["skin"], np.int64).reshape(n, -1), "skin_w": f32(c["skin_w"]).reshape(n, -1),
            "normal": f32(c["normal"]).reshape(-1, 3),
            **{f: f32(c[f]) if f in c else np.full(n, DEFAULTS[f], np.float32) for f in FIELD_NAMES}}


def store(obj, data: dict) -> None:
    fl = lambda a: [float(x) for x in f32(a).ravel()]        # noqa: E731
    if data.get("locked"):
        ints = lambda a: [int(x) for x in np.asarray(a).ravel()]  # noqa: E731
        obj[KEY] = {"locked": 1, "count": int(data["count"]), "tile": int(data["tile"]), "co": fl(data["co"]),
                    "tris": ints(data["tris"]), "tri_uv": fl(data["tri_uv"]), "links": ints(data["links"]),
                    "tails": fl(data["tails"]), "nodes": list(data["nodes"]), "skin": ints(data["skin"]),
                    "skin_w": fl(data["skin_w"]), "normal": fl(data["normal"]),
                    **{n: fl(data[n]) for n in FIELD_NAMES}}
        return
    base = f32(data.get("base_co", data["co"]))
    offset = f32(data.get("rest_offset", np.zeros_like(base)))
    obj[KEY] = {"base_co": fl(base), "rest_offset": fl(offset),
                "rows": int(data["rows"]), "cols": int(data["cols"]), "closed": int(bool(data["closed"])),
                "tile": int(data["tile"]), "co": fl(base + offset), "nodes": list(data["nodes"]),
                "node_rows": int(data["node_rows"]), "node_cols": int(data["node_cols"]),
                "sub_rows": int(data["sub_rows"]), "sub_cols": int(data["sub_cols"]), "node_co": fl(data["node_co"]),
                "skin": [int(x) for x in np.asarray(data["skin"]).ravel()], "skin_w": fl(data["skin_w"]),
                "centre": [] if data["centre"] is None else fl(data["centre"]),
                **{n: fl(data[n]) for n in FIELD_NAMES}}


def locked(obj) -> bool:
    return has(obj) and bool(obj[KEY].get("locked"))


def count(d: dict) -> int:
    return d["count"]


def faces(d: dict) -> list:
    """The mesh's faces: the grid's quads, or a locked Part's triangles."""
    if d["locked"]:
        return [tuple(int(v) for v in t) for t in d["tris"]]
    return grid.faces(d["rows"], d["cols"], d["closed"])


def face_uvs(d: dict) -> np.ndarray:
    """Each face corner's UV, on the Part's UV tile (u + tile)."""
    if d["locked"]:
        return d["tri_uv"].reshape(-1, 2) + (d["tile"], 0.0)
    return np.asarray(grid.face_uv(d["rows"], d["cols"], d["closed"], d["tile"]), np.float32).reshape(-1, 2)


_grid_links: dict = {}


def links(d: dict) -> np.ndarray:
    """Every link as (a, b, type, shape index into grid.SHAPES)."""
    if d["locked"]:
        return d["links"]
    key = (d["rows"], d["cols"], d["closed"])
    if key not in _grid_links:
        _grid_links[key] = grid.links(*key)
    return _grid_links[key]


EDITED_KEY = "ree_gpuc_tails_edited"       # on a locked Part: the link shapes whose tail was set by hand


def link_tails(obj, d: dict) -> np.ndarray:
    """Each link's tail (k, k2, lo, hi): its shape's on the Part; a locked Part's links keep their own except the
    shapes whose tail was set by hand (all their links take it)."""
    part = obj.ree_gpuc_part
    names = [s.lower() for s in grid.SHAPES]
    shape = np.array([getattr(part, n).tail() for n in names], np.float32)
    ln = links(d)
    if not d["locked"]:
        return shape[ln[:, 3]]
    out = d["tails"].copy()
    for k, n in enumerate(names):
        if n in obj.get(EDITED_KEY, ()):
            out[ln[:, 3] == k] = shape[k]
    return out


def particle_uv(d: dict) -> np.ndarray:
    """Each particle's place on the UV (a locked Part's: the mean of its triangle corners')."""
    if not d["locked"]:
        return grid.particle_uv(d["rows"], d["cols"], d["closed"], d["tile"])
    uv, n = np.zeros((d["count"], 2)), np.zeros(d["count"])
    np.add.at(uv, d["tris"].ravel(), d["tri_uv"].reshape(-1, 2))
    np.add.at(n, d["tris"].ravel(), 1.0)
    return uv / np.maximum(n, 1)[:, None] + (d["tile"], 0.0)


def group_name(field: str) -> str:
    return "gpuc_" + field


def field_range(obj, field: str) -> tuple[float, float]:
    """The values a group weight 0 and 1 stand for: 0-1 for the simulation weight, else the Part's range."""
    if field == "sim_weight":
        return 0.0, 1.0
    lo, hi = getattr(obj.ree_gpuc_part, field + "_range")
    return float(lo), float(hi)


def to_weight(obj, field: str, values) -> np.ndarray:
    lo, hi = field_range(obj, field)
    v = np.asarray(values, float)
    return np.clip((v - lo) / (hi - lo), 0.0, 1.0) if hi != lo else np.zeros(len(v))


def to_value(obj, field: str, weights) -> np.ndarray:
    lo, hi = field_range(obj, field)
    return f32(lo + np.asarray(weights, float) * (hi - lo))


def set_values(obj, field: str, index, value: float) -> None:
    """Particle values into the canonical data (the caller updates the mesh's group)."""
    c = obj[KEY]
    n = int(c["count"]) if c.get("locked") else int(c["rows"]) * int(c["cols"])
    vals = f32(c[field]) if field in c else np.full(n, DEFAULTS[field], np.float32)
    vals[np.asarray(index, np.intp)] = np.float32(value)
    c[field] = [float(x) for x in vals]


def group_weights(obj, n: int) -> dict:
    """Each value group's weights as the mesh has them (edit mode: the edit mesh), NaN where a particle is not in
    the group; None for a group that is gone or a mesh that is not the grid."""
    out = {}
    if obj.mode == "EDIT":
        import bmesh
        bm = bmesh.from_edit_mesh(obj.data)
        if len(bm.verts) != n:
            return {f: None for f in FIELD_NAMES}
        layer = bm.verts.layers.deform.active
        for f in FIELD_NAMES:
            g = obj.vertex_groups.get(group_name(f))
            if g is None:
                out[f] = None
            elif layer is None:
                out[f] = np.full(n, np.nan)
            else:
                out[f] = np.array([v[layer].get(g.index, np.nan) for v in bm.verts])
        return out
    me = obj.data
    if len(me.vertices) != n:
        return {f: None for f in FIELD_NAMES}
    index = {}
    for f in FIELD_NAMES:
        g = obj.vertex_groups.get(group_name(f))
        out[f] = None if g is None else np.full(n, np.nan)
        if g is not None:
            index[g.index] = f
    for v in me.vertices:
        for ge in v.groups:
            f = index.get(ge.group)
            if f is not None:
                out[f][v.index] = ge.weight
    return out


def values(obj, d: dict | None = None) -> dict:
    """The particle values as the groups now hold them (the canonical ones where a group or a particle's weight
    is missing, or where the weight is still the canonical value's: that value exactly, not its round trip through
    the range): what the display shows."""
    d = d or load(obj)
    n = count(d)
    out = {}
    for f, w in group_weights(obj, n).items():
        if w is None:
            out[f] = d[f]
            continue
        kept = np.isnan(w) | (np.float32(w) == mapping_weights(obj, f, d))
        out[f] = np.where(kept, d[f], to_value(obj, f, np.nan_to_num(w)))
    return out


def write_groups(obj, d: dict | None = None) -> None:
    """Restore exact cached masks, or initialize groups from canonical values for imported/new data."""
    d = d or load(obj)
    n = count(d)
    if obj.mode == "EDIT":
        import bmesh
        bm = bmesh.from_edit_mesh(obj.data)
        if len(bm.verts) != n:                # a structural edit is restored from canonical data on exit
            return
        layer = bm.verts.layers.deform.verify()
        for f in FIELD_NAMES:
            g = obj.vertex_groups.get(group_name(f)) or obj.vertex_groups.new(name=group_name(f))
            w = mapping_weights(obj, f, d)
            for v, weight in zip(bm.verts, w):
                v[layer][g.index] = float(weight)
        bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
        remember_mapping(obj)
        return
    for f in FIELD_NAMES:
        g = obj.vertex_groups.get(group_name(f)) or obj.vertex_groups.new(name=group_name(f))
        w = mapping_weights(obj, f, d)
        g.remove(list(range(n)))
        for value in np.unique(w):
            g.add([int(i) for i in np.where(w == value)[0]], float(value), "REPLACE")
    remember_mapping(obj)


def build_mesh(obj) -> None:
    """The mesh (rest pose, armature space), grid UV, row / column attributes and value groups from the canonical
    data. No bone groups, no Armature modifier: the mesh is data and stays in the rest pose (the display can
    follow the pose)."""
    d = load(obj)
    me = obj.data
    me.clear_geometry()
    for name in FIELD_NAMES + (ROW, COL):
        if name in me.attributes:
            me.attributes.remove(me.attributes[name])
    while me.uv_layers:
        me.uv_layers.remove(me.uv_layers[0])
    me.from_pydata(d["co"].tolist(), [], faces(d))
    uv = me.uv_layers.new(name=UV_LAYER)
    uv.data.foreach_set("uv", face_uvs(d).astype(np.float32).ravel())
    if not d["locked"]:
        if PARTICLE_ID in me.attributes:
            me.attributes.remove(me.attributes[PARTICLE_ID])
        me.attributes.new(PARTICLE_ID, "INT", "POINT").data.foreach_set("value", np.arange(count(d), dtype=np.int32))
        r, c = np.divmod(np.arange(d["rows"] * d["cols"], dtype=np.int32), d["cols"])
        me.attributes.new(ROW, "INT", "POINT").data.foreach_set("value", r)
        me.attributes.new(COL, "INT", "POINT").data.foreach_set("value", c)
    me.update()
    for mod in [m for m in obj.modifiers if m.type == "ARMATURE"]:
        obj.modifiers.remove(mod)
    keep = {group_name(f) for f in FIELD_NAMES}
    for g in [g for g in obj.vertex_groups if g.name not in keep]:
        obj.vertex_groups.remove(g)
    write_groups(obj, d)
    obj.vertex_groups.active_index = obj.vertex_groups[group_name(FIELD_NAMES[0])].index


def mismatch(obj, positions=True) -> str:
    """What differs between the mesh and the canonical data's structure ("" when nothing): vertices, faces, grid
    UV, a value group gone or a particle missing from one. Object mode only. Group weights are not compared:
    they are the values (sync takes them)."""
    d = load(obj)
    me = obj.data
    n = count(d)
    if len(me.vertices) != n:
        return f"{len(me.vertices)} vertices for {n} particles"
    co = np.empty(3 * n, np.float32)
    me.vertices.foreach_get("co", co)
    if positions and not np.array_equal(co, d["co"].ravel()):
        return "particles moved"
    if not d["locked"]:
        attr = me.attributes.get(PARTICLE_ID)
        if attr is None or attr.domain != "POINT" or attr.data_type != "INT":
            return "particle IDs removed"
        ids = np.empty(n, np.int32)
        attr.data.foreach_get("value", ids)
        if not np.array_equal(ids, np.arange(n)):
            return "particle IDs changed or reordered"
    fs = faces(d)
    corners = np.asarray([v for f in fs for v in f], np.int32)
    if len(me.loops) != len(corners) or len(me.polygons) != len(fs):
        return "faces changed"
    loops = np.empty(len(corners), np.int32)
    me.loops.foreach_get("vertex_index", loops)
    if not np.array_equal(loops, corners):
        return "faces changed"
    edges = {tuple(sorted((f[i], f[(i + 1) % len(f)]))) for f in fs for i in range(len(f))}
    if len(me.edges) != len(edges) or {tuple(sorted(e.vertices)) for e in me.edges} != edges:
        return "edges changed"
    uv = me.uv_layers.get(UV_LAYER)
    want = face_uvs(d).astype(np.float32).ravel()
    if uv is None:
        return "grid UV removed"
    got = np.empty(len(want), np.float32)
    uv.data.foreach_get("uv", got)
    if not np.allclose(got, want, atol=1e-6):
        return "grid UV moved"
    if any(m.type == "ARMATURE" for m in obj.modifiers):
        return "made by an older version"
    for f, w in group_weights(obj, n).items():
        if w is None:
            return f"group {group_name(f)} removed"
        if np.isnan(w).any():
            return f"{int(np.isnan(w).sum())} particles left group {group_name(f)}"
    return ""


def sync(obj) -> None:
    """The canonical values from the groups (the last good values, for a restore and the export)."""
    d = load(obj)
    c = obj[KEY]
    for f, v in values(obj, d).items():
        if not np.array_equal(v, d[f]):
            c[f] = [float(x) for x in v]
    remember_mapping(obj)


def set_positions(obj, co) -> None:
    """Commit rest coordinates; skin and output ownership stay attached to particle IDs."""
    d = load(obj)
    if d["locked"]:
        raise ValueError("Keep Structure Parts cannot edit rest positions")
    co = f32(co).reshape(count(d), 3)
    from .modules.gpuc_build import validate_geometry
    tris = [(f[0], f[k], f[k + 1]) for f in faces(d) for k in range(1, len(f) - 1)]
    validate_geometry(co, tris)
    c = obj[KEY]
    c["base_co"] = d["base_co"].ravel().tolist()
    c["rest_offset"] = (co - d["base_co"]).ravel().tolist()
    c["co"] = co.ravel().tolist()
    from . import sim
    sim.invalidate(obj)


_busy = False


def upgrade(obj) -> bool:
    """A Part made before a particle value was added: that value at its default, its group made (no notice)."""
    c = obj[KEY]
    if not c.get("locked") and "rest_offset" not in c:
        d = load(obj)
        c["base_co"] = d["co"].ravel().tolist()
        c["rest_offset"] = np.zeros_like(d["co"]).ravel().tolist()
        # Only migrate old meshes. A removed ID layer on an upgraded Part is an error.
        me = obj.data
        if len(me.vertices) == count(d) and PARTICLE_ID not in me.attributes:
            me.attributes.new(PARTICLE_ID, "INT", "POINT").data.foreach_set("value", np.arange(count(d), dtype=np.int32))
    missing = [f for f in FIELD_NAMES if f not in c]
    if not missing:
        return False
    d = load(obj)
    for f in missing:
        c[f] = [float(x) for x in d[f]]
    write_groups(obj)
    return True


def restore(obj) -> str:
    """Rebuild the mesh when its structure differs from the canonical data (object mode), else take the values
    the groups hold (object and weight paint mode); the reason for a rebuild, or ""."""
    global _busy
    if _busy or not has(obj) or obj.mode not in {"OBJECT", "WEIGHT_PAINT"}:
        return ""
    _busy = True
    try:
        upgrade(obj)
        why = mismatch(obj, positions=locked(obj)) if obj.mode == "OBJECT" else ""
        if not why and obj.mode == "OBJECT" and not locked(obj):
            co = np.array([tuple(v.co) for v in obj.data.vertices], np.float32)
            if not np.array_equal(co, load(obj)["co"]):
                try:
                    set_positions(obj, co)
                    obj["ree_gpuc_notice"] = "Rest positions saved; offsets are kept when regenerating"
                except ValueError as e:
                    why = str(e)
        if why:
            build_mesh(obj)
            obj["ree_gpuc_notice"] = f"Restored the particle mesh ({why}); only valid rest-position edits are accepted"
        else:
            sync(obj)
    finally:
        _busy = False
    return why


@bpy.app.handlers.persistent
def _on_depsgraph(scene, depsgraph):
    if _busy:
        return
    seen = set()
    for upd in depsgraph.updates:
        obj = upd.id.original
        if upd.is_updated_transform and isinstance(obj, bpy.types.Object) and obj.get("TYPE") == "RE_GPUC_PART":
            parent = obj.parent                       # dragged under another Config in the outliner
            if parent is not None and parent.get("TYPE") == "RE_GPUC_CONFIG" and obj.ree_gpuc_part.config != parent:
                obj.ree_gpuc_part.config = parent
        if not upd.is_updated_geometry:
            continue
        ids = [upd.id.original]
        if isinstance(ids[0], bpy.types.Mesh):
            ids = [o for o in scene.objects if o.data == ids[0]]
        for obj in ids:
            if isinstance(obj, bpy.types.Object) and obj.name not in seen and obj.get("TYPE") == "RE_GPUC_PART":
                seen.add(obj.name)
                restore(obj)


@bpy.app.handlers.persistent
def _initialize_mapping(*_):
    # Called after registration (not inside Blender's restricted add-on context), and after file load.
    for obj in bpy.data.objects:
        if has(obj) and MAPPING_KEY not in obj:
            fit_ranges(obj, load(obj))
            write_groups(obj)
    return None


def register():
    bpy.app.handlers.depsgraph_update_post.append(_on_depsgraph)
    bpy.app.handlers.load_post.append(_initialize_mapping)
    bpy.app.timers.register(_initialize_mapping, first_interval=0.0)


def unregister():
    if bpy.app.timers.is_registered(_initialize_mapping):
        bpy.app.timers.unregister(_initialize_mapping)
    if _initialize_mapping in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_initialize_mapping)
    if _on_depsgraph in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(_on_depsgraph)

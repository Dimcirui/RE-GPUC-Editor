"""Stores a Part's canonical data as an ID property on its object and keeps the Part's mesh consistent with it.

Maintenance constraints:
- The particle values live in the vertex groups ``gpuc_<field>``: weight 0-1 maps linearly onto the Part's range
  and is not normalised. ``sync`` copies them back whenever the mesh structure is intact.
- Every Part stores its mesh under ``KEY``: particles, triangles, UVs, links with their own tails, skin by bone
  name and normals. ``count``, ``faces``, ``face_uvs``, ``links`` and ``particle_uv`` read it, or a bone grid.
- A Part made from bones also stores its bone grid under ``RECIPE_KEY`` (row by row, row 0 at the top, the
  bones' rest heads in armature space; ``sub_rows`` / ``sub_cols`` particles between bones; 4 node indices of
  skin per particle). While ``ATTACHED_KEY`` is set it follows the grid: ``store`` of a grid writes the grid and
  its mesh (``_bake``), valid rest-position edits are committed as offsets from ``base_co`` (the faces keep their
  winding), and changes to topology, particle IDs or UV are undone by ``restore``. ``load`` then also holds the
  grid's ``GRID_KEYS``. ``locked``: a Part that does not follow a grid.
- A Part from a file, edited as a mesh (``structure.edit_as_mesh``) or made from a mesh never moves with bones;
  a position change on a Part that cannot be edited is undone.
- An editable Part also stores its faces (quads split at a-c, ``topology``), each of their triangles'
  tangent flags and each particle's index in the file (``src``, -1 for a new one); its mesh carries
  ``PARTICLE_ID``, one skin group per bone, deforming by ``SKIN_MODIFIER`` so that Blender's normalizing leaves
  the value groups out, and the flags (``TANGENT_FLAGS``, copied back by ``sync``). A change
  to its structure or skin is taken by ``structure.adopt`` instead of being undone."""
import bpy
import numpy as np

from .modules import gpuc_build, grid

KEY = "ree_gpuc_canon"
UV_LAYER = "grid"
ROW, COL = "gpuc_row", "gpuc_col"
PARTICLE_ID = "gpuc_particle_id"
SKIN_MODIFIER = "GPUC Skin"
# on an editable locked Part's faces: the tangent flags of each face's first triangle and of a quad's second
TANGENT_FLAGS = ("gpuc_tangent_flags_a", "gpuc_tangent_flags_b")
MAPPING_KEY = "ree_gpuc_value_mapping"


def mapping_weights(obj, field, data):
    """Group weights for ``field``: the cached painted mask while its range and values still match (exact even
    when Min == Max), else weights mapped from the values."""
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
    """Recomputes ``field``'s values from the painted mask after its range changed; returns whether they did."""
    state = obj.get(MAPPING_KEY, {}).get(field)
    if state is not None and tuple(state["range"]) == field_range(obj, field):
        return False  # An unchanged range keeps the stored values exact.
    d = load(obj)
    w = group_weights(obj, count(d))[field]
    if w is None or not np.isfinite(w).all():
        return False  # A mesh that no longer matches is left to ``restore``.
    obj[KEY][field] = to_value(obj, field, w).tolist()
    remember_mapping(obj, {field: w})
    from . import sim
    sim.invalidate(obj)
    return True


def fit_ranges(obj, values):
    """Widens each field's range to include the supplied values, leaving the values unchanged."""
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
    ("inverse_mass", "Inverse mass", 0x08, 17.267),         # wind response only
    ("space_limit_lower", "Space limit lower", 0x10, 2.0),  # inward along the normal (Config Info State's limit)
    ("space_limit_upper", "Space limit upper", 0x14, 0.225),
    ("backstop_radius", "Backstop radius", 0x18, 0.0),      # 0: no backstop
    ("backstop_offset", "Backstop offset", 0x1C, 0.0),
    # section 28's record, apart from the particle record (``gpuc_params.SEC28_VALUES``): effect unknown
    *((f"unkn_particle_{k}", f"Unkn Particle {k}", 4 * k, float(v))
      for k, v in enumerate(np.frombuffer(gpuc_build.SEC28, "<f4"))),
)
DEFAULTS = {f[0]: f[3] for f in PARTICLE_FIELDS}
FIELD_NAMES = tuple(f[0] for f in PARTICLE_FIELDS)


def f32(values) -> np.ndarray:
    return np.asarray(values, np.float32)


def has(obj) -> bool:
    return KEY in obj.keys()


GRID_KEYS = ("rows", "cols", "closed", "sub_rows", "sub_cols", "node_rows", "node_cols", "node_co", "base_co",
             "rest_offset", "centre", "diagonal_bend")


def load(obj) -> dict:
    """The Part's mesh: particles, faces, links, skin and values (``"attached"``: it follows its bone grid, whose
    ``GRID_KEYS`` it then also holds)."""
    out = _load_locked(obj[KEY])
    out["attached"] = attached(obj)
    if out["attached"]:
        g = recipe(obj)
        out.update({k: g[k] for k in GRID_KEYS})
    return out


def recipe(obj) -> dict:
    """The bone grid of a Part made from bones, with the values its mesh has while it follows the grid (else the
    ones it had when it was edited as a mesh)."""
    c = obj[RECIPE_KEY]
    vals = obj[KEY] if attached(obj) else c
    return _load_grid(c, obj, vals)


def _load_grid(c, obj, vals) -> dict:
    rows, cols = int(c["rows"]), int(c["cols"])
    co = f32(c["co"]).reshape(-1, 3)
    nodes, nr, nc = [str(b) for b in c["nodes"]], int(c["node_rows"]), int(c["node_cols"])
    sr, sc = int(c["sub_rows"]), int(c["sub_cols"])
    node_co = f32(c["node_co"]).reshape(-1, 3)
    skin, skin_w = np.asarray(c["skin"], np.int64).reshape(-1, 4), f32(c["skin_w"]).reshape(-1, 4)
    centre = f32(c["centre"]) if len(c["centre"]) else None
    base = f32(c.get("base_co", co)).reshape(-1, 3)
    offset = f32(c.get("rest_offset", np.zeros_like(co))).reshape(-1, 3)
    return {"locked": False, "count": rows * cols, "base_co": base, "rest_offset": offset,
            "rows": rows, "cols": cols, "closed": bool(c["closed"]), "tile": int(c["tile"]), "co": co,
            "nodes": nodes, "node_rows": nr, "node_cols": nc, "sub_rows": sr, "sub_cols": sc, "node_co": node_co,
            "skin": skin, "skin_w": skin_w, "centre": centre, "diagonal_bend": obj.ree_gpuc_part.diagonal_bend,
            **{n: f32(vals[n]) if n in vals else np.full(rows * cols, DEFAULTS[n], np.float32) for n in FIELD_NAMES}}


def _load_locked(c) -> dict:
    n = int(c["count"])
    links = np.asarray(c["links"], np.int64).reshape(-1, 4)
    out = {"locked": True, "count": n, "rows": 0, "cols": 0, "closed": False, "centre": None,
           "tile": int(c["tile"]), "co": f32(c["co"]).reshape(-1, 3),
           "tris": np.asarray(c["tris"], np.int64).reshape(-1, 3), "tri_uv": f32(c["tri_uv"]).reshape(-1, 3, 2),
           "links": links, "tails": f32(c["tails"]).reshape(-1, 4), "nodes": [str(b) for b in c["nodes"]],
           "skin": np.asarray(c["skin"], np.int64).reshape(n, -1), "skin_w": f32(c["skin_w"]).reshape(n, -1),
           "normal": f32(c["normal"]).reshape(-1, 3), "faces": None, "face_uv": None,
           "src": np.asarray(c["src"], np.int64) if "src" in c else np.arange(n),
           "editable": bool(c.get("editable")), "edited": bool(c.get("edited")),
           **{f: f32(c[f]) if f in c else np.full(n, DEFAULTS[f], np.float32) for f in FIELD_NAMES}}
    if "faces" in c:
        flat, sizes = [int(v) for v in c["faces"]], np.cumsum([0] + [int(k) for k in c["face_n"]])
        out["faces"] = [tuple(flat[a:b]) for a, b in zip(sizes[:-1], sizes[1:])]
        out["face_uv"] = f32(c["face_uv"]).reshape(-1, 2)
        n_tris = sum(len(f) - 2 for f in out["faces"])
        out["tri_flags"] = np.asarray(c["tri_flags"], np.int64) if "tri_flags" in c else np.zeros(n_tris, np.int64)
    return out


def store(obj, data: dict) -> None:
    fl = lambda a: [float(x) for x in f32(a).ravel()]        # noqa: E731
    if data.get("locked"):
        ints = lambda a: [int(x) for x in np.asarray(a).ravel()]  # noqa: E731
        obj[KEY] = {"locked": 1, "count": int(data["count"]), "tile": int(data["tile"]), "co": fl(data["co"]),
                    "tris": ints(data["tris"]), "tri_uv": fl(data["tri_uv"]), "links": ints(data["links"]),
                    "tails": fl(data["tails"]), "nodes": list(data["nodes"]), "skin": ints(data["skin"]),
                    "skin_w": fl(data["skin_w"]), "normal": fl(data["normal"]),
                    **{n: fl(data[n]) for n in FIELD_NAMES}}
        c = obj[KEY]
        if data.get("faces") is not None:
            c["faces"] = [int(v) for f in data["faces"] for v in f]
            c["face_n"] = [len(f) for f in data["faces"]]
            c["face_uv"] = fl(data["face_uv"])
            c["src"] = ints(data.get("src", np.arange(int(data["count"]))))
            if data.get("tri_flags") is not None:
                c["tri_flags"] = ints(data["tri_flags"])
        for flag in ("editable", "edited"):
            if data.get(flag):
                c[flag] = 1
        return
    base = f32(data.get("base_co", data["co"]))
    offset = f32(data.get("rest_offset", np.zeros_like(base)))
    obj[RECIPE_KEY] = {"base_co": fl(base), "rest_offset": fl(offset),
                       "rows": int(data["rows"]), "cols": int(data["cols"]), "closed": int(bool(data["closed"])),
                       "tile": int(data["tile"]), "co": fl(base + offset), "nodes": list(data["nodes"]),
                       "node_rows": int(data["node_rows"]), "node_cols": int(data["node_cols"]),
                       "sub_rows": int(data["sub_rows"]), "sub_cols": int(data["sub_cols"]),
                       "node_co": fl(data["node_co"]), "skin": [int(x) for x in np.asarray(data["skin"]).ravel()],
                       "skin_w": fl(data["skin_w"]), "centre": [] if data["centre"] is None else fl(data["centre"]),
                       **{n: fl(data[n]) for n in FIELD_NAMES}}
    obj[ATTACHED_KEY] = 1
    _bake(obj, _load_grid(obj[RECIPE_KEY], obj, obj[RECIPE_KEY]))


def _bake(obj, g) -> None:
    """Write a bone grid as the Part's mesh, as the export writes it: faces wound outward and split at Other
    Diagonal, UVs on the unorm16 steps, the grid's skin, its links with their tails (``OVERRIDES_KEY`` kept per
    link), new-triangle tangent flags."""
    from . import fileio
    from .modules import topology
    faces, corners = fileio.grid_faces(obj, g, obj.ree_gpuc_part.armature)
    corners = [[(round(u * 65535) / 65535, round(v * 65535) / 65535) for u, v in f] for f in corners]
    face_uv = f32([p for f in corners for p in f]).reshape(-1, 2)
    tris, tri_uv = topology.triangles(faces, face_uv)
    co = g["co"]
    own = overrides(obj, g)
    file_grid = obj.get(FILE_GRID_KEY)                 # another density: no particle is the file's any more
    same = file_grid is None or list(file_grid) == [g["rows"], g["cols"], int(g["closed"])]
    src = np.arange(count(g)) if same else np.full(count(g), -1)
    obj[EDITED_KEY] = [n for k, n in enumerate(s.lower() for s in grid.SHAPES) if k not in own]
    store(obj, {"locked": True, "count": count(g), "tile": g["tile"], "co": co, "tris": tris, "tri_uv": tri_uv,
                "links": links(g), "tails": link_tails(obj, g), "nodes": g["nodes"], "skin": g["skin"],
                "skin_w": g["skin_w"], "normal": topology.normals(co, tris), "faces": faces, "face_uv": face_uv,
                "src": src, "tri_flags": gpuc_build.tangent_flags(faces, corners), "editable": True,
                **{n: g[n] for n in FIELD_NAMES}})


def rebake(obj) -> None:
    """Write a Part that follows its bone grid again from the grid (after a setting the mesh depends on changed)."""
    if attached(obj):
        store(obj, recipe(obj))
        if obj.mode == "OBJECT":
            build_mesh(obj)
        from . import sim
        sim.invalidate(obj)


def locked(obj) -> bool:
    """Whether a Part keeps its mesh as it is: it does not follow a bone grid."""
    return has(obj) and bool(obj[KEY].get("locked")) and ATTACHED_KEY not in obj.keys()


RECIPE_KEY = "ree_gpuc_recipe"           # on a Part made from bones: its bone grid's data
ATTACHED_KEY = "ree_gpuc_attached"       # on a Part made from bones: it follows its bone grid


def attached(obj) -> bool:
    """Whether a Part follows its bone grid: made from bones and not edited as a mesh."""
    return has(obj) and ATTACHED_KEY in obj.keys()


FILE_GRID_KEY = "ree_gpuc_file_grid"   # on a file Part attached to bones: (rows, cols, closed) of its file grid
MESH_KEY = "ree_gpuc_from_mesh"          # on a Part made from a mesh (``structure.from_mesh``)
FILE_KEY = "ree_gpuc_file_part"          # on a Part imported from its file: its part's index in the file


def file_part(obj) -> bool:
    """Whether a Part is one of its imported cloth's file (also when it was attached to bones since)."""
    return has(obj) and FILE_KEY in obj.keys()


def made_here(obj) -> bool:
    """Whether a Part was made in the editor, from bones or a mesh, rather than imported from a file."""
    return has(obj) and not file_part(obj) and (RECIPE_KEY in obj.keys() or MESH_KEY in obj.keys())


def from_bones(obj) -> bool:
    """Whether a Part was made from bones and edited as a mesh (``structure.edit_as_mesh``)."""
    return has(obj) and RECIPE_KEY in obj.keys() and ATTACHED_KEY not in obj.keys()


def editable_part(obj) -> bool:
    """``editable`` without loading the Part (the panel asks on every redraw)."""
    return locked(obj) and bool(obj[KEY].get("editable")) and "faces" in obj[KEY] and not attached(obj)


def edited(obj) -> bool:
    """Whether a locked Part's structure was changed (``structure.adopt``), so its cloth exports rebuilt."""
    return locked(obj) and bool(obj[KEY].get("edited"))


def count(d: dict) -> int:
    return d["count"]


def faces(d: dict) -> list:
    """The mesh's faces: the grid's quads, or a locked Part's faces (else its triangles)."""
    if d["locked"]:
        if d.get("faces") is not None:
            return list(d["faces"])
        return [tuple(int(v) for v in t) for t in d["tris"]]
    return grid.faces(d["rows"], d["cols"], d["closed"])


def editable(d: dict) -> bool:
    """Whether a change to the Part's mesh structure is taken as its new structure (``structure.adopt``)."""
    return d["locked"] and d.get("editable", False) and d.get("faces") is not None and not d.get("attached")


def face_uvs(d: dict) -> np.ndarray:
    """Each face corner's UV, on the Part's UV tile (u + tile)."""
    if d["locked"]:
        if d.get("faces") is not None:
            return d["face_uv"] + (d["tile"], 0.0)
        return d["tri_uv"].reshape(-1, 2) + (d["tile"], 0.0)
    return np.asarray(grid.face_uv(d["rows"], d["cols"], d["closed"], d["tile"]), np.float32).reshape(-1, 2)


_grid_links: dict = {}


def links(d: dict) -> np.ndarray:
    """Every link as (a, b, type, shape index into ``grid.SHAPES``)."""
    if d["locked"]:
        return d["links"]
    key = (d["rows"], d["cols"], d["closed"], d.get("diagonal_bend", True))
    if key not in _grid_links:
        _grid_links[key] = grid.links(*key)
    return _grid_links[key]


EDITED_KEY = "ree_gpuc_tails_edited"       # on a locked Part: the link shapes whose tail was set by hand
# on a Part made from bones: links whose tail is not their shape's, kept from a rebuilt file:
# {"grid": [rows, cols, closed, diagonal bend], shape: {"links": [index into links()], "tails": [k, k2, lo, hi, ...]}};
# setting a shape's tail drops its entry, a grid of other links ignores them all
OVERRIDES_KEY = "ree_gpuc_link_overrides"


def _grid_key(d: dict) -> list:
    return [int(d["rows"]), int(d["cols"]), int(bool(d["closed"])), int(bool(d.get("diagonal_bend", True)))]


def set_overrides(obj, d: dict, tails, default) -> int:
    """Keep each link's tail that is not its shape's ``default`` (one per shape) as an override; return how many."""
    ln, out = links(d), {"grid": _grid_key(d)}
    tails, default = np.asarray(tails, np.float32), np.asarray(default, np.float32)
    for k, n in enumerate(s.lower() for s in grid.SHAPES):
        own = np.where((ln[:, 3] == k) & (np.abs(tails - default[k]).max(axis=1) > 1e-6))[0]
        if len(own):
            out[n] = {"links": own.tolist(), "tails": tails[own].ravel().tolist()}
    if len(out) > 1:
        obj[OVERRIDES_KEY] = out
    elif OVERRIDES_KEY in obj.keys():
        del obj[OVERRIDES_KEY]
    return sum(len(v["links"]) for n, v in out.items() if n != "grid")


def overrides(obj, d: dict) -> dict:
    """{shape index: (link indices, tails)} of the overrides that apply to the Part's current grid."""
    c = obj.get(OVERRIDES_KEY)
    if d["locked"] or c is None or list(c.get("grid", ())) != _grid_key(d):
        return {}
    names = [s.lower() for s in grid.SHAPES]
    return {names.index(n): (np.asarray(v["links"], np.int64), f32(v["tails"]).reshape(-1, 4))
            for n, v in c.items() if n in names}


def link_tails(obj, d: dict) -> np.ndarray:
    """Each link's tail (k, k2, lo, hi) from its shape's settings on the Part, a Part made from bones taking its
    ``OVERRIDES_KEY`` links' own; a locked Part's links keep the file's tails except for the shapes listed under
    ``EDITED_KEY``."""
    part = obj.ree_gpuc_part
    names = [s.lower() for s in grid.SHAPES]
    shape = np.array([getattr(part, n).tail() for n in names], np.float32)
    ln = links(d)
    if not d["locked"]:
        out = shape[ln[:, 3]]
        for idx, tails in overrides(obj, d).values():
            out[idx] = tails
        return out
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
    """Writes ``value`` at ``index`` into the canonical data; the caller updates the mesh's group."""
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
    """The particle values the groups now hold, as the display shows them. A particle keeps its exact canonical
    value where its group or weight is missing or the weight still equals ``mapping_weights``."""
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
    """Writes every value group from ``mapping_weights``, creating missing groups; in edit mode, skipped when
    the vertex count differs."""
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
    """Rebuilds the mesh (rest pose, armature space), grid UV, attributes and value groups from the canonical
    data. Bone groups are written for an editable Part only, which keeps its Armature modifier (``keep_modifier``);
    any other Part loses both and stays in the rest pose."""
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
    if d.get("attached") or editable(d):
        if PARTICLE_ID in me.attributes:
            me.attributes.remove(me.attributes[PARTICLE_ID])
        me.attributes.new(PARTICLE_ID, "INT", "POINT").data.foreach_set("value", np.arange(count(d), dtype=np.int32))
    if d.get("attached"):
        cols = recipe(obj)["cols"]
        r, c = np.divmod(np.arange(count(d), dtype=np.int32), cols)
        me.attributes.new(ROW, "INT", "POINT").data.foreach_set("value", r)
        me.attributes.new(COL, "INT", "POINT").data.foreach_set("value", c)
    me.update()
    keep_modifier(obj, d)
    keep = {group_name(f) for f in FIELD_NAMES}
    for g in [g for g in obj.vertex_groups if g.name not in keep]:
        obj.vertex_groups.remove(g)
    if editable(d):
        write_tangent_flags(obj, d)
        dense = skin_dense(d)
        for k, name in enumerate(d["nodes"]):
            g = obj.vertex_groups.new(name=name)
            for value in np.unique(dense[:, k]):
                if value > 0:
                    g.add([int(i) for i in np.flatnonzero(dense[:, k] == value)], float(value), "REPLACE")
    write_groups(obj, d)
    obj.vertex_groups.active_index = obj.vertex_groups[group_name(FIELD_NAMES[0])].index


def skin_bones(d) -> list:
    """The bones a Part is skinned to: a Part made from bones its every node bone, a locked Part those with a
    weight."""
    if not d["locked"]:
        return list(d["nodes"])
    used = set(np.unique(d["skin"][d["skin_w"] > 0]).tolist())
    return [n for k, n in enumerate(d["nodes"]) if k in used]


def driven_names(obj, d) -> list:
    """The bones the Part drives (its Driven Bones setting): its skin bones, or the bones listed."""
    p = obj.ree_gpuc_part
    if p.driven_bones == "LISTED":
        return list(dict.fromkeys(b.name for b in p.driven_list if b.name))
    return skin_bones(d)


def skin_armature(obj):
    """The armature an editable Part's skin groups deform by (``SKIN_MODIFIER``), None without one."""
    arm = obj.ree_gpuc_part.armature
    return arm if arm is not None and arm.type == "ARMATURE" else None


def keep_modifier(obj, d) -> None:
    """Give an editable Part one Armature modifier on its armature, enabled in the viewport, which keeps Blender's
    normalizing to the bone groups and leaves the value groups out; remove any other Armature modifier."""
    arm = skin_armature(obj) if editable(d) else None
    for mod in [m for m in obj.modifiers if m.type == "ARMATURE" and (arm is None or m.name != SKIN_MODIFIER)]:
        obj.modifiers.remove(mod)
    if arm is None:
        return
    mod = obj.modifiers.get(SKIN_MODIFIER) or obj.modifiers.new(SKIN_MODIFIER, "ARMATURE")
    if mod.object != arm:
        mod.object = arm
    for name, value in (("use_vertex_groups", True), ("use_bone_envelopes", False), ("show_viewport", True),
                        ("show_render", True), ("show_in_editmode", False), ("show_on_cage", False)):
        if getattr(mod, name) != value:
            setattr(mod, name, value)


def write_tangent_flags(obj, d) -> None:
    """Put an editable Part's triangles' tangent flags on its faces (``TANGENT_FLAGS``)."""
    me = obj.data
    flags = iter(int(x) for x in d["tri_flags"])
    first, second = [], []
    for f in d["faces"]:
        first.append(next(flags, 0))
        second.append(next(flags, 0) if len(f) == 4 else 0)
    for name, values in zip(TANGENT_FLAGS, (first, second)):
        if name in me.attributes:
            me.attributes.remove(me.attributes[name])
        me.attributes.new(name, "INT", "FACE").data.foreach_set("value", np.asarray(values, np.int32))


def read_tangent_flags(obj, d) -> list | None:
    """The triangles' tangent flags the faces hold, clamped to u16; None when an attribute is missing."""
    me = obj.data
    got = []
    for name in TANGENT_FLAGS:
        attr = me.attributes.get(name)
        if attr is None or attr.domain != "FACE" or attr.data_type != "INT" or len(attr.data) != len(d["faces"]):
            return None
        values = np.empty(len(d["faces"]), np.int32)
        attr.data.foreach_get("value", values)
        got.append(np.clip(values, 0, 0xFFFF).tolist())
    out = []
    for f, a, b in zip(d["faces"], *got):
        out += [a, b] if len(f) == 4 else [a]
    return out


def skin_dense(d) -> np.ndarray:
    """A locked Part's skin as one weight per particle and bone of ``d["nodes"]``."""
    n = count(d)
    out = np.zeros((n, len(d["nodes"])), np.float32)
    for k in range(d["skin"].shape[1]):
        np.add.at(out, (np.arange(n), d["skin"][:, k]), d["skin_w"][:, k])
    return out


def skin_group_names(obj) -> list:
    """The names of the mesh's vertex groups that are not value groups: its skin groups."""
    values_ = {group_name(f) for f in FIELD_NAMES}
    return [g.name for g in obj.vertex_groups if g.name not in values_]


def skin_groups(obj) -> tuple:
    """(names, weights: vertices x names) of the mesh's vertex groups that are not value groups; object mode."""
    names = set(skin_group_names(obj))
    groups = [g for g in obj.vertex_groups if g.name in names]
    col = {g.index: k for k, g in enumerate(groups)}
    out = np.zeros((len(obj.data.vertices), len(groups)), np.float32)
    for v in obj.data.vertices:
        for ge in v.groups:
            k = col.get(ge.group)
            if k is not None:
                out[v.index, k] = ge.weight
    return [g.name for g in groups], out


def mismatch(obj, positions=True) -> str:
    """The first structural difference between the mesh and the canonical data, "" when none. Object mode only;
    group weights are values and are not compared."""
    d = load(obj)
    me = obj.data
    n = count(d)
    if len(me.vertices) != n:
        return f"{len(me.vertices)} vertices for {n} particles"
    co = np.empty(3 * n, np.float32)
    me.vertices.foreach_get("co", co)
    if positions and not np.array_equal(co, d["co"].ravel()):
        return "particles moved"
    if d.get("attached"):
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
    if not editable(d) and any(m.type == "ARMATURE" for m in obj.modifiers):
        return "made by an older version"
    for f, w in group_weights(obj, n).items():
        if w is None:
            return f"group {group_name(f)} removed"
        if np.isnan(w).any():
            return f"{int(np.isnan(w).sum())} particles left group {group_name(f)}"
    if editable(d):
        names, w = skin_groups(obj)
        have = skin_dense(d)
        mine = np.zeros((n, len(names)), np.float32)
        for k, name in enumerate(d["nodes"]):
            if name in names:
                mine[:, names.index(name)] = have[:, k]
            elif have[:, k].any():
                return f"skin group {name} removed"
        if not np.allclose(mine, w, atol=0.5 / 255):
            return "skin weights changed"
    return ""


def sync(obj) -> None:
    """Copies the groups' values into the canonical data as the last good values for ``restore`` and export."""
    d = load(obj)
    c = obj[KEY]
    for f, v in values(obj, d).items():
        if not np.array_equal(v, d[f]):
            c[f] = [float(x) for x in v]
    if editable(d):
        keep_modifier(obj, d)
        flags = read_tangent_flags(obj, d)
        if flags is None:
            write_tangent_flags(obj, d)
        elif flags != [int(x) for x in d["tri_flags"]]:
            c["tri_flags"] = flags
            write_tangent_flags(obj, load(obj))    # shows clamped values as written
    remember_mapping(obj)


def set_positions(obj, co) -> None:
    """Commits rest positions as offsets from the bone grid's ``base_co``; raises ``ValueError`` for a Part that does
    not follow its bones, or invalid geometry."""
    if not attached(obj):
        raise ValueError("only a Part that follows its bones takes rest positions as offsets")
    d = load(obj)
    co = f32(co).reshape(count(d), 3)
    from .modules.gpuc_build import validate_geometry
    validate_geometry(co, d["tris"])
    from .modules import topology
    r, c = obj[RECIPE_KEY], obj[KEY]
    r["rest_offset"] = (co - f32(r["base_co"]).reshape(-1, 3)).ravel().tolist()
    r["co"] = c["co"] = co.ravel().tolist()     # the faces keep the winding they were made with
    c["normal"] = topology.normals(co, d["tris"]).ravel().tolist()
    from . import sim
    sim.invalidate(obj)


_busy = False


def upgrade(obj) -> bool:
    """Adds the particle values a Part from an earlier 1.x lacks (an imported Part takes its file's, others
    the defaults); returns whether values were added."""
    c = obj[KEY]
    missing = [f for f in FIELD_NAMES if f not in c]
    if not missing:
        return False
    d = load(obj)
    kept = {}
    if c.get("locked"):                       # an imported Part takes the values its file holds
        from . import fileio
        kept = fileio.stored_part_values(obj, missing)
    for f in missing:
        c[f] = [float(x) for x in kept.get(f, d[f])]
    write_groups(obj)
    return True


def restore(obj) -> str:
    """Rebuilds the mesh when its structure differs from the canonical data (object mode), else commits rest
    positions and syncs the values (object and weight paint mode); returns the rebuild reason, or ""."""
    global _busy
    if _busy or not has(obj) or obj.mode not in {"OBJECT", "WEIGHT_PAINT"}:
        return ""
    _busy = True
    try:
        upgrade(obj)
        why = mismatch(obj, positions=not attached(obj)) if obj.mode == "OBJECT" else ""
        if not why and obj.mode == "OBJECT" and attached(obj):
            co = np.array([tuple(v.co) for v in obj.data.vertices], np.float32)
            if not np.array_equal(co, load(obj)["co"]):
                try:
                    set_positions(obj, co)
                    obj["ree_gpuc_notice"] = "Rest positions saved; offsets are kept when regenerating"
                except ValueError as e:
                    why = str(e)
        if why and editable(load(obj)):
            from . import structure
            try:
                obj["ree_gpuc_notice"] = structure.adopt(obj)
                why = ""
            except ValueError as e:
                build_mesh(obj)
                obj["ree_gpuc_notice"] = f"Restored the particle mesh: {e}"
        elif why:
            build_mesh(obj)
            obj["ree_gpuc_notice"] = f"Restored the particle mesh ({why}); only valid rest-position edits are accepted"
        else:
            sync(obj)
    finally:
        _busy = False
    return why


def _same_shape(obj) -> bool:
    """Whether the mesh still has the stored particles at their places and as many corners: a quick check for an
    update its armature may have caused alone."""
    me, c = obj.data, obj[KEY]
    co = np.empty(3 * len(me.vertices), np.float32)
    me.vertices.foreach_get("co", co)
    return len(co) == len(c["co"]) and np.array_equal(co, f32(c["co"])) and len(me.loops) == len(c["faces"])


@bpy.app.handlers.persistent
def _on_depsgraph(scene, depsgraph):
    if _busy:
        return
    seen = set()
    # a posed or moved armature deforms its Parts' meshes (SKIN_MODIFIER) without changing them: skipped when the
    # mesh looks the same
    posed = {u.id.original.name for u in depsgraph.updates if isinstance(u.id.original, bpy.types.Object)
             and u.id.original.type == "ARMATURE"}
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
                mod = obj.modifiers.get(SKIN_MODIFIER)
                if mod is not None and mod.object is not None and mod.object.name in posed and _same_shape(obj):
                    continue
                restore(obj)


@bpy.app.handlers.persistent
def _initialize_mapping(*_):
    # Called after registration (not inside Blender's restricted add-on context), and after file load.
    for obj in bpy.data.objects:
        if has(obj) and MAPPING_KEY not in obj:
            fit_ranges(obj, load(obj))
            write_groups(obj)
        if locked(obj) and editable_part(obj):
            keep_modifier(obj, load(obj))
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

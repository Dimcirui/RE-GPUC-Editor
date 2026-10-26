"""Generates a Part's particle grid from bone chains in the armature's rest pose, checks it against the armature
and regenerates it.

Each chain is one column of the bone grid, top to bottom, ordered round the cloth by ``grid.order_columns``. A
particle sits on every bone's head, with ``sub_rows`` / ``sub_cols`` more between neighbouring bones, each skinned
to the four bones round it. Regenerating keeps every Config, Part and link-tail value and resamples each
particle's values from the old grid.

Maintenance constraints:
- The hierarchy and names follow the RE Chain Editor's: the purple <name>.gpuc collection holds
  ``GPUC_HEADER <collection>`` on the armature, its Configs (``GPUC_CONFIG_00``, ...) and under each its Parts
  (``GPUC_PART_00_<bone>``, ...).
- These objects sit at no offset: a Part's mesh is in armature space."""
import bpy
import numpy as np

from . import canon, props
from .modules import grid

COLLECTION_COLOUR = "COLOR_06"          # purple; the chain editor's chain and clsp collections are orange
# a new part's link tails (props.LINK_PRESETS)
LINK_PRESET = "SKIRT_1005"


class GenerateError(ValueError):
    pass


def _selected(arm) -> set:
    pbs = arm.pose.bones
    return {pb.name for pb in pbs if getattr(pb, "select", False) or getattr(pb.bone, "select", False)}


def chains_from_selected(arm) -> list[list[str]]:
    """The selected bones as chains, each top to bottom (a bone and its selected child, and so on)."""
    sel = _selected(arm)
    bones = arm.data.bones
    tops = [n for n in sel if bones[n].parent is None or bones[n].parent.name not in sel]
    out = []
    for top in sorted(tops):
        chain = [top]
        while True:
            kids = [c.name for c in bones[chain[-1]].children if c.name in sel]
            if not kids:
                break
            if len(kids) > 1:
                raise GenerateError(f"{chain[-1]} has {len(kids)} selected children ({', '.join(kids[:3])}): "
                                    "a column must not branch")
            chain.append(kids[0])
        out.append(chain)
    return out


def chains_from_parents(arm) -> list[list[str]]:
    """Each selected bone and every bone below it as a chain, refused unless each has one child at most and no
    selected bone is below another."""
    sel = _selected(arm)
    bones = arm.data.bones
    out = []
    for top in sorted(sel):
        above = [p.name for p in bones[top].parent_recursive if p.name in sel]
        if above:
            raise GenerateError(f"{top} is below the selected {above[0]}: select only the first bone of each chain")
        chain = [top]
        while bones[chain[-1]].children:
            kids = bones[chain[-1]].children
            if len(kids) > 1:
                raise GenerateError(f"{chain[-1]} has {len(kids)} children ({', '.join(k.name for k in kids[:3])}): "
                                    "a column must not branch")
            chain.append(kids[0].name)
        out.append(chain)
    return out


def heads(arm, names) -> np.ndarray:
    """The bones' rest heads in armature space."""
    bones = arm.data.bones
    return np.array([tuple(bones[n].head_local) for n in names], np.float32).reshape(-1, 3)


def layout(arm, cols, closed="AUTO", truncate=False, sub_rows=0, sub_cols=0) -> dict:
    """The bone grid the chains make and the particle grid on it."""
    if len(cols) < 2:
        raise GenerateError(f"a part needs at least two chains (the columns); {len(cols)} selected")
    lengths = sorted({len(c) for c in cols})
    if len(lengths) > 1 and not truncate:
        raise GenerateError(f"the chains have {', '.join(map(str, lengths))} bones: use chains of the same length, "
                            "or turn on Cut to the shortest chain")
    nr = lengths[0]
    if nr < 2:
        raise GenerateError("the chains need at least two bones each (the rows)")
    cols = [c[:nr] for c in cols]
    order, is_closed, centre = grid.order_columns(heads(arm, [c[0] for c in cols]), closed)
    cols = [cols[k] for k in order]
    nodes = [cols[c][r] for r in range(nr) for c in range(len(cols))]
    lay = {"node_rows": nr, "node_cols": len(cols), "sub_rows": sub_rows, "sub_cols": sub_cols,
           "closed": is_closed, "centre": centre, "nodes": nodes}
    return {**lay, **_particles(lay, heads(arm, nodes))}


def _particles(lay: dict, node_co) -> dict:
    nr, nc = lay["node_rows"], lay["node_cols"]
    pos, skin, weight = grid.subdivide(np.asarray(node_co, float).reshape(nr, nc, 3), lay["sub_rows"],
                                       lay["sub_cols"], lay["closed"], lay["centre"])
    rows, cols = grid.dims(nr, nc, lay["sub_rows"], lay["sub_cols"], lay["closed"])
    return {"rows": rows, "cols": cols, "node_co": node_co, "co": pos, "skin": skin, "skin_w": weight}


def unique_name(prefix: str, suffix: str = "") -> str:
    """``prefix`` + the first two-digit index no object name uses after ``prefix`` (+ ``suffix``)."""
    taken = {o.name[len(prefix):len(prefix) + 2] for o in bpy.data.objects if o.name.startswith(prefix)}
    k = 0
    while f"{k:02d}" in taken:
        k += 1
    return f"{prefix}{k:02d}{suffix}"


def new_collection(name: str, arm=None, game: str = "") -> bpy.types.Collection:
    """Creates the cloth: a purple <name>.gpuc collection with its header (on the armature) and one Config,
    inside the collection named <name> if there is one, else in the scene's. A non-empty ``game`` (a
    ``props.GAMES`` extension) sets the header's game and applies its ``props.GAME_DEFAULTS``."""
    base = name[:-len(".gpuc")] if name.endswith(".gpuc") else name
    coll = bpy.data.collections.new(base + ".gpuc")
    coll["~TYPE"] = props.COLLECTION_TYPE
    coll["~GPUC_SCHEMA"] = props.SCHEMA
    coll.color_tag = COLLECTION_COLOUR
    (bpy.data.collections.get(base) or bpy.context.scene.collection).children.link(coll)
    head = new_header(coll, arm)
    if game:
        h = head.ree_gpuc_header
        h.game = game
        for field, value in props.GAME_DEFAULTS.get(game, {}).items():
            setattr(h, field, value)
    new_config(coll, arm)
    return coll


def new_header(coll, arm=None) -> bpy.types.Object:
    obj = bpy.data.objects.new(f"GPUC_HEADER {coll.name}", None)
    obj["TYPE"] = props.HEADER_TYPE
    obj.empty_display_type, obj.empty_display_size = "CUBE", 0.03
    obj.hide_render = True
    coll.objects.link(obj)
    if arm is not None:
        props.place_under(obj, arm)
    return obj


def _armature(coll):
    h = props.header(coll)
    if h is not None and h.parent is not None and h.parent.type == "ARMATURE":
        return h.parent
    return next((p.ree_gpuc_part.armature for p in props.parts(coll) if p.ree_gpuc_part.armature), None)


def new_config(coll, arm=None) -> bpy.types.Object:
    """A Config under the cloth's header (or on the armature), made the current one (new parts go under it)."""
    arm = arm or _armature(coll)
    obj = bpy.data.objects.new(unique_name("GPUC_CONFIG_"), None)
    obj["TYPE"] = props.CONFIG_TYPE
    obj.empty_display_type, obj.empty_display_size = "PLAIN_AXES", 0.05
    obj.hide_render = True
    props.fill_curves(obj.ree_gpuc_config)
    coll.objects.link(obj)
    h = props.header(coll)
    if h is not None:
        props.place_under(obj, h)
    elif arm is not None:
        props.place_under(obj, arm)
    s = bpy.context.scene.ree_gpuc
    if s.target in (None, coll):
        s.config = obj
    return obj


def new_part(coll, arm, lay: dict, config=None) -> bpy.types.Object:
    """A part object under the Config (the current one, else the cloth's first, else a new one), from a layout
    (layout()), with default values."""
    existing = props.parts(coll)
    tile = max((p.ree_gpuc_part.uv_tile for p in existing), default=-1) + 1
    first = lay["nodes"][0]
    name = unique_name("GPUC_PART_", "_" + first.rsplit("_", 1)[0])
    obj = bpy.data.objects.new(name, bpy.data.meshes.new(name))
    obj["TYPE"] = props.PART_TYPE
    coll.objects.link(obj)
    obj.hide_render = True
    obj.show_wire = True
    obj.show_in_front = bpy.context.scene.ree_gpuc.in_front
    if props.header(coll) is None:
        new_header(coll, arm)
    config = config if props.is_config(config) else (props.configs(coll) or [None])[0]
    if config is None:
        config = new_config(coll, arm)
    if config.parent is None:
        props.place_under(config, props.header(coll) or arm)
    p = obj.ree_gpuc_part
    p.armature, p.rows, p.cols, p.closed, p.uv_tile = arm, lay["rows"], lay["cols"], lay["closed"], tile
    p.sub_rows, p.sub_cols = lay["sub_rows"], lay["sub_cols"]
    p.part_name = f"_p{len(existing):03d}"
    parent = arm.data.bones[first].parent
    p.root_joint = parent.name if parent is not None else first
    p.config = config                                   # and under it
    p.link_preset = LINK_PRESET
    n = lay["rows"] * lay["cols"]
    values = {name: np.full(n, default, np.float32) for name, _, _, default in canon.PARTICLE_FIELDS}
    values["sim_weight"][:lay["cols"]] = 0.0          # the top row follows the skinning (pinned)
    canon.store(obj, {**lay, "tile": tile, **values})
    canon.build_mesh(obj)
    return obj


def check(obj) -> list[str]:
    """What no longer matches the armature: missing bones, or bones whose rest head moved (a locked Part:
    ``fileio.locked_problems``)."""
    if canon.locked(obj):
        from . import fileio
        return fileio.locked_problems(obj)
    d = canon.load(obj)
    arm = obj.ree_gpuc_part.armature
    if arm is None or arm.type != "ARMATURE":
        return ["the part has no armature"]
    missing = [b for b in d["nodes"] if b not in arm.data.bones]
    if missing:
        return [f"{len(missing)} bones are gone: {', '.join(missing[:4])}"]
    moved = np.linalg.norm(heads(arm, d["nodes"]) - d["node_co"], axis=1)
    far = np.where(moved > 1e-5)[0]
    if len(far):
        return [f"{len(far)} bones moved off the rest heads the part was made from (up to {moved.max() * 100:.2f} cm)"]
    return []


def resample(values, old: dict, new: dict) -> np.ndarray:
    """A particle value carried from the old grid to the new one: each new particle takes the old grid's value at
    its place on the bone grid (bilinear; round the ring when the old grid closed)."""
    values = np.asarray(values, float)
    trailing = values.shape[1:]
    v = values.reshape(old["rows"], old["cols"], *trailing)
    r_new, c_new = np.divmod(np.arange(new["rows"] * new["cols"]), new["cols"])
    r = r_new / (new["sub_rows"] + 1) * (old["sub_rows"] + 1)            # in old particle rows
    c = c_new / (new["sub_cols"] + 1) * (old["sub_cols"] + 1)            # in old particle columns
    r0 = np.clip(np.floor(r).astype(int), 0, old["rows"] - 1)
    r1 = np.minimum(r0 + 1, old["rows"] - 1)
    fr = np.clip(r - r0, 0.0, 1.0)
    if old["closed"]:
        c0 = np.floor(c).astype(int) % old["cols"]
        c1 = (c0 + 1) % old["cols"]
        fc = c - np.floor(c)
    else:
        c0 = np.clip(np.floor(c).astype(int), 0, old["cols"] - 1)
        c1 = np.minimum(c0 + 1, old["cols"] - 1)
        fc = np.clip(c - c0, 0.0, 1.0)
        if new["closed"]:
            seam = c > old["cols"] - 1
            c0[seam], c1[seam] = old["cols"] - 1, 0
            fc[seam] = (c[seam] - (old["cols"] - 1)) / (old["sub_cols"] + 1)
    fr = fr.reshape((-1,) + (1,) * len(trailing))
    fc = fc.reshape((-1,) + (1,) * len(trailing))
    top = v[r0, c0] * (1 - fc) + v[r0, c1] * fc
    bottom = v[r1, c0] * (1 - fc) + v[r1, c1] * fc
    return (top * (1 - fr) + bottom * fr).astype(np.float32)


def regenerate(obj, closed=None, sub_rows=None, sub_cols=None) -> None:
    """The particles made again from the bones' rest heads, with a new ring or density if given; every value kept
    (the groups' latest first; carried over the grid when its size changes)."""
    if canon.locked(obj):
        raise GenerateError(f"{obj.name} keeps its .gpuc's structure: it is not regenerated")
    canon.restore(obj)
    old = canon.load(obj)
    masks = canon.group_weights(obj, canon.count(old))
    arm = obj.ree_gpuc_part.armature
    if arm is None or arm.type != "ARMATURE":
        raise GenerateError(f"{obj.name} has no armature")
    missing = [b for b in old["nodes"] if b not in arm.data.bones]
    if missing:
        raise GenerateError(f"{len(missing)} bones are gone ({', '.join(missing[:4])}): generate the part again")
    d = dict(old)
    if closed is not None:
        d["closed"] = bool(closed)
    if sub_rows is not None:
        d["sub_rows"] = int(sub_rows)
    if sub_cols is not None:
        d["sub_cols"] = int(sub_cols)
    node_co = heads(arm, d["nodes"])
    if d["closed"] or d["centre"] is not None:          # the axis again from the new top row
        _, _, d["centre"] = grid.order_columns(node_co[:d["node_cols"]], "CLOSED" if d["closed"] else "OPEN")
    d.update(_particles(d, node_co))
    d["base_co"] = canon.f32(d["co"])
    d["rest_offset"] = resample(old["rest_offset"], old, d)
    d["co"] = d["base_co"] + d["rest_offset"]
    from .modules.gpuc_build import validate_geometry
    validate_geometry(d["co"], [(f[0], f[k], f[k + 1]) for f in canon.faces(d) for k in range(1, len(f) - 1)])
    if (d["rows"], d["cols"]) != (old["rows"], old["cols"]) or d["closed"] != old["closed"]:
        for name in canon.FIELD_NAMES:
            d[name] = resample(old[name], old, d)
    canon.store(obj, d)
    canon.remember_mapping(obj, {field: resample(w, old, d) for field, w in masks.items()
                                if w is not None and np.isfinite(w).all()})
    p = obj.ree_gpuc_part
    p.rows, p.cols, p.closed, p.sub_rows, p.sub_cols = d["rows"], d["cols"], d["closed"], d["sub_rows"], d["sub_cols"]
    canon.build_mesh(obj)
    from . import sim
    sim.invalidate(obj)

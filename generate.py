"""Generates a Part's particle grid from bone chains in the armature's rest pose, checks it against the armature
and regenerates it.

Each chain is one column of the bone grid, top to bottom, ordered round the cloth by ``grid.order_columns``. A
particle sits on every bone's head, with ``sub_rows`` / ``sub_cols`` more between neighbouring bones, each skinned
to the four bones round it; ``anchor_rows`` more above the top row (``anchor_gap`` apart, pinned, as the game's
files hold the cloth by a row just above its first bones) and ``hem_rows`` below the bottom one (past the chains'
ends), skinned as the row they extend. Regenerating keeps every Config, Part and link-tail value and resamples each
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
    """The bones' rest heads in armature space; NaN for a node named "" (a short chain's past its end)."""
    bones = arm.data.bones
    return np.array([tuple(bones[n].head_local) if n else (np.nan,) * 3 for n in names],
                    np.float32).reshape(-1, 3)


def tops(node_co, node_cols: int) -> np.ndarray:
    """Each chain's first bone head (row by row node heads, NaN for a node a chain lacks)."""
    co = np.asarray(node_co, float).reshape(-1, node_cols, 3)
    out = np.empty((node_cols, 3))
    for c in range(node_cols):
        have = co[~np.isnan(co[:, c, 0]), c]
        out[c] = have[0] if len(have) else np.nan
    return out


def layout(arm, cols, closed="AUTO", truncate=False, sub_rows=0, sub_cols=0, anchor_rows=0, anchor_gap=0.01,
           hem_rows=0, side_cols=0, skin_style="HEADS") -> dict:
    """The bone grid the chains make and the particle grid on it. Chains of different lengths are cut to the
    shortest with ``truncate``; else the shorter ones end early, the faces past their ends left out."""
    if len(cols) < 2:
        raise GenerateError(f"a part needs at least two chains (the columns); {len(cols)} selected")
    lengths = sorted({len(c) for c in cols})
    if lengths[0] < 2:
        raise GenerateError("the chains need at least two bones each (the rows)")
    nr = lengths[0] if truncate else lengths[-1]
    cols = [c[:nr] for c in cols]
    order, is_closed, centre = grid.order_columns(heads(arm, [c[0] for c in cols]), closed)
    cols = [cols[k] for k in order]
    nodes = [cols[c][r] if r < len(cols[c]) else "" for r in range(nr) for c in range(len(cols))]
    lay = {"node_rows": nr, "node_cols": len(cols), "sub_rows": sub_rows, "sub_cols": sub_cols,
           "closed": is_closed, "centre": centre, "nodes": nodes, "anchor_rows": int(anchor_rows),
           "anchor_gap": float(anchor_gap), "hem_rows": int(hem_rows),
           "side_cols": [0, 0] if is_closed else [int(side_cols)] * 2, "skin_style": str(skin_style)}
    lay["mask"] = short_chain_mask(lay)
    return {**lay, **_particles(lay, heads(arm, nodes))}


def lattice_steps(d: dict) -> tuple:
    """(particles between each two bones down the chains, across them, side columns (left, right)) of a bone
    grid; its own lists, else its Sub Rows / Sub Cols for every gap."""
    nr, nc, closed = d["node_rows"], d["node_cols"], d["closed"]
    rs = [int(x) for x in d["row_steps"]] if d.get("row_steps") is not None else grid.steps(d["sub_rows"], nr - 1)
    cs = [int(x) for x in d["col_steps"]] if d.get("col_steps") is not None else None
    if cs is None:
        cs = grid.steps(d["sub_cols"], nc if closed else nc - 1)
    side = (0, 0) if closed else tuple(int(x) for x in d.get("side_cols", (0, 0)))
    return rs, cs, side


def grid_dims(d: dict) -> tuple:
    """(rows, cols) of a bone grid's particles, with its anchor, hem and side rows."""
    rs, cs, (left, right) = lattice_steps(d)
    rows, cols = grid.dims(d["node_rows"], d["node_cols"], rs, cs, d["closed"])
    return rows + d.get("anchor_rows", 0) + d.get("hem_rows", 0), cols + left + right


def node_points(d: dict) -> list:
    """Each bone node's lattice point (r * cols + c), row by row."""
    rs, cs, (left, _) = lattice_steps(d)
    _, cols = grid_dims(d)
    rl, cl = grid.node_lines(rs, d["node_rows"]), grid.node_lines(cs, d["node_cols"], d["closed"])
    above = d.get("anchor_rows", 0)
    return [(above + rl[r]) * cols + left + cl[c] for r in range(d["node_rows"]) for c in range(d["node_cols"])]


def short_chain_mask(d: dict) -> list:
    """The cells past the end of a short chain (a column whose nodes end early, named ""): a cell between two
    chains goes where either has ended."""
    nr, nc, closed = d["node_rows"], d["node_cols"], d["closed"]
    nodes = d["nodes"]
    if "" not in nodes:
        return []
    rs, cs, (left, right) = lattice_steps(d)
    rows, cols = grid_dims(d)
    rl, cl = grid.node_lines(rs, nr), grid.node_lines(cs, nc, closed)
    above = d.get("anchor_rows", 0)
    end = []
    for c in range(nc):
        have = [r for r in range(nr) if nodes[r * nc + c]]
        end.append(rows - 1 if have[-1] == nr - 1 else above + rl[have[-1]])
    span = cols if closed else cols - 1
    out = []
    for cell in range(span):
        x = cell - left
        if x < 0:
            a = b = 0
        elif not closed and x >= cl[-1]:
            a = b = nc - 1
        else:
            a = min(int(np.searchsorted(cl, x, side="right")) - 1, nc - 1)
            b = (a + 1) % nc if closed else min(a + 1, nc - 1)
        out += [r * span + cell for r in range(rows - 1) if r + 1 > min(end[a], end[b])]
    return sorted(out)


def _particles(lay: dict, node_co) -> dict:
    nr, nc = lay["node_rows"], lay["node_cols"]
    rs, cs, (left, right) = lattice_steps(lay)
    node = np.asarray(node_co, float).reshape(nr, nc, 3).copy()
    gone = np.isnan(node[..., 0])
    for c in range(nc):                                # a chain's missing nodes: between its bones, or on past them
        have = [r for r in range(nr) if not gone[r, c]]
        if len(have) < 2:
            raise GenerateError("a chain needs at least two bones")
        for r in range(nr):
            if not gone[r, c]:
                continue
            up, down = [x for x in have if x < r], [x for x in have if x > r]
            if up and down:
                a, b = up[-1], down[0]
                node[r, c] = node[a, c] + (node[b, c] - node[a, c]) * (r - a) / (b - a)
            else:
                a, b = (have[0], have[1]) if not up else (have[-2], have[-1])
                node[r, c] = node[a, c] + (node[b, c] - node[a, c]) * (r - a) / (b - a)
    segments = lay.get("skin_style", "HEADS") == "SEGMENTS"
    pos, skin, weight = grid.subdivide(node, rs, cs, lay["closed"], lay["centre"], segments)
    if gone.any():                                     # skinned to the chain's nearest bone instead
        to = np.arange(nr * nc)
        for c in range(nc):
            have = [r for r in range(nr) if not gone[r, c]]
            for r in range(nr):
                if gone[r, c]:
                    to[r * nc + c] = min(have, key=lambda x: (abs(x - r), x)) * nc + c
        skin, weight = _merged(to[skin], weight)
    rows, cols = grid.dims(nr, nc, rs, cs, lay["closed"])
    pos, skin, weight = grid.extend_cols(pos, skin, weight, rows, cols, left, right)
    cols += left + right
    above, below = lay.get("anchor_rows", 0), lay.get("hem_rows", 0)
    pos, skin, weight = grid.extend(pos, skin, weight, cols, above, lay.get("anchor_gap", 0.01), below)
    if segments and below:                             # past the last heads: the last bones' alone
        weight = np.asarray(weight, float).copy()
        for i in range((rows + above) * cols, len(weight)):
            last = np.asarray(skin[i]) // nc == nr - 1
            if (weight[i] * last).sum() > 0:
                weight[i] = weight[i] * last / (weight[i] * last).sum()
    rows += above + below
    sh = grid.shape(rows, cols, lay["closed"], lay.get("mask", ()), lay.get("slits", ()))
    return {"rows": rows, "cols": cols, "node_co": node.reshape(-1, 3).astype(np.float32), "co": pos[sh.point],
            "skin": skin[sh.point], "skin_w": weight[sh.point], "row_steps": rs, "col_steps": cs,
            "side_cols": [left, right]}


def _merged(skin, weight) -> tuple:
    """Skin with each bone once per particle (weights summed), its slots kept."""
    skin, weight = np.asarray(skin).copy(), np.asarray(weight, float).copy()
    for i in range(len(skin)):
        idx, inv = np.unique(skin[i], return_inverse=True)
        if len(idx) == skin.shape[1]:
            continue
        w = np.bincount(inv, weights=weight[i], minlength=len(idx))
        skin[i] = np.concatenate([idx, np.full(skin.shape[1] - len(idx), idx[0])])
        weight[i] = np.concatenate([w, np.zeros(skin.shape[1] - len(idx))])
    return skin, weight


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
    first = next(n for n in lay["nodes"] if n)          # a chain may lack its first nodes
    name = unique_name("GPUC_PART_", "_" + first.rsplit("_", 1)[0])
    obj = bpy.data.objects.new(name, bpy.data.meshes.new(name))
    obj["TYPE"] = props.PART_TYPE
    coll.objects.link(obj)
    obj.hide_render = True
    obj.show_wire = True
    obj.show_in_front = bpy.context.scene.ree_gpuc.in_front != "BEHIND"
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
    n = len(lay["co"])
    values = {name: np.full(n, default, np.float32) for name, _, _, default in canon.PARTICLE_FIELDS}
    pinned = lay.get("anchor_rows", 0) or 1              # the anchor rows, else the top row: they follow the skinning
    values["sim_weight"][_rows_of(lay) < pinned] = 0.0
    canon.store(obj, {**lay, "tile": tile, **values})
    canon.build_mesh(obj)
    return obj


def check(obj) -> list[str]:
    """What no longer matches the armature: missing bones, or bones whose rest head moved (a Part that does not
    follow its bones: ``fileio.locked_problems``)."""
    if not canon.attached(obj):
        from . import fileio, fit, follow
        problems = fileio.locked_problems(obj)
        n, far = follow.moved(obj)
        if n and not problems:
            why = fit._fittable(obj)
            problems.append(f"{n} skin bones moved since its particles were placed (up to {far * 100:.2f} cm): "
                            + (f"they cannot follow them, as {why}" if why else "Follow Bones moves them along"))
        return problems
    d = canon.recipe(obj)
    arm = obj.ree_gpuc_part.armature
    if arm is None or arm.type != "ARMATURE":
        return ["the part has no armature"]
    missing = [b for b in d["nodes"] if b and b not in arm.data.bones]
    if missing:
        return [f"{len(missing)} bones are gone: {', '.join(missing[:4])}"]
    have = [k for k, b in enumerate(d["nodes"]) if b]
    moved = np.linalg.norm(heads(arm, [d["nodes"][k] for k in have]) - d["node_co"][have], axis=1)
    far = np.where(moved > 1e-5)[0]
    if len(far):
        return [f"{len(far)} bones moved off the rest heads the part was made from (up to {moved.max() * 100:.2f} cm)"]
    return []


def _rows_of(d: dict) -> np.ndarray:
    """Each particle's lattice row."""
    return canon.shape(d).point // d["cols"]


def resample(values, old: dict, new: dict) -> np.ndarray:
    """A particle value carried from the old grid to the new one: each new particle takes the old grid's value at
    its place on the bone grid (bilinear; round the ring when the old grid closed). A slit's second copies take
    the old second copies' (the first's where it had none); a point the old shape left out, its neighbour's."""
    sh_o, sh_n = canon.shape(old), canon.shape(new)
    values = np.asarray(values, float)
    first = grid.lattice(values, sh_o)
    out = _resample_lattice(first, old, new)
    if not (sh_n.copy == 1).any():
        return out[sh_n.point].astype(np.float32)
    second = first.copy()
    second[sh_o.point[sh_o.copy == 1]] = values[sh_o.copy == 1]
    return grid.particles(out, sh_n, _resample_lattice(second, old, new)).astype(np.float32)


def _resample_lattice(values, old: dict, new: dict) -> np.ndarray:
    """``resample`` on the lattice points: rows * cols values of the old grid to the new one's."""
    trailing = values.shape[1:]
    v = values.reshape(old["rows"], old["cols"], *trailing)
    r_new, c_new = np.divmod(np.arange(new["rows"] * new["cols"]), new["cols"])
    r = _old_place(r_new, old, new, True)
    c = _old_place(c_new, old, new, False)
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
        seam = c > old["cols"] - 1                     # a new ring's gap between the old last column and the first
        c0[seam], c1[seam] = old["cols"] - 1, 0
        fc[seam] = c[seam] - (old["cols"] - 1)
    fr = fr.reshape((-1,) + (1,) * len(trailing))
    fc = fc.reshape((-1,) + (1,) * len(trailing))
    top = v[r0, c0] * (1 - fc) + v[r0, c1] * fc
    bottom = v[r1, c0] * (1 - fc) + v[r1, c1] * fc
    return top * (1 - fr) + bottom * fr


def _axis(d: dict, rows: bool) -> tuple:
    """(rows before the bones, each bone's row, rows after them, closed) down a bone grid; or the same across
    it (side columns, each chain's column)."""
    rs, cs, (left, right) = lattice_steps(d)
    if rows:
        return d.get("anchor_rows", 0), grid.node_lines(rs, d["node_rows"]), d.get("hem_rows", 0), False
    return left, grid.node_lines(cs, d["node_cols"], d["closed"]), right, d["closed"]


def _old_place(x, old: dict, new: dict, rows: bool) -> np.ndarray:
    """New particle rows (or columns; fractional) as places on the old grid: between the bones by the bones'
    places, anchor rows and left side columns counted from the first bone, hem rows and right side columns from the
    last (the old last bone's when it had none); a new ring's gap after the last chain past the old last column."""
    lead_o, lines_o, trail_o, closed_o = _axis(old, rows)
    lead_n, lines_n, trail_n, closed_n = _axis(new, rows)
    body = np.asarray(x, float) - lead_n
    nodes = np.arange(len(lines_n))
    if closed_n:
        u = np.interp(body % lines_n[-1], lines_n, nodes)
    else:
        u = np.interp(body, lines_n, nodes)
    out = np.interp(u, np.arange(len(lines_o)), lines_o) + lead_o
    if closed_n and not closed_o:
        past = u > len(lines_o) - 1
        out[past] = lead_o + lines_o[-1] + (u[past] - (len(lines_o) - 1))
    if not closed_n:
        before, after = body < 0, body > lines_n[-1]
        out[before] = np.maximum(lead_o + body[before], 0.0)
        out[after] = lead_o + lines_o[-1] + np.minimum(body[after] - lines_n[-1], trail_o)
    return out


def _carry_shape(old: dict, new: dict) -> tuple:
    """The old grid's mask and slits on the new grid (another density, ring or extra rows): a new cell is left out
    when its middle lies in a cell the old left out; a slit keeps its place between the chains and its top row's
    place down them, and is dropped where the new grid cannot have it."""
    sh = canon.shape(old)
    if sh.plain:
        return [], []
    rows, cols, closed = new["rows"], new["cols"], new["closed"]
    span_o, span_n = (old["cols"] if old["closed"] else old["cols"] - 1), (cols if closed else cols - 1)
    mid_r = _old_place(np.arange(rows - 1) + 0.5, old, new, True)
    mid_c = _old_place(np.arange(span_n) + 0.5, old, new, False)
    gone = set(sh.mask)
    mask = []
    for r in range(rows - 1):
        ro = min(max(int(np.floor(mid_r[r])), 0), old["rows"] - 2)
        for c in range(span_n):
            co = int(np.floor(mid_c[c]))
            co = co % span_o if old["closed"] else min(max(co, 0), span_o - 1)
            if ro * span_o + co in gone:
                mask.append(r * span_n + c)
    at_r = _old_place(np.arange(rows), old, new, True)
    at_c = _old_place(np.arange(cols), old, new, False)
    slits = []
    for c, top in sh.slits:
        far = np.abs(at_c - c)
        if old["closed"]:
            far = np.minimum(far, old["cols"] - far)
        cn = int(np.argmin(np.round(far, 6)))            # halfway between two: the first
        tn = int(np.argmin(np.round(np.abs(at_r - top), 6)))
        if 0 <= tn < rows - 1 and (closed or 0 < cn < cols - 1):
            slits.append((cn, tn))
    return mask, sorted(set(slits))


def regenerate(obj, closed=None, sub_rows=None, sub_cols=None, anchor_rows=None, anchor_gap=None,
               hem_rows=None, mask=None, slits=None, side_cols=None, skin_style=None) -> None:
    """The particles made again from the bones' rest heads, with a new ring or density if given; every value kept
    (the groups' latest first; carried over the grid when its size changes). A grid with its own particles between
    each two bones keeps them until another Sub Rows / Sub Cols is given."""
    if not canon.attached(obj):
        raise GenerateError(f"{obj.name} does not follow its bones: it is not regenerated")
    canon.restore(obj)
    old = canon.recipe(obj)
    masks = canon.group_weights(obj, canon.count(old))
    arm = obj.ree_gpuc_part.armature
    if arm is None or arm.type != "ARMATURE":
        raise GenerateError(f"{obj.name} has no armature")
    missing = [b for b in old["nodes"] if b and b not in arm.data.bones]
    if missing:
        raise GenerateError(f"{len(missing)} bones are gone ({', '.join(missing[:4])}): generate the part again")
    d = dict(old)
    if closed is not None and bool(closed) != old["closed"]:
        d["closed"], d["col_steps"] = bool(closed), None
    if sub_rows is not None and int(sub_rows) != old["sub_rows"]:
        d["sub_rows"], d["row_steps"] = int(sub_rows), None
    if sub_cols is not None and int(sub_cols) != old["sub_cols"]:
        d["sub_cols"], d["col_steps"] = int(sub_cols), None
    for key, value, kind in (("anchor_rows", anchor_rows, int), ("anchor_gap", anchor_gap, float),
                             ("hem_rows", hem_rows, int)):
        if value is not None:
            d[key] = kind(value)
    if side_cols is not None:
        d["side_cols"] = [int(side_cols)] * 2
    if skin_style is not None:
        d["skin_style"] = str(skin_style)
    if d["closed"]:
        d["side_cols"] = [0, 0]
    rs, cs, side = lattice_steps(d)
    d["row_steps"], d["col_steps"], d["side_cols"] = rs, cs, list(side)
    d["rows"], d["cols"] = grid_dims(d)
    now = (rs, cs, side, d["closed"], d["anchor_rows"], d["hem_rows"])
    same = now == (old["row_steps"], old["col_steps"], tuple(old["side_cols"]), old["closed"], old["anchor_rows"],
                   old["hem_rows"])
    carried = (old["mask"], old["slits"]) if same else _carry_shape(old, d)
    d["mask"] = list(mask) if mask is not None else carried[0]
    d["slits"] = [tuple(x) for x in slits] if slits is not None else carried[1]
    node_co = heads(arm, d["nodes"])
    if d["closed"] or d["centre"] is not None:          # the axis again from the new top row
        _, _, d["centre"] = grid.order_columns(tops(node_co, d["node_cols"]), "CLOSED" if d["closed"] else "OPEN")
    d.update(_particles(d, node_co))
    d["base_co"] = canon.f32(d["co"])
    d["rest_offset"] = resample(old["rest_offset"], old, d)
    d["co"] = d["base_co"] + d["rest_offset"]
    from .modules.gpuc_build import validate_geometry
    validate_geometry(d["co"], [(f[0], f[k], f[k + 1]) for f in canon.faces(d) for k in range(1, len(f) - 1)])
    if not same or (canon.shape(d).mask, canon.shape(d).slits) != (canon.shape(old).mask, canon.shape(old).slits):
        for name in canon.FIELD_NAMES:
            d[name] = resample(old[name], old, d)
        if d["anchor_rows"] > old["anchor_rows"]:              # new anchor rows are pinned, as new Parts' are
            d["sim_weight"][_rows_of(d) < d["anchor_rows"]] = 0.0
    canon.store(obj, d)
    canon.remember_mapping(obj, {field: resample(w, old, d) for field, w in masks.items()
                                if w is not None and np.isfinite(w).all()})
    p = obj.ree_gpuc_part
    p.rows, p.cols, p.closed, p.sub_rows, p.sub_cols = d["rows"], d["cols"], d["closed"], d["sub_rows"], d["sub_cols"]
    canon.build_mesh(obj)
    from . import sim
    sim.invalidate(obj)

"""Edits a Part's links in Edit Mode: the links between the selected particles, of the shapes ticked in the Links
panel, are removed, given a tail of their own or a stiffness preset's, or let follow their shape's tail; a link is
added between two selected particles. In Object Mode a stiffness preset sets a Part's overall tails, and Apply to
All lets every link of a shape follow its tail.

Maintenance constraints:
- The links live in the Part's canonical data (an object property), which Edit Mode's undo does not hold. Each
  edit numbers its step on the edit mesh (``STEP_LAYER``, which that undo does hold) and keeps the links of every
  step it made here (``_steps``); after an undo or redo the links of the step the mesh shows are put back.
- A Part that follows its bone grid takes its links from the grid: its tails can be its links' own (kept as the
  grid's overrides), but links are added and removed only once it is edited as a mesh."""
import bmesh
import bpy
import numpy as np
from bpy.app.handlers import persistent
from bpy.props import EnumProperty, FloatProperty, IntProperty, StringProperty

from . import canon, props
from .modules import grid

STEP_LAYER = ".ree_gpuc_link_step"
SHAPE_ITEMS = [(str(k), label, desc) for k, (_, label, desc) in enumerate(props.LINK_SHAPES)]
TYPE_OF_SHAPE = (0, 0, 1, 2)                 # a link shape's link type (grid.LINK_TYPES)
_steps = {}                                  # Part name -> {step: (links, tails, own, edited, overrides)}
_count = [0]


def part(context):
    """The active Part in Edit Mode, else None."""
    obj = context.active_object
    if obj is None or obj.mode != "EDIT" or not props.is_part(obj) or not canon.has(obj):
        return None
    return obj


def selected(obj) -> np.ndarray:
    bm = bmesh.from_edit_mesh(obj.data)
    return np.fromiter((v.select for v in bm.verts), bool, len(bm.verts))


def between(obj, d):
    """The links both of whose particles are selected (indices into ``canon.links(d)``), whatever their shape;
    None while particles were added or removed since Edit Mode was entered."""
    sel = selected(obj)
    if len(sel) != canon.count(d):
        return None
    ln = canon.links(d)
    return np.flatnonzero(sel[ln[:, 0]] & sel[ln[:, 1]])


def picked(obj, d, scene):
    """``between``, of the shapes ticked in the Links panel."""
    idx = between(obj, d)
    if idx is None:
        return None
    on = [k for k in range(4) if scene.ree_gpuc.link_pick[k]]
    return idx[np.isin(canon.links(d)[idx, 3], on)]


def why_not(obj, d, structure: bool = False) -> str:
    """Why the links of the Part in Edit Mode cannot be edited ("" when they can); ``structure``: links added or
    removed."""
    if structure and d["attached"]:
        return (f"{obj.name} follows its bone grid, whose links it takes: Edit as Mesh to add or remove links "
                f"(its tails can be set as it is)")
    if structure and not d.get("editable"):
        return f"{obj.name} keeps its file's structure as it is: its links can only take other tails"
    if len(selected(obj)) != canon.count(d):
        return f"particles of {obj.name} were added or removed: leave Edit Mode once, then edit its links"
    return ""


# --- undo -------------------------------------------------------------------------------------------------------

def _state(obj):
    c = obj[canon.KEY]
    over = obj.get(canon.OVERRIDES_KEY)
    return (list(c["links"]), list(c["tails"]), list(c["own"]), bool(c.get("edited")),
            over.to_dict() if over is not None else None)


def _put(obj, state) -> None:
    links, tails, own, edited, over = state
    c = obj[canon.KEY]
    c["links"], c["tails"], c["own"] = links, tails, own
    if edited:
        c["edited"] = 1
    elif "edited" in c:
        del c["edited"]
    if over is not None:
        obj[canon.OVERRIDES_KEY] = over
    elif canon.OVERRIDES_KEY in obj.keys():
        del obj[canon.OVERRIDES_KEY]


def _layer(bm):
    return bm.verts.layers.int.get(STEP_LAYER) or bm.verts.layers.int.new(STEP_LAYER)


def _shown_step(obj) -> int:
    bm = bmesh.from_edit_mesh(obj.data)
    layer = bm.verts.layers.int.get(STEP_LAYER)
    first = next(iter(bm.verts), None)
    return int(first[layer]) if layer is not None and first is not None else 0


def begin(obj) -> None:
    """Keep the links as they are before an edit, under the step the mesh shows."""
    _steps.setdefault(obj.name, {}).setdefault(_shown_step(obj), _state(obj))


def finish(obj) -> None:
    """Number the edit's step on the mesh and keep the links it left."""
    _count[0] += 1
    bm = bmesh.from_edit_mesh(obj.data)
    layer = _layer(bm)
    for v in bm.verts:
        v[layer] = _count[0]
    bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
    _steps.setdefault(obj.name, {})[_count[0]] = _state(obj)
    from . import sim
    sim.invalidate(obj)


@persistent
def _after_undo(*_args) -> None:
    """Put back the links of the step each Part in Edit Mode now shows."""
    for name, steps in _steps.items():
        obj = bpy.data.objects.get(name)
        if obj is None or obj.mode != "EDIT" or not canon.has(obj):
            continue
        state = steps.get(_shown_step(obj))
        if state is not None and state != _state(obj):
            _put(obj, state)


# --- editing ----------------------------------------------------------------------------------------------------

def _edit(op, context, structure=False):
    """(Part, its data, the picked links), after ``begin``; None (reported) when they cannot be edited."""
    from .ops import refused
    obj = part(context)
    d = canon.load(obj)
    why = why_not(obj, d, structure)
    if why:
        refused(op, why)
        return None
    begin(obj)
    return obj, d, picked(obj, d, context.scene)


def _store_links(obj, d, links, tails, own) -> None:
    """A locked Part's new set of links; the export then writes its file again from the Parts."""
    canon.store(obj, {**d, "links": np.asarray(links, np.int64).reshape(-1, 4),
                      "tails": np.asarray(tails, np.float32).reshape(-1, 4), "own": np.asarray(own, bool),
                      "edited": True})


def _redraw(context) -> None:
    for area in context.screen.areas:
        area.tag_redraw()


class _EditOp:
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return part(context) is not None


class REE_GPUC_OT_link_stiffness(_EditOp, bpy.types.Operator):
    """Give the links between the selected particles, of the shapes ticked, a stiffness of their own: a change of
    their shape's overall stiffness leaves them"""
    bl_idname = "ree_gpuc.link_stiffness"
    bl_label = "Set Stiffness"
    k: FloatProperty(name="Stiffness", default=1.0, min=0.0, max=1.0, subtype="FACTOR",
                     description="The share of a link's length error each solve removes")
    k2: FloatProperty(name="Multiplier", default=1.0, min=0.0, max=1.0, subtype="FACTOR",
                      description="The stiffness is k x this while lo < rest length / current length < hi")
    lo: FloatProperty(name="Range Low", default=1.0, min=0.0, max=1.0, precision=4)
    hi: FloatProperty(name="Range High", default=1.0, min=0.0, max=props.HI_MAX, precision=3)

    def invoke(self, context, event):
        obj = part(context)
        d = canon.load(obj)
        idx = picked(obj, d, context.scene)
        if idx is not None and len(idx):              # the first picked link's tail to start from
            self.k, self.k2, self.lo, self.hi = (float(x) for x in canon.link_tails(obj, d)[idx[0]])
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        got = _edit(self, context)
        if got is None:
            return {"CANCELLED"}
        obj, d, idx = got
        canon.set_own(obj, idx, np.tile((self.k, self.k2, self.lo, self.hi), (len(idx), 1)))
        finish(obj)
        _redraw(context)
        self.report({"INFO"}, f"{len(idx)} links have a stiffness of their own")
        return {"FINISHED"}


class REE_GPUC_OT_link_follow(_EditOp, bpy.types.Operator):
    """Let the links between the selected particles, of the shapes ticked, take their shape's overall stiffness"""
    bl_idname = "ree_gpuc.link_follow"
    bl_label = "Follow Overall"

    def execute(self, context):
        got = _edit(self, context)
        if got is None:
            return {"CANCELLED"}
        obj, d, idx = got
        canon.follow(obj, idx)
        finish(obj)
        _redraw(context)
        self.report({"INFO"}, f"{len(idx)} links follow their shape's overall stiffness")
        return {"FINISHED"}


class REE_GPUC_OT_link_apply_preset(_EditOp, bpy.types.Operator):
    """Give the links between the selected particles, of the shapes ticked, the stiffness preset's tail of their
    shape, as their own"""
    bl_idname = "ree_gpuc.link_apply_preset"
    bl_label = "Apply to Selected Links"

    @classmethod
    def poll(cls, context):
        return part(context) is not None and context.scene.ree_gpuc.stiffness_preset != "NONE"

    def execute(self, context):
        from . import presets
        from .ops import refused
        try:
            tails = np.asarray(presets.stiffness_tails(context.scene.ree_gpuc.stiffness_preset), np.float32)
        except (OSError, ValueError, KeyError) as e:
            return refused(self, f"the stiffness preset cannot be read: {e}")
        got = _edit(self, context)
        if got is None:
            return {"CANCELLED"}
        obj, d, idx = got
        canon.set_own(obj, idx, tails[canon.links(d)[idx, 3]])
        finish(obj)
        _redraw(context)
        self.report({"INFO"}, f"Applied the preset to {len(idx)} links")
        return {"FINISHED"}


class REE_GPUC_OT_link_remove(_EditOp, bpy.types.Operator):
    """Remove the links between the selected particles, of the shapes ticked. A link the faces give stays away
    while its two particles do"""
    bl_idname = "ree_gpuc.link_remove"
    bl_label = "Remove Links"

    def execute(self, context):
        got = _edit(self, context, structure=True)
        if got is None:
            return {"CANCELLED"}
        obj, d, idx = got
        keep = np.setdiff1d(np.arange(len(d["links"])), idx)
        _store_links(obj, d, d["links"][keep], d["tails"][keep], d["own"][keep])
        finish(obj)
        _redraw(context)
        self.report({"INFO"}, f"Removed {len(idx)} links")
        return {"FINISHED"}


class REE_GPUC_OT_link_add(_EditOp, bpy.types.Operator):
    """Add a link of this shape between the two selected particles; it follows its shape's overall stiffness"""
    bl_idname = "ree_gpuc.link_add"
    bl_label = "Add Link"
    shape: EnumProperty(name="Shape", items=SHAPE_ITEMS, default="2")

    def execute(self, context):
        from .ops import refused
        got = _edit(self, context, structure=True)
        if got is None:
            return {"CANCELLED"}
        obj, d, _ = got
        sel = np.flatnonzero(selected(obj))
        if len(sel) != 2:
            return refused(self, f"select the two particles to link ({len(sel)} are selected)")
        shape = int(self.shape)
        a, b = (int(x) for x in sel)
        t = TYPE_OF_SHAPE[shape]
        ln = d["links"]
        if ((np.minimum(ln[:, 0], ln[:, 1]) == a) & (np.maximum(ln[:, 0], ln[:, 1]) == b) & (ln[:, 2] == t)).any():
            return refused(self, f"particles {a} and {b} have a {props.LINK_SHAPES[shape][1]} link already")
        tail = getattr(obj.ree_gpuc_part, props.LINK_SHAPES[shape][0]).tail()
        _store_links(obj, d, np.vstack([ln, [b, a, t, shape]]), np.vstack([d["tails"], tail]),
                     np.append(d["own"], False))
        finish(obj)
        _redraw(context)
        self.report({"INFO"}, f"Added a {props.LINK_SHAPES[shape][1]} link")
        return {"FINISHED"}


class _SavePreset:
    preset_name: StringProperty(name="Name", default="New Stiffness")

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def save(self, context, tails):
        from . import presets
        from .ops import refused
        try:
            context.scene.ree_gpuc.stiffness_preset = presets.save_stiffness(self.preset_name, tails)
        except FileExistsError:
            return refused(self, f"a stiffness preset named {self.preset_name} exists: choose another name")
        except (OSError, ValueError) as e:
            return refused(self, f"the stiffness preset cannot be saved: {e}")
        self.report({"INFO"}, f"Saved the stiffness preset {self.preset_name}")
        return {"FINISHED"}


class REE_GPUC_OT_link_save_preset(_SavePreset, bpy.types.Operator):
    """Save a stiffness preset: each shape's tail from the first selected link of it, of the shapes ticked, else
    the shape's overall one"""
    bl_idname = "ree_gpuc.link_save_preset"
    bl_label = "Save Selected as Preset"

    @classmethod
    def poll(cls, context):
        return part(context) is not None

    def execute(self, context):
        obj = part(context)
        d = canon.load(obj)
        idx = picked(obj, d, context.scene)
        idx = np.zeros(0, np.int64) if idx is None else idx
        ln, now = canon.links(d), canon.link_tails(obj, d)
        tails = [getattr(obj.ree_gpuc_part, n).tail() for n, _, _ in props.LINK_SHAPES]
        for k in range(4):
            mine = idx[ln[idx, 3] == k]
            if len(mine):
                tails[k] = tuple(float(x) for x in now[mine[0]])
        return self.save(context, tails)


# --- a Part's overall stiffness (Object Mode, the Part panel) ---------------------------------------------------

def _object_part(context):
    obj = context.object
    return obj if props.is_part(obj) and canon.has(obj) and obj.mode == "OBJECT" else None


class REE_GPUC_OT_links_follow_all(bpy.types.Operator):
    """Let every link of this shape take the shape's overall stiffness, those with a stiffness of their own too"""
    bl_idname = "ree_gpuc.links_follow_all"
    bl_label = "Apply to All"
    bl_options = {"REGISTER", "UNDO"}
    shape: IntProperty(min=0, max=3)

    @classmethod
    def poll(cls, context):
        return _object_part(context) is not None

    def execute(self, context):
        obj = _object_part(context)
        d = canon.load(obj)
        mine = np.flatnonzero(canon.links(d)[:, 3] == self.shape)
        canon.follow(obj, mine)
        from . import sim
        sim.invalidate(obj)
        self.report({"INFO"}, f"All {len(mine)} {props.LINK_SHAPES[self.shape][1]} links take the overall stiffness")
        return {"FINISHED"}


class REE_GPUC_OT_stiffness_apply_overall(bpy.types.Operator):
    """Set the Part's overall stiffness of the four shapes from the stiffness preset; the links that follow it take
    it, those with a stiffness of their own keep theirs"""
    bl_idname = "ree_gpuc.stiffness_apply_overall"
    bl_label = "Apply to Overall"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return _object_part(context) is not None and context.scene.ree_gpuc.stiffness_preset != "NONE"

    def execute(self, context):
        from . import presets, sim
        from .ops import refused
        choice = context.scene.ree_gpuc.stiffness_preset
        try:
            tails = presets.stiffness_tails(choice)
        except (OSError, ValueError, KeyError) as e:
            return refused(self, f"the stiffness preset cannot be read: {e}")
        obj = _object_part(context)
        p = obj.ree_gpuc_part
        if choice.startswith("BUILTIN:"):
            p.link_preset = choice[8:]
        else:
            for (name, _, _), (k, k2, lo, hi) in zip(props.LINK_SHAPES, tails):
                t = getattr(p, name)
                t.k, t.k2, t.lo, t.hi = k, k2, lo, hi
                t.conditional = k2 != 1.0
        sim.invalidate(obj)
        self.report({"INFO"}, "Applied the stiffness preset to the overall stiffness")
        return {"FINISHED"}


class REE_GPUC_OT_stiffness_save_overall(_SavePreset, bpy.types.Operator):
    """Save the Part's overall stiffness of the four shapes as a stiffness preset"""
    bl_idname = "ree_gpuc.stiffness_save_overall"
    bl_label = "Save Overall as Preset"

    @classmethod
    def poll(cls, context):
        return _object_part(context) is not None

    def execute(self, context):
        p = _object_part(context).ree_gpuc_part
        return self.save(context, [getattr(p, n).tail() for n, _, _ in props.LINK_SHAPES])


def own_counts(obj) -> list:
    """Per shape (``props.LINK_SHAPES`` order): (links with a tail of their own, links)."""
    d = canon.load(obj)
    ln = canon.links(d)
    return [(int((d["own"] & (ln[:, 3] == k)).sum()), int((ln[:, 3] == k).sum())) for k in range(len(grid.SHAPES))]


CLASSES = (REE_GPUC_OT_link_stiffness, REE_GPUC_OT_link_follow, REE_GPUC_OT_link_apply_preset,
           REE_GPUC_OT_link_remove, REE_GPUC_OT_link_add, REE_GPUC_OT_link_save_preset, REE_GPUC_OT_links_follow_all,
           REE_GPUC_OT_stiffness_apply_overall, REE_GPUC_OT_stiffness_save_overall)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    for handlers in (bpy.app.handlers.undo_post, bpy.app.handlers.redo_post):
        handlers.append(_after_undo)


def unregister():
    for handlers in (bpy.app.handlers.undo_post, bpy.app.handlers.redo_post):
        if _after_undo in handlers:
            handlers.remove(_after_undo)
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
    _steps.clear()

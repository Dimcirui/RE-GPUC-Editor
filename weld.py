"""Manages welds: the zero-length, full-stiffness links joining two parts at the bones they share, from the later
part to the earlier.

One weld object per pair of welded Parts, under the header at the welded particles' rest centre, holds the two
Parts and the links' tail; the preview holds each pair on one point (``sim.weld``).

Maintenance constraints:
- For Parts made from bones, ``pairs`` finds each shared bone's lattice particle again on every call, so a
  regenerated Part keeps its welds; the export leaves out and reports a weld that has ``problems``.
- A Keep Structure cloth keeps its file's pairs under ``PAIRS_KEY``; its export writes the tail but cannot add
  or remove a weld."""
import bpy
import numpy as np
from bpy.props import FloatProperty, IntProperty, PointerProperty

from . import props
from .ui import _ObjectPanel, _split

TYPE = "RE_GPUC_WELD"
PAIRS_KEY = "ree_gpuc_weld_pairs"       # on a weld: (particle of part a, particle of part b) flat
EDITED_KEY = "ree_gpuc_weld_edited"     # on a weld whose tail was set by hand: all its links take it on export


def is_weld(obj) -> bool:
    return obj is not None and obj.get("TYPE") == TYPE


def _poll_part(self, obj):
    return props.is_part(obj)


def _edited(self, context):
    self.id_data[EDITED_KEY] = 1


class REE_GPUC_Weld(bpy.types.PropertyGroup):
    """A weld object's values: the two Parts and the tail their links share. With a rest length of 0 the
    multiplier never applies"""
    part_a: PointerProperty(name="Part A", type=bpy.types.Object, poll=_poll_part)
    part_b: PointerProperty(name="Part B", type=bpy.types.Object, poll=_poll_part)
    k: FloatProperty(name="Stiffness", default=1.0, min=0.0, max=1.0, subtype="FACTOR", update=_edited,
                     description="The share of the gap each solve closes: 1 holds the two particles on one point")
    k2: FloatProperty(name="Multiplier", default=1.0, min=0.0, max=1.0, subtype="FACTOR", update=_edited)
    lo: FloatProperty(name="Range low", default=1.0, min=0.0, max=1.0, precision=4, update=_edited)
    hi: FloatProperty(name="Range high", default=props.HI_MAX, min=0.0, max=props.HI_MAX, precision=3,
                      update=_edited)
    link_type: IntProperty(name="Link Type", default=4, description="The links' type number. Usually 4; "
                                                                     "the simulation does not read it")

    def tail(self) -> tuple:
        return self.k, self.k2, self.lo, self.hi


def node_particles(d) -> dict:
    """Each bone's particle on a Part made from bones: its lattice node's, the one the export uses."""
    out = {}
    for j, name in enumerate(d["nodes"]):
        r, c = divmod(j, d["node_cols"])
        out[name] = r * (d["sub_rows"] + 1) * d["cols"] + c * (d["sub_cols"] + 1)
    return out


def shared_pairs(part_a, part_b) -> list:
    """For each bone two Parts made from bones share, its particle on each: (particle of a, particle of b),
    sorted by bone name."""
    na, nb = node_particles(_layout(part_a)), node_particles(_layout(part_b))
    return [(na[name], nb[name]) for name in sorted(set(na) & set(nb))]


def _layout(part) -> dict:
    """The keys of ``canon.load`` that ``node_particles`` reads, without loading the rest (the overlay asks on
    every redraw)."""
    from . import canon
    c = part[canon.KEY]
    if "nodes" not in c:                             # v0.1: one particle per bone
        return {"nodes": [str(b) for b in c["bones"]], "node_cols": int(c["cols"]), "sub_rows": 0, "sub_cols": 0,
                "cols": int(c["cols"])}
    return {"nodes": [str(b) for b in c["nodes"]], "node_cols": int(c["node_cols"]), "sub_rows": int(c["sub_rows"]),
            "sub_cols": int(c["sub_cols"]), "cols": int(c["cols"])}


def pairs(obj) -> list:
    """A weld's pairs: a Keep Structure cloth's as its file has them, else its Parts' shared bones' (made again
    each time, so a Part regenerated keeps them)."""
    from . import canon
    w = obj.ree_gpuc_weld
    if props.is_part(w.part_a) and props.is_part(w.part_b) and canon.has(w.part_a) and canon.has(w.part_b) \
            and not canon.locked(w.part_a) and not canon.locked(w.part_b):
        return shared_pairs(w.part_a, w.part_b)
    flat = list(obj.get(PAIRS_KEY, ()))
    return list(zip(flat[0::2], flat[1::2]))


def problems(obj, coll=None) -> list:
    """Why a weld cannot be written (the export leaves it out, said), [] when it can."""
    from . import canon
    w = obj.ree_gpuc_weld
    a, b = w.part_a, w.part_b
    if not props.is_part(a) or not props.is_part(b):
        return ["it needs two Parts"]
    if a == b:
        return ["its two Parts are the same"]
    if coll is not None and (a.name not in coll.all_objects or b.name not in coll.all_objects):
        return ["a Part is not this cloth's"]
    if not canon.has(a) or not canon.has(b):
        return ["a Part has no particles"]
    if canon.locked(a) != canon.locked(b):
        return ["one Part keeps a file's structure, the other was made from bones"]
    if canon.locked(a):
        n = {a: int(a[canon.KEY]["count"]), b: int(b[canon.KEY]["count"])}
        if not pairs(obj):
            return ["it welds no particles"]
        if any(not (0 <= ia < n[a] and 0 <= ib < n[b]) for ia, ib in pairs(obj)):
            return ["it names particles its Parts no longer have: import the file again"]
        return []
    if not shared_pairs(a, b):
        return ["its Parts share no bone: a weld joins each bone's particle on both"]
    return []


def objects(coll) -> list:
    return sorted((o for o in coll.all_objects if is_weld(o)), key=lambda o: o.name) if coll is not None else []


def welds(coll) -> list:
    """Every welded pair of the cloth's welds that can be written: (weld object, Part a, its particle, Part b, its
    particle); a second weld of the same two Parts left out."""
    out, seen = [], set()
    for obj in objects(coll):
        w = obj.ree_gpuc_weld
        key = frozenset((w.part_a, w.part_b))
        if key in seen or problems(obj, coll):
            continue
        seen.add(key)
        out += [(obj, w.part_a, ia, w.part_b, ib) for ia, ib in pairs(obj)]
    return out


def skipped(coll) -> list:
    """The cloth's welds welds() leaves out, with why."""
    out, seen = [], set()
    for obj in objects(coll):
        w = obj.ree_gpuc_weld
        why = problems(obj, coll)
        key = frozenset((w.part_a, w.part_b))
        if not why and key in seen:
            why = ["an earlier weld joins the same two Parts"]
        elif not why:
            seen.add(key)
        if why:
            out.append((obj, why[0]))
    return out


def collection(coll):
    """The cloth's welds collection (made on first use; white, after its other collections)."""
    name = f"GPUC Welds - {coll.name}"
    sub = next((c for c in coll.children if c.name == name), None)
    if sub is None:
        sub = bpy.data.collections.new(name)
        coll.children.link(sub)
    sub.color_tag = "NONE"
    return sub


def new(coll, part_a, part_b, particles=None, tail=(1.0, 1.0, 1.0, props.HI_MAX), link_type=4):
    """A weld object for the pairs (particle of part_a, particle of part_b), at their rest centre; for Parts made
    from bones (particles None) their shared bones' (pairs())."""
    from . import canon, generate
    a, b = part_a.ree_gpuc_part.part_name, part_b.ree_gpuc_part.part_name
    obj = bpy.data.objects.new(generate.unique_name("WELD_", f" - {a} > {b}"), None)
    obj["TYPE"] = TYPE
    obj.empty_display_type, obj.empty_display_size = "PLAIN_AXES", 0.03
    obj.hide_render = True
    collection(coll).objects.link(obj)
    head = props.header(coll)
    if head is not None:
        props.place_under(obj, head)
    if particles is None:
        particles = shared_pairs(part_a, part_b)
    co = canon.load(part_a)["co"]
    if particles:
        obj.location = tuple(np.asarray(co, float)[[ia for ia, _ in particles]].mean(axis=0))
    obj.lock_location = obj.lock_rotation = obj.lock_scale = (True, True, True)
    obj[PAIRS_KEY] = [int(v) for p in particles for v in p]
    w = obj.ree_gpuc_weld
    w.part_a, w.part_b = part_a, part_b
    w.k, w.k2, w.lo, w.hi = tail[0], tail[1], tail[2], min(tail[3], props.HI_MAX)
    w.link_type = link_type
    if EDITED_KEY in obj.keys():                # the file's own tails until one is set by hand
        del obj[EDITED_KEY]
    return obj


def _selected_parts(context) -> list:
    return [o for o in context.selected_objects if props.is_part(o)]


class REE_GPUC_OT_weld_parts(bpy.types.Operator):
    """Weld the two selected Parts at the bones they share: each shared bone's particle on both Parts is held on
    one point. The weld follows the Parts when they are regenerated"""
    bl_idname = "ree_gpuc.weld_parts"
    bl_label = "Weld Parts"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return context.mode == "OBJECT" and len(_selected_parts(context)) == 2

    def execute(self, context):
        from . import canon, fileio
        coll = next((c for c in bpy.data.collections if props.is_gpuc_collection(c)
                     and all(o.name in c.all_objects for o in _selected_parts(context))), None)
        if coll is None:
            self.report({"ERROR"}, "The two Parts are not of one cloth")
            return {"CANCELLED"}
        if any(canon.locked(o) for o in _selected_parts(context)):
            self.report({"ERROR"}, "A cloth that keeps a file's structure cannot take a new weld")
            return {"CANCELLED"}
        order = fileio.by_name(props.parts(coll))
        later, earlier = sorted(_selected_parts(context), key=order.index, reverse=True)   # links go from the later Part to the earlier
        if any({o.ree_gpuc_weld.part_a, o.ree_gpuc_weld.part_b} == {later, earlier} for o in objects(coll)):
            self.report({"ERROR"}, f"{later.name} and {earlier.name} are welded already")
            return {"CANCELLED"}
        if not shared_pairs(later, earlier):
            self.report({"ERROR"}, f"{later.name} and {earlier.name} share no bone: a weld joins each bone's "
                                   f"particle on both")
            return {"CANCELLED"}
        obj = new(coll, later, earlier)
        self.report({"INFO"}, f"{obj.name}: {len(pairs(obj))} welded particle pairs")
        return {"FINISHED"}


class REE_GPUC_OT_select_weld(bpy.types.Operator):
    """Edit the two welded Parts with the weld's particles selected"""
    bl_idname = "ree_gpuc.select_weld"
    bl_label = "Select Welded Particles"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        w = context.object.ree_gpuc_weld if is_weld(context.object) else None
        return w is not None and props.is_part(w.part_a) and props.is_part(w.part_b)

    def execute(self, context):
        obj = context.object
        w = obj.ree_gpuc_weld
        if context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        chosen = {w.part_a.name: {ia for ia, _ in pairs(obj)}, w.part_b.name: {ib for _, ib in pairs(obj)}}
        for o in context.view_layer.objects.selected:
            o.select_set(False)
        for part in (w.part_a, w.part_b):
            me, keep = part.data, chosen[part.name]
            for v in me.vertices:
                v.select = v.index in keep
            for e in me.edges:                       # edges and faces too: edit mode flushes theirs to the verts
                e.select = e.vertices[0] in keep and e.vertices[1] in keep
            for f in me.polygons:
                f.select = all(i in keep for i in f.vertices)
            part.select_set(True)
        context.view_layer.objects.active = w.part_a
        both = [w.part_a, w.part_b]                  # the panel's context still names the weld: the parts here
        with context.temp_override(object=w.part_a, active_object=w.part_a, selected_objects=both,
                                   selected_editable_objects=both):
            bpy.ops.object.mode_set(mode="EDIT")
        return {"FINISHED"}


class REE_GPUC_PT_object_weld(_ObjectPanel, bpy.types.Panel):
    bl_label = "GPUC Weld"
    KIND = staticmethod(is_weld)

    def draw(self, context):
        obj = context.object
        w = obj.ree_gpuc_weld
        why = problems(obj)
        if why:
            self.layout.label(text=f"Not exported: {why[0]}", icon="ERROR")
        col = _split(self.layout)
        col.prop(w, "part_a")
        col.prop(w, "part_b")
        col.label(text=f"{len(pairs(obj))} welded particle pairs")
        col.separator()
        col.prop(w, "k")
        col.prop(w, "k2")
        row = col.row(align=True)
        row.prop(w, "lo", text="Range")
        row.prop(w, "hi", text="")
        sub = col.column()
        sub.active = False
        sub.prop(w, "link_type")
        self.layout.operator("ree_gpuc.select_weld", icon="RESTRICT_SELECT_OFF")


CLASSES = (REE_GPUC_Weld, REE_GPUC_OT_weld_parts, REE_GPUC_OT_select_weld, REE_GPUC_PT_object_weld)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Object.ree_gpuc_weld = PointerProperty(type=REE_GPUC_Weld)


def unregister():
    del bpy.types.Object.ree_gpuc_weld
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)

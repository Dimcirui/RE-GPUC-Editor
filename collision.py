"""Builds the .gpuc's own collision capsules (section 16: two joints, an offset and a radius at each end) as the
RE Chain Editor builds a chain2 capsule, and keeps their objects and values in sync.

Maintenance constraints:
- A ROOT object holds the values, with a BEGIN and an END object held on their joints by a Child Of constraint
  named ``BoneName``: an end's location is its offset from the joint, its scale its radius.
- The ends are authoritative: ``sync`` copies them into the ROOT's values; ``apply`` moves them when a value is
  set. A sphere is a capsule whose two ends meet."""
import bpy
import numpy as np
from mathutils import Matrix, Vector

from . import props

ROOT_TYPE, BEGIN_TYPE, END_TYPE = "RE_GPUC_CAPSULE_ROOT", "RE_GPUC_CAPSULE_BEGIN", "RE_GPUC_CAPSULE_END"
_busy = False


def is_root(obj) -> bool:
    return obj is not None and obj.get("TYPE") == ROOT_TYPE


def root_of(obj):
    """The capsule a ROOT, BEGIN or END object belongs to, else None."""
    if obj is None:
        return None
    if is_root(obj):
        return obj
    if obj.get("TYPE") in (BEGIN_TYPE, END_TYPE) and is_root(obj.parent):
        return obj.parent
    return None


def ends(root):
    """(BEGIN, END) objects of a capsule."""
    kids = {c.get("TYPE"): c for c in root.children}
    return kids.get(BEGIN_TYPE), kids.get(END_TYPE)


def roots(scene) -> list:
    return [o for o in scene.objects if is_root(o)]


def collection(coll):
    """The cloth's collisions collection (made on first use, as the chain editor's Chain Collisions - <chain>)."""
    name = f"GPUC Collisions - {coll.name}"
    sub = next((c for c in coll.children if c.name == name), None)
    if sub is None:
        sub = bpy.data.collections.new(name)
        coll.children.link(sub)
    sub.color_tag = "NONE"                         # white, as the chain editor's collision collections
    return sub


def sim_collection(coll):
    """The cloth's collection of the preview's objects (the forces, the test colliders; made on first use,
    white, above its collisions collection). Objects of those kinds lying in the cloth's collection itself move
    into it."""
    name = f"GPUC Simulation Objects - {coll.name}"
    sub = next((c for c in coll.children if c.name == name), None)
    if sub is None:
        sub = bpy.data.collections.new(name)
        coll.children.link(sub)
        below = [c for prefix in ("GPUC Collisions - ", "GPUC Welds - ") for c in coll.children
                 if c.name.startswith(prefix)]
        for c in below:                            # children keep their link order: relinked, they go after
            coll.children.unlink(c)
            coll.children.link(c)
    sub.color_tag = "NONE"
    for obj in list(coll.objects):
        if props.is_force(obj) or props.is_collider(obj):
            sub.objects.link(obj)
            coll.objects.unlink(obj)
    return sub


def _end(name, kind, root, arm, bone, offset, radius, coll, cloth=None):
    from . import reference
    obj = bpy.data.objects.new(name, None)
    obj["TYPE"] = kind
    obj.empty_display_type, obj.empty_display_size = "SPHERE", 1.0
    obj.hide_render = True
    coll.objects.link(obj)
    obj.parent = root
    c = obj.constraints.new("CHILD_OF")
    owner = reference.bone_owner(cloth, arm, bone)            # the armature's bone, else the reference skeleton's
    c.name, c.target = "BoneName", owner or arm
    c.subtarget = bone if owner is not None else ""           # unknown: the armature's origin
    c.use_scale_x = c.use_scale_y = c.use_scale_z = False
    c.set_inverse_pending = False
    c.inverse_matrix.identity()
    obj.location = offset
    obj.scale = (radius,) * 3
    return obj


def unresolved(name: str) -> bool:
    return name.startswith("HASH_")


def new_capsule(coll, arm, bone_a, offset_a, radius_a, bone_b, offset_b, radius_b, hash_a=0, hash_b=0, *, shape=None):
    """A capsule from bone_a (+ offset_a, radius_a) to bone_b (+ offset_b, radius_b) in the cloth's collisions
    collection, named as the chain editor names its collisions. An end whose joint the armature lacks (an
    import: its name HASH_<hash>, its hash kept) is held at the armature's origin until a bone is set."""
    from .generate import unique_name
    sub = collection(coll)
    sphere = shape == "SPHERE" if shape is not None else bone_a == bone_b and Vector(offset_a) == Vector(offset_b)
    shape = "SPHERE" if sphere else "CAPSULE"
    label = bone_a if sphere else f"{bone_a} > {bone_b}"
    root = bpy.data.objects.new(unique_name("COL_", f"_{shape} - {label}"), None)
    root["TYPE"] = ROOT_TYPE
    root.empty_display_type, root.empty_display_size = "PLAIN_AXES", 0.0
    root.hide_render = True
    sub.objects.link(root)
    header = props.header(coll)
    if header is not None:
        props.place_under(root, header)
        if arm is None:          # a header with no armature holds game space: the ends in the bones' armature space
            from . import reference
            root.matrix_parent_inverse = Matrix(np.linalg.inv(reference.TO_ARMATURE).tolist())
    prefix = root.name.split(" - ")[0]
    _end(f"{prefix}_BEGIN {bone_a}", BEGIN_TYPE, root, arm, bone_a, offset_a, radius_a, sub, coll)
    _end(f"{prefix}_END {bone_b}", END_TYPE, root, arm, bone_b, offset_b, radius_b, sub, coll)
    sync(root)
    c = root.ree_gpuc_capsule
    c.hash_a, c.hash_b = hash_a, hash_b
    return root


def bound(root) -> bool:
    """Whether both ends are on a bone of the armature."""
    a, b = ends(root)
    return all(e is not None and e.constraints.get("BoneName") is not None and e.constraints["BoneName"].subtarget
               for e in (a, b))


def geometry(scene) -> list:
    """Every visible capsule as (root, begin centre, end centre, begin radius, end radius), world space."""
    out = []
    for root in roots(scene):
        a, b = ends(root)
        if a is None or b is None or not (a.visible_get() or b.visible_get()):
            continue
        ma, mb = np.array(a.matrix_world, float), np.array(b.matrix_world, float)
        ra = float(np.cbrt(abs(np.linalg.det(ma[:3, :3]))))
        rb = float(np.cbrt(abs(np.linalg.det(mb[:3, :3]))))
        out.append((root, ma[:3, 3].copy(), mb[:3, 3].copy(), ra, rb))
    return out


def _radius(obj) -> float:
    m = np.array(obj.matrix_world, float)
    return float(np.cbrt(abs(np.linalg.det(m[:3, :3]))))


def reference_clsp(scene):
    """The active cloth's Reference CLSP collection (on its header), else None."""
    h = props.header(scene.ree_gpuc.target)
    return h.ree_gpuc_header.reference_clsp if h is not None else None


def clsp_shapes(coll) -> list:
    """The visible shapes of a .clsp the RE Chain Editor imported, in its order (by name: COL_00, ...): (name,
    shape SPHERE or CAPSULE, begin centre, end centre, begin radius, end radius, CLSP flag A, flag B), world
    space; its spheres (``RE_CHAIN_COLLISION_SINGLE``) and capsules (a ROOT with BEGIN and END objects). The
    chain editor keeps a radius as its object's scale."""
    out = []
    if coll is None:
        return out
    for obj in sorted(coll.all_objects, key=lambda o: o.name):
        kind = obj.get("TYPE")
        if kind not in {"RE_CHAIN_COLLISION_SINGLE", "RE_CHAIN_COLLISION_CAPSULE_ROOT"}:
            continue
        c = obj.re_chain_chaincollision if hasattr(obj, "re_chain_chaincollision") else None
        flags = (int(c.clspBitFlag0), int(c.clspBitFlag1)) if c is not None else (-1, -1)
        if kind == "RE_CHAIN_COLLISION_SINGLE":
            if not obj.visible_get():
                continue
            p, r = np.array(obj.matrix_world, float)[:3, 3].copy(), _radius(obj)
            out.append((obj.name, "SPHERE", p, p, r, r) + flags)
            continue
        kids = {k.get("TYPE"): k for k in obj.children}
        a, b = kids.get("RE_CHAIN_COLLISION_CAPSULE_START"), kids.get("RE_CHAIN_COLLISION_CAPSULE_END")
        if a is None or b is None or not (a.visible_get() or b.visible_get()):
            continue
        ra = _radius(a)
        tapered = c is not None and str(getattr(c, "chainCollisionShape", "")) == "5"
        rb = _radius(b) if tapered else ra
        out.append((obj.name, "CAPSULE", np.array(a.matrix_world, float)[:3, 3].copy(),
                    np.array(b.matrix_world, float)[:3, 3].copy(), ra, rb) + flags)
    return out


def sync(root) -> None:
    """The ROOT's values from its ends (what the viewport shows)."""
    global _busy
    a, b = ends(root)
    if a is None or b is None:
        return
    c = root.ree_gpuc_capsule
    _busy = True
    try:
        for end, joint, offset, radius in ((a, "joint_a", "offset_a", "radius_a"), (b, "joint_b", "offset_b", "radius_b")):
            con = end.constraints.get("BoneName")
            if con is not None and con.subtarget and getattr(c, joint) != con.subtarget:
                setattr(c, joint, con.subtarget)
            if np.float32(getattr(c, offset)).tobytes() != np.float32(end.location).tobytes():   # -0.0 kept too
                setattr(c, offset, tuple(end.location))
            if np.float32(getattr(c, radius)).tobytes() != np.float32(end.scale[0]).tobytes():
                setattr(c, radius, end.scale[0])
    finally:
        _busy = False


def apply(capsule) -> None:
    """The ends from the ROOT's values (a value set in the panel)."""
    if _busy:
        return
    from . import generate, reference
    root = capsule.id_data
    cloth = reference.cloth_of(root)
    arm = generate._armature(cloth) if cloth is not None else None
    a, b = ends(root)
    for end, joint, offset, radius in ((a, capsule.joint_a, capsule.offset_a, capsule.radius_a),
                                       (b, capsule.joint_b, capsule.offset_b, capsule.radius_b)):
        if end is None:
            continue
        con = end.constraints.get("BoneName")
        if con is not None:
            owner = reference.bone_owner(cloth, arm, joint) if cloth is not None else None
            if owner is None and con.target is not None and con.target.type == "ARMATURE"                     and joint in con.target.data.bones:
                owner = con.target
            if owner is not None and con.target != owner:
                con.target = owner
            want = joint if owner is not None else ""
            if con.subtarget != want:
                con.subtarget = want
        end.location = offset
        end.scale = (radius,) * 3


@bpy.app.handlers.persistent
def _on_depsgraph(scene, depsgraph):
    if _busy:
        return
    done = set()
    for upd in depsgraph.updates:
        root = root_of(upd.id.original) if isinstance(upd.id.original, bpy.types.Object) else None
        if root is not None and root.name not in done and upd.is_updated_transform:
            done.add(root.name)
            sync(root)


def register():
    bpy.app.handlers.depsgraph_update_post.append(_on_depsgraph)


def unregister():
    if _on_depsgraph in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(_on_depsgraph)

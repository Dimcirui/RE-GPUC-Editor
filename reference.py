"""The reference skeleton: where the joints a cloth names but its mesh's armature lacks (the
own capsules' ends, a Part's root joint: body bones the game finds on the character's full skeleton) sit in the
editor. From data/skeletons.json (made from the vanilla .fbxskel files: their bone names, hashes,
parents and rest matrices; positions only, not the files): ch03_000_9000's bones where it has them, a bone only
another skeleton has put under its parent there by its own offset from that parent.

Each cloth that needs one gets an armature of those bones (GPUC Reference Skeleton - <cloth>, hidden, in its
Simulation Objects collection, under the mesh's armature, its data in the armature's space: the table's game
space turned +90 degrees about X, as the RE Mesh Editor turns a mesh's armature). A bone the mesh's
armature has copies that bone (world space), rest and pose: a bone it lacks follows its parent with the vanilla
offset. So a mesh whose armature was re-zeroed into a T-pose carries the missing leg and arm joints into that
pose too; a joint whose own ancestors the mesh lacks keeps the vanilla pose (said at import)."""
import json
import math
import os

import bpy
import numpy as np
from mathutils import Matrix

from . import collision, props

TYPE = "RE_GPUC_REFERENCE_SKELETON"
BASE = "ch03_000_9000"
# the table's game space (y up) into a mesh armature's data space (z up: the RE Mesh Editor turns it in)
TO_ARMATURE = np.array(Matrix.Rotation(math.radians(90.0), 4, "X"), float)
_bones: list = []


def bones() -> list:
    """[(name, hash, parent index or -1, rest 4x4)], parents first: ch03_000_9000's, then each other skeleton's
    bones it lacks (under their parent here, by their own offset from it)."""
    if _bones:
        return _bones
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "skeletons.json")
    data = json.load(open(path, encoding="utf-8"))
    keys = [BASE] + sorted(k for k in data if k != BASE)
    index = {}
    for key in keys:
        own = data[key]
        mats = [np.array(b[3], float).reshape(4, 4) for b in own]
        for k, (name, h, parent, _) in enumerate(own):
            up = own[parent][0] if parent >= 0 else None
            if name in index or up is not None and up not in index:
                continue                                   # placed already, or its parent is not: left out
            m = mats[k] if key == BASE or up is None else _bones[index[up]][3] @ np.linalg.inv(mats[parent]) @ mats[k]
            index[name] = len(_bones)
            _bones.append((name, int(h), index[up] if up is not None else -1, m))
    return _bones


def by_hash() -> dict:
    return {h: name for name, h, _, _ in bones()}


def armature(coll):
    """The cloth's reference skeleton, else None."""
    return next((o for o in coll.all_objects if o.get("TYPE") == TYPE), None) if coll is not None else None


def ensure(coll, arm):
    """The cloth's reference skeleton under arm (made when missing or on another armature). With no armature (a
    Keep Structure import with no mesh) under the cloth's header, whose space is the game's: the vanilla pose."""
    ref = armature(coll)
    under = arm if arm is not None else props.header(coll)
    if ref is not None and ref.parent == under:
        return ref
    if ref is not None:
        data = ref.data
        bpy.data.objects.remove(ref)
        bpy.data.armatures.remove(data)
    name = f"GPUC Reference Skeleton - {coll.name}"
    ref = bpy.data.objects.new(name, bpy.data.armatures.new(name))
    ref["TYPE"] = TYPE
    ref.hide_render = True
    ref.data.display_type = "STICK"
    collision.sim_collection(coll).objects.link(ref)
    ref.parent = under
    if arm is None:                                    # the header's game space into the bones' armature space
        ref.matrix_parent_inverse = Matrix(np.linalg.inv(TO_ARMATURE).tolist())
    vl = bpy.context.view_layer
    active, mode = vl.objects.active, bpy.context.mode
    if mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    vl.objects.active = ref
    bpy.ops.object.mode_set(mode="EDIT")
    eb = ref.data.edit_bones
    made = []
    for name, _, parent, m in bones():
        b = eb.new(name)
        b.head, b.tail = (0.0, 0.0, 0.0), (0.0, 0.03, 0.0)
        b.matrix = Matrix((TO_ARMATURE @ m).tolist())
        b.length = 0.03
        made.append((b, parent))
    for b, parent in made:
        if parent >= 0:
            b.parent = made[parent][0]
    bpy.ops.object.mode_set(mode="OBJECT")
    for pb in ref.pose.bones if arm is not None else ():
        if pb.name in arm.data.bones:
            c = pb.constraints.new("COPY_TRANSFORMS")
            c.target, c.subtarget = arm, pb.name
    vl.objects.active = active
    ref.hide_set(True)
    ref.hide_select = True
    return ref


def pose_offset(arm) -> tuple:
    """How far the armature's rest pose turns from the reference's on the bones both have: (bones over 5 degrees,
    the most in degrees)."""
    turned, most = 0, 0.0
    for name, _, _, m in bones():
        b = arm.data.bones.get(name)
        if b is None:
            continue
        r = np.array(b.matrix_local, float)[:3, :3] @ (TO_ARMATURE @ m)[:3, :3].T
        angle = math.degrees(math.acos(max(-1.0, min(1.0, (np.trace(r) - 1) / 2))))
        turned += angle > 5.0
        most = max(most, angle)
    return turned, most


def resolve(coll, arm, hashes) -> tuple:
    """Names for hashes the armature (arm, None with no mesh) lacks: ({hash: name} of the ones the reference
    knows, notes). Makes the
    cloth's reference skeleton when one is needed. MH Wilds cloths only: the table is Wilds' skeletons (RE9's
    vanilla .fbxskel files hold the head and hair alone), so a RE9 cloth keeps such joints by their hashes."""
    head = props.header(coll)
    if head is not None and head.ree_gpuc_header.game != props.GAMES[0][0]:
        return {}, []
    names = by_hash()
    have = {b.name for b in arm.data.bones} if arm is not None else set()
    found = {h: names[h] for h in set(hashes) if h in names and names[h] not in have}
    notes = []
    if found:
        ensure(coll, arm)
        turned, most = pose_offset(arm) if arm is not None else (0, 0.0)
        lacks = "the mesh's armature lacks" if arm is not None else "named by hash (no mesh)"
        notes.append(f"{len(found)} joints {lacks} placed by the reference skeleton ({BASE}): "
                     f"{', '.join(sorted(found.values())[:4])}{', ...' if len(found) > 4 else ''}")
        if turned:
            notes.append(f"the armature's rest pose turns from the reference on {turned} bones (up to {most:.0f} "
                         f"degrees, a T-pose?): the joints it lacks follow their nearest bone it has")
    return found, notes


def bone_owner(coll, arm, name):
    """The armature that has the bone: arm's own first, else the cloth's reference skeleton; None."""
    if arm is not None and arm.type == "ARMATURE" and name in arm.data.bones:
        return arm
    ref = armature(coll)
    return ref if ref is not None and name in ref.data.bones else None


def is_reference(obj) -> bool:
    return obj is not None and obj.get("TYPE") == TYPE


def cloth_of(obj):
    return next((c for c in bpy.data.collections if props.is_gpuc_collection(c) and obj.name in c.all_objects), None)

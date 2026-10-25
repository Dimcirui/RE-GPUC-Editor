"""Keep generated Parts in sync with committed rest-bone changes.

Edit-mode changes wait for the armature to leave Edit Mode. Pose/animation updates with unchanged rest
heads do no work. Failed or locked Parts keep their geometry and show the problem in the Part panel.
"""
import bpy
import numpy as np

from . import canon, generate, props, sim

_busy = False
_snapshots = {}
_pending_arms = set()
_pending_parts = set()


def _status(obj, text):
    if obj.get("ree_gpuc_check") != text:
        obj["ree_gpuc_check"] = text


def _refresh(scene, objects):
    global _busy
    if _busy:
        return
    _busy = True
    try:
        for obj in objects:
            if not props.is_part(obj) or not canon.has(obj):
                continue
            arm = obj.ree_gpuc_part.armature
            if (arm is not None and arm.type == "ARMATURE" and arm.data.is_editmode) or obj.mode != "OBJECT":
                _pending_parts.add(obj.name)
                continue
            _pending_parts.discard(obj.name)
            try:
                problems = generate.check(obj)
                if problems and not canon.locked(obj):
                    if (arm is not None and arm.type == "ARMATURE"
                            and all(n in arm.data.bones for n in canon.load(obj)["nodes"])):
                        generate.regenerate(obj)
                        problems = generate.check(obj)
                _status(obj, "; ".join(problems) if problems else "ok")
            except Exception as e:  # one failed Part must not disable the listener or block other Parts
                _status(obj, f"Automatic rebuild failed: {e}")
    finally:
        _busy = False


def _snapshot(arm):
    bones = arm.data.bones
    co = np.empty(len(bones) * 3, np.float32)
    bones.foreach_get("head_local", co)
    return tuple(b.name for b in bones), co.tobytes(), tuple(v for row in arm.matrix_world for v in row)


@bpy.app.handlers.persistent
def _on_depsgraph(scene, depsgraph):
    if _busy:
        return
    arms = set(_pending_arms)
    parts = set(_pending_parts)
    for upd in depsgraph.updates:
        item = upd.id.original
        if isinstance(item, bpy.types.Armature):
            arms.update(o.name for o in scene.objects if o.type == "ARMATURE" and o.data == item)
        elif isinstance(item, bpy.types.Object):
            if item.type == "ARMATURE":
                arms.add(item.name)
            elif props.is_part(item):
                parts.add(item.name)
    for name in arms:
        arm = scene.objects.get(name)
        if arm is None or arm.type != "ARMATURE":
            _pending_arms.discard(name)
            _snapshots.pop(name, None)
            continue
        if arm.data.is_editmode:
            _pending_arms.add(name)
            continue
        _pending_arms.discard(name)
        snapshot = _snapshot(arm)
        if _snapshots.get(name) == snapshot:
            continue
        _snapshots[name] = snapshot
        parts.update(o.name for o in scene.objects if props.is_part(o) and o.ree_gpuc_part.armature == arm)
    _refresh(scene, [scene.objects[n] for n in parts if n in scene.objects])
    _pending_parts.intersection_update(scene.objects.keys())


@bpy.app.handlers.persistent
def _on_load(*_):
    _snapshots.clear()
    _pending_arms.clear()
    _pending_parts.clear()


@bpy.app.handlers.persistent
def _on_history(*_):
    _on_load()
    scene = bpy.context.scene
    objects = [o for o in scene.objects if props.is_part(o)]
    for obj in objects:
        sim.invalidate(obj)
    _refresh(scene, objects)


def register():
    bpy.app.handlers.depsgraph_update_post.append(_on_depsgraph)
    bpy.app.handlers.load_post.append(_on_load)
    bpy.app.handlers.undo_post.append(_on_history)
    bpy.app.handlers.redo_post.append(_on_history)


def unregister():
    for handlers, callback in ((bpy.app.handlers.depsgraph_update_post, _on_depsgraph),
                               (bpy.app.handlers.load_post, _on_load),
                               (bpy.app.handlers.undo_post, _on_history),
                               (bpy.app.handlers.redo_post, _on_history)):
        if callback in handlers:
            handlers.remove(callback)
    _on_load()

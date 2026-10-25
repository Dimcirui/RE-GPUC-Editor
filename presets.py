"""Versioned JSON parameter presets, independent of scene references and generated geometry."""
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile

import bpy
from bpy.props import BoolProperty, EnumProperty, PointerProperty, StringProperty

from . import collision, props
from .modules import re4

VERSION = 1
# UI identifier, label, object TYPE, property group
KINDS = (
    ("HEADER", "Header", props.HEADER_TYPE, "ree_gpuc_header"),
    ("CONFIG", "Config", props.CONFIG_TYPE, "ree_gpuc_config"),
    ("PART", "Part", props.PART_TYPE, "ree_gpuc_part"),
    ("COLLISION", "Collision", collision.ROOT_TYPE, "ree_gpuc_capsule"),
    ("WELD", "Weld", "RE_GPUC_WELD", "ree_gpuc_weld"),
    ("FORCE", "Preview Force", props.FORCE_TYPE, "ree_gpuc_force"),
    ("COLLIDER", "Preview Collider", props.COLLIDER_TYPE, "ree_gpuc_collider"),
)
GROUPS = {key: group for key, _, _, group in KINDS}
TYPE_KIND = {typ: key for key, _, typ, _ in KINDS}
PREVIEW = {"FORCE", "COLLIDER"}
PART_FIELDS = {"friction", "bounce", "stickiness", "surface_area", "plane_bits", "sphere_bits", "capsule_bits",
               "obb_bits", "clsp_flags_a", "clsp_flags_b", "vertical", "ring", "shear", "bend"}
# Keep every dynamic enum string alive until unregister, including lists from previously selected types.
_enum_cache = {}


def root_directory():
    # User data survives extension replacement/upgrades. Merely drawing the panel does not create folders.
    return Path(bpy.utils.user_resource("SCRIPTS")) / "presets" / "ree_gpuc_editor"


def owner(obj):
    return collision.root_of(obj) or obj


def kind_of(obj):
    obj = owner(obj)
    return TYPE_KIND.get(obj.get("TYPE")) if obj is not None else None


def cloth_game(obj, scene):
    from . import reference
    obj = owner(obj)
    head = obj if props.is_header(obj) else props.header(reference.cloth_of(obj)) if obj else None
    head = head or props.header(scene.ree_gpuc.target)
    return head.ree_gpuc_header.game if head else props.GAMES[0][0]


def game_of(obj, scene):
    return "ANY" if kind_of(obj) in PREVIEW else cloth_game(obj, scene)


def context_game(settings, context):
    return "ANY" if settings.kind in PREVIEW else cloth_game(context.active_object, context.scene)


def directory(kind, game):
    if kind not in GROUPS or game not in {"ANY", *(g[0] for g in props.GAMES)}:
        raise ValueError("Unsupported preset type or game")
    return root_directory() / (game.lstrip(".") if game != "ANY" else "preview") / kind.lower()


def valid_name(name):
    name = name.strip()
    if (not name or name == "NONE" or len(name) > 100 or name.endswith((".", " ")) or ".." in name
            or re.search(r'[<>:"/\\|?*\x00-\x1f]', name)
            or name.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
                                               *(f"LPT{i}" for i in range(1, 10))}):
        raise ValueError("Enter a valid preset name (no path separators or reserved file names)")
    return name


def path_for(kind, game, name):
    return directory(kind, game) / (valid_name(name) + ".json")


def fields(kind, game):
    if game == re4.EXT:
        return {"HEADER": {"max_velocity"}, "CONFIG": set(re4.CONFIG),
                "PART": set(re4.PART) | {"vertical", "ring", "shear", "bend"}}.get(kind,
                    {"k", "k2", "lo", "hi"} if kind == "WELD" else set())
    return {
        "HEADER": {f[0] for f in props.HEADER_FIELDS},
        "CONFIG": {f[0] for f in props.CONFIG_FIELDS} | {f[0] for f in props.CONFIG_UNKNOWNS}
                  | {"info_state", "translation_curve", "rotation_curve"},
        "PART": PART_FIELDS,
        "COLLISION": {"offset_a", "radius_a", "offset_b", "radius_b"},
        "WELD": {"k", "k2", "lo", "hi", "link_type"},
        "FORCE": {"kind", "enabled", "accel", "lo", "hi", "period"},
        "COLLIDER": {"kind", "radius", "length", "size", "plane_size"},
    }[kind]


def snapshot(obj, scene):
    from . import ops
    obj = owner(obj)
    kind, game = kind_of(obj), game_of(obj, scene)
    if kind is None or not fields(kind, game):
        raise ValueError("This object does not support parameter presets")
    if kind == "COLLISION":
        collision.sync(obj)
    data = ops._to_dict(getattr(obj, GROUPS[kind]))
    data = {k: v for k, v in data.items() if k in fields(kind, game)}
    for curve in ("translation_curve", "rotation_curve"):
        if curve in data:
            data[curve].pop("active_key", None)
    return {"format": "ree_gpuc_editor.preset", "version": VERSION, "kind": kind, "game": game,
            "values": data}


def validate(pg, values, allowed=None):
    """Preflight the entire JSON before mutating any destination (including nested curve keys)."""
    if not isinstance(values, dict):
        raise ValueError("Preset values must be an object")
    for key, value in values.items():
        prop = pg.bl_rna.properties.get(key)
        if prop is None or key in {"rna_type", "name", "active_key"} or allowed is not None and key not in allowed:
            raise ValueError(f"Unsupported preset field: {key}")
        if prop.type == "POINTER":
            child = getattr(pg, key)
            if not isinstance(child, bpy.types.PropertyGroup):
                raise ValueError(f"Object references cannot be preset fields: {key}")
            validate(child, value)
        elif prop.type == "COLLECTION":
            if not isinstance(value, list) or key != "keys" or len(value) > 10000:
                raise ValueError(f"Invalid preset collection: {key}")
            # Validate against the registered key schema without adding any items to the target.
            schema = props.REE_GPUC_Key.bl_rna
            validate_key = type("KeySchema", (), {"bl_rna": schema})()
            for item in value:
                validate(validate_key, item)
        else:
            vals = value if getattr(prop, "array_length", 0) else [value]
            if getattr(prop, "array_length", 0) and (not isinstance(value, list) or len(value) != prop.array_length):
                raise ValueError(f"Invalid vector size: {key}")
            for v in vals:
                if prop.type == "BOOLEAN":
                    ok = isinstance(v, bool)
                elif prop.type in {"INT", "FLOAT"}:
                    ok = (type(v) in ({int} if prop.type == "INT" else {int, float}) and math.isfinite(v)
                          and prop.hard_min <= v <= prop.hard_max)
                elif prop.type == "ENUM":
                    ok = isinstance(v, str) and v in prop.enum_items.keys()
                else:
                    ok = prop.type == "STRING" and isinstance(v, str)
                if not ok or prop.is_readonly:
                    raise ValueError(f"Invalid value for preset field: {key}")


def save(obj, scene, name, overwrite=False):
    data = snapshot(obj, scene)
    data["name"] = valid_name(name)
    path = path_for(data["kind"], data["game"], name)
    payload = json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation by default; replacement is atomic and requires the dialog's overwrite option.
    if not overwrite:
        with path.open("x", encoding="utf-8") as f:
            f.write(payload)
    else:
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, suffix=".tmp", delete=False) as f:
                temp_path = Path(f.name)
                f.write(payload)
            os.replace(temp_path, path)
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)
    return path


def read(kind, game, name):
    path = path_for(kind, game, name)
    if path.stat().st_size > 4 * 1024 * 1024:
        raise ValueError("Preset file is too large")
    with path.open(encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict) or data.get("format") != "ree_gpuc_editor.preset" or data.get("version") != VERSION:
        raise ValueError("Unsupported preset format or version")
    if data.get("kind") != kind or data.get("game") != game:
        raise ValueError("Preset object type or game does not match")
    if not isinstance(data.get("values"), dict) or not data["values"]:
        raise ValueError("Preset contains no parameter values")
    return data


def apply(data, objects, scene):
    from . import canon, ops, sim
    kind, game = data["kind"], data["game"]
    targets = list({owner(o).name: owner(o) for o in objects if kind_of(o) == kind and game_of(o, scene) == game}.values())
    if not targets:
        raise ValueError("Select an object matching the preset type and game")
    for obj in targets:
        if obj.mode != "OBJECT":
            raise ValueError("Apply presets in Object Mode")
        validate(getattr(obj, GROUPS[kind]), data["values"], fields(kind, game))
    for obj in targets:
        if props.is_part(obj) and canon.has(obj):
            canon.sync(obj)  # Preserve particle values before any parameter callbacks.
    for obj in targets:
        ops._from_dict(getattr(obj, GROUPS[kind]), data["values"])
        if kind == "CONFIG":
            for curve in ("translation_curve", "rotation_curve"):
                getattr(obj.ree_gpuc_config, curve).active_key = 0
        sim.invalidate(obj)
    if kind in {"HEADER", "CONFIG", "COLLISION", "FORCE", "COLLIDER", "WELD"}:
        for obj in sim.parts(scene):
            sim.invalidate(obj)
    return len(targets)


def preset_items(self, context):
    if context is None:
        return [("NONE", "No saved presets", "", 0)]
    folder = directory(self.kind, context_game(self, context))
    try:
        names = tuple(sorted((p.stem for p in folder.glob("*.json") if p.is_file()), key=str.casefold))
    except OSError:
        names = ()
    key = (str(folder), names)
    if key not in _enum_cache:
        items = [("NONE", "Select a preset" if names else "No saved presets", "", 0)]
        used = {0}
        for name in names:
            number = int(hashlib.sha256(name.encode("utf-8")).hexdigest()[:7], 16) + 1
            while number in used:
                number += 1
            used.add(number)
            items.append((name, name, str(folder / (name + ".json")), number))
        _enum_cache[key] = items
    return _enum_cache[key]


def _kind_changed(self, context):
    self.preset = "NONE"
    if context is not None:
        self["_preset_game"] = context_game(self, context)


class REE_GPUC_PresetSettings(bpy.types.PropertyGroup):
    kind: EnumProperty(name="Object Type", items=[(key, label, "") for key, label, _, _ in KINDS],
                       default="CONFIG", update=_kind_changed)
    preset: EnumProperty(name="Preset", items=preset_items)


def follow_selection(obj, scene):
    kind = kind_of(obj)
    if kind and hasattr(scene, "ree_gpuc_presets"):
        s = scene.ree_gpuc_presets
        if s.kind != kind:
            s.kind = kind
        game = game_of(obj, scene)
        if s.get("_preset_game") != game:
            s.preset = "NONE"
            s["_preset_game"] = game


class REE_GPUC_OT_save_preset(bpy.types.Operator):
    """Save the active object's parameters; geometry and object references stay separate"""
    bl_idname = "ree_gpuc.save_preset"
    bl_label = "Save Active as Preset"
    preset_name: StringProperty(name="Name", default="New Preset")
    overwrite: BoolProperty(name="Overwrite existing preset", default=False)

    @classmethod
    def poll(cls, context):
        return (context.mode == "OBJECT" and kind_of(context.active_object) == context.scene.ree_gpuc_presets.kind
                and kind_of(context.active_object) is not None)

    def invoke(self, context, event):
        suggested = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", owner(context.active_object).name).strip(" .")[:100]
        try:
            self.preset_name = valid_name(suggested)
        except ValueError:
            self.preset_name = "New Preset"
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        try:
            path = save(context.active_object, context.scene, self.preset_name, self.overwrite)
            context.scene.ree_gpuc_presets.preset = path.stem
        except FileExistsError:
            self.report({"ERROR"}, "Preset already exists; choose a new name or enable Overwrite existing preset")
            return {"CANCELLED"}
        except (OSError, ValueError, TypeError) as e:
            self.report({"ERROR"}, f"Could not save preset: {e}")
            return {"CANCELLED"}
        self.report({"INFO"}, f"Saved preset: {path.stem}")
        return {"FINISHED"}


class REE_GPUC_OT_apply_preset(bpy.types.Operator):
    """Apply to selected objects matching the chosen type and game"""
    bl_idname = "ree_gpuc.apply_preset"
    bl_label = "Apply to Selected"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return context.mode == "OBJECT" and context.scene.ree_gpuc_presets.preset != "NONE"

    def execute(self, context):
        s = context.scene.ree_gpuc_presets
        try:
            count = apply(read(s.kind, context_game(s, context), s.preset), context.selected_objects, context.scene)
        except (OSError, ValueError, TypeError, KeyError) as e:
            self.report({"ERROR"}, f"Could not apply preset: {e}")
            return {"CANCELLED"}
        self.report({"INFO"}, f"Applied preset to {count} matching objects")
        return {"FINISHED"}


class REE_GPUC_OT_open_preset_folder(bpy.types.Operator):
    """Open the user preset directory; JSON files can be shared or removed here"""
    bl_idname = "ree_gpuc.open_preset_folder"
    bl_label = "Open Preset Folder"

    def execute(self, context):
        try:
            s = context.scene.ree_gpuc_presets
            folder = directory(s.kind, context_game(s, context))
            folder.mkdir(parents=True, exist_ok=True)
            bpy.ops.wm.path_open(filepath=str(folder))
        except (OSError, ValueError, RuntimeError) as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        return {"FINISHED"}


CLASSES = (REE_GPUC_PresetSettings, REE_GPUC_OT_save_preset, REE_GPUC_OT_apply_preset, REE_GPUC_OT_open_preset_folder)


def _initial_selection():
    """Run after registration: Blender enables add-ons inside RestrictBlend."""
    scene = getattr(bpy.context, "scene", None)
    if scene is not None:
        follow_selection(getattr(bpy.context, "active_object", None), scene)
    return None


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.ree_gpuc_presets = PointerProperty(type=REE_GPUC_PresetSettings)
    bpy.app.timers.register(_initial_selection, first_interval=0.0)


def unregister():
    if bpy.app.timers.is_registered(_initial_selection):
        bpy.app.timers.unregister(_initial_selection)
    del bpy.types.Scene.ree_gpuc_presets
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
    _enum_cache.clear()

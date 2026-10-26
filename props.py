"""Define the property groups the editor stores on Blender data: header, Config, Part, scene settings.

Particle values are not stored here: they live in the Part's canonical data (``canon``) and show on its mesh."""
import bpy
from bpy.props import (BoolProperty, EnumProperty, FloatProperty, FloatVectorProperty, IntProperty, PointerProperty,
                       StringProperty)

COLLECTION_TYPE = "RE_GPUC_COLLECTION"
HEADER_TYPE = "RE_GPUC_HEADER"


def is_header(obj) -> bool:
    return obj is not None and obj.get("TYPE") == HEADER_TYPE


def header(coll):
    return next((o for o in coll.all_objects if is_header(o)), None) if coll is not None else None


# The header's editable values; counts, offsets and the rest box are computed on export.
# (property, label, file offset, kind, known, default, description). Defaults are ch03_001_0002's.
# ``known`` False draws the value greyed out; 32-bit values show as signed integers (-1: all bits set).
HEADER_FIELDS = (
    ("calculate_mode", "Calculate Mode", 0x68, "E", True, "QUALITY",
     "Simulation steps per frame; used only when the component's CalculateMode is Default. Usually Quality or 0"),
    ("max_velocity", "Max Velocity", 0x78, "f", True, 1.0,
     "The most a particle moves per step (m; 0.1 is 6 m/s at 60 steps a second). Usually 0.1 to 1"),
    ("kernel_flags", "Kernel Flags", 0x8C, "I", True, 12, "Which kernels the cloth runs; use the button to tick them"),
    ("unk04", "Unkn Param 0", 0x04, "I", False, -386320729, "Effect unknown. Differs from file to file"),
    ("unk40", "Unkn Param 1", 0x40, "I", False, 0,
     "Effect unknown. Usually 0. A cloth that drives mesh vertices writes the mesh's LOD count here instead"),
    ("unk54", "Unkn Param 2", 0x54, "I", False, 0, "Effect unknown. Usually 0"),
    ("unk5c", "Unkn Param 3", 0x5C, "I", False, 0, "Effect unknown. Usually 0"),
    ("unk60", "Unkn Param 4", 0x60, "I", False, 0, "Effect unknown. Common values: 0, 1, 65536, 131073, 196608"),
    ("friction", "Friction", 0x6C, "f", False, 0.0,
     "The cloth's friction, passed to the cloth shader. Effect unknown. Usually 0"),
    ("unk70", "Unkn Param 6", 0x70, "I", False, 0, "Effect unknown. Usually 0"),
    ("unk74", "Unkn Param 7", 0x74, "I", False, 0, "Effect unknown. Usually 0"),
    ("unk7c", "Unkn Param 8", 0x7C, "I", False, 1, "Effect unknown. Usually 1"),
    ("unk80", "Unkn Param 9", 0x80, "I", False, 512, "Effect unknown. Usually 256, 512 or 1024"),
    # RE9 and Onimusha only: offsets in their 0xC0 headers (gpuc_params.RE9_ONLY)
    ("unk0c", "Unkn Param 12", 0x0C, "I", False, 0, "RE9 and Onimusha only. Effect unknown. Differs from file to file"),
    ("unk10", "Unkn Param 13", 0x10, "I", False, 0, "RE9 and Onimusha only. Effect unknown. Usually 0"),
    ("unk14", "Unkn Param 14", 0x14, "I", False, 0, "RE9 and Onimusha only. Effect unknown. Usually 0"),
    ("unk18", "Unkn Param 15", 0x18, "I", False, 0, "RE9 and Onimusha only. Effect unknown. Usually 0"),
    ("unk4c", "Unkn Param 16", 0x4C, "I", False, 0,
     "RE9 and Onimusha only. Effect unknown. Differs from file to file"),
)
# The games the editor writes: (file extension, name, description, number). A saved .blend stores the number:
# existing numbers must not change, and a new game takes an unused one.
GAMES = [(".241111760", "MH Wilds", "Monster Hunter Wilds, .gpuc.241111760", 0),
         (".250925365", "RE9", "Resident Evil Requiem, .gpuc.250925365", 1),
         (".260209505", "Onimusha", "Onimusha: Way of the Sword, .gpuc.260209505", 3),
         (".221108868", "RE4", "Resident Evil 4, .gpuc.221108868", 2)]
# A new RE9 / Onimusha header's values where that game's most common ones differ from HEADER_FIELDS' defaults
GAME_DEFAULTS = {".250925365": {"calculate_mode": "UNSET", "max_velocity": 0.1, "kernel_flags": 4, "unk7c": 0},
                 ".260209505": {"max_velocity": 0.1, "kernel_flags": 20}}
# via.dynamics.ClothCalculateMode (the file keeps the number)
CALCULATE_MODES = [
    ("UNSET", "0", "Value 0. Steps per frame unknown", 0),
    ("PERFORMANCE", "Performance", "Always 1 step a frame", 1),
    ("BALANCE", "Balance", "2 steps when a frame takes over 1.5 frames' time, else 1", 2),
    ("QUALITY", "Quality", "As Balance; any other difference is unknown", 3),
]

# What the cloth moves in game (Kernel Flags bit 2 on for joints, off for mesh vertices; see modules/vertex_drive)
DRIVE_MODES = [
    ("JOINTS", "Joints", "The cloth moves the bones its Parts are made from; the mesh follows them by its weights"),
    ("VERTICES", "Mesh Vertices",
     "The cloth moves the mesh's vertices near it directly, in every LOD. The export reads them from the exported "
     ".mesh, so export the mesh first and the .gpuc again after each mesh export"),
]
VERTEX_SELECTIONS = [
    ("DISTANCE", "By Distance", "The vertices of the exported .mesh within Drive Distance of the cloth"),
    ("REFERENCE", "Reference Mesh", "The vertices picked on the reference mesh (the exported .mesh imported as it "
                                    "is), in its GPUC_Drive vertex group"),
]


def _poll_reference(self, coll):
    return coll.get("ree_gpuc_reference") is not None


VERTEX_DRIVE_GAMES = {".241111760", ".250925365", ".260209505", ".221108868"}
EXPERIMENTAL_DRIVE = {".241111760", ".250925365"}       # Wilds and RE9: no game file of theirs drives vertices

CLSP_COLLECTION_TYPE = "RE_CLSP_COLLECTION"        # the RE Chain Editor's imported .clsp


def _redraw_any(self, context):
    _redraw(self, context)


def _poll_clsp(self, coll):
    return coll.get("~TYPE") == CLSP_COLLECTION_TYPE


REE_GPUC_Header = type("REE_GPUC_Header", (bpy.types.PropertyGroup,), {
    "__doc__": "The .gpuc's header values (counts and offsets are written on export)",
    "__annotations__": {
        "game": EnumProperty(name="Game", items=GAMES, update=_redraw_any,
                             description="The game the .gpuc is for: which header values it has and how it is "
                                         "exported"),
        "reference_clsp": PointerProperty(
            name="Reference CLSP", type=bpy.types.Collection, poll=_poll_clsp, update=_redraw_any,
            description="A .clsp the RE Chain Editor imported, which the cloth will collide with in game: its "
                        "shapes name the CLSP Flags' bits and the preview collides with the ones a Part takes "
                        "(not written to the .gpuc)"),
        # RE4 header values named by guess (a cloth made from bones; Keep Structure keeps the file's)
        "re4_unk44": IntProperty(name="Collision Iterations", default=0,
                                 description="RE4 header +0x44. Unconfirmed: collision iterations or quality, as it "
                                             "rises with the colliders in game files (0 to 3; 0 in 87 of 111)"),
        "re4_unk60": IntProperty(name="Solver Iterations", default=1,
                                 description="RE4 header +0x60. Unconfirmed: solver iterations or substeps, as main "
                                             "outfits take 8 to 16 (1 to 16 in game files; 1 in 81 of 111)"),
        "drive_mode": EnumProperty(name="Drive", items=DRIVE_MODES, default="JOINTS", update=_redraw_any,
                                   description="What the cloth moves in game"),
        # export settings of a cloth that drives mesh vertices (written to no file: how the export picks and binds)
        "drive_selection": EnumProperty(
            name="Vertex Selection", items=VERTEX_SELECTIONS, default="DISTANCE", update=_redraw_any,
            description="How the export picks the mesh vertices the cloth drives"),
        "drive_reference": PointerProperty(
            name="Reference Mesh", type=bpy.types.Collection, poll=_poll_reference, update=_redraw_any,
            description="The exported .mesh imported as it is (Import Reference Mesh): its GPUC_Drive vertex group "
                        "holds the driven vertices, and the export checks the cloth against its .mesh"),
        "drive_distance": FloatProperty(
            name="Drive Distance", default=0.02, min=0.0, soft_max=0.1, precision=3, unit="LENGTH",
            description="Mesh vertices closer than this to the cloth's rest surface are driven"),
        "drive_slots": IntProperty(
            name="Triangles per Vertex", default=4, min=1, max=8,
            description="How many nearby cloth triangles each driven vertex follows, the nearest weighted most. 1 "
                        "follows the nearest alone (creases may show where triangles meet); game files use 1 to 5"),
        **{name: (FloatProperty(name=label, default=default, precision=4, min=0.0, description=desc) if kind == "f"
                  else EnumProperty(name=label, items=CALCULATE_MODES, default=default, description=desc)
                  if kind == "E" else IntProperty(name=label, default=default, description=desc))
           for name, label, _, kind, _, default, desc in HEADER_FIELDS}}})
CONFIG_TYPE = "RE_GPUC_CONFIG"
PART_TYPE = "RE_GPUC_PART"
SCHEMA = 1


def is_config(obj) -> bool:
    return obj is not None and obj.get("TYPE") == CONFIG_TYPE


def is_part(obj) -> bool:
    return obj is not None and obj.type == "MESH" and obj.get("TYPE") == PART_TYPE


def is_gpuc_collection(coll) -> bool:
    return coll is not None and coll.get("~TYPE") == COLLECTION_TYPE


def parts(coll) -> list:
    return [o for o in coll.all_objects if is_part(o)] if coll is not None else []


def configs(coll) -> list:
    return [o for o in coll.all_objects if is_config(o)] if coll is not None else []


def is_re4(coll) -> bool:
    h = header(coll)
    return h is not None and h.ree_gpuc_header.game == ".221108868"


def is_re4_object(obj) -> bool:
    return obj is not None and any(is_re4(c) for c in obj.users_collection)



def _redraw(self, context):
    for window in context.window_manager.windows:
        for area in window.screen.areas:
            if area.type in {"VIEW_3D", "IMAGE_EDITOR"}:
                area.tag_redraw()


# The config block's fields: (property, label, kind, default). Labels are the names Wilds-Dynamics and physsim use;
# defaults are the mid-length skirt ch03_100_1005's.
CONFIG_FIELDS = (
    ("simulation_strength", "SimulationStrength", "f", 1.0), ("blend_weight", "BlendWeight", "f", 1.0),
    ("gravity_factor", "GravityFactor", "f", 0.75), ("velocity_damping", "VelocityDamping", "f", 0.16),
    ("second_damping", "Second damping", "?", False), ("second_velocity_damping", "Second VelocityDamping", "f", 0.05),
    ("second_damping_speed", "Second damping speed", "f", 3.0),
    ("overwrite_translation", "Overwrite translation", "?", False),
    ("blend_translation", "BlendAmountTranslation", "f", 0.0),
    ("overwrite_rotation", "Overwrite rotation", "?", False), ("blend_rotation", "BlendAmountRotation", "f", 0.0),
    ("use_speed_limit", "Limit speed", "?", False), ("speed_limit", "Speed limit", "f", 1.0),
    ("use_angular_speed_limit", "Limit angular speed", "?", False),
    ("angular_speed_limit", "Angular speed limit", "f", 10.0),
    ("self_collision", "Self collision", "?", False),
    ("translation_speed", "Translation curve speed", "f", 50.0),
    ("rotation_speed", "Rotation curve speed", "f", 720.0),
)
# ConfigInfoState's named bits, for the bit dialog. The default 5 is ch03_100_1005's (SkinningCollision,
# SpaceLimitCylinder).
STATE_BITS = {0: "SkinningCollision", 1: "SpaceLimitSphere", 2: "SpaceLimitCylinder", 3: "LinearStrength",
              4: "HybridStrength"}
# The config block's values with no known effect, drawn greyed out: (property, label, default, description).
# Defaults are ch03_100_1005's.
CONFIG_UNKNOWNS = (
    ("unk00", "Unkn Param 0", 1277511368,
     "Effect unknown. The first Config usually has 1277511368; the others differ"),
    ("unk04", "Unkn Param 1", 0, "Effect unknown. Usually 0"),
    ("unk08", "Unkn Param 2", 0, "Effect unknown. Usually 0"),
    ("unk0c", "Unkn Param 3", 0, "Effect unknown. Usually 0"),
    ("unk10", "Unkn Param 4", 329497134, "Effect unknown. Differs per Config; Unkn Param 4 to 7 may form one ID"),
    ("unk14", "Unkn Param 5", 1193019816, "See Unkn Param 4"),
    ("unk18", "Unkn Param 6", 515622574, "See Unkn Param 4"),
    ("unk1c", "Unkn Param 7", 345486607, "See Unkn Param 4"),
    ("unk34", "Unkn Param 8", 0, "Effect unknown. Usually 0"),
)
CONFIG_DESCRIPTIONS = {
    "simulation_strength": "The particles' weight is min(1, this x their simulation weight). Usually 1",
    "blend_weight": "The cloth bones take this share of the simulation over the animation (the preview draws the "
                    "particles so). Usually 1 for skirts; some coats use 0.6",
    "second_damping": "Over Second damping speed a particle's velocity takes Second VelocityDamping instead",
    "second_velocity_damping": "The damping over Second damping speed. Usually 0.001 to 0.19",
    "second_damping_speed": "The speed (m/s) over which Second VelocityDamping applies. Usually 3 or 1.7",
    "self_collision": "Read by the game. Effect unknown. Usually off",
    "translation_speed": "The translation curve's input is min(1, the root's speed / this) (m/s). Usually 0.01 to 100",
    "rotation_speed": "The rotation curve's input is min(1, the root's turn rate / this) (deg/s). Usually 360 or 720",
}

# Config value limits: shares and blends are hard 0-1; the others take a soft range around the values game files use
# (typed values may go past it). SLIDERS are drawn as sliders; the angular speed limit and rotation speed are not.
CONFIG_LIMITS = {
    "simulation_strength": dict(min=0.0, max=1.0),
    "blend_weight": dict(min=0.0, max=1.0),
    "gravity_factor": dict(soft_min=0.0, soft_max=4.0),       # soft: -1 floats the cloth up
    "velocity_damping": dict(min=0.0, max=1.0),
    "second_velocity_damping": dict(min=0.0, max=1.0),
    "second_damping_speed": dict(min=0.0, soft_max=10.0),
    "blend_translation": dict(min=0.0, max=1.0),
    "blend_rotation": dict(min=0.0, max=1.0),
    "speed_limit": dict(min=0.0, soft_max=30.0),
    "angular_speed_limit": dict(min=0.0),
    "translation_speed": dict(min=0.0, soft_max=100.0),
    "rotation_speed": dict(min=0.0),
}
UNVERIFIED = {"bounce", "stickiness", "self_collision"}   # effect unknown: drawn greyed out
SLIDERS = ({name for name in CONFIG_LIMITS if name not in {"angular_speed_limit", "rotation_speed"}}
           | {"friction", "bounce", "stickiness"})

# via.KeyFrame's curve type (the file keeps the number)
KEY_TYPES = [
    ("LINEAR", "Linear", "", 1),
    ("STEP", "Step", "Holds the value until the next key", 2),
    ("HERMITE", "Hermite", "A smooth curve by the tangents", 3),
    ("WEIGHTED_5", "Weighted Hermite (5)", "A smooth curve by the tangents and their weights", 5),
    ("WEIGHTED_6", "Weighted Hermite (6)", "A smooth curve by the tangents and their weights", 6),
    ("HERMITE_0", "Hermite (0)", "Type 0, treated as Hermite", 0),
]
HERMITE_TYPES = {"HERMITE", "WEIGHTED_5", "WEIGHTED_6", "HERMITE_0"}
WEIGHTED_TYPES = {"WEIGHTED_5", "WEIGHTED_6"}


class REE_GPUC_Key(bpy.types.PropertyGroup):
    """One key of a config curve (via.KeyFrame; time and tangents are half floats in the file)."""
    time: FloatProperty(name="Time", default=0.0, min=0.0, max=1.0, precision=3,
                        description="The curve's input: how fast the root moves over the curve speed, 0 to 1")
    value: FloatProperty(name="Value", default=0.0, soft_min=0.0, soft_max=1.0, precision=3,
                         description="The blend amount at this time")
    type: EnumProperty(name="Type", items=KEY_TYPES, default="LINEAR",
                       description="How the curve goes from this key to the next")
    in_tangent: FloatProperty(name="In Tangent", default=0.0, precision=3)
    in_weight: FloatProperty(name="In Weight", default=0.0, precision=3)
    out_tangent: FloatProperty(name="Out Tangent", default=0.0, precision=3)
    out_weight: FloatProperty(name="Out Weight", default=0.0, precision=3)


class REE_GPUC_Curve(bpy.types.PropertyGroup):
    """A config curve (via.AnimationCurve): the blend amount by how fast the root moves or turns, used while the
    blend amount is not overwritten."""
    keys: bpy.props.CollectionProperty(type=REE_GPUC_Key)
    active_key: IntProperty(name="Key", default=0, min=0)
    clamp_flags: IntProperty(name="Clamp Flags", default=0,
                             description="Bit 0: the value is held between Clamp Min and Clamp Max; use the button to "
                                         "tick it. Usually 0")
    clamp_min: FloatProperty(name="Clamp Min", default=0.0, precision=3,
                             description="The lowest value while Clamp Flags bit 0 is set. Usually 0")
    clamp_max: FloatProperty(name="Clamp Max", default=1.0, precision=3,
                             description="The highest value while Clamp Flags bit 0 is set. Usually 1")
    unk04: IntProperty(name="Unkn Param 0", default=0, description="Effect unknown. Usually 0")
    unk08: FloatProperty(name="Unkn Param 1", default=15000.0, description="Effect unknown. Usually 15000")
    unk14: IntProperty(name="Unkn Param 2", default=0, description="Effect unknown. Usually 0")
    unk1c: IntProperty(name="Unkn Param 3", default=0, description="Effect unknown. Usually 0")

    def value(self, x):
        """Evaluate the curve at ``x`` (a number or an array) with ``modules.curve``'s approximation."""
        from .modules import curve
        keys = [(k.time, k.value, k.bl_rna.properties["type"].enum_items[k.type].value, k.in_tangent, k.out_tangent)
                for k in self.keys]
        return curve.evaluate(keys, x, (self.clamp_min, self.clamp_max) if self.clamp_flags & 1 else None)

    def ordered(self) -> bool:
        times = [k.time for k in self.keys]
        return all(a <= b for a, b in zip(times, times[1:]))

    def set_keys(self, keys) -> None:
        """Replace the keys; each is (time, value, type, in tangent, in weight, out tangent, out weight)."""
        self.keys.clear()
        for t, v, typ, it, iw, ot, ow in keys:
            k = self.keys.add()
            k.time, k.value, k.type = t, v, typ
            k.in_tangent, k.in_weight, k.out_tangent, k.out_weight = it, iw, ot, ow
        self.active_key = 0


# ch03_100_1005's curves (the config defaults' source)
CURVE_DEFAULTS = {
    "translation_curve": (
        (0.0, 1.0, "HERMITE", 0.028, 0.0, 0.028, 0.0), (0.129, 1.0, "HERMITE", 0.0, 0.0, 0.0, 0.0),
        (0.15, 0.8, "HERMITE", -0.576, 0.0, -0.576, 0.0), (0.3, 0.5, "HERMITE", -0.051, 0.0, -0.051, 0.0),
        (0.4, 0.5, "HERMITE", 0.0, 0.0, 0.0, 0.0), (0.421, 0.799, "HERMITE", 0.43, 0.0, 0.43, 0.0),
        (0.6, 1.0, "LINEAR", 0.0, 0.0, 0.0, 0.0), (1.0, 1.0, "LINEAR", 0.0, 0.0, 0.0, 0.0)),
    "rotation_curve": (
        (0.0, 0.4, "LINEAR", 0.0, 0.0, 0.0, 0.0), (0.5, 0.5, "LINEAR", 0.0, 0.0, 0.0, 0.0),
        (1.0, 1.0, "LINEAR", 0.0, 0.0, 0.0, 0.0)),
}
# (property, label, the switch that turns it off, its speed)
CURVES = (("translation_curve", "Translation Curve", "overwrite_translation", "translation_speed"),
          ("rotation_curve", "Rotation Curve", "overwrite_rotation", "rotation_speed"))


def fill_curves(cfg, force: bool = False) -> None:
    """Set a Config's curves to ``CURVE_DEFAULTS``; without ``force`` only empty ones (a collection has no default)."""
    for name, keys in CURVE_DEFAULTS.items():
        curve = getattr(cfg, name)
        if force or not len(curve.keys):
            curve.set_keys(keys)

from .modules import re4

# RE4 Config values: (label, the game files' commonest, the guess), named in the order of Wilds' fields they line up
# with (docs/re4-wilds-numeric-comparison); 0x30-0x38 at 0 stop the cloth moving in game
RE4_CONFIG_FIELDS = {
    "re4_value_24": ("Velocity Damping", 0.0, "0 to 0.4 in game files"),
    "re4_value_28": ("Blend Translation", 0.0, "0 to 1 in game files"),
    "re4_value_2c": ("Blend Rotation", 0.0, "0 to 1 in game files"),
    "re4_value_30": ("Simulation Strength", 1.0, "0.8 to 1 in game files; 0 leaves the mesh unmoved"),
    "re4_value_34": ("Blend Weight", 1.0, "0.2 to 1 in game files; 0 leaves the mesh unmoved"),
    "re4_value_38": ("Gravity Factor", 1.0, "0.6 to 1 in game files"),
}

REE_GPUC_Config = type("REE_GPUC_Config", (bpy.types.PropertyGroup,), {
    "__doc__": "One config block of the .gpuc; Parts point at one (several may share it)",
    "__annotations__": {**{name: (FloatProperty(name=RE4_CONFIG_FIELDS[name][0], default=RE4_CONFIG_FIELDS[name][1],
                                               precision=5,
                                               description=f"RE4 Config +0x{off:02X}. Unconfirmed: named by the "
                                                           f"Wilds field in its place ({RE4_CONFIG_FIELDS[name][2]})")
                                  if fmt == "f" else IntProperty(name=f"RE4 flags +0x{off:02X}",
                                                                 description=f"RE4 Config +0x{off:02X}. Unknown: 0 "
                                                                             f"in every game file"))
                            for name, (off, fmt) in re4.CONFIG.items()},
                        **{name: (BoolProperty(name=label, default=default,
                                               description=CONFIG_DESCRIPTIONS.get(name, "")) if kind == "?"
                                  else FloatProperty(name=label, default=default, precision=4,
                                                     description=CONFIG_DESCRIPTIONS.get(name, ""),
                                                     **CONFIG_LIMITS.get(name, {})))
                           for name, label, kind, default in CONFIG_FIELDS},
                        "info_state": IntProperty(name="Config Info State", default=5,
                                                  description="SkinningCollision (set: collisions judge the side "
                                                              "from the skinned places, legs pass through; clear: "
                                                              "from the last step's, legs push the cloth), the space "
                                                              "limit (sphere or cylinder) and the strength mode; use "
                                                              "the button to tick them"),
                        "translation_curve": PointerProperty(type=REE_GPUC_Curve),
                        "rotation_curve": PointerProperty(type=REE_GPUC_Curve),
                        **{name: IntProperty(name=label, default=default, description=desc)
                           for name, label, default, desc in CONFIG_UNKNOWNS}}})


HI_MAX = 65535 / 4095          # hi is a u16 / 4095 in the file, lo a u16 / 65535
_applying = False


def _tail_edit(self, context):
    """Set the Part's link preset to Custom; on a locked Part, mark the shape so ``canon.link_tails`` applies the
    tail to every link of that shape; on a Part made from bones, drop the shape's per-link overrides."""
    obj = self.id_data
    shape = self.path_from_id().rsplit(".", 1)[-1]
    if obj.get("ree_gpuc_canon", {}).get("locked"):
        edited = list(obj.get("ree_gpuc_tails_edited", ()))
        if shape not in edited:
            obj["ree_gpuc_tails_edited"] = edited + [shape]
    elif shape in obj.get("ree_gpuc_link_overrides", {}):
        over = obj["ree_gpuc_link_overrides"].to_dict()
        del over[shape]
        if len(over) > 1:
            obj["ree_gpuc_link_overrides"] = over
        else:
            del obj["ree_gpuc_link_overrides"]
    if not _applying:
        part = self.id_data.ree_gpuc_part
        if part.link_preset != "CUSTOM":
            part.link_preset = "CUSTOM"
    _redraw(self, context)


class REE_GPUC_LinkTail(bpy.types.PropertyGroup):
    """A link's tail: stiffness k, the share of the length error a solve removes; with the
    conditional multiplier, k x multiplier while lo < rest length / current length < hi. Off, the file gets
    (multiplier, lo, hi) = (1, 1, 1), a range that never holds."""
    k: FloatProperty(name="Stiffness", default=1.0, min=0.0, max=1.0, subtype="FACTOR", update=_tail_edit,
                     description="The share of a link's length error each solve removes: 1 back to the rest length "
                                 "at once, 0 no pull")
    conditional: BoolProperty(name="Use conditional multiplier", default=False, update=_tail_edit,
                              description="A different stiffness while the link's length is in a range")
    k2: FloatProperty(name="Multiplier", default=1.0, min=0.0, max=1.0, subtype="FACTOR", update=_tail_edit,
                      description="The stiffness is k x this while lo < rest length / current length < hi")
    lo: FloatProperty(name="Range low", default=1.0, min=0.0, max=1.0, precision=4, update=_tail_edit,
                      description="rest / current length (a ratio, not a length): under 1 the link is stretched, over "
                                  "1 compressed. The file keeps 0-1 in steps of 1/65535")
    hi: FloatProperty(name="Range high", default=HI_MAX, min=0.0, max=HI_MAX, precision=3, update=_tail_edit,
                      description="rest / current length (a ratio): the file keeps 0-16 in steps of 1/4095")

    def tail(self) -> tuple:
        """(k, k2, lo, hi) as the file gets them."""
        return (self.k, self.k2, self.lo, self.hi) if self.conditional else (self.k, 1.0, 1.0, 1.0)

    def summary(self) -> str:
        """When the multiplier applies, in lengths, and the two stiffnesses."""
        if not self.conditional or self.k2 == 1.0:
            return f"Stiffness {self.k:.3g} at every length"
        if self.lo >= self.hi:
            return f"Never applies (low >= high): stiffness {self.k:.3g} at every length"
        short = f"{1 / self.hi:.3g}x" if self.hi > 0 else "0.0x"
        long = f"{1 / self.lo:.3g}x" if self.lo > 0 else "any length"
        return f"{self.k * self.k2:.3g} while {short} to {long} rest, else {self.k:.3g}"


# Link presets: key -> (label, description, tails); tails are (k, k2, lo, hi) per shape in LINK_SHAPES order,
# taken from the file the label names.
LINK_PRESETS = {
    "SKIRT_1005": ("Mid-length skirt (ch03_100_1005)", "Soft round the body, stiff down it",
                   ((1.0, 1.0, 1.0, 14.403), (0.3, 0.29, 1.0, 0.8), (0.29, 0.31, 0.333, 0.8),
                    (0.5, 0.15, 0.333, 11.683))),
    "SKIRT_0015": ("Skirt, stiff ring (ch03_032_0015)", "Stiff ring, no shear or bend links",
                   ((1.0, 0.95, 1.0, HI_MAX), (0.9, 0.3, 0.0, HI_MAX), (0.0, 1.0, 1.0, 1.0),
                    (0.0, 0.15, 0.0, HI_MAX))),
    "COAT_HOPE": ("Coat (Hope, ch03_001_0002)", "Stiff everywhere, no conditional multiplier",
                  ((1.0, 1.0, 1.0, 1.0), (1.0, 1.0, 1.0, 1.0), (0.75, 1.0, 1.0, 1.0), (0.9, 1.0, 1.0, 1.0))),
    "HAKAMA": ("Hakama (ch03_066_0004)", "Soft shear and bend",
               ((1.0, 0.88, 0.84, 0.16), (1.0, 0.76, 0.671, 0.16), (0.3, 0.3, 0.952, HI_MAX),
                (0.15, 0.2, 0.28, HI_MAX))),
}


def _link_preset(self, context):
    """Copy the chosen preset's tails into the Part's four shapes."""
    global _applying
    if self.link_preset == "CUSTOM":
        return
    _applying = True
    try:
        for (name, _, _), (k, k2, lo, hi) in zip(LINK_SHAPES, LINK_PRESETS[self.link_preset][2]):
            t = getattr(self, name)
            t.k, t.k2, t.lo, t.hi = k, k2, lo, hi
            t.conditional = k2 != 1.0
    finally:
        _applying = False
    _redraw(self, context)


# the link shapes (modules/grid.py SHAPES) a Part sets a tail for: (property, label, description)
LINK_SHAPES = (
    ("vertical", "Vertical", "Quad edges down a column (structural, type 0)"),
    ("ring", "Ring", "Quad edges across a row (structural, type 0)"),
    ("shear", "Shear", "Quad diagonals (type 1)"),
    ("bend", "Bend", "Quad diagonals' second link and two-step links (type 2)"),
)


def _poll_config(self, obj):
    return is_config(obj)


def _part_config(self, context):
    """Parent the Part under its new Config, as a chain group sits under its chain settings."""
    obj = self.id_data
    if is_config(self.config) and obj.parent != self.config:
        place_under(obj, self.config)


def place_under(obj, parent) -> None:
    """obj under parent with no offset (a Config sits on the armature, a Part on its Config: armature space)."""
    obj.parent = parent
    obj.matrix_parent_inverse.identity()
    obj.matrix_basis.identity()


_loading_ranges = 0


def _range(field):
    def update(self, context):
        from . import canon
        obj = self.id_data
        if _loading_ranges:
            return
        if is_part(obj) and canon.has(obj):
            canon.change_range(obj, field)
        scene = getattr(context, "scene", None)
        if scene is not None and getattr(context, "active_object", None) == obj:
            scene.ree_gpuc.colour_by = scene.ree_gpuc.field
        _redraw(self, context)
    return update


def _in_front(self, context):
    for obj in context.scene.objects:
        if is_part(obj):
            obj.show_in_front = self.in_front
    _redraw(self, context)


class REE_GPUC_Part(bpy.types.PropertyGroup):
    """A part block of the .gpuc and its particle grid's generation."""
    config: PointerProperty(name="Config", type=bpy.types.Object, poll=_poll_config, update=_part_config)
    armature: PointerProperty(name="Armature", type=bpy.types.Object)
    re4_value_30: FloatProperty(name="Long Range Attachment Stretchiness", precision=5,
                               description="RE4 Part +0x30. Unconfirmed: named so in RE4's PartInfo type, effect "
                                           "not tested (0 in 161 of 162 game Parts)")
    rows: IntProperty(name="Rows")
    cols: IntProperty(name="Columns")
    sub_rows: IntProperty(name="Between bones, down")
    sub_cols: IntProperty(name="Between chains, across")
    closed: BoolProperty(name="Closed ring")
    flip_normals: BoolProperty(name="Flip Normals", default=False, update=_redraw,
                               description="Turn the particles' normals the other way (they point away from the "
                                           "cloth's axis or, for a flat piece, from its root joint): the space "
                                           "limits and the backstop are measured along them")
    other_diagonal: BoolProperty(name="Other Diagonal", default=False,
                                 description="Split each quad into its two triangles along the other diagonal (from "
                                             "the lower left to the upper right corner): some files are made so. "
                                             "Rebuild from Bones finds it; the triangles give the particles' frames "
                                             "and the collision edges")
    diagonal_bend: BoolProperty(name="Diagonal Bend", default=True, update=_redraw,
                                description="Give each quad diagonal a bend link (type 2) besides its shear link. "
                                            "Off: the diagonals carry shear links alone, as some files are made. "
                                            "Rebuild from Bones finds it")
    uv_tile: IntProperty(name="UV tile", description="The part's place on the grid UV (u from this to this + 1)")
    # the part block (section 6); its config index and name hash follow from the hierarchy and the name
    part_name: StringProperty(name="Name", default="_p000", description="The part block's name (usually _p000, _p001, ...)")
    use_root_joint: BoolProperty(name="Use Root Joint", default=True,
                                 description="The part follows its root joint for the inertia transfer (off: the cloth "
                                             "mesh's first joint)")
    root_joint: StringProperty(name="Root Joint", description="The joint the part follows")
    friction: FloatProperty(name="Friction", default=0.0, min=0.0, soft_max=1.0, precision=4,
                            description="Usually 0 to 0.5; some Onimusha files use 30 or 100. The preview uses at "
                                        "most 1")
    bounce: FloatProperty(name="Bounce", default=0.0, min=0.0, max=1.0, precision=4)
    stickiness: FloatProperty(name="Stickiness", default=0.0, min=0.0, max=1.0, precision=4)
    surface_area: FloatProperty(name="Surface area", default=1.0, min=0.0, precision=4)
    plane_bits: IntProperty(name="Collision Plane Bits", default=-1,
                            description="Which planes the part collides with; use the button to tick them. -1 all")
    sphere_bits: IntProperty(name="Collision Sphere Bits", default=-1,
                             description="Which spheres the part collides with; use the button to tick them. -1 all")
    capsule_bits: IntProperty(name="Collision Capsule Bits", default=-1,
                              description="Which capsules the part collides with; use the button to tick them. -1 "
                                          "all; with any of the first bits clear the .gpuc's own capsules do not "
                                          "collide")
    obb_bits: IntProperty(name="Collision OBB Bits", default=-1,
                          description="Which boxes the part collides with; use the button to tick them. -1 all")
    radius_range: FloatVectorProperty(name="Radius range", size=2, default=(0.0, 0.05), min=0.0, precision=4,
                                      update=_range("radius"), description="The collision radius (m) a gpuc_radius weight of "
                                                                 "0 and 1 stands for")
    max_distance_range: FloatVectorProperty(name="Max distance range", size=2, default=(0.0, 2.0), min=0.0,
                                            precision=3, update=_range("max_distance"),
                                            description="The max distance (m) a gpuc_max_distance weight of 0 and 1 "
                                                        "stands for")
    inverse_mass_range: FloatVectorProperty(name="Inverse mass range", size=2, default=(0.0, 200.0), min=0.0,
                                            precision=2, update=_range("inverse_mass"),
                                            description="The inverse mass a gpuc_inverse_mass weight of 0 and 1 "
                                                        "stands for")
    space_limit_lower_range: FloatVectorProperty(name="Space limit lower range", size=2, default=(0.0, 10.0),
                                                 min=0.0, precision=3, update=_range("space_limit_lower"),
                                                 description="The inward space limit (m) a gpuc_space_limit_lower "
                                                             "weight of 0 and 1 stands for")
    space_limit_upper_range: FloatVectorProperty(name="Space limit upper range", size=2, default=(0.0, 1.0),
                                                 min=0.0, precision=3, update=_range("space_limit_upper"),
                                                 description="The outward space limit (m) a gpuc_space_limit_upper "
                                                             "weight of 0 and 1 stands for")
    backstop_radius_range: FloatVectorProperty(name="Backstop radius range", size=2, default=(0.0, 1.0), min=0.0,
                                               precision=3, update=_range("backstop_radius"),
                                               description="The backstop radius (m) a gpuc_backstop_radius weight "
                                                           "of 0 and 1 stands for")
    backstop_offset_range: FloatVectorProperty(name="Backstop offset range", size=2, default=(0.0, 0.5),
                                               precision=3, update=_range("backstop_offset"),
                                               description="The backstop offset (m) a gpuc_backstop_offset weight "
                                                           "of 0 and 1 stands for")
    clsp_flags_a: IntProperty(name="CLSP Flags A", default=1032193,
                              description="With CLSP Flags B, one 64-bit mask of the CLSP tag groups the part "
                                          "collides with (as a chain group's): a CLSP shape is used when its mask "
                                          "meets this one; -1 every shape. It must include the outfit's leg "
                                          "shapes. Default: ch03_100_1005's")
    clsp_flags_b: IntProperty(name="CLSP Flags B", default=2048,
                              description="The high half of the CLSP tag mask (see CLSP Flags A)")
    link_preset: EnumProperty(name="Links", default="CUSTOM", update=_link_preset,
                              items=[(key, label, desc) for key, (label, desc, _) in LINK_PRESETS.items()]
                              + [("CUSTOM", "Custom", "The Part's own tails (any edit makes the preset Custom)")])
    vertical: PointerProperty(type=REE_GPUC_LinkTail)
    ring: PointerProperty(type=REE_GPUC_LinkTail)
    shear: PointerProperty(type=REE_GPUC_LinkTail)
    bend: PointerProperty(type=REE_GPUC_LinkTail)


def _poll_collection(self, coll):
    return is_gpuc_collection(coll)


PARTICLE_COLOURS = [
    ("NONE", "Plain", "One colour"),
    ("sim_weight", "Simulation weight", "As in Weight Paint: 0 pinned (blue) to 1 simulated (red)"),
    ("radius", "Collision radius", "As gpuc_radius shows in Weight Paint: the Part's range, low blue to high red"),
    ("max_distance", "Max distance", "As gpuc_max_distance shows in Weight Paint: the Part's range, low blue to high red"),
    *((name, label, f"As gpuc_{name} shows in Weight Paint: the Part's range, low blue to high red")
      for name, label in (("inverse_mass", "Inverse mass"), ("space_limit_lower", "Space limit lower"),
                          ("space_limit_upper", "Space limit upper"), ("backstop_radius", "Backstop radius"),
                          ("backstop_offset", "Backstop offset"))),
]


# the preview's test colliders (not written to any file)
TEST_KINDS = [("SPHERE", "Sphere", ""), ("CAPSULE", "Capsule", ""),
              ("PLANE", "Plane", "An endless plane through the object, facing its +Z; the Part's Friction applies"),
              ("BOX", "Box", "An oriented box (OBB) of Size at the object; the Part's Friction applies")]


def _field(self, context):
    """Make the chosen value the particle colours and each Part's active vertex group."""
    self.colour_by = self.field
    for obj in parts(self.target):
        g = obj.vertex_groups.get("gpuc_" + self.field)
        if g is not None:
            obj.vertex_groups.active_index = g.index
    _redraw(self, context)


def _poll_target_config(self, obj):
    return is_config(obj) and (self.target is None or obj.name in self.target.all_objects)


_ALL_FIELDS = [(*item, i) for i, item in enumerate(PARTICLE_COLOURS[1:])]
_RE4_FIELDS = [item for item in _ALL_FIELDS if item[0] in re4.PARTICLE_VALUES]


def _field_items(self, context):
    return _RE4_FIELDS if is_re4(self.target) else _ALL_FIELDS


_COLLIDER_KINDS = [("SPHERE", "Sphere", ""), ("CAPSULE", "Capsule", "")]
_COLLIDER_KINDS_RE4 = _COLLIDER_KINDS + [
    ("PLANE", "Plane", "An endless plane through the joint facing its +Y, as the game files' ground")]


def _collider_kinds(self, context):
    """RE4 alone has planes."""
    return _COLLIDER_KINDS_RE4 if is_re4(self.target) else _COLLIDER_KINDS


class REE_GPUC_Settings(bpy.types.PropertyGroup):
    """The scene's editor settings: where Generate puts parts, the particle tool, the display."""
    target: PointerProperty(name="Cloth", type=bpy.types.Collection, poll=_poll_collection,
                            description="The .gpuc collection Generate adds parts to")
    config: PointerProperty(name="Config", type=bpy.types.Object, poll=_poll_target_config,
                            description="The Config Generate puts new parts under (as the chain editor's current "
                                        "chain settings)")
    field: EnumProperty(name="Value", items=_field_items, default=0, update=_field)
    value: FloatProperty(name="Set to", default=1.0, precision=4)
    colour_by: EnumProperty(name="Particle colours", items=PARTICLE_COLOURS, default="sim_weight", update=_redraw)
    show_base_positions: BoolProperty(name="Show Base Positions", default=True, update=_redraw,
                                     description="Show generated base points and lines to edited rest positions")
    show_particles: BoolProperty(name="Particles", default=True, update=_redraw)
    particle_style: EnumProperty(name="Style", default="SPHERE", update=_redraw,
                                 items=[("SPHERE", "Radius", "A sphere of the particle's collision radius"),
                                        ("POINT", "Point", "A dot of a fixed size on screen")])
    sphere_alpha: FloatProperty(name="Opacity", default=0.45, min=0.05, max=1.0, update=_redraw)
    point_size: FloatProperty(name="Size", default=8.0, min=1.0, max=30.0, update=_redraw)
    in_front: BoolProperty(name="Draw Through Objects", default=True, update=_in_front,
                           description="The particles, base positions and the part meshes in front of every mesh")
    links_in_front: BoolProperty(name="Draw Through Objects", default=True, update=_redraw,
                                 description="The links and welds in front of every mesh")
    limits_in_front: BoolProperty(name="Draw Through Objects", default=False, update=_redraw,
                                  description="The space limits and backstops in front of every mesh")
    show_colliders: BoolProperty(name="Colliders", default=True, update=_redraw,
                                 description="The .gpuc's own collision capsules and the preview's test colliders")
    colliders_in_front: BoolProperty(name="Draw Through Objects", default=False, update=_redraw,
                                     description="The colliders in front of every mesh")
    show_links: BoolProperty(name="Links", default=True, update=_redraw)
    link_colour: EnumProperty(name="Link colours", default="SHAPE", update=_redraw,
                              items=[("SHAPE", "Shape", "Vertical green, ring orange, shear blue, bend pink"),
                                     ("STIFFNESS", "Stiffness", "The shape's k in the weight paint colours: 0 blue to 1 red"),
                                     ("NONE", "Plain", "One colour")])
    show_vertical: BoolProperty(name="Vertical", default=True, update=_redraw, description=LINK_SHAPES[0][2])
    show_ring: BoolProperty(name="Ring", default=True, update=_redraw, description=LINK_SHAPES[1][2])
    show_shear: BoolProperty(name="Shear", default=True, update=_redraw, description=LINK_SHAPES[2][2])
    show_bend: BoolProperty(name="Bend", default=True, update=_redraw, description=LINK_SHAPES[3][2])
    follow_pose: BoolProperty(name="Follow Skeleton Pose", default=False, update=_redraw,
                              description="Draw the particles and links where the posed bones carry them (the "
                                          "particle mesh itself stays in the rest pose)")
    show_uv_values: BoolProperty(name="Colours on the UV grid", default=True, update=_redraw)
    show_space_limit: BoolProperty(name="Space limits", default=False, update=_redraw,
                                   description="Each particle's space limits along its normal (inward blue, outward "
                                               "orange), no longer than its max distance; for parts whose Config "
                                               "Info State turns a space limit on")
    show_backstop: BoolProperty(name="Backstops", default=False, update=_redraw,
                                description="Each particle's backstop sphere, inside along its normal")
    selected_only_object: BoolProperty(name="Object Mode", default=False, update=_redraw,
                                       description="Draw only the selected parts")
    selected_only_edit: BoolProperty(name="Edit Mode", default=False, update=_redraw,
                                     description="In edit mode, with particles selected, draw only theirs (their "
                                                 "spheres, links, limits and backstops)")
    show_curves: BoolProperty(name="Curve graph", default=True, update=_redraw,
                              description="The active Config's (or the active Part's Config's) two curves in the "
                                          "corner of the 3D view")
    particle_mode: EnumProperty(name="Edit with", default="WEIGHT",
                                items=[("WEIGHT", "Weight Paint", "Paint a value's group with the weight brushes"),
                                       ("VIEW_3D", "3D Viewport", "Select particles in the 3D view, set a value"),
                                       ("UV", "UV Editor", "Select particles on the grid UV, set a value")])
    collider_kind: EnumProperty(name="Collision Shape", items=_collider_kinds)
    test_collider_kind: EnumProperty(name="Collider", items=TEST_KINDS)


def _capsule(self, context):
    from . import collision
    collision.apply(self)
    _redraw(self, context)


class REE_GPUC_Capsule(bpy.types.PropertyGroup):
    """One of the .gpuc's own collision capsules (as a chain2 collision capsule): each end on a joint, an offset
    from it and a radius. Kept in step with the BEGIN and END objects (collision.py)."""
    joint_a: StringProperty(name="Joint", update=_capsule, description="The joint the capsule's start is on")
    offset_a: FloatVectorProperty(name="Collision Offset", size=3, subtype="XYZ", step=0.1, update=_capsule,
                                  description="The start's offset from its joint")
    radius_a: FloatProperty(name="Radius", default=0.05, min=0.0, step=0.1, precision=4, update=_capsule)
    joint_b: StringProperty(name="End Joint", update=_capsule, description="The joint the capsule's end is on")
    offset_b: FloatVectorProperty(name="End Collision Offset", size=3, subtype="XYZ", step=0.1, update=_capsule,
                                  description="The end's offset from its joint")
    radius_b: FloatProperty(name="End Radius", default=0.05, min=0.0, step=0.1, precision=4, update=_capsule,
                            description="The end's radius (a tapered capsule when it differs from Radius)")
    hash_a: IntProperty(name="Joint Hash", default=0,
                        description="The start joint's name hash from the imported file, kept while the armature has "
                                    "no bone of that name (0: none)")
    hash_b: IntProperty(name="End Joint Hash", default=0,
                        description="The end joint's name hash from the imported file (0: none)")


def _plane(self, context):
    from . import collision
    collision.apply_plane(self)
    _redraw(self, context)


class REE_GPUC_Plane(bpy.types.PropertyGroup):
    """One of an RE4 .gpuc's own collision planes (section 11): the points p of the joint's space with
    normal . p = distance, pushed out to the normal's side. Kept in step with its object (collision.py)."""
    joint: StringProperty(name="Joint", update=_plane, description="The joint the plane is on")
    normal: FloatVectorProperty(name="Normal", size=3, subtype="XYZ", default=(0.0, 1.0, 0.0), update=_plane,
                                description="The side particles are kept on, in the joint's space (every game "
                                            "file: 0, 1, 0)")
    distance: FloatProperty(name="Distance", default=0.0, step=0.1, precision=4, update=_plane,
                            description="The plane's distance from the joint along Normal (every game file: 0). "
                                        "Unconfirmed: its sign in the game")


COLLIDER_TYPE = "RE_GPUC_COLLIDER"


def is_collider(obj) -> bool:
    return obj is not None and obj.get("TYPE") == COLLIDER_TYPE


def _collider(self, context):
    obj = self.id_data
    obj.empty_display_type = {"PLANE": "SINGLE_ARROW", "BOX": "CUBE"}.get(self.kind, "SPHERE")
    obj.empty_display_size = {"PLANE": 0.25 * self.plane_size, "BOX": 0.5 * min(self.size)}.get(self.kind, self.radius)
    _redraw(self, context)


class REE_GPUC_Collider(bpy.types.PropertyGroup):
    """A test collider for the preview simulation (not written to any file): a sphere at the object, a capsule
    along its local Z (length between the two end centres), an endless plane facing its +Z, or a box of Size.
    Scaled with the object."""
    kind: EnumProperty(name="Shape", items=TEST_KINDS, update=_collider)
    radius: FloatProperty(name="Radius", default=0.08, min=0.001, precision=3, subtype="DISTANCE", update=_collider)
    length: FloatProperty(name="Length", default=0.3, min=0.0, precision=3, subtype="DISTANCE", update=_collider)
    size: FloatVectorProperty(name="Size", size=3, default=(0.3, 0.3, 0.3), min=0.001, precision=3, subtype="XYZ",
                              unit="LENGTH", update=_collider, description="The box's full size along the object's axes")
    plane_size: FloatProperty(name="Display Size", default=1.0, min=0.01, precision=2, subtype="DISTANCE",
                              update=_collider, description="How large the plane is drawn (it is endless)")


FORCE_TYPE = "RE_GPUC_FORCE"


def is_force(obj) -> bool:
    return obj is not None and obj.get("TYPE") == FORCE_TYPE


class REE_GPUC_Force(bpy.types.PropertyGroup):
    """A force on the preview simulation's particles along the object's +Z (rotate the object to aim it), its
    strength these values x the object's scale along +Z: a constant acceleration (gravity by default; x the
    Config's GravityFactor), or a wind whose acceleration swings between Low and High over Period (a sine)."""
    kind: EnumProperty(name="Kind", items=[("CONSTANT", "Constant", "A steady acceleration (gravity)"),
                                           ("WIND", "Wind", "An acceleration swinging between Low and High")],
                       update=_redraw)
    enabled: BoolProperty(name="Enabled", default=True, update=_redraw)
    accel: FloatProperty(name="Acceleration", default=9.81, min=0.0, soft_max=50.0, unit="ACCELERATION",
                         update=_redraw, description="m/s² along the object's +Z")
    lo: FloatProperty(name="Low", default=0.0, min=0.0, soft_max=30.0, unit="ACCELERATION", update=_redraw,
                      description="The wind's weakest acceleration over a period (m/s²)")
    hi: FloatProperty(name="High", default=5.0, min=0.0, soft_max=30.0, unit="ACCELERATION", update=_redraw,
                      description="The wind's strongest acceleration over a period (m/s²)")
    period: FloatProperty(name="Period", default=2.0, min=0.05, soft_max=10.0, unit="TIME_ABSOLUTE", update=_redraw,
                          description="Seconds from strongest to strongest")

    def strength(self, t: float) -> float:
        """The acceleration now (t: seconds since the simulation started)."""
        if not self.enabled:
            return 0.0
        if self.kind == "CONSTANT":
            return self.accel
        import math
        return self.lo + (self.hi - self.lo) * (0.5 + 0.5 * math.cos(math.tau * t / self.period))


def _use_force(kind):
    def update(self, context):
        """Show the kind's force object, adding it when first turned on; off hides it and keeps its aim."""
        from . import sim
        on = self.use_gravity if kind == "CONSTANT" else self.use_wind
        obj = sim.force_object(context.scene, kind)
        if obj is None and on:
            obj = sim.add_force(context.scene, kind, context.scene.cursor.location, context.scene.ree_gpuc.target)
        if obj is not None:
            obj.ree_gpuc_force.enabled = on
            obj.hide_viewport = not on
        _redraw(self, context)
    return update


def _get_collider_index(self):
    """The active object's place in the scene's objects while it is a test collider (the panel's list follows
    the selection); -1 otherwise."""
    obj = bpy.context.view_layer.objects.active
    if not is_collider(obj):
        return -1
    return next((i for i, o in enumerate(self.id_data.objects) if o == obj), -1)


def _set_collider_index(self, value):
    """Picking a collider in the panel's list selects it (in Object Mode)."""
    objects = self.id_data.objects
    if not 0 <= value < len(objects) or not is_collider(objects[value]) or bpy.context.mode != "OBJECT":
        return
    view_layer = bpy.context.view_layer
    for o in view_layer.objects.selected:
        o.select_set(False)
    obj = objects[value]
    if obj.name in view_layer.objects:
        obj.select_set(True)
        view_layer.objects.active = obj


class REE_GPUC_Sim(bpy.types.PropertyGroup):
    """The preview simulation (sim.py): an approximation of the game's cloth, run on the parts' particles only;
    nothing it does is written to the file, the mesh or the armature."""
    use_gravity: BoolProperty(name="Gravity", default=True, update=_use_force("CONSTANT"),
                              description="A constant force along its arrow's +Z (Earth's gravity at first)")
    use_wind: BoolProperty(name="Wind", default=False, update=_use_force("WIND"),
                           description="A force swinging between Low and High along its arrow's +Z")
    iterations: IntProperty(name="Iterations", default=4, min=1, max=16,
                            description="Constraint and collision passes per frame (the component's LoopCount)")
    follow_pose: BoolProperty(name="Follow Skeleton Pose", default=True,
                              description="Pinned particles follow the posed bones; off, the rest pose")
    collider_index: IntProperty(name="Collider", get=_get_collider_index, set=_set_collider_index,
                                description="The test collider picked in the panel's list (the active object)")


CLASSES = (REE_GPUC_Header, REE_GPUC_Capsule, REE_GPUC_Plane, REE_GPUC_Key, REE_GPUC_Curve, REE_GPUC_Config, REE_GPUC_LinkTail, REE_GPUC_Part, REE_GPUC_Settings, REE_GPUC_Collider, REE_GPUC_Force,
           REE_GPUC_Sim)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Object.ree_gpuc_header = PointerProperty(type=REE_GPUC_Header)
    bpy.types.Object.ree_gpuc_capsule = PointerProperty(type=REE_GPUC_Capsule)
    bpy.types.Object.ree_gpuc_plane = PointerProperty(type=REE_GPUC_Plane)
    bpy.types.Object.ree_gpuc_config = PointerProperty(type=REE_GPUC_Config)
    bpy.types.Object.ree_gpuc_part = PointerProperty(type=REE_GPUC_Part)
    bpy.types.Scene.ree_gpuc = PointerProperty(type=REE_GPUC_Settings)
    bpy.types.Object.ree_gpuc_collider = PointerProperty(type=REE_GPUC_Collider)
    bpy.types.Object.ree_gpuc_force = PointerProperty(type=REE_GPUC_Force)
    bpy.types.Scene.ree_gpuc_sim = PointerProperty(type=REE_GPUC_Sim)


def unregister():
    del bpy.types.Scene.ree_gpuc_sim
    del bpy.types.Object.ree_gpuc_force
    del bpy.types.Object.ree_gpuc_collider
    del bpy.types.Scene.ree_gpuc
    del bpy.types.Object.ree_gpuc_part
    del bpy.types.Object.ree_gpuc_config
    del bpy.types.Object.ree_gpuc_header
    del bpy.types.Object.ree_gpuc_plane
    del bpy.types.Object.ree_gpuc_capsule
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)

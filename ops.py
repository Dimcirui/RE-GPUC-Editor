"""The editor's operators, laid out after the RE Chain Editor's: the header and Configs in Object Mode, parts from
bones in Pose Mode (each in a dialog), the extra and collision tools, the clipboard, the bit-flag dialogs."""
import os
import struct

import bmesh
import bpy
import numpy as np
from bpy_extras.io_utils import ExportHelper, ImportHelper
from mathutils import Matrix, Vector

from . import canon, collision, fileio, generate, props, sim


def _settings(context):
    return context.scene.ree_gpuc


def refused(op, message: str) -> set:
    op.report({"ERROR"}, message)
    return {"CANCELLED"}


def _select_only(context, obj) -> None:
    for o in context.view_layer.objects.selected:
        o.select_set(False)
    obj.select_set(True)
    context.view_layer.objects.active = obj


# --- import / export (as the chain editor's: the game by the file's extension, the armature and collections by
# name) ------------------------------------------------------------------------------------------------------

def _mesh_guess(context):
    """The mesh collection to use: the active armature's, else the last imported mesh's, else the only one."""
    obj = context.active_object
    if obj is not None and obj.type == "ARMATURE":
        coll = fileio.mesh_collection(obj)
        if coll is not None:
            return coll
    coll = bpy.data.collections.get(context.scene.get("REMeshLastImportedCollection", ""))
    if coll is not None:
        return coll
    meshes = [c for c in bpy.data.collections if any("Main Mesh LOD" in k.name for k in c.children)]
    return meshes[0] if len(meshes) == 1 else None


IMPORT_MODES = [
    ("UPDATE", "Update Existing", "The file's values onto the GPUC collection it was made from or exported as: "
                                  "refused unless their structures are the same"),
    ("KEEP", "Keep Structure", "A new GPUC collection keeping the file's particles, faces, links and skinning: "
                               "every value editable, the structure locked"),
    ("REBUILD", "Rebuild from Bones", "Parts made again from the mesh's bones with the file's values (they can be "
                                      "regenerated), when that gives the file's structure back; else Keep Structure. "
                                      "A file this editor exported always comes back whole"),
]


class REE_GPUC_OT_import(bpy.types.Operator, ImportHelper):
    """Import a .gpuc: its values onto the cloth it came from, or a new cloth keeping its structure"""
    bl_idname = "ree_gpuc.import_file"
    bl_label = "Import"
    bl_options = {"PRESET", "REGISTER", "UNDO"}

    filter_glob: bpy.props.StringProperty(default="*.gpuc.*", options={"HIDDEN"})
    files: bpy.props.CollectionProperty(type=bpy.types.OperatorFileListElement, options={"HIDDEN", "SKIP_SAVE"})
    directory: bpy.props.StringProperty(subtype="DIR_PATH", options={"HIDDEN", "SKIP_SAVE"})
    import_mode: bpy.props.EnumProperty(name="Import Mode", items=IMPORT_MODES, default="REBUILD")
    gpuc_collection: bpy.props.StringProperty(name="", description="The GPUC collection the file's values go onto")
    mesh_collection: bpy.props.StringProperty(
        name="", description="The RE Mesh Editor collection of the mesh the .gpuc moves: its armature names the "
                             "bones, finds the driven joints and places the capsules. Keep Structure can go without "
                             "it: the shape only")
    force_rebuild: bpy.props.BoolProperty(
        name="Force Rebuild", default=False,
        description="Keep the rebuilt cloth also when it does not give the file's structure back (positions, links, "
                    "skinning or driven joints as the editor makes them, each link shape its commonest tail): what "
                    "changed is said. A part that is no grid of bone chains still imports with Keep Structure")

    def invoke(self, context, event):
        if not self.mesh_collection:
            mesh = _mesh_guess(context)
            self.mesh_collection = mesh.name if mesh is not None else ""
        target = _settings(context).target
        if target is not None and not self.gpuc_collection:
            self.gpuc_collection = target.name
        if self.directory:                                   # dropped on the 3D view: the options only
            return context.window_manager.invoke_props_dialog(self, title=f"Import {len(self.paths())} .gpuc")
        return super().invoke(context, event)

    def paths(self) -> list:
        """The files picked or dropped (several when the file browser or a drop gives several)."""
        if self.directory and len(self.files) and self.files[0].name:
            return [os.path.join(self.directory, f.name) for f in self.files]
        return [self.filepath]

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "import_mode", text="")
        if self.import_mode == "UPDATE":
            layout.label(text="GPUC Collection:")
            layout.prop_search(self, "gpuc_collection", bpy.data, "collections", icon="COLLECTION_COLOR_06")
        layout.label(text="Mesh Collection:" if self.import_mode == "REBUILD" else "Mesh Collection (optional):")
        layout.prop_search(self, "mesh_collection", bpy.data, "collections")
        if self.import_mode == "KEEP" and not self.mesh_collection:
            layout.label(text="Shape only: no posing, joints by hash", icon="INFO")
        if self.import_mode == "REBUILD":
            layout.prop(self, "force_rebuild")
            if not self.mesh_collection:
                layout.label(text="No mesh collection: Keep Structure instead", icon="INFO")

    def execute(self, context):
        paths = self.paths()
        wrong = [os.path.basename(p) for p in paths
                 if not (".gpuc" in os.path.basename(p) and fileio.gp.ext_of_path(p))]
        if wrong:
            return refused(self, f"Only MH Wilds, RE9, Onimusha and RE4 .gpuc files can be "
                                 f"imported ({wrong[0]})")
        if self.import_mode == "UPDATE" and len(paths) > 1:
            return refused(self, "Update Existing takes one file: its values go onto one GPUC collection")
        mesh = bpy.data.collections.get(self.mesh_collection) if self.mesh_collection else None
        if context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        mode = self.import_mode
        fell_back = []                                         # (file, why) imported with Keep Structure instead
        if mode == "REBUILD" and mesh is None:                 # the default with no mesh: the shape only
            mode = "KEEP"
            fell_back.append(("", "no mesh collection (Rebuild from Bones needs the mesh's bones)"))
            self.report({"WARNING"}, "No mesh collection: imported with Keep Structure (Rebuild from Bones needs "
                                     "the mesh's bones)")
        done = []
        for path in paths:
            name = os.path.basename(path)
            try:
                if mode == "UPDATE":
                    coll = bpy.data.collections.get(self.gpuc_collection)
                    if coll is None or not props.is_gpuc_collection(coll):
                        return refused(self, "Pick the GPUC collection the values go onto")
                    notes = fileio.update_existing(path, coll, mesh)
                elif mode == "REBUILD":
                    coll, notes = fileio.import_rebuild(path, mesh, self.force_rebuild)
                else:
                    coll, notes = fileio.import_keep(path, mesh)
            except fileio.ImportProblem as e:
                self.report({"ERROR"}, f"{name} not imported: {e}")
                continue
            except (OSError, ValueError, KeyError, IndexError, struct.error) as e:
                self.report({"ERROR"}, f"Cannot read {name}: {e}")
                continue
            _settings(context).target = coll
            done.append(name)
            for note in notes:
                self.report({"WARNING"}, f"{name}: {note}" if len(paths) > 1 else note)
                if note.startswith("Not rebuilt ("):
                    fell_back.append((name, note[len("Not rebuilt ("):note.rfind("): imported with")]))
        if fell_back and done:
            _fell_back_popup(context, fell_back)
        if not done:
            return {"CANCELLED"}
        context.scene["REEGpucLastImportedVersion"] = int(fileio.gp.ext_of_path(paths[-1])[1:])
        self.report({"INFO"}, f"Imported {', '.join(done)} ({dict((m[0], m[1]) for m in IMPORT_MODES)[mode]})")
        return {"FINISHED"}


def _fell_back_popup(context, fell_back) -> None:
    """Say in a popup that files were imported with Keep Structure, not rebuilt, and why (a report alone is easily
    missed among the others). Not in background Blender."""
    if bpy.app.background:
        return
    import textwrap

    def draw(menu, _context):
        col = menu.layout.column()
        for name, why in fell_back:
            if name:
                col.label(text=name, icon="FILE")
            for k, line in enumerate(textwrap.wrap(why, 90)):
                col.label(text=line, icon="BLANK1" if k or name else "INFO")
        col.separator()
        col.label(text="Their Parts keep the file's structure (locked): values can be edited, not regenerated")
    context.window_manager.popup_menu(draw, title="Imported with Keep Structure, not rebuilt", icon="ERROR")


class REE_GPUC_FH_import(bpy.types.FileHandler):
    """Drop a .gpuc on the 3D view: the import's options in a dialog. Blender matches the last extension only, the
    version (shared with other formats): the import refuses anything but a .gpuc."""
    bl_idname = "REE_GPUC_FH_import"
    bl_label = "RE GPUC import"
    bl_import_operator = "ree_gpuc.import_file"
    bl_file_extensions = ";".join(fileio.gp.EXTS)

    @classmethod
    def poll_drop(cls, context):
        return context.area is not None and context.area.type == "VIEW_3D"


EXPORT_KEY = "ree_gpuc_last_export"     # on the header (and the scene): the path last exported to


class REE_GPUC_OT_export(bpy.types.Operator, ExportHelper):
    """Export a GPUC collection as a .gpuc"""
    bl_idname = "ree_gpuc.export_file"
    bl_label = "Export"
    bl_options = {"PRESET"}

    filename_ext: bpy.props.EnumProperty(name="", description="The game to export the .gpuc for",
                                         items=fileio.GAMES)
    filter_glob: bpy.props.StringProperty(default="*.gpuc*", options={"HIDDEN"})
    target_collection: bpy.props.StringProperty(name="", description="The GPUC collection to export")
    mesh_collection: bpy.props.StringProperty(
        name="", description="The RE Mesh Editor collection of the mesh the cloth moves (its weighted bones give the "
                             "output joints' indices). Blank: the one the armature is in")
    joint_indices: bpy.props.EnumProperty(
        name="Joint Indices", items=fileio.JOINT_INDICES, default="FILE",
        description="Set when the dialog opens: As Imported for a cloth keeping its file's structure, From Mesh for "
                    "one made from bones (always)")

    def invoke(self, context, event):
        coll = _settings(context).target
        if coll is not None and not self.target_collection:
            self.target_collection = coll.name
        if coll is not None:
            head = props.header(coll)
            if head is not None:
                self.filename_ext = head.ree_gpuc_header.game                # the cloth's game
            name = coll.name.split(".gpuc")[0] + ".gpuc" + self.filename_ext
            # the folder: the cloth's last export, else the file it was imported from, else the last export of any
            last = head.get(EXPORT_KEY, "") if head is not None else ""
            source = head.get(fileio.SOURCE_KEY, "") if head is not None else ""
            folder = os.path.dirname(last or source or context.scene.get(EXPORT_KEY, ""))
            self.filepath = os.path.join(folder, name) if folder else name
        target = bpy.data.collections.get(self.target_collection)
        # a cloth keeping its file's structure goes with the mesh the file came with until told otherwise; one made
        # from bones goes with a mesh exported again by the RE Mesh Editor (its bones' weights changed)
        self.joint_indices = "FILE" if fileio.keeps_structure(target) else "MESH"
        return super().invoke(context, event)

    def draw(self, context):
        layout = self.layout
        layout.label(text="Game:")
        layout.prop(self, "filename_ext")
        layout.label(text="GPUC Collection:")
        layout.prop_search(self, "target_collection", bpy.data, "collections", icon="COLLECTION_COLOR_06")
        coll = bpy.data.collections.get(self.target_collection)
        if coll is not None and fileio.keeps_structure(coll):
            layout.label(text="Written over its own file", icon="INFO")
            layout.label(text="Joint Indices:")
            layout.prop(self, "joint_indices", text="")
            if self.joint_indices == "MESH":
                layout.label(text="Mesh Collection:")
                layout.prop_search(self, "mesh_collection", bpy.data, "collections")
            return
        layout.label(text="Joint Indices: From Mesh (made from bones)")
        layout.label(text="Mesh Collection:")
        layout.prop_search(self, "mesh_collection", bpy.data, "collections")

    def execute(self, context):
        coll = bpy.data.collections.get(self.target_collection)
        if coll is None or not props.is_gpuc_collection(coll):
            return refused(self, "Pick the GPUC collection to export")
        mesh = bpy.data.collections.get(self.mesh_collection) if self.mesh_collection else None
        if context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        try:
            notes = fileio.export(coll, self.filepath, mesh, self.joint_indices, self.filename_ext)
        except fileio.ExportError as e:
            return refused(self, f"Not exported: {e}")
        for note in notes:
            self.report({"WARNING"}, note)
        props.header(coll)[EXPORT_KEY] = self.filepath
        context.scene[EXPORT_KEY] = self.filepath
        self.report({"INFO"}, f"Exported {os.path.basename(self.filepath)}")
        return {"FINISHED"}


# --- Object Mode --------------------------------------------------------------------------------------------

def _mesh_name(coll) -> str:
    """A RE Mesh Editor collection's file name (its name without .mesh), "" for any other collection."""
    if coll is None or coll.get("~TYPE") != "RE_MESH_COLLECTION" and ".mesh" not in coll.name:
        return ""
    return coll.name.split(".mesh")[0]


def suggest(context) -> tuple[str, str]:
    """The cloth's name and armature, as the chain editor names a chain after the mesh: the armature's mesh
    collection (the .gpuc goes beside the mesh of the same name), else the mesh last imported, else the
    armature's own name; the armature is the active one, else the one in that mesh collection."""
    obj = context.active_object
    arm = obj if obj is not None and obj.type == "ARMATURE" else None
    last = bpy.data.collections.get(context.scene.get("REMeshLastImportedCollection", ""))
    if arm is None and last is not None:
        arm = next((o for o in last.all_objects if o.type == "ARMATURE"), None)
    name = next((n for n in map(_mesh_name, arm.users_collection if arm else ()) if n), "")
    name = name or _mesh_name(last) or (arm.name if arm else "") or "newCloth"
    return name, arm.name if arm else ""


class REE_GPUC_OT_create_header(bpy.types.Operator):
    """A new cloth: a <name>.gpuc collection with its header on the armature and one Config"""
    bl_idname = "ree_gpuc.create_header"
    bl_label = "Create GPUC Header"
    bl_options = {"REGISTER", "UNDO"}

    name: bpy.props.StringProperty(name="Name", default="newCloth",
                                   description="The .gpuc's name: use the mesh file's (ch03_039_0012 for "
                                               "ch03_039_0012.mesh)")
    armature: bpy.props.StringProperty(name="Armature")
    game: bpy.props.EnumProperty(name="Game", items=props.GAMES, description="The game the .gpuc is for")

    def invoke(self, context, event):
        self.name, self.armature = suggest(context)
        last = f".{context.scene.get('REEGpucLastImportedVersion', '')}"   # the game last imported, else Wilds
        if last in fileio.gp.EXTS:
            self.game = last
        return context.window_manager.invoke_props_dialog(self)

    def draw(self, context):
        col = self.layout.column()
        col.prop(self, "game")
        col.prop(self, "name")
        col.prop_search(self, "armature", bpy.data, "objects")

    def execute(self, context):
        arm = bpy.data.objects.get(self.armature)
        if self.game == fileio.re4.EXT:
            return refused(self, "RE4 supports imported Keep Structure cloth only: import an original RE4 .gpuc")
        if arm is None or arm.type != "ARMATURE":
            self.report({"ERROR"}, "Choose the armature the cloth is on")
            return {"CANCELLED"}
        _settings(context).target = generate.new_collection(self.name, arm, self.game)
        return {"FINISHED"}


class REE_GPUC_OT_new_config(bpy.types.Operator):
    """A Config under the active cloth's header, made the current one (new parts go under it)"""
    bl_idname = "ree_gpuc.new_config"
    bl_label = "Create GPUC Config"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return _settings(context).target is not None

    def execute(self, context):
        generate.new_config(_settings(context).target)
        return {"FINISHED"}


class REE_GPUC_OT_check(bpy.types.Operator):
    """Compare every part of the active cloth with its bones"""
    bl_idname = "ree_gpuc.check"
    bl_label = "Check"

    @classmethod
    def poll(cls, context):
        return bool(props.parts(_settings(context).target))

    def execute(self, context):
        bad = 0
        for obj in props.parts(_settings(context).target):
            problems = generate.check(obj)
            obj["ree_gpuc_check"] = "; ".join(problems) if problems else "ok"
            bad += bool(problems)
            if problems:
                self.report({"WARNING"}, f"{obj.name}: {'; '.join(problems)}")
        self.report({"WARNING"} if bad else {"INFO"},
                    f"{bad} parts need attention" if bad else "Every part ok")
        return {"FINISHED"}


def _cloth_armature(context):
    s = _settings(context)
    h = props.header(s.target)
    if h is not None and h.parent is not None and h.parent.type == "ARMATURE":
        return h.parent
    return next((p.ree_gpuc_part.armature for p in props.parts(s.target) if p.ree_gpuc_part.armature), None)


class REE_GPUC_OT_switch_to_pose(bpy.types.Operator):
    """Pose Mode on the active cloth's armature"""
    bl_idname = "ree_gpuc.switch_to_pose"
    bl_label = "Switch to Pose Mode"

    @classmethod
    def poll(cls, context):
        return _cloth_armature(context) is not None or (context.active_object is not None
                                                         and context.active_object.type == "ARMATURE")

    def execute(self, context):
        arm = _cloth_armature(context) or context.active_object
        if context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        _select_only(context, arm)
        bpy.ops.object.mode_set(mode="POSE")
        return {"FINISHED"}


class REE_GPUC_OT_switch_to_object(bpy.types.Operator):
    """Back to Object Mode"""
    bl_idname = "ree_gpuc.switch_to_object"
    bl_label = "Switch to Object Mode"

    def execute(self, context):
        bpy.ops.object.mode_set(mode="OBJECT")
        return {"FINISHED"}


# --- Pose Mode: parts from bones ----------------------------------------------------------------------------

RING = [("AUTO", "Auto", "Closed when the chains go all the way round"),
        ("CLOSED", "Closed", "Join the last chain to the first (a skirt)"),
        ("OPEN", "Open", "Open at one side (a cape, a panel)")]


class _PartOptions:
    closed: bpy.props.EnumProperty(name="Ring", items=RING, default="AUTO")
    truncate: bpy.props.BoolProperty(name="Cut to the shortest chain")
    sub_rows: bpy.props.IntProperty(name="Between bones, down", default=0, min=0, max=8,
                                    description="Particles between two bones of a chain (0: one per bone)")
    sub_cols: bpy.props.IntProperty(name="Between chains, across", default=0, min=0, max=8,
                                    description="Columns of particles between two neighbouring chains")

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == "ARMATURE" and obj.mode == "POSE"

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def draw(self, context):
        col = self.layout.column()
        col.use_property_split = True
        for name in ("closed", "truncate", "sub_rows", "sub_cols"):
            col.prop(self, name)

    def execute(self, context):
        s = _settings(context)
        arm = context.active_object
        if fileio.keeps_structure(s.target):
            return refused(self, f"{s.target.name} keeps a .gpuc's structure (Keep Structure import): make a new "
                                 f"GPUC collection for parts from bones")
        try:
            lay = generate.layout(arm, self.chains(arm), self.closed, self.truncate, self.sub_rows, self.sub_cols)
        except (generate.GenerateError, ValueError) as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        if s.target is None:
            s.target = generate.new_collection(arm.name, arm)
        obj = generate.new_part(s.target, arm, lay, s.config)
        self.report({"INFO"}, f"{obj.name}: {lay['rows']} x {lay['cols']}, {'closed' if lay['closed'] else 'open'}")
        return {"FINISHED"}


class REE_GPUC_OT_part_from_parents(_PartOptions, bpy.types.Operator):
    """A part from the first bone of each chain: every bone below it, one child each, is taken"""
    bl_idname = "ree_gpuc.part_from_parents"
    bl_label = "Create Part From Parent Bones"
    bl_options = {"REGISTER", "UNDO"}

    @staticmethod
    def chains(arm):
        return generate.chains_from_parents(arm)


class REE_GPUC_OT_part_from_selected(_PartOptions, bpy.types.Operator):
    """A part from the selected bones: each chain of them a column, a particle on every bone"""
    bl_idname = "ree_gpuc.part_from_selected"
    bl_label = "Create Part From Selected Bones"
    bl_options = {"REGISTER", "UNDO"}

    @staticmethod
    def chains(arm):
        return generate.chains_from_selected(arm)


def _part_items(self, context):
    s = _settings(context)
    return [(o.name, o.name, "") for o in props.parts(s.target) if not canon.locked(o)] or [("", "No part", "")]


def _default_part(context):
    s = _settings(context)
    obj = context.active_object
    if props.is_part(obj):
        return obj.name
    under = [c for c in (s.config.children if s.config else ()) if props.is_part(c)]
    return (under or props.parts(s.target) or [None])[0]


class REE_GPUC_OT_regenerate_part(bpy.types.Operator):
    """The part's particles laid out again from the bones it was made from, with its density or ring changed
    if you like; every value is kept (carried over the grid when its size changes)"""
    bl_idname = "ree_gpuc.regenerate_part"
    bl_label = "Regenerate Particles From Part"
    bl_options = {"REGISTER", "UNDO"}

    closed: bpy.props.EnumProperty(name="Ring", items=RING[1:])
    sub_rows: bpy.props.IntProperty(name="Between bones, down", min=0, max=8)
    sub_cols: bpy.props.IntProperty(name="Between chains, across", min=0, max=8)

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return (context.mode == "OBJECT" and props.is_part(obj) and obj.select_get()
                and not canon.locked(obj))

    def invoke(self, context, event):
        self._fill(context.active_object)
        return context.window_manager.invoke_props_dialog(self)

    def _fill(self, obj):
        if props.is_part(obj):
            p = obj.ree_gpuc_part
            self.closed = "CLOSED" if p.closed else "OPEN"
            self.sub_rows, self.sub_cols = p.sub_rows, p.sub_cols

    def draw(self, context):
        col = self.layout.column()
        col.use_property_split = True
        for name in ("closed", "sub_rows", "sub_cols"):
            col.prop(self, name)

    def execute(self, context):
        obj = context.active_object
        if not props.is_part(obj):
            self.report({"ERROR"}, "No part to regenerate")
            return {"CANCELLED"}
        if obj.mode != "OBJECT":
            self.report({"ERROR"}, f"{obj.name} is being edited: leave its edit mode first")
            return {"CANCELLED"}
        try:
            generate.regenerate(obj, self.closed == "CLOSED", self.sub_rows, self.sub_cols)
        except generate.GenerateError as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        obj["ree_gpuc_check"] = "ok"
        p = obj.ree_gpuc_part
        self.report({"INFO"}, f"{obj.name}: {p.rows} x {p.cols}")
        return {"FINISHED"}


class REE_GPUC_OT_select_part_bones(bpy.types.Operator):
    """Select the bones a part was made from (Pose Mode on its armature)"""
    bl_idname = "ree_gpuc.select_part_bones"
    bl_label = "Select Part Bones"
    bl_options = {"REGISTER", "UNDO"}

    part: bpy.props.EnumProperty(name="Part", items=_part_items)

    @classmethod
    def poll(cls, context):
        return bool(props.parts(_settings(context).target))

    def invoke(self, context, event):
        default = _default_part(context)
        name = default if isinstance(default, str) else default.name if default else ""
        if name:
            self.part = name
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        obj = bpy.data.objects.get(self.part)
        if not props.is_part(obj) or obj.ree_gpuc_part.armature is None:
            return {"CANCELLED"}
        arm = obj.ree_gpuc_part.armature
        nodes = set(canon.load(obj)["nodes"])
        if context.mode != "OBJECT" and context.active_object != arm:
            bpy.ops.object.mode_set(mode="OBJECT")
        if context.mode != "POSE":
            _select_only(context, arm)
            bpy.ops.object.mode_set(mode="POSE")
        for pb in arm.pose.bones:
            on = pb.name in nodes
            if hasattr(pb, "select"):
                pb.select = on
            else:
                pb.bone.select = on
        first = canon.load(obj)["nodes"][0]
        if first in arm.data.bones:
            arm.data.bones.active = arm.data.bones[first]
        self.report({"INFO"}, f"{len(nodes)} bones")
        return {"FINISHED"}


# --- collision tools (preview colliders) --------------------------------------------------------------------

def new_collider(context, kind: str, label: str = "") -> bpy.types.Object:
    obj = bpy.data.objects.new(generate.unique_name("COL_", f"_{kind.title()}{' ' + label if label else ''}"), None)
    obj["TYPE"] = props.COLLIDER_TYPE
    obj.hide_render = True
    coll = _settings(context).target
    (collision.sim_collection(coll) if coll is not None else context.scene.collection).objects.link(obj)
    obj.ree_gpuc_collider.kind = kind
    props._collider(obj.ree_gpuc_collider, context)      # the empty's display for the kind
    return obj


class REE_GPUC_OT_add_collider(bpy.types.Operator):
    """A test collider at the 3D cursor, to push the simulated particles (not written to the file)"""
    bl_idname = "ree_gpuc.add_collider"
    bl_label = "Add Collider"
    bl_options = {"REGISTER", "UNDO"}

    kind: bpy.props.EnumProperty(items=props.TEST_KINDS)

    def execute(self, context):
        if context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        obj = new_collider(context, self.kind)
        obj.location = context.scene.cursor.location
        _select_only(context, obj)
        return {"FINISHED"}


class REE_GPUC_OT_remove_collider(bpy.types.Operator):
    """Delete the test collider picked in the list (the active object)"""
    bl_idname = "ree_gpuc.remove_collider"
    bl_label = "Remove Collider"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return props.is_collider(context.active_object)

    def execute(self, context):
        bpy.data.objects.remove(context.active_object)
        return {"FINISHED"}


class REE_GPUC_OT_collider_from_bones(bpy.types.Operator):
    """The .gpuc's own collision on the selected bones (as the chain editor's Collision From Bone): a sphere on
    each bone's head; a capsule along one bone, or between the heads of two"""
    bl_idname = "ree_gpuc.collider_from_bones"
    bl_label = "Collision From Bones"
    bl_options = {"REGISTER", "UNDO"}

    radius: bpy.props.FloatProperty(name="Radius", default=0.05, min=0.001, precision=3, subtype="DISTANCE")

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == "ARMATURE" and obj.mode == "POSE"

    def execute(self, context):
        from . import collision
        s = _settings(context)
        arm = context.active_object
        sel = [pb for pb in arm.pose.bones if getattr(pb, "select", False) or getattr(pb.bone, "select", False)]
        if not sel:
            self.report({"ERROR"}, "Select a bone")
            return {"CANCELLED"}
        if s.collider_kind == "CAPSULE" and len(sel) > 2:
            self.report({"ERROR"}, "A capsule takes one bone or two")
            return {"CANCELLED"}
        if s.target is None:
            s.target = generate.new_collection(arm.name, arm)
        zero, r = (0.0, 0.0, 0.0), self.radius
        if s.collider_kind == "SPHERE":
            made = [(pb.name, zero, pb.name, zero) for pb in sel]
        elif len(sel) == 1:
            made = [(sel[0].name, zero, sel[0].name, zero)]
        else:
            made = [(sel[0].name, zero, sel[1].name, zero)]
        for bone_a, off_a, bone_b, off_b in made:
            collision.new_capsule(s.target, arm, bone_a, off_a, r, bone_b, off_b, r, shape=s.collider_kind)
        self.report({"INFO"}, f"{len(made)} collisions")
        return {"FINISHED"}


# --- clipboard ----------------------------------------------------------------------------------------------

# what Copy takes from each kind of object (the structure a Part was made with stays its own)
CLIP_GROUPS = {props.HEADER_TYPE: "ree_gpuc_header", props.CONFIG_TYPE: "ree_gpuc_config",
               props.PART_TYPE: "ree_gpuc_part"}
CLIP_SKIP = {"rna_type", "name", "config", "armature", "rows", "cols", "sub_rows", "sub_cols", "closed", "uv_tile",
             "part_name", "root_joint", "use_root_joint"}


def _to_dict(pg) -> dict:
    out = {}
    for prop in pg.bl_rna.properties:
        key = prop.identifier
        if key in CLIP_SKIP or prop.is_readonly and prop.type not in {"POINTER", "COLLECTION"}:
            continue
        if prop.type == "POINTER":
            if isinstance(getattr(pg, key), bpy.types.PropertyGroup):
                out[key] = _to_dict(getattr(pg, key))
            continue
        if prop.type == "COLLECTION":
            out[key] = [_to_dict(item) for item in getattr(pg, key)]
            continue
        value = getattr(pg, key)
        out[key] = list(value) if prop.type in {"FLOAT", "INT", "BOOLEAN"} and getattr(prop, "array_length", 0) else value
    return out


def _from_dict(pg, values: dict) -> None:
    later = []
    for key, value in values.items():
        if key == "link_preset":
            later.append((key, value))              # after the tails, so the preset is not turned to Custom
        elif isinstance(value, dict):
            _from_dict(getattr(pg, key), value)
        elif isinstance(value, list) and isinstance(getattr(pg, key), bpy.types.bpy_prop_collection):
            items = getattr(pg, key)
            items.clear()
            for item in value:
                _from_dict(items.add(), item)
        else:
            setattr(pg, key, value)
    for key, value in later:
        setattr(pg, key, value)


class REE_GPUC_OT_copy(bpy.types.Operator):
    """Copy the active header's, Config's or Part's values"""
    bl_idname = "ree_gpuc.copy"
    bl_label = "Copy"

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.get("TYPE") in CLIP_GROUPS

    def execute(self, context):
        obj = context.active_object
        context.scene["ree_gpuc_clipboard"] = {"type": obj["TYPE"], "source": obj.name,
                                               "values": _to_dict(getattr(obj, CLIP_GROUPS[obj["TYPE"]]))}
        return {"FINISHED"}


class REE_GPUC_OT_paste(bpy.types.Operator):
    """Paste the copied values onto every selected object of the same kind"""
    bl_idname = "ree_gpuc.paste"
    bl_label = "Paste"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        clip = context.scene.get("ree_gpuc_clipboard")
        return clip is not None and any(o.get("TYPE") == clip["type"] for o in context.selected_objects)

    def execute(self, context):
        clip = context.scene["ree_gpuc_clipboard"]
        values = clip["values"].to_dict()
        done = 0
        for obj in context.selected_objects:
            if obj.get("TYPE") == clip["type"]:
                _from_dict(getattr(obj, CLIP_GROUPS[clip["type"]]), values)
                done += 1
        self.report({"INFO"}, f"{done} objects")
        return {"FINISHED"}


# --- bit flags ----------------------------------------------------------------------------------------------

# the bits of the full body CLSP the chain editor's Create Full Body CLSP makes (its FULL_BODY_CLSP_NAMES)
FULL_BODY_CLSP = {0: "Hip_HJ_00", 1: "Spine_0", 2: "Spine_1", 3: "Spine_2", 4: "Neck_0 > L_UpperArm",
                  5: "Neck_0 > R_UpperArm", 6: "L_UpperArm > L_Forearm", 7: "R_UpperArm > R_Forearm",
                  8: "L_Forearm > L_Hand", 9: "R_Forearm > R_Hand", 10: "L_Hand > L_MiddleF1", 11: "R_Hand > R_MiddleF1",
                  12: "Neck_0 > Head", 13: "Head", 14: "L_Thigh > L_Knee", 15: "R_Thigh > R_Knee",
                  16: "L_Shin > L_Foot", 17: "R_Shin > R_Foot", 18: "L_Foot > L_Toe", 19: "R_Foot > R_Toe",
                  23: "Hip_HJ_00 (second shape)"}
KERNEL_FLAGS = {1: "Joint Deform Info (not confirmed)", 2: "Mesh Multi Joint Deform", 3: "Solve Constraint Legacy",
                4: "Tangent Passes"}


def _bit_names(context, target) -> dict:
    """Names of the known bits of a field (the rest are listed by number): the CLSP Flags' from the cloth's
    Reference CLSP (each bit by the shapes that set it; else the full body CLSP's), the sphere and capsule bits'
    from the shapes in the game's order (the .gpuc's own, then the Reference CLSP's)."""
    from . import collision
    clsp = collision.clsp_shapes(collision.reference_clsp(context.scene))
    if target in ("ree_gpuc_part.clsp_flags_a", "ree_gpuc_part.clsp_flags_b") and clsp:
        k = 6 if target.endswith("_a") else 7
        names = {}
        for shape in clsp:
            for i in range(32):
                if (shape[k] & 0xFFFFFFFF) >> i & 1:
                    names.setdefault(i, []).append(_short(shape[0]))
        return {i: ", ".join(n) for i, n in names.items()}
    if target == "ree_gpuc_part.clsp_flags_a":
        return FULL_BODY_CLSP
    if target == "ree_gpuc_part.sphere_bits":
        return {i: _short(s[0]) for i, s in enumerate(s for s in clsp if s[1] == "SPHERE") if i < 32}
    if target == "ree_gpuc_header.kernel_flags":
        return KERNEL_FLAGS
    if target == "ree_gpuc_config.info_state":
        return props.STATE_BITS
    if target.endswith("_curve.clamp_flags"):
        return {0: "Clamp"}
    if target == "ree_gpuc_part.capsule_bits":
        coll = _settings(context).target
        own = sorted((o.name for o in collision.roots(context.scene) if coll and o.name in coll.all_objects))
        names = own + [_short(s[0]) + " (CLSP)" for s in clsp if s[1] == "CAPSULE"]
        return {i: name for i, name in enumerate(names) if i < 32}
    return {}


def _short(name: str) -> str:
    """A chain editor's collision name without its COL_NN_ prefix: the joints."""
    return name.split(" - ", 1)[1] if " - " in name else name.split(" ", 1)[-1]


BIT_FIELDS = ("ree_gpuc_part.clsp_flags_a", "ree_gpuc_part.clsp_flags_b", "ree_gpuc_part.plane_bits",
              "ree_gpuc_part.sphere_bits", "ree_gpuc_part.capsule_bits", "ree_gpuc_part.obb_bits",
              "ree_gpuc_header.kernel_flags", "ree_gpuc_config.info_state",
              "ree_gpuc_config.translation_curve.clamp_flags", "ree_gpuc_config.rotation_curve.clamp_flags")


class REE_GPUC_OT_set_bits(bpy.types.Operator):
    """Tick the bits of this flag field"""
    bl_idname = "ree_gpuc.set_bits"
    bl_label = "Set Flags"
    bl_options = {"REGISTER", "UNDO", "INTERNAL"}
    __annotations__ = {"target": bpy.props.StringProperty(options={"HIDDEN"}),
                       **{f"bit{i}": bpy.props.BoolProperty(name=f"Bit {i}") for i in range(32)}}

    def _data(self, context):
        *path, prop = self.target.split(".")
        data = context.active_object
        for name in path:
            data = getattr(data, name)
        return data, prop

    def invoke(self, context, event):
        if self.target not in BIT_FIELDS or context.active_object is None:
            return {"CANCELLED"}
        data, prop = self._data(context)
        value = getattr(data, prop) & 0xFFFFFFFF
        for i in range(32):
            setattr(self, f"bit{i}", bool(value >> i & 1))
        return context.window_manager.invoke_props_dialog(self, width=360)

    def draw(self, context):
        names = _bit_names(context, self.target)
        listed = max(names) + 1 if names else 0          # one list in bit order up to the last named bit
        col = self.layout.column()
        for i in range(listed):
            col.prop(self, f"bit{i}", text=names.get(i, f"bit{i}"))
        flow = self.layout.grid_flow(row_major=True, columns=4, even_columns=True)
        for i in range(listed, 32):
            flow.prop(self, f"bit{i}", text=f"bit{i}")

    def execute(self, context):
        data, prop = self._data(context)
        value = sum(1 << i for i in range(32) if getattr(self, f"bit{i}"))
        setattr(data, prop, value - (1 << 32) if value >= 1 << 31 else value)       # a signed 32-bit integer
        return {"FINISHED"}


# --- config curves ------------------------------------------------------------------------------------------

_CURVE = {"curve": bpy.props.EnumProperty(items=[(name, label, "") for name, label, _, _ in props.CURVES],
                                          options={"HIDDEN"})}


def _curve(context, name):
    obj = context.active_object
    return getattr(obj.ree_gpuc_config, name) if props.is_config(obj) else None


class REE_GPUC_OT_curve_key_add(bpy.types.Operator):
    """Add a key halfway between the active key and the next"""
    bl_idname = "ree_gpuc.curve_key_add"
    bl_label = "Add Key"
    bl_options = {"REGISTER", "UNDO", "INTERNAL"}
    __annotations__ = _CURVE

    def execute(self, context):
        curve = _curve(context, self.curve)
        if curve is None:
            return {"CANCELLED"}
        keys, i = curve.keys, curve.active_key
        if len(keys) and i < len(keys):
            a = keys[i]
            b = keys[i + 1] if i + 1 < len(keys) else None
            time, value, kind = ((a.time + b.time) / 2, (a.value + b.value) / 2, a.type) if b else (1.0, a.value, a.type)
        else:
            i, time, value, kind = len(keys) - 1, 0.0, 0.0, "LINEAR"
        k = keys.add()
        k.time, k.value, k.type = time, value, kind
        keys.move(len(keys) - 1, i + 1)
        curve.active_key = i + 1
        return {"FINISHED"}


class REE_GPUC_OT_curve_key_remove(bpy.types.Operator):
    """Remove the active key"""
    bl_idname = "ree_gpuc.curve_key_remove"
    bl_label = "Remove Key"
    bl_options = {"REGISTER", "UNDO", "INTERNAL"}
    __annotations__ = _CURVE

    def execute(self, context):
        curve = _curve(context, self.curve)
        if curve is None or not len(curve.keys):
            return {"CANCELLED"}
        curve.keys.remove(min(curve.active_key, len(curve.keys) - 1))
        curve.active_key = max(0, min(curve.active_key, len(curve.keys) - 1))
        return {"FINISHED"}


class REE_GPUC_OT_curve_sort(bpy.types.Operator):
    """Put the keys in time order"""
    bl_idname = "ree_gpuc.curve_sort"
    bl_label = "Sort Keys"
    bl_options = {"REGISTER", "UNDO", "INTERNAL"}
    __annotations__ = _CURVE

    def execute(self, context):
        curve = _curve(context, self.curve)
        if curve is None:
            return {"CANCELLED"}
        keys = sorted(((k.time, k.value, k.type, k.in_tangent, k.in_weight, k.out_tangent, k.out_weight)
                       for k in curve.keys), key=lambda k: k[0])
        curve.set_keys(keys)
        return {"FINISHED"}


class REE_GPUC_OT_curve_reset(bpy.types.Operator):
    """The curve's keys back to the default (ch03_100_1005's)"""
    bl_idname = "ree_gpuc.curve_reset"
    bl_label = "Reset Curve"
    bl_options = {"REGISTER", "UNDO", "INTERNAL"}
    __annotations__ = _CURVE

    def execute(self, context):
        curve = _curve(context, self.curve)
        if curve is None:
            return {"CANCELLED"}
        curve.set_keys(props.CURVE_DEFAULTS[self.curve])
        return {"FINISHED"}


# --- particles ----------------------------------------------------------------------------------------------

class REE_GPUC_OT_edit_particles(bpy.types.Operator):
    """Edit mode on the cloth's parts, to select particles in the 3D view or on the grid UV"""
    bl_idname = "ree_gpuc.edit_particles"
    bl_label = "Select Particles"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return bool(props.parts(_settings(context).target))

    def execute(self, context):
        if context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        objs = [o for o in props.parts(_settings(context).target) if o.visible_get()]
        if not objs:
            self.report({"ERROR"}, "No visible part")
            return {"CANCELLED"}
        for o in context.view_layer.objects.selected:
            o.select_set(False)
        for o in objs:
            o.select_set(True)
        context.view_layer.objects.active = objs[0]
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.mesh.select_mode(type="VERT")
        context.scene.tool_settings.use_uv_select_sync = True
        return {"FINISHED"}


class REE_GPUC_OT_reset_rest_offsets(bpy.types.Operator):
    """Restore generated particles to the positions generated from the current rest bones"""
    bl_idname = "ree_gpuc.reset_rest_offsets"
    bl_label = "Reset Rest Offsets"
    bl_options = {"REGISTER", "UNDO"}
    selected_only: bpy.props.BoolProperty(default=True)

    @classmethod
    def poll(cls, context):
        objs = context.objects_in_mode if context.mode == "EDIT_MESH" else [context.object]
        return any(props.is_part(o) and canon.has(o) and not canon.locked(o) for o in objs if o)

    def execute(self, context):
        objs = list(context.objects_in_mode) if context.mode == "EDIT_MESH" else [context.object]
        for obj in objs:
            if not props.is_part(obj) or not canon.has(obj) or canon.locked(obj):
                continue
            d = canon.load(obj)
            if obj.mode == "EDIT":
                bm = bmesh.from_edit_mesh(obj.data)
                layer = bm.verts.layers.int.get(canon.PARTICLE_ID)
                if layer is None or len(bm.verts) != canon.count(d):
                    return refused(self, "Particle structure changed: leave Edit Mode to restore it first")
                for v in bm.verts:
                    if self.selected_only and not v.select:
                        continue
                    k = v[layer]
                    if k < 0 or k >= len(d["base_co"]):
                        return refused(self, "Invalid particle ID")
                    v.co = d["base_co"][k]
                bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
            else:
                canon.restore(obj)
                d = canon.load(obj)
                co = d["co"].copy()
                indices = [v.index for v in obj.data.vertices if v.select or not self.selected_only]
                co[indices] = d["base_co"][indices]
                try:
                    canon.set_positions(obj, co)
                except ValueError as e:
                    return refused(self, str(e))
                obj.data.vertices.foreach_set("co", co.ravel())
                obj.data.update()
        return {"FINISHED"}


class REE_GPUC_OT_set_value(bpy.types.Operator):
    """Set the chosen particle value on the selected particles"""
    bl_idname = "ree_gpuc.set_value"
    bl_label = "Set on Selected"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return context.mode == "EDIT_MESH" and any(props.is_part(o) for o in context.objects_in_mode)

    def execute(self, context):
        s = _settings(context)
        value = float(np.float32(s.value))
        s.colour_by = s.field
        if s.field == "sim_weight":
            value = min(max(value, 0.0), 1.0)
        count, clipped = 0, []
        for obj in context.objects_in_mode:
            if props.is_re4_object(obj) and s.field not in fileio.re4.PARTICLE_VALUES:
                return refused(self, f"RE4 has no particle field {s.field}")
            g = obj.vertex_groups.get(canon.group_name(s.field)) if props.is_part(obj) else None
            if g is None or not canon.has(obj):
                continue
            lo, hi = canon.field_range(obj, s.field)
            if not lo <= value <= hi:
                clipped.append(obj.name)
            weight = float(canon.to_weight(obj, s.field, [value])[0])
            bm = bmesh.from_edit_mesh(obj.data)
            layer = bm.verts.layers.deform.verify()
            index = [v.index for v in bm.verts if v.select]
            for v in bm.verts:
                if v.select:
                    v[layer][g.index] = weight
            bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
            canon.set_values(obj, s.field, index, float(canon.to_value(obj, s.field, [weight])[0]))
            count += len(index)
        if clipped:
            self.report({"WARNING"}, f"{count} particles; outside the range of {', '.join(clipped)}: clipped "
                                     "(widen the Part's range)")
        else:
            self.report({"INFO"}, f"{count} particles")
        return {"FINISHED"}


class REE_GPUC_OT_paint(bpy.types.Operator):
    """Weight Paint the chosen value's group on the active part (or the cloth's first part)"""
    bl_idname = "ree_gpuc.paint"
    bl_label = "Paint"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return bool(props.parts(_settings(context).target)) or props.is_part(context.active_object)

    def execute(self, context):
        s = _settings(context)
        obj = context.active_object if props.is_part(context.active_object) else props.parts(s.target)[0]
        if props.is_re4_object(obj) and s.field not in fileio.re4.PARTICLE_VALUES:
            return refused(self, f"RE4 has no particle field {s.field}")
        s.colour_by = s.field
        if context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        _select_only(context, obj)
        g = obj.vertex_groups.get(canon.group_name(s.field))
        if g is not None:
            obj.vertex_groups.active_index = g.index
        bpy.ops.object.mode_set(mode="WEIGHT_PAINT")
        return {"FINISHED"}


# --- preview simulation -------------------------------------------------------------------------------------

class REE_GPUC_OT_simulate(bpy.types.Operator):
    """Start or stop the preview simulation of the cloth's parts"""
    bl_idname = "ree_gpuc.simulate"
    bl_label = "Simulate"

    @classmethod
    def poll(cls, context):
        return sim.running() or (not props.is_re4(_settings(context).target) and bool(sim.parts(context.scene)))

    def execute(self, context):
        if sim.running():
            sim.stop()
        else:
            try:
                sim.start(context.scene)
            except ValueError as e:
                return refused(self, str(e))
        for area in context.screen.areas:
            area.tag_redraw()
        return {"FINISHED"}


class REE_GPUC_OT_sim_reset(bpy.types.Operator):
    """Put the simulated particles back on their skinned places"""
    bl_idname = "ree_gpuc.sim_reset"
    bl_label = "Reset"

    @classmethod
    def poll(cls, context):
        return sim.running()

    def execute(self, context):
        for obj in sim.parts(context.scene):
            sim.reset(obj, context.scene)
        return {"FINISHED"}


# --- the current Config follows the selection ---------------------------------------------------------------

def follow_selection(obj, scene) -> None:
    """The cloth and current Config follow the active object: a Config is made current, a Part makes its
    Config current (what new parts go under); a header makes its cloth active."""
    s = scene.ree_gpuc
    from . import presets
    presets.follow_selection(obj, scene)
    cfg = obj if props.is_config(obj) else obj.ree_gpuc_part.config if props.is_part(obj) else None
    owner = cfg if props.is_config(cfg) else obj if props.is_header(obj) else None
    if owner is None:
        return
    coll = next((c for c in owner.users_collection if props.is_gpuc_collection(c)), None)
    if coll is not None and s.target != coll:
        s.target = coll
    if props.is_config(cfg) and s.config != cfg:
        s.config = cfg


_owner = object()


def _active_changed():
    ctx = bpy.context
    if ctx.view_layer is not None and ctx.view_layer.objects.active is not None:
        follow_selection(ctx.view_layer.objects.active, ctx.scene)


def _collection_changed():
    """A cloth's collection (or one inside it) clicked in the Outliner makes that cloth active."""
    ctx = bpy.context
    lc = ctx.view_layer.active_layer_collection if ctx.view_layer is not None else None
    if lc is None:
        return
    coll = lc.collection
    cloth = coll if props.is_gpuc_collection(coll) else next(
        (c for c in bpy.data.collections if props.is_gpuc_collection(c) and coll in c.children_recursive), None)
    if cloth is not None and ctx.scene.ree_gpuc.target != cloth:
        ctx.scene.ree_gpuc.target = cloth


@bpy.app.handlers.persistent
def _subscribe(*_):
    bpy.msgbus.clear_by_owner(_owner)
    bpy.msgbus.subscribe_rna(key=(bpy.types.LayerObjects, "active"), owner=_owner, args=(),
                             notify=_active_changed)
    bpy.msgbus.subscribe_rna(key=(bpy.types.ViewLayer, "active_layer_collection"), owner=_owner, args=(),
                             notify=_collection_changed)


CLASSES = (REE_GPUC_OT_import, REE_GPUC_FH_import, REE_GPUC_OT_export, REE_GPUC_OT_create_header, REE_GPUC_OT_new_config,
           REE_GPUC_OT_check, REE_GPUC_OT_switch_to_pose, REE_GPUC_OT_switch_to_object, REE_GPUC_OT_part_from_parents,
           REE_GPUC_OT_part_from_selected, REE_GPUC_OT_regenerate_part, REE_GPUC_OT_select_part_bones,
           REE_GPUC_OT_add_collider, REE_GPUC_OT_remove_collider, REE_GPUC_OT_collider_from_bones, REE_GPUC_OT_copy, REE_GPUC_OT_paste,
           REE_GPUC_OT_set_bits, REE_GPUC_OT_curve_key_add, REE_GPUC_OT_curve_key_remove, REE_GPUC_OT_curve_sort,
           REE_GPUC_OT_curve_reset, REE_GPUC_OT_edit_particles,
           REE_GPUC_OT_reset_rest_offsets, REE_GPUC_OT_set_value, REE_GPUC_OT_paint,
           REE_GPUC_OT_simulate, REE_GPUC_OT_sim_reset)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    _subscribe()
    bpy.app.handlers.load_post.append(_subscribe)


def unregister():
    bpy.msgbus.clear_by_owner(_owner)
    if _subscribe in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_subscribe)
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)

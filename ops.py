"""Operators that import, export and edit GPUC cloth, and the msgbus hooks that make the active cloth and Config
follow the selection."""
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


# --- import / export ----------------------------------------------------------------------------------------

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
    ("UPDATE", "Update Existing", "Write the file's values onto the GPUC collection it was imported or exported "
                                  "from. Refused if the two structures differ"),
    ("KEEP", "Keep Structure", "Create a new GPUC collection with the file's particles, faces, links and skinning. "
                               "Every value can be edited; the structure cannot be regenerated"),
    ("REBUILD", "Rebuild from Bones", "Rebuild the parts from the mesh's bones with the file's values, so they can be "
                                      "regenerated. Uses Keep Structure if the rebuild does not reproduce the "
                                      "file's structure. Files exported by this editor always rebuild"),
]


class REE_GPUC_OT_import(bpy.types.Operator, ImportHelper):
    """Import .gpuc files as new cloth, or write their values onto an existing cloth"""
    bl_idname = "ree_gpuc.import_file"
    bl_label = "Import"
    bl_options = {"PRESET", "REGISTER", "UNDO"}

    filter_glob: bpy.props.StringProperty(default="*.gpuc.*", options={"HIDDEN"})
    files: bpy.props.CollectionProperty(type=bpy.types.OperatorFileListElement, options={"HIDDEN", "SKIP_SAVE"})
    directory: bpy.props.StringProperty(subtype="DIR_PATH", options={"HIDDEN", "SKIP_SAVE"})
    import_mode: bpy.props.EnumProperty(name="Import Mode", items=IMPORT_MODES, default="REBUILD")
    gpuc_collection: bpy.props.StringProperty(name="", description="The GPUC collection that receives the file's values")
    mesh_collection: bpy.props.StringProperty(
        name="", description="The RE Mesh Editor collection of the mesh this cloth belongs to. Its armature is used "
                             "to name bones, find driven joints and place capsules. Optional for Keep Structure, "
                             "which then imports the shape only")
    force_rebuild: bpy.props.BoolProperty(
        name="Force Rebuild", default=False,
        description="Keep the rebuilt cloth even when it differs from the file's structure (positions, links, "
                    "skinning or driven joints as the editor generates them; each link shape uses its most common "
                    "settings). The differences are reported. Parts that are not a grid of bone chains still import "
                    "with Keep Structure")

    def invoke(self, context, event):
        if not self.mesh_collection:
            mesh = _mesh_guess(context)
            self.mesh_collection = mesh.name if mesh is not None else ""
        target = _settings(context).target
        if target is not None and not self.gpuc_collection:
            self.gpuc_collection = target.name
        if self.directory:                                   # dropped on the 3D view: no file browser
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
            layout.label(text="Without a mesh: shape only, cannot be posed", icon="INFO")
        if self.import_mode == "REBUILD":
            layout.prop(self, "force_rebuild")
            if not self.mesh_collection:
                layout.label(text="No mesh collection: imports with Keep Structure", icon="INFO")

    def execute(self, context):
        paths = self.paths()
        wrong = [os.path.basename(p) for p in paths
                 if not (".gpuc" in os.path.basename(p) and fileio.gp.ext_of_path(p))]
        if wrong:
            return refused(self, f"Only MH Wilds, RE9, Onimusha and RE4 .gpuc files can be "
                                 f"imported ({wrong[0]})")
        if self.import_mode == "UPDATE" and len(paths) > 1:
            return refused(self, "Update Existing imports one file at a time")
        mesh = bpy.data.collections.get(self.mesh_collection) if self.mesh_collection else None
        if context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        mode = self.import_mode
        fell_back = []                                         # (file, why) imported with Keep Structure instead
        if mode == "REBUILD" and mesh is None:
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
                        return refused(self, "Choose the GPUC collection that receives the values")
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
                if note.startswith("Not rebuilt ("):           # note format set by fileio.import_rebuild
                    fell_back.append((name, note[len("Not rebuilt ("):note.rfind("): imported with")]))
        if fell_back and done:
            _fell_back_popup(context, fell_back)
        if not done:
            return {"CANCELLED"}
        context.scene["REEGpucLastImportedVersion"] = int(fileio.gp.ext_of_path(paths[-1])[1:])
        self.report({"INFO"}, f"Imported {', '.join(done)} ({dict((m[0], m[1]) for m in IMPORT_MODES)[mode]})")
        return {"FINISHED"}


def _fell_back_popup(context, fell_back) -> None:
    """List in a popup the files imported with Keep Structure instead of rebuilt; does nothing in background mode."""
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
        col.label(text="Their Parts keep the file's structure: values can be edited, but not regenerated")
    context.window_manager.popup_menu(draw, title="Imported with Keep Structure, not rebuilt", icon="ERROR")


class REE_GPUC_FH_import(bpy.types.FileHandler):
    """Import a .gpuc dropped on the 3D view"""
    bl_idname = "REE_GPUC_FH_import"
    bl_label = "RE GPUC import"
    bl_import_operator = "ree_gpuc.import_file"
    # Blender matches only the last extension (the version, shared with other formats); the import refuses non-.gpuc.
    bl_file_extensions = ";".join(fileio.gp.EXTS)

    @classmethod
    def poll_drop(cls, context):
        return context.area is not None and context.area.type == "VIEW_3D"


EXPORT_KEY = "ree_gpuc_last_export"     # on the header and the scene: the path last exported to


class REE_GPUC_OT_export(bpy.types.Operator, ExportHelper):
    """Export a GPUC collection as a .gpuc file"""
    bl_idname = "ree_gpuc.export_file"
    bl_label = "Export"
    bl_options = {"PRESET"}

    filename_ext: bpy.props.EnumProperty(name="", description="The game to export the .gpuc for",
                                         items=fileio.GAMES)
    filter_glob: bpy.props.StringProperty(default="*.gpuc*", options={"HIDDEN"})
    target_collection: bpy.props.StringProperty(name="", description="The GPUC collection to export")
    mesh_collection: bpy.props.StringProperty(
        name="", description="The RE Mesh Editor collection of the mesh this cloth belongs to: with Force Export and "
                             "no matching .mesh, its weighted bones give the driven joints' indices. Leave blank to "
                             "use the armature's collection")
    mesh_file: bpy.props.StringProperty(
        name="Mesh File", description="The exported .mesh whose weighted bones give the driven joints' indices "
                                      "(paste its full path). Blank: the same-name .mesh beside the .gpuc, else the "
                                      "one the last export used", options={"SKIP_SAVE"})
    joint_indices: bpy.props.EnumProperty(
        name="Joint Indices", items=fileio.JOINT_INDICES, default="FILE",
        description="How driven joint indices are written. Cloth imported with Keep Structure defaults to As "
                    "Imported; cloth made from bones always uses From Mesh")
    force_export: bpy.props.BoolProperty(
        name="Force Export", default=False, options={"SKIP_SAVE"},
        description="Export without an exported .mesh to check the cloth against: the driven joints' indices then "
                    "come from the scene's weights, and a mesh that does not match makes the cloth behave wrongly "
                    "in game. A cloth that drives mesh vertices still needs the .mesh")

    def invoke(self, context, event):
        coll = _settings(context).target
        if coll is not None and not self.target_collection:
            self.target_collection = coll.name
        if coll is not None:
            head = props.header(coll)
            if head is not None:
                self.filename_ext = head.ree_gpuc_header.game
            name = coll.name.split(".gpuc")[0] + ".gpuc" + self.filename_ext
            # folder: this cloth's last export, else its source file, else the scene's last export
            last = head.get(EXPORT_KEY, "") if head is not None else ""
            source = head.get(fileio.SOURCE_KEY, "") if head is not None else ""
            folder = os.path.dirname(last or source or context.scene.get(EXPORT_KEY, ""))
            self.filepath = os.path.join(folder, name) if folder else name
        target = bpy.data.collections.get(self.target_collection)
        # A Keep Structure cloth assumes its original mesh; a cloth made from bones assumes a mesh re-exported by the
        # RE Mesh Editor, whose bone indices follow the new weights.
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
            layout.label(text="Exported with the imported file's structure", icon="INFO")
            layout.label(text="Joint Indices:")
            layout.prop(self, "joint_indices", text="")
            if self.joint_indices == "MESH":
                self._draw_mesh(layout)
            return
        head = props.header(coll) if coll is not None else None
        if head is not None and head.ree_gpuc_header.drive_mode == "VERTICES":
            layout.label(text="Drives mesh vertices: read from the exported .mesh", icon="INFO")
        else:
            layout.label(text="Joint Indices: From Mesh (cloth made from bones)")
        self._draw_mesh(layout)

    def _draw_mesh(self, layout):
        # a text field: a file path button would open a second file browser inside this one
        layout.label(text="Mesh File (blank: same name beside):")
        layout.prop(self, "mesh_file", text="")
        layout.prop(self, "force_export")
        layout.label(text="Mesh Collection (blank: the armature's):")
        layout.prop_search(self, "mesh_collection", bpy.data, "collections")

    def execute(self, context):
        coll = bpy.data.collections.get(self.target_collection)
        if coll is None or not props.is_gpuc_collection(coll):
            return refused(self, "Choose the GPUC collection to export")
        mesh = bpy.data.collections.get(self.mesh_collection) if self.mesh_collection else None
        if context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        try:
            notes = fileio.export(coll, self.filepath, mesh, self.joint_indices, self.filename_ext, self.mesh_file,
                                  self.force_export)
        except fileio.ExportError as e:
            return refused(self, f"Not exported: {e}")
        for note in notes:
            self.report({"INFO"} if isinstance(note, fileio.Info) else {"WARNING"}, note)
        written = props.header(coll).get(fileio.EXPORTED_KEY, self.filepath)    # renamed after a matching .mesh
        props.header(coll)[EXPORT_KEY] = written
        context.scene[EXPORT_KEY] = written
        self.report({"INFO"}, f"Exported {os.path.basename(written)}")
        return {"FINISHED"}


# --- Object Mode --------------------------------------------------------------------------------------------

def _mesh_name(coll) -> str:
    """Return a RE Mesh Editor collection's name without ``.mesh``, or "" for any other collection."""
    if coll is None or coll.get("~TYPE") != "RE_MESH_COLLECTION" and ".mesh" not in coll.name:
        return ""
    return coll.name.split(".mesh")[0]


def suggest(context) -> tuple[str, str]:
    """Return a default cloth name and armature name.

    The name comes from the armature's mesh collection, else the last imported mesh, else the armature; the
    armature is the active one, else the one in the last imported mesh collection.
    """
    obj = context.active_object
    arm = obj if obj is not None and obj.type == "ARMATURE" else None
    last = bpy.data.collections.get(context.scene.get("REMeshLastImportedCollection", ""))
    if arm is None and last is not None:
        arm = next((o for o in last.all_objects if o.type == "ARMATURE"), None)
    name = next((n for n in map(_mesh_name, arm.users_collection if arm else ()) if n), "")
    name = name or _mesh_name(last) or (arm.name if arm else "") or "newCloth"
    return name, arm.name if arm else ""


class REE_GPUC_OT_create_header(bpy.types.Operator):
    """Create a new cloth: a .gpuc collection with a header on the armature and one Config"""
    bl_idname = "ree_gpuc.create_header"
    bl_label = "Create GPUC Header"
    bl_options = {"REGISTER", "UNDO"}

    name: bpy.props.StringProperty(name="Name", default="newCloth",
                                   description="The .gpuc's name. Use the mesh file's name, e.g. ch03_039_0012 "
                                               "for ch03_039_0012.mesh")
    armature: bpy.props.StringProperty(name="Armature")
    game: bpy.props.EnumProperty(name="Game", items=props.GAMES, description="The game the .gpuc is for")

    def invoke(self, context, event):
        self.name, self.armature = suggest(context)
        last = f".{context.scene.get('REEGpucLastImportedVersion', '')}"   # default to the last imported game
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
        if arm is None or arm.type != "ARMATURE":
            self.report({"ERROR"}, "Choose the armature the cloth is on")
            return {"CANCELLED"}
        _settings(context).target = generate.new_collection(self.name, arm, self.game)
        return {"FINISHED"}


class REE_GPUC_OT_new_config(bpy.types.Operator):
    """Add a Config under the active cloth's header and make it current; new parts go under it"""
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
    """Switch the active cloth's armature to Pose Mode"""
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
    """Switch back to Object Mode"""
    bl_idname = "ree_gpuc.switch_to_object"
    bl_label = "Switch to Object Mode"

    def execute(self, context):
        bpy.ops.object.mode_set(mode="OBJECT")
        return {"FINISHED"}


# --- Pose Mode: parts from bones ----------------------------------------------------------------------------

RING = [("AUTO", "Auto", "Closed if the chains go all the way around"),
        ("CLOSED", "Closed", "Join the last chain to the first, e.g. a skirt"),
        ("OPEN", "Open", "Leave one side open, e.g. a cape or a panel")]


class _PartOptions:
    closed: bpy.props.EnumProperty(name="Ring", items=RING, default="AUTO")
    truncate: bpy.props.BoolProperty(name="Cut to the shortest chain")
    sub_rows: bpy.props.IntProperty(name="Between bones, down", default=0, min=0, max=8,
                                    description="Extra particles between two bones of a chain. 0: one per bone")
    sub_cols: bpy.props.IntProperty(name="Between chains, across", default=0, min=0, max=8,
                                    description="Extra columns of particles between two neighbouring chains")

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
            return refused(self, f"{s.target.name} was imported with Keep Structure and cannot take new parts. "
                                 f"Create a new GPUC collection for parts from bones")
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
    """Create a part from the selected first bone of each chain; each chain runs down to its last bone and must
    not branch"""
    bl_idname = "ree_gpuc.part_from_parents"
    bl_label = "Create Part From Parent Bones"
    bl_options = {"REGISTER", "UNDO"}

    @staticmethod
    def chains(arm):
        return generate.chains_from_parents(arm)


class REE_GPUC_OT_part_from_selected(_PartOptions, bpy.types.Operator):
    """Create a part from the selected bones: each chain becomes a column, with a particle on every bone"""
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
    """Lay out the part's particles again from its bones, optionally with a new density or ring. Values are kept
    and carried over when the grid size changes"""
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
            self.report({"ERROR"}, f"{obj.name} is in Edit Mode. Return to Object Mode first")
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


# --- collision: test colliders and .gpuc collision ----------------------------------------------------------

def new_collider(context, kind: str, label: str = "") -> bpy.types.Object:
    obj = bpy.data.objects.new(generate.unique_name("COL_", f"_{kind.title()}{' ' + label if label else ''}"), None)
    obj["TYPE"] = props.COLLIDER_TYPE
    obj.hide_render = True
    coll = _settings(context).target
    (collision.sim_collection(coll) if coll is not None else context.scene.collection).objects.link(obj)
    obj.ree_gpuc_collider.kind = kind
    props._collider(obj.ree_gpuc_collider, context)      # sets the empty's display for the kind
    return obj


class REE_GPUC_OT_add_collider(bpy.types.Operator):
    """Add a test collider at the 3D cursor. It affects only the preview simulation and is not exported"""
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
    """Add .gpuc collision on the selected bones: a sphere on each bone's head, a capsule between the heads of
    two bones, or (RE4) a plane through each bone's head facing its +Y"""
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
            self.report({"ERROR"}, "Select one or two bones for a capsule")
            return {"CANCELLED"}
        if s.target is None:
            s.target = generate.new_collection(arm.name, arm)
        if s.collider_kind == "PLANE":
            if not props.is_re4(s.target):
                self.report({"ERROR"}, "Only RE4 cloth has collision planes")
                return {"CANCELLED"}
            for pb in sel:
                collision.new_plane(s.target, arm, pb.name)
            self.report({"INFO"}, f"{len(sel)} planes")
            return {"FINISHED"}
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

# The property group Copy reads per object type; CLIP_SKIP keeps names, links and a Part's grid out of the clipboard.
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
            later.append((key, value))              # after the tails: editing a tail sets the preset to Custom
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
    """Copy the values of the active header, Config or Part"""
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

# Bit names of the full-body CLSP made by the RE Chain Editor's Create Full Body CLSP (its FULL_BODY_CLSP_NAMES).
FULL_BODY_CLSP = {0: "Hip_HJ_00", 1: "Spine_0", 2: "Spine_1", 3: "Spine_2", 4: "Neck_0 > L_UpperArm",
                  5: "Neck_0 > R_UpperArm", 6: "L_UpperArm > L_Forearm", 7: "R_UpperArm > R_Forearm",
                  8: "L_Forearm > L_Hand", 9: "R_Forearm > R_Hand", 10: "L_Hand > L_MiddleF1", 11: "R_Hand > R_MiddleF1",
                  12: "Neck_0 > Head", 13: "Head", 14: "L_Thigh > L_Knee", 15: "R_Thigh > R_Knee",
                  16: "L_Shin > L_Foot", 17: "R_Shin > R_Foot", 18: "L_Foot > L_Toe", 19: "R_Foot > R_Toe",
                  23: "Hip_HJ_00 (second shape)"}
KERNEL_FLAGS = {1: "Joint Deform Info (uncertain)", 2: "Mesh Multi Joint Deform", 3: "Solve Constraint Legacy",
                4: "Tangent Passes"}


def _bit_names(context, target) -> dict:
    """Return {bit: name} for the known bits of a flag field; other bits are listed by number.

    CLSP flags are named by the Reference CLSP shapes that set them; sphere and capsule bits follow the shape order
    (the cloth's own capsules, then the Reference CLSP's).
    """
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
    coll = _settings(context).target
    re4_own = props.is_re4(coll)                 # RE4 writes its own spheres and planes apart from its capsules
    if target == "ree_gpuc_part.plane_bits" and re4_own:
        own = sorted(o.name for o in collision.planes(context.scene) if o.name in coll.all_objects)
        return {i: name for i, name in enumerate(own) if i < 32}
    if target == "ree_gpuc_part.sphere_bits":
        own = sorted(o.name for o in collision.roots(context.scene) if re4_own and o.name in coll.all_objects
                     and collision.is_sphere(o.ree_gpuc_capsule))
        names = own + [_short(s[0]) + " (CLSP)" for s in clsp if s[1] == "SPHERE"]
        return {i: name for i, name in enumerate(names) if i < 32}
    if target == "ree_gpuc_header.kernel_flags":
        return KERNEL_FLAGS
    if target == "ree_gpuc_config.info_state":
        return props.STATE_BITS
    if target.endswith("_curve.clamp_flags"):
        return {0: "Clamp"}
    if target == "ree_gpuc_part.capsule_bits":
        own = sorted((o.name for o in collision.roots(context.scene) if coll and o.name in coll.all_objects
                      and not (re4_own and collision.is_sphere(o.ree_gpuc_capsule))))
        names = own + [_short(s[0]) + " (CLSP)" for s in clsp if s[1] == "CAPSULE"]
        return {i: name for i, name in enumerate(names) if i < 32}
    return {}


def _short(name: str) -> str:
    """Return the joint part of a RE Chain Editor collision name, without its ``COL_NN_`` prefix."""
    return name.split(" - ", 1)[1] if " - " in name else name.split(" ", 1)[-1]


BIT_FIELDS = ("ree_gpuc_part.clsp_flags_a", "ree_gpuc_part.clsp_flags_b", "ree_gpuc_part.plane_bits",
              "ree_gpuc_part.sphere_bits", "ree_gpuc_part.capsule_bits", "ree_gpuc_part.obb_bits",
              "ree_gpuc_header.kernel_flags", "ree_gpuc_config.info_state",
              "ree_gpuc_config.translation_curve.clamp_flags", "ree_gpuc_config.rotation_curve.clamp_flags")


class REE_GPUC_OT_set_bits(bpy.types.Operator):
    """Set the individual bits of this flag field"""
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
        listed = max(names) + 1 if names else 0          # a list up to the last named bit, then a grid
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
    """Reset the curve's keys to the default"""
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
    """Enter Edit Mode on the cloth's visible parts to select particles in the 3D view or the UV Editor"""
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
    """Move particles back to the positions generated from the current rest bones"""
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
                    return refused(self, "The particle mesh was changed. Leave Edit Mode to restore it first")
                for v in bm.verts:
                    if self.selected_only and not v.select:
                        continue
                    k = v[layer]
                    if k < 0 or k >= len(d["base_co"]):
                        return refused(self, "A particle has an invalid ID. Leave Edit Mode to restore the mesh")
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
            self.report({"WARNING"}, f"{count} particles; clipped to the mapping range of {', '.join(clipped)}. "
                                     "Widen the Part's range to allow this value")
        else:
            self.report({"INFO"}, f"{count} particles")
        return {"FINISHED"}


class REE_GPUC_OT_paint(bpy.types.Operator):
    """Weight Paint the chosen value on the active part, or on the cloth's first part"""
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
    """Move the simulated particles back to their skinned positions"""
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
    """Make the active object's cloth and Config current: a Config itself, a Part's Config, or a header's cloth."""
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
    """Make a cloth active when its collection, or one inside it, becomes the active layer collection."""
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
def _subscribe(*_):          # loading a file clears msgbus subscriptions, so load_post subscribes again
    bpy.msgbus.clear_by_owner(_owner)
    bpy.msgbus.subscribe_rna(key=(bpy.types.LayerObjects, "active"), owner=_owner, args=(),
                             notify=_active_changed)
    bpy.msgbus.subscribe_rna(key=(bpy.types.ViewLayer, "active_layer_collection"), owner=_owner, args=(),
                             notify=_collection_changed)


# --- reference mesh: the exported .mesh imported as it is, its driven vertices picked on it --------------------

def _cloth_head(context):
    """The header of the cloth an operator acts for: the active object's when it is one, else the sidebar's."""
    obj = context.object
    return obj if props.is_header(obj) else props.header(_settings(context).target)


def _cloth_of_head(head):
    return next((c for c in bpy.data.collections if props.is_gpuc_collection(c) and head.name in c.objects), None)


class REE_GPUC_OT_import_reference(bpy.types.Operator, ImportHelper):
    """Import the exported .mesh as it is (every LOD) as the cloth's reference mesh, to pick the vertices it drives.
    When the cloth has a reference already, its picked vertices carry over by place"""
    bl_idname = "ree_gpuc.import_reference"
    bl_label = "Import Reference Mesh"
    bl_options = {"REGISTER", "UNDO"}

    filter_glob: bpy.props.StringProperty(default="*.mesh.*", options={"HIDDEN"})

    def invoke(self, context, event):
        head = _cloth_head(context)
        if head is None:
            return refused(self, "Choose a cloth first")
        coll = _cloth_of_head(head)
        found = fileio.find_mesh(coll)[0] if coll is not None else ""
        if found:
            self.filepath = found
        return super().invoke(context, event)

    def execute(self, context):
        head = _cloth_head(context)
        if head is None:
            return refused(self, "Choose a cloth first")
        if context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        h = head.ree_gpuc_header
        old = h.drive_reference if fileio.is_reference(h.drive_reference) else None
        try:
            ref, notes = fileio.import_reference(self.filepath)
        except fileio.ExportError as e:
            return refused(self, f"Not imported: {e}")
        if old is not None:
            lost = fileio.carry_selection(old, ref)
            notes.append(f"Picked vertices carried over from {old.name}"
                         + (f"; {lost} found no vertex at their place: pick them again" if lost else "")
                         + f". {old.name} is kept: delete it when done")
        h.drive_reference = ref
        h.drive_mode, h.drive_selection = "VERTICES", "REFERENCE"
        for note in notes:
            self.report({"INFO"}, note)
        return {"FINISHED"}


def _reference_edit_objects(context) -> list:
    return [o for o in context.objects_in_mode_unique_data if fileio.SUBMESH_KEY in o.keys()]


DRIVE_ACTIONS = [("ADD", "Add Selected", "Put the selected vertices in GPUC_Drive: the cloth drives them"),
                 ("REMOVE", "Remove Selected", "Take the selected vertices out of GPUC_Drive"),
                 ("SELECT", "Select Driven", "Select the vertices in GPUC_Drive (the selection only)")]


class REE_GPUC_OT_drive_vertices(bpy.types.Operator):
    """Add or remove the selected reference mesh vertices as driven, or select the driven ones"""
    bl_idname = "ree_gpuc.drive_vertices"
    bl_label = "Driven Vertices"
    bl_options = {"REGISTER", "UNDO"}

    action: bpy.props.EnumProperty(items=DRIVE_ACTIONS, default="ADD")

    @classmethod
    def poll(cls, context):
        return context.mode == "EDIT_MESH" and bool(_reference_edit_objects(context))

    def execute(self, context):
        total = 0
        for obj in _reference_edit_objects(context):
            bm = bmesh.from_edit_mesh(obj.data)
            vg = obj.vertex_groups.get(fileio.DRIVE_GROUP) or obj.vertex_groups.new(name=fileio.DRIVE_GROUP)
            layer = bm.verts.layers.deform.verify()
            k = vg.index
            for v in bm.verts:
                dv = v[layer]
                if self.action == "SELECT":
                    v.select_set(dv.get(k, 0.0) > 0)
                elif v.select and self.action == "ADD":
                    dv[k] = 1.0
                elif v.select and k in dv:
                    del dv[k]
                total += dv.get(k, 0.0) > 0
            bm.select_flush_mode()
            bmesh.update_edit_mesh(obj.data)
        self.report({"INFO"}, f"{total} driven vertices in the edited meshes")
        return {"FINISHED"}


class REE_GPUC_OT_drive_select_near(bpy.types.Operator):
    """Select the reference mesh vertices within a distance of the cloth's rest surface. Only the selection changes:
    adjust it, then Add Selected"""
    bl_idname = "ree_gpuc.drive_select_near"
    bl_label = "Select Near the Cloth"
    bl_options = {"REGISTER", "UNDO"}

    distance: bpy.props.FloatProperty(name="Distance", default=0.02, min=0.0, soft_max=0.1, precision=3,
                                      unit="LENGTH")
    extend: bpy.props.BoolProperty(name="Extend", default=False, description="Keep the selection there is")

    @classmethod
    def poll(cls, context):
        return context.mode == "EDIT_MESH" and bool(_reference_edit_objects(context))

    def execute(self, context):
        from .modules import vertex_drive
        objs = _reference_edit_objects(context)
        ref = fileio.reference_of(objs[0])
        head = next((o for o in bpy.data.objects if props.is_header(o) and o.ree_gpuc_header.drive_reference == ref),
                    None) or _cloth_head(context)
        coll = _cloth_of_head(head) if head is not None else None
        if coll is None:
            return refused(self, "No cloth uses this reference mesh")
        try:
            pts, tris = fileio.cloth_surface(coll)
        except fileio.ExportError as e:
            return refused(self, str(e))
        frames = vertex_drive.tri_frames(pts, tris)
        picked = 0
        for obj in objs:
            bm = bmesh.from_edit_mesh(obj.data)
            mat = np.array(obj.matrix_world, float)
            co = np.array([v.co for v in bm.verts], float).reshape(-1, 3) @ mat[:3, :3].T + mat[:3, 3]
            near = vertex_drive.distances(co, frames).min(1) < self.distance if len(co) else []
            for v, on in zip(bm.verts, near):
                v.select_set(bool(on) or (self.extend and v.select))
                picked += bool(on)
            bm.select_flush_mode()
            bmesh.update_edit_mesh(obj.data)
        self.report({"INFO"}, f"{picked} vertices within {self.distance * 100:.1f} cm of {coll.name} selected")
        return {"FINISHED"}


class REE_GPUC_OT_drive_spread_lods(bpy.types.Operator):
    """Give the reference mesh's later LODs the driven vertices of LOD0: a vertex is driven when the LOD0 vertex
    nearest it is"""
    bl_idname = "ree_gpuc.drive_spread_lods"
    bl_label = "LOD0 to Later LODs"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return fileio.reference_of(context.object) is not None

    def execute(self, context):
        ref = fileio.reference_of(context.object)
        mode = context.mode
        if mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        try:
            done = fileio.spread_lods(ref)
        except fileio.ExportError as e:
            return refused(self, str(e))
        finally:
            if mode == "EDIT_MESH":
                bpy.ops.object.mode_set(mode="EDIT")
        self.report({"INFO"}, ", ".join(f"LOD{k} {n}" for k, n in sorted(done.items())) or "No later LOD")
        return {"FINISHED"}


CLASSES = (REE_GPUC_OT_import, REE_GPUC_FH_import, REE_GPUC_OT_export, REE_GPUC_OT_create_header, REE_GPUC_OT_new_config,
           REE_GPUC_OT_check, REE_GPUC_OT_switch_to_pose, REE_GPUC_OT_switch_to_object, REE_GPUC_OT_part_from_parents,
           REE_GPUC_OT_part_from_selected, REE_GPUC_OT_regenerate_part, REE_GPUC_OT_select_part_bones,
           REE_GPUC_OT_add_collider, REE_GPUC_OT_remove_collider, REE_GPUC_OT_collider_from_bones, REE_GPUC_OT_copy, REE_GPUC_OT_paste,
           REE_GPUC_OT_set_bits, REE_GPUC_OT_curve_key_add, REE_GPUC_OT_curve_key_remove, REE_GPUC_OT_curve_sort,
           REE_GPUC_OT_curve_reset, REE_GPUC_OT_edit_particles,
           REE_GPUC_OT_reset_rest_offsets, REE_GPUC_OT_set_value, REE_GPUC_OT_paint,
           REE_GPUC_OT_simulate, REE_GPUC_OT_sim_reset, REE_GPUC_OT_import_reference,
           REE_GPUC_OT_drive_vertices, REE_GPUC_OT_drive_select_near, REE_GPUC_OT_drive_spread_lods)


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

"""Panels of the 3D view's RE GPUC tab and the per-object panels in Properties > Object.

Panels only draw: a value that applies only while its switch is on, or whose effect is unknown
(``props.UNVERIFIED``, the Unknown fields), is drawn inactive but stays editable.
"""
import bpy

from . import canon, props

CATEGORY = "RE GPUC"


def _split(layout):
    col = layout.column()
    col.use_property_split, col.use_property_decorate = True, False
    return col


def _collections(layout, s):
    """Draw the active cloth and the Reference CLSP stored on its header."""
    layout.label(text="Active GPUC Collection")
    layout.prop(s, "target", text="", icon="COLLECTION_COLOR_06")
    h = props.header(s.target)
    if h is not None:
        layout.label(text="Reference CLSP Collection")
        layout.prop(h.ree_gpuc_header, "reference_clsp", text="", icon="OUTLINER_COLLECTION")


def _bits(layout, data, prop, target):
    """Draw a flag field with the button that opens its bit dialog."""
    row = layout.row()
    row.prop(data, prop)
    row.operator("ree_gpuc.set_bits", text="", icon="DOWNARROW_HLT").target = target


# --- the 3D view's RE GPUC tab -------------------------------------------------------------------------------

class _Sidebar:
    bl_space_type, bl_region_type, bl_category = "VIEW_3D", "UI", CATEGORY


class REE_GPUC_PT_general(_Sidebar, bpy.types.Panel):
    bl_label = "GPUC: General Tools"
    bl_context = "objectmode"

    def draw(self, context):
        s = context.scene.ree_gpuc
        layout = self.layout
        row = layout.row(align=True)
        row.operator("ree_gpuc.import_file", icon="IMPORT")
        row.operator("ree_gpuc.export_file", icon="EXPORT")
        row = layout.row(align=True)
        row.operator("ree_gpuc.create_header", text="Create", icon="ADD")
        row.operator("ree_gpuc.check", icon="VIEWZOOM")
        _collections(layout, s)
        layout.operator("ree_gpuc.new_config")
        obj = context.active_object
        if props.is_part(obj) and obj.select_get() and not canon.locked(obj):
            layout.operator("ree_gpuc.regenerate_part")
        layout.operator("ree_gpuc.weld_parts", icon="AUTOMERGE_ON")
        layout.label(text="Create new parts in Pose Mode.")
        layout.operator("ree_gpuc.switch_to_pose")


class REE_GPUC_PT_pose(_Sidebar, bpy.types.Panel):
    bl_label = "GPUC: Pose Mode Tools"
    bl_context = "posemode"

    def draw(self, context):
        s = context.scene.ree_gpuc
        layout = self.layout
        _collections(layout, s)
        layout.label(text="GPUC Config")
        row = layout.row(align=True)
        row.prop(s, "config", text="")
        row.operator("ree_gpuc.new_config", text="", icon="ADD")
        layout.operator("ree_gpuc.part_from_parents")
        layout.operator("ree_gpuc.part_from_selected")
        layout.label(text="Collision Tools")
        layout.prop(s, "collider_kind")
        layout.operator("ree_gpuc.collider_from_bones")
        layout.label(text="Extra Tools")
        layout.operator("ree_gpuc.select_part_bones")
        layout.label(text="Configure parts in Object Mode.")
        layout.operator("ree_gpuc.switch_to_object")


class REE_GPUC_PT_presets(_Sidebar, bpy.types.Panel):
    bl_label = "GPUC: Presets"
    bl_context = "objectmode"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        from . import presets
        layout = self.layout
        s = context.scene.ree_gpuc_presets
        layout.prop(s, "kind")
        game = presets.context_game(s, context)
        label = "Preview (all games)" if game == "ANY" else dict((x[0], x[1]) for x in props.GAMES)[game]
        layout.label(text=label)
        layout.prop(s, "preset")
        layout.operator("ree_gpuc.apply_preset", icon="CHECKMARK")
        layout.operator("ree_gpuc.save_preset", icon="ADD")
        layout.operator("ree_gpuc.open_preset_folder", icon="FILE_FOLDER")
        layout.label(text="Type follows the active GPUC object")


class REE_GPUC_PT_clipboard(_Sidebar, bpy.types.Panel):
    bl_label = "GPUC: Clipboard"
    bl_context = "objectmode"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        layout.label(text="Copy GPUC Object Properties")
        row = layout.row()
        row.operator("ree_gpuc.copy")
        row.operator("ree_gpuc.paste")
        layout.label(text="Clipboard Contents:")
        clip = context.scene.get("ree_gpuc_clipboard")
        layout.label(text=f"{clip['type']} ({clip['source']})" if clip else "Empty")


def _value_tool(layout, context, setting: bool, select_particles: bool = False):
    """Draw the particle value chooser and its mapping range; with ``setting``, also the value to set."""
    s = context.scene.ree_gpuc
    col = _split(layout)
    col.prop(s, "field")
    obj = context.active_object
    if s.field == "sim_weight":
        layout.label(text="Mapping Range: 0 - 1 (fixed)")
    elif props.is_part(obj):
        layout.label(text=f"Mapping Range · {obj.name}")
        row = layout.row(align=True)
        row.prop(obj.ree_gpuc_part, s.field + "_range", index=0, text="Min")
        row.prop(obj.ree_gpuc_part, s.field + "_range", index=1, text="Max")
    else:
        layout.label(text="Select a Part to edit its mapping range", icon="INFO")
    if setting:
        if select_particles and context.mode != "EDIT_MESH":
            layout.operator("ree_gpuc.edit_particles", icon="EDITMODE_HLT")
        col = _split(layout)
        col.active = context.mode == "EDIT_MESH"
        col.prop(s, "value")
        layout.operator("ree_gpuc.set_value", icon="CHECKMARK")


class REE_GPUC_PT_particles(_Sidebar, bpy.types.Panel):
    bl_label = "GPUC: Particles"

    def draw(self, context):
        s = context.scene.ree_gpuc
        layout = self.layout
        header, body = layout.panel("ree_gpuc_particle_offsets")
        header.label(text="Position Offsets")
        if body is not None:
            body.prop(s, "show_base_positions")
            body.label(text="Edit Part vertices to adjust offsets", icon="INFO")
            body.separator()
            row = body.row(align=True)
            row.operator("ree_gpuc.reset_rest_offsets", text="Reset Selected").selected_only = True
            row.operator("ree_gpuc.reset_rest_offsets", text="Reset All").selected_only = False
        header, body = layout.panel("ree_gpuc_particle_weights")
        header.label(text="Weight Strength")
        if body is None:
            return
        _split(body).prop(s, "particle_mode")
        if s.particle_mode == "WEIGHT":
            _value_tool(body, context, setting=False)
            body.operator("ree_gpuc.paint", icon="BRUSH_DATA")
            return
        _value_tool(body, context, setting=True, select_particles=True)
        if s.particle_mode == "UV":
            body.label(text="Open a UV Editor to use this")
            _split(body).prop(s, "show_uv_values")


class REE_GPUC_PT_simulate(_Sidebar, bpy.types.Panel):
    bl_label = "GPUC: Preview Simulation"

    def draw(self, context):
        from . import sim
        layout = self.layout
        st, s = context.scene.ree_gpuc_sim, context.scene.ree_gpuc
        if props.is_re4(s.target):
            layout.label(text="Preview simulation is not available for RE4 cloth", icon="INFO")
            return
        row = layout.row(align=True)
        running = sim.running()
        row.operator("ree_gpuc.simulate", text="Stop" if running else "Simulate",
                     icon="PAUSE" if running else "PLAY", depress=running)
        row.operator("ree_gpuc.sim_reset", icon="FILE_REFRESH")
        error = context.scene.get("ree_gpuc_sim_error")
        if error:
            layout.label(text=f"Stopped: {error}", icon="ERROR")
        header, body = layout.panel("ree_gpuc_sim_settings")
        header.label(text="Sim Settings")
        if body is not None:
            col = _split(body)
            col.prop(st, "iterations")
            col.prop(st, "follow_pose")
        header, body = layout.panel("ree_gpuc_sim_force")
        header.label(text="Sim Force")
        if body is not None:
            for switch, kind in (("use_gravity", "CONSTANT"), ("use_wind", "WIND")):
                col = _split(body)
                col.prop(st, switch)
                obj = sim.force_object(context.scene, kind)
                if obj is None or not getattr(st, switch):
                    continue
                f = obj.ree_gpuc_force
                sub = col.column(align=True)
                for name in (("accel",) if kind == "CONSTANT" else ("lo", "hi", "period")):
                    sub.prop(f, name)
                sub.label(text=f"Scale x {sim.gain(obj):.3g}")
        header, body = layout.panel("ree_gpuc_sim_collider")
        header.label(text="Sim Collider")
        if body is not None:
            row = body.row()
            row.template_list("REE_GPUC_UL_colliders", "", context.scene, "objects", st, "collider_index", rows=3)
            side = row.column(align=True)
            side.operator("ree_gpuc.add_collider", text="", icon="ADD").kind = s.test_collider_kind
            side.operator("ree_gpuc.remove_collider", text="", icon="REMOVE")
            _split(body).prop(s, "test_collider_kind", text="New Shape")
            obj = context.active_object
            if props.is_collider(obj):
                _collider(body, obj.ree_gpuc_collider)


class REE_GPUC_UL_colliders(bpy.types.UIList):
    """Test colliders of the active cloth"""

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row(align=True)
        row.prop(item, "name", text="", emboss=False, icon="SPHERE" if item.ree_gpuc_collider.kind == "SPHERE"
                 else {"CAPSULE": "MESH_CAPSULE", "PLANE": "MESH_PLANE", "BOX": "MESH_CUBE"}[item.ree_gpuc_collider.kind])
        row.prop(item, "hide_viewport", text="", emboss=False)

    def filter_items(self, context, data, propname):
        from . import sim
        objects = getattr(data, propname)
        flags = [self.bitflag_filter_item if props.is_collider(o) and sim.ours(context.scene, o) else 0
                 for o in objects]
        order = bpy.types.UI_UL_list.sort_items_by_name(objects, "name")
        return flags, order


class REE_GPUC_PT_display(_Sidebar, bpy.types.Panel):
    bl_label = "GPUC: Display"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        s = context.scene.ree_gpuc
        layout = self.layout
        header, body = layout.panel("ree_gpuc_display_general")
        header.label(text="General Display")
        if body is not None:
            col = _split(body)
            col.prop(s, "follow_pose")
            sub = col.column(heading="Draw Selected Only")
            sub.prop(s, "selected_only_object")
            sub.prop(s, "selected_only_edit")
        header, body = layout.panel("ree_gpuc_display_particles", default_closed=True)
        header.prop(s, "show_particles")
        if body is not None:
            col = _split(body)
            col.prop(s, "in_front")              # also applies to part meshes and base positions: never inactive
            col = _split(body)
            col.active = s.show_particles
            col.prop(s, "particle_style")
            col.prop(s, "sphere_alpha" if s.particle_style == "SPHERE" else "point_size")
            col.prop(s, "colour_by")
        header, body = layout.panel("ree_gpuc_display_links", default_closed=True)
        header.prop(s, "show_links")
        if body is not None:
            col = _split(body)
            col.active = s.show_links
            col.prop(s, "links_in_front")
            col.prop(s, "link_colour")
            shapes = col.column(heading="Shapes")
            for name, label, _ in props.LINK_SHAPES:
                shapes.prop(s, "show_" + name, text=label)
        header, body = layout.panel("ree_gpuc_display_limits", default_closed=True)
        header.label(text="Limits")
        if body is not None:
            col = _split(body)
            col.prop(s, "show_space_limit")
            col.prop(s, "show_backstop")
            sub = col.column()
            sub.active = s.show_space_limit or s.show_backstop
            sub.prop(s, "limits_in_front")
        header, body = layout.panel("ree_gpuc_display_colliders", default_closed=True)
        header.prop(s, "show_colliders")
        if body is not None:
            col = _split(body)
            col.active = s.show_colliders
            col.prop(s, "colliders_in_front")
        header, body = layout.panel("ree_gpuc_display_other", default_closed=True)
        header.label(text="Curves and UV")
        if body is not None:
            col = _split(body)
            col.prop(s, "show_curves")
            col.prop(s, "show_uv_values")


class REE_GPUC_PT_uv(bpy.types.Panel):
    bl_space_type, bl_region_type, bl_category = "IMAGE_EDITOR", "UI", CATEGORY
    bl_label = "GPUC: Particles"

    @classmethod
    def poll(cls, context):
        return context.mode == "EDIT_MESH" and any(props.is_part(o) for o in context.objects_in_mode)

    def draw(self, context):
        s = context.scene.ree_gpuc
        _value_tool(self.layout, context, setting=True)
        col = _split(self.layout)
        col.prop(s, "colour_by")
        col.prop(s, "show_uv_values")


# --- Properties > Object: one panel per kind of object -------------------------------------------------------

class _ObjectPanel:
    bl_space_type, bl_region_type, bl_context = "PROPERTIES", "WINDOW", "object"
    KIND = None

    @classmethod
    def poll(cls, context):
        return cls.KIND(context.object)


class REE_GPUC_PT_object_header(_ObjectPanel, bpy.types.Panel):
    bl_label = "GPUC Header"
    KIND = staticmethod(props.is_header)

    def draw(self, context):
        from .modules import gpuc_params
        h = context.object.ree_gpuc_header
        col = _split(self.layout)
        sub = col.column()
        sub.enabled = "ree_gpuc_raw" not in context.object.keys()     # Keep Structure: the game is the file's
        sub.prop(h, "game")
        fields = gpuc_params.header_fields(h.game)
        for name, label, _, kind, known, _, _ in props.HEADER_FIELDS:
            if name not in fields:
                continue
            if name == "kernel_flags":
                _bits(col, h, name, "ree_gpuc_header.kernel_flags")
                continue
            sub = col.column()
            sub.active = known
            sub.prop(h, name, text=label)


# (switch, the Config values that apply only while it is on)
CONFIG_GATES = (("second_damping", ("second_velocity_damping", "second_damping_speed")),
                ("overwrite_translation", ("blend_translation",)),
                ("overwrite_rotation", ("blend_rotation",)),
                ("use_speed_limit", ("speed_limit",)),
                ("use_angular_speed_limit", ("angular_speed_limit",)))


class REE_GPUC_PT_object_config(_ObjectPanel, bpy.types.Panel):
    bl_label = "GPUC Config"
    KIND = staticmethod(props.is_config)

    def draw(self, context):
        cfg = context.object.ree_gpuc_config
        layout = self.layout
        if props.is_re4_object(context.object):
            from .modules import re4
            layout.label(text="RE4 values as stored in the file. Effects unknown.", icon="INFO")
            for name in re4.CONFIG:
                _split(layout).prop(cfg, name)
            return
        gated = ({n for _, names in CONFIG_GATES for n in names} | {g for g, _ in CONFIG_GATES}
                 | {speed for _, _, _, speed in props.CURVES})
        col = _split(layout)
        _bits(col, cfg, "info_state", "ree_gpuc_config.info_state")
        for name, label, _, _ in props.CONFIG_FIELDS:
            if name not in gated:
                sub = col.column()
                sub.active = name not in props.UNVERIFIED
                sub.prop(cfg, name, text=label, slider=name in props.SLIDERS)
        labels = {name: label for name, label, _, _ in props.CONFIG_FIELDS}
        for switch, names in CONFIG_GATES:
            col = _split(layout)
            col.prop(cfg, switch, text=labels[switch])
            sub = col.column()
            sub.active = getattr(cfg, switch)
            for name in names:
                sub.prop(cfg, name, text=labels[name], slider=name in props.SLIDERS)
        for name, label, switch, speed in props.CURVES:
            header, body = layout.panel(f"ree_gpuc_{name}", default_closed=True)
            curve = getattr(cfg, name)
            header.label(text=f"{label} ({len(curve.keys)} keys)" if not getattr(cfg, switch)
                         else f"{label} (off: {labels[switch]})")
            if body is not None:
                body.active = not getattr(cfg, switch)
                _curve(body, cfg, curve, name, labels[speed], speed)
        header, body = layout.panel("ree_gpuc_config_unknown", default_closed=True)
        header.label(text="Unknown")
        if body is not None:
            col = _split(body)
            col.active = False
            for name, label, _, _ in props.CONFIG_UNKNOWNS:
                col.prop(cfg, name, text=label)


class REE_GPUC_UL_keys(bpy.types.UIList):
    """Keys of a Config curve"""

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row(align=True)
        row.prop(item, "time", text="", emboss=False)
        row.prop(item, "value", text="", emboss=False)
        row.prop(item, "type", text="", emboss=False)


def _curve(layout, cfg, curve, name, speed_label, speed):
    col = _split(layout)
    col.prop(cfg, speed, text=speed_label, slider=speed in props.SLIDERS)
    row = layout.row()
    row.template_list("REE_GPUC_UL_keys", name, curve, "keys", curve, "active_key", rows=4)
    side = row.column(align=True)
    for op, icon in (("curve_key_add", "ADD"), ("curve_key_remove", "REMOVE")):
        side.operator(f"ree_gpuc.{op}", text="", icon=icon).curve = name
    side.separator()
    side.operator("ree_gpuc.curve_sort", text="", icon="SORTTIME").curve = name
    side.operator("ree_gpuc.curve_reset", text="", icon="FILE_REFRESH").curve = name
    if not curve.ordered():
        layout.label(text="Keys out of time order", icon="ERROR")
    if 0 <= curve.active_key < len(curve.keys):
        key = curve.keys[curve.active_key]
        col = _split(layout)
        col.prop(key, "time")
        col.prop(key, "value")
        col.prop(key, "type")
        sub = col.column(align=True)
        sub.active = key.type in props.HERMITE_TYPES
        sub.prop(key, "in_tangent")
        sub.prop(key, "out_tangent")
        sub = col.column(align=True)
        sub.active = key.type in props.WEIGHTED_TYPES
        sub.prop(key, "in_weight")
        sub.prop(key, "out_weight")
    col = _split(layout)
    _bits(col, curve, "clamp_flags", f"ree_gpuc_config.{name}.clamp_flags")
    sub = col.column(align=True)
    sub.active = bool(curve.clamp_flags & 1)
    sub.prop(curve, "clamp_min")
    sub.prop(curve, "clamp_max")
    sub = col.column()
    sub.active = False
    for prop in ("unk04", "unk08", "unk14", "unk1c"):
        sub.prop(curve, prop)


class REE_GPUC_PT_object_part(_ObjectPanel, bpy.types.Panel):
    bl_label = "GPUC Part"
    KIND = staticmethod(props.is_part)

    def draw(self, context):
        obj = context.object
        p = obj.ree_gpuc_part
        layout = self.layout
        for key, icon in (("ree_gpuc_check", "ERROR"), ("ree_gpuc_notice", "INFO")):
            text = obj.get(key)
            if text and text != "ok":
                layout.label(text=text, icon=icon)
        if canon.locked(obj):
            layout.label(text="Imported with Keep Structure: values can be edited, but not regenerated",
                         icon="LOCKED")
        re4_part = props.is_re4_object(obj)
        col = _split(layout)
        identity = col.column()
        identity.enabled = not re4_part
        identity.prop(p, "config")
        identity.prop(p, "part_name")
        identity.prop(p, "use_root_joint")
        sub = col.column()
        sub.active = p.use_root_joint and not re4_part
        if p.armature is not None and p.armature.type == "ARMATURE":
            sub.prop_search(p, "root_joint", p.armature.data, "bones", text="Joint")
        else:
            sub.prop(p, "root_joint", text="Joint")
        _bits(col, p, "clsp_flags_a", "ree_gpuc_part.clsp_flags_a")
        _bits(col, p, "clsp_flags_b", "ree_gpuc_part.clsp_flags_b")
        for name in ("friction", "bounce", "stickiness", "surface_area"):
            if re4_part and name == "surface_area":
                continue
            sub = col.column()
            sub.active = name not in props.UNVERIFIED
            sub.prop(p, name, slider=name in props.SLIDERS)
        for name in ("plane_bits", "sphere_bits", "capsule_bits", "obb_bits"):
            if re4_part and name == "obb_bits":
                continue
            _bits(col, p, name, f"ree_gpuc_part.{name}")
        if re4_part:
            col.prop(p, "re4_value_30")
        _split(layout).prop(p, "link_preset")
        for name, label, _ in props.LINK_SHAPES:
            tail = getattr(p, name)
            header, body = layout.panel(f"ree_gpuc_link_{name}", default_closed=True)
            row = header.row(align=True)
            row.label(text=label + (" *" if tail.conditional else ""))
            row.prop(tail, "k", text="Stiffness", slider=True)
            if body is None:
                continue
            body.prop(tail, "conditional")
            sub = body.column(align=True)
            sub.active = tail.conditional
            sub.prop(tail, "k2", slider=True)
            row = sub.row(align=True)
            row.prop(tail, "lo", text="Low", slider=True)
            row.prop(tail, "hi", text="High", slider=True)
            body.label(text=tail.summary())

        if not re4_part:
            layout.separator()
            layout.label(text="Export Settings")
            col = _split(layout)
            col.prop(p, "flip_normals")
            col.prop(p, "other_diagonal")


class REE_GPUC_PT_object_collision(_ObjectPanel, bpy.types.Panel):
    bl_label = "GPUC Collision"

    @staticmethod
    def KIND(obj):
        from . import collision
        return collision.root_of(obj) is not None

    def draw(self, context):
        from . import collision
        root = collision.root_of(context.object)
        from . import reference
        c = root.ree_gpuc_capsule
        targets = []
        for end in collision.ends(root):
            con = end.constraints.get("BoneName") if end else None
            targets.append(con.target if con is not None and con.target is not None
                           and con.target.type == "ARMATURE" else None)
        if not collision.bound(root):
            self.layout.label(text="A joint has no bone. Set one, or export keeps the joint from the file",
                              icon="ERROR")
        elif any(reference.is_reference(t) for t in targets):
            self.layout.label(text="A joint is missing from the mesh; placed by the reference skeleton", icon="INFO")
        col = _split(self.layout)
        for arm, (joint, offset, radius) in zip(targets, (("joint_a", "offset_a", "radius_a"),
                                                          ("joint_b", "offset_b", "radius_b"))):
            if arm is not None:
                col.prop_search(c, joint, arm.data, "bones")
            else:
                col.prop(c, joint)
            col.prop(c, offset)
            col.prop(c, radius)


class REE_GPUC_PT_object_collider(_ObjectPanel, bpy.types.Panel):
    bl_label = "GPUC Test Collider"
    KIND = staticmethod(props.is_collider)

    def draw(self, context):
        _collider(self.layout, context.object.ree_gpuc_collider)


def _collider(layout, c):
    """Draw a test collider's shape and sizes."""
    col = _split(layout)
    col.prop(c, "kind")
    if c.kind in {"SPHERE", "CAPSULE"}:
        col.prop(c, "radius")
    if c.kind == "CAPSULE":
        col.prop(c, "length")
    elif c.kind == "BOX":
        col.prop(c, "size")
    elif c.kind == "PLANE":
        col.prop(c, "plane_size")


class REE_GPUC_PT_object_force(_ObjectPanel, bpy.types.Panel):
    bl_label = "GPUC Force"
    KIND = staticmethod(props.is_force)

    def draw(self, context):
        from . import sim
        obj = context.object
        f = obj.ree_gpuc_force
        col = _split(self.layout)
        col.prop(f, "enabled")
        sub = col.column()
        sub.active = f.enabled
        for name in (("accel",) if f.kind == "CONSTANT" else ("lo", "hi", "period")):
            sub.prop(f, name)
        sub.label(text=f"Scale x {sim.gain(obj):.3g}")


CLASSES = (REE_GPUC_PT_general, REE_GPUC_PT_pose, REE_GPUC_PT_presets, REE_GPUC_PT_clipboard, REE_GPUC_PT_particles, REE_GPUC_UL_colliders, REE_GPUC_PT_simulate,
           REE_GPUC_PT_display, REE_GPUC_PT_uv, REE_GPUC_PT_object_header, REE_GPUC_UL_keys, REE_GPUC_PT_object_config,
           REE_GPUC_PT_object_part, REE_GPUC_PT_object_collision, REE_GPUC_PT_object_collider, REE_GPUC_PT_object_force)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)

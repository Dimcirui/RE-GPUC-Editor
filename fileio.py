"""Import .gpuc files as cloths (parameters, Keep Structure, Rebuild from Bones, Update Existing) and export cloths
as .gpuc.

Wilds, RE9 and Onimusha files share blocks and records; their headers (``gp.header_fields``), RE9's skin indices
(``gpuc.read_skin``) and Onimusha's section table differ. RE4 files import with Keep Structure only; a cloth made
from bones exports for RE4 through ``re4_build``. The header's ``game`` decides which layout an export writes.

Maintenance constraints:
- Game model space = ``ROTATE_BACK`` (-90 deg about X) x the armature's ``matrix_world`` x armature space, as the
  RE Mesh Editor exports the mesh.
- Skinning holds full mesh bone indices (armature order); an output joint's index is its bone's place among the
  mesh's weighted bones: the exported .mesh's own remap when one matching the armature is found, else the scene's
  (``weighted_bones``), as the RE Mesh Editor builds its remap (``export_weighted``).
- Generated exports wind faces so particle normals point out; space limits and backstops are measured along them.
- Exports keep Kernel Flags bit 2 (MeshMultiJointDeform): ``gpuc_build`` lays out sections 23 to 26 for it. A cloth
  that drives mesh vertices (header Drive) clears it and ``vertex_drive`` lays out sections 23, 24 and 27 instead,
  from the exported .mesh's vertices (``mesh_file_vertices``)."""
import base64
import collections
import importlib
import json
import math
import os
import struct

import bpy
import numpy as np
from mathutils import Matrix

from . import canon, collision, generate, props, reference, weld
from .modules import gpuc, gpuc_build, gpuc_params as gp, grid, re4, re4_build, vertex_drive

ROTATE_BACK = np.array(Matrix.Rotation(math.radians(-90.0), 4, "X"), float)
GAMES = props.GAMES
SOURCE_KEY = "ree_gpuc_source"           # on the header: the file last imported
ASSET_KEY = "~ASSETPATH"                 # on the collection: its game path, read by Wilds-Dynamics


def asset_path(path: str) -> str:
    """Return the path below ``natives/<platform>/`` without the version suffix (``Art/.../ch03_100_1005.gpuc``),
    else ""."""
    parts = path.replace("\\", "/").split("/")
    low = [p.lower() for p in parts]
    if "natives" not in low or low.index("natives") + 2 >= len(parts):
        return ""
    rest = parts[low.index("natives") + 2:]
    name = rest[-1]
    k = name.lower().find(".gpuc")
    rest[-1] = name[:k + len(".gpuc")] if k >= 0 else name
    return "/".join(rest)


def set_asset_path(coll, path: str, exported: bool = False) -> None:
    """Set ``~ASSETPATH`` from an imported or exported file under natives; an export elsewhere removes it, as the
    chain editor does. An unchanged value is not rewritten."""
    found = asset_path(path)
    if found:
        if coll.get(ASSET_KEY) != found:
            coll[ASSET_KEY] = found
    elif exported and ASSET_KEY in coll.keys():
        del coll[ASSET_KEY]


class ExportError(Exception):
    pass


def file_stem(path: str) -> str:
    name = os.path.basename(path)
    return name.split(".gpuc")[0] if ".gpuc" in name else os.path.splitext(name)[0]


def by_name(objs) -> list:
    return sorted(objs, key=lambda o: o.name)


def _enum(prop_owner, prop: str, number: int):
    """Return the identifier of the enum item whose value is ``number``, else None."""
    for item in prop_owner.bl_rna.properties[prop].enum_items:
        if item.value == number:
            return item.identifier
    return None


# --- import -------------------------------------------------------------------------------------------------

def import_params(path: str, merge=None, arm=None, configs=True, parts=True, values=False) -> list[str]:
    """Import the file's parameters into a new <file>.gpuc collection or into ``merge``; return notes on blocks
    with no target. Part blocks go to the Parts by name order; particles stay the editor's own."""
    if path.endswith(re4.EXT):
        raise ImportProblem("RE4 supports Keep Structure import only")
    r = gp.read(open(path, "rb").read())
    notes = []
    coll = merge or generate.new_collection(file_stem(path), arm)
    head = props.header(coll) or generate.new_header(coll, arm)
    arm = arm or generate._armature(coll)
    head[SOURCE_KEY] = path
    set_asset_path(coll, path)
    head.ree_gpuc_header.game = r["ext"]
    _set_header(head.ree_gpuc_header, r["header"], notes)
    cfg_objs = _set_configs(coll, arm, r["configs"], notes) if configs else by_name(props.configs(coll))
    if parts:
        part_objs = by_name(props.parts(coll))
        for i, p in enumerate(r["parts"]):
            if i >= len(part_objs):
                notes.append(f"{len(r['parts']) - i} part blocks have no Part to go to (make the Parts first)")
                break
            _set_part(part_objs[i], p, r["tails"][i], r["values"][i] if values else None, cfg_objs, notes)
    return notes


def _set_header(h, values, notes) -> None:
    for name, value in values.items():
        if name == "calculate_mode":
            item = _enum(h, name, value)
            if item is None:
                notes.append(f"Calculate Mode {value} is not one this editor knows; kept {h.calculate_mode}")
                continue
            value = item
        setattr(h, name, value)


def _set_configs(coll, arm, configs, notes) -> list:
    """Write config blocks onto the Configs by place, creating more as needed; return the Configs in order."""
    cfg_objs = by_name(props.configs(coll))
    for i, c in enumerate(configs):
        if i >= len(cfg_objs):
            cfg_objs.append(generate.new_config(coll, arm))
        _set_config(cfg_objs[i].ree_gpuc_config, c, notes)
    return cfg_objs


def _set_config(cfg, c, notes) -> None:
    if any(name in c for name in re4.CONFIG):
        for name in re4.CONFIG:
            setattr(cfg, name, c[name])
        return
    for name in gp.CONFIG:
        setattr(cfg, name, c[name])
    for name in gp.CURVES:
        curve = getattr(cfg, name)
        for field in gp.CURVE:
            setattr(curve, field, c[name][field])
        keys = []
        for t, v, typ, it, iw, ot, ow in c[name]["keys"]:
            kind = _enum(props.REE_GPUC_Key, "type", typ)
            if kind is None:
                notes.append(f"key type {typ} read as Hermite")
                kind = "HERMITE"
            keys.append((t, v, kind, it, iw, ot, ow))
        curve.set_keys(keys)


def _set_part(obj, p, tails, values, cfg_objs, notes) -> None:
    part = obj.ree_gpuc_part
    for name in gp.PART:
        if name in p:
            setattr(part, name, p[name])
    if "re4_value_30" in p:
        part.re4_value_30 = p["re4_value_30"]
    part.part_name = p["part_name"]
    if p["config"] < len(cfg_objs):
        part.config = cfg_objs[p["config"]]
    part.use_root_joint = p["use_root_joint"]
    if "root_name" in p:
        part.root_joint = p["root_name"]
    if p["use_root_joint"]:
        arm = part.armature
        names = {gpuc_build.name_hash(b.name): b.name for b in arm.data.bones} if arm is not None else {}
        if reference.armature(reference.cloth_of(obj)) is not None:
            names = {**reference.by_hash(), **names}
        if p["root_hash"] in names:
            part.root_joint = names[p["root_hash"]]
        else:
            notes.append(f"{obj.name}: the root joint (hash {p['root_hash']}) is not on the armature; kept "
                         f"{part.root_joint or 'none'}")
    for shape, (k, k2, lo, hi) in tails.items():
        t = getattr(part, shape)
        t.k, t.k2, t.lo, t.hi = k, k2, lo, min(hi, props.HI_MAX)
        t.conditional = gp.tail_bytes(k, k2, lo, hi) != gp.tail_bytes(k, 1.0, 1.0, 1.0)   # off exports (k, 1, 1, 1)
    if values and canon.has(obj):
        d = canon.load(obj)
        everyone = np.arange(d["rows"] * d["cols"])
        for field in canon.FIELD_NAMES:
            v = values.get(field)
            if v is None or field == "sim_weight":          # pinning stays the Part's own
                continue
            canon.fit_ranges(obj, {field: [v]})
            canon.set_values(obj, field, everyone, v)
        canon.write_groups(obj)


# --- export -------------------------------------------------------------------------------------------------

def mesh_collection(arm):
    """The RE Mesh Editor collection the armature is in, else None."""
    for coll in arm.users_collection:
        if coll.get("~TYPE") == "RE_MESH_COLLECTION" or any("Main Mesh LOD" in c.name for c in coll.children):
            return coll
    return None


def weighted_bones(arm, mesh_coll) -> list[str]:
    """Return the bones weighted in the first "Main Mesh LOD" (else the mesh collection), excluded meshes skipped,
    in armature order, as the RE Mesh Editor exports them; raises ``ExportError`` when none are. It cannot know
    which meshes an export left out (Selected Only): ``export_weighted`` prefers the exported .mesh's own table."""
    lods = sorted((c for c in mesh_coll.children if "Main Mesh LOD" in c.name), key=lambda c: c.name)
    lod = lods[0] if lods else mesh_coll
    used = set()
    for obj in lod.objects:
        if obj.type != "MESH" or obj.get("MeshExportExclude"):
            continue
        groups = {g.index: g.name.removeprefix("SHAPEKEY_") for g in obj.vertex_groups}
        for v in obj.data.vertices:
            for ge in v.groups:
                used.add(groups.get(ge.group))
    if not used:
        raise ExportError(f"{mesh_coll.name} has no weighted mesh")
    return [b.name for b in arm.data.bones if b.name in used]


def _outward(co, rows, cols, closed, centre, inside) -> bool:
    """Whether ``grid.faces``' winding gives outward normals for this Part."""
    tri = np.asarray(grid.faces(rows, cols, closed)[0][:3])
    n = np.cross(co[tri[1]] - co[tri[0]], co[tri[2]] - co[tri[0]])
    out = grid.normals(co, rows, cols, closed)[tri[0]] * grid.normal_sign(co, grid.normals(co, rows, cols, closed),
                                                                           centre, inside)
    return float(n @ out) > 0


JOINT_INDICES = [("FILE", "As Imported", "Keep the driven joints' indices from the imported file. Use when the "
                                         "game keeps the mesh the file came with"),
                 ("MESH", "From Mesh", "Index each driven joint by its bone among the exported .mesh's weighted "
                                       "bones (else the mesh collection's). Use after exporting the mesh again")]

MESH_FILE_KEY = "ree_gpuc_mesh_file"     # on the header: the .mesh the last export took its joint indices from
MISMATCH = "if the mesh and the .gpuc do not match, the cloth may behave wrongly in game"


def companion_mesh(gpuc_path: str) -> tuple:
    """Return (the one ``<stem>.mesh.<number>`` beside a .gpuc, else "", why none)."""
    folder, name = os.path.split(bpy.path.abspath(gpuc_path))
    stem = name.split(".gpuc")[0]
    prefix = stem + ".mesh."
    try:
        found = sorted(n for n in os.listdir(folder or ".") if n.startswith(prefix) and n[len(prefix):].isdigit()
                       and os.path.isfile(os.path.join(folder, n)))
    except OSError:
        found = []
    if len(found) > 1:
        return "", f"{len(found)} {stem}.mesh files lie beside the .gpuc (paste the exported one into Mesh File)"
    return (os.path.join(folder, found[0]), "") if found else ("", f"no {stem}.mesh found beside the .gpuc")


def find_mesh(coll, mesh_file: str = "", gpuc_path: str = "", last: bool = True) -> tuple:
    """Return (the .mesh to take joint indices from, else "", why none): ``mesh_file``, else the one beside
    ``gpuc_path`` (blank: the cloth's last export or import), else (``last``) the one the last export used."""
    if mesh_file:
        path = bpy.path.abspath(mesh_file)
        return (path, "") if os.path.isfile(path) else ("", f"Mesh File {path} does not exist")
    head = props.header(coll)
    gpuc_path = gpuc_path or (str(head.get(EXPORTED_KEY, "") or head.get(SOURCE_KEY, "")) if head else "")
    path, why = companion_mesh(gpuc_path) if gpuc_path else ("", "no .mesh found (the cloth has no file yet)")
    used = bpy.path.abspath(str(head.get(MESH_FILE_KEY, ""))) if last and head and head.get(MESH_FILE_KEY) else ""
    if not path and used and os.path.isfile(used):
        return used, ""
    return path, why


def matching_meshes(folder: str, arm) -> list:
    """The ``*.mesh.<number>`` files in ``folder`` whose skeleton is ``arm``'s, by name."""
    bones = [b.name for b in arm.data.bones]
    out = []
    try:
        names = sorted(os.listdir(folder or "."))
    except OSError:
        return out
    for n in names:
        stem, _, number = n.rpartition(".")
        if not stem.endswith(".mesh") or not number.isdigit() or not os.path.isfile(os.path.join(folder, n)):
            continue
        try:
            if mesh_file_bones(os.path.join(folder, n))[0] == bones:
                out.append(os.path.join(folder, n))
        except ExportError:
            continue
    return out


FORCE_HINT = "or turn on Force Export to export without one"


def export_mesh(coll, path: str, mesh_file: str = "") -> tuple:
    """Return (the .mesh an export calibrates against, the path to export to, notes); raises ``ExportError``.
    ``mesh_file``, else the same-name .mesh beside ``path``; its skeleton must be the cloth armature's. Without
    either, the one .mesh beside ``path`` with the armature's skeleton, and the .gpuc takes its name."""
    arm = _cloth_armature(coll)
    if arm is None:
        raise ExportError("the cloth has no armature to match a .mesh against")
    found, why = find_mesh(coll, mesh_file, path, last=False)
    if found:
        try:
            if mesh_file_bones(found)[0] != [b.name for b in arm.data.bones]:
                raise ExportError("its skeleton is not the cloth armature's")
        except ExportError as e:
            raise ExportError(f"{os.path.basename(found)} does not match this cloth: {e}. Export the mesh this cloth "
                              f"belongs to, {FORCE_HINT}") from e
        return found, path, []
    if mesh_file:
        raise ExportError(f"{why}: {FORCE_HINT}")
    folder = os.path.dirname(bpy.path.abspath(path))
    others = matching_meshes(folder, arm)
    if len(others) > 1:
        raise ExportError(f"{why}, and {len(others)} other meshes there have the cloth's skeleton "
                          f"({', '.join(os.path.basename(o) for o in others[:3])}): paste the one into Mesh File, "
                          f"{FORCE_HINT}")
    if not others:
        raise ExportError(f"{why}, and no other .mesh there has the cloth's skeleton. Export the mesh beside it, paste "
                          f"its path into Mesh File, {FORCE_HINT}")
    stem = os.path.basename(others[0]).split(".mesh.")[0]
    name = os.path.basename(path)
    renamed = os.path.join(folder, stem + (name[name.find(".gpuc"):] if ".gpuc" in name else os.path.splitext(name)[1]))
    return others[0], renamed, [f"Found {os.path.basename(others[0])} beside it (no same-name .mesh): exported as "
                                f"{os.path.basename(renamed)}"]


def _cloth_armature(coll):
    arm = generate._armature(coll)
    return arm if arm is not None and arm.type == "ARMATURE" else None


def _mesh_reader():
    """The enabled RE Mesh Editor's .mesh reader module, else None."""
    for name in bpy.context.preferences.addons.keys():
        try:
            mod = importlib.import_module(f"{name}.modules.mesh.file_re_mesh")
        except Exception:
            continue
        if callable(getattr(mod, "readREMesh", None)):
            return mod
    return None


def mesh_file_bones(path: str) -> tuple:
    """Return (the .mesh's full skeleton, its weighted bones in remap order); raises ``ExportError``. Only the
    header, skeleton and names are read, so a mesh whose streaming half is elsewhere still reads."""
    m = _mesh_reader()
    if m is None:
        raise ExportError("RE Mesh Editor is not enabled to read it")
    try:
        number = int(path.rsplit(".", 1)[1])
        version = m.meshFileVersionToNewVersionDict.get(number) or m.getNearestRemapVersion(number)
        with open(path, "rb") as file:
            head = m.FileHeader()
            head.read(file, version)
            if not head.skeletonOffset:
                raise ExportError("it has no skeleton")
            file.seek(head.skeletonOffset)
            skel = m.Skeleton()
            skel.read(file)
            file.seek(head.nameOffsetsOffset)
            offsets = [m.read_uint64(file) for _ in range(head.nameCount)]
            raw = []
            for offset in offsets:
                file.seek(offset)
                raw.append(m.read_string(file))
            file.seek(head.boneNameRemapOffset)
            names = [raw[m.read_ushort(file)] for _ in range(skel.boneCount)]
        remap = list(skel.boneRemapList)
    except ExportError:
        raise
    except Exception as e:
        raise ExportError(f"it cannot be read ({e})") from e
    if not remap or len(set(remap)) != len(remap) or not all(0 <= i < len(names) for i in remap):
        raise ExportError("its weighted-bone table is invalid")
    return names, [names[i] for i in remap]


def mesh_file_vertices(path: str) -> tuple:
    """Return (every LOD's vertices in the .mesh's vertex-buffer order (V, 3) in game space, each vertex's LOD, the
    LOD count); raises ``ExportError``. Reads the whole mesh with RE Mesh Editor (its streaming half too)."""
    m = _mesh_reader()
    if m is None:
        raise ExportError("RE Mesh Editor is not enabled to read the .mesh")
    try:
        parse = importlib.import_module(m.__name__.rsplit(".", 1)[0] + ".re_mesh_parse")
        mesh = m.readREMesh(path)
        parsed = parse.ParsedREMesh()
        parsed.ParseREMesh(mesh, {"importAllLOD": True, "importShadowMesh": False, "importOcclusionMesh": False,
                                  "importBlendShapes": False})
    except Exception as e:
        raise ExportError(f"{os.path.basename(path)} cannot be read ({e})") from e
    runs = {}
    for li, lod in enumerate(parsed.mainMeshLODList):
        for group in lod.visconGroupList:
            for sub in group.subMeshList:
                if sub.isReusedMesh or int(sub.meshVertexOffset) in runs:
                    continue
                runs[int(sub.meshVertexOffset)] = (li, np.asarray(sub.vertexPosList, float).reshape(-1, 3))
    count = max((off + len(v) for off, (_, v) in runs.items()), default=0)
    if not count:
        raise ExportError(f"{os.path.basename(path)} has no vertices")
    pos, lod_of, seen = np.zeros((count, 3)), np.zeros(count, np.int64), np.zeros(count, bool)
    for off, (li, v) in runs.items():
        pos[off:off + len(v)], lod_of[off:off + len(v)], seen[off:off + len(v)] = v, li, True
    if not seen.all():
        raise ExportError(f"{os.path.basename(path)}: {int((~seen).sum())} vertices belong to no submesh")
    return pos, lod_of, len(parsed.mainMeshLODList)


DRIVE_GROUPS = ("GPUC_Drive", "GPUC_NoDrive")     # vertex groups forcing mesh vertices in / out of a vertex drive


def drive_overrides(mesh_coll, arm, pos, lod_of) -> tuple:
    """Return (forced in, forced out masks over the .mesh's vertices, the number in the scene's LODs without a
    match, the .mesh's LODs the scene lacks): each vertex takes the DRIVE_GROUPS of the scene vertices of its LOD at
    the same game-space place (within 0.1 mm). A mesh collection without "Main Mesh LOD"
    children is LOD0 alone (the RE Mesh Editor's default import). Without a mesh collection nothing is forced."""
    on, off = np.zeros(len(pos), bool), np.zeros(len(pos), bool)
    if mesh_coll is None:
        return on, off, None, []
    from mathutils import kdtree
    lods = sorted((c for c in mesh_coll.children if "Main Mesh LOD" in c.name), key=lambda c: c.name) or [mesh_coll]
    to_game = ROTATE_BACK
    unmatched = np.zeros(len(pos), bool)
    missing = [li for li in range(len(lods), int(lod_of.max()) + 1)]
    for li, lod in enumerate(lods):
        sel = np.flatnonzero(lod_of == li)
        if not len(sel):
            continue
        entries = []
        for obj in lod.objects:
            if obj.type != "MESH" or obj.get("MeshExportExclude"):
                continue
            groups = {g.index: g.name for g in obj.vertex_groups if g.name in DRIVE_GROUPS}
            mat = to_game @ np.array(obj.matrix_world, float)
            for v in obj.data.vertices:
                names = {groups[ge.group] for ge in v.groups if ge.group in groups and ge.weight > 0}
                entries.append(((mat @ np.r_[v.co, 1.0])[:3], names))
        if not entries:
            unmatched[sel] = True
            continue
        tree = kdtree.KDTree(len(entries))
        for k, (co, _) in enumerate(entries):
            tree.insert(co, k)
        tree.balance()
        for i in sel:
            found = tree.find_range(pos[i], 1e-4)        # every scene vertex there: a seam's copies share a place
            if not found:
                unmatched[i] = True
                continue
            names = set().union(*(entries[k][1] for _, k, _ in found))
            on[i] = DRIVE_GROUPS[0] in names
            off[i] = DRIVE_GROUPS[1] in names
    return on, off, int(unmatched.sum()), missing


def vertex_drive_info(raw: bytes):
    """A vertex-driving .gpuc's (J, S, the mesh's vertex count, its LOD count, each vertex's output), else None."""
    if re4.matches(raw):
        return None
    g = gpuc.Gpuc(raw)
    ext = gp.ext_of(g.layout)
    if struct.unpack_from("<I", raw, gp.header_fields(ext)["kernel_flags"][0])[0] & 4:
        return None
    c = g.counts()
    V, L = (struct.unpack_from("<I", raw, off)[0] for off in (gp.offset_of(ext, "drive_vertices"), gp.drive_lods(ext)))
    if not c["joints"] or not V or 27 not in g.sections:
        return None
    return c["joints"], c["joint_slots"], V, L, vertex_drive.read_map(g.sections[27].data, V)


def _drive_vertices(g, head, coll, arm, mesh_coll, mesh_path, notes) -> None:
    """Make the built file ``g`` drive the mesh's vertices (``vertex_drive.apply``); raises ``ExportError``."""
    h = head.ree_gpuc_header
    if not mesh_path:
        raise ExportError("Drive Mesh Vertices needs the exported .mesh: export the mesh, then put its path in Mesh "
                          "File or the .gpuc beside it")
    pos, lod_of, lods = mesh_file_vertices(mesh_path)
    ppos, tris = vertex_drive.particles_and_triangles(g)
    near = vertex_drive.distances(pos, vertex_drive.tri_frames(ppos, tris)).min(1)
    on, off, unmatched, missing = drive_overrides(mesh_coll or (mesh_collection(arm) if arm else None), arm, pos,
                                                  lod_of)
    driven = np.flatnonzero(((near < h.drive_distance) | on) & ~off)
    name = os.path.basename(mesh_path)
    if not len(driven):
        raise ExportError(f"no vertex of {name} lies within {h.drive_distance * 100:.1f} cm of the cloth: raise "
                          f"Drive Distance or add vertices to the GPUC_Drive group")
    tri, u, v, hh, w = vertex_drive.bind(pos[driven], ppos, tris, h.drive_slots)
    ext = gp.ext_of(g.layout)
    vertex_drive.apply(g, len(pos), lods, driven, (tri, u, v, hh, w), gp.offset_of(ext, "drive_vertices"),
                       gp.drive_lods(ext))
    q = vertex_drive.decode(g.sections[24].data, len(driven), h.drive_slots)
    err = np.linalg.norm(vertex_drive.evaluate(ppos, tris, *q) - pos[driven], axis=1)
    per_lod = ", ".join(f"LOD{k} {int((lod_of[driven] == k).sum())}/{int((lod_of == k).sum())}" for k in range(lods))
    notes.append(f"Drives {len(driven)} vertices of {name} ({per_lod})")
    if unmatched is None:
        notes.append("No mesh collection: the vertex groups GPUC_Drive / GPUC_NoDrive were not read")
    elif unmatched:
        notes.append(f"{unmatched} vertices of {name} match no scene vertex: their vertex groups are not read (is "
                     f"the scene's mesh the one exported?)")
    if missing and (on.any() or off.any()):
        notes.append(f"LOD{', LOD'.join(map(str, missing))} not in the scene: their vertices go by Drive Distance "
                     f"alone (import the mesh with all LODs to use the vertex groups there)")
    if err.max() > 1e-3:
        notes.append(f"{int((err > 1e-3).sum())} driven vertices sit up to {err.max() * 1000:.1f} mm off after "
                     f"packing (far from the cloth?)")
    if ext != gp.ONI_EXT:
        notes.append(f"Drive Mesh Vertices is experimental for {game_name(ext)}: no game file of it drives vertices")


def export_weighted(coll, arm, mesh_coll, mesh_file, gpuc_path, notes) -> list[str]:
    """Return the weighted bones that index the driven joints: the .mesh's (``find_mesh``) when its skeleton is the
    armature's; else, with a note, the scene's (``weighted_bones``). Raises ``ExportError`` with neither."""
    path, why = find_mesh(coll, mesh_file, gpuc_path)
    if path:
        try:
            names, weighted = mesh_file_bones(path)
            if names == [b.name for b in arm.data.bones]:
                return weighted
            why = "its skeleton is not the cloth armature's"
        except ExportError as e:
            why = str(e)
        notes.append(f"{os.path.basename(path)} does not match this cloth: {why}. Joint indices come from the "
                     f"scene's weights; {MISMATCH}")
    else:
        notes.append(f"No matching mesh found: {why}. Joint indices come from the scene's weights; {MISMATCH}")
    mesh_coll = mesh_coll or mesh_collection(arm)
    if mesh_coll is None:
        raise ExportError("no mesh collection: the export needs the mesh's weighted bones")
    return weighted_bones(mesh_armature_or(mesh_coll, arm), mesh_coll)


def export(coll, path: str, mesh_coll=None, joints: str = "FILE", game: str = "", mesh_file: str = "",
           force: bool = False) -> list[str]:
    """Write the cloth as a .gpuc of ``game`` (blank: the header's) and return notes; raises ``ExportError``.
    A cloth made from bones, and From Mesh joints, calibrate against the exported .mesh (``export_mesh``), which
    may rename the file (the header's EXPORTED_KEY is the path written); ``force`` exports without one, the joints
    then taken from the scene's weights. A Keep Structure cloth As Imported needs none. The .mesh found is
    remembered for later exports and Wilds-Dynamics."""
    notes = []
    if keeps_structure(coll) and joints != "MESH":
        found = ""
    elif force or _cloth_armature(coll) is None:          # no armature: build_bytes says why it cannot export
        found = find_mesh(coll, mesh_file, path)[0]
    else:
        found, path, notes = export_mesh(coll, path, mesh_file)
    data, more = build_bytes(coll, mesh_coll, joints, game, found, path)
    notes = notes + more
    with open(path, "wb") as f:
        f.write(data)
    head = props.header(coll)
    set_asset_path(coll, path, exported=True)
    head[EXPORTED_KEY] = path                                  # wd_api: the file it stands for
    if found and (joints == "MESH" or not keeps_structure(coll)):
        head[MESH_FILE_KEY] = found
    return notes


EXPORTED_KEY = "ree_gpuc_exported"      # on the header: the file last exported (wd_api.file_path)


def game_name(ext: str) -> str:
    return next((g[1] for g in GAMES if g[0] == ext), ext)


def build_bytes(coll, mesh_coll=None, joints: str = "FILE", game: str = "", mesh_file: str = "",
                gpuc_path: str = "") -> tuple:
    """Return the cloth's .gpuc bytes and notes; raises ``ExportError`` when it cannot be exported. ``mesh_file``
    and ``gpuc_path`` lead to the .mesh whose weighted bones index the driven joints (``export_weighted``)."""
    head = props.header(coll)
    if head is None:
        raise ExportError("the cloth has no header")
    game = game or head.ree_gpuc_header.game
    cfg_objs = by_name(props.configs(coll))
    part_objs = by_name(props.parts(coll))
    if not part_objs:
        raise ExportError("the cloth has no Parts")
    for obj in part_objs:
        if obj.ree_gpuc_part.config not in cfg_objs:
            raise ExportError(f"{obj.name} has no Config of this cloth")
        if obj.mode != "OBJECT":
            raise ExportError(f"{obj.name} is in {obj.mode.lower().replace('_', ' ')} mode")
    if keeps_structure(coll):
        own = gp.ext_of(gpuc.Gpuc(stored_file(coll)).layout)
        if own != game:
            raise ExportError(f"it keeps a {game_name(own)} file's structure: export it for {game_name(own)}")
        if own == re4.EXT:
            if joints != "FILE":
                raise ExportError("RE4 keeps its original skinning and output bindings: use As Imported")
            return _build_re4(coll, head, cfg_objs, part_objs)
        return _build_keep(coll, head, cfg_objs, part_objs, joints == "MESH", mesh_coll, mesh_file, gpuc_path)
    return _build_generated(coll, head, cfg_objs, part_objs, mesh_coll, game, mesh_file, gpuc_path)


def _build_generated(coll, head, cfg_objs, part_objs, mesh_coll, ext=gp.EXT, mesh_file="", gpuc_path="") -> tuple:
    notes = []
    layout = gpuc.LAYOUTS[int(ext[1:])]
    arm = generate._armature(coll)
    if arm is None or arm.type != "ARMATURE":
        raise ExportError("the cloth has no armature")
    for obj in part_objs:
        if not canon.has(obj):
            raise ExportError(f"{obj.name} has no particles")
        if canon.locked(obj):
            raise ExportError(f"{obj.name} keeps a file's structure in a cloth that does not")
        problems = generate.check(obj)
        if problems:
            raise ExportError(f"{obj.name}: {problems[0]} (regenerate it)")
    vertices = head.ree_gpuc_header.drive_mode == "VERTICES"
    if vertices and ext not in props.VERTEX_DRIVE_GAMES:
        raise ExportError(f"{game_name(ext)} cloth cannot drive mesh vertices: set Drive to Joints")
    is_re4 = ext == re4.EXT                  # RE4 skins by the mesh's weighted bones, so it needs them either way
    weighted = [] if vertices and not is_re4 else export_weighted(coll, arm, mesh_coll, mesh_file, gpuc_path, notes)
    bones = [b.name for b in arm.data.bones]
    to_game = ROTATE_BACK @ np.array(arm.matrix_world, float)

    positions, sim_weight, skin_bones, skin_w, faces, uvs, outputs, bend = [], [], [], [], [], [], [], []
    shapes, part_vals, driven, base, starts = {}, [], {}, 0, {}
    skinned = collections.defaultdict(list)            # bone -> (particle, its skin weight to it) of every Part
    for obj, why in weld.skipped(coll):
        notes.append(f"{obj.name} left out: {why}")
    welded = collections.defaultdict(set)              # the Parts each Part is welded to
    for _, pa, _, pb, _ in weld.welds(coll):
        welded[pa].add(pb)
        welded[pb].add(pa)
    for k, obj in enumerate(part_objs):
        starts[obj] = base
        d = canon.load(obj)
        vals = canon.values(obj, d)
        rows, cols, closed = d["rows"], d["cols"], d["closed"]
        co = np.asarray(d["co"], float)
        game = co @ to_game[:3, :3].T + to_game[:3, 3]
        n = rows * cols
        positions.append(game)
        bend.append(np.full(n, d["diagonal_bend"]))
        sim_weight.append(np.clip(vals["sim_weight"], 0.0, 1.0))
        part_vals.append(vals)
        root = arm.data.bones.get(obj.ree_gpuc_part.root_joint)
        inside = np.array(root.head_local, float) if root is not None else np.zeros(3)
        quads = grid.faces(rows, cols, closed)
        quad_uv = [[(min(max(u, 0.0), 1.0), min(max(v, 0.0), 1.0)) for u, v in f]
                   for f in grid.face_uv(rows, cols, closed, 0)]
        if not _outward(co, rows, cols, closed, d["centre"], inside):
            quads = [(a, dd, c, b) for a, b, c, dd in quads]
            quad_uv = [[f[0], f[3], f[2], f[1]] for f in quad_uv]
        if obj.ree_gpuc_part.other_diagonal:              # gpuc_build splits (a, b, c, d) at a-c: start at b
            quads = [q[1:] + q[:1] for q in quads]
            quad_uv = [f[1:] + f[:1] for f in quad_uv]
        faces += [tuple(base + i for i in f) for f in quads]
        uvs += quad_uv
        node_full = [bones.index(name) for name in d["nodes"]]
        shared = []
        for i in range(n):
            w = np.asarray(d["skin_w"][i], float)
            u8 = np.floor(w / max(w.sum(), 1e-9) * 255 + 0.5).astype(int)
            u8[int(np.argmax(u8))] += 255 - int(u8.sum())
            idx = [node_full[j] for j in d["skin"][i]]
            skin_bones.append(idx + [idx[0]] * 4)
            skin_w.append(list(u8) + [0] * 4)
            for j, x in zip(d["skin"][i], w / max(w.sum(), 1e-9)):
                if x > 0:
                    skinned[d["nodes"][j]].append((base + i, float(x)))
        for name in d["nodes"] if not vertices else ():
            if name not in weighted:
                notes.append(f"{name} has no weights in the mesh: it is not driven")
                continue
            if name in driven:
                if not welded[obj] & driven[name]:
                    shared.append(name)
                driven[name].add(obj)
                continue
            world = to_game @ np.array(arm.data.bones[name].matrix_local, float)
            outputs.append(dict(palette=weighted.index(name), world=world.T, particles=[], weights=[],
                                full=bones.index(name), bone=name))
            driven[name] = {obj}
        if shared:
            notes.append(f"{obj.name}: {len(shared)} of its bones are an earlier Part's too ({', '.join(shared[:3])}"
                         f"{', ...' if len(shared) > 3 else ''}) and it is not welded to it: their joints follow "
                         f"both Parts")
        for (a, b, typ, _), tail in zip(canon.links(d), canon.link_tails(obj, d)):
            shapes[(base + int(a), base + int(b), int(typ))] = gp.tail_bytes(*map(float, tail))   # float32 rounds
        base += n
    # As in game files: each joint is driven by every particle skinned to its bone (any Part), by that skin weight;
    # the heaviest MIN_SLOTS are kept (ties: lower particles), in descending particle order.
    for o in outputs:
        keep = sorted(skinned[o.pop("bone")], key=lambda p: (-p[1], p[0]))[:gpuc_build.MIN_SLOTS]
        keep.sort(key=lambda p: -p[0])
        total = sum(x for _, x in keep)
        o["particles"], o["weights"] = [p for p, _ in keep], [x / total for _, x in keep]
    outputs.sort(key=lambda o: o["full"])
    if not outputs and not vertices:
        raise ExportError("no bone of the cloth has weights in the mesh: nothing would move")
    spec = gpuc_build.ClothSpec(np.vstack(positions), np.concatenate(sim_weight), skin_bones, skin_w, faces, uvs,
                                outputs, np.concatenate(bend))
    skin_table = sorted({b for row, w in zip(skin_bones, skin_w) for b, x in zip(row, w) if x})
    link_tail = lambda typ, a, b: shapes.get((min(a, b), max(a, b), typ))  # noqa: E731
    if is_re4:
        data = _build_generated_re4(coll, head, arm, cfg_objs, part_objs, part_vals, spec, weighted, bones,
                                    link_tail, starts, vertices, mesh_coll, find_mesh(coll, mesh_file, gpuc_path)[0],
                                    notes)
        return data, list(dict.fromkeys(notes))
    out = gpuc_build.build(spec, layout, skin_table, link_tail=link_tail, welds=_weld_links(coll, starts))

    g = gpuc.Gpuc(out, layout)
    for i in gpuc_build.PACKED:                               # parsing infers 16 from a start on 16; these pack at 4
        g.sections[i].align = 4
    _write_blocks(g, head.ree_gpuc_header, cfg_objs, part_objs, part_vals, notes, arm, vertices=vertices)
    _write_capsules(g, coll, arm, notes)
    # one batch info and sim LOD (the generator's); no interpolation, restriction or self-collision contact;
    # simulation deform weights 1, as in game files
    for name, v in (("batch_infos", 1), ("sim_lods", 1), ("sim_lod_one", 1), ("interpolations", 0),
                    ("restrictions", 0), ("sim_deform_weights", 1), ("point_triangle_contacts", 0),
                    ("edge_edge_contacts", 0)):
        struct.pack_into("<I", g.header, gp.offset_of(ext, name), v)
    if vertices:
        _drive_vertices(g, head, coll, arm, mesh_coll, find_mesh(coll, mesh_file, gpuc_path)[0], notes)
    data = g.build()
    gpuc.Gpuc(data, layout)                                   # raises if the output does not parse back
    return data, list(dict.fromkeys(notes))


def _build_generated_re4(coll, head, arm, cfg_objs, part_objs, part_vals, spec, weighted, bones, link_tail, starts,
                         vertices, mesh_coll, mesh_path, notes) -> bytes:
    """RE4 bytes for a cloth made from bones (``re4_build``): skin by the mesh's weighted bones, joints (452 layout)
    or, with Drive Mesh Vertices, LOD0's vertices (328 layout); the header's RE4 values, the Parts' and Configs'."""
    h = head.ree_gpuc_header
    place = {name: k for k, name in enumerate(weighted)}
    skin_remap = np.zeros((len(spec.positions), 8), np.int64)
    for i, (row, w) in enumerate(zip(spec.skin_bones, spec.skin_w)):
        for k, (b, x) in enumerate(zip(row, w)):
            if not x:
                continue
            if bones[b] not in place:
                raise ExportError(f"{bones[b]} skins particles but has no weights in the mesh: RE4 skins the cloth "
                                  f"by the mesh's weighted bones")
            skin_remap[i, k] = place[bones[b]]
    if (spec.skin_w > 0).sum(1).max(initial=0) > 8:
        raise ExportError("a particle has more than 8 skin bones (RE4 takes 8)")
    pvalues = np.column_stack([np.concatenate([np.clip(v["sim_weight"], 0.0, 1.0) if f == "sim_weight" else v[f]
                                               for v in part_vals]) for f in re4.PARTICLE_VALUES])
    part_of = np.concatenate([np.full(len(v["sim_weight"]), k) for k, v in enumerate(part_vals)])
    parts = []
    for obj in part_objs:
        p = obj.ree_gpuc_part
        known = reference.bone_owner(reference.cloth_of(obj), arm, p.root_joint) is not None
        root = p.root_joint if p.use_root_joint and known else None
        if p.use_root_joint and root is None:
            notes.append(f"{obj.name}: its root joint {p.root_joint or '(none)'} is not a bone: written without one")
        parts.append({"name": p.part_name, "config": cfg_objs.index(p.config), "root": root,
                      "body10": struct.pack("<ii", -1, -1),          # -1 in every game file
                      "body24": struct.pack("<4f3i", p.friction, p.bounce, p.stickiness, p.re4_value_30,
                                            p.plane_bits, p.sphere_bits, p.capsule_bits)})
    configs = []
    for obj in cfg_objs:
        c = obj.ree_gpuc_config
        guid = struct.pack("<4i", c.unk10, c.unk14, c.unk18, c.unk1c)
        values = struct.pack("<6f", *(getattr(c, f"re4_value_{o:02x}") for o in range(0x24, 0x3C, 4)))
        configs.append({"name": obj.name, "body0c": struct.pack("<I", 0) + guid + struct.pack("<i", c.re4_flags_20)
                        + values + struct.pack("<i", c.re4_flags_3c)})
    capsules = []
    for root in by_name(r for r in collision.roots(bpy.context.scene) if r.name in coll.all_objects):
        c = root.ree_gpuc_capsule
        if any(reference.bone_owner(coll, arm, j) is None for j in (c.joint_a, c.joint_b))                 or c.radius_a <= 0 or c.radius_b <= 0:
            notes.append(f"{root.name}: left out (RE4 names a capsule's joints: both must be bones; radii over 0)")
            continue
        capsules.append((c.joint_a, c.joint_b, struct.pack("<3ff3ff", *c.offset_a, c.radius_a, *c.offset_b,
                                                             c.radius_b)))
    drive = None
    if vertices:
        if not mesh_path:
            raise ExportError("Drive Mesh Vertices needs the exported .mesh: export the mesh, then put its path in "
                              "Mesh File or the .gpuc beside it")
        pos, lod_of, _ = mesh_file_vertices(mesh_path)
        frames = vertex_drive.tri_frames(spec.positions, [t[:3] for t in gpuc_build._triangles(spec)])
        near = vertex_drive.distances(pos, frames).min(1)
        on, off, unmatched, missing = drive_overrides(mesh_coll or mesh_collection(arm), arm, pos, lod_of)
        driven = np.flatnonzero(((near < h.drive_distance) | on) & ~off & (lod_of == 0))
        if not len(driven):
            raise ExportError(f"no LOD0 vertex of {os.path.basename(mesh_path)} lies within "
                              f"{h.drive_distance * 100:.1f} cm of the cloth: raise Drive Distance or add vertices "
                              f"to the GPUC_Drive group")
        drive = (pos[driven], driven, h.drive_slots)
        notes.append(f"Drives {len(driven)} of {int((lod_of == 0).sum())} LOD0 vertices of "
                     f"{os.path.basename(mesh_path)} (RE4 drives LOD0 alone)")
        if unmatched:
            notes.append(f"{unmatched} vertices match no scene vertex: their vertex groups are not read")
    try:
        return re4_build.build(spec, pvalues, part_of, parts, configs,
                               {"max_velocity": h.max_velocity, "h44": h.re4_unk44, "h60": h.re4_unk60},
                               skin_remap, len(weighted), link_tail, _weld_links(coll, starts), capsules, drive, notes)
    except ValueError as e:
        raise ExportError(str(e)) from e


def _weld_links(coll, starts) -> list:
    """Return the writable welds as ``gpuc_build`` links (a, b, type, tail) in file particle indices, each weld's
    tail on all its links, pointing from the later Part to the earlier as in game files."""
    out = []
    for obj, pa, ia, pb, ib in weld.welds(coll):
        a, b = starts[pa] + ia, starts[pb] + ib
        w = obj.ree_gpuc_weld
        out.append((max(a, b), min(a, b), w.link_type, gp.tail_bytes(*w.tail())))
    return out


def _build_keep(coll, head, cfg_objs, part_objs, from_mesh=False, mesh_coll=None, mesh_file="",
                gpuc_path="") -> tuple:
    """Write a Keep Structure cloth's values over its stored file, the structure unchanged; ``from_mesh`` re-indexes
    the driven joints by their bones among the mesh's weighted bones (``export_weighted``)."""
    notes = []
    g = gpuc.Gpuc(stored_file(coll))
    game = gp.ext_of(g.layout)
    f_part = np.frombuffer(bytes(g.sections[0].data)[:g.counts()["particles"] * gp.PARTICLE], np.uint8) \
        .reshape(-1, gp.PARTICLE)[:, 0x28:0x2C].copy().view("<u4")[:, 0]
    counts = np.bincount(f_part, minlength=len(part_objs)).tolist()
    mine = []
    for obj in part_objs:
        if not canon.locked(obj):
            raise ExportError(f"{obj.name} was made from bones in a cloth that keeps a file's structure")
        mine.append(canon.count(canon.load(obj)))
    if mine != counts:
        raise ExportError(f"its Parts ({mine} particles) are not its file's ({counts}): import it again")
    arm = next((o.ree_gpuc_part.armature for o in part_objs if o.ree_gpuc_part.armature), None)
    vals = [canon.values(o) for o in part_objs]
    starts = [int(np.argmax(f_part == k)) for k in range(len(part_objs))]        # each part's run, where it lies
    vertices = vertex_drive_info(stored_file(coll)) is not None
    _write_blocks(g, head.ree_gpuc_header, cfg_objs, part_objs, vals, notes, arm, starts, vertices)
    # each link's tail: the links of a part in the file's order, as the import took them
    tails = [canon.link_tails(o, canon.load(o)) for o in part_objs]
    taken = [0] * len(part_objs)
    s2 = g.sections[2].data
    welded = {}
    for obj, pa, ia, pb, ib in weld.welds(coll):
        if pa in part_objs and pb in part_objs:
            welded[(starts[part_objs.index(pa)] + ia, starts[part_objs.index(pb)] + ib)] = obj
    for k in range(g.counts()["links"]):
        a, b = struct.unpack_from("<HH", s2, k * 16)
        i = int(f_part[a])
        if f_part[b] != i:                            # a link joining two parts: its weld's
            obj = welded.get((a, b))
            if obj is None:
                raise ExportError(f"the file welds particle {a} (part {i}) to {b} (part {int(f_part[b])}) and no "
                                  f"weld of the cloth does: a Keep Structure cloth cannot take a weld out")
            if obj.get(weld.EDITED_KEY):
                s2[k * 16 + 8:k * 16 + 16] = gp.tail_bytes(*obj.ree_gpuc_weld.tail())
            continue
        s2[k * 16 + 8:k * 16 + 16] = gp.tail_bytes(*tails[i][taken[i]])
        taken[i] += 1
    _write_capsules(g, coll, arm, notes)
    if from_mesh:
        names = list(head.get(JOINTS_KEY, ()))
        if arm is None or len(names) != struct.unpack_from("<I", g.header, gp.offset_of(game, "joints"))[0]:
            raise ExportError("its driven joints were not found at import (no mesh collection): export As Imported")
        weighted = export_weighted(coll, arm, mesh_coll, mesh_file, gpuc_path, notes)
        s23 = g.sections[23].data
        lost = [k for k, n in enumerate(names) if n not in weighted]
        if lost:
            raise ExportError(f"{len(lost)} driven joints have no weighted bone in the mesh "
                              f"({', '.join(names[k] or '(none)' for k in lost[:3])}): export As Imported")
        for k, n in enumerate(names):
            struct.pack_into("<I", s23, 4 * k, weighted.index(n))
    data = g.build()
    gpuc.Gpuc(data, g.layout)
    return data, list(dict.fromkeys(notes))


def _re4_properties(pg):
    """Return a JSON-ready snapshot of a property group, hidden fields included, so clipboard or API edits count."""
    out = {}
    for prop in pg.bl_rna.properties:
        name = prop.identifier
        if name == "rna_type" or prop.is_readonly and prop.type not in {"POINTER", "COLLECTION"}:
            continue
        value = getattr(pg, name)
        if prop.type == "POINTER":
            out[name] = _re4_properties(value) if isinstance(value, bpy.types.PropertyGroup) else (value.name if value else None)
        elif prop.type == "COLLECTION":
            out[name] = [_re4_properties(v) for v in value]
        else:
            out[name] = list(value) if getattr(prop, "array_length", 0) else value
    return out


def _snapshot_re4(coll):
    head = props.header(coll)
    head["ree_gpuc_re4_mapping"] = 2
    for i, obj in enumerate(by_name(props.configs(coll))):
        obj["ree_gpuc_re4_index"] = i
    for obj, pg in [(head, head.ree_gpuc_header)] + [(o, o.ree_gpuc_config) for o in props.configs(coll)] + [
            (o, o.ree_gpuc_part) for o in props.parts(coll)]:
        obj["ree_gpuc_re4_baseline"] = json.dumps(_re4_properties(pg))
        obj["ree_gpuc_re4_transform"] = [float(v) for row in obj.matrix_basis for v in row]


def _re4_changes(obj, pg, allowed, ignored=()):
    if "ree_gpuc_re4_baseline" not in obj:
        raise ExportError(f"{obj.name}: not an original RE4 object")
    before = json.loads(obj["ree_gpuc_re4_baseline"])
    now = _re4_properties(pg)
    changed = {k: v for k, v in now.items() if before.get(k) != v}
    unsupported = set(changed) - set(allowed) - set(ignored)
    if unsupported:
        raise ExportError(f"{obj.name}: RE4 cannot edit {', '.join(sorted(unsupported))}")
    transform = [float(v) for row in obj.matrix_basis for v in row]
    if not np.allclose(transform, list(obj["ree_gpuc_re4_transform"]), rtol=1e-6, atol=1e-6):
        raise ExportError(f"{obj.name}: RE4 cannot change object transforms")
    return {k: v for k, v in changed.items() if k in allowed}


def _build_re4(coll, head, cfg_objs, part_objs):
    if head.get("ree_gpuc_re4_mapping") != 2:
        raise ExportError("an older version of the add-on imported this RE4 cloth: import the original file again with Keep Structure")
    raw = stored_file(coll)
    source = re4.File(raw)
    f = source.geometry()
    configs = sorted(cfg_objs, key=lambda o: o.get("ree_gpuc_re4_index", -1))
    parts = sorted(part_objs, key=lambda o: o.get("ree_gpuc_re4_index", -1))
    want = [i for i in range(source.c["parts"]) if (f["part_of"] == i).any()]
    if ([o.get("ree_gpuc_re4_index", -1) for o in configs] != list(range(source.c["configs"]))
            or [o.get("ree_gpuc_re4_index", -1) for o in parts] != want):
        raise ExportError("RE4 cannot add, remove or replace Parts or Configs")
    if any(r.name in coll.all_objects for r in collision.roots(bpy.context.scene)):
        raise ExportError("RE4 collisions are preserved in the source file; editing or adding capsules is not supported")
    header = _re4_changes(head, head.ree_gpuc_header, {"max_velocity"}, {"reference_clsp"})
    cfg_changes = [_re4_changes(o, o.ree_gpuc_config, re4.CONFIG) for o in configs]
    part_changes = [{} for _ in range(source.c["parts"])]
    particle_changes, tails = {}, {}
    part_by_index = {}
    for obj in parts:
        i = obj["ree_gpuc_re4_index"]
        part_by_index[i] = obj
        ignored = {f + "_range" for f in canon.FIELD_NAMES[1:]} | {"link_preset", "vertical", "ring", "shear", "bend"}
        part_changes[i] = _re4_changes(obj, obj.ree_gpuc_part, re4.PART, ignored)
        if not canon.locked(obj) or canon.mismatch(obj):
            raise ExportError(f"{obj.name}: RE4 particle structure changed; import again")
        sel = np.where(f["part_of"] == i)[0]
        d = canon.load(obj)
        tris = f["tris"][(f["part_of"][f["tris"]] == i).all(axis=1)] - int(sel[0])
        if (canon.count(d) != len(sel) or not np.array_equal(d["co"], f["pos"][sel].astype(np.float32))
                or not np.array_equal(d["tris"], tris)):
            raise ExportError(f"{obj.name}: RE4 particle positions or triangles changed; import again")
        values = canon.values(obj, d)
        for name, value in values.items():
            original = f["values"][name][sel]
            changed = np.flatnonzero(value != original)
            if len(changed) and name not in re4.PARTICLE_VALUES:
                raise ExportError(f"RE4 has no particle field {name}")
            for j in changed:
                particle_changes.setdefault(int(sel[j]), {})[name] = float(value[j])
    # Only edited link shapes and welds are patched; other tails stay byte-exact.
    per_part = {i: canon.link_tails(o, canon.load(o)) for i, o in part_by_index.items()}
    taken = collections.Counter()
    starts = {i: int(np.flatnonzero(f["part_of"] == i)[0]) for i in part_by_index}
    welded = {}
    weld_pairs = []
    for obj, pa, ia, pb, ib in weld.welds(coll):
        if pa in parts and pb in parts:
            pair = (starts[pa["ree_gpuc_re4_index"]] + ia, starts[pb["ree_gpuc_re4_index"]] + ib)
            welded[pair] = obj
            weld_pairs.append(pair)
    expected_pairs = [(a, b) for a, b, _, _ in f["links"] if f["part_of"][a] != f["part_of"][b]]
    if collections.Counter(weld_pairs) != collections.Counter(expected_pairs):
        raise ExportError("RE4 cannot add, remove or change welded particle pairs")
    for k, (a, b, typ, tail) in enumerate(f["links"]):
        i, other = int(f["part_of"][a]), int(f["part_of"][b])
        if i != other:
            obj = welded.get((a, b))
            if obj is None:
                raise ExportError("RE4 cannot remove an imported weld")
            if obj.get(weld.EDITED_KEY):
                if obj.ree_gpuc_weld.link_type != typ:
                    raise ExportError("RE4 cannot change weld link types")
                tails[k] = gp.tail_bytes(*obj.ree_gpuc_weld.tail())
        else:
            obj = part_by_index[i]
            shape = grid.SHAPES[_shape(typ, a, b, f["pos"])].lower()
            if shape in obj.get(canon.EDITED_KEY, ()):
                tails[k] = gp.tail_bytes(*per_part[i][taken[i]])
            taken[i] += 1
    try:
        return re4.patch(raw, header, cfg_changes, part_changes, particle_changes, tails), []
    except ValueError as e:
        raise ExportError(str(e)) from e


def mesh_armature_or(mesh_coll, arm):
    found = next((o for o in mesh_coll.all_objects if o.type == "ARMATURE"), None)
    return found or arm


def capsule_records(coll, arm, notes) -> list:
    """Return the cloth's capsules as section 16 records; a joint on no bone falls back to its imported hash, and a
    capsule with neither, or a radius <= 0, is left out. Both cases add a note."""
    out = []
    for root in by_name(r for r in collision.roots(bpy.context.scene) if r.name in coll.all_objects):
        c = root.ree_gpuc_capsule
        hashes = []
        for joint, stored in ((c.joint_a, c.hash_a), (c.joint_b, c.hash_b)):
            if reference.bone_owner(coll, arm, joint) is not None:
                hashes.append(gpuc_build.name_hash(joint))
            elif stored:
                hashes.append(stored & 0xFFFFFFFF)
                notes.append(f"{root.name}: {joint} written by its hash (it takes effect if the game's skeleton "
                             f"has that bone)")
            else:
                hashes.append(None)
        if None in hashes or c.radius_a <= 0 or c.radius_b <= 0:
            notes.append(f"{root.name}: left out (a joint on no bone, or a radius not over 0)")
            continue
        out.append((*hashes, tuple(c.offset_a), c.radius_a, tuple(c.offset_b), c.radius_b))
    return out


def _write_capsules(g, coll, arm, notes) -> None:
    caps = capsule_records(coll, arm, notes)
    g.set_capsules(b"".join(struct.pack("<II8x3ff3ff", ha, hb, *oa, ra, *ob, rb) for ha, hb, oa, ra, ob, rb in caps))
    struct.pack_into("<I", g.header, gp.offset_of(gp.ext_of(g.layout), "capsules"), len(caps))


def _contiguous(part_of) -> bool:
    """Whether each part's particles form one run; the runs may come in any order."""
    return all(np.ptp(idx) + 1 == len(idx) for idx in (np.where(part_of == p)[0] for p in np.unique(part_of)))


def _write_blocks(g, h, cfg_objs, part_objs, part_vals, notes, arm=None, starts=None, vertices=False) -> None:
    """``vertices``: the file drives mesh vertices, so Kernel Flags bit 2 is cleared instead of set."""
    header = g.header
    game = gp.ext_of(g.layout)
    values = {name: getattr(h, name) for name in gp.header_fields(game)}
    values["calculate_mode"] = h.bl_rna.properties["calculate_mode"].enum_items[h.calculate_mode].value
    if vertices and values["kernel_flags"] & 4:
        notes.append("Kernel Flags: Mesh Multi Joint Deform turned off (the cloth drives mesh vertices)")
        values["kernel_flags"] &= ~4
    elif not vertices and not values["kernel_flags"] & 4:
        notes.append("Kernel Flags: Mesh Multi Joint Deform kept on (the exported file requires it)")
        values["kernel_flags"] |= 4
    gp.write_header(header, values, game)
    # configs and their curves' keys
    key_base = g.sections[12].offset
    blocks, keys = bytearray(), bytearray()
    for obj in cfg_objs:
        cfg = obj.ree_gpuc_config
        c = {name: getattr(cfg, name) for name in gp.CONFIG}
        curves = {}
        for name in gp.CURVES:
            curve = getattr(cfg, name)
            ks = sorted(((k.time, k.value, k.bl_rna.properties["type"].enum_items[k.type].value, k.in_tangent,
                          k.in_weight, k.out_tangent, k.out_weight) for k in curve.keys), key=lambda x: x[0])
            curves[name] = {**{f: getattr(curve, f) for f in gp.CURVE}, "keys": ks}
        block, kb = gp.config_block(c, curves, key_base, len(keys))
        blocks += block
        keys += kb
    g.sections[8].data = bytearray(blocks)
    g.sections[12].data = bytearray(keys)
    # part blocks, their names, every particle's values, part and config
    # Keep what precedes the names in section 22: an Onimusha file may hold a second section 21 block there (count at
    # header 0x60), which has no table slot of its own.
    names, at = gp.names_section([o.ree_gpuc_part.part_name for o in part_objs])
    before = min((off for sec, off in g.refs.get("sec6", []) if sec == 22), default=0)
    g.sections[22].data = bytearray(bytes(g.sections[22].data[:before]) + names)
    g.refs["sec6"] = [(22, before + off) for off in at]
    s6, s0 = bytearray(), g.sections[0].data
    starts = starts or np.cumsum([0] + [len(v["sim_weight"]) for v in part_vals]).tolist()
    for k, (obj, vals) in enumerate(zip(part_objs, part_vals)):
        p = obj.ree_gpuc_part
        config = cfg_objs.index(p.config)
        known = reference.bone_owner(reference.cloth_of(obj), arm, p.root_joint) is not None
        root = p.root_joint if p.use_root_joint and known else None
        block = bytearray(gp.part_block({name: getattr(p, name) for name in gp.PART}, config, p.part_name, root))
        if p.use_root_joint and root is None and ROOT_HASH_KEY in obj.keys():      # the file's, kept
            struct.pack_into("<II", block, 0x18, 1, obj[ROOT_HASH_KEY] & 0xFFFFFFFF)
            notes.append(f"{obj.name}: the root joint written by the file's hash")
        elif p.use_root_joint and root is None:
            notes.append(f"{obj.name}: its root joint {p.root_joint or '(none)'} is not a bone: written without one")
        s6 += block
        n = len(vals["sim_weight"])
        for i in range(n):
            at_ = (starts[k] + i) * gp.PARTICLE
            for field, off in gp.PARTICLE_VALUES.items():
                v = float(np.clip(vals[field][i], 0.0, 1.0)) if field == "sim_weight" else float(vals[field][i])
                s0[at_ + off:at_ + off + 4] = np.float32(v).tobytes()
            s0[at_ + 0x28:at_ + 0x30] = np.array([k, config], "<u4").tobytes()
    g.sections[6].data = s6
    struct.pack_into("<I", header, gp.offset_of(game, "parts"), len(part_objs))
    struct.pack_into("<I", header, gp.offset_of(game, "configs"), len(cfg_objs))


# --- Keep Structure -----------------------------------------------------------------------------------------

RAW_KEY = "ree_gpuc_raw"                 # on the header of a Keep Structure cloth: its file (base64)
ROOT_HASH_KEY = "ree_gpuc_root_hash"     # on a Part whose root joint the armature lacks: the file's hash


class ImportProblem(Exception):
    pass


def keeps_structure(coll) -> bool:
    head = props.header(coll) if coll is not None else None
    return head is not None and RAW_KEY in head.keys()


def stored_file(coll) -> bytes:
    return base64.b64decode(props.header(coll)[RAW_KEY])


def mesh_armature(mesh_coll):
    arm = next((o for o in mesh_coll.all_objects if o.type == "ARMATURE"), None) if mesh_coll is not None else None
    if arm is None:
        raise ImportProblem("pick the mesh collection (the RE Mesh Editor's, with its armature)")
    return arm


def _u32s(g, section, n) -> np.ndarray:
    return np.frombuffer(bytes(g.sections[section].data)[:4 * n], "<u4").astype(np.int64)


def file_parts(raw: bytes) -> dict:
    """Return a .gpuc's structure in game space: particles (skin as full mesh bone indices, u8 weights), triangles
    and UVs, links (a, b, type, tail), output joints and the file's own capsules."""
    if re4.matches(raw):
        return re4.File(raw).geometry()
    g = gpuc.Gpuc(raw)
    game = gp.ext_of(g.layout)
    c = g.counts()
    P, T, L = c["particles"], c["triangles"], c["links"]
    s0 = bytes(g.sections[0].data)
    p0 = np.frombuffer(s0[:P * gp.PARTICLE], np.uint8).reshape(P, gp.PARTICLE)
    q = p0[:, 0x54:0x58].copy().view("<u4")[:, 0]
    normal = np.column_stack([(q >> s & 0x3FF) / 1023 * 2 - 1 for s in (0, 10, 20)])
    t11 = _u32s(g, 11, c["joint_table"])
    skin_idx, skin_w = [], []
    for i in range(P):
        idx, w = gpuc.read_skin(s0, i, g.layout)
        skin_idx.append([int(t11[k]) if wk and k < len(t11) else -1 for k, wk in zip(idx, w)])
        skin_w.append(list(w))
    tb = bytes(g.sections[1].data)
    tris = np.array([struct.unpack_from("<3H", tb, i * 20) for i in range(T)], np.int64).reshape(-1, 3)
    tri_uv = np.array([struct.unpack_from("<6H", tb, i * 20 + 8) for i in range(T)], float).reshape(-1, 3, 2) / 65535
    lb, ty = bytes(g.sections[2].data), bytes(g.sections[29].data)
    links = [(*struct.unpack_from("<HH", lb, k * 16), struct.unpack_from("<I", ty, k * 4)[0],
              lb[k * 16 + 8:k * 16 + 16]) for k in range(L)]
    J, S = c["joints"], c["joint_slots"]
    flags = struct.unpack_from("<I", raw, gp.header_fields(game)["kernel_flags"][0])[0]
    joints = []
    if flags & 4 and J:
        t23 = _u32s(g, 23, J)
        s24, s25 = bytes(g.sections[24].data), bytes(g.sections[25].data)
        for j in range(J):
            slots = [struct.unpack_from("<if", s24, (j * S + k) * 8) for k in range(S)]
            used = [(k, cp, w) for k, (cp, w) in enumerate(slots) if cp >= 0]
            off = [np.frombuffer(s25[(j * S + k) * 48 + 36:(j * S + k) * 48 + 48], "<f4").astype(float)
                   for k, _, _ in used]
            joints.append({"palette": int(t23[j]), "drivers": [(cp, w) for _, cp, w in used], "offsets": off})
    caps = []
    s16 = bytes(g.sections[16].data) if 16 in g.sections else b""
    for k in range(c["capsules"] if s16 else 0):
        ha, hb = struct.unpack_from("<II", s16, k * 48)
        v = struct.unpack_from("<8f", s16, k * 48 + 16)
        caps.append((ha, hb, v[0:3], v[3], v[4:7], v[7]))
    frames = np.frombuffer(bytes(g.sections[26].data)[:P * 48], "<f4").reshape(-1, 3, 4) if joints else None
    return {"g": g, "game": game, "count": P, "pos": p0[:, 0x48:0x54].copy().view("<f4").astype(float),
            "part_of": p0[:, 0x28:0x2C].copy().view("<u4")[:, 0].astype(np.int64), "normal": normal,
            "skin": np.array(skin_idx, np.int64), "skin_w": np.array(skin_w, float), "tris": tris,
            "tri_uv": tri_uv, "links": links, "joints": joints, "caps": caps, "frames": frames,
            "flags": flags, "vertex": None if flags & 4 else vertex_drive_info(raw),
            "values": {field: p0[:, off:off + 4].copy().view("<f4")[:, 0].astype(np.float32)
                       for field, off in gp.PARTICLE_VALUES.items()}}


def _shape(typ, a, b, pos) -> int:
    """Return a link's ``grid.SHAPES`` index from its type and, for type 0, its direction (game space, y up)."""
    if typ == 1:
        return 2
    if typ != 0:
        return 3
    return 0 if gpuc_build.direction(pos[a], pos[b]) == "vert" else 1


JOINTS_KEY = "ree_gpuc_joints"           # on a Keep Structure header: each driven joint's bone ("" none found)


def _bind(f: dict, j: dict) -> np.ndarray:
    """Return a driven joint's bind position (game space): its first particle plus the bind offset in that frame."""
    cp = j["drivers"][0][0]
    return f["pos"][cp] + f["frames"][cp][:, :3].astype(float).T @ j["offsets"][0]


def locked_problems(obj) -> list:
    """Return problems of a Keep Structure Part against its armature: missing skin or driven bones, or driven
    bones over 1 mm from where the file binds them (the import's tolerance); [] when none or not applicable."""
    coll = reference.cloth_of(obj)
    arm = obj.ree_gpuc_part.armature
    if coll is None or not keeps_structure(coll) or arm is None or arm.type != "ARMATURE":
        return []
    d = canon.load(obj)
    missing = [b for b in d["nodes"] if not b.startswith("#") and b not in arm.data.bones]
    if missing:
        return [f"{len(missing)} skin bones are gone: {', '.join(missing[:4])}"]
    f = file_parts(stored_file(coll))
    k = by_name(props.parts(coll)).index(obj)
    names = list(props.header(coll).get(JOINTS_KEY, ()))
    if not f["joints"] or f["frames"] is None or len(names) != len(f["joints"]):
        return []
    to_game = ROTATE_BACK @ np.array(arm.matrix_world, float)
    gone, far = [], []
    for name, j in zip(names, f["joints"]):
        if not name or not j["drivers"] or f["part_of"][j["drivers"][0][0]] != k:
            continue
        b = arm.data.bones.get(name)
        if b is None:
            gone.append(name)
            continue
        far.append(float(np.linalg.norm((to_game @ np.r_[b.head_local, 1.0])[:3] - _bind(f, j))))
    if gone:
        return [f"{len(gone)} driven bones are gone: {', '.join(gone[:4])}"]
    moved = [x for x in far if x > 1e-3]
    if moved:
        return [f"{len(moved)} driven bones moved off where the file binds them (up to {max(moved) * 100:.2f} cm): "
                f"the file's structure no longer fits the mesh (import it again with Rebuild from Bones, or move them "
                f"back)"]
    return []


def joint_bones(f: dict, arm, weighted=None) -> tuple:
    """Match each driven joint's bind position to the nearest bone head; return (names, "" where none is within
    1 mm; distances). Where several heads lie within 1 mm, the bone the joint's index names among ``weighted``
    (the mesh's weighted bones) is taken when it is one of them."""
    if not f["joints"] or f["frames"] is None:
        return [], []
    to_game = ROTATE_BACK @ np.array(arm.matrix_world, float)
    names = [b.name for b in arm.data.bones]
    heads = np.array([(to_game @ np.r_[b.head_local, 1.0])[:3] for b in arm.data.bones])
    out, dist = [], []
    for j in f["joints"]:
        if not j["drivers"]:
            out.append("")
            dist.append(float("inf"))
            continue
        d = np.linalg.norm(heads - _bind(f, j), axis=1)
        k = int(np.argmin(d))
        named = weighted[j["palette"]] if weighted and 0 <= j["palette"] < len(weighted) else None
        if named in names and d[names.index(named)] <= 1e-3:
            k = names.index(named)
        out.append(names[k] if d[k] <= 1e-3 else "")
        dist.append(float(d[k]))
    return out, dist


def mesh_weighted(path: str, arm):
    """The weighted bones of the .mesh beside the .gpuc at ``path`` when its skeleton is ``arm``'s, else None."""
    found = companion_mesh(path)[0] if path else ""
    try:
        names, weighted = mesh_file_bones(found) if found else ([], [])
    except ExportError:
        return None
    return weighted if weighted and names == [b.name for b in arm.data.bones] else None


def check_keep(f: dict, arm, mesh_coll, path: str = "") -> tuple:
    """Return (each driven joint's bone, notes) for the file on this armature; raises ``ImportProblem`` when it
    does not fit. The .mesh beside ``path`` (``mesh_weighted``) settles joints on bones that share a place."""
    notes = []
    bones = [b.name for b in arm.data.bones]
    if not f["flags"] & 4 and f["vertex"] is None:
        raise ImportProblem("its Kernel Flags lack Mesh Multi Joint Deform and it drives no mesh vertices the "
                            "editor reads")
    used = f["skin"][f["skin"] >= 0]
    if len(used) and used.max() >= len(bones):
        raise ImportProblem(f"its skinning names bone {int(used.max())}, the armature has {len(bones)}: pick the "
                            f"mesh this .gpuc belongs to")
    from_file = mesh_weighted(path, arm)
    joints, dist = joint_bones(f, arm, from_file)
    lost = [x for n, x in zip(joints, dist) if not n]
    if joints and len(lost) == len(joints):
        raise ImportProblem("none of its driven joints sits on a bone of this armature: pick the mesh this .gpuc "
                            "belongs to")
    if lost:
        notes.append(f"{len(lost)} of {len(joints)} driven joints sit on no bone (the nearest "
                     f"{min(lost) * 1000:.1f} mm off): their indices are kept as they are")
    try:
        weighted = from_file or weighted_bones(arm, mesh_coll)
        moved = sum(1 for n, j in zip(joints, f["joints"]) if n and (n not in weighted or weighted.index(n) != j["palette"]))
        if moved:
            notes.append(f"{moved} driven joints' indices differ from the mesh's as the RE Mesh Editor exports it: "
                         f"export with Joint Indices 'From Mesh' once the mesh is exported again")
    except ExportError as e:
        notes.append(f"Joint indices not compared with the mesh: {e}")
    return joints, notes


NO_MESH = ("No mesh collection: the shape only. The skin bones go by index (no posing), the driven joints are "
           "not found (export As Imported only) and a joint the reference skeleton lacks by its hash")


def import_keep(path: str, mesh_coll=None) -> tuple:
    """Import the file as a new Keep Structure collection (locked Parts, the file stored on the header for export);
    return (collection, notes). Without a mesh collection the header is rotated as the RE Mesh Editor rotates its
    armature, so the shape lands where the mesh import would put it."""
    raw = open(path, "rb").read()
    is_re4 = re4.matches(raw)
    if is_re4:
        mesh_coll = None
    arm = mesh_armature(mesh_coll) if mesh_coll is not None else None
    f = file_parts(raw)
    if arm is not None:
        joints, notes = check_keep(f, arm, mesh_coll, path)
    else:
        if not is_re4 and not f["flags"] & 4 and f["vertex"] is None:
            raise ImportProblem("its Kernel Flags lack Mesh Multi Joint Deform and it drives no mesh vertices the "
                                "editor reads")
        joints, notes = [], [NO_MESH]
    if not _contiguous(f["part_of"]):
        raise ImportProblem("a part's particles are not all together")
    r = gp.read(raw)
    bones = [b.name for b in arm.data.bones] if arm is not None else []
    by_hash = {gpuc_build.name_hash(b): b for b in bones}
    coll = generate.new_collection(file_stem(path), arm)
    head = props.header(coll)
    if arm is None:
        head.matrix_world = Matrix(np.linalg.inv(ROTATE_BACK).tolist())   # game space under it, as on an armature
    head[RAW_KEY] = base64.b64encode(raw).decode("ascii")
    head[SOURCE_KEY] = path
    set_asset_path(coll, path)
    head[JOINTS_KEY] = joints
    head.ree_gpuc_header.game = r["ext"]
    _set_header(head.ree_gpuc_header, r["header"], notes)
    cfg_objs = _set_configs(coll, arm, r["configs"], notes)
    if not is_re4:                                    # joints the armature lacks: named from the reference skeleton
        found, more = reference.resolve(coll, arm, _joint_hashes(f, r))
        by_hash.update(found)
        notes += more
    inv = np.linalg.inv(ROTATE_BACK @ np.array(arm.matrix_world, float)) if arm is not None else np.eye(4)
    for i, block in enumerate(r["parts"]):
        sel = np.where(f["part_of"] == i)[0]
        if not len(sel):
            notes.append(f"part block {i} has no particles: left out")
            continue
        first, n = int(sel[0]), len(sel)
        tri_keep = [k for k, t in enumerate(f["tris"]) if first <= t[0] < first + n]
        link_keep = [ln for ln in f["links"] if first <= ln[0] < first + n and first <= ln[1] < first + n]
        bone_name = (lambda b: bones[b]) if bones else (lambda b: f"#{b}")  # noqa: E731
        names = sorted({bone_name(b) for b in f["skin"][sel].ravel() if b >= 0})
        index = {b: k for k, b in enumerate(names)}
        data = {
            "locked": True, "count": n, "tile": len(props.parts(coll)),
            "co": (f["pos"][sel] @ inv[:3, :3].T + inv[:3, 3]).astype(np.float32),
            "normal": f["normal"][sel] @ inv[:3, :3].T,
            "tris": f["tris"][tri_keep] - first, "tri_uv": f["tri_uv"][tri_keep],
            "links": np.array([(a - first, b - first, t, _shape(t, a, b, f["pos"])) for a, b, t, _ in link_keep],
                              np.int64).reshape(-1, 4),
            "tails": np.array([gp.tail_values(tail) for *_, tail in link_keep], np.float32).reshape(-1, 4),
            "nodes": names,
            "skin": np.array([[index[bone_name(b)] if b >= 0 else 0 for b in row] for row in f["skin"][sel]],
                             np.int64),
            "skin_w": (f["skin_w"][sel] / 255.0).astype(np.float32),
            **{field: f["values"][field][sel] for field in canon.FIELD_NAMES}}
        obj = _new_locked_part(coll, arm, cfg_objs[min(block["config"], len(cfg_objs) - 1)], block, data)
        if is_re4:
            obj["ree_gpuc_re4_index"] = i
        _set_part(obj, block, r["tails"][i], None, cfg_objs, notes)
        obj[canon.EDITED_KEY] = []
        if block["use_root_joint"] and block["root_hash"] not in by_hash:
            obj[ROOT_HASH_KEY] = gp.signed(block["root_hash"])
    _keep_capsules(coll, arm, f["caps"], by_hash, notes)
    _keep_welds(coll, f, notes)                       # after the collisions: the chain editor's order
    if f.get("vertex"):
        J, S, V, L, _ = f["vertex"]
        notes.append(f"It drives {J} of {V} mesh vertices ({L} LODs) directly; the bindings are kept from the file")
    if is_re4:
        notes = ["RE4: the original skinning, output bindings and collisions are kept from the file and not editable",
                 "RE4 Config values are named by their offset in the file; their effects are unknown",
                 "RE4 UVs are generated for selection only; no UVs are written to the file"]
        _snapshot_re4(coll)
    return coll, list(dict.fromkeys(notes))


def _keep_welds(coll, f, notes) -> list:
    """Create one weld per pair of parts the file links, with the commonest tail and type; return the names of
    welds whose links differ. A Keep Structure cloth still exports each link's own tail until the weld is edited."""
    objs = by_name(props.parts(coll))
    start = {int(p): int(np.argmax(f["part_of"] == p)) for p in np.unique(f["part_of"])}
    groups = collections.defaultdict(list)
    for a, b, t, tail in f["links"]:
        pa, pb = int(f["part_of"][a]), int(f["part_of"][b])
        if pa != pb:
            groups[(pa, pb)].append((a - start[pa], b - start[pb], t, tail))
    mixed = []
    for (pa, pb), links in sorted(groups.items()):
        tails = collections.Counter(tail for *_, tail in links)
        types = collections.Counter(t for _, _, t, _ in links)
        obj = weld.new(coll, objs[pa], objs[pb], [(ia, ib) for ia, ib, _, _ in links],
                       gp.tail_values(tails.most_common(1)[0][0]), types.most_common(1)[0][0])
        if len(tails) > 1 or len(types) > 1:
            mixed.append(obj.name)
            if keeps_structure(coll):
                notes.append(f"{obj.name}: its links have {len(tails)} tails, {len(types)} types: the commonest "
                             f"shown, each written as the file has it until edited")
        notes.append(f"{obj.name}: {len(links)} welded particle pairs (links joining two parts)")
    return mixed


def _new_locked_part(coll, arm, config, block, data) -> bpy.types.Object:
    name = generate.unique_name("GPUC_PART_", "_" + (block["part_name"] or "part"))
    obj = bpy.data.objects.new(name, bpy.data.meshes.new(name))
    obj["TYPE"] = props.PART_TYPE
    coll.objects.link(obj)
    obj.hide_render = True
    obj.show_wire = True
    obj.show_in_front = bpy.context.scene.ree_gpuc.in_front
    p = obj.ree_gpuc_part
    p.armature, p.uv_tile = arm, data["tile"]
    p.config = config
    canon.fit_ranges(obj, data)
    canon.store(obj, data)
    canon.build_mesh(obj)
    return obj


def _keep_capsules(coll, arm, caps, by_hash, notes) -> None:
    missing = 0
    for ha, hb, oa, ra, ob, rb in caps:
        a, b = by_hash.get(ha, f"HASH_{ha}"), by_hash.get(hb, f"HASH_{hb}")
        missing += (ha not in by_hash) + (hb not in by_hash)
        collision.new_capsule(coll, arm, a, oa, ra, b, ob, rb, gp.signed(ha), gp.signed(hb))
    if missing:
        notes.append(f"{missing} capsule ends name joints the armature lacks: kept at its origin with their hashes")


# --- Rebuild from Bones -------------------------------------------------------------------------------------

def _recognise(f: dict, i: int, bones: list) -> tuple:
    """Return (layout, links {(a, b, type): tail}, ``grid.links``) for a file part the generator could make, else
    (None, reason, None). Particles skinned to a single bone are the bone nodes and must lie on a sub-lattice."""
    sel = np.where(f["part_of"] == i)[0]
    base, n = int(sel[0]), len(sel)
    mine = {(min(a, b) - base, max(a, b) - base, t): tail for a, b, t, tail in f["links"]
            if base <= a < base + n and base <= b < base + n}
    found = None
    for bend in (True, False):
        for closed in (False, True):
            for cols in range(2, n // 2 + 1):
                if n % cols == 0:
                    gl = grid.links(n // cols, cols, closed, bend)
                    if len(gl) == len(mine) and {(int(a), int(b), int(t)) for a, b, t, _ in gl} == set(mine):
                        found = (n // cols, cols, closed, bend, gl)
                        break
            if found:
                break
        if found:
            break
    if found is None:
        alone = int(((f["skin_w"][sel] > 0).sum(1) == 1).sum())
        why = (f"its {n} particles and their links form no rows-by-columns grid of the kind the editor makes from "
               f"bone chains")
        return None, why + ("" if alone else " (a free-form mesh: no particle follows one bone alone)"), None
    rows, cols, closed, bend, gl = found
    skin, sw = f["skin"][sel], f["skin_w"][sel]
    single = {k for k in range(n) if (sw[k] > 0).sum() == 1}
    span = cols if closed else cols - 1
    for sr in range(rows - 1):
        if (rows - 1) % (sr + 1):
            continue
        for sc in range(span):
            if span % (sc + 1):
                continue
            at = [(r, c) for r in range(0, rows, sr + 1) for c in range(0, cols, sc + 1)]
            if {r * cols + c for r, c in at} != single:
                continue
            names = [bones[int(skin[r * cols + c][int(np.argmax(sw[r * cols + c]))])] for r, c in at]
            if len(set(names)) != len(names):
                return None, "a bone sits on two of its particles", None
            lay = {"node_rows": (rows - 1) // (sr + 1) + 1, "node_cols": len(range(0, cols, sc + 1)),
                   "sub_rows": sr, "sub_cols": sc, "closed": closed, "nodes": names, "diagonal_bend": bend}
            return lay, mine, gl
    return None, "its particles skinned to one bone alone lie on no lattice of its grid", None


def _other_diagonal(f, sel, rows, cols, closed) -> bool:
    """Whether every quad of the part is split at the second-fourth corner diagonal; a mixed split returns False."""
    base, n = int(sel[0]), len(sel)
    mine = {tuple(sorted(int(v) - base for v in t)) for t in f["tris"] if base <= t[0] < base + n}
    quads = grid.faces(rows, cols, closed)
    other = {tuple(sorted(t)) for a, b, c, d in quads for t in ((a, b, d), (b, c, d))}
    return mine == other


def _joint_hashes(f, r) -> list:
    """Return the hashes of the file's capsule ends and part root joints."""
    return [h for c in f["caps"] for h in c[:2]] + [p["root_hash"] for p in r["parts"] if p["use_root_joint"]]


def _remove_cloth(coll) -> None:
    for obj in list(coll.all_objects):
        data = obj.data
        bpy.data.objects.remove(obj)
        if isinstance(data, bpy.types.Mesh) and not data.users:
            bpy.data.meshes.remove(data)
        elif isinstance(data, bpy.types.Armature) and not data.users:
            bpy.data.armatures.remove(data)
    for c in list(coll.children_recursive) + [coll]:
        bpy.data.collections.remove(c)


def _preset_of(part) -> str:
    """Return the link preset whose float32 tails match the Part's as written, else CUSTOM."""
    mine = [gp.tail_bytes(*getattr(part, s).tail()) for s in gp.SHAPES]
    for key, (_, _, tails) in props.LINK_PRESETS.items():
        f32 = [[float(np.float32(x)) for x in t] for t in tails]
        if mine == [gp.tail_bytes(k, k2, lo, hi) if k2 != 1.0 else gp.tail_bytes(k, 1.0, 1.0, 1.0)
                    for k, k2, lo, hi in f32]:
            return key
    return "CUSTOM"


def import_rebuild(path: str, mesh_coll, force: bool = False) -> tuple:
    """Import the file as Parts regenerated from the mesh's bones with the file's values; return (collection, notes).

    Each link shape takes its commonest tail, the other links keep theirs (``canon.set_overrides``), and the
    particles keep their offsets from the bones. The rebuild is kept only when its export matches the file's
    ``fingerprint``; otherwise the file is imported with Keep Structure and the first note starts "Not rebuilt (" (parsed by
    ``ops``). ``force`` keeps a mismatching rebuild, but a part that is no grid of bones still falls back."""
    raw = open(path, "rb").read()
    if path.endswith(re4.EXT):
        coll, notes = import_keep(path, mesh_coll)
        return coll, ["RE4 imported with Keep Structure; Rebuild from Bones is not supported"] + notes
    arm = mesh_armature(mesh_coll)
    f = file_parts(raw)
    if not _contiguous(f["part_of"]):
        raise ImportProblem("a part's particles are not all together")
    bones = [b.name for b in arm.data.bones]
    used = f["skin"][f["skin"] >= 0]
    if len(used) and used.max() >= len(bones):
        raise ImportProblem(f"its skinning names bone {int(used.max())}, the armature has {len(bones)}: pick the "
                            f"mesh this .gpuc belongs to")
    r = gp.read(raw)

    def keep(why):
        coll, notes = import_keep(path, mesh_coll)
        return coll, [f"Not rebuilt ({why}): imported with Keep Structure"] + notes

    found = []
    for i in range(len(r["parts"])):
        lay, links, gl = _recognise(f, i, bones)
        if lay is None:
            return keep(f"part {i} {r['parts'][i]['part_name']}: {links}")
        found.append((lay, links, gl))
    notes, changed = [], []
    by_hash = {gpuc_build.name_hash(b): b for b in bones}
    to_arm = np.linalg.inv(ROTATE_BACK @ np.array(arm.matrix_world, float))
    coll = generate.new_collection(file_stem(path), arm)
    head = props.header(coll)
    head[SOURCE_KEY] = path
    set_asset_path(coll, path)
    head.ree_gpuc_header.game = r["ext"]
    _set_header(head.ree_gpuc_header, r["header"], notes)
    if f["vertex"]:
        _rebuild_drive(head.ree_gpuc_header, f, path, notes)
    cfg_objs = _set_configs(coll, arm, r["configs"], notes)
    placed, more = reference.resolve(coll, arm, _joint_hashes(f, r))
    by_hash.update(placed)
    notes += more
    for i, (block, (lay, links, gl)) in enumerate(zip(r["parts"], found)):
        sel = np.where(f["part_of"] == i)[0]
        node_co = generate.heads(arm, lay["nodes"])
        lay["centre"] = grid.order_columns(node_co[:lay["node_cols"]], "CLOSED" if lay["closed"] else "OPEN")[2]
        lay.update(generate._particles(lay, node_co))
        obj = generate.new_part(coll, arm, lay, cfg_objs[min(block["config"], len(cfg_objs) - 1)])
        tails = {}
        for a, b, t, s in gl:
            tails.setdefault(gp.SHAPES[s], collections.Counter())[links[(int(a), int(b), int(t))]] += 1
        common = {s: gp.tail_values(c.most_common(1)[0][0]) for s, c in tails.items()}
        _set_part(obj, block, common, None, cfg_objs, notes)
        p = obj.ree_gpuc_part
        p.link_preset = _preset_of(p)
        p.other_diagonal = _other_diagonal(f, sel, lay["rows"], lay["cols"], lay["closed"])
        p.diagonal_bend = lay["diagonal_bend"]
        if block["use_root_joint"] and block["root_hash"] not in by_hash:
            obj[ROOT_HASH_KEY] = gp.signed(block["root_hash"])
        d = canon.load(obj)
        canon.fit_ranges(obj, {field: f["values"][field][sel] for field in canon.FIELD_NAMES})
        for field in canon.FIELD_NAMES:
            v = f["values"][field][sel]
            d[field] = v
        d["rest_offset"] = (f["pos"][sel] @ to_arm[:3, :3].T + to_arm[:3, 3]) - d["base_co"]   # the file's rest pose
        canon.store(obj, d)
        canon.write_groups(obj)
        far = float(np.linalg.norm(d["rest_offset"], axis=1).max())
        if far > 1e-3:
            notes.append(f"part {i}: particles kept up to {far * 1000:.1f} mm off their bones, as rest offsets")
        default = [common.get(s, (0.0, 1.0, 1.0, 1.0)) for s in gp.SHAPES]
        own = canon.set_overrides(obj, canon.load(obj), [gp.tail_values(links[(int(a), int(b), int(t))])
                                                         for a, b, t, _ in gl], default)
        if own:
            notes.append(f"part {i}: {own} links keep their own tails from the file (setting a link shape gives "
                         f"all its links that tail)")
    _keep_capsules(coll, arm, f["caps"], by_hash, notes)
    for name in _keep_welds(coll, f, notes):         # after the collisions: the chain editor's order
        changed.append(f"{name}: its links have more than one tail or type (the commonest taken)")
    for obj in weld.objects(coll):                   # a rebuilt weld's pairs are its Parts' shared bones'
        flat = list(obj[weld.PAIRS_KEY])
        if set(weld.pairs(obj)) != set(zip(flat[0::2], flat[1::2])):
            changed.append(f"{obj.name}: its particles are not the ones of the bones its Parts share")
    try:
        mine, _ = build_bytes(coll, mesh_coll)
        changed += differences(fingerprint(mine, bones, arm=arm), fingerprint(raw, bones, arm=arm))
    except ExportError as e:
        changed.append(f"the rebuilt cloth does not export: {e}")
    changed = list(dict.fromkeys(changed))
    if changed and not force:
        _remove_cloth(coll)
        return keep("; ".join(changed[:2]) + (f" and {len(changed) - 2} more" if len(changed) > 2 else ""))
    if changed:
        notes = [f"Force Rebuild changed: {c}" for c in changed] + notes
    return coll, list(dict.fromkeys(notes))


def _rebuild_drive(h, f, path, notes) -> None:
    """Set a rebuilt cloth to drive mesh vertices as the file does: its triangles per vertex, and a Drive Distance
    between its driven and other vertices of the .mesh beside it when one distance parts them."""
    J, S, V, L, out = f["vertex"]
    h.drive_mode = "VERTICES"
    h.drive_slots = min(max(S, 1), vertex_drive.MAX_SLOTS)
    mesh, why = companion_mesh(path)
    try:
        if not mesh:
            raise ExportError(why)
        pos, _, _ = mesh_file_vertices(mesh)
        if len(pos) != V:
            raise ExportError(f"it has {len(pos)} vertices, the .gpuc drives a mesh of {V}")
    except ExportError as e:
        notes.append(f"Drive Distance left at {h.drive_distance * 100:.1f} cm: {e}")
        return
    near = vertex_drive.distances(pos, vertex_drive.tri_frames(f["pos"], f["tris"])).min(1)
    drv = out >= 0
    inside, outside = near[drv].max(initial=0.0), near[~drv].min(initial=np.inf)
    if inside < outside:
        h.drive_distance = float(min((inside + outside) / 2, inside + 0.01))
    else:
        notes.append(f"No one Drive Distance gives the file's driven vertices ({int(drv.sum())}): its vertices "
                     f"{inside * 100:.2f} cm and {outside * 100:.2f} cm from the cloth differ in it; use the vertex "
                     f"groups GPUC_Drive / GPUC_NoDrive")
        h.drive_distance = float(inside + 1e-4)


# --- comparing structures -----------------------------------------------------------------------------------

def fingerprint(raw: bytes, bones=None, weighted=None, arm=None) -> dict:
    """Return the structure two .gpuc must share to count as the same; skin uses bone names when ``bones`` is
    given and driven joints use bound bones when ``arm`` is given, else indices."""
    f = file_parts(raw)

    def name(b):
        return bones[b] if bones and 0 <= b < len(bones) else f"#{b}"

    skin = [tuple(sorted((name(b), int(w)) for b, w in zip(row, ws) if w)) for row, ws in zip(f["skin"], f["skin_w"])]
    ids = joint_bones(f, arm)[0] if arm is not None else [j["palette"] for j in f["joints"]]
    joints = sorted((str(k), tuple(sorted((int(cp), round(float(w), 3)) for cp, w in j["drivers"])))
                    for k, j in zip(ids, f["joints"]))
    driven = tuple(np.flatnonzero(f["vertex"][4] >= 0).tolist()) if f.get("vertex") else None
    return {"parts": np.bincount(f["part_of"]).tolist(), "tris": {tuple(sorted(t)) for t in f["tris"].tolist()},
            "links": {(min(a, b), max(a, b), t) for a, b, t, _ in f["links"]}, "pos": f["pos"], "skin": skin,
            "joints": joints, "caps": [(ha, hb) for ha, hb, *_ in f["caps"]], "driven": driven}


def differences(a: dict, b: dict) -> list:
    """Describe how fingerprint ``b`` (the file) differs from ``a`` (the cloth), most basic first."""
    out = []
    if a["parts"] != b["parts"]:
        return [f"particles per part {a['parts']} / {b['parts']}"]
    if a["tris"] != b["tris"]:
        out.append(f"triangles: {len(b['tris'] - a['tris'])} more, {len(a['tris'] - b['tris'])} fewer")
    if a["links"] != b["links"]:
        out.append(f"links: {len(b['links'] - a['links'])} more, {len(a['links'] - b['links'])} fewer")
    far = float(np.linalg.norm(a["pos"] - b["pos"], axis=1).max()) if len(a["pos"]) else 0.0
    if far > 1e-3:
        out.append(f"rest positions off by up to {far * 1000:.1f} mm")
    skin = sum(x != y for x, y in zip(a["skin"], b["skin"]))
    if skin:
        out.append(f"skinning of {skin} particles differs")
    if a["joints"] != b["joints"]:
        out.append(f"driven joints differ ({len(a['joints'])} / {len(b['joints'])})")
    if a["caps"] != b["caps"]:
        out.append(f"own capsules differ ({len(a['caps'])} / {len(b['caps'])})")
    if a.get("driven") != b.get("driven"):
        count = lambda d: "joints" if d is None else f"{len(d)} vertices"  # noqa: E731
        out.append(f"driven mesh vertices differ ({count(a.get('driven'))} / {count(b.get('driven'))})")
    return out


# --- Update Existing ----------------------------------------------------------------------------------------

def update_existing(path: str, coll, mesh_coll=None) -> list:
    """Write the file's values onto a cloth with the same ``fingerprint``, else raise ``ImportProblem``; return notes.
    Link tails go per link on a locked Part and per shape on a generated one."""
    if path.endswith(re4.EXT) or props.is_re4(coll):
        raise ImportProblem("RE4 currently supports Keep Structure imports; import the file as a new collection")
    raw = open(path, "rb").read()
    game, mine_game = gp.ext_of(gpuc.Gpuc(raw).layout), props.header(coll).ree_gpuc_header.game
    if game != mine_game:
        raise ImportProblem(f"a {game_name(game)} file onto a {game_name(mine_game)} cloth")
    arm = mesh_armature(mesh_coll) if mesh_coll is not None else generate._armature(coll)
    if (arm is None or arm.type != "ARMATURE") and not keeps_structure(coll):
        raise ImportProblem("pick the mesh collection (the cloth has no armature)")
    bones = [b.name for b in arm.data.bones] if arm is not None and arm.type == "ARMATURE" else None
    mesh_coll = mesh_coll or (mesh_collection(arm) if bones else None)
    try:
        weighted = weighted_bones(arm, mesh_coll) if mesh_coll is not None else None
    except ExportError:
        weighted = None
    if keeps_structure(coll):
        mine = stored_file(coll)
    else:
        if weighted is None:
            raise ImportProblem("pick the mesh collection: comparing a cloth made from bones needs its weighted bones")
        try:
            mine, _ = build_bytes(coll, mesh_coll)
        except ExportError as e:
            raise ImportProblem(f"the cloth cannot be compared: {e}") from e
    diff = differences(fingerprint(mine, bones, weighted), fingerprint(raw, bones, weighted))
    if diff:
        more = f" (and {len(diff) - 2} more)" if len(diff) > 2 else ""
        raise ImportProblem("not the same structure: " + "; ".join(diff[:2]) + more)
    notes = []
    r, f = gp.read(raw), file_parts(raw)
    head = props.header(coll)
    _set_header(head.ree_gpuc_header, r["header"], notes)
    cfg_objs = _set_configs(coll, arm, r["configs"], notes)
    part_objs = by_name(props.parts(coll))
    for i, (obj, block) in enumerate(zip(part_objs, r["parts"])):
        sel = np.where(f["part_of"] == i)[0]
        _set_part(obj, block, r["tails"][i], None, cfg_objs, notes)
        d = canon.load(obj)
        p = obj.ree_gpuc_part
        canon.fit_ranges(obj, {field: f["values"][field][sel] for field in canon.FIELD_NAMES})
        for field in canon.FIELD_NAMES:
            v = f["values"][field][sel]
            d[field] = v
        if d["locked"]:
            first = int(sel[0])
            d["tails"] = np.array([gp.tail_values(t) for a, b, _, t in f["links"]
                                   if first <= a < first + len(sel) and first <= b < first + len(sel)],
                                  np.float32).reshape(-1, 4)
            obj[canon.EDITED_KEY] = []
        else:
            spread = [s for s, n in _tail_spread(f, sel).items() if n > 1]
            if spread:
                notes.append(f"{obj.name}: the file's {', '.join(spread)} links have more than one tail: the commonest "
                             f"taken")
        canon.store(obj, d)
        canon.write_groups(obj)
    roots = by_name(rt for rt in collision.roots(bpy.context.scene) if rt.name in coll.all_objects)
    for root, (_, _, oa, ra, ob, rb) in zip(roots, f["caps"]):
        c = root.ree_gpuc_capsule
        c.offset_a, c.radius_a, c.offset_b, c.radius_b = oa, ra, ob, rb
    _update_welds(coll, f, part_objs, notes)
    if keeps_structure(coll):
        head[RAW_KEY] = base64.b64encode(raw).decode("ascii")
    head[SOURCE_KEY] = path
    set_asset_path(coll, path)
    return list(dict.fromkeys(notes))


def _update_welds(coll, f, part_objs, notes) -> None:
    """Set each weld's tail and type to the commonest among its file links and clear its edited flag, so a Keep
    Structure cloth exports each link's own tail again."""
    start = {obj: int(np.argmax(f["part_of"] == k)) for k, obj in enumerate(part_objs)}
    by_pair = {(a, b): (t, tail) for a, b, t, tail in f["links"]}
    for obj in weld.objects(coll):
        w = obj.ree_gpuc_weld
        if w.part_a not in start or w.part_b not in start:
            continue
        found = [by_pair.get((start[w.part_a] + ia, start[w.part_b] + ib)) for ia, ib in weld.pairs(obj)]
        found = [x for x in found if x is not None]
        if not found:
            continue
        tails = collections.Counter(tail for _, tail in found)
        types = collections.Counter(t for t, _ in found)
        k, k2, lo, hi = gp.tail_values(tails.most_common(1)[0][0])
        w.k, w.k2, w.lo, w.hi, w.link_type = k, k2, lo, min(hi, props.HI_MAX), types.most_common(1)[0][0]
        if weld.EDITED_KEY in obj.keys():
            del obj[weld.EDITED_KEY]
        if (len(tails) > 1 or len(types) > 1) and not keeps_structure(coll):
            notes.append(f"{obj.name}: the file's links have more than one tail or type: the commonest taken")


def _tail_spread(f, sel) -> dict:
    """Count the distinct tails per link shape within a part."""
    first, n = int(sel[0]), len(sel)
    seen = collections.defaultdict(set)
    for a, b, t, tail in f["links"]:
        if first <= a < first + n and first <= b < first + n:      # not the welds joining it to another
            seen[grid.SHAPES[_shape(t, a, b, f["pos"])].lower()].add(tail)
    return {s: len(v) for s, v in seen.items()}

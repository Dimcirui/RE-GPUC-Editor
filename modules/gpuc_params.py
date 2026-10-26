"""Read a .gpuc's parameters and pack them into its blocks, without Blender, for MH Wilds (.gpuc.241111760), RE9
(.gpuc.250925365) and Onimusha (.gpuc.260209505). Names are the editor's properties (props.py); offsets are
Wilds'. RE9 and Onimusha differ in the header only (their 0xC0 headers: ``header_fields``); every block and record
below is the same in all three (particle 108, part 64, config 192 bytes).

  header       the editable values (props.HEADER_FIELDS; the counts and bounds are derived)
  config       section 8, 192 bytes each; its two curves (via.AnimationCurve, 0x28 bytes at +0x50 / +0x78) point
               at their keys in section 12 (via.KeyFrame, 16 bytes: f32 value, i16 type, f16 time, in tangent,
               in weight, out tangent, out weight)
  part         section 6, 64 bytes each; its name in section 22 (see names_section)
  link tails   per part, the most common tail of each link shape: type 0 links down the cloth (vertical) and
               round it (ring), told apart by direction (game space, y up); type 1 shear, type 2 bend
  particles    per part, the median of each particle value (section 0, 108 bytes each)"""
from __future__ import annotations

import collections
import struct

import numpy as np

from . import gpuc, gpuc_build, re4

EXT = ".241111760"                       # MH Wilds
RE9_EXT = ".250925365"                   # Resident Evil Requiem
ONI_EXT = ".260209505"                   # Onimusha: Way of the Sword
RE4_EXT = re4.EXT
EXTS = (EXT, RE9_EXT, ONI_EXT, RE4_EXT)
CONFIG_SIZE, PART_SIZE, KEY_SIZE, PARTICLE = 192, 64, 16, 108
CURVES = {"translation_curve": 0x50, "rotation_curve": 0x78}
HEADER = {"calculate_mode": (0x68, "I"), "max_velocity": (0x78, "f"), "kernel_flags": (0x8C, "i"),
          "unk04": (0x04, "i"), "unk40": (0x40, "i"), "unk54": (0x54, "i"), "unk5c": (0x5C, "i"),
          "unk60": (0x60, "i"), "friction": (0x6C, "f"), "unk70": (0x70, "i"), "unk74": (0x74, "i"),
          "unk7c": (0x7C, "i"), "unk80": (0x80, "i")}
# RE9 and Onimusha only: (offset, kind). 0x0C a second hash beside the first (0x08, Wilds' 0x04), 0x10 to 0x18 0,
# 0x4C a value that differs per file
RE9_ONLY = {"unk0c": (0x0C, "i"), "unk10": (0x10, "i"), "unk14": (0x14, "i"), "unk18": (0x18, "i"),
            "unk4c": (0x4C, "i")}
# the header's other values at their Wilds offsets: the counts (gpuc.WILDS_COUNTS) and these (StaticClothInfo's
# names). batch_infos counts section 7's entries, sim_lod_one section 13's; the game's files have interpolations,
# restrictions (blend infos) and the two self-collision contact desc counts 0, sim_deform_weights 1, and the
# generator writes none of them
HEADER_WILDS = {**{name: off for off, name in gpuc.WILDS_COUNTS.items()}, "batch_infos": 0x28, "configs": 0x2C,
                "sim_lods": 0x30, "sim_lod_one": 0x44, "interpolations": 0x48, "restrictions": 0x4C,
                "sim_deform_weights": 0x50, "point_triangle_contacts": 0x84, "edge_edge_contacts": 0x88,
                "bounds": 0x90}


def _re9_offset(off: int):
    """A Wilds header offset's place in the RE9 header, None for the one RE9 lacks (Wilds 0x60): 0x04 at 0x08,
    0x3C to 0x5C 20 bytes later (RE9 has its own value at 0x4C), the rest 16 bytes later."""
    if off == 0x04:
        return 0x08
    if off == 0x60:
        return None
    return off + 0x14 if 0x3C <= off <= 0x5C else off + 0x10


def _oni_offset(off: int):
    """A Wilds header offset's place in the Onimusha header, None for the one Onimusha lacks (Wilds 0x54): RE9's
    up to Wilds 0x50, then 16 bytes later from the joint table (0x58 -> 0x68) on, Wilds 0x60 included."""
    if off == 0x54:
        return None
    return off + 0x10 if off >= 0x58 else _re9_offset(off)


def _offset(ext: str, off: int):
    return off if ext == EXT else _oni_offset(off) if ext == ONI_EXT else _re9_offset(off)


def header_fields(ext: str) -> dict:
    """The editable header values of a game's file: {property: (offset, kind)}."""
    if ext == RE4_EXT:
        return {"max_velocity": (0x54, "f")}
    if ext == EXT:
        return dict(HEADER)
    out = {name: (_offset(ext, off), kind) for name, (off, kind) in HEADER.items() if _offset(ext, off) is not None}
    return {**out, **RE9_ONLY}


def offset_of(ext: str, name: str) -> int:
    """Where a game's header keeps a count or a written value (HEADER_WILDS' names)."""
    return _offset(ext, HEADER_WILDS[name])


def ext_of(layout) -> str:
    """The game's extension of a parsed file's layout; ValueError for a layout the editor does not take."""
    ext = f".{layout.ext}"
    if ext not in EXTS:
        raise ValueError(f"unsupported {layout.name} .gpuc (.{layout.ext}): only MH Wilds, RE9, Onimusha and RE4 "
                         f"files are supported")
    return ext


def ext_of_path(path: str) -> str:
    """A file's game by its extension ("" for neither)."""
    return next((e for e in EXTS if path.endswith(e)), "")


# "?" a u32 0 / 1, "i" a signed u32, "f" a float
CONFIG = {"unk00": (0x00, "i"), "unk04": (0x04, "i"), "unk08": (0x08, "i"), "unk0c": (0x0C, "i"),
          "unk10": (0x10, "i"), "unk14": (0x14, "i"), "unk18": (0x18, "i"), "unk1c": (0x1C, "i"),
          "self_collision": (0x20, "?"), "velocity_damping": (0x24, "f"), "second_damping": (0x28, "?"),
          "second_velocity_damping": (0x2C, "f"), "second_damping_speed": (0x30, "f"), "unk34": (0x34, "i"),
          "overwrite_translation": (0x38, "?"), "overwrite_rotation": (0x3C, "?"),
          "blend_translation": (0x40, "f"), "blend_rotation": (0x44, "f"), "translation_speed": (0x48, "f"),
          "rotation_speed": (0x4C, "f"), "use_speed_limit": (0xA0, "?"), "speed_limit": (0xA4, "f"),
          "use_angular_speed_limit": (0xA8, "?"), "angular_speed_limit": (0xAC, "f"),
          "simulation_strength": (0xB0, "f"), "blend_weight": (0xB4, "f"), "gravity_factor": (0xB8, "f"),
          "info_state": (0xBC, "i")}
CURVE = {"unk04": (0x04, "i"), "unk08": (0x08, "f"), "clamp_min": (0x0C, "f"), "clamp_max": (0x10, "f"),
         "unk14": (0x14, "i"), "clamp_flags": (0x18, "i"), "unk1c": (0x1C, "i")}
PART = {"clsp_flags_a": (0x10, "i"), "clsp_flags_b": (0x14, "i"), "friction": (0x20, "f"), "bounce": (0x24, "f"),
        "stickiness": (0x28, "f"), "surface_area": (0x2C, "f"), "plane_bits": (0x30, "i"),
        "sphere_bits": (0x34, "i"), "capsule_bits": (0x38, "i"), "obb_bits": (0x3C, "i")}
PARTICLE_VALUES = {"sim_weight": 0x00, "radius": 0x04, "inverse_mass": 0x08, "max_distance": 0x0C,
                   "space_limit_lower": 0x10, "space_limit_upper": 0x14, "backstop_radius": 0x18,
                   "backstop_offset": 0x1C}
SHAPES = ("vertical", "ring", "shear", "bend")
HI_SCALE = 4095.0


def signed(v: int) -> int:
    v &= 0xFFFFFFFF
    return v - (1 << 32) if v >= 1 << 31 else v


def _get(data, at, kind):
    if kind == "f":
        return struct.unpack_from("<f", data, at)[0]
    v = struct.unpack_from("<I", data, at)[0]
    return bool(v) if kind == "?" else signed(v) if kind == "i" else v


def _put(data, at, kind, value):
    if kind == "f":
        struct.pack_into("<f", data, at, float(value))
    else:
        struct.pack_into("<I", data, at, (int(bool(value)) if kind == "?" else int(value)) & 0xFFFFFFFF)


# --- reading ------------------------------------------------------------------------------------------------

def read_curve(raw, block, at) -> dict:
    """A config curve: its values and keys (time, value, type number, in tangent, in weight, out tangent, out
    weight)."""
    c = {name: _get(block, at + off, kind) for name, (off, kind) in CURVE.items()}
    n, ptr = struct.unpack_from("<I", block, at)[0], struct.unpack_from("<Q", block, at + 0x20)[0]
    keys = []
    for k in range(n if ptr else 0):
        v, typ = struct.unpack_from("<fh", raw, ptr + KEY_SIZE * k)
        t, it, iw, ot, ow = struct.unpack_from("<5e", raw, ptr + KEY_SIZE * k + 6)
        keys.append((float(t), float(v), int(typ), float(it), float(iw), float(ot), float(ow)))
    c["keys"] = keys
    return c


def tail_values(tail: bytes) -> tuple:
    k, k2, lo, hi = struct.unpack("<4H", tail)
    return k / 65535.0, k2 / 65535.0, lo / 65535.0, hi / HI_SCALE


def tail_bytes(k, k2, lo, hi) -> bytes:
    q = lambda x, s: max(0, min(65535, int(round(x * s))))  # noqa: E731
    return struct.pack("<4H", q(k, 65535.0), q(k2, 65535.0), q(lo, 65535.0), q(hi, HI_SCALE))


def read(raw: bytes) -> dict:
    """Every parameter of a .gpuc: {"ext", "header", "configs", "parts", "tails", "values"} (tails and values one
    per part); a part's root joint as its hash (the armature names it)."""
    if re4.matches(raw):
        return re4.File(raw).parameters()
    g = gpuc.Gpuc(raw)
    ext = ext_of(g.layout)
    header = {name: _get(raw, off, kind) for name, (off, kind) in header_fields(ext).items()}
    s8 = bytes(g.sections[8].data)
    configs = []
    for b in range(struct.unpack_from("<I", raw, offset_of(ext, "configs"))[0]):
        block = s8[b * CONFIG_SIZE:(b + 1) * CONFIG_SIZE]
        c = {name: _get(block, off, kind) for name, (off, kind) in CONFIG.items()}
        for name, at in CURVES.items():
            c[name] = read_curve(raw, block, at)
        configs.append(c)
    s6 = bytes(g.sections[6].data)
    parts = []
    for i in range(struct.unpack_from("<I", raw, offset_of(ext, "parts"))[0]):
        block = s6[i * PART_SIZE:(i + 1) * PART_SIZE]
        ptr = struct.unpack_from("<Q", block, 0)[0]
        end = ptr
        while end + 1 < len(raw) and raw[end:end + 2] != b"\0\0":
            end += 2
        p = {name: _get(block, off, kind) for name, (off, kind) in PART.items()}
        p.update(part_name=raw[ptr:end].decode("utf-16-le", "replace") if ptr else "",
                 config=struct.unpack_from("<I", block, 0x0C)[0],
                 use_root_joint=bool(struct.unpack_from("<I", block, 0x18)[0]),
                 root_hash=struct.unpack_from("<I", block, 0x1C)[0])
        parts.append(p)
    n = g.counts()["particles"]
    p0 = np.frombuffer(bytes(g.sections[0].data)[:n * PARTICLE], np.uint8).reshape(n, PARTICLE)
    part_of = p0[:, 0x28:0x2C].copy().view("<u4")[:, 0]
    floats = p0[:, :0x20].copy().view("<f4")
    pos = p0[:, 0x48:0x54].copy().view("<f4")
    values = [{name: float(np.median(floats[part_of == i, off // 4])) if (part_of == i).any() else None
               for name, off in PARTICLE_VALUES.items()} for i in range(len(parts))]
    return {"ext": ext, "header": header, "configs": configs, "parts": parts, "values": values,
            "tails": _tails(g, pos, part_of, len(parts))}


def _tails(g, pos, part_of, n_parts) -> list:
    L, T = bytes(g.sections[2].data), bytes(g.sections[29].data)
    count = collections.defaultdict(collections.Counter)
    for k in range(g.counts()["links"]):
        a, b = struct.unpack_from("<HH", L, k * 16)
        typ = struct.unpack_from("<I", T, k * 4)[0]
        shape = {1: "shear", 2: "bend"}.get(typ)
        if typ == 0:
            way = gpuc_build.direction(pos[a], pos[b])
            shape = "vertical" if way == "vert" else "ring" if way == "horiz" else None
        if shape is not None:
            count[(int(part_of[a]), shape)][L[k * 16 + 8:k * 16 + 16]] += 1
    return [{s: tail_values(count[(i, s)].most_common(1)[0][0]) for s in SHAPES if count[(i, s)]}
            for i in range(n_parts)]


# --- writing ------------------------------------------------------------------------------------------------

def config_block(values: dict, curves: dict, key_base: int, key_start: int) -> tuple[bytes, bytes]:
    """A config block and its curves' keys: curves {name: {curve values, "keys"}}; the keys are laid out from
    key_start (index into section 12) and pointed at as key_base + their offset (the file offset of section
    12, as gpuc.Gpuc.build() expects)."""
    block, keys = bytearray(CONFIG_SIZE), bytearray()
    for name, (off, kind) in CONFIG.items():
        _put(block, off, kind, values[name])
    for name, at in CURVES.items():
        c = curves[name]
        for field, (off, kind) in CURVE.items():
            _put(block, at + off, kind, c[field])
        struct.pack_into("<I", block, at, len(c["keys"]))
        struct.pack_into("<Q", block, at + 0x20, key_base + key_start + len(keys))
        for t, v, typ, it, iw, ot, ow in c["keys"]:
            keys += struct.pack("<fh5e", v, typ, t, it, iw, ot, ow)
    return bytes(block), bytes(keys)


def part_block(values: dict, config: int, name: str, root_joint: str | None) -> bytes:
    """A part block without its name pointer (gpuc.Gpuc's section 6 refs carry it)."""
    block = bytearray(PART_SIZE)
    struct.pack_into("<II", block, 0x08, gpuc_build.name_hash(name), config)
    for field, (off, kind) in PART.items():
        _put(block, off, kind, values[field])
    struct.pack_into("<II", block, 0x18, int(bool(root_joint)), gpuc_build.name_hash(root_joint) if root_joint else 0)
    return bytes(block)


def names_section(names) -> tuple[bytes, list[int]]:
    """Section 22 and each name's offset in it: the names UTF-16, null-terminated, back to back, the whole padded
    to 16 bytes."""
    out, at = bytearray(), []
    for n in names:
        at.append(len(out))
        out += n.encode("utf-16-le") + b"\0\0"
    return bytes(out + b"\0" * (-len(out) % 16)), at


def write_header(header: bytearray, values: dict, ext: str = EXT) -> None:
    """The game's editable header values (the ones values lacks left as they are)."""
    for name, (off, kind) in header_fields(ext).items():
        if name in values:
            _put(header, off, kind, values[name])

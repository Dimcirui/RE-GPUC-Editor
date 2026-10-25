"""RE4 221108868: validated, fixed-offset reading and preservation-only editing.

Disk Config fields are deliberately named by offset; GPU ConfigInfo is a different
structure. Never relocate sections or reinterpret unknown bytes on export.
"""
import collections
import math
import struct

import numpy as np

EXT = ".221108868"
PARTICLE = 100
COUNTS = {4: "particles", 8: "triangles", 12: "links", 16: "edges",
          20: "triangle_groups", 24: "link_groups", 28: "edge_groups",
          32: "parts", 36: "batch_infos", 40: "configs", 44: "outputs"}
PARTICLE_VALUES = {"sim_weight": 0, "radius": 4, "inverse_mass": 8,
                   "max_distance": 12, "backstop_radius": 16}
CONFIG = {**{f"re4_value_{o:02x}": (o, "f") for o in range(0x24, 0x3C, 4)},
          "re4_flags_20": (0x20, "i"), "re4_flags_3c": (0x3C, "i")}
PART = {"clsp_flags_a": (0x10, "i"), "clsp_flags_b": (0x14, "i"),
        "friction": (0x24, "f"), "bounce": (0x28, "f"), "stickiness": (0x2C, "f"),
        "re4_value_30": (0x30, "f"),
        "plane_bits": (0x34, "i"), "sphere_bits": (0x38, "i"), "capsule_bits": (0x3C, "i")}


def matches(raw):
    if len(raw) < 0x124 or raw[:4] != b"GCLO":
        return False
    return (struct.unpack_from("<I", raw, 0x30)[0] == 0x120
            and all(not o or 0x120 <= o < len(raw) for o in struct.unpack_from("<18Q", raw, 0x90)))


class File:
    def __init__(self, raw):
        if not matches(raw):
            raise ValueError("not a supported RE4 GPUC layout")
        self.raw = bytes(raw)
        self.header = bytearray(raw[:0x90])
        self.offsets = struct.unpack_from("<18Q", raw, 0x90)
        self.c = {n: struct.unpack_from("<I", raw, o)[0] for o, n in COUNTS.items()}
        for slot, count, stride in [(0, "particles", 100), (1, "triangles", 8),
                                    (2, "links", 16), (3, "edges", 4),
                                    (4, "triangle_groups", 4), (5, "link_groups", 4),
                                    (6, "edge_groups", 4), (7, "parts", 64),
                                    (8, "batch_infos", 80), (9, "configs", 64), (15, "links", 4)]:
            self.span(self.offsets[slot], self.c[count] * stride)
        if not self.c["parts"] or not self.c["configs"]:
            raise ValueError("RE4 file has no Parts or Configs")
        for slot, key in ((7, "parts"), (9, "configs")):
            for i in range(self.c[key]):
                self.name_at(self.offsets[slot] + 64 * i)
        self.flags = struct.unpack_from("<I", raw, 0x6C)[0]
        if self.flags not in (0, 4):
            raise ValueError(f"unverified RE4 output layout flags 0x{self.flags:X}")
        self.span(self.offsets[10], self.c["outputs"] * (452 if self.flags & 4 else 328))
        for slot, key, total in ((4, "triangle_groups", "triangles"),
                                 (5, "link_groups", "links"), (6, "edge_groups", "edges")):
            if sum(struct.unpack_from(f"<{self.c[key]}I", raw, self.offsets[slot])) != self.c[total]:
                raise ValueError(f"RE4 {key} do not sum to {total}")

    def span(self, at, size):
        if at < 0x120 or size < 0 or at + size > len(self.raw):
            raise ValueError(f"RE4 span {at:#x}+{size:#x} outside file")

    def name_at(self, at):
        ptr = struct.unpack_from("<Q", self.raw, at)[0]
        if not ptr:
            return ""
        self.span(ptr, 2)
        end = ptr
        while end + 2 <= len(self.raw) and self.raw[end:end + 2] != b"\0\0":
            end += 2
        if end + 2 > len(self.raw):
            raise ValueError("unterminated RE4 name")
        return self.raw[ptr:end].decode("utf-16-le")

    def get(self, at, fmt="f"):
        return struct.unpack_from("<" + fmt, self.raw, at)[0]

    def build(self):
        return bytes(self.header) + self.raw[0x90:]

    def parameters(self):
        from . import gpuc_params as gp
        f = self.geometry()
        configs = [{k: self.get(self.offsets[9] + i * 64 + o, fmt) for k, (o, fmt) in CONFIG.items()}
                   for i in range(self.c["configs"])]
        parts = []
        for i in range(self.c["parts"]):
            at = self.offsets[7] + i * 64
            p = {k: self.get(at + o, fmt) for k, (o, fmt) in PART.items()}
            p.update(part_name=self.name_at(at), config=self.get(at + 12, "I"),
                     use_root_joint=bool(self.get(at + 24, "Q")), root_hash=self.get(at + 32, "I"),
                     root_name=self.name_at(at + 24))
            if p["config"] >= len(configs):
                raise ValueError("RE4 Part Config index outside Config table")
            parts.append(p)
        tails = []
        for i in range(len(parts)):
            seen = collections.defaultdict(collections.Counter)
            for a, b, typ, tail in f["links"]:
                if f["part_of"][a] != i or f["part_of"][b] != i:
                    continue
                shape = {1: "shear", 2: "bend"}.get(typ)
                if typ == 0:
                    from .gpuc_build import direction
                    shape = {"vert": "vertical", "horiz": "ring"}.get(direction(f["pos"][a], f["pos"][b]))
                if shape:
                    seen[shape][tail] += 1
            tails.append({s: gp.tail_values(c.most_common(1)[0][0]) for s, c in seen.items()})
        return {"ext": EXT, "header": {"max_velocity": self.get(0x54)}, "configs": configs,
                "parts": parts, "tails": tails,
                "values": [{k: float(np.median(v[f["part_of"] == i])) if (f["part_of"] == i).any() else None
                            for k, v in f["values"].items()} for i in range(len(parts))]}

    def geometry(self):
        n, nt, nl = (self.c[k] for k in ("particles", "triangles", "links"))
        p = np.frombuffer(self.raw, np.uint8, n * 100, self.offsets[0]).reshape(n, 100)
        def column(at, kind="<f4"):
            return p[:, at:at + 4].copy().view(kind).ravel()
        pos = p[:, 64:76].copy().view("<f4")
        part = column(32, "<u4").astype(np.int64)
        if not np.isfinite(pos).all() or (part >= self.c["parts"]).any():
            raise ValueError("invalid RE4 particle positions or Part indices")
        tri = np.frombuffer(self.raw, "<u2", nt * 4, self.offsets[1]).reshape(nt, 4)[:, :3].astype(np.int64)
        types = np.frombuffer(self.raw, "<u4", nl, self.offsets[15])
        links = []
        for k in range(nl):
            at = self.offsets[2] + k * 16
            a, b = struct.unpack_from("<HH", self.raw, at)
            links.append((a, b, int(types[k]), self.raw[at + 8:at + 16]))
        if (tri >= n).any() or any(max(a, b) >= n or t > 2 for a, b, t, _ in links):
            raise ValueError("invalid RE4 topology indices or link types")
        q = column(76, "<u4")
        normal = np.column_stack([(q >> s & 1023) / 1023 * 2 - 1 for s in (0, 10, 20)])
        # Shape-only import: keep actual skin and output bindings in the source bytes.
        # No bone-index reinterpretation or synthetic posing.
        values = {k: column(o) for k, o in PARTICLE_VALUES.items()}
        values.update({k: np.zeros(n, np.float32) for k in ("space_limit_lower", "space_limit_upper", "backstop_offset")})
        # A unique selection grid per Part, not an interpretation of absent disk UVs.
        uv = np.zeros((n, 2), float)
        for i in range(self.c["parts"]):
            sel = np.flatnonzero(part == i)
            width = max(2, int(math.ceil(math.sqrt(len(sel)))))
            seq = np.arange(len(sel))
            uv[sel, 0] = (seq % width) / (width - 1)
            uv[sel, 1] = (seq // width) / max(1, int(math.ceil(len(sel) / width)) - 1)
        return {"game": EXT, "count": n, "pos": pos.astype(float), "part_of": part,
                "normal": normal, "skin": np.full((n, 1), -1, np.int64),
                "skin_w": np.zeros((n, 1), float), "tris": tri,
                "tri_uv": uv[tri],
                "links": links, "joints": [], "caps": [], "frames": None,
                "flags": self.flags, "values": values}


def patch(raw, header=None, configs=None, parts=None, particles=None, link_tails=None):
    """Fixed-size scalar edits only. Unknown bytes and every offset stay identical."""
    f = File(raw)
    out = bytearray(raw)
    def write(at, fmt, value):
        if fmt == "f" and not math.isfinite(float(value)):
            raise ValueError("RE4 edited values must be finite")
        struct.pack_into("<" + fmt, out, at, value)
    for name, value in (header or {}).items():
        if name != "max_velocity":
            raise ValueError(f"RE4 header field is not editable: {name}")
        write(0x54, "f", value)
    for records, fields, slot, key in ((configs, CONFIG, 9, "configs"), (parts, PART, 7, "parts")):
        if records is None:
            continue
        if len(records) != f.c[key]:
            raise ValueError(f"RE4 cannot change {key} count")
        for i, values in enumerate(records):
            for name, value in values.items():
                if name not in fields:
                    raise ValueError(f"RE4 field is not editable: {name}")
                off, fmt = fields[name]
                write(f.offsets[slot] + i * 64 + off, fmt, value)
    for i, values in (particles or {}).items():
        if not 0 <= i < f.c["particles"]:
            raise ValueError("RE4 particle index outside file")
        for name, value in values.items():
            if name not in PARTICLE_VALUES:
                raise ValueError(f"RE4 particle field is not present: {name}")
            write(f.offsets[0] + i * 100 + PARTICLE_VALUES[name], "f", value)
    for i, tail in (link_tails or {}).items():
        if not 0 <= i < f.c["links"] or len(tail) != 8:
            raise ValueError("invalid RE4 link edit")
        at = f.offsets[2] + i * 16 + 8
        out[at:at + 8] = tail
    return bytes(out)

"""Structural reader/writer for RE Engine .gpuc in three layouts:
  MH Wilds          .gpuc.241111760   header 0xB0
  RE9 (Requiem)     .gpuc.250925365   header 0xC0
  Onimusha WotS     .gpuc.260209505   header 0xC0, table slots 18..28 = sections 19..29

File layout:
  0x000  header (magic GCLO; counts, bounds), size per layout
  +hdr   30 x u64 section offsets (0 = absent)
  +0xF0  sections, laid out in a fixed order; several entries may share an offset (empty ones)

Sections are addressed by their Wilds/RE9 table index in every layout ("section index"), so code
written against Wilds indices (11 bone table, 23 weighted bones, 25 bindings, 26 frames, ...)
reads RE9 and Onimusha files unchanged. Onimusha drops one empty slot: its table slot i holds
section i for i < 18 and section i + 1 for 18 <= i <= 28; slot 29 is always 0. Section 18 (empty
in nearly all Wilds and RE9 files) has no slot there.

Record sizes are the same in all three: particle 108, triangle 20, link 16, edge 8, config 192,
key frame 16, frame (sec 26) 48 per particle, sec 28 24 per particle, sec 29 4 per link.

Cross references besides the table:
  - one header pointer (Wilds 0x08, RE9 / Onimusha 0x04), normally to section 26
    (Onimusha files that drive vertices directly, with sections 25 / 26 empty: to section 23)
  - section 6 +0, normally to the start of section 22
  - particle +0x5C / +0x64: skin bones (indices into section 11) and u8 weights; the index encoding
    differs by layout, see read_skin / write_skin
  - section 8 (192-byte configs) +0x70 / +0x98: via.AnimationCurve key pointers into section 12
    (16-byte via.KeyFrame: f32 value, u16 curve type, f16 time, f16 in/out tangent)
The header and section 6 pointers are resolved when parsing to (section, distance into it) and
rewritten from that on build.

A Section keeps its raw bytes including the padding that followed it in the source file, so an
unmodified parse/build round-trips byte for byte. When a section's bytes change size, build()
re-lays out every later section, keeping each start's original alignment (16 or 4); empty
sections take the start of the next section that carries data, as in the source files.

Setting g.layout to another layout before build() writes the sections in that layout's table;
the header bytes are written as they are (replace g.header with one of the target layout).

CLI:
  python -I gpuc.py check <files...>            round-trip every file, report mismatches
  python -I gpuc.py info <file>                 print layout, header counts and section table
"""
import struct
import sys

TABLE_COUNT = 30
# Wilds values, kept for older scripts that read them; parsing uses the file's Layout
HEADER_SIZE = 0xB0
TABLE_OFFSET = 0xB0
DATA_START = TABLE_OFFSET + TABLE_COUNT * 8

CONFIG_SECTION, CONFIG_BLOCK, CURVE_KEY_PTRS, CURVE_KEY_SECTION = 8, 192, (0x70, 0x98), 12
SEC6_PTR = (6, 0x00)
CAPSULE_SECTION = 16
PART_ENTRY = 64  # section 6: one entry per part, its name pointer (into section 22) at +0x00

# counts: header offset in Wilds; RE9 and Onimusha have them 0x10 later, except where listed
WILDS_COUNTS = {
    0x0C: "particles", 0x10: "triangles", 0x14: "links", 0x18: "edges",
    0x1C: "link_groups", 0x20: "edge_groups", 0x24: "parts", 0x34: "joints", 0x38: "joint_slots",
    0x58: "joint_table", 0x64: "capsules",
}


class Layout:
    def __init__(self, name, ext, header_size, slots, header_ptr, counts):
        self.name = name
        self.ext = ext
        self.header_size = header_size
        self.table_offset = header_size
        self.data_start = header_size + TABLE_COUNT * 8
        self.slots = slots                                  # table slot -> section index (None = unused)
        self.slot_of = {s: i for i, s in enumerate(slots) if s is not None}
        self.header_ptr = header_ptr
        self.counts = counts                                # header offset -> name

    def __repr__(self):
        return f"Layout({self.name})"


def _shifted(extra):
    c = {o + 0x10: n for o, n in WILDS_COUNTS.items() if n not in extra}
    c.update({o: n for n, o in extra.items()})
    return c


WILDS = Layout("wilds", 241111760, 0xB0, list(range(30)), 0x08, WILDS_COUNTS)
RE9 = Layout("re9", 250925365, 0xC0, list(range(30)), 0x04,
             _shifted({"joint_table": 0x6C, "capsules": 0x74}))
ONIMUSHA = Layout("onimusha", 260209505, 0xC0, list(range(18)) + list(range(19, 30)) + [None], 0x04,
                  _shifted({"joint_table": 0x68, "capsules": 0x74}))
try:
    from . import re4
except ImportError:  # direct CLI: python -I modules/gpuc.py
    import os
    sys.path.insert(0, os.path.dirname(__file__))
    import re4

RE4 = Layout("re4", 221108868, 0x90, list(range(18)), 0x30, re4.COUNTS)
LAYOUTS = {l.ext: l for l in (WILDS, RE9, ONIMUSHA, RE4)}


def detect(raw):
    """Layout from the bytes alone (load() prefers the file extension)."""
    if re4.matches(raw):
        return RE4
    first = min(o for o in (struct.unpack_from("<Q", raw, 0xB0 + 8 * i)[0] for i in range(TABLE_COUNT)) if o)
    if first == WILDS.data_start:
        return WILDS
    slot = [struct.unpack_from("<Q", raw, 0xC0 + 8 * i)[0] for i in range(TABLE_COUNT)]
    # Onimusha: slot 29 unused, and slot 21 (section 22, never empty) has its own start
    if slot[29] == 0 and slot[21] and slot[21] != slot[22]:
        return ONIMUSHA
    return RE9


class Section:
    def __init__(self, index, offset, data, align):
        self.index = index
        self.offset = offset
        self.data = data
        self.align = align

    def __repr__(self):
        return f"Section({self.index}, off=0x{self.offset:X}, size={len(self.data)}, align={self.align})"


class Gpuc:
    def __init__(self, raw, layout=None):
        if raw[:4] != b"GCLO":
            raise ValueError("not a gpuc file")
        if len(raw) < 0x1B0:
            raise ValueError("empty gpuc (%d bytes)" % len(raw))
        L = self.layout = layout or detect(raw)
        if L is RE4:
            f = re4.File(raw)
            self.raw, self.header = f.raw, f.header
            self.sections, self.order, self.refs = {}, [], {}
            return
        self.header = bytearray(raw[:L.header_size])
        slot_off = [struct.unpack_from("<Q", raw, L.table_offset + 8 * i)[0] for i in range(TABLE_COUNT)]
        offsets = {}
        for i, o in enumerate(slot_off):
            if o and L.slots[i] is None:
                raise ValueError(f"{L.name}: table slot {i} is set but unused in this layout")
            if L.slots[i] is not None:
                offsets[L.slots[i]] = o
        self.present = [bool(offsets.get(s)) for s in range(TABLE_COUNT)]
        # Order: by offset; among equal offsets keep section order (empty entries come first,
        # the last one at an offset carries the data).
        order = sorted((o, s) for s, o in offsets.items() if o)
        if order[0][0] != L.data_start:
            raise ValueError("first section at 0x%X, expected 0x%X" % (order[0][0], L.data_start))
        starts = sorted(set(o for o, _ in order)) + [len(raw)]
        owner = {o: s for o, s in order}                    # last section at each start
        # except the capsules: with a count, section 16 holds its start even when empty sections 19 and 20
        # share it after it (ch04_000_0070, ch04_205_0000); it goes last among them, as an owner does
        caps = next(o for o, n in L.counts.items() if n == "capsules")
        if offsets.get(CAPSULE_SECTION) and struct.unpack_from("<I", self.header, caps)[0]:
            owner[offsets[CAPSULE_SECTION]] = CAPSULE_SECTION
            order.sort(key=lambda x: (x[0], owner[x[0]] == x[1], x[1]))
        self.sections = {}
        self.order = []
        for o, s in order:
            end = starts[starts.index(o) + 1] if owner[o] == s else o
            align = 16 if o % 16 == 0 else 4
            self.sections[s] = Section(s, o, bytearray(raw[o:end]), align)
            self.order.append(s)
        self.tail = b""

        def resolve(v):
            if not starts[0] <= v < len(raw):
                raise ValueError(f"cross reference 0x{v:X} outside the section data")
            start = max(x for x in starts[:-1] if x <= v)
            return owner[start], v - start

        self.refs = {"header": resolve(struct.unpack_from("<I", self.header, L.header_ptr)[0])}
        sec, off = SEC6_PTR
        if sec in self.sections:
            # one name pointer per 64-byte part entry (multi-part files have one per part)
            d = self.sections[sec].data
            self.refs["sec6"] = [resolve(struct.unpack_from("<Q", d, e + off)[0])
                                 for e in range(0, len(d) - PART_ENTRY + 1, PART_ENTRY)]

    # ---- header helpers -------------------------------------------------------------------
    def u32(self, off):
        return struct.unpack_from("<I", self.header, off)[0]

    def counts(self):
        return {name: self.u32(off) for off, name in self.layout.counts.items()}

    def set_capsules(self, data):
        """Section 16's bytes (the caller sets the header count). Where the file has no section 16 it is made as in
        vanilla files that have one (Wilds, RE9, Onimusha alike): 16-aligned, after sections 19, 20 and any of 14 / 15,
        with the empty section 18 after it. Empty data takes the section out: vanilla files without capsules have
        none."""
        if not data:
            if CAPSULE_SECTION in self.sections:
                del self.sections[CAPSULE_SECTION]
                self.order.remove(CAPSULE_SECTION)
                self.present[CAPSULE_SECTION] = False
            return
        if CAPSULE_SECTION not in self.sections:
            if 18 in self.order:
                self.order.remove(18)
            after = [k for k, s in enumerate(self.order) if s in (14, 15, 19, 20)]
            at = after[-1] + 1 if after else self.order.index(21)
            self.order[at:at] = [CAPSULE_SECTION] + ([18] if 18 in self.sections else [])
            self.sections[CAPSULE_SECTION] = Section(CAPSULE_SECTION, 0, bytearray(), 16)
            self.present[CAPSULE_SECTION] = True
        self.sections[CAPSULE_SECTION].data = bytearray(data)

    # ---- writer --------------------------------------------------------------------------
    def build(self):
        L = self.layout
        if L is RE4:
            if len(self.header) != 0x90:
                raise ValueError("RE4 header size cannot change")
            return bytes(self.header) + self.raw[0x90:]
        for s in self.order:
            if s not in L.slot_of and self.sections[s].data:
                raise ValueError(f"section {s} has data but no table slot in layout {L.name}")
        if len(self.header) != L.header_size:
            raise ValueError(f"header is 0x{len(self.header):X} bytes, layout {L.name} needs 0x{L.header_size:X}")
        out = bytearray(self.header) + bytearray(TABLE_COUNT * 8)
        new_off = {}
        pending = []
        for s in self.order:
            sec = self.sections[s]
            if not sec.data:
                pending.append(s)  # empty: shares the start of the next section with data
                continue
            out += b"\0" * ((-len(out)) % sec.align)
            for e in pending + [s]:
                new_off[e] = len(out)
            pending = []
            out += sec.data
        for e in pending:
            new_off[e] = len(out)
        for s, slot in L.slot_of.items():
            struct.pack_into("<Q", out, L.table_offset + 8 * slot, new_off.get(s, 0) if self.present[s] else 0)
        target, delta = self.refs["header"]
        struct.pack_into("<I", out, L.header_ptr, new_off[target] + delta)
        for k, (target, delta) in enumerate(self.refs.get("sec6", [])):
            struct.pack_into("<Q", out, new_off[SEC6_PTR[0]] + k * PART_ENTRY + SEC6_PTR[1], new_off[target] + delta)
        # curve key pointers: keep each pointer's distance into section 12
        cfg, keys = self.sections.get(CONFIG_SECTION), self.sections.get(CURVE_KEY_SECTION)
        if cfg is not None and keys is not None:
            for b in range(len(cfg.data) // CONFIG_BLOCK):
                for p in CURVE_KEY_PTRS:
                    pos = b * CONFIG_BLOCK + p
                    old = struct.unpack_from("<Q", cfg.data, pos)[0]
                    if old == 0:
                        continue  # no curve (some mod files)
                    if not keys.offset <= old <= keys.offset + len(keys.data):
                        raise ValueError(f"config {b} curve pointer +0x{p:X} = 0x{old:X} outside section 12")
                    struct.pack_into("<Q", out, new_off[CONFIG_SECTION] + pos,
                                     new_off[CURVE_KEY_SECTION] + old - keys.offset)
        return bytes(out)


PARTICLE = 108
SKIN_INDEX, SKIN_WEIGHT = 0x5C, 0x64   # particle record: skin bone indices, then u8 weights (/255)


def skin_slots(layout):
    """Skin influences per particle: Wilds and Onimusha 6 (indices packed 10 bits, three per u32,
    the game's CS_SkinningControlPoint), RE9 8 (one u8 each)."""
    return 8 if layout is RE9 else 6


def read_skin(raw, i, layout):
    """Particle i's skin influences as (indices into section 11, u8 weights), skin_slots(layout) each."""
    o = i * PARTICLE
    n = skin_slots(layout)
    w = list(raw[o + SKIN_WEIGHT:o + SKIN_WEIGHT + n])
    if layout is RE9:
        return list(raw[o + SKIN_INDEX:o + SKIN_INDEX + 8]), w
    a, b = struct.unpack_from("<II", raw, o + SKIN_INDEX)
    return [(v >> s) & 1023 for v in (a, b) for s in (0, 10, 20)], w


def write_skin(rec, off, idx, w, layout):
    """Write skin influences into a particle record at off (8 index bytes + 8 weight bytes).
    Influences with weight 0 are dropped; at most skin_slots(layout) may remain."""
    pairs = [(int(k), int(x)) for k, x in zip(idx, w) if x]
    n = skin_slots(layout)
    if len(pairs) > n:
        raise ValueError(f"{len(pairs)} skin influences, {layout.name} takes {n}")
    if any(not 0 <= k < (256 if layout is RE9 else 1024) for k, _ in pairs):
        raise ValueError(f"skin index out of range: {pairs}")
    pairs += [(0, 0)] * (8 - len(pairs))
    ks, ws = [k for k, _ in pairs], [x for _, x in pairs]
    if layout is RE9:
        struct.pack_into("<8B", rec, off + SKIN_INDEX, *ks)
    else:
        struct.pack_into("<II", rec, off + SKIN_INDEX, ks[0] | ks[1] << 10 | ks[2] << 20, ks[3] | ks[4] << 10 | ks[5] << 20)
    struct.pack_into("<8B", rec, off + SKIN_WEIGHT, *ws)


def load(path, layout=None):
    raw = open(path, "rb").read()
    if layout is None:
        ext = path.rsplit(".", 1)[-1]
        layout = LAYOUTS.get(int(ext)) if ext.isdigit() else None
    return Gpuc(raw, layout)


def _check(paths):
    bad = 0
    ok = 0
    for p in paths:
        raw = open(p, "rb").read()
        try:
            g = load(p)
        except ValueError as e:
            print(f"skip {p}: {e}")
            continue
        if detect(raw) is not g.layout:
            print(f"layout by extension {g.layout.name}, by bytes {detect(raw).name}: {p}")
        if g.build() != raw:
            bad += 1
            print("MISMATCH", p)
        else:
            ok += 1
    print(f"round-trip identical: {ok}, mismatched: {bad}")
    return bad == 0


def _info(path):
    g = load(path)
    print(g.layout.name, g.counts(), "refs", g.refs)
    for s in g.order:
        print(f"slot {g.layout.slot_of[s]:2d}", g.sections[s])


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "check":
        sys.exit(0 if _check(sys.argv[2:]) else 1)
    elif len(sys.argv) == 3 and sys.argv[1] == "info":
        _info(sys.argv[2])
    else:
        print(__doc__)

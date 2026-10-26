"""Build a single-part .gpuc from cloth geometry, using a template gpuc: MH Wilds .gpuc.241111760, or RE9
.gpuc.250925365 from a RE9 template (the file takes the template's layout: its header's places, its skin encoding).

What is generated (rules verified on the 89 vanilla files):
  particles, triangles (with per-vertex UV), links and their types, link/edge graph colouring,
  edges and the per-particle CSR, particle frames, output joint bindings and frames, skinning
  table (sec 11), output joint table (sec 23), count sections 7/10/13, header counts and bounds.
What is copied from the template:
  config (sec 8) and its curves (sec 12), part table (sec 6/22), built-in collision capsules
  (sec 16), sec 20, the per-particle material fields and sec 28 (from the template particle
  nearest in rest position), link tails (most common tail of each link type).
Without a template (a layout instead, see blank()) none of these: the editor writes config, parts and capsules
itself, and no shader reads sections 10, 14, 15, 20 or 28 (the game's cloth shader, checked 2026-10-25).

The spec is a dict (see ClothSpec below); positions are in mesh model space.
"""
import collections
import math
import struct

import numpy as np

from . import gpuc  # ree-gpuc-generator: vendored as a package (the one change to this file)

NAN = struct.unpack("<f", b"\xff\xff\xff\xff")[0]
MIN_SLOTS = 8  # particle slots per output joint, see build()
# sections that follow the one before unpadded in vanilla files (Wilds and RE9: their starts are 4-aligned unless the
# one before happens to end on 16); gpuc.Gpuc reads a start on 16 as 16-aligned, so a built file used as the next
# template would pad them
PACKED = (3, 4, 5, 19, 20)


class ClothSpec:
    """Input geometry.

    positions   (P, 3) rest positions
    sim_weight  (P,)   0 = follows skinning, 1 = fully simulated
    skin_bones  (P, 8) full mesh bone indices (any value where the weight is 0)
    skin_w      (P, 8) u8 weights, each row sums to 255; at most 6 non-zero (Wilds packs 6 skin
                indices of 10 bits, see gpuc.write_skin)
    faces       list of faces in file order; a face is a tuple of 3 or 4 particle indices.
                A quad (a, b, c, d) is split into (a, b, c) and (a, c, d).
    face_uv     list of per-face UVs, one (u, v) per face vertex, in [0, 1]
    outputs     list of output joints in mesh bone order:
                dict(palette=index in the mesh's weighted bone list, world=4x4 row-major
                bind matrix (translation in row 3), particles=[ids], weights=[floats])
    """

    def __init__(self, positions, sim_weight, skin_bones, skin_w, faces, face_uv, outputs):
        self.positions = np.asarray(positions, float)
        self.sim_weight = np.asarray(sim_weight, float)
        self.skin_bones = np.asarray(skin_bones, int)
        self.skin_w = np.asarray(skin_w, int)
        self.faces = [tuple(f) for f in faces]
        self.face_uv = face_uv
        self.outputs = outputs


def _triangles(spec):
    # tail: u16 tangent flags (bits 0-2 flip the tangent at that corner, a mirrored UV; bit 3 no UV tangent, the
    # face normal only; read by RecalculateControlPointNormals_Tangent alone), then three unorm16 UVs
    tris = []  # (i, j, k, uv tail bytes)
    for f, uv in zip(spec.faces, spec.face_uv):
        q = [max(0, min(65535, int(round(c * 65535)))) for p in uv for c in p]
        if len(f) == 3:
            tail = struct.pack("<H6H", 0, *q)
            tris.append((f[0], f[1], f[2], tail))
        else:
            # first triangle (a, b, c); the second copies the first one's tail
            tail = struct.pack("<H6H", 0, *(q[0:2] + q[2:4] + q[4:6]))
            tris.append((f[0], f[1], f[2], tail))
            tris.append((f[0], f[2], f[3], tail))
    return tris


def _relations(faces):
    """Pair -> relation: 'Q' quad/triangle edge, 'diag' quad diagonal, 'bend' two steps along edges."""
    rel = {}
    for f in faces:
        n = len(f)
        for k in range(n):
            rel[frozenset((f[k], f[(k + 1) % n]))] = "Q"
    for f in faces:
        if len(f) == 4:
            for e in (frozenset((f[0], f[2])), frozenset((f[1], f[3]))):
                rel.setdefault(e, "diag")
    nb = collections.defaultdict(set)
    for e, r in rel.items():
        if r == "Q":
            a, b = tuple(e)
            nb[a].add(b)
            nb[b].add(a)
    for a in sorted(nb):
        for k in sorted(nb[a]):
            for b in sorted(nb[k]):
                if b != a and b not in nb[a]:
                    rel.setdefault(frozenset((a, b)), "bend")
    return rel


# link types per relation: structural 0, shear 1, bend 2; a quad diagonal carries both 1 and 2
LINK_TYPES = {"Q": (0,), "diag": (1, 2), "bend": (2,)}


def _links(spec):
    rel = _relations(spec.faces)
    return [(tuple(sorted(e)), typ, r) for e, r in sorted(rel.items(), key=lambda x: tuple(sorted(x[0])))
            for typ in LINK_TYPES[r]]


def _colour(items, key=lambda x: x):
    """Greedy colouring: returns items reordered batch by batch and the batch sizes."""
    batches = []  # list of (used particle set, items)
    for it in items:
        p = key(it)
        for used, members in batches:
            if p[0] not in used and p[1] not in used:
                used.update(p)
                members.append(it)
                break
        else:
            batches.append(({p[0], p[1]}, [it]))
    batches.sort(key=lambda b: -len(b[1]))
    return [it for _, members in batches for it in members], [len(members) for _, members in batches]


def validate_geometry(pos, triangles):
    """Reject invalid rest geometry before constructing normals and particle frames."""
    pos = np.asarray(pos, float)
    if not np.isfinite(pos).all():
        raise ValueError("particle positions contain non-finite values")
    for a, b, c in triangles:
        ab, ac = pos[b] - pos[a], pos[c] - pos[a]
        scale = max(float(np.linalg.norm(ab)), float(np.linalg.norm(ac)))
        if scale <= 1e-12 or np.linalg.norm(np.cross(ab, ac)) <= max(1e-20, scale * scale * 1e-8):
            raise ValueError(f"degenerate rest triangle at particles {a}, {b}, {c}")


def _frames(pos, tris, tangent):
    validate_geometry(pos, [(a, b, c) for a, b, c, _ in tris])
    P = len(pos)
    nsum = np.zeros((P, 3))
    for i, j, k, _ in tris:
        n = np.cross(pos[j] - pos[i], pos[k] - pos[i])
        n /= np.linalg.norm(n)
        for a in (i, j, k):
            nsum[a] += n
    A = np.zeros((P, 3, 3))
    normals = np.zeros((P, 3))
    for i in range(P):
        length = np.linalg.norm(nsum[i])
        if length < 1e-10:
            raise ValueError(f"cannot construct rest normal for particle {i}")
        n = nsum[i] / length
        e = pos[tangent[i]] - pos[i]
        e -= n * (n @ e)
        length = np.linalg.norm(e)
        if length < 1e-12:
            raise ValueError(f"cannot construct rest tangent for particle {i}")
        y = -e / length
        A[i] = np.stack([np.cross(y, n), y, n])  # rows = local axes (world -> local)
        normals[i] = n
    return A, normals


def _pack_normal(n):
    q = [int(math.floor((c + 1) / 2 * 1023)) for c in n]
    q = [max(0, min(1023, v)) for v in q]
    return q[0] | q[1] << 10 | q[2] << 20


def name_hash(name):
    """RE Engine joint name hash: murmur3 x86_32 of the UTF-16LE name, seed 0xFFFFFFFF."""
    data = name.encode("utf-16-le")
    h, c1, c2 = 0xFFFFFFFF, 0xCC9E2D51, 0x1B873593
    n = len(data) // 4 * 4
    for i in range(0, n, 4):
        k = int.from_bytes(data[i:i + 4], "little")
        k = (k * c1) & 0xFFFFFFFF
        k = ((k << 15) | (k >> 17)) & 0xFFFFFFFF
        k = (k * c2) & 0xFFFFFFFF
        h ^= k
        h = ((h << 13) | (h >> 19)) & 0xFFFFFFFF
        h = (h * 5 + 0xE6546B64) & 0xFFFFFFFF
    k = 0
    tail = data[n:]
    for i in range(len(tail) - 1, -1, -1):
        k = (k << 8) | tail[i]
    if tail:
        k = (k * c1) & 0xFFFFFFFF
        k = ((k << 15) | (k >> 17)) & 0xFFFFFFFF
        k = (k * c2) & 0xFFFFFFFF
        h ^= k
    h ^= len(data)
    h ^= h >> 16
    h = (h * 0x85EBCA6B) & 0xFFFFFFFF
    h ^= h >> 13
    h = (h * 0xC2B2AE35) & 0xFFFFFFFF
    h ^= h >> 16
    return h


def capsule_bytes(capsules):
    """Section 16 records: (joint, pair joint, offset0 in joint space, r0, offset1 in pair space, r1)."""
    return b"".join(struct.pack("<II8x3ff3ff", name_hash(j), name_hash(p), *o0, r0, *o1, r1)
                    for j, p, o0, r0, o1, r1 in capsules)


def _pad16(b):
    return bytes(b) + b"\0" * (-len(b) % 16)


# a blank file's section 28 record (no shader reads the section): the commonest of the vanilla Wilds files
SEC28 = bytes.fromhex("cdcccc3d0000403f0000403fec115f3fec115f3fec115f3f")


def blank(layout):
    """A file of layout with every section empty, in the vanilla order (sections 9, 14, 15, 17 absent; 16 is added
    by set_capsules; Onimusha has no slot for 18): what build() starts from without a template."""
    g = gpuc.Gpuc.__new__(gpuc.Gpuc)
    g.layout, g.header = layout, bytearray(layout.header_size)
    g.header[:4] = b"GCLO"
    order = [6, 7, 8, 10, 11, 12, 13, 0, 2, 1, 3, 4, 5, 19, 20] + ([18] if 18 in layout.slot_of else []) \
        + list(range(21, 30))
    g.sections = {s: gpuc.Section(s, 0, bytearray(), 4 if s in PACKED else 16) for s in order}
    g.order, g.present = order, [s in g.sections for s in range(gpuc.TABLE_COUNT)]
    g.refs, g.tail = {"header": (26, 0), "sec6": []}, b""
    return g


def build(spec, template, skin_table, capsules=None, particle_radius=None, link_tail=None, welds=None):
    """template: a gpuc.Gpuc, or a gpuc.Layout to start from blank(layout) (zero particle fields, SEC28 for every
    particle, link tails ffffffffffffff0f where link_tail gives none, no config, part or capsule).
    skin_table: list of full mesh bone indices referenced by spec.skin_bones (sec 11).
    capsules: optional list for section 16 (see capsule_bytes); default copies the template's.
    particle_radius: optional collision radius for every particle (particle +0x04).
    link_tail: optional function (type, a, b) -> 8-byte link tail or None; None falls back to the
    template's most common tail for (type, relation).
    welds: optional list of (a, b, type, 8-byte tail): zero-length links after the faces' (vanilla joins
    two parts' coincident particles with them, type 4), written a to b as given, in any order."""
    from_blank = not isinstance(template, gpuc.Gpuc)
    t = blank(template) if from_blank else template
    tp = _template_particles(t)
    pos = spec.positions
    P = len(pos)
    tris = _triangles(spec)
    # links, coloured
    weld_tail = {(a, b, typ): tail for a, b, typ, tail in welds or ()}
    link_order, link_batches = _colour(_links(spec) + [((a, b), typ, "weld") for a, b, typ in sorted(weld_tail)],
                                       key=lambda x: x[0])
    # edges = unique triangle edges, coloured; CSR in edge-file order
    edge_set = set()
    for i, j, k, _ in tris:
        edge_set |= {tuple(sorted((i, j))), tuple(sorted((j, k))), tuple(sorted((i, k)))}
    edge_order, edge_batches = _colour(sorted(edge_set))
    adj = collections.defaultdict(list)
    for a, b in edge_order:
        adj[a].append(b)
        adj[b].append(a)
    start, s = {}, 0
    for i in range(P):
        start[i] = s
        s += len(adj[i])
    tangent = [adj[i][0] for i in range(P)]
    A, normals = _frames(pos, tris, tangent)

    # template material fields per particle: nearest template particle (a blank one: zeros, SEC28)
    if not from_blank:
        tpos = np.array([p["pos"] for p in tp])
        near = [int(np.argmin(np.linalg.norm(tpos - p, axis=1))) for p in pos]
        traw = bytes(t.sections[0].data)
        t28 = bytes(t.sections[28].data)
    else:
        near, traw, t28 = [0] * P, bytes(108), SEC28

    skin_index = {b: k for k, b in enumerate(skin_table)}
    sec0 = bytearray()
    sec28 = bytearray()
    for i in range(P):
        r = bytearray(traw[near[i] * 108:near[i] * 108 + 108])
        struct.pack_into("<f", r, 0x00, float(spec.sim_weight[i]))
        if particle_radius is not None:
            struct.pack_into("<f", r, 0x04, float(particle_radius))
        struct.pack_into("<II", r, 0x20, start[i], len(adj[i]))
        struct.pack_into("<II", r, 0x28, 0, 0)
        struct.pack_into("<ff4I", r, 0x30, NAN, NAN, 0, 0, 0, 0)   # controlPointIndices none, initialDistances
        struct.pack_into("<3f", r, 0x48, *pos[i])
        struct.pack_into("<II", r, 0x54, _pack_normal(normals[i]), tangent[i])
        idx = [skin_index[b] if w else 0 for b, w in zip(spec.skin_bones[i], spec.skin_w[i])]
        gpuc.write_skin(r, 0, idx, spec.skin_w[i], t.layout)
        sec0 += r
        sec28 += t28[near[i] * 24:near[i] * 24 + 24]

    sec1 = b"".join(struct.pack("<3H", i, j, k) + tail for i, j, k, tail in tris)

    tails = _template_link_tails(t)
    sec2 = bytearray()
    sec29 = bytearray()
    for (a, b), typ, r in link_order:
        if r == "weld":
            sec2 += struct.pack("<HHf", a, b, 0.0) + weld_tail[(a, b, typ)]
            sec29 += struct.pack("<I", typ)
            continue
        tail = link_tail(typ, a, b) if link_tail else None
        sec2 += struct.pack("<HHf", a, b, float(np.linalg.norm(pos[a] - pos[b]))) + (tail or tails[(typ, r)])
        sec29 += struct.pack("<I", typ)

    sec3 = b"".join(struct.pack("<4H", a, b, start[a] + adj[a].index(b), start[b] + adj[b].index(a))
                    for a, b in edge_order)

    # particle frames: [A | -A p]
    sec26 = b"".join(np.hstack([A[i], (-A[i] @ pos[i])[:, None]]).astype("<f4").tobytes() for i in range(P))

    # output joints, one or more particle slots each
    # Slots per joint (header +0x38). Always 8: a gpuc with fewer slots crashes the CPU solver
    # (verified: Hope, ch03_001_0002, with 1 slot per joint crashes when switched to CPU mode, 8 slots does not).
    S = max(MIN_SLOTS, max(len(o["particles"]) for o in spec.outputs))
    sec24 = bytearray()
    sec25 = bytearray()
    for o in spec.outputs:
        W = np.asarray(o["world"], float)
        R, tb = W[:3, :3], W[3, :3]
        for k in range(S):
            if k < len(o["particles"]):
                pk = o["particles"][k]
                sec24 += struct.pack("<if", pk, float(o["weights"][k]))
                L = R @ A[pk].T
                off = A[pk] @ (tb - pos[pk])
                sec25 += np.concatenate([L.reshape(9), off]).astype("<f4").tobytes()
            else:
                sec24 += struct.pack("<if", -1, 0.0)
                sec25 += b"\0" * 48
    sec23 = b"".join(struct.pack("<I", o["palette"]) for o in spec.outputs)
    sec11 = b"".join(struct.pack("<I", b) for b in skin_table)

    T, L, E, J = len(tris), len(link_order), len(edge_order), len(spec.outputs)
    nb1, nb2 = len(link_batches), len(edge_batches)
    sec4 = b"".join(struct.pack("<I", n) for n in link_batches)
    sec5 = b"".join(struct.pack("<I", n) for n in edge_batches)
    # sec 7 one BatchInfo: 9 counts (particles, triangles, links, edges, link / edge groups, deform infos, point-
    # triangle / edge-edge contact descs), the same 9 offsets, 2 padding; sec 10 a CPU-side tally no shader reads;
    # sec 13 one SimulationLodInfo: (offset, count) of particles, triangles, links, edges and the two contact descs,
    # 2 padding, deform infos, interpolation / restriction offsets, batch info offset / count (0 runs no constraint)
    sec7 = struct.pack("<20I", P, T, L, E, nb1, nb2, J, *([0] * 13))
    sec10 = struct.pack("<24I", 0, P, T, L, E, 0, J, *([0] * 17))
    sec13 = struct.pack("<20I", 0, P, 0, T, 0, L, 0, E, 0, 0, 0, 0, 0, 0, 0, J, 0, 0, 0, 1)

    new = {0: sec0, 1: sec1, 2: sec2, 3: sec3, 4: sec4, 5: sec5, 7: sec7, 10: sec10, 11: sec11, 13: sec13,
           23: sec23, 24: sec24, 25: sec25, 26: sec26, 28: sec28, 29: sec29}
    if capsules is not None:
        new[16] = capsule_bytes(capsules)
    g = t if from_blank else gpuc.Gpuc(t.build(), t.layout)    # a blank file does not parse (nothing in it)
    for i in PACKED:
        if i in g.sections:
            g.sections[i].align = 4
    if 16 in new:
        g.set_capsules(new.pop(16))
    for i, data in new.items():
        g.sections[i].data = bytearray(_pad16(data) if g.sections[i].align == 16 else data)
    h = g.header
    at = {name: off for off, name in t.layout.counts.items()}
    struct.pack_into("<7I", h, at["particles"], P, T, L, E, nb1, nb2, 1)
    struct.pack_into("<2I", h, at["joints"], J, S)
    struct.pack_into("<I", h, at["joint_table"], len(skin_table))
    if capsules is not None:
        struct.pack_into("<I", h, at["capsules"], len(capsules))
        # part +0x38 is the capsule mask over the frame's collider table (own capsules first, then
        # clsp); Hope's 0xFFFFFFE0 drops the first 5, so the built-in capsules would not collide
        s6 = g.sections[6].data
        for e in range(0, len(s6) - gpuc.PART_ENTRY + 1, gpuc.PART_ENTRY):
            struct.pack_into("<I", s6, e + 0x38, 0xFFFFFFFF)
    lo, hi = pos.min(0) - 1.0, pos.max(0) + 1.0
    struct.pack_into("<4f4f", h, t.layout.header_size - 0x20, *lo, 0.0, *hi, 0.0)      # the bounds end the header
    return g.build()


def _template_particles(t):
    P = t.counts()["particles"]
    raw = bytes(t.sections[0].data)
    return [dict(pos=struct.unpack_from("<3f", raw, i * 108 + 0x48)) for i in range(P)]


def template_faces(t):
    """Faces of a gpuc: consecutive triangles (a, b, c), (a, c, d) with the same tail form a quad."""
    n = t.counts()["triangles"]
    T = bytes(t.sections[1].data)
    recs = [(struct.unpack_from("<3H", T, i * 20), T[i * 20 + 6:i * 20 + 20]) for i in range(n)]
    faces, uvs, i = [], [], 0
    while i < n:
        tr, tail = recs[i]
        q = struct.unpack_from("<6H", tail, 2)
        uv = [(q[0] / 65535, q[1] / 65535), (q[2] / 65535, q[3] / 65535), (q[4] / 65535, q[5] / 65535)]
        nxt = recs[i + 1][0] if i + 1 < n else None
        if nxt and recs[i + 1][1] == tail and nxt[0] == tr[0] and nxt[1] == tr[2]:
            faces.append((tr[0], tr[1], tr[2], nxt[2]))
            uvs.append(uv + [uv[2]])
            i += 2
        else:
            faces.append(tr)
            uvs.append(uv)
            i += 1
    return faces, uvs


def link_tails_by_direction(t, vertical_axis=1):
    """Most common link tail per (type, direction) in a template: 'vert' (|d.y| > 0.7),
    'horiz' (|d.y| < 0.3) or 'slant'. Vanilla skirts keep vertical links stiff and make the
    links around the body soft (100_1005: type 0 vertical 1.0, around the body 0.3)."""
    n = t.counts()["links"]
    L, S = bytes(t.sections[2].data), bytes(t.sections[29].data)
    pos = np.array([p["pos"] for p in _template_particles(t)])
    c = collections.defaultdict(collections.Counter)
    for k in range(n):
        a, b = struct.unpack_from("<HH", L, k * 16)
        c[(S[k * 4], direction(pos[a], pos[b], vertical_axis))][L[k * 16 + 8:k * 16 + 16]] += 1
    return {key: cnt.most_common(1)[0][0] for key, cnt in c.items()}


def copy_config(g, src):
    """Copy every config value with a known file offset (gpuc_apply_preset.CONFIG_OFFSETS) from
    the first config block of src into the first block of g (both gpuc.Gpuc); returns the changes."""
    import gpuc_apply_preset
    s8 = bytes(src.sections[8].data)
    changed = []
    for name, (off, fmt) in gpuc_apply_preset.CONFIG_OFFSETS.items():
        old, new = struct.unpack_from(fmt, g.sections[8].data, off)[0], struct.unpack_from(fmt, s8, off)[0]
        struct.pack_into(fmt, g.sections[8].data, off, new)
        if old != new:
            changed.append(f"{name}: {old:.3g} -> {new:.3g}")
    return changed


def direction(p, q, vertical_axis=1):
    d = np.asarray(q, float) - np.asarray(p, float)
    v = abs(d[vertical_axis]) / np.linalg.norm(d)
    return "vert" if v > 0.7 else "horiz" if v < 0.3 else "slant"


def _template_link_tails(t):
    """Most common link tail per (type, relation) in the template."""
    n = t.counts()["links"]
    L, S = bytes(t.sections[2].data), bytes(t.sections[29].data)
    rel = _relations(template_faces(t)[0])
    c = collections.defaultdict(collections.Counter)
    by_type = collections.defaultdict(collections.Counter)
    for k in range(n):
        a, b = struct.unpack_from("<HH", L, k * 16)
        tail = L[k * 16 + 8:k * 16 + 16]
        c[(S[k * 4], rel.get(frozenset((a, b)), "other"))][tail] += 1
        by_type[S[k * 4]][tail] += 1
    tails = {}
    for typ in (0, 1, 2):
        default = by_type[typ].most_common(1)[0][0] if by_type[typ] else bytes.fromhex("ffffffffffffff0f")
        for r in ("Q", "diag", "bend"):
            tails[(typ, r)] = c[(typ, r)].most_common(1)[0][0] if c[(typ, r)] else default
    return tails

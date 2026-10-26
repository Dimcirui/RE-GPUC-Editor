"""Write a complete RE4 .gpuc.221108868 from cloth geometry (layout: docs/re4-structure.md).

``write(m)`` serializes a semantic model (the dict ``build`` makes): counts, offsets, the batch table, name hashes,
bounds, the thread group size and the string pool are derived. Rebuilt from the 111 game files' own models it gives
every byte before the string pool back.
``build(...)`` makes that model from the generator's geometry, with the new games' rules where RE4's own choices
are unknown (tangent neighbour, bend links, batch split): the game reads what is stored.

Maintenance constraints:
- Particles, and each batch's triangles, links and edges, are stored batch by batch; a batch holds whole Parts
  (welded Parts together), and every record of it names only its particles.
- Skin indices and 452-layout joints are places in the mesh's ``boneRemapList``; the header keeps its length.
- A 452 frame's row 2 is the unit edge to the particle's tangent neighbour (+0x50), row 1 the normal made
  perpendicular to it, row 0 their cross product; the stored tangent and the frame must agree.
"""
import struct

import numpy as np

from . import gpuc_build, vertex_drive
from .gpuc_build import name_hash

EXT = ".221108868"
ORDER = (8, 7, 9, 0, 10, 2, 1, 3, 4, 5, 6, 16, 17, 11, 12, 13, 14, 15)
ALIGN16 = {10, 2, 1, 3, 11, 12, 13, 14, 15}
BATCH_LIMIT = 512                 # particles a batch takes before the next Part starts another (game files: <= 448)
SLOTS_452, SLOTS_328 = 8, 16
# section 14 per particle (6 floats, effect unknown): the game files' commonest
S14 = struct.pack("<6f", 0.1, 0.75, 0.5, 0.8714, 0.8714, 0.8714)
IDENTITY_34 = struct.pack("<12f", 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0)


def write(m) -> bytes:
    P, T, L, E = len(m["pos"]), len(m["tris"]), len(m["links"]), len(m["edges"])
    flags = m["flags"]
    stride = 452 if flags & 4 else 328
    O = len(m["outputs"]) // stride
    col = m["colliders"]
    npl, nsp, ncap = (len(col[s]) for s in (11, 12, 13))
    names = []                    # the string pool: first-reference order, each once

    def ref(s):
        if s is not None and s not in names:
            names.append(s)
    for pt in m["parts"]:
        ref(pt["name"])
        ref(pt["root"])
    for cf in m["configs"]:
        ref(cf["name"])
    for s in (11, 12, 13):
        for a, b, _ in col[s]:
            ref(a)
            ref(b)
    bs = np.asarray(m["batch_sizes"], int).reshape(-1, 9)
    offs = np.vstack([np.zeros((1, 9), int), np.cumsum(bs, 0)[:-1]])
    sec = {8: b"".join(struct.pack("<20I", *bs[b, :7], m["batch_f07"][b], *bs[b, 7:9], *offs[b, :7],
                                   m["batch_out_end"][b], *offs[b, 7:9]) for b in range(len(bs)))}
    s0 = bytearray()
    for i in range(P):
        r = bytearray(100)
        r[0:0x14] = bytes(m["pvalues"][i])
        part = int(m["part_of"][i])
        struct.pack_into("<II", r, 0x20, part, m["parts"][part]["config"])
        struct.pack_into("<ii", r, 0x28, -1, -1)
        struct.pack_into("<3f", r, 0x40, *m["pos"][i])
        struct.pack_into("<II", r, 0x4C, int(m["normal_packed"][i]), int(m["tangent"][i]))
        r[0x54:0x5C] = bytes(m["skin_idx"][i])
        r[0x5C:0x64] = bytes(m["skin_w"][i])
        s0 += r
    sec[0] = s0
    sec[10] = m["outputs"]
    sec[2] = b"".join(struct.pack("<HHf", int(a), int(b), float(rest)) + bytes(tail)
                      for (a, b), rest, tail in zip(m["links"], m["link_rest"], m["link_tail"]))
    sec[1] = b"".join(struct.pack("<4H", *map(int, t), 0) for t in m["tris"])
    sec[3] = np.asarray(m["edges"], "<u2").reshape(-1, 2).tobytes()
    for s, key in ((4, "tri_groups"), (5, "link_groups"), (6, "edge_groups")):
        sec[s] = struct.pack(f"<{len(m[key])}I", *m[key])
    sec[16], sec[17], sec[14] = m["s16"], m["s17"], m["s14"]
    sec[15] = np.asarray(m["link_type"], "<u4").tobytes()
    sizes = {7: 64 * len(m["parts"]), 9: 64 * len(m["configs"]), 11: 48 * npl, 12: 48 * nsp, 13: 64 * ncap}
    at, off = 0x124, [0] * 18
    for s in ORDER:
        n = sizes[s] if s in sizes else len(sec[s])
        if s in ALIGN16:
            at = (at + 15) // 16 * 16
        off[s] = 0 if (n == 0 and s in (11, 12, 13)) else at
        at += n
    at = (at + 15) // 16 * 16
    ptr = {}
    for s in names:
        ptr[s] = at
        at += 2 * len(s) + 2
    pointer = lambda s: ptr[s] if s is not None else 0  # noqa: E731
    hashed = lambda s: name_hash(s) if s is not None else 0  # noqa: E731
    sec[7] = b"".join(struct.pack("<QII", pointer(pt["name"]), name_hash(pt["name"]), pt["config"]) + pt["body10"]
                      + struct.pack("<QI", pointer(pt["root"]), hashed(pt["root"])) + pt["body24"]
                      for pt in m["parts"])
    sec[9] = b"".join(struct.pack("<QI", pointer(cf["name"]), name_hash(cf["name"])) + cf["body0c"]
                      for cf in m["configs"])
    for s in (11, 12, 13):
        sec[s] = b"".join(struct.pack("<QQII8x", pointer(a), pointer(b), hashed(a), hashed(b)) + pay
                          for a, b, pay in col[s])
    out = bytearray(at)
    for s in ORDER:
        out[off[s]:off[s] + len(sec[s])] = sec[s]
    for s, p in ptr.items():
        out[p:p + 2 * len(s)] = s.encode("utf-16-le")
    pos = np.asarray(m["pos"], float)
    lo, hi = pos.min(0) - 1, pos.max(0) + 1
    tg = max(256, 1 << (int(bs[:, 0].max()) - 1).bit_length())
    struct.pack_into("<4s11I", out, 0, b"GCLO", P, T, L, E, len(m["tri_groups"]), len(m["link_groups"]),
                     len(m["edge_groups"]), len(m["parts"]), len(bs), len(m["configs"]), O)
    struct.pack_into("<6I", out, 0x30, 0x120, m["h34"], m["h38"], npl | nsp << 16, ncap, m["h44"])
    struct.pack_into("<f3I", out, 0x54, m["max_velocity"], m["remap_len"] | m["h58_hi"] << 16, tg, m["h60"])
    struct.pack_into("<3I", out, 0x64, len(m["s16"]) // 4, len(m["s17"]) // 8, flags)
    struct.pack_into("<4f4f", out, 0x70, *lo, 0.0, *hi, 0.0)
    struct.pack_into("<18Q", out, 0x90, *off)
    struct.pack_into("<I", out, 0x120, 0 if flags & 4 else O)
    return bytes(out)


def _colour_items(items, members):
    """Greedy colouring of items (each a tuple of particles): items reordered group by group, and the group sizes,
    the largest first (no particle twice in a group)."""
    groups = []
    for it in items:
        ps = members(it)
        for used, lst in groups:
            if not used & ps:
                used |= ps
                lst.append(it)
                break
        else:
            groups.append((set(ps), [it]))
    groups.sort(key=lambda g: -len(g[1]))
    return [it for _, lst in groups for it in lst], [len(lst) for _, lst in groups]


def batches(part_sizes, welded_pairs, limit=BATCH_LIMIT):
    """Each Part's batch: consecutive Parts fill a batch up to ``limit`` particles; a batch ends only between two
    Parts no weld spans, since particles are stored batch by batch."""
    out, b, filled = [], 0, 0
    for k, size in enumerate(part_sizes):
        spanned = any(min(pa, pb) < k <= max(pa, pb) for pa, pb in welded_pairs)
        if k and not spanned and filled + size > limit:
            b, filled = b + 1, 0
        out.append(b)
        filled += size
    return out


def _frames_re4(pos, normals, tangent):
    """[A | -A p] per particle, RE4's convention (module docstring)."""
    e = pos[tangent] - pos
    z = e / np.linalg.norm(e, axis=1, keepdims=True)
    y = normals - np.einsum("ij,ij->i", normals, z)[:, None] * z
    y /= np.maximum(np.linalg.norm(y, axis=1, keepdims=True), 1e-12)
    A = np.stack([np.cross(y, z), y, z], 1)
    return np.concatenate([A, -np.einsum("nij,nj->ni", A, pos)[:, :, None]], 2)


def build(spec, pvalues, part_of, parts, configs, header, skin_remap, remap_len, link_tail=None, welds=None,
          capsules=(), vertices=None, notes=None):
    """Return RE4 file bytes. ``spec`` a gpuc_build.ClothSpec (positions game space; ``outputs`` the driven joints
    with ``palette`` their place in the mesh's remap, used unless ``vertices``); ``pvalues`` (P, 5) sim weight,
    radius, inverse mass, max distance, backstop radius; ``part_of`` each particle's Part;
    ``parts`` / ``configs`` dicts (name, config, root, body10, body24 / name, body0c); ``header`` dict (max_velocity,
    h44, h60); ``skin_remap`` (P, 8) remap places and spec.skin_w their u8 weights; ``link_tail`` as gpuc_build's;
    ``welds`` (a, b, type, tail); ``capsules`` (joint, pair joint, payload 32 B); ``vertices`` (positions (N, 3)
    of the LOD0 vertices to drive, their indices, slots): the 328 layout instead of joints."""
    notes = notes if notes is not None else []
    pos = np.asarray(spec.positions, np.float32).astype(np.float64)
    P = len(pos)
    part_of = np.asarray(part_of, np.int64)
    tris = [(i, j, k) for i, j, k, _ in gpuc_build._triangles(spec)]
    weld_tail = {(a, b, typ): tail for a, b, typ, tail in welds or ()}
    links = [(tuple(ab), typ, rel) for ab, typ, rel in gpuc_build._links(spec)] \
        + [((a, b), typ, "weld") for a, b, typ in sorted(weld_tail)]
    edges = sorted({tuple(sorted(e)) for a, b, c in tris for e in ((a, b), (b, c), (a, c))})
    # batches of whole Parts
    sizes = np.bincount(part_of, minlength=len(parts)).tolist()
    welded = {(int(part_of[a]), int(part_of[b])) for (a, b), _, rel in links if rel == "weld"}
    of_part = batches(sizes, welded)
    batch_of = np.asarray(of_part)[part_of]
    B = max(of_part) + 1
    if any(np.bincount(batch_of) > BATCH_LIMIT):
        notes.append(f"a batch holds {int(np.bincount(batch_of).max())} particles, over the {BATCH_LIMIT} game files "
                     f"stay under: split the Part")
    by_batch = lambda items, first: [[it for it in items if batch_of[first(it)] == b] for b in range(B)]  # noqa: E731
    tri_b, link_b, edge_b = by_batch(tris, lambda t: t[0]), by_batch(links, lambda x: x[0][0]), \
        by_batch(edges, lambda e: e[0])
    T_out, L_out, E_out, tg, lg, eg, rows = [], [], [], [], [], [], []
    for b in range(B):
        t_o, t_g = _colour_items(tri_b[b], set)
        l_o, l_g = _colour_items(link_b[b], lambda x: set(x[0]))
        e_o, e_g = _colour_items(edge_b[b], set)
        T_out += t_o
        L_out += l_o
        E_out += e_o
        tg += t_g
        lg += l_g
        eg += e_g
        rows.append([int((batch_of == b).sum()), len(t_o), len(l_o), len(e_o), len(t_g), len(l_g), len(e_g), 0, 0])
    # normals and tangents: the new games' rules (gpuc_build), over the stored edges
    adj = {i: [] for i in range(P)}
    for a, b in E_out:
        adj[a].append(b)
        adj[b].append(a)
    if any(not v for v in adj.values()):
        raise ValueError("a particle lies on no triangle")
    tangent = np.array([adj[i][0] for i in range(P)])
    _, normals = gpuc_build._frames(pos, [(a, b, c, None) for a, b, c in T_out], tangent)
    packed = np.array([gpuc_build._pack_normal(n) for n in normals], np.int64)
    # links: a < b, the rest length float32 as the game files', a weld's 0
    tails = gpuc_build._template_link_tails(gpuc_build.blank(gpuc_build.gpuc.WILDS))
    p32 = pos.astype(np.float32)
    link_ab, link_rest, link_tail_b, link_type = [], [], [], []
    for (a, b), typ, rel in L_out:
        if rel == "weld":
            tail, rest = weld_tail[(a, b, typ)], 0.0
        else:
            tail = (link_tail(typ, a, b) if link_tail else None) or tails[(typ, rel)]
            rest = float(np.sqrt(np.sum((p32[a] - p32[b]) ** 2, dtype=np.float32), dtype=np.float32))
        link_ab.append(tuple(sorted((a, b))))
        link_rest.append(rest)
        link_tail_b.append(tail)
        link_type.append(typ)
    # outputs
    if vertices is None:
        flags, slots = 4, SLOTS_452
        frames = _frames_re4(pos, normals, tangent).astype("<f4")
        recs, out_end, dropped = [[] for _ in range(B)], [], 0
        for o in spec.outputs:
            ps = list(o["particles"])
            if not ps:
                continue
            b = int(batch_of[ps[0]])
            keep = [(p, w) for p, w in zip(ps, o["weights"]) if batch_of[p] == b][:slots]
            dropped += len(ps) - len(keep)
            total = sum(w for _, w in keep)
            r = struct.pack("<I", o["palette"])
            for k in range(slots):
                if k < len(keep):
                    p, w = keep[k]
                    r += struct.pack("<If", p, w / total) + frames[p].tobytes()
                else:
                    r += struct.pack("<If", 0xFFFFFFFF, 0.0) + IDENTITY_34
            recs[b].append(r)
        if dropped:
            notes.append(f"{dropped} particle slots of driven joints left out: they were in another batch than the "
                         f"joint's first particle")
        outputs = b"".join(r for lst in recs for r in lst)
        out_end = np.cumsum([len(lst) for lst in recs]).tolist()
    else:
        flags = 0
        vpos, vidx, slots = vertices
        tri, u, v, h, w = vertex_drive.bind(vpos, pos, np.asarray(T_out, np.int64), min(slots, SLOTS_328))
        recs = bytearray()
        for n in range(len(vidx)):
            recs += struct.pack("<II", int(vidx[n]), 0)
            for k in range(SLOTS_328):
                if k < tri.shape[1] and tri[n, k] >= 0:
                    recs += struct.pack("<I4f", int(tri[n, k]), u[n, k], v[n, k], h[n, k], w[n, k])
                else:
                    recs += struct.pack("<I4f", 0xFFFFFFFF, 0.0, 0.0, 0.0, 0.0)
        outputs, out_end = bytes(recs), [0] * B
    caps = [(a, b, pay) for a, b, pay in capsules]
    m = {"flags": flags, "h34": 0, "h38": 1, "h44": int(header.get("h44", 0)) & 0xFFFFFFFF,
         "max_velocity": float(header["max_velocity"]), "h58_hi": 0x0100, "h60": int(header.get("h60", 1)) & 0xFFFFFFFF,
         "remap_len": int(remap_len), "batch_sizes": rows, "batch_f07": [0] * B, "batch_out_end": out_end,
         "tri_groups": tg, "link_groups": lg, "edge_groups": eg, "parts": parts, "configs": configs,
         "pvalues": [struct.pack("<5f", *row) for row in np.asarray(pvalues, float)], "part_of": part_of, "pos": pos.astype("<f4"),
         "normal_packed": packed, "tangent": tangent,
         "skin_idx": [bytes(int(x) for x in row) for row in np.asarray(skin_remap)],
         "skin_w": [bytes(int(x) for x in row) for row in np.asarray(spec.skin_w)],
         "s14": S14 * P, "tris": T_out, "links": link_ab, "link_rest": link_rest, "link_tail": link_tail_b,
         "link_type": link_type, "edges": E_out, "outputs": outputs, "colliders": {11: [], 12: [], 13: caps},
         "s16": b"", "s17": b""}
    return write(m)

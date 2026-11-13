"""Pair a part's triangles into quads and derive its links from its faces (``gpuc_build._relations``).

Maintenance constraints:
- A quad (a, b, c, d) is split at a-c into (a, b, c) and (a, c, d), as ``gpuc_build._triangles`` writes it, so
  ``quads`` puts the file's diagonal at a-c and keeps both triangles' winding.
- A part's quad diagonals carry a bend link all or none: ``quads`` fails on a part that mixes them."""
import collections

import numpy as np

from . import gpuc_build


CORNER_BITS = 7                   # tangent flag bits 0-2: one per triangle corner


def carry_flags(tris, flags, onto) -> list:
    """Each triangle of ``onto`` (in the numbering of ``tris``; a negative index is a new particle): the tangent
    flags of the triangle of ``tris`` on the same particles, its corner bits following their particles, or None."""
    was = {frozenset(t): (t, int(x)) for t, x in zip((tuple(map(int, t)) for t in tris), flags)}
    out = []
    for t in onto:
        t = [int(v) for v in t]
        hit = was.get(frozenset(t)) if min(t) >= 0 else None
        if hit is None:
            out.append(None)
            continue
        old, x = hit
        out.append((x & ~CORNER_BITS) | sum(1 << t.index(p) for c, p in enumerate(old) if x >> c & 1))
    return out


def quads(tris, tri_uv, links, extra: bool = False) -> tuple | None:
    """Return (faces, face corner UVs, diagonal bend) when the part's links are exactly those ``gpuc_build`` makes
    from its triangles paired into quads, else None. ``links`` are (a, b, type) in part indices. With ``extra``,
    the links may differ from the faces' (``extra_links`` and ``missing_links`` give how), and an edge of more
    than two triangles (a non-manifold one) pairs the two whose other corners a shear link joins, as a quad's
    other diagonal; a triangle that pairs with none stays a triangle."""
    tris = [tuple(int(v) for v in t) for t in tris]
    tri_uv = np.asarray(tri_uv, float).reshape(-1, 3, 2)
    structural = {(min(a, b), max(a, b)) for a, b, t in links if t == 0}
    shear = {(min(a, b), max(a, b)) for a, b, t in links if t == 1}
    by_edge = collections.defaultdict(list)
    for k, t in enumerate(tris):
        for i in range(3):
            by_edge[tuple(sorted((t[i], t[(i + 1) % 3])))].append(k)
    paired, faces, uvs = set(), [], []

    def other(k, edge):
        return next(v for v in tris[k] if v not in edge)

    for edge, ks in sorted(by_edge.items()):
        if edge in structural:
            continue
        if len(ks) == 2:
            pairs = [tuple(ks)]
        elif not extra:
            return None
        else:                                         # several triangles on the edge: the shear link finds a quad
            pairs = [(i, j) for n, i in enumerate(ks) for j in ks[n + 1:]
                     if tuple(sorted((other(i, edge), other(j, edge)))) in shear]
        for i, j in pairs:
            if paired & {i, j}:
                if not extra:
                    return None
                continue
            quad = _join(tris[i], tri_uv[i], tris[j], tri_uv[j])
            if quad is None:
                if not extra:
                    return None
                continue
            paired |= {i, j}
            faces.append(quad[0])
            uvs.append(quad[1])
    for k, t in enumerate(tris):
        if k not in paired:
            faces.append(t)
            uvs.append(tri_uv[k])
    want = {(min(a, b), max(a, b), int(t)) for a, b, t in links}
    for bend in (True, False):
        if {tuple(x) for x in links_of(faces, bend).tolist()} == want and len(want) == len(links):
            return faces, np.concatenate(uvs).reshape(-1, 2), bend
    if extra:
        made = {bend: {tuple(x) for x in links_of(faces, bend).tolist()} for bend in (True, False)}
        bend = min((True, False), key=lambda b: len(made[b] ^ want))
        return faces, np.concatenate(uvs).reshape(-1, 2), bend
    return None


def missing_links(faces, links) -> set:
    """The links, (a, b, type), that the faces give and ``links`` do not hold; the diagonals' bend links, which the
    Diagonal Bend setting gives or not, aside."""
    have = {(min(int(a), int(b)), max(int(a), int(b)), int(t)) for a, b, t, *_ in links}
    return {tuple(x) for x in links_of(faces, False).tolist()} - have


def extra_links(faces, links) -> set:
    """The links, (a, b, type), that the faces do not give, the diagonals' bend links counted as given."""
    made = {tuple(x) for x in links_of(faces, True).tolist()}
    return {(min(int(a), int(b)), max(int(a), int(b)), int(t)) for a, b, t, *_ in links} - made


def _join(t1, uv1, t2, uv2):
    """The quad of two triangles sharing their diagonal, starting at a diagonal corner, or None when their
    windings disagree."""
    shared = set(t1) & set(t2)
    k = next(i for i in range(3) if t1[i] not in shared)          # t1 turned so its off corner is second
    order = [(k - 1) % 3, k, (k + 1) % 3]
    a, b, c = (t1[i] for i in order)
    d = next(v for v in t2 if v not in shared)
    second = (a, c, d)
    if not any(second == t2[i:] + t2[:i] for i in range(3)):
        return None
    uv = uv1[order]
    # a second triangle that repeats the first one's UVs (as the export writes it) holds none of its own corner
    d_uv = uv[0] + uv[2] - uv[1] if np.array_equal(uv1, uv2) else uv2[t2.index(d)]
    return (a, b, c, d), np.vstack([uv, d_uv[None]])


def triangles(faces, face_uv) -> tuple:
    """(triangles, their corner UVs) as the export splits the faces."""
    tris, uvs, at = [], [], 0
    face_uv = np.asarray(face_uv, float).reshape(-1, 2)
    for f in faces:
        uv = face_uv[at:at + len(f)]
        at += len(f)
        tris.append(tuple(f[:3]))
        uvs.append(uv[:3])
        if len(f) == 4:
            tris.append((f[0], f[2], f[3]))
            uvs.append(uv[[0, 2, 3]])
    return np.asarray(tris, np.int64).reshape(-1, 3), np.asarray(uvs, np.float32).reshape(-1, 3, 2)


class _Spec:
    def __init__(self, faces, bend):
        self.faces = [tuple(f) for f in faces]
        self.diagonal_bend = bend


def links_of(faces, diagonal_bend: bool = True) -> np.ndarray:
    """Every link (a, b, type), a < b, in the export's order."""
    n = max((max(f) for f in faces), default=-1) + 1
    bend = None if diagonal_bend else np.zeros(n, bool)
    out = [(a, b, typ) for (a, b), typ, _ in gpuc_build._links(_Spec(faces, bend))]
    return np.asarray(out, np.int64).reshape(-1, 3)


def normals(co, tris) -> np.ndarray:
    """Each particle's unit normal: its triangles' normals, area weighted, by their winding."""
    co = np.asarray(co, float)
    t = np.asarray(tris, np.int64).reshape(-1, 3)
    n = np.cross(co[t[:, 1]] - co[t[:, 0]], co[t[:, 2]] - co[t[:, 0]])
    out = np.zeros_like(co)
    for k in range(3):
        np.add.at(out, t[:, k], n)
    return out / np.maximum(np.linalg.norm(out, axis=1, keepdims=True), 1e-12)


def unfold(faces) -> tuple:
    """Lay quads out on integer cells by walking them across their shared edges, not by particle index; a quad
    wound against its neighbour is walked turning the other way. Returns (each particle's (x, y), how often each
    offset was met where the walk reached a particle it had placed elsewhere): a ring meets one offset, its
    circumference, both ways; quads the walk cannot reach from the first are not placed."""
    by_edge = collections.defaultdict(list)
    for k, q in enumerate(faces):
        for i in range(4):
            by_edge[frozenset((q[i], q[(i + 1) % 4]))].append(k)
    coord, start, seams = {}, {}, collections.Counter()
    if not faces:
        return coord, seams
    coord[faces[0][0]] = (0, 0)
    start[0] = (0, (1, 0), 1)                          # first edge, its step, 1 turning left / -1 right
    queue = collections.deque([0])
    while queue:
        k = queue.popleft()
        q = faces[k]
        j0, d, hand = start[k]
        for s in range(4):
            i = (j0 + s) % 4
            a, b = q[i], q[(i + 1) % 4]
            nb = (coord[a][0] + d[0], coord[a][1] + d[1])
            if b in coord and coord[b] != nb:
                seams[(coord[b][0] - nb[0], coord[b][1] - nb[1])] += 1
            coord.setdefault(b, nb)
            for m in by_edge[frozenset((a, b))]:
                if m == k or m in start:
                    continue
                other = faces[m]
                j = next(j for j in range(4) if {other[j], other[(j + 1) % 4]} == {a, b})
                start[m] = (j, (-d[0], -d[1]), hand) if other[j] == b else (j, d, -hand)
                queue.append(m)
            d = (-d[1], d[0]) if hand > 0 else (d[1], -d[0])
    return coord, seams

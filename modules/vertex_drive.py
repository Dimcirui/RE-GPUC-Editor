"""Bind mesh vertices to cloth triangles, and read and write the bindings of a cloth that drives mesh vertices
(Kernel Flags bit 2 off; layout: docs/onimusha-vertex-drive.md).

A driven vertex is sum_k w_k (a + u (b - a) + v (c - a) + h n) over its slots, with a, b, c its triangle's
particles' rest positions and n that triangle's unit normal.

Maintenance constraints:
- A vertex's index is its submesh's vertexStartIndex in the .mesh plus its index there (RE Mesh Editor's
  ``meshVertexOffset``), so the export takes the vertices from the exported .mesh, never the scene.
- Every slot reproduces the vertex at rest exactly before quantization; the weights only shape the motion.
"""
import struct

import numpy as np

EMPTY = (0xFFFF0000, 0)
MAX_SLOTS = 8


def tri_frames(pos, tris):
    """Return (a, b - a, c - a, unit normal) of each triangle."""
    pos = np.asarray(pos, np.float64)
    tris = np.asarray(tris, np.int64).reshape(-1, 3)
    a, b, c = pos[tris[:, 0]], pos[tris[:, 1]], pos[tris[:, 2]]
    e0, e1 = b - a, c - a
    n = np.cross(e0, e1)
    n /= np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-20)
    return a, e0, e1, n


def _segment(p, a, d):
    """Distance from points p (N, 1, 3) to segments a + t d, t in [0, 1] (M, 3)."""
    t = np.clip(np.einsum("nmk,mk->nm", p - a, d) / np.maximum(np.einsum("mk,mk->m", d, d), 1e-30), 0.0, 1.0)
    return np.linalg.norm(p - a - t[..., None] * d, axis=2)


def distances(points, frames, chunk=1 << 21):
    """Return (N, M) distances from each point to each triangle's closest point."""
    a, e0, e1, n = frames
    points = np.asarray(points, np.float64)
    M = len(a)
    out = np.empty((len(points), M))
    inv = np.linalg.inv(np.stack([e0, e1, n], 2))                         # world -> (u, v, h)
    step = max(1, chunk // max(M, 1))
    for s in range(0, len(points), step):
        p = points[s:s + step, None, :]
        uvh = np.einsum("mij,nmj->nmi", inv, p - a)
        inside = (uvh[..., 0] >= 0) & (uvh[..., 1] >= 0) & (uvh[..., 0] + uvh[..., 1] <= 1)
        edge = np.minimum(np.minimum(_segment(p, a, e0), _segment(p, a, e1)), _segment(p, a + e0, e1 - e0))
        out[s:s + step] = np.where(inside, np.abs(uvh[..., 2]), edge)
    return out


def bind(points, pos, tris, slots, chunk=1 << 21):
    """Return each point's influences: (tri (N, S) int, u, v, h, w (N, S) float); unused slots have tri -1, w 0.
    The S nearest triangles within twice the nearest distance plus 1 cm take weights 1 / (d + 1 mm)^2."""
    frames = tri_frames(pos, tris)
    a, e0, e1, n = frames
    inv = np.linalg.inv(np.stack([e0, e1, n], 2))
    points = np.asarray(points, np.float64)
    N, S = len(points), slots
    tri = np.full((N, S), -1, np.int64)
    uvh = np.zeros((N, S, 3))
    w = np.zeros((N, S))
    step = max(1, chunk // max(len(a), 1))
    for s in range(0, N, step):
        d = distances(points[s:s + step], frames, chunk)
        order = np.argsort(d, axis=1)[:, :S]
        dk = np.take_along_axis(d, order, 1)
        keep = dk <= 2 * dk[:, :1] + 0.01
        wk = np.where(keep, 1.0 / (dk + 1e-3) ** 2, 0.0)
        wk /= wk.sum(1, keepdims=True)
        p = points[s:s + step, None, :]
        local = np.einsum("nkij,nkj->nki", inv[order], p - a[order])
        tri[s:s + step] = np.where(keep, order, -1)
        uvh[s:s + step] = np.where(keep[..., None], local, 0.0)
        w[s:s + step] = wk
    return tri, uvh[..., 0], uvh[..., 1], uvh[..., 2], w


def evaluate(pos, tris, tri, u, v, h, w):
    """Return the points the influences give (rest pose)."""
    a, e0, e1, n = tri_frames(pos, tris)
    live = tri >= 0
    t = np.where(live, tri, 0)
    pt = a[t] + u[..., None] * e0[t] + v[..., None] * e1[t] + h[..., None] * n[t]
    ww = np.where(live, w, 0.0)
    return (pt * ww[..., None]).sum(1) / np.maximum(ww.sum(1), 1e-12)[:, None]


def _half_bits(x):
    return np.asarray(x, np.float32).astype(np.float16).view(np.uint16).astype(np.int64)


def _round_bits(bits, drop):
    """f16 bit patterns rounded to a multiple of 2^drop in magnitude (sign kept), never past the largest finite."""
    sign, mag = bits & 0x8000, bits & 0x7FFF
    mag = np.minimum(((mag + (1 << (drop - 1))) >> drop) << drop, (0x7BFF >> drop) << drop)
    return sign | mag


def _weights_u8(w):
    """Quantize each row's weights to u8 summing to 255 (rows of zeros stay zero)."""
    q = np.floor(w * 255 + 0.5).astype(np.int64)
    rows = q.sum(1) > 0
    top = np.argmax(w, 1)
    q[np.arange(len(q)), top] += np.where(rows, 255 - q.sum(1), 0)
    return np.clip(q, 0, 255)


def encode(tri, u, v, h, w):
    """Section 24 bytes for (J, S) influences (see the module docstring)."""
    tri = np.asarray(tri, np.int64)
    if tri.max(initial=-1) >= 0xFFFF:
        raise ValueError(f"triangle {int(tri.max())} does not fit a vertex-drive slot (at most 65534)")
    wq = _weights_u8(np.where(tri >= 0, w, 0.0))
    hu = _round_bits(_half_bits(u), 2)
    hv = _round_bits(_half_bits(v), 3)
    hh = _round_bits(_half_bits(h), 3)
    w0 = (tri << 16) | (hu & 0xFFFC) | ((hv >> 14) & 1) | (((hv >> 15) & 1) << 1)
    w1 = wq | (((hh >> 3) & 0x1FFF) << 8) | (((hv >> 3) & 0x7FF) << 21)
    w0 = np.where(tri < 0, EMPTY[0], w0)
    w1 = np.where(tri < 0, EMPTY[1], w1)
    return np.stack([w0, w1], -1).astype("<u4").tobytes()


def decode(data, J, S):
    """Return (tri, u, v, h, w) arrays (J, S) from section 24 bytes; empty slots have tri -1, w 0. A slot whose
    weight rounded to 0 keeps its triangle, so decoded slots encode back unchanged."""
    s = np.frombuffer(bytes(data)[:J * S * 8], "<u4").reshape(J, S, 2).astype(np.int64)
    w0, w1 = s[..., 0], s[..., 1]

    def half(bits):
        return bits.astype(np.uint16).view(np.float16).astype(np.float64)

    w = (w1 & 0xFF) / 255.0
    tri = np.where((w0 == EMPTY[0]) & (w1 == EMPTY[1]), -1, w0 >> 16)
    u = half(w0 & 0xFFFC)
    v = half((((w1 >> 21) & 0x7FF) << 3) | ((w0 & 1) << 14) | (((w0 >> 1) & 1) << 15))
    h = half(((w1 >> 8) & 0x1FFF) << 3)
    return tri, np.where(tri >= 0, u, 0.0), np.where(tri >= 0, v, 0.0), np.where(tri >= 0, h, 0.0), w


def vertex_map(vertex_count, driven):
    """Section 27 bytes: each vertex's output (its place among ``driven``, ascending), -1 for the others."""
    m = np.full(vertex_count, -1, "<i4")
    m[np.asarray(driven, np.int64)] = np.arange(len(driven))
    return m.tobytes()


def read_map(data, vertex_count):
    return np.frombuffer(bytes(data)[:4 * vertex_count], "<i4").astype(np.int64)


def apply(g, vertex_count, lod_count, driven, influences, at_vertices, at_lods):
    """Turn a built gpuc.Gpuc into a vertex-driving one in place: ``driven`` the vertices (ascending), their
    ``influences`` (tri, u, v, h, w), each (len(driven), S); ``at_vertices`` / ``at_lods`` the header offsets of
    the mesh's vertex and LOD counts. The caller clears Kernel Flags bit 2."""
    J, S = influences[0].shape
    at = {name: off for off, name in g.layout.counts.items()}
    pad = lambda b: bytes(b) + b"\0" * (-len(b) % 16)  # noqa: E731
    g.sections[23].data = bytearray(pad(b"\0" * 4 * J))
    g.sections[24].data = bytearray(pad(encode(*influences)))
    g.sections[25].data = bytearray()
    g.sections[26].data = bytearray()
    g.sections[27].data = bytearray(pad(vertex_map(vertex_count, driven)))
    g.refs["header"] = (23, 0)
    for sec, word in ((7, 6), (10, 6)):                   # deform-info counts: none in a vertex-driving file
        struct.pack_into("<I", g.sections[sec].data, 4 * word, 0)
    struct.pack_into("<I", g.sections[13].data, 4 * 15, J)
    struct.pack_into("<2I", g.header, at["joints"], J, S)
    struct.pack_into("<I", g.header, at_vertices, vertex_count)
    struct.pack_into("<I", g.header, at_lods, lod_count)


def particles_and_triangles(g):
    """A parsed gpuc.Gpuc's particle rest positions (float32 as written) and triangles."""
    c = g.counts()
    P, T = c["particles"], c["triangles"]
    p0 = np.frombuffer(bytes(g.sections[0].data)[:P * 108], np.uint8).reshape(P, 108)
    pos = p0[:, 0x48:0x54].copy().view("<f4").astype(np.float64)
    tb = bytes(g.sections[1].data)
    tris = np.array([struct.unpack_from("<3H", tb, i * 20) for i in range(T)], np.int64).reshape(-1, 3)
    return pos, tris

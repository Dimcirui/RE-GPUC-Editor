"""A cloth part's particle grid, Blender-free: columns of particles (one per bone, 1:1 layout), rows from the
top (row 0) down, joined into quads; the grid UV the editor selects on; the distance links and their shapes.

Particle i is row i // cols, column i % cols. A closed grid (a skirt ring) joins the last column to the
first; an open one (a cape, a front panel) does not. The links follow the generator's rules exactly
(gpuc_build._relations and LINK_TYPES): a quad edge gives a structural link (type 0), a quad diagonal a shear
(1) and a bend (2) link, two steps straight along quad edges a bend link (2). The type number is only a label
(the kernels never read it, measured on the game's GPU solver); what a link does is its tail (k, k2, lo, hi), set
per shape: VERTICAL and RING (the quad edges down a column and across a row), SHEAR (the diagonals' type 1),
BEND (the diagonals' type 2 and the two-step links)."""
from __future__ import annotations

import math

import numpy as np

from . import gpuc_build

SHAPES = ("VERTICAL", "RING", "SHEAR", "BEND")


class GridError(ValueError):
    pass


def order_columns(tops, closed: str = "AUTO") -> tuple[list[int], bool, np.ndarray | None]:
    """The columns' order around the cloth and whether it closes, from each column's top position (x, y, z,
    Blender space, z up). Columns go round a vertical axis through the circle the tops lie on (fitted), starting
    at the front (-y) and turning towards +x; tops nearly in a line (a flat panel) go along that line from -x,
    open. An open piece starts after the widest gap between neighbours. closed: AUTO (closed when that gap is
    under 1.5 times the median gap), CLOSED or OPEN. Also returns the axis' (x, y), None for a flat panel."""
    tops = np.asarray(tops, float).reshape(-1, 3)
    if len(tops) < 2:
        raise GridError("a cloth part needs at least two columns (one column has no quads: use chain2)")
    xy = tops[:, :2]
    centre = _circle_centre(xy)
    if centre is None:
        d = np.linalg.svd(xy - xy.mean(axis=0))[2][0]
        d = d if d[0] >= 0 else -d
        return [int(k) for k in np.argsort((xy - xy.mean(axis=0)) @ d, kind="stable")], closed == "CLOSED", None
    angle = np.array([round(math.atan2(p[0] - centre[0], -(p[1] - centre[1])), 9) % math.tau for p in xy])
    order = [int(k) for k in np.argsort(angle, kind="stable")]
    a = angle[order]
    gaps = np.diff(np.append(a, a[0] + math.tau))         # gap after each column, the last one wrapping
    widest = int(np.argmax(gaps))
    if closed == "AUTO":
        is_closed = len(tops) >= 3 and gaps[widest] < 1.5 * float(np.median(gaps))
    else:
        is_closed = closed == "CLOSED"
    if not is_closed:
        order = order[widest + 1:] + order[:widest + 1]
    return order, bool(is_closed), centre


def _circle_centre(xy):
    """The centre of the circle through the points (least squares), None when they are nearly in a line
    (fewer than three, or a radius over five times their spread)."""
    if len(xy) < 3:
        return None
    xy = xy[np.lexsort((xy[:, 1], xy[:, 0]))]          # in a fixed order: the same centre from the same points
    a = np.column_stack([2 * xy, np.ones(len(xy))])
    (cx, cy, c), *_ = np.linalg.lstsq(a, (xy ** 2).sum(axis=1), rcond=None)
    radius = math.sqrt(max(c + cx * cx + cy * cy, 0.0))
    spread = float(np.ptp(xy, axis=0).max())
    return None if spread <= 0 or radius > 5 * spread else np.array([cx, cy])


def dims(node_rows: int, node_cols: int, sub_rows: int, sub_cols: int, closed: bool) -> tuple[int, int]:
    """The particle grid's rows and columns: sub_rows particles between two bones down a chain, sub_cols columns
    between two neighbouring chains (a closed ring also between the last chain and the first)."""
    rows = (node_rows - 1) * (sub_rows + 1) + 1
    cols = node_cols * (sub_cols + 1) if closed else (node_cols - 1) * (sub_cols + 1) + 1
    return rows, cols


def subdivide(nodes, sub_rows: int, sub_cols: int, closed: bool, centre=None):
    """The particles of a grid of bone heads (node_rows x node_cols x 3, row 0 at the top): each particle's
    position, and its skin as the 4 corner bones (flat node indices) and their bilinear weights. A particle on a
    bone is that bone's alone. Around an axis (centre: its x, y) the in-between columns follow the circle (angle
    and distance from the axis are interpolated, not the chord); without one, straight lines."""
    nodes = np.asarray(nodes, float)
    nr, nc = nodes.shape[:2]
    rows, cols = dims(nr, nc, sub_rows, sub_cols, closed)
    pos = np.empty((rows * cols, 3))
    skin = np.empty((rows * cols, 4), np.int64)
    weight = np.empty((rows * cols, 4))
    for r in range(rows):
        r0, v = divmod(r, sub_rows + 1)
        v /= sub_rows + 1
        r1 = min(r0 + 1, nr - 1)
        for c in range(cols):
            c0, u = divmod(c, sub_cols + 1)
            u /= sub_cols + 1
            c1 = (c0 + 1) % nc if closed else min(c0 + 1, nc - 1)
            corners = [(r0, c0), (r0, c1), (r1, c0), (r1, c1)]
            w = np.array([(1 - u) * (1 - v), u * (1 - v), (1 - u) * v, u * v])
            pts = np.array([nodes[a, b] for a, b in corners])
            i = r * cols + c
            pos[i] = _blend(pts, w, centre)
            skin[i] = [a * nc + b for a, b in corners]
            weight[i] = w
    return pos, skin, weight


def _blend(pts, w, centre):
    if centre is None:
        return w @ pts
    d = pts[:, :2] - centre
    ang = np.arctan2(d[:, 1], d[:, 0])
    ang = ang[0] + (ang - ang[0] + math.pi) % math.tau - math.pi        # unwrapped next to the first corner
    a = w @ ang
    rad = w @ np.linalg.norm(d, axis=1)
    return np.array([centre[0] + rad * math.cos(a), centre[1] + rad * math.sin(a), w @ pts[:, 2]])


def faces(rows: int, cols: int, closed: bool) -> list[tuple[int, int, int, int]]:
    """The quads, row by row: (r, c), (r + 1, c), (r + 1, c + 1), (r, c + 1)."""
    if rows < 2:
        raise GridError("a cloth part needs at least two rows")
    span = cols if closed else cols - 1
    out = []
    for r in range(rows - 1):
        for c in range(span):
            d = (c + 1) % cols
            out.append((r * cols + c, (r + 1) * cols + c, (r + 1) * cols + d, r * cols + d))
    return out


def face_uv(rows: int, cols: int, closed: bool, tile: int = 0) -> list[list[tuple[float, float]]]:
    """Each quad's corners on the grid UV: u = column over the columns (a closed grid's seam column at both
    0 and 1), v = 1 at the top row, 0 at the bottom; each part on its own UV tile (u + tile)."""
    span = cols if closed else cols - 1
    out = []
    for r in range(rows - 1):
        for c in range(span):
            u0, u1 = tile + c / span, tile + (c + 1) / span
            v0, v1 = 1.0 - r / (rows - 1), 1.0 - (r + 1) / (rows - 1)
            out.append([(u0, v0), (u0, v1), (u1, v1), (u1, v0)])
    return out


def particle_uv(rows: int, cols: int, closed: bool, tile: int = 0) -> np.ndarray:
    """Each particle's place on the grid UV (a closed grid's seam column at u = 0)."""
    span = cols if closed else cols - 1
    r, c = np.divmod(np.arange(rows * cols), cols)
    return np.column_stack([tile + c / span, 1.0 - r / (rows - 1)])


def links(rows: int, cols: int, closed: bool) -> np.ndarray:
    """Every link as (a, b, type, shape index into SHAPES), a < b, in the generator's order."""
    out = []
    for (a, b), typ, rel in gpuc_build._links(_Spec(faces(rows, cols, closed))):
        ra, ca = divmod(a, cols)
        rb, cb = divmod(b, cols)
        if rel == "Q":
            shape = 0 if ca == cb else 1
        elif rel == "diag":
            shape = 2 if typ == 1 else 3
        else:
            shape = 3
        out.append((a, b, typ, shape))
    return np.array(out, np.int64).reshape(-1, 4)


class _Spec:
    """What gpuc_build._links reads of a ClothSpec."""

    def __init__(self, quads):
        self.faces = [tuple(f) for f in quads]


def normals(co, rows: int, cols: int, closed: bool) -> np.ndarray:
    """Each particle's unit normal from its grid neighbours: (down the column) x (along the row), central
    differences (one-sided at open edges). Its sign is the grid's; normal_sign() turns it outward."""
    p = np.asarray(co, float).reshape(rows, cols, 3)
    if closed:
        across = np.roll(p, -1, axis=1) - np.roll(p, 1, axis=1)
    else:
        across = np.empty_like(p)
        across[:, 1:-1] = p[:, 2:] - p[:, :-2]
        across[:, 0], across[:, -1] = p[:, 1] - p[:, 0], p[:, -1] - p[:, -2]
    down = np.empty_like(p)
    if rows > 1:
        down[1:-1] = p[2:] - p[:-2]
        down[0], down[-1] = p[1] - p[0], p[-1] - p[-2]
    else:
        down[:] = (0.0, 0.0, -1.0)
    n = np.cross(down, across).reshape(-1, 3)
    length = np.linalg.norm(n, axis=1, keepdims=True)
    return np.where(length > 1e-12, n / np.maximum(length, 1e-12), (0.0, 0.0, 1.0))


def normal_sign(co, n, centre=None, inside=None) -> float:
    """+1 or -1: what turns normals() outward, away from the vertical axis through centre (x, y) or, for a flat
    panel, away from a point inside (the root joint)."""
    co, n = np.asarray(co, float), np.asarray(n, float)
    if centre is not None:
        out = co[:, :2] - np.asarray(centre, float)[:2]
        dot = float((n[:, :2] * out).sum())
    elif inside is not None:
        dot = float((n * (co - np.asarray(inside, float))).sum())
    else:
        return 1.0
    return -1.0 if dot < 0 else 1.0

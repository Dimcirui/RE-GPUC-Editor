"""Lay out a cloth part's particle grid without Blender: columns of particles (one per bone at 1:1), rows from
the top (row 0) down, joined into quads; the grid UV the editor selects on; the distance links and their shapes.

Particle i is row i // cols, column i % cols, unless the grid has a ``shape``: cells left out (``mask``) and slits,
lattice columns cut from below a row down to the bottom, whose points each have two particles (the cells on the
left take the first, those on the right the second). Its particles are then the lattice points some cell uses, in
lattice order, a slit's second copy after its first. A closed grid (a skirt ring) joins the last column to the
first; an open one (a cape, a front panel) does not. The links follow ``gpuc_build._relations`` and
``LINK_TYPES``: a quad edge gives a structural link (type 0), a quad diagonal a shear (1) and a bend (2) link
(the shear alone without ``diagonal_bend``), two steps straight along quad edges a bend link (2). The game's solver does not read the type number; what a link
does is its tail (k, k2, lo, hi), set per shape: VERTICAL and RING (the quad edges down a column and across a
row), SHEAR (the diagonals' type 1), BEND (the diagonals' type 2 and the two-step links)."""
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


def steps(sub, gaps: int) -> list:
    """Particles between each two neighbouring bones: ``sub`` for every one of the ``gaps``, or its own list."""
    if isinstance(sub, (int, np.integer)):
        return [int(sub)] * gaps
    out = [int(x) for x in sub]
    if len(out) != gaps:
        raise GridError(f"{len(out)} steps given for {gaps} gaps between bones")
    return out


def node_lines(sub, nodes: int, closed: bool = False) -> list:
    """The particle row (or column) of each bone node, from the top (or the first column); a ring's also the
    first node's again after the last gap."""
    gaps = steps(sub, nodes if closed else nodes - 1)
    return [int(x) for x in np.concatenate([[0], np.cumsum(np.array(gaps) + 1)])]


def dims(node_rows: int, node_cols: int, sub_rows, sub_cols, closed: bool) -> tuple[int, int]:
    """The particle grid's rows and columns: sub_rows particles between two bones down a chain, sub_cols columns
    between two neighbouring chains (a closed ring also between the last chain and the first); either a number for
    every gap or a list, one per gap."""
    rows = node_lines(sub_rows, node_rows)[-1] + 1
    lines = node_lines(sub_cols, node_cols, closed)
    return rows, (lines[-1] if closed else lines[-1] + 1)


def _segment(lines: list, x: int) -> tuple:
    """(the node gap a particle row or column lies in, its share of the way across it); on the last node, that
    node and no share."""
    k = int(np.searchsorted(lines, x, side="right")) - 1
    if k >= len(lines) - 1:
        return len(lines) - 1, 0.0
    return k, (x - lines[k]) / (lines[k + 1] - lines[k])


def extend(pos, skin, weight, cols: int, above: int = 0, gap: float = 0.01, below: int = 0) -> tuple:
    """A particle grid with ``above`` rows over its top row, ``gap`` apart along each column's way up (its second
    row to its first), and ``below`` rows under its bottom row, as far apart as its last two rows; each skinned as the
    row it extends. Returns (pos, skin, weight) with the rows added."""
    pos, skin, weight = np.asarray(pos, float), np.asarray(skin), np.asarray(weight, float)
    if not (above or below) or len(pos) < 2 * cols:
        return pos, skin, weight
    top, second = pos[:cols], pos[cols:2 * cols]
    up = top - second
    up /= np.maximum(np.linalg.norm(up, axis=1, keepdims=True), 1e-12)
    step = pos[-cols:] - pos[-2 * cols:-cols]
    parts_p = [top + up * gap * k for k in range(above, 0, -1)] + [pos] + \
        [pos[-cols:] + step * k for k in range(1, below + 1)]
    parts_s = [skin[:cols]] * above + [skin] + [skin[-cols:]] * below
    parts_w = [weight[:cols]] * above + [weight] + [weight[-cols:]] * below
    return np.concatenate(parts_p), np.concatenate(parts_s), np.concatenate(parts_w)


def extend_cols(pos, skin, weight, rows: int, cols: int, left: int = 0, right: int = 0) -> tuple:
    """A particle grid with ``left`` columns before its first column and ``right`` after its last, each as far from
    the next as its own two edge columns, skinned as the column it extends. Returns (pos, skin, weight)."""
    pos, skin, weight = np.asarray(pos, float), np.asarray(skin), np.asarray(weight, float)
    if not (left or right) or cols < 2:
        return pos, skin, weight
    p = pos.reshape(rows, cols, 3)
    s_, w_ = skin.reshape(rows, cols, -1), weight.reshape(rows, cols, -1)
    first, last = p[:, :1], p[:, -1:]
    step_l, step_r = p[:, :1] - p[:, 1:2], p[:, -1:] - p[:, -2:-1]
    parts_p = [first + step_l * k for k in range(left, 0, -1)] + [p] + [last + step_r * k for k in range(1, right + 1)]
    parts_s = [s_[:, :1]] * left + [s_] + [s_[:, -1:]] * right
    parts_w = [w_[:, :1]] * left + [w_] + [w_[:, -1:]] * right
    width = cols + left + right
    return (np.concatenate(parts_p, axis=1).reshape(rows * width, 3),
            np.concatenate(parts_s, axis=1).reshape(rows * width, -1),
            np.concatenate(parts_w, axis=1).reshape(rows * width, -1))


def subdivide(nodes, sub_rows, sub_cols, closed: bool, centre=None):
    """The particles of a grid of bone heads (node_rows x node_cols x 3, row 0 at the top): each particle's
    position, and its skin as the 4 corner bones (flat node indices) and their bilinear weights. A particle on a
    bone is that bone's alone. Around an axis (centre: its x, y) the in-between columns follow the circle (angle
    and distance from the axis are interpolated, not the chord); without one, straight lines. ``sub_rows`` and
    ``sub_cols``: a number for every gap between bones, or a list (``steps``)."""
    nodes = np.asarray(nodes, float)
    nr, nc = nodes.shape[:2]
    rows, cols = dims(nr, nc, sub_rows, sub_cols, closed)
    row_lines, col_lines = node_lines(sub_rows, nr), node_lines(sub_cols, nc, closed)
    pos = np.empty((rows * cols, 3))
    skin = np.empty((rows * cols, 4), np.int64)
    weight = np.empty((rows * cols, 4))
    for r in range(rows):
        r0, v = _segment(row_lines, r)
        r1 = min(r0 + 1, nr - 1)
        for c in range(cols):
            c0, u = _segment(col_lines, c)
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


class Shape:
    """A grid's particles and quads (see the module): ``point`` each particle's lattice point (r * cols + c),
    ``copy`` 0, or 1 for a slit's second, ``faces`` the quads in particle indices as ``faces`` has them, ``cells`` the
    index into ``faces(rows, cols, closed)`` of each, ``first`` each lattice point's first particle (-1: none)."""

    def __init__(self, rows, cols, closed, mask=(), slits=()):
        self.rows, self.cols, self.closed = rows, cols, closed
        self.mask, self.slits = tuple(sorted({int(k) for k in mask})), tuple(sorted((int(c), int(t)) for c, t in slits))
        span = cols if closed else cols - 1
        cut = set()
        for c, top in self.slits:
            if not (0 <= top < rows - 1) or not (0 <= c < cols) or (not closed and c in (0, cols - 1)):
                raise GridError(f"no slit can be cut at column {c} from row {top}: a {rows} x {cols} grid")
            cut.update(r * cols + c for r in range(top + 1, rows))
        gone = set(self.mask)
        corners, used = [], set()
        for k, q in enumerate(faces(rows, cols, closed)):
            if k in gone:
                continue
            left = k % span
            corner = [(p, int(p in cut and p % cols == left)) for p in q]
            corners.append((k, corner))
            used.update(corner)
        if not corners:
            raise GridError("every cell of the grid is left out")
        index = {}
        for p in range(rows * cols):
            for copy in (0, 1):
                if (p, copy) in used:
                    index[(p, copy)] = len(index)
        self.point = np.array([p for p, _ in index], np.int64)
        self.copy = np.array([c for _, c in index], np.int64)
        self.faces = [tuple(index[x] for x in corner) for _, corner in corners]
        self.cells = [k for k, _ in corners]
        self.first = np.full(rows * cols, -1, np.int64)
        for (p, copy), i in index.items():
            if copy == 0 or self.first[p] < 0:
                self.first[p] = i
        self.plain = not self.mask and not self.slits

    def __len__(self):
        return len(self.point)


_shapes: dict = {}


def shape(rows: int, cols: int, closed: bool, mask=(), slits=()) -> Shape:
    """The grid's ``Shape`` (cached)."""
    key = (int(rows), int(cols), bool(closed), tuple(sorted({int(k) for k in mask})),
           tuple(sorted((int(c), int(t)) for c, t in slits)))
    if key not in _shapes:
        _shapes[key] = Shape(*key)
    return _shapes[key]


def lattice(values, sh: Shape, fill=None) -> np.ndarray:
    """Per-particle values on the lattice points (rows * cols): each point its first particle's; a point no cell
    uses takes ``fill``, or with none its nearest used neighbour's along the row, else down the column."""
    values = np.asarray(values)
    out = values[np.maximum(sh.first, 0)].copy()
    missing = sh.first < 0
    if not missing.any():
        return out
    if fill is not None:
        out[missing] = fill
        return out
    r, c = np.divmod(np.arange(sh.rows * sh.cols), sh.cols)
    have = np.where(~missing)[0]
    for p in np.where(missing)[0]:
        same_row = have[r[have] == r[p]]
        pool = same_row if len(same_row) else have
        q = pool[np.argmin(np.abs(c[pool] - c[p]) + 1000 * np.abs(r[pool] - r[p]))]
        out[p] = values[sh.first[q]]
    return out


def particles(lattice_values, sh: Shape, second=None) -> np.ndarray:
    """Lattice point values on the particles; a slit's second copies take ``second``'s, when given."""
    out = np.asarray(lattice_values)[sh.point].copy()
    if second is not None:
        out[sh.copy == 1] = np.asarray(second)[sh.point[sh.copy == 1]]
    return out


def face_uv(rows: int, cols: int, closed: bool, tile: int = 0, sh: Shape | None = None) -> list[list[tuple[float, float]]]:
    """Each quad's corners on the grid UV: u = column over the columns (a closed grid's seam column at both
    0 and 1), v = 1 at the top row, 0 at the bottom; each part on its own UV tile (u + tile). With a ``Shape``, its
    quads'."""
    if sh is not None and not sh.plain:
        every = face_uv(rows, cols, closed, tile)
        return [every[k] for k in sh.cells]
    span = cols if closed else cols - 1
    out = []
    for r in range(rows - 1):
        for c in range(span):
            u0, u1 = tile + c / span, tile + (c + 1) / span
            v0, v1 = 1.0 - r / (rows - 1), 1.0 - (r + 1) / (rows - 1)
            out.append([(u0, v0), (u0, v1), (u1, v1), (u1, v0)])
    return out


def particle_uv(rows: int, cols: int, closed: bool, tile: int = 0, sh: Shape | None = None) -> np.ndarray:
    """Each particle's place on the grid UV (a closed grid's seam column at u = 0); with a ``Shape``, its
    particles'."""
    span = cols if closed else cols - 1
    r, c = np.divmod(np.arange(rows * cols) if sh is None else sh.point, cols)
    return np.column_stack([tile + c / span, 1.0 - r / (rows - 1)])


def links(rows: int, cols: int, closed: bool, diagonal_bend: bool = True, mask=(), slits=()) -> np.ndarray:
    """Every link as (a, b, type, shape index into SHAPES), a < b, in the generator's order; without
    ``diagonal_bend`` the diagonals carry no bend link. With a ``mask`` or ``slits``, of the grid's ``shape``."""
    out = []
    sh = shape(rows, cols, closed, mask, slits)
    bend = None if diagonal_bend else np.zeros(len(sh), bool)
    for (a, b), typ, rel in gpuc_build._links(_Spec(sh.faces, bend)):
        ca, cb = sh.point[a] % cols, sh.point[b] % cols
        if rel == "Q":
            kind = 0 if ca == cb else 1
        elif rel == "diag":
            kind = 2 if typ == 1 else 3
        else:
            kind = 3
        out.append((a, b, typ, kind))
    return np.array(out, np.int64).reshape(-1, 4)


class _Spec:
    """What gpuc_build._links reads of a ClothSpec."""

    def __init__(self, quads, diagonal_bend=None):
        self.faces = [tuple(f) for f in quads]
        self.diagonal_bend = diagonal_bend


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

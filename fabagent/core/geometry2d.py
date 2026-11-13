"""2D geometrija: poligoni kao numpy nizovi (N, 2), bez ponovljene zadnje tacke.

Sve funkcije su deterministicke. Lukovi se aproksimiraju tetivama cija greska ne
prelazi zadanu toleranciju (config).
"""

from __future__ import annotations

import math

import numpy as np

from fabagent.core import config as C


# ----------------------------------------------------------------- osnovno
def signed_area(poly: np.ndarray) -> float:
    x, y = poly[:, 0], poly[:, 1]
    return 0.5 * float(np.dot(x, np.roll(y, -1)) - np.dot(np.roll(x, -1), y))


def ensure_orientation(poly: np.ndarray, ccw: bool) -> np.ndarray:
    """ccw=True -> pozitivna povrsina u matematickom (y gore) smislu."""
    area = signed_area(poly)
    if (area > 0) != ccw:
        return poly[::-1].copy()
    return poly


def bbox(polys: list[np.ndarray]) -> tuple[float, float, float, float]:
    pts = np.vstack(polys)
    return (float(pts[:, 0].min()), float(pts[:, 1].min()),
            float(pts[:, 0].max()), float(pts[:, 1].max()))


def perimeter(poly: np.ndarray) -> float:
    return float(np.linalg.norm(np.roll(poly, -1, axis=0) - poly, axis=1).sum())


def segments_of(poly: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    return poly, np.roll(poly, -1, axis=0)


def arc_segments(radius: float, sweep_rad: float = 2 * math.pi,
                 tol: float = C.ARC_CHORD_TOLERANCE_MM,
                 min_seg: int = C.ARC_MIN_SEGMENTS, max_seg: int = C.ARC_MAX_SEGMENTS) -> int:
    """Broj segmenata tako da greska tetive bude <= tol."""
    if radius <= tol:
        return max(1, int(math.ceil(min_seg * sweep_rad / (2 * math.pi))))
    step = 2.0 * math.acos(1.0 - tol / radius)
    n_full = int(math.ceil(2 * math.pi / step))
    n_full = max(min_seg, min(max_seg, n_full))
    return max(1, int(math.ceil(n_full * sweep_rad / (2 * math.pi))))


def circle(cx: float, cy: float, r: float, segments: int | None = None, **kw) -> np.ndarray:
    n = segments if segments is not None else arc_segments(r, **kw)
    t = np.arange(n) * (2 * math.pi / n)
    return np.column_stack([cx + r * np.cos(t), cy + r * np.sin(t)])


def arc(cx: float, cy: float, r: float, a0: float, a1: float, **kw) -> np.ndarray:
    """Tacke luka od a0 do a1 (radijani), ukljucujuci oba kraja."""
    n = arc_segments(r, abs(a1 - a0), **kw)
    t = np.linspace(a0, a1, n + 1)
    return np.column_stack([cx + r * np.cos(t), cy + r * np.sin(t)])


def rounded_rect(x0: float, y0: float, x1: float, y1: float, r: float, **kw) -> np.ndarray:
    """Pravougaonik sa zaobljenim uglovima, CCW (y gore). r=0 -> 4 tacke."""
    w, h = x1 - x0, y1 - y0
    if w <= 0 or h <= 0:
        raise ValueError("pravougaonik mora imati pozitivne dimenzije")
    if r < 0 or r > min(w, h) / 2 + C.GEOMETRY_EPS_MM:
        raise ValueError("radijus ugla mora biti u [0, min(w,h)/2]")
    if r <= C.GEOMETRY_EPS_MM:
        return np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1]], dtype=float)
    r = min(r, w / 2, h / 2)
    parts = [
        arc(x1 - r, y0 + r, r, -math.pi / 2, 0, **kw),
        arc(x1 - r, y1 - r, r, 0, math.pi / 2, **kw),
        arc(x0 + r, y1 - r, r, math.pi / 2, math.pi, **kw),
        arc(x0 + r, y0 + r, r, math.pi, 1.5 * math.pi, **kw),
    ]
    pts = np.vstack(parts)
    return dedupe(pts)


def dedupe(poly: np.ndarray, eps: float = C.GEOMETRY_EPS_MM) -> np.ndarray:
    """Uklanja uzastopne duplikate (ukljucujuci zadnja == prva)."""
    keep = [0]
    for i in range(1, len(poly)):
        if np.linalg.norm(poly[i] - poly[keep[-1]]) > eps:
            keep.append(i)
    out = poly[keep]
    if len(out) > 1 and np.linalg.norm(out[-1] - out[0]) <= eps:
        out = out[:-1]
    return out


def chaikin(poly: np.ndarray, iterations: int) -> np.ndarray:
    """Chaikin zaobljavanje zatvorenog poligona (deterministicko)."""
    pts = np.asarray(poly, dtype=float)
    for _ in range(iterations):
        nxt = np.roll(pts, -1, axis=0)
        q = 0.75 * pts + 0.25 * nxt
        r = 0.25 * pts + 0.75 * nxt
        pts = np.empty((2 * len(q), 2))
        pts[0::2] = q
        pts[1::2] = r
    return pts


def normalize_to_box(poly: np.ndarray, x0: float, y0: float, w: float, h: float) -> np.ndarray:
    mn = poly.min(axis=0)
    mx = poly.max(axis=0)
    span = mx - mn
    out = (poly - mn) / span
    return np.column_stack([x0 + out[:, 0] * w, y0 + out[:, 1] * h])


# ------------------------------------------------------------ predikati
def points_in_polygon(points: np.ndarray, poly: np.ndarray) -> np.ndarray:
    """Even-odd test za vise tacaka odjednom."""
    pts = np.atleast_2d(points)
    x, y = pts[:, 0][:, None], pts[:, 1][:, None]
    a = poly[None, :, :]
    b = np.roll(poly, -1, axis=0)[None, :, :]
    ay, by = a[..., 1], b[..., 1]
    ax, bx = a[..., 0], b[..., 0]
    cond = (ay > y) != (by > y)
    with np.errstate(divide="ignore", invalid="ignore"):
        xint = ax + (y - ay) * (bx - ax) / (by - ay)
    crossing = cond & (x < xint)
    return (np.count_nonzero(crossing, axis=1) % 2) == 1


def point_segments_distance(points: np.ndarray, a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Matrica udaljenosti (P, S) tacaka do segmenata a->b."""
    p = np.atleast_2d(points)[:, None, :]
    ab = (b - a)[None, :, :]
    ap = p - a[None, :, :]
    denom = np.einsum("ijk,ijk->ij", ab, ab)
    denom = np.where(denom < 1e-30, 1e-30, denom)
    t = np.clip(np.einsum("ijk,ijk->ij", ap, ab) / denom, 0.0, 1.0)
    proj = a[None, :, :] + t[..., None] * ab
    return np.linalg.norm(p - proj, axis=2)


def min_distance_points_to_polyline(points: np.ndarray, poly: np.ndarray, closed: bool = True,
                                    chunk: int = 512) -> float:
    a = poly if closed else poly[:-1]
    b = np.roll(poly, -1, axis=0) if closed else poly[1:]
    best = math.inf
    pts = np.atleast_2d(points)
    for i in range(0, len(pts), chunk):
        d = point_segments_distance(pts[i:i + chunk], a, b)
        best = min(best, float(d.min()))
    return best


def _orient(a, b, c):
    return (b[..., 0] - a[..., 0]) * (c[..., 1] - a[..., 1]) - (b[..., 1] - a[..., 1]) * (c[..., 0] - a[..., 0])


def segments_intersect_matrix(a1, b1, a2, b2, eps: float = 1e-12) -> np.ndarray:
    """(N, M) bool: da li se segment i iz skupa 1 sijece sa segmentom j iz skupa 2
    (ukljucujuci dodir)."""
    A1, B1 = a1[:, None, :], b1[:, None, :]
    A2, B2 = a2[None, :, :], b2[None, :, :]
    d1 = _orient(A2, B2, A1)
    d2 = _orient(A2, B2, B1)
    d3 = _orient(A1, B1, A2)
    d4 = _orient(A1, B1, B2)
    proper = (((d1 > eps) & (d2 < -eps)) | ((d1 < -eps) & (d2 > eps))) & \
             (((d3 > eps) & (d4 < -eps)) | ((d3 < -eps) & (d4 > eps)))
    return proper


def polyline_min_distance(p1: np.ndarray, p2: np.ndarray, closed1: bool = True,
                          closed2: bool = True, chunk: int = 256) -> float:
    """Minimalna udaljenost izmedju dvije poligonalne linije (0 ako se sijeku)."""
    a1 = p1 if closed1 else p1[:-1]
    b1 = np.roll(p1, -1, axis=0) if closed1 else p1[1:]
    a2 = p2 if closed2 else p2[:-1]
    b2 = np.roll(p2, -1, axis=0) if closed2 else p2[1:]
    best = math.inf
    for i in range(0, len(a1), chunk):
        sa, sb = a1[i:i + chunk], b1[i:i + chunk]
        if segments_intersect_matrix(sa, sb, a2, b2).any():
            return 0.0
        best = min(best, float(point_segments_distance(sa, a2, b2).min()))
    for j in range(0, len(a2), chunk):
        best = min(best, float(point_segments_distance(a2[j:j + chunk], a1, b1).min()))
    return best


def is_simple(poly: np.ndarray) -> bool:
    """Provjera da se zatvoreni poligon ne sijece sam sa sobom (nesusjedne ivice)."""
    a = poly
    b = np.roll(poly, -1, axis=0)
    n = len(poly)
    inter = segments_intersect_matrix(a, b, a, b)
    idx = np.arange(n)
    # iskljuci isti segment i susjedne
    mask = np.ones((n, n), dtype=bool)
    mask[idx, idx] = False
    mask[idx, (idx + 1) % n] = False
    mask[(idx + 1) % n, idx] = False
    return not bool((inter & mask).any())


def densify(poly: np.ndarray, max_len: float, closed: bool = True) -> np.ndarray:
    """Ubacuje tacke tako da nijedna ivica nije duza od max_len."""
    out = []
    n = len(poly)
    count = n if closed else n - 1
    for i in range(count):
        a, b = poly[i], poly[(i + 1) % n]
        k = max(1, int(math.ceil(np.linalg.norm(b - a) / max_len)))
        for j in range(k):
            out.append(a + (b - a) * (j / k))
    if not closed:
        out.append(poly[-1])
    return np.array(out)


# ------------------------------------------------- celije -> konture
def grid_to_polygons(grid: np.ndarray, cell: float, x0: float = 0.0, y0: float = 0.0) -> list[np.ndarray]:
    """Unija popunjenih celija (grid[row, col] = True) -> zatvorene konture.

    Ivice se orijentisu tako da je popunjena celija lijevo (u y-dolje koordinatama
    vanjske konture idu u smjeru kazaljke, rupe obrnuto). Kod dijagonalnog dodira
    celija (sahovnica) konture se razdvajaju deterministicki: skrece se prema
    popunjenoj celiji tekuce ivice. Kolinearne tacke se uklanjaju.
    """
    g = np.asarray(grid, dtype=bool)
    rows, cols = g.shape
    padded = np.zeros((rows + 2, cols + 2), dtype=bool)
    padded[1:-1, 1:-1] = g

    # Usmjerene ivice na mrezi vrhova (x=kolona, y=red), popunjeno lijevo od smjera
    # u y-dolje sistemu: gornja ivica celije ide s desna na lijevo itd.
    edges: dict[tuple[int, int], list[tuple[int, int]]] = {}

    def add(a, b):
        edges.setdefault(a, []).append(b)

    for r in range(rows):
        for c in range(cols):
            if not g[r, c]:
                continue
            pr, pc = r + 1, c + 1
            if not padded[pr - 1, pc]:      # gore prazno -> ivica (c+1,r)->(c,r)
                add((c + 1, r), (c, r))
            if not padded[pr + 1, pc]:      # dolje prazno -> (c,r+1)->(c+1,r+1)
                add((c, r + 1), (c + 1, r + 1))
            if not padded[pr, pc - 1]:      # lijevo prazno -> (c,r)->(c,r+1)
                add((c, r), (c, r + 1))
            if not padded[pr, pc + 1]:      # desno prazno -> (c+1,r+1)->(c+1,r)
                add((c + 1, r + 1), (c + 1, r))

    def turn_rank(d_in, d_out):
        # Prioritet: skretanje "lijevo" u y-dolje sistemu = prema popunjenoj strani.
        cross = d_in[0] * d_out[1] - d_in[1] * d_out[0]
        if cross < 0:
            return 0   # skretanje prema popunjenoj celiji
        if cross == 0:
            return 1
        return 2

    polys: list[np.ndarray] = []
    for start in sorted(edges):
        while edges.get(start):
            loop = [start]
            prev = start
            cur = edges[start].pop(0)
            d_in = (cur[0] - prev[0], cur[1] - prev[1])
            while cur != start:
                loop.append(cur)
                options = edges.get(cur, [])
                if not options:
                    raise RuntimeError("otvorena kontura pri pracenju mreze")
                options.sort(key=lambda p: turn_rank(d_in, (p[0] - cur[0], p[1] - cur[1])))
                nxt = options.pop(0)
                d_in = (nxt[0] - cur[0], nxt[1] - cur[1])
                prev, cur = cur, nxt
            pts = np.array(loop, dtype=float)
            # ukloni kolinearne tacke
            prev_p = np.roll(pts, 1, axis=0)
            next_p = np.roll(pts, -1, axis=0)
            cross = (pts[:, 0] - prev_p[:, 0]) * (next_p[:, 1] - pts[:, 1]) - \
                    (pts[:, 1] - prev_p[:, 1]) * (next_p[:, 0] - pts[:, 0])
            pts = pts[np.abs(cross) > 0]
            polys.append(np.column_stack([x0 + pts[:, 0] * cell, y0 + pts[:, 1] * cell]))
    return polys


def grid_area(grid: np.ndarray, cell: float) -> float:
    return float(np.count_nonzero(grid)) * cell * cell

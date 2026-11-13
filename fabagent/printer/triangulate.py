"""Triangulacija poligona sa rupama: premoscavanje rupa + ear clipping.

Rezultat je deterministican (ista ulazna kontura -> isti trouglovi), sto je bitno
za lijepljenje dijelova mesha po zajednickim stranicama.
"""

from __future__ import annotations

import numpy as np

from fabagent.core.errors import InvalidGeometry
from fabagent.core.geometry2d import ensure_orientation


def _cross(o, a, b) -> float:
    return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])


def _in_wedge(pts, poly, k, m) -> bool:
    """Da li tacka m lezi u unutrasnjem uglu poligona na poziciji k."""
    n = len(poly)
    a, v, b = pts[poly[k - 1]], pts[poly[k]], pts[poly[(k + 1) % n]]
    left1 = _cross(a, v, m) > 0
    left2 = _cross(v, b, m) > 0
    if _cross(a, v, b) > 0:
        return left1 and left2
    return left1 or left2


def _bridge(pts: np.ndarray, poly: list[int], m_idx: int) -> int:
    """Pozicija (u poly) vrha vidljivog iz tacke M desno (Eberly)."""
    M = pts[m_idx]
    n = len(poly)
    best_x, best_k = np.inf, -1
    for k in range(n):
        a, b = pts[poly[k]], pts[poly[(k + 1) % n]]
        # unutrasnjost je lijevo od ivice; desno od M granica ide prema gore
        if a[1] == b[1] or not (a[1] <= M[1] <= b[1]):
            continue
        x = a[0] + (M[1] - a[1]) * (b[0] - a[0]) / (b[1] - a[1])
        if M[0] <= x < best_x:
            best_x, best_k = x, k
    if best_k < 0:
        raise InvalidGeometry("rupa nije unutar vanjske konture (triangulacija)")
    a_k, b_k = best_k, (best_k + 1) % n
    I = np.array([best_x, M[1]])
    cand = a_k if pts[poly[a_k]][0] > pts[poly[b_k]][0] else b_k
    P = pts[poly[cand]]
    if np.allclose(P, I):
        return cand
    # reflex vrhovi unutar trougla M, I, P -> uzmi onaj sa najmanjim uglom prema zraci
    tri = (M, I, P) if _cross(M, I, P) > 0 else (M, P, I)
    best = cand
    best_key = None
    for k in range(n):
        v = pts[poly[k]]
        if k == cand:
            continue
        if _cross(pts[poly[k - 1]], v, pts[poly[(k + 1) % n]]) >= 0:
            continue  # nije reflex
        if (_cross(tri[0], tri[1], v) >= 0 and _cross(tri[1], tri[2], v) >= 0
                and _cross(tri[2], tri[0], v) >= 0):
            d = v - M
            key = (abs(np.arctan2(d[1], d[0])), float(np.hypot(*d)))
            if best_key is None or key < best_key:
                best, best_key = k, key
    # ako se isti vrh pojavljuje vise puta, izaberi poziciju ciji ugao "vidi" M
    target = poly[best]
    occurrences = [k for k in range(n) if poly[k] == target]
    if len(occurrences) > 1:
        for k in occurrences:
            if _in_wedge(pts, poly, k, M):
                return k
    return best


def triangulate(outer: np.ndarray, holes: list[np.ndarray]) -> tuple[np.ndarray, np.ndarray]:
    """Vraca (tacke Nx2, trouglovi Mx3) - trouglovi su CCW."""
    outer = ensure_orientation(np.asarray(outer, float), ccw=True)
    holes = [ensure_orientation(np.asarray(h, float), ccw=False) for h in holes]
    pts = np.vstack([outer] + holes)
    poly = list(range(len(outer)))
    offset = len(outer)
    hole_idx = []
    for h in holes:
        hole_idx.append(list(range(offset, offset + len(h))))
        offset += len(h)
    order = sorted(range(len(holes)), key=lambda i: -pts[hole_idx[i]][:, 0].max())
    for i in order:
        idx = hole_idx[i]
        hp = pts[idx]
        m = int(np.lexsort((hp[:, 1], -hp[:, 0]))[0])
        k = _bridge(pts, poly, idx[m])
        loop = idx[m:] + idx[:m] + [idx[m]]
        poly = poly[:k + 1] + loop + [poly[k]] + poly[k + 1:]
    return pts, _ear_clip(pts, poly)


def _ear_clip(pts: np.ndarray, poly: list[int]) -> np.ndarray:
    scale = float(np.ptp(pts, axis=0).max()) or 1.0
    eps = 1e-12 * scale * scale
    poly = list(poly)
    tris = []
    guard = 0
    i = 0
    while len(poly) > 3:
        n = len(poly)
        ia, ib, ic = poly[(i - 1) % n], poly[i % n], poly[(i + 1) % n]
        a, b, c = pts[ia], pts[ib], pts[ic]
        is_ear = _cross(a, b, c) > eps
        if is_ear:
            others = np.array([poly[j] for j in range(n) if j not in ((i - 1) % n, i % n, (i + 1) % n)])
            if len(others):
                q = pts[others]
                same = (np.all(q == a, axis=1) | np.all(q == b, axis=1) | np.all(q == c, axis=1))
                q = q[~same]
                if len(q):
                    d1 = (b[0] - a[0]) * (q[:, 1] - a[1]) - (b[1] - a[1]) * (q[:, 0] - a[0])
                    d2 = (c[0] - b[0]) * (q[:, 1] - b[1]) - (c[1] - b[1]) * (q[:, 0] - b[0])
                    d3 = (a[0] - c[0]) * (q[:, 1] - c[1]) - (a[1] - c[1]) * (q[:, 0] - c[0])
                    inside = (d1 >= -eps) & (d2 >= -eps) & (d3 >= -eps)
                    is_ear = not inside.any()
        if is_ear:
            tris.append((ia, ib, ic))
            del poly[i % n]
            guard = 0
            i = (i - 1) % len(poly) if i % n != 0 else 0
            continue
        i = (i + 1) % n
        guard += 1
        if guard > n:
            raise InvalidGeometry("triangulacija nije uspjela (degenerisan poligon)")
    a, b, c = (pts[k] for k in poly)
    if _cross(a, b, c) <= eps:
        raise InvalidGeometry("triangulacija: zadnji trougao je degenerisan")
    tris.append(tuple(poly))
    return np.array(tris, dtype=np.int64)

"""Bulove operacije (spajanje, presjek, oduzimanje) i offset nad zatvorenim poligonima.

Ulaz i izlaz su prstenovi (nizovi tacaka u mm) sa even-odd pravilom popune - rupa je zaseban prsten.
Krivulje se prije poziva pretvaraju u kratke ravne stranice (to radi radni ekran).

Postupak (isti za sve operacije):
  1. sve stranice se medjusobno presijeku i podijele u pod-stranice (i kolinearna preklapanja),
     identicne pod-stranice se spoje u jednu;
  2. za svaku pod-stranicu se ispita tacka malo lijevo i malo desno od sredine: ako je rezultat
     operacije sa jedne strane "unutra", a sa druge "vani", pod-stranica je dio nove konture;
  3. usmjerene pod-stranice (unutrasnjost lijevo) se nizu u zatvorene konture.
Zbog koraka 2 zajednicke ivice i dodiri u tacki nisu poseban slucaj.

Offset koristi isti postupak nad "sirovim" paralelnim linijama i lukovima, a "unutra" se racuna iz
udaljenosti do originalne konture - zato se samopresjeci sirovog offseta sami odbace.
"""

from __future__ import annotations

import math
from collections import defaultdict

import numpy as np

from fabagent.core.errors import InvalidGeometry, InvalidParam

KEY_DECIMALS = 6             # tjemena blize od 1e-6 mm su isto tjeme
SNAP_TOL_MM = 1e-7           # presjek blizi od ovoga kraju stranice = kraj stranice
SIDE_EPS_MM = 1e-4           # probna tacka lijevo/desno od stranice (Bulove operacije)
MIN_AREA_MM2 = 1e-8          # konture manje povrsine se odbacuju
OFFSET_ARC_TOL_MM = 0.005    # najvece odstupanje zaobljenog ugla od idealnog kruga (prema van)
MIN_OFFSET_MM = 0.01         # najmanja udaljenost offseta
MAX_EDGES = 6000             # granica broja stranica u jednoj operaciji (memorija / vrijeme)
PAIR_BUDGET = 200_000        # broj parova stranica u jednom numpy bloku
OPS = ("union", "intersection", "difference", "xor")


def _key(x: float, y: float) -> tuple[float, float]:
    return (round(float(x), KEY_DECIMALS), round(float(y), KEY_DECIMALS))


def signed_area(ring) -> float:
    ring = np.asarray(ring, dtype=np.float64)
    x, y = ring[:, 0], ring[:, 1]
    return 0.5 * float(np.dot(x, np.roll(y, -1)) - np.dot(np.roll(x, -1), y))


def area(rings: list[np.ndarray]) -> float:
    """Povrsina even-odd oblasti koju vraca ovaj modul (rupe imaju suprotan smjer od vanjskih kontura)."""
    return abs(sum(signed_area(r) for r in rings))


def prepare(raw, what: str = "oblik") -> list[np.ndarray]:
    """Provjeri i ocisti prstenove: brojevi, bar 3 razlicite tacke, bez uzastopnih duplikata."""
    if not isinstance(raw, (list, tuple)) or not raw:
        raise InvalidParam(f"{what}: potrebna je lista zatvorenih kontura")
    rings = []
    for r in raw:
        try:
            a = np.asarray(r, dtype=np.float64)
        except (TypeError, ValueError):
            raise InvalidParam(f"{what}: tacke moraju biti brojevi [x, y]") from None
        if a.ndim != 2 or a.shape[1] != 2 or not np.isfinite(a).all():
            raise InvalidParam(f"{what}: tacke moraju biti brojevi [x, y]")
        keep = [0] + [i for i in range(1, len(a)) if _key(*a[i]) != _key(*a[i - 1])]
        a = a[keep]
        if len(a) > 1 and _key(*a[0]) == _key(*a[-1]):
            a = a[:-1]
        if len(a) >= 3 and abs(signed_area(a)) > MIN_AREA_MM2:
            rings.append(a)
    if not rings:
        raise InvalidGeometry(f"{what} nema nijednu zatvorenu konturu sa povrsinom", "geometrija")
    return rings


def _edges(rings: list[np.ndarray]) -> tuple[np.ndarray, np.ndarray]:
    a = np.vstack(rings)
    b = np.vstack([np.roll(r, -1, axis=0) for r in rings])
    return a, b


def _limit(n: int) -> None:
    if n > MAX_EDGES:
        raise InvalidGeometry(f"oblik ima {n} stranica (najvise {MAX_EDGES}) - pojednostavi ga ili ga podijeli",
                              "geometrija")


# ------------------------------------------------------------------ 1. podjela stranica
def _arrange(A: np.ndarray, B: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    D = B - A
    L = np.hypot(D[:, 0], D[:, 1])
    ok = L > SNAP_TOL_MM
    A, B, D, L = A[ok], B[ok], D[ok], L[ok]
    n = len(A)
    _limit(n)
    cuts: list[list[tuple[float, float, float]]] = [[] for _ in range(n)]
    tol = SNAP_TOL_MM
    step = max(1, PAIR_BUDGET // max(n, 1))
    cols = np.arange(n)[None, :]
    for i0 in range(0, n, step):
        i1 = min(n, i0 + step)
        ax, ay = A[i0:i1, 0][:, None], A[i0:i1, 1][:, None]
        dx, dy = D[i0:i1, 0][:, None], D[i0:i1, 1][:, None]
        li, lj = L[i0:i1][:, None], L[None, :]
        ex, ey = D[None, :, 0], D[None, :, 1]
        wx, wy = A[None, :, 0] - ax, A[None, :, 1] - ay
        den = dx * ey - dy * ex
        par = np.abs(den) <= 1e-10 * li * lj
        upper = cols > np.arange(i0, i1)[:, None]            # svaki par samo jednom (j > i)
        with np.errstate(divide="ignore", invalid="ignore"):
            t = (wx * ey - wy * ex) / den
            u = (wx * dy - wy * dx) / den
            hit = upper & ~par & (t > -tol / li) & (t < 1 + tol / li) & (u > -tol / lj) & (u < 1 + tol / lj)
        ii, jj = np.nonzero(hit)
        if len(ii):
            gi = ii + i0
            tv, uv = np.clip(t[ii, jj], 0.0, 1.0), np.clip(u[ii, jj], 0.0, 1.0)
            P = A[gi] + tv[:, None] * D[gi]
            for end in (A[gi], B[gi], A[jj], B[jj]):         # presjek tik uz kraj stranice = taj kraj
                near = np.hypot(P[:, 0] - end[:, 0], P[:, 1] - end[:, 1]) <= tol
                P[near] = end[near]
            in_i = (tv * L[gi] > tol) & ((1 - tv) * L[gi] > tol)
            in_j = (uv * L[jj] > tol) & ((1 - uv) * L[jj] > tol)
            for k in range(len(gi)):
                if in_i[k]:
                    cuts[gi[k]].append((float(tv[k]), float(P[k, 0]), float(P[k, 1])))
                if in_j[k]:
                    cuts[jj[k]].append((float(uv[k]), float(P[k, 0]), float(P[k, 1])))
        col = upper & par & (np.abs(wx * dy - wy * dx) <= tol * li)   # kolinearne stranice
        ii, jj = np.nonzero(col)
        for i, j in zip((ii + i0).tolist(), jj.tolist()):
            for a_, b_ in ((i, j), (j, i)):                  # krajevi b_ koji leze unutar a_
                for p in (A[b_], B[b_]):
                    s = float(np.dot(p - A[a_], D[a_])) / L[a_]
                    if tol < s < L[a_] - tol:
                        cuts[a_].append((s / L[a_], float(p[0]), float(p[1])))
    canon: dict[tuple, tuple[float, float]] = {}
    seen: dict[tuple, None] = {}
    for i in range(n):
        seq = [(0.0, A[i, 0], A[i, 1]), *sorted(cuts[i]), (1.0, B[i, 0], B[i, 1])]
        prev = None
        for _, x, y in seq:
            k = _key(x, y)
            canon.setdefault(k, (float(x), float(y)))
            if prev is not None and k != prev:
                seen.setdefault((prev, k) if prev < k else (k, prev))
            prev = k
    if not seen:
        return np.zeros((0, 2)), np.zeros((0, 2))
    return (np.array([canon[a] for a, _ in seen], float), np.array([canon[b] for _, b in seen], float))


# ------------------------------------------------------------------ 2. unutra / vani
def even_odd(P: np.ndarray, A: np.ndarray, B: np.ndarray) -> np.ndarray:
    """Za svaku tacku P: da li je unutar oblasti (even-odd) omedjene stranicama A->B."""
    out = np.zeros(len(P), dtype=bool)
    if not len(A):
        return out
    ax, ay, bx, by = A[None, :, 0], A[None, :, 1], B[None, :, 0], B[None, :, 1]
    step = max(1, PAIR_BUDGET // len(A))
    for k0 in range(0, len(P), step):
        px, py = P[k0:k0 + step, 0][:, None], P[k0:k0 + step, 1][:, None]
        cond = (ay > py) != (by > py)
        with np.errstate(divide="ignore", invalid="ignore"):
            xint = ax + (py - ay) * (bx - ax) / (by - ay)
        out[k0:k0 + step] = (np.count_nonzero(cond & (px < xint), axis=1) % 2) == 1
    return out


def winding(P: np.ndarray, A: np.ndarray, B: np.ndarray) -> np.ndarray:
    """Broj obilazaka stranica A->B oko svake tacke P. Za poligone istog smjera to je broj poligona
    koji sadrze tacku, pa je != 0 isto sto i "tacka je u uniji poligona"."""
    out = np.zeros(len(P), dtype=np.int64)
    if not len(A):
        return out
    ax, ay, bx, by = A[None, :, 0], A[None, :, 1], B[None, :, 0], B[None, :, 1]
    step = max(1, PAIR_BUDGET // len(A))
    for k0 in range(0, len(P), step):
        px, py = P[k0:k0 + step, 0][:, None], P[k0:k0 + step, 1][:, None]
        left = (bx - ax) * (py - ay) - (px - ax) * (by - ay)
        up = (ay <= py) & (by > py) & (left > 0)
        down = (ay > py) & (by <= py) & (left < 0)
        out[k0:k0 + step] = np.count_nonzero(up, axis=1) - np.count_nonzero(down, axis=1)
    return out


# ------------------------------------------------------------------ 3. konture
def _trace(SA: np.ndarray, SB: np.ndarray, inside, eps: float) -> list[np.ndarray]:
    if not len(SA):
        return []
    d = SB - SA
    ln = np.hypot(d[:, 0], d[:, 1])[:, None]
    nrm = np.column_stack([-d[:, 1], d[:, 0]]) / ln
    mid = (SA + SB) / 2.0
    fl, fr = inside(mid + eps * nrm), inside(mid - eps * nrm)
    fwd, rev = fl & ~fr, fr & ~fl
    S = np.vstack([SA[fwd], SB[rev]])
    E = np.vstack([SB[fwd], SA[rev]])
    return _clean(_stitch(S, E))


def _stitch(S: np.ndarray, E: np.ndarray) -> list[np.ndarray]:
    m = len(S)
    ks = [_key(*p) for p in S]
    ke = [_key(*p) for p in E]
    ang = np.arctan2(E[:, 1] - S[:, 1], E[:, 0] - S[:, 0])
    used = [False] * m
    while True:                                  # odbaci slijepe stranice (bez nastavka ili bez prethodnika)
        starts = {ks[i] for i in range(m) if not used[i]}
        ends = {ke[i] for i in range(m) if not used[i]}
        dead = [i for i in range(m) if not used[i] and (ke[i] not in starts or ks[i] not in ends)]
        if not dead:
            break
        for i in dead:
            used[i] = True
    out = defaultdict(list)
    for i, k in enumerate(ks):
        if not used[i]:
            out[k].append(i)
    two_pi = 2 * math.pi
    rings = []
    for i in range(m):
        if used[i]:
            continue
        loop, cur, closed = [i], i, False
        used[i] = True
        while True:
            k = ke[cur]
            if k == ks[i]:
                closed = True
                break
            cands = [e for e in out.get(k, ()) if not used[e]]
            if not cands:
                break
            back = ang[cur] + math.pi                       # prva izlazna stranica u smjeru kazaljke od povratka

            def turn(e, back=back):
                a = (back - ang[e]) % two_pi
                return a if a > 1e-9 else two_pi
            cur = min(cands, key=turn)
            used[cur] = True
            loop.append(cur)
        if closed and len(loop) >= 3:
            rings.append(S[loop])
    return rings


def _clean(rings: list[np.ndarray]) -> list[np.ndarray]:
    """Ukloni tacke koje leze na pravoj izmedju susjeda i konture bez povrsine."""
    out = []
    for r in rings:
        for _ in range(3):
            p, q = np.roll(r, 1, axis=0), np.roll(r, -1, axis=0)
            v1, v2 = r - p, q - r
            cr = np.abs(v1[:, 0] * v2[:, 1] - v1[:, 1] * v2[:, 0])
            keep = cr > 1e-9 * np.maximum(np.hypot(*v1.T) * np.hypot(*v2.T), 1e-30)
            if keep.all() or keep.sum() < 3:
                break
            r = r[keep]
        if len(r) >= 3 and abs(signed_area(r)) > MIN_AREA_MM2:
            out.append(r)
    return out


# ------------------------------------------------------------------ javne funkcije
def boolean(a, b, op: str) -> list[np.ndarray]:
    """a, b: liste prstenova (even-odd). op: union | intersection | difference (a - b) | xor."""
    if op not in OPS:
        raise InvalidParam(f"operacija: '{op}' nije dozvoljeno, izbor: {list(OPS)}")
    ra, rb = prepare(a, "prvi oblik"), prepare(b, "drugi oblik")
    a1, b1 = _edges(ra)
    a2, b2 = _edges(rb)
    SA, SB = _arrange(np.vstack([a1, a2]), np.vstack([b1, b2]))

    def inside(P):
        ia, ib = even_odd(P, a1, b1), even_odd(P, a2, b2)
        return {"union": ia | ib, "intersection": ia & ib, "difference": ia & ~ib, "xor": ia ^ ib}[op]
    return _trace(SA, SB, inside, SIDE_EPS_MM)


def union(shapes) -> list[np.ndarray]:
    """Spajanje proizvoljnog broja oblika (svaki oblik je lista prstenova)."""
    if not isinstance(shapes, (list, tuple)) or len(shapes) < 2:
        raise InvalidParam("spajanje: potrebna su bar 2 oblika")
    edges = [_edges(prepare(s, f"oblik {i + 1}")) for i, s in enumerate(shapes)]
    SA, SB = _arrange(np.vstack([e[0] for e in edges]), np.vstack([e[1] for e in edges]))

    def inside(P):
        res = np.zeros(len(P), dtype=bool)
        for ea, eb in edges:
            res |= even_odd(P, ea, eb)
        return res
    return _trace(SA, SB, inside, SIDE_EPS_MM)


def offset(rings, distance: float) -> list[np.ndarray]:
    """distance > 0: prosirenje (prema van), < 0: suzenje (prema unutra). Uglovi prema van su zaobljeni."""
    try:
        dist = float(distance)
    except (TypeError, ValueError):
        raise InvalidParam("offset: udaljenost mora biti broj (mm)") from None
    if not math.isfinite(dist) or abs(dist) < MIN_OFFSET_MM:
        raise InvalidParam(f"offset: udaljenost mora biti bar {MIN_OFFSET_MM:g} mm (plus ili minus)")
    src = prepare(rings, "oblik")
    ea, eb = _edges(src)
    d = abs(dist)
    # Pojas sirine d oko konture = unija pravougaonika oko stranica i lepeza (opisanih lukova) na
    # vanjskoj strani svakog skretanja. Sve su to poligoni, pa je ispitivanje "unutra" tacno.
    step = 2 * math.acos(d / (d + OFFSET_ARC_TOL_MM))
    cover: list[np.ndarray] = []          # zatvoreni poligoni pojasa (za ispitivanje unutra/vani)
    ra, rb = [], []                       # stranice koje mogu biti dio nove konture
    for ring in src:
        nxt = np.roll(ring, -1, axis=0)
        u = nxt - ring
        u /= np.hypot(u[:, 0], u[:, 1])[:, None]
        nrm = np.column_stack([-u[:, 1], u[:, 0]])
        for sgn in (1.0, -1.0):
            ra.append(ring + sgn * d * nrm)
            rb.append(nxt + sgn * d * nrm)
        for i in range(len(ring)):
            cover.append(np.array([ring[i] + d * nrm[i], ring[i] - d * nrm[i],
                                   nxt[i] - d * nrm[i], nxt[i] + d * nrm[i]]))
            n1, n2 = nrm[i - 1], nrm[i]
            turn = n1[0] * n2[1] - n1[1] * n2[0]
            dot = float(np.dot(n1, n2))
            if abs(turn) < 1e-12 and dot > 0:
                continue                                     # nema skretanja
            sgn = -1.0 if turn > 0 else 1.0                  # vanjska strana skretanja
            sweep = math.atan2(turn, dot) if abs(turn) >= 1e-12 else math.pi
            a0 = math.atan2(sgn * n1[1], sgn * n1[0])
            k = max(1, int(math.ceil(abs(sweep) / step)))
            dth = sweep / k
            th = a0 + dth * (np.arange(k) + 0.5)
            corners = ring[i] + (d / math.cos(dth / 2)) * np.column_stack([np.cos(th), np.sin(th)])
            fan = np.vstack([ring[i] + sgn * d * n1, corners, ring[i] + sgn * d * n2])
            ra.append(fan[:-1])
            rb.append(fan[1:])
            cover.append(np.vstack([ring[i][None, :], fan]))
    cover = [c if signed_area(c) > 0 else c[::-1] for c in cover]
    ca, cb = _edges(cover)
    SA, SB = _arrange(np.vstack(ra), np.vstack(rb))

    def inside(P):
        inp, band = even_odd(P, ea, eb), winding(P, ca, cb) != 0
        return (inp | band) if dist > 0 else (inp & ~band)
    return _trace(SA, SB, inside, SIDE_EPS_MM)

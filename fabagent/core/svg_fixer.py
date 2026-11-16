"""Validacija i automatski popravak SVG fajla koji korisnik uploaduje.

Pravila (deterministicki, sve granice u config.py):
  * <image> (raster)                       -> odbijanje (ValidationFailed)
  * DOCTYPE/ENTITY, ne-SVG, prevelik fajl  -> odbijanje
  * jedinice: mm/cm/in/pt/pc/px; px = 1/96 in po SVG/CSS standardu (biljezi se)
  * <text>                                 -> putanje ugradjenim blok-fontom (ako su svi
                                              znakovi podrzani), inace blokirajuca greska
  * otvorene putanje ciji su krajevi < UPLOAD_JOIN_TOL_MM -> spajaju se / zatvaraju
  * duple putanje (unutar UPLOAD_DUP_TOL_MM) -> uklanjaju se
  * sloj: popunjen oblik -> ENGRAVE, plava linija -> SCORE, ostale linije -> CUT;
    stroke-width 0.1 mm i boje slojeva postavlja postojeci SVG generator.
Sve sto se ne moze popraviti bez pretpostavke ide u listu ``blocking`` i
onemogucava odobravanje (nista se ne brise tiho).
"""

from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

import numpy as np

from fabagent.core import config as C
from fabagent.core import geometry2d as g2
from fabagent.core.errors import FabError, ValidationFailed

SVG_NS = "http://www.w3.org/2000/svg"
_UNIT_MM = {
    "mm": 1.0, "cm": 10.0, "in": C.MM_PER_IN, "pt": C.MM_PER_IN / C.PT_PER_IN,
    "pc": C.MM_PER_IN / 6.0, "px": C.MM_PER_IN / C.CSS_PX_PER_IN, "": C.MM_PER_IN / C.CSS_PX_PER_IN,
}
_LEN_RE = re.compile(r"^\s*([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)\s*([a-z%]*)\s*$")
_NUM_RE = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")
_SKIP = {"defs", "metadata", "title", "desc", "style", "namedview", "clipPath", "mask",
         "pattern", "symbol", "linearGradient", "radialGradient", "marker", "filter", "script"}
_UNSUPPORTED = {"use", "foreignObject", "switch"}
_SCORE_COLORS = {"#0000ff", "#00f", "blue"}


@dataclass
class FixReport:
    fixes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    blocking: list[str] = field(default_factory=list)
    stats: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {"fixes": self.fixes, "warnings": self.warnings, "blocking": self.blocking, "stats": self.stats}


@dataclass
class _Shape:
    layer: str
    points: np.ndarray       # mm
    closed: bool
    source: str


@dataclass
class FixResult:
    width_mm: float
    height_mm: float
    report: FixReport
    layers: dict              # layer -> list[np.ndarray] (zatvorene konture)
    open_paths: list          # nerijesene otvorene putanje (mm)
    design: object | None     # LaserDesign kada nema blokirajucih gresaka
    svg: str                  # popravljeni SVG (produkcijski) ili pregled sa greskama

    @property
    def production_ready(self) -> bool:
        return not self.report.blocking and self.design is not None


# ------------------------------------------------------------- pomocnici
def _tag(el) -> str:
    return el.tag.split("}", 1)[1] if isinstance(el.tag, str) and "}" in el.tag else str(el.tag)


def _length_mm(value: str | None) -> tuple[float | None, str]:
    if value is None:
        return None, ""
    m = _LEN_RE.match(value)
    if not m:
        return None, "?"
    return float(m.group(1)), m.group(2)


def _parse_transform(text: str | None) -> np.ndarray:
    M = np.eye(3)
    if not text:
        return M
    for name, args in re.findall(r"(\w+)\s*\(([^)]*)\)", text):
        a = [float(v) for v in _NUM_RE.findall(args)]
        if name == "matrix" and len(a) == 6:
            T = np.array([[a[0], a[2], a[4]], [a[1], a[3], a[5]], [0, 0, 1]])
        elif name == "translate":
            T = np.array([[1, 0, a[0]], [0, 1, a[1] if len(a) > 1 else 0], [0, 0, 1]])
        elif name == "scale":
            sx = a[0]
            sy = a[1] if len(a) > 1 else sx
            T = np.diag([sx, sy, 1.0])
        elif name == "rotate":
            r = math.radians(a[0])
            R = np.array([[math.cos(r), -math.sin(r), 0], [math.sin(r), math.cos(r), 0], [0, 0, 1]])
            if len(a) == 3:
                T = np.array([[1, 0, a[1]], [0, 1, a[2]], [0, 0, 1]]) @ R @ np.array(
                    [[1, 0, -a[1]], [0, 1, -a[2]], [0, 0, 1]])
            else:
                T = R
        elif name == "skewX":
            T = np.array([[1, math.tan(math.radians(a[0])), 0], [0, 1, 0], [0, 0, 1]])
        elif name == "skewY":
            T = np.array([[1, 0, 0], [math.tan(math.radians(a[0])), 1, 0], [0, 0, 1]])
        else:
            raise ValueError(f"nepoznata transformacija '{name}'")
        M = M @ T
    return M


def _style(el, inherited: dict) -> dict:
    st = dict(inherited)
    for k in ("fill", "stroke", "display", "visibility", "font-size", "text-anchor", "font-family"):
        if el.get(k) is not None:
            st[k] = el.get(k).strip()
    for part in (el.get("style") or "").split(";"):
        if ":" in part:
            k, v = part.split(":", 1)
            st[k.strip()] = v.strip()
    return st


def _layer_for(st: dict, tag: str = "path") -> str:
    fill = (st.get("fill") or "black").lower()   # SVG default fill je crna
    if tag == "line":
        fill = "none"                            # <line> po SVG pravilu nema ispunu
    stroke = (st.get("stroke") or "none").lower()
    if fill not in ("none", "transparent"):
        return "ENGRAVE"
    if stroke in _SCORE_COLORS:
        return "SCORE"
    return "CUT"


def _f(el, name, default=0.0) -> float:
    v, _ = _length_mm(el.get(name))
    return default if v is None else v


def _shape_to_d(el, tag: str) -> str | None:
    if tag == "path":
        return el.get("d")
    if tag == "rect":
        x, y, w, h = _f(el, "x"), _f(el, "y"), _f(el, "width"), _f(el, "height")
        if w <= 0 or h <= 0:
            return None
        rx, ry = el.get("rx"), el.get("ry")
        rx = _f(el, "rx") if rx is not None else (_f(el, "ry") if ry is not None else 0.0)
        ry = _f(el, "ry") if ry is not None else rx
        rx, ry = min(rx, w / 2), min(ry, h / 2)
        if rx <= 0 or ry <= 0:
            return f"M {x} {y} H {x + w} V {y + h} H {x} Z"
        return (f"M {x + rx} {y} H {x + w - rx} A {rx} {ry} 0 0 1 {x + w} {y + ry} V {y + h - ry} "
                f"A {rx} {ry} 0 0 1 {x + w - rx} {y + h} H {x + rx} A {rx} {ry} 0 0 1 {x} {y + h - ry} "
                f"V {y + ry} A {rx} {ry} 0 0 1 {x + rx} {y} Z")
    if tag in ("circle", "ellipse"):
        cx, cy = _f(el, "cx"), _f(el, "cy")
        rx = _f(el, "r") if tag == "circle" else _f(el, "rx")
        ry = _f(el, "r") if tag == "circle" else _f(el, "ry")
        if rx <= 0 or ry <= 0:
            return None
        return (f"M {cx + rx} {cy} A {rx} {ry} 0 0 1 {cx} {cy + ry} A {rx} {ry} 0 0 1 {cx - rx} {cy} "
                f"A {rx} {ry} 0 0 1 {cx} {cy - ry} A {rx} {ry} 0 0 1 {cx + rx} {cy} Z")
    if tag == "line":
        return f"M {_f(el, 'x1')} {_f(el, 'y1')} L {_f(el, 'x2')} {_f(el, 'y2')}"
    if tag in ("polyline", "polygon"):
        nums = [float(v) for v in _NUM_RE.findall(el.get("points") or "")]
        if len(nums) < 4:
            return None
        pts = " ".join(f"{nums[i]} {nums[i + 1]}" for i in range(0, len(nums) - 1, 2))
        return f"M {pts}" + (" Z" if tag == "polygon" else "")
    return None


def _flatten_d(d: str, M: np.ndarray, mm_per_unit: float) -> list[tuple[np.ndarray, bool]]:
    """Putanja -> lista (tacke u mm, zatvorena). Krivulje se uzorkuju korakom iz config.py."""
    from svgpathtools import Line, parse_path   # uvoz tek kada treba (scipy je spor za uvoz)
    path = parse_path(d)
    out = []
    for sub in path.continuous_subpaths():
        pts = []
        for seg in sub:
            if isinstance(seg, Line):
                ts = np.array([0.0, 1.0])
            else:
                length_mm = seg.length() * mm_per_unit * float(np.sqrt(abs(np.linalg.det(M[:2, :2]))) or 1.0)
                n = max(2, int(math.ceil(length_mm / C.UPLOAD_CURVE_STEP_MM)))
                ts = np.linspace(0.0, 1.0, n + 1)
            z = np.array([seg.point(t) for t in ts])
            seg_pts = np.column_stack([z.real, z.imag])
            pts.append(seg_pts if not pts else seg_pts[1:])
        if not pts:
            continue
        P = np.vstack(pts)
        P = (np.column_stack([P, np.ones(len(P))]) @ M.T)[:, :2] * mm_per_unit
        closed = bool(sub.isclosed())
        out.append((P, closed))
    return out


def _clean(points: np.ndarray, closed: bool) -> np.ndarray:
    keep = [0]
    for i in range(1, len(points)):
        if np.linalg.norm(points[i] - points[keep[-1]]) > C.GEOMETRY_EPS_MM:
            keep.append(i)
    p = points[keep]
    if closed and len(p) > 1 and np.linalg.norm(p[-1] - p[0]) <= C.UPLOAD_JOIN_TOL_MM:
        p = p[:-1]
    return p


def _is_duplicate(a: np.ndarray, b: np.ndarray, a_closed: bool, b_closed: bool) -> bool:
    tol = C.UPLOAD_DUP_TOL_MM
    if np.abs(a.min(0) - b.min(0)).max() > tol or np.abs(a.max(0) - b.max(0)).max() > tol:
        return False
    da = g2.densify(a, 0.5, closed=a_closed)
    db = g2.densify(b, 0.5, closed=b_closed)
    return (g2.min_distance_points_to_polyline(da, b, b_closed) <= tol and
            _max_dist(da, b, b_closed) <= tol and _max_dist(db, a, a_closed) <= tol)


def _max_dist(points: np.ndarray, poly: np.ndarray, closed: bool) -> float:
    a = poly if closed else poly[:-1]
    b = np.roll(poly, -1, axis=0) if closed else poly[1:]
    if len(a) == 0:
        return float(np.linalg.norm(points - poly[0], axis=1).max())
    return float(g2.point_segments_distance(points, a, b).min(axis=1).max())


def _join_open(paths: list[np.ndarray]) -> tuple[list[np.ndarray], list[np.ndarray], int]:
    """Greedy spajanje otvorenih putanja ciji su krajevi blizi od tolerancije.
    Vraca (zatvorene, i dalje otvorene, broj spojeva)."""
    tol = C.UPLOAD_JOIN_TOL_MM
    pool = [p.copy() for p in paths]
    closed, still_open, joins = [], [], 0
    while pool:
        chain = pool.pop(0)
        grown = True
        while grown:
            grown = False
            if len(chain) > 2 and np.linalg.norm(chain[-1] - chain[0]) <= tol:
                break
            best = None
            for idx, other in enumerate(pool):
                for end_c, end_o in ((-1, 0), (-1, -1), (0, -1), (0, 0)):
                    d = float(np.linalg.norm(chain[end_c] - other[end_o]))
                    if d <= tol and (best is None or d < best[0]):
                        best = (d, idx, end_c, end_o)
            if best is not None:
                _, idx, end_c, end_o = best
                other = pool.pop(idx)
                if end_c == -1:
                    chain = np.vstack([chain, other if end_o == 0 else other[::-1]])
                else:
                    chain = np.vstack([other if end_o == -1 else other[::-1], chain])
                joins += 1
                grown = True
        if len(chain) > 2 and np.linalg.norm(chain[-1] - chain[0]) <= tol:
            closed.append(_clean(chain, True))
        else:
            still_open.append(chain)
    return closed, still_open, joins


# ------------------------------------------------------------- glavna funkcija
def _reject(name: str, problem: str):
    raise ValidationFailed(name, [problem])


def fix_svg(data: bytes, filename: str = "upload.svg") -> FixResult:
    from fabagent.laser.design import LaserDesign, check_design
    from fabagent.laser.font import GLYPHS, render_text

    if len(data) > C.UPLOAD_MAX_BYTES:
        _reject(filename, f"fajl je veci od {C.UPLOAD_MAX_BYTES // (1024 * 1024)} MB")
    # XXE i "billion laughs" dolaze iskljucivo preko unutrasnjeg podskupa
    # (<!DOCTYPE svg [ ... ]>) i preko <!ENTITY>. Obican DOCTYPE koji samo
    # pokazuje na vanjski DTD (tako izvoze CorelDRAW i Illustrator) ne nosi
    # nikakav rizik - ElementTree vanjski DTD ne dohvata. Ranije je ovdje bila
    # provjera "ima li '[' u prvih 2048 bajta poslije DOCTYPE-a", pa je padao
    # svaki komercijalni paket cim bi se '[' pojavilo bilo gdje u stilu ili
    # putanji. Sada se gleda samo da li '[' dolazi PRIJE kraja same deklaracije.
    nisko = data.lower()
    if b"<!entity" in nisko:
        _reject(filename, "ENTITY deklaracije nisu dozvoljene")
    i = nisko.find(b"<!doctype")
    if i >= 0:
        kraj = data.find(b">", i)
        uglasta = data.find(b"[", i)
        if uglasta != -1 and (kraj == -1 or uglasta < kraj):
            _reject(filename, "DOCTYPE sa unutrasnjim deklaracijama nije dozvoljen")
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        _reject(filename, f"XML nije parsabilan: {exc}")
    if _tag(root) != "svg":
        _reject(filename, "fajl nije SVG (korijenski element nije <svg>)")
    if any(_tag(el) == "image" for el in root.iter()):
        _reject(filename, "SVG sadrzi ugradjenu sliku (<image>), a ovuda idu samo linije. "
                "Za graviranje slike posalji PNG ili JPG kroz Upload.")

    rep = FixReport()
    # --- jedinice i velicina dokumenta
    vb = [float(v) for v in _NUM_RE.findall(root.get("viewBox") or "")]
    if root.get("viewBox") is not None and len(vb) != 4:
        _reject(filename, "viewBox nije ispravan")
    dims = {}
    for attr, vbi in (("width", 2), ("height", 3)):
        val, unit = _length_mm(root.get(attr))
        if unit == "?" or unit == "%":
            _reject(filename, f"{attr}={root.get(attr)!r}: dimenzije moraju biti apsolutne (mm, cm, in, pt, px)")
        if val is None:
            if not vb:
                _reject(filename, "SVG nema width/height ni viewBox - velicina nije poznata")
            val, unit = vb[vbi], "px"
            rep.warnings.append(f"{attr} nije zadan; uzeta vrijednost iz viewBox u px (1 px = 1/96 in)")
        if unit not in _UNIT_MM:
            _reject(filename, f"nepoznata jedinica '{unit}'")
        if unit in ("", "px"):
            rep.warnings.append(f"{attr} je u px: preracunato 1 px = 1/96 in po SVG standardu "
                                "(fajlovi iz starijeg Illustratora koriste 72 dpi - provjeri dimenzije)")
        dims[attr] = val * _UNIT_MM[unit]
    W, H = dims["width"], dims["height"]
    if vb:
        sx, sy = W / vb[2], H / vb[3]
        if abs(sx - sy) > 1e-6 * max(sx, sy):
            rep.blocking.append("viewBox i width/height nemaju isti omjer - skaliranje nije jednoznacno")
        base = np.array([[sx, 0, -vb[0] * sx], [0, sx, -vb[1] * sx], [0, 0, 1]])
    else:
        base = np.diag([_UNIT_MM["px"], _UNIT_MM["px"], 1.0])
    rep.stats["document_mm"] = [round(W, 3), round(H, 3)]

    # --- obilazak stabla
    shapes: list[_Shape] = []
    counters = {"elementi": 0, "tekst": 0, "skriveno": 0}

    def walk(el, M, st):
        tag = _tag(el)
        if tag in _SKIP:
            return
        if tag in _UNSUPPORTED:
            rep.blocking.append(f"element <{tag}> nije podrzan - u editoru ga razgradi (npr. Unlink Clone)")
            return
        try:
            M = M @ _parse_transform(el.get("transform"))
        except ValueError as exc:
            rep.blocking.append(f"<{tag}>: {exc}")
            return
        st = _style(el, st)
        if st.get("display") == "none" or st.get("visibility") == "hidden":
            counters["skriveno"] += 1
            return
        if tag == "text":
            handle_text(el, M, st)
            return
        d = _shape_to_d(el, tag)
        if d:
            counters["elementi"] += 1
            layer = _layer_for(st, tag)
            try:
                subs = _flatten_d(d, M, 1.0)
            except Exception as exc:  # svgpathtools: neispravan d
                rep.blocking.append(f"<{tag} id={el.get('id')!r}>: putanja nije citljiva ({exc})")
                return
            for pts, closed in subs:
                if layer == "ENGRAVE" and not closed:
                    closed = True   # popunjen oblik se po SVG pravilu zatvara
                shapes.append(_Shape(layer, pts, closed, f"<{tag} id={el.get('id')!r}>"))
        for child in el:
            walk(child, M, st)

    def handle_text(el, M, st):
        text = "".join(el.itertext()).strip()
        if not text:
            return
        counters["tekst"] += 1
        bad = sorted({c for c in text if c not in GLYPHS})
        if bad:
            rep.blocking.append(
                f"tekst {text!r}: znakovi {bad!r} nisu u ugradjenom fontu (samo VELIKA slova, cifre i osnovni znakovi); "
                "pretvori tekst u putanje u editoru (Inkscape: Path > Object to Path)")
            return
        size, unit = _length_mm(st.get("font-size") or "16px")
        if size is None or unit not in ("", "px"):
            if unit in _UNIT_MM and size is not None:
                size = size * _UNIT_MM[unit] / _UNIT_MM["px"]
            else:
                rep.blocking.append(f"tekst {text!r}: font-size {st.get('font-size')!r} nije podrzan")
                return
        x = _f(el, "x")
        y = _f(el, "y")
        first = next((c for c in el if _tag(c) == "tspan"), None)
        if el.get("x") is None and first is not None:
            x, y = _f(first, "x"), _f(first, "y")
        cap = size * C.UPLOAD_TEXT_CAP_RATIO                 # korisnicke jedinice
        s = math.sqrt(abs(float(np.linalg.det(M[:2, :2])))) # mm po korisnickoj jedinici
        try:
            geo = render_text(text, cap * s, C.LABEL_LETTER_SPACING_CELLS)
        except FabError as exc:
            rep.blocking.append(f"tekst {text!r}: {exc}")
            return
        anchor = st.get("text-anchor", "start")
        width_u = geo.width / s
        dx = {"middle": -width_u / 2, "end": -width_u}.get(anchor, 0.0)
        for poly in geo.polygons:
            P = poly / s + np.array([x + dx, y - cap])
            P = (np.column_stack([P, np.ones(len(P))]) @ M.T)[:, :2]
            shapes.append(_Shape("ENGRAVE", P, True, f"<text {text!r}>"))
        family = st.get("font-family", "nepoznat")
        rep.fixes.append(f"Tekst {text!r} pretvoren u putanje ugradjenim blok-fontom "
                         f"(font {family} nije dostupan offline - izgled se razlikuje od originala)")

    walk(root, base, {})
    rep.stats.update(counters)
    if counters["skriveno"]:
        rep.warnings.append(f"Preskoceno {counters['skriveno']} skrivenih elemenata (display:none)")

    # --- ciscenje
    cleaned, dropped = [], 0
    for s in shapes:
        p = _clean(s.points, s.closed)
        if len(p) < 2 or (s.closed and len(p) < 3) or g2.perimeter(p) <= C.GEOMETRY_EPS_MM:
            dropped += 1
            continue
        cleaned.append(_Shape(s.layer, p, s.closed, s.source))
    if dropped:
        rep.fixes.append(f"Uklonjeno {dropped} degenerisanih putanja (nulte duzine)")

    def dedupe(items: list[_Shape]) -> tuple[list[_Shape], int]:
        kept: list[_Shape] = []
        removed = 0
        for s in items:
            if any(k.layer == s.layer and _is_duplicate(s.points, k.points, s.closed, k.closed) for k in kept):
                removed += 1
            else:
                kept.append(s)
        return kept, removed

    cleaned, dup1 = dedupe(cleaned)

    # --- spajanje otvorenih putanja po sloju
    final: list[_Shape] = []
    open_left: list[np.ndarray] = []
    total_joins = 0
    closed_from_open = 0
    for layer in C.LAYERS:
        layer_shapes = [s for s in cleaned if s.layer == layer]
        final += [s for s in layer_shapes if s.closed]
        opens = [s.points for s in layer_shapes if not s.closed]
        if not opens:
            continue
        closed, still, joins = _join_open(opens)
        total_joins += joins
        closed_from_open += len(closed)
        final += [_Shape(layer, c, True, "spojeno") for c in closed if len(c) >= 3]
        for o in still:
            open_left.append(o)
            gap = float(np.linalg.norm(o[-1] - o[0]))
            rep.blocking.append(
                f"{layer}: otvorena putanja od ({o[0][0]:.2f}, {o[0][1]:.2f}) do ({o[-1][0]:.2f}, {o[-1][1]:.2f}) mm "
                f"- krajevi su {gap:.2f} mm udaljeni (> {C.UPLOAD_JOIN_TOL_MM} mm), ne moze se zatvoriti bez pretpostavke")
    if total_joins:
        rep.fixes.append(f"Spojeno {total_joins} otvorenih linija (krajevi < {C.UPLOAD_JOIN_TOL_MM} mm)")
    if closed_from_open:
        rep.fixes.append(f"Zatvoreno {closed_from_open} kontura iz otvorenih putanja")

    final, dup2 = dedupe(final)
    if dup1 + dup2:
        rep.fixes.append(f"Uklonjeno {dup1 + dup2} duplih linija (tolerancija {C.UPLOAD_DUP_TOL_MM} mm)")

    # --- djelimicna preklapanja CUT linija (rez dva puta) - samo upozorenje
    cut = [s.points for s in final if s.layer == "CUT"]
    overlap_mm = 0.0
    for i in range(len(cut)):
        for j in range(i + 1, len(cut)):
            if np.any(cut[i].min(0) > cut[j].max(0) + C.UPLOAD_DUP_TOL_MM) or \
                    np.any(cut[j].min(0) > cut[i].max(0) + C.UPLOAD_DUP_TOL_MM):
                continue
            dense = g2.densify(cut[i], 0.5)
            near = g2.point_segments_distance(dense, cut[j], np.roll(cut[j], -1, axis=0)).min(axis=1)
            overlap_mm += float((near <= C.UPLOAD_DUP_TOL_MM).sum()) * 0.5
    if overlap_mm > 1.0:
        rep.warnings.append(f"Konture dijele ivice: ~{overlap_mm:.0f} mm CUT linija se rezuje dva puta "
                            "(djelimicno preklapanje se ne uklanja automatski jer bi otvorilo konture)")

    rep.fixes.append(f"Debljina svih linija postavljena na {C.SVG_STROKE_WIDTH_MM} mm, boje po slojevima "
                     "(CUT crvena, ENGRAVE crna, SCORE plava)")
    layers = {layer: [s.points for s in final if s.layer == layer] for layer in C.LAYERS}
    rep.stats["layers"] = {k: len(v) for k, v in layers.items()}
    if not any(layers.values()):
        rep.blocking.append("nakon obrade nema nijedne putanje za laser")

    design = LaserDesign(W, H)
    for layer in C.LAYERS:
        if layers[layer]:
            design.add(layer, layers[layer], layer.lower())
    if not rep.blocking:
        problems = check_design(design)
        rep.blocking += problems
    if rep.blocking:
        return FixResult(W, H, rep, layers, open_left, None, preview_svg(W, H, layers, open_left))

    from fabagent.laser.svg_builder import design_to_svg
    from fabagent.laser.svg_validator import validate_svg_text
    svg = design_to_svg(design, f"FabAgent upload: {filename}", "FabAgent - automatski popravljen SVG")
    report = validate_svg_text(svg)
    if not report["ok"]:
        rep.blocking += report["errors"]
        return FixResult(W, H, rep, layers, open_left, None, preview_svg(W, H, layers, open_left))
    return FixResult(W, H, rep, layers, open_left, design, svg)


def preview_svg(W: float, H: float, layers: dict, open_paths: list) -> str:
    """Pregled kada fajl nije spreman za produkciju: otvorene putanje narandzasto isprekidano."""
    def pts(p):
        return " ".join(f"{x:.3f},{y:.3f}" for x, y in p)
    parts = [f'<svg xmlns="{SVG_NS}" version="1.1" width="{W:.3f}mm" height="{H:.3f}mm" viewBox="0 0 {W:.3f} {H:.3f}">']
    for layer, rings in layers.items():
        color = C.LAYER_COLORS[layer]
        fill = color if layer == "ENGRAVE" else "none"
        parts.append(f'<g id="{layer}">')     # isti id-jevi kao produkcijski SVG (layer manager)
        for r in rings:
            parts.append(f'<polygon points="{pts(r)}" fill="{fill}" stroke="{color}" stroke-width="0.2"/>')
        parts.append("</g>")
    parts.append('<g id="OPEN">')
    for o in open_paths:
        parts.append(f'<polyline points="{pts(o)}" fill="none" stroke="#ff8800" stroke-width="0.6" '
                     'stroke-dasharray="1.5,1"/>')
    parts.append("</g></svg>")
    return "\n".join(parts)

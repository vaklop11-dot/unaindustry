"""Radni ekran (slobodno crtanje, LightBurn stil): G-code iz oblika i slika, tekst u konture, fontovi.

Koordinate dokumenta su u mm, ishodiste gore-lijevo radne povrsine, Y prema dolje (kao platno u
browseru). Masina ima ishodiste dolje-lijevo - Y okrece gcode._Writer, isto kao za ostale poslove.
Krivulje stizu vec pretvorene u kratke ravne dijelove (to radi radni ekran), pa ovdje nema pogadjanja.
"""

from __future__ import annotations

import base64
import binascii
import math
import os
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from fabagent.core import config as C
from fabagent.core import gcode as G
from fabagent.core import raster as R
from fabagent.core.errors import (InvalidGeometry, InvalidParam, MissingParam, OutOfRange, RangeViolation,
                                  ValidationFailed)

LAYER_COUNT = 30                      # slojevi 00..29, kao paleta u LightBurn-u
MODES = ("Line", "Fill")
MAX_POINTS = 600_000                  # ukupan broj tacaka u jednom poslu
TEXT_MAX_CHARS = 2000
TEXT_HEIGHT_RANGE_MM = (1.0, 300.0)   # visina velikog slova
FONT_EXT = (".ttf", ".otf")


@dataclass(frozen=True)
class WsLayer:
    index: int
    mode: str
    speed_mm_s: float
    power_pct: float
    passes: int
    interval_mm: float
    output: bool

    @property
    def setting(self) -> G.LayerSetting:
        return G.LayerSetting("Engrave" if self.mode == "Fill" else "Cut", self.speed_mm_s, self.power_pct,
                              self.passes, self.interval_mm)

    def describe(self) -> str:
        return (f"sloj {self.index:02d}: {self.mode}, {self.speed_mm_s * 60:g} mm/min, {self.power_pct:g} %, "
                f"{self.passes} prolaz(a), interval {self.interval_mm:g} mm")


# ------------------------------------------------------------------ ulaz
def _num(v, name: str) -> float:
    if isinstance(v, bool) or v in (None, ""):
        raise MissingParam("slojevi", [name])
    try:
        f = float(v)
    except (TypeError, ValueError):
        raise InvalidParam(f"{name}: mora biti broj") from None
    if not math.isfinite(f):
        raise InvalidParam(f"{name}: mora biti broj")
    return f


def parse_layers(raw) -> list[WsLayer]:
    """Lista slojeva redom izvrsavanja. Brzina stize u mm/min (kao u tabeli Cuts / Layers)."""
    if not isinstance(raw, list) or not raw:
        raise MissingParam("slojevi", ["layers"])
    out, seen, violations = [], set(), []
    lo_s, hi_s = (v * 60.0 for v in C.LAYER_SPEED_RANGE_MM_S)
    for d in raw:
        if not isinstance(d, dict):
            raise InvalidParam("layers: svaki sloj mora biti objekat")
        idx = d.get("index")
        if isinstance(idx, bool) or not isinstance(idx, int) or not (0 <= idx < LAYER_COUNT):
            raise InvalidParam(f"layers.index: mora biti cijeli broj 0..{LAYER_COUNT - 1}")
        if idx in seen:
            raise InvalidParam(f"layers: sloj {idx:02d} je naveden dva puta")
        seen.add(idx)
        name = f"sloj {idx:02d}"
        mode = d.get("mode")
        if mode not in MODES:
            raise InvalidParam(f"{name}.mode: '{mode}' nije dozvoljeno, izbor: {list(MODES)}")
        speed = _num(d.get("speed_mm_min"), f"{name}.speed_mm_min")
        power = _num(d.get("power_pct"), f"{name}.power_pct")
        passes_f = _num(d.get("passes"), f"{name}.passes")
        interval = _num(d.get("interval_mm"), f"{name}.interval_mm")
        if passes_f != int(passes_f):
            raise InvalidParam(f"{name}.passes: mora biti cijeli broj")
        for label, v, a, b, unit in ((f"{name}.speed_mm_min", speed, lo_s, hi_s, "mm/min"),
                                     (f"{name}.power_pct", power, *C.LAYER_POWER_RANGE_PCT, "%"),
                                     (f"{name}.passes", passes_f, *C.LAYER_PASSES_RANGE, ""),
                                     (f"{name}.interval_mm", interval, *C.LAYER_INTERVAL_RANGE_MM, "mm")):
            if not (a <= v <= b):
                violations.append(RangeViolation(label, v, a, b, unit))
        out.append(WsLayer(idx, mode, speed / 60.0, power, int(passes_f), interval, d.get("output", True) is not False))
    if violations:
        raise OutOfRange("slojevi", violations)
    return out


def _layer_index(v, what: str) -> int:
    if isinstance(v, bool) or not isinstance(v, int) or not (0 <= v < LAYER_COUNT):
        raise InvalidParam(f"{what}.layer: mora biti cijeli broj 0..{LAYER_COUNT - 1}")
    return v


def parse_shapes(raw) -> list[tuple[int, bool, np.ndarray]]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise InvalidParam("shapes mora biti lista")
    out, total = [], 0
    for i, s in enumerate(raw):
        if not isinstance(s, dict):
            raise InvalidParam("shapes: svaki oblik mora biti objekat")
        layer = _layer_index(s.get("layer"), f"oblik {i + 1}")
        try:
            pts = np.asarray(s.get("points"), dtype=np.float64)
        except (TypeError, ValueError):
            raise InvalidParam(f"oblik {i + 1}: tacke moraju biti brojevi [x, y]") from None
        if pts.ndim != 2 or pts.shape[1] != 2 or not np.isfinite(pts).all():
            raise InvalidParam(f"oblik {i + 1}: tacke moraju biti brojevi [x, y]")
        d = np.hypot(*np.diff(pts, axis=0).T) if len(pts) > 1 else np.zeros(0)
        pts = pts[np.concatenate([[True], d > C.GEOMETRY_EPS_MM])]
        closed = s.get("closed") is True
        if closed and len(pts) > 1 and np.hypot(*(pts[0] - pts[-1])) <= C.GEOMETRY_EPS_MM:
            pts = pts[:-1]
        if len(pts) < (3 if closed else 2):
            continue                                   # tacka ili prazna putanja - nema sta da se pali
        total += len(pts)
        out.append((layer, closed, pts))
    if total > MAX_POINTS:
        raise InvalidGeometry(f"crtez ima {total:,} tacaka (najvise {MAX_POINTS:,}) - pojednostavi ga", "radni ekran")
    return out


def parse_images(raw) -> list[dict]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise InvalidParam("images mora biti lista")
    out = []
    for i, im in enumerate(raw):
        name = f"slika {i + 1}"
        if not isinstance(im, dict):
            raise InvalidParam("images: svaka slika mora biti objekat")
        layer = _layer_index(im.get("layer"), name)
        try:
            corners = np.asarray(im.get("corners"), dtype=np.float64)
        except (TypeError, ValueError):
            raise InvalidParam(f"{name}: corners moraju biti 4 tacke [x, y]") from None
        if corners.shape != (4, 2) or not np.isfinite(corners).all():
            raise InvalidParam(f"{name}: corners moraju biti 4 tacke [x, y] (gore-lijevo, gore-desno, dolje-desno, dolje-lijevo)")
        mode = im.get("mode")
        if mode not in C.RASTER_MODES:
            raise InvalidParam(f"{name}.mode: '{mode}' nije dozvoljeno, izbor: {list(C.RASTER_MODES)}")
        threshold = algorithm = None
        if mode == "threshold":
            threshold = _num(im.get("threshold"), f"{name}.threshold")
            if not (C.IMAGE_THRESHOLD_MIN <= threshold <= C.IMAGE_THRESHOLD_MAX):
                raise OutOfRange("slika", [RangeViolation(f"{name}.threshold", threshold, C.IMAGE_THRESHOLD_MIN,
                                                          C.IMAGE_THRESHOLD_MAX, "")])
        if mode == "dither":
            algorithm = im.get("algorithm")
            if algorithm not in C.DITHER_ALGORITHMS:
                raise InvalidParam(f"{name}.algorithm: '{algorithm}' nije dozvoljeno, izbor: {list(C.DITHER_ALGORITHMS)}")
        out.append({"layer": layer, "corners": corners, "mode": mode, "threshold": threshold, "algorithm": algorithm,
                    "negative": im.get("negative") is True, "bytes": decode_data_url(im.get("data"), name)})
    return out


def decode_data_url(data, name: str = "slika") -> bytes:
    if not isinstance(data, str) or not data.startswith("data:image/") or ";base64," not in data[:80]:
        raise InvalidParam(f"{name}: podaci slike moraju biti data:image/...;base64")
    try:
        raw = base64.b64decode(data.split(",", 1)[1], validate=True)
    except (binascii.Error, ValueError):
        raise InvalidParam(f"{name}: neispravan base64") from None
    if len(raw) > C.UPLOAD_MAX_BYTES:
        raise ValidationFailed(name, [f"slika je veca od {C.UPLOAD_MAX_BYTES // (1024 * 1024)} MB"])
    return raw


# ------------------------------------------------------------------ slike
def image_power_map(im: dict, interval: float) -> tuple[np.ndarray, float, float]:
    """Slika (moze biti rotirana/zrcaljena) -> mapa snage 0..1 u mrezi koraka `interval`; (mapa, x0, y0)."""
    import cv2

    from fabagent.core.image_trace import load_gray
    c = im["corners"]
    tl, tr, bl = c[0], c[1], c[3]
    origin = c.min(axis=0)
    size = c.max(axis=0) - origin
    nx, ny = (max(1, int(math.ceil(v / interval - 1e-9))) for v in size)
    if nx * ny > C.RASTER_MAX_PIXELS:
        raise ValidationFailed("slika", [f"slika {size[0]:.1f}x{size[1]:.1f} mm uz interval {interval:g} mm daje "
                                         f"{nx}x{ny} = {nx * ny:,} tacaka (najvise {C.RASTER_MAX_PIXELS:,}) - "
                                         "smanji sliku ili povecaj interval sloja"])
    gray = load_gray(im["bytes"])
    h, w = gray.shape
    fx = min(1.0, float(np.hypot(*(tr - tl))) / interval / w)
    fy = min(1.0, float(np.hypot(*(bl - tl))) / interval / h)
    if fx < 1.0 or fy < 1.0:                       # smanjivanje sa usrednjavanjem prije okretanja
        gray = cv2.resize(gray, (max(1, round(w * fx)), max(1, round(h * fy))), interpolation=cv2.INTER_AREA)
        h, w = gray.shape
    src = np.float32([[0, 0], [w, 0], [0, h]])
    dst = np.float32([(tl - origin) / interval, (tr - origin) / interval, (bl - origin) / interval])
    M = cv2.getAffineTransform(src, dst)
    g = cv2.warpAffine(gray, M, (nx, ny), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=255)
    covered = cv2.warpAffine(np.full((h, w), 255, np.uint8), M, (nx, ny), flags=cv2.INTER_NEAREST,
                             borderMode=cv2.BORDER_CONSTANT, borderValue=0) > 127
    if im["negative"]:
        g = 255 - g
    if im["mode"] == "threshold":
        p = (g < im["threshold"]).astype(np.float64)
    elif im["mode"] == "dither":
        p = R.error_diffusion(g, im["algorithm"]).astype(np.float64)
    else:
        p = 1.0 - g.astype(np.float64) / 255.0
    p[~covered] = 0.0                              # izvan slike se nikad ne pali
    return p, float(origin[0]), float(origin[1])


def _raster_rows(w: G._Writer, pmap: np.ndarray, x0: float, y0: float, interval: float, st: G.LayerSetting) -> None:
    ny, nx = pmap.shape
    S = np.round(pmap * st.s_value).astype(int)
    over, feed = C.ENGRAVE_OVERSCAN_MM, st.feed
    edges = x0 + np.arange(nx + 1) * interval
    for _ in range(st.passes):
        forward = True
        for r in range(ny):
            row = S[r]
            nz = np.nonzero(row)[0]
            if not len(nz):
                continue
            a, b = int(nz[0]), int(nz[-1]) + 1
            y = y0 + (r + 0.5) * interval
            if forward:
                w.g0((edges[a] - over, y))
                w.g1((edges[a], y), 0, feed, burn=False)
                k = a
                while k < b:
                    e = k
                    while e < b and row[e] == row[k]:
                        e += 1
                    w.g1((edges[e], y), int(row[k]), feed)
                    k = e
                w.g1((edges[b] + over, y), 0, feed, burn=False)
            else:
                w.g0((edges[b] + over, y))
                w.g1((edges[b], y), 0, feed, burn=False)
                k = b
                while k > a:
                    e = k
                    while e > a and row[e - 1] == row[k - 1]:
                        e -= 1
                    w.g1((edges[e], y), int(row[k - 1]), feed)
                    k = e
                w.g1((edges[a] - over, y), 0, feed, burn=False)
            forward = not forward


# ------------------------------------------------------------------ G-code
def _line_layer(w: G._Writer, closed: list[np.ndarray], opened: list[np.ndarray], st: G.LayerSetting) -> None:
    s, feed = st.s_value, st.feed
    for _ in range(st.passes):
        for ring in (G._order_rings(closed, w.pos) if closed else []):
            w.g0(ring[0])
            for p in ring[1:]:
                w.g1(p, s, feed)
            w.g1(ring[0], s, feed)
        pool = list(opened)
        while pool:                                # otvorene linije: najblizi kraj sljedeci
            def near(i):
                return min(float(np.hypot(*(pool[i][0] - w.pos))), float(np.hypot(*(pool[i][-1] - w.pos))))
            line = pool.pop(min(range(len(pool)), key=near))
            if np.hypot(*(line[-1] - w.pos)) < np.hypot(*(line[0] - w.pos)):
                line = line[::-1]
            w.g0(line[0])
            for p in line[1:]:
                w.g1(p, s, feed)


def _collect(doc: dict):
    layers = parse_layers(doc.get("layers"))
    shapes = parse_shapes(doc.get("shapes"))
    images = parse_images(doc.get("images"))
    known = {ly.index for ly in layers}
    missing = sorted(({s[0] for s in shapes} | {im["layer"] for im in images}) - known)
    if missing:
        raise MissingParam("slojevi", [f"sloj {i:02d} (postavke sloja)" for i in missing])
    active = [ly for ly in layers if ly.output]
    on = {ly.index for ly in active}
    shapes = [s for s in shapes if s[0] in on]
    images = [im for im in images if im["layer"] in on]
    if not shapes and not images:
        raise InvalidGeometry("nema nijednog oblika za izlaz (prazan crtez ili su svi slojevi iskljuceni)", "radni ekran")
    return active, shapes, images


def _extent(active: list[WsLayer], shapes, images) -> tuple[tuple[float, float, float, float], tuple[float, float, float, float]]:
    """(granice oblika, granice kretanja glave ukljucujuci prelet kod popune i slika)."""
    fill = {ly.index for ly in active if ly.mode == "Fill"}
    over = C.ENGRAVE_OVERSCAN_MM
    boxes, moves = [], []
    for layer, closed, pts in shapes:
        x0, y0, x1, y1 = *pts.min(axis=0), *pts.max(axis=0)
        boxes.append((x0, y0, x1, y1))
        pad = over if (layer in fill and closed) else 0.0
        moves.append((x0 - pad, y0, x1 + pad, y1))
    for im in images:
        x0, y0, x1, y1 = *im["corners"].min(axis=0), *im["corners"].max(axis=0)
        boxes.append((x0, y0, x1, y1))
        moves.append((x0 - over, y0, x1 + over, y1))

    def merge(bs):
        a = np.array(bs)
        return float(a[:, 0].min()), float(a[:, 1].min()), float(a[:, 2].max()), float(a[:, 3].max())
    return merge(boxes), merge(moves)


def _check_bounds(move: tuple[float, float, float, float]) -> None:
    W, H = C.LASER_WORK_AREA_MM
    x0, y0, x1, y1 = move
    eps = 1e-6
    if x0 < -eps or y0 < -eps or x1 > W + eps or y1 > H + eps:
        raise OutOfRange("pozicija", [RangeViolation(
            "pozicija", 0, None, None, "mm",
            f"crtez (X {x0:.1f}..{x1:.1f}, Y {H - y1:.1f}..{H - y0:.1f} mm, ukljucujuci prelet "
            f"{C.ENGRAVE_OVERSCAN_MM:g} mm kod popune i slika) izlazi izvan radne povrsine {W:g} x {H:g} mm")])


def generate(doc: dict) -> G.GcodeResult:
    """doc: {layers: [...], shapes: [...], images: [...]} -> G-code cijelog posla."""
    active, shapes, images = _collect(doc)
    box, move = _extent(active, shapes, images)
    _check_bounds(move)
    H = C.LASER_WORK_AREA_MM[1]
    w = G._Writer(0.0, 0.0)
    w.lines += ["; FabAgent G-code - radni ekran",
                f"; masina: {C.LASER_MACHINE_NAME}, ishodiste {C.MACHINE_ORIGIN}, S max {C.GRBL_S_MAX}",
                "G21 ; mm", "G90 ; apsolutno", "M5", f"{C.GRBL_LASER_MODE_CMD} S0"]
    warnings, used = [], []
    for ly in active:
        mine = [(c, p) for layer, c, p in shapes if layer == ly.index]
        pics = [im for im in images if im["layer"] == ly.index]
        if not mine and not pics:
            continue
        st = ly.setting
        used.append(ly.index)
        w.lines.append(f"; --- {ly.describe()}")
        closed = [p for c, p in mine if c]
        opened = [p for c, p in mine if not c]
        if ly.mode == "Fill":
            if closed:
                G._fill_layer(w, closed, st)
            if opened:
                warnings.append(f"Sloj {ly.index:02d} (Fill): {len(opened)} otvorenih linija je preskoceno - "
                                "popuna radi samo sa zatvorenim oblicima.")
        else:
            _line_layer(w, closed, opened, st)
        for im in pics:
            pmap, x0, y0 = image_power_map(im, ly.interval_mm)
            w.lines.append(f"; slika {pmap.shape[1]}x{pmap.shape[0]} tacaka, {im['mode']}")
            _raster_rows(w, pmap, x0, y0, ly.interval_mm, st)
    w.lines += ["M5 ; laser ugasen", "G0 X0 Y0", "M2"]
    travel_s = w.travel_mm / (C.GRBL_TRAVEL_FEED_MM_MIN / 60.0)
    stats = {"lines": len(w.lines), "layers": used, "burn_mm": round(w.burn_mm, 1), "travel_mm": round(w.travel_mm, 1),
             "time_s": round(w.burn_s + travel_s, 1), "warnings": warnings,
             "bbox_mm": [round(box[0], 3), round(H - box[3], 3), round(box[2], 3), round(H - box[1], 3)]}
    return G.GcodeResult("\n".join(w.lines) + "\n", stats)


def frame(doc: dict) -> G.GcodeResult:
    """Obilazak granicnog pravougaonika posla sa UGASENIM laserom (M5, S0)."""
    active, shapes, images = _collect(doc)
    box, move = _extent(active, shapes, images)
    _check_bounds(move)
    x0, y0, x1, y1 = box
    H = C.LASER_WORK_AREA_MM[1]
    w = G._Writer(0.0, 0.0)
    w.lines += ["; FabAgent FRAME - laser ugasen (M5, S0)", "G21", "G90", "M5"]
    w.g0((x0, y1))
    for p in ((x1, y1), (x1, y0), (x0, y0), (x0, y1)):
        w.lines.append(f"G1 {w.xy(p)} S0 F{C.GRBL_TRAVEL_FEED_MM_MIN:.0f}")
    w.lines.append("M5")
    return G.GcodeResult("\n".join(w.lines) + "\n",
                         {"lines": len(w.lines), "bbox_mm": [round(x0, 3), round(H - y1, 3), round(x1, 3), round(H - y0, 3)]})


# ------------------------------------------------------------------ fontovi i tekst
_FONTS: dict[str, dict] | None = None


def font_dirs() -> list[Path]:
    dirs = []
    if os.environ.get("WINDIR"):
        dirs.append(Path(os.environ["WINDIR"]) / "Fonts")
    if os.environ.get("LOCALAPPDATA"):
        dirs.append(Path(os.environ["LOCALAPPDATA"]) / "Microsoft" / "Windows" / "Fonts")
    dirs += [Path("/usr/share/fonts"), Path("/Library/Fonts")]
    return [d for d in dirs if d.is_dir()]


def list_fonts() -> list[dict]:
    """Fontovi instalirani na ovom racunaru (TTF/OTF). Cita se jednom po pokretanju servera."""
    global _FONTS
    if _FONTS is None:
        from fontTools.ttLib import TTFont
        found: dict[str, dict] = {}
        for d in font_dirs():
            for f in sorted(d.rglob("*")):
                if f.suffix.lower() not in FONT_EXT or f.name.lower() in found:
                    continue
                try:
                    font = TTFont(str(f), lazy=True)
                    name = font["name"].getDebugName(4) or f.stem
                    if not font.getBestCmap():
                        continue
                    font.close()
                except Exception:      # ostecen ili nepodrzan font - samo se ne nudi
                    continue
                found[f.name.lower()] = {"id": f.name, "name": str(name), "path": str(f)}
        _FONTS = found
    return sorted(({"id": v["id"], "name": v["name"]} for v in _FONTS.values()), key=lambda v: v["name"].lower())


def text_path(text, font_id, height_mm) -> dict:
    """Tekst -> SVG putanja (konture slova) u mm. Pocetak (0, 0) je lijevi kraj osnovne linije prvog reda,
    Y prema dolje. `height_mm` je visina velikog slova."""
    from fontTools.pens.svgPathPen import SVGPathPen
    from fontTools.pens.transformPen import TransformPen
    from fontTools.ttLib import TTFont

    missing = [n for n, v in (("text", text), ("font", font_id), ("height_mm", height_mm)) if v in (None, "")]
    if missing:
        raise MissingParam("tekst", missing)
    if not isinstance(text, str) or not text.strip():
        raise MissingParam("tekst", ["text"])
    if len(text) > TEXT_MAX_CHARS:
        raise InvalidParam(f"text: najvise {TEXT_MAX_CHARS} znakova")
    height = _num(height_mm, "height_mm")
    if not (TEXT_HEIGHT_RANGE_MM[0] <= height <= TEXT_HEIGHT_RANGE_MM[1]):
        raise OutOfRange("tekst", [RangeViolation("height_mm", height, *TEXT_HEIGHT_RANGE_MM)])
    list_fonts()
    entry = (_FONTS or {}).get(str(font_id).lower())
    if entry is None:
        raise InvalidParam(f"font: '{font_id}' nije medju instaliranim fontovima")
    font = TTFont(entry["path"])
    glyphs, cmap, hmtx = font.getGlyphSet(), font.getBestCmap(), font["hmtx"]
    upm = font["head"].unitsPerEm
    cap = getattr(font["OS/2"], "sCapHeight", 0) if "OS/2" in font else 0
    if not cap or cap <= 0:
        cap = 0.7 * upm
    scale = height / cap
    hhea = font["hhea"]
    line_step = (hhea.ascent - hhea.descent + hhea.lineGap) * scale
    parts, unknown, width = [], [], 0.0
    for row, line in enumerate(text.replace("\r\n", "\n").replace("\r", "\n").split("\n")):
        x = 0.0
        for ch in line:
            gname = cmap.get(ord(ch))
            if gname is None:
                if ch not in unknown:
                    unknown.append(ch)
                continue
            pen = SVGPathPen(glyphs, ntos=lambda v: f"{v:.4f}".rstrip("0").rstrip(".") or "0")
            glyphs[gname].draw(TransformPen(pen, (scale, 0, 0, -scale, x, row * line_step)))
            cmds = pen.getCommands()
            if cmds:
                parts.append(cmds)
            x += hmtx[gname][0] * scale
        width = max(width, x)
    if not parts:
        raise ValidationFailed("tekst", ["font nema nijedno od unesenih slova"])
    return {"d": " ".join(parts), "font": entry["id"], "font_name": entry["name"], "height_mm": height,
            "width_mm": round(width, 3), "missing": unknown}

"""Konverzija DXF / PDF / PNG / JPG -> SVG, nakon cega SVG obavezno ide kroz svg_fixer.

  * DXF  (ezdxf):   LINE, CIRCLE, ARC, LWPOLYLINE (sa bulge), POLYLINE, ELLIPSE + INSERT
                    (blokovi se razgradjuju). Jedinice iz $INSUNITS; ako ih nema, korisnik ih
                    mora zadati (nema pretpostavke). Ostali entiteti (TEXT, HATCH, SPLINE...)
                    -> blokirajuca greska, ne gube se tiho.
  * PDF  (pymupdf): vektorski crtezi sa jedine stranice koja ih ima; samo slika -> greska;
                    tekst (fontovi) -> blokirajuca greska.
  * PNG/JPG (pymupdf ucitava, potrace trasira): prag 0-255, ciljna sirina u mm (DPI slike se
                    ne pogadja).
Rezultat je SVG u mm + napomene i blokirajuci problemi koji idu u izvjestaj fixer-a.
"""

from __future__ import annotations

import io
import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from fabagent.core import config as C
from fabagent.core.errors import InvalidParam, MissingParam, OutOfRange, RangeViolation, ValidationFailed

SVG_NS = "http://www.w3.org/2000/svg"
SUPPORTED_EXT = (".svg", ".dxf", ".pdf", ".png", ".jpg", ".jpeg")
# Arhive nisu crtezi. Operater ih svejedno prilaze, jer su mu paketi stigli tako,
# pa im treba poruka koja kaze sta da uradi - a ne "format nije podrzan".
ARHIVA_EXT = (".zip", ".rar", ".7z", ".tar", ".gz")
IMAGE_EXT = (".png", ".jpg", ".jpeg")
DXF_UNIT_CHOICES = {"mm": 1.0, "cm": 10.0, "in": C.MM_PER_IN}
# $INSUNITS kodovi -> mm
DXF_INSUNITS_MM = {1: C.MM_PER_IN, 2: 12 * C.MM_PER_IN, 4: 1.0, 5: 10.0, 6: 1000.0, 14: 100.0}
DXF_INSUNITS_NAMES = {1: "in", 2: "ft", 4: "mm", 5: "cm", 6: "m", 14: "dm"}
DXF_SUPPORTED = {"LINE", "CIRCLE", "ARC", "LWPOLYLINE", "POLYLINE", "ELLIPSE"}


@dataclass
class Conversion:
    svg: bytes
    source_format: str
    notes: list[str] = field(default_factory=list)       # sta je konverzija uradila
    blocking: list[str] = field(default_factory=list)    # sto onemogucava odobravanje
    preview_source: bytes | None = None                   # za prikaz "originala" (SVG)
    bitmap_png: bytes | None = None                       # slika: obradjena bitmapa (medjukorak)
    trace_options: dict | None = None


def detect_format(filename: str) -> str:
    ext = Path(filename).suffix.lower()
    if ext in ARHIVA_EXT:
        mb = C.UPLOAD_MAX_BYTES // (1024 * 1024)
        raise InvalidParam(
            f"fajl: '{ext}' je arhiva, a ne crtez. Raspakuj je i prilozi jedan fajl iz nje "
            f"({', '.join(SUPPORTED_EXT)}). Arhive se kroz ovu formu ne mogu ni poslati - "
            f"granica je {mb} MB, a paketi su po vise stotina MB.")
    if ext not in SUPPORTED_EXT:
        raise InvalidParam(f"fajl: format '{ext or '?'}' nije podrzan; dozvoljeno: {', '.join(SUPPORTED_EXT)}")
    return "jpg" if ext == ".jpeg" else ext[1:]


def _fmt(v: float) -> str:
    return f"{v:.4f}".rstrip("0").rstrip(".") or "0"


def _svg_doc(width_mm: float, height_mm: float, body: list[str], title: str) -> bytes:
    head = (f'<svg xmlns="{SVG_NS}" version="1.1" width="{_fmt(width_mm)}mm" height="{_fmt(height_mm)}mm" '
            f'viewBox="0 0 {_fmt(width_mm)} {_fmt(height_mm)}"><title>{title}</title>')
    return (head + "".join(body) + "</svg>").encode("utf-8")


def _polyline_d(pts: np.ndarray, closed: bool) -> str:
    s = "M " + " L ".join(f"{_fmt(x)} {_fmt(y)}" for x, y in pts)
    return s + (" Z" if closed else "")


# ------------------------------------------------------------------- DXF
def dxf_to_svg(data: bytes, units: str | None = None) -> Conversion:
    import ezdxf
    from ezdxf import recover
    from fabagent.core import geometry2d as g2

    try:
        doc, auditor = recover.read(io.BytesIO(data))
    except Exception as exc:  # ezdxf.DXFStructureError i slicno
        raise ValidationFailed("dxf", [f"DXF nije citljiv: {exc}"]) from exc
    conv = Conversion(b"", "dxf")
    polylines: list[tuple[np.ndarray, bool]] = []
    unsupported: dict[str, int] = {}

    def emit(e, depth=0):
        t = e.dxftype()
        if t == "INSERT":
            if depth > 8:
                unsupported["INSERT (preduboko ugnijezdjen)"] = unsupported.get("INSERT", 0) + 1
                return
            for v in e.virtual_entities():
                emit(v, depth + 1)
            return
        if t in ("LWPOLYLINE", "POLYLINE"):
            closed = bool(e.closed if t == "LWPOLYLINE" else e.is_closed)
            pts = []
            for v in e.virtual_entities():          # bulge -> ARC, ravni dijelovi -> LINE
                seg = _entity_points(v, g2)
                if seg is None:
                    continue
                pts.append(seg if not pts else seg[1:])
            if pts:
                P = np.vstack(pts)
                polylines.append((P, closed or np.linalg.norm(P[0] - P[-1]) < C.GEOMETRY_EPS_MM))
            return
        if t not in DXF_SUPPORTED:
            unsupported[t] = unsupported.get(t, 0) + 1
            return
        pts = _entity_points(e, g2)
        if pts is not None:
            polylines.append((pts, t == "CIRCLE" or (t == "ELLIPSE" and _full_ellipse(e))))

    for e in doc.modelspace():
        emit(e)
    if unsupported:
        conv.blocking.append("DXF entiteti nisu podrzani (sadrzaj bi se izgubio): "
                             + ", ".join(f"{k} ({v})" for k, v in sorted(unsupported.items()))
                             + " - u CAD-u ih razgradi (Explode) u linije/lukove")
    if not polylines:
        if auditor.has_errors:
            raise ValidationFailed("dxf", ["DXF nije ispravan i nema citljivih entiteta"])
        raise ValidationFailed("dxf", ["DXF je prazan (nema podrzanih entiteta u modelspace)"])
    # jedinice se provjeravaju tek kada postoji sadrzaj
    code = int(doc.header.get("$INSUNITS", 0) or 0)
    if code in DXF_INSUNITS_MM:
        scale = DXF_INSUNITS_MM[code]
        conv.notes.append(f"DXF jedinice iz zaglavlja: {DXF_INSUNITS_NAMES[code]}")
        if units:
            conv.notes.append(f"zadane jedinice '{units}' se ne koriste - DXF ih vec definise")
    elif units:
        if units not in DXF_UNIT_CHOICES:
            raise InvalidParam(f"jedinice: '{units}' nije dozvoljeno, izbor: {list(DXF_UNIT_CHOICES)}")
        scale = DXF_UNIT_CHOICES[units]
        conv.notes.append(f"DXF nema jedinice ($INSUNITS={code}); koriste se zadane: {units}")
    else:
        raise MissingParam("upload_dxf", ["jedinice"])


    allp = np.vstack([p for p, _ in polylines]) * scale
    lo, hi = allp.min(axis=0), allp.max(axis=0)
    pad = C.LASER_DOC_PADDING_MM
    W, H = float(hi[0] - lo[0]) + 2 * pad, float(hi[1] - lo[1]) + 2 * pad
    body = []
    for P, closed in polylines:
        Q = P * scale
        # DXF je y-gore, SVG y-dolje: preslikavanje y uz pomak u ishodiste
        svg_pts = np.column_stack([Q[:, 0] - lo[0] + pad, hi[1] - Q[:, 1] + pad])
        body.append(f'<path d="{_polyline_d(svg_pts, closed)}" fill="none" stroke="#000000"/>')
    conv.notes.append(f"DXF: {len(polylines)} putanja, dimenzije {W - 2 * pad:.2f} x {H - 2 * pad:.2f} mm")
    conv.svg = _svg_doc(W, H, body, "DXF konverzija")
    conv.preview_source = conv.svg
    return conv


def _full_ellipse(e) -> bool:
    return abs((e.dxf.end_param - e.dxf.start_param) - 2 * math.pi) < 1e-9


def _entity_points(e, g2) -> np.ndarray | None:
    t = e.dxftype()
    if t == "LINE":
        return np.array([[e.dxf.start.x, e.dxf.start.y], [e.dxf.end.x, e.dxf.end.y]], float)
    if t == "CIRCLE":
        c = e.dxf.center
        return g2.circle(c.x, c.y, e.dxf.radius)
    if t == "ARC":
        c = e.dxf.center
        a0, a1 = math.radians(e.dxf.start_angle), math.radians(e.dxf.end_angle)
        if a1 <= a0:
            a1 += 2 * math.pi           # DXF lukovi idu CCW od start do end
        return g2.arc(c.x, c.y, e.dxf.radius, a0, a1)
    if t == "ELLIPSE":
        n = max(C.ARC_MIN_SEGMENTS, g2.arc_segments(e.dxf.major_axis.magnitude))
        pts = np.array([[v.x, v.y] for v in e.flattening(C.ARC_CHORD_TOLERANCE_MM, segments=n)], float)
        return pts[:-1] if _full_ellipse(e) and len(pts) > 2 else pts
    return None


# ------------------------------------------------------------------- PDF
def pdf_to_svg(data: bytes, trace_options=None) -> Conversion:
    import pymupdf

    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception as exc:
        raise ValidationFailed("pdf", [f"PDF nije citljiv: {exc}"]) from exc
    if doc.needs_pass:
        raise ValidationFailed("pdf", ["PDF je zasticen lozinkom"])
    pages = []
    images_total = 0
    for page in doc:
        drawings = page.get_drawings()
        images_total += len(page.get_images())
        if drawings:
            pages.append((page, drawings))
    if not pages:
        if images_total:
            return _pdf_raster(doc, trace_options)
        raise ValidationFailed("pdf", ["PDF je prazan (nema vektorskih crteza)"])
    conv = Conversion(b"", "pdf")
    if len(pages) > 1:
        conv.blocking.append(f"PDF ima {len(pages)} stranice sa crtezima; za laser podijeli fajl na pojedinacne stranice")
    page, drawings = pages[0]
    if page.get_text().strip():
        conv.blocking.append("PDF sadrzi tekst (fontove) koji se ne moze pretvoriti bez fonta - "
                             "u programu za crtanje pretvori tekst u krivulje (Create Outlines) prije izvoza")
    if page.get_images():
        conv.blocking.append("PDF sadrzi i rastersku sliku pored vektora - slika bi se izgubila")
    pt_mm = C.MM_PER_IN / C.PT_PER_IN
    W, H = page.rect.width * pt_mm, page.rect.height * pt_mm
    body = []
    for d in drawings:
        parts = []
        for item in d["items"]:
            op = item[0]
            if op == "l":
                parts.append(f"M {_fmt(item[1].x * pt_mm)} {_fmt(item[1].y * pt_mm)} "
                             f"L {_fmt(item[2].x * pt_mm)} {_fmt(item[2].y * pt_mm)}")
            elif op == "c":
                p = [item[k] for k in range(1, 5)]
                parts.append(f"M {_fmt(p[0].x * pt_mm)} {_fmt(p[0].y * pt_mm)} C " +
                             " ".join(f"{_fmt(q.x * pt_mm)} {_fmt(q.y * pt_mm)}" for q in p[1:]))
            elif op == "re":
                r = item[1]
                parts.append(f"M {_fmt(r.x0 * pt_mm)} {_fmt(r.y0 * pt_mm)} H {_fmt(r.x1 * pt_mm)} "
                             f"V {_fmt(r.y1 * pt_mm)} H {_fmt(r.x0 * pt_mm)} Z")
            elif op == "qu":
                q = item[1]
                pts = [q.ul, q.ur, q.lr, q.ll]
                parts.append("M " + " L ".join(f"{_fmt(v.x * pt_mm)} {_fmt(v.y * pt_mm)}" for v in pts) + " Z")
        if not parts:
            continue
        dstr = " ".join(parts) + (" Z" if d.get("closePath") else "")
        fill = _hex(d.get("fill")) if d.get("fill") is not None else "none"
        stroke = _hex(d.get("color")) if d.get("color") is not None else "none"
        if stroke == "none" and fill == "none":
            continue
        body.append(f'<path d="{dstr}" fill="{fill}" stroke="{stroke}"/>')
    conv.notes.append(f"PDF: {len(body)} vektorskih crteza sa stranice {page.number + 1}, "
                      f"stranica {W:.1f} x {H:.1f} mm (1 pt = 1/72 in)")
    conv.svg = _svg_doc(W, H, body, "PDF konverzija")
    conv.preview_source = conv.svg
    return conv


def _hex(rgb) -> str:
    r, g, b = (max(0, min(255, int(round(c * 255)))) for c in rgb[:3])
    return f"#{r:02x}{g:02x}{b:02x}"


# ------------------------------------------------------------------- slike
def _pdf_raster(doc, trace_options) -> Conversion:
    """PDF bez vektora: tacno jedna slika -> trasiranje. Velicina iz polozaja slike na stranici."""
    import pymupdf
    placements = []
    for page in doc:
        for img in page.get_images(full=True):
            for rect in page.get_image_rects(img[0]):
                placements.append((page, img[0], rect))
    if trace_options is None or (trace_options.threshold is None and trace_options.mode == "fiksni"):
        raise ValidationFailed("pdf", ["PDF ne sadrzi vektore, samo sliku - zadaj parametre trasiranja "
                                       "(prag itd.) da bi se slika pretvorila u vektore"])
    if len(placements) != 1:
        raise ValidationFailed("pdf", [f"PDF ne sadrzi vektore, a ima {len(placements)} postavljenih slika; "
                                       "za trasiranje mora imati tacno jednu"])
    page, xref, rect = placements[0]
    pix = pymupdf.Pixmap(doc, xref)
    if pix.alpha or (pix.colorspace and pix.colorspace.n not in (1, 3)):
        pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
    pt_mm = C.MM_PER_IN / C.PT_PER_IN
    conv = image_to_svg(pix.tobytes("png"), None, rect.width * pt_mm, options=trace_options,
                        height_mm=rect.height * pt_mm)
    conv.source_format = "pdf"
    conv.notes.insert(0, f"PDF ne sadrzi vektore: slika sa stranice {page.number + 1} "
                         f"({rect.width * pt_mm:.1f} x {rect.height * pt_mm:.1f} mm) je trasirana")
    return conv


def image_to_svg(data: bytes, threshold: int | None, width_mm: float | None, *, options=None,
                 height_mm: float | None = None, outline: bool = False) -> Conversion:
    from fabagent.core import image_trace as T

    opts = options if options is not None else T.TraceOptions(threshold=threshold)
    need_threshold = opts.threshold if opts.mode == "fiksni" else 0
    missing = [n for n, v in (("prag", need_threshold), ("sirina_mm", width_mm)) if v is None]
    if missing:
        raise MissingParam("upload_slika", missing)
    T.validate_options(opts)
    if not (C.IMAGE_WIDTH_MIN_MM <= width_mm <= C.LASER_WORK_AREA_MM[0]):
        raise OutOfRange("upload_slika", [RangeViolation("sirina_mm", width_mm, C.IMAGE_WIDTH_MIN_MM,
                                                         C.LASER_WORK_AREA_MM[0])])
    gray = T.load_gray(data)
    h_px, w_px = gray.shape
    if w_px * h_px > C.IMAGE_MAX_PIXELS:
        raise ValidationFailed("slika", [f"slika ima {w_px}x{h_px} piksela; max je "
                                         f"{C.IMAGE_MAX_PIXELS:,} piksela - smanji je prije uploada"])
    fg = T.binarize(gray, opts)
    hint = "povecaj prag" if opts.mode == "fiksni" else "provjeri cutoff/invert"
    if not fg.any():
        raise ValidationFailed("slika", [f"uz zadane postavke nema nijednog tamnog piksela - {hint}"])
    if fg.all():
        raise ValidationFailed("slika", ["uz zadane postavke je cijela slika tamna - smanji prag ili ukljuci invert"])
    paths, raw = T.trace(fg, opts)
    if not paths:
        raise ValidationFailed("slika", ["trasiranje nije dalo nijednu konturu"])
    W = width_mm
    H = height_mm if height_mm is not None else h_px * width_mm / w_px
    inner = "".join(paths) if raw else '<path d="' + " ".join(paths) + '"/>'
    body = (f'<svg xmlns="{SVG_NS}" version="1.1" width="{_fmt(W)}mm" height="{_fmt(H)}mm" '
            f'viewBox="0 0 {w_px} {h_px}" preserveAspectRatio="none"><title>Trasirana slika</title>'
            + (f'<g fill="none" stroke="{C.LAYER_COLORS["SCORE"]}">{inner}</g></svg>' if outline else
               f'<g fill="#000000" fill-rule="evenodd" stroke="none">{inner}</g></svg>'))
    conv = Conversion(body.encode("utf-8"), "image")
    ratio = float(fg.mean())
    steps = [f"motor {opts.engine}", f"prag {opts.threshold}" if opts.mode == "fiksni" else "adaptivni prag"]
    if opts.cutoff is not None:
        steps.append(f"cutoff {opts.cutoff}")
    if opts.invert:
        steps.append("invertovano")
    if opts.denoise:
        steps.append(f"uklanjanje suma {opts.denoise}")
    if opts.simplify:
        steps.append(f"RDP {opts.simplify:g} px")
    conv.notes.append(f"Slika {w_px}x{h_px} px trasirana ({', '.join(steps)}), {ratio * 100:.1f} % tamnih "
                      f"piksela, {len(paths)} kontura, {W:.1f} x {H:.1f} mm ({W / w_px * 1000:.1f} um/px)")
    conv.notes.append("Obrisi (Vectorize) -> sloj SCORE (plavo); u Layer Manageru mu se moze dati mod Cut"
                      if outline else "Trasirani oblici su popunjeni -> sloj ENGRAVE (graviranje)")
    conv.bitmap_png = T.bitmap_png(fg)
    conv.trace_options = opts.as_dict()
    return conv


def convert(filename: str, data: bytes, *, threshold: int | None = None, width_mm: float | None = None,
            dxf_units: str | None = None, trace: dict | None = None) -> Conversion:
    """trace: sirove opcije iz forme (prag, cutoff, invertuj, uklanjanje_suma, pojednostavljenje,
    nacin_praga, motor). Bez njih se koristi samo `threshold` (osnovno trasiranje)."""
    from fabagent.core import image_trace as T

    if len(data) > C.UPLOAD_MAX_BYTES:
        raise ValidationFailed(filename, [f"fajl je veci od {C.UPLOAD_MAX_BYTES // (1024 * 1024)} MB"])
    fmt = detect_format(filename)
    if fmt == "svg":
        return Conversion(data, "svg", preview_source=data)
    if fmt == "dxf":
        return dxf_to_svg(data, dxf_units)
    opts = None
    if trace is not None and any(str(v).strip() for v in trace.values() if v is not None):
        if fmt == "pdf":
            try:
                opts = T.parse_options(trace)
            except MissingParam:
                opts = None          # PDF sa vektorima ne treba prag
        else:
            try:
                opts = T.parse_options(trace)
            except MissingParam as exc:
                raise MissingParam("upload_slika",
                                   exc.missing + (["sirina_mm"] if width_mm is None else [])) from None
    elif threshold is not None:
        opts = T.TraceOptions(threshold=threshold)
    outline = bool(trace) and str(trace.get("obrisi", "")).lower() == "true"
    if fmt == "pdf":
        return pdf_to_svg(data, opts)
    return image_to_svg(data, threshold, width_mm, options=opts, outline=outline)

"""Napredna obrada slike prije trasiranja (slicno LightBurn "Trace Image").

Redoslijed (deterministicki):
  1. ucitavanje (OpenCV), providni pikseli -> pozadina (bijelo)
  2. siva skala
  3. invert (opciono, za crtez svijetao na tamnoj podlozi)
  4. denoise: Gaussian blur prije praga (opciono)
  5. prag: fiksni (cutoff <= sivo < threshold) ili adaptivni (cv2.adaptiveThreshold) uz cutoff
  6. denoise: morfolosko otvaranje + zatvaranje nakon praga (opciono)
  7. trasiranje motorom: vlastiti (nas kod, podrazumijevani), potrace (krivulje, GPL),
     konture (OpenCV + RDP) ili vtracer (ako je instaliran)
  8. simplify: Ramer-Douglas-Peucker (cv2.approxPolyDP) na konturama, tolerancija u pikselima

Opcione obrade se primjenjuju SAMO ako su zadane - nezadana obrada znaci "bez te obrade"
(identitet), ne izmisljenu vrijednost. Obavezni su prag (u fiksnom nacinu) i ciljna sirina.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

import numpy as np

from fabagent.core import config as C
from fabagent.core.errors import InvalidParam, MissingParam, OutOfRange, RangeViolation, ValidationFailed

MODES = ("fiksni", "adaptivni")
ENGINES = ("vlastiti", "potrace", "konture", "vtracer")
_BOOL = {"true": True, "1": True, "da": True, "on": True, "false": False, "0": False, "ne": False}


@dataclass(frozen=True)
class TraceOptions:
    threshold: int | None = None     # gornja granica (piksel < threshold je crtez)
    cutoff: int | None = None        # donja granica (piksel < cutoff se ignorise)
    invert: bool = False
    denoise: int = 0                 # 0 = bez uklanjanja suma
    simplify: float = 0.0            # px; 0 = bez RDP
    mode: str = "fiksni"
    engine: str = "vlastiti"

    def as_dict(self) -> dict:
        return {"prag": self.threshold, "cutoff": self.cutoff, "invertuj": self.invert,
                "uklanjanje_suma": self.denoise, "pojednostavljenje_px": self.simplify,
                "nacin_praga": self.mode, "motor": self.engine}


_VTRACER_STATE: dict = {}

# vtracer je nativna (Rust) biblioteka; na nekim kombinacijama Python/OS rusi proces
# (npr. vtracer 0.6.15 + Python 3.14 + Windows: access violation). Zato se UVIJEK
# pokrece u zasebnom procesu - pad ne smije oboriti web server.
_VTRACER_SCRIPT = (
    "import sys, vtracer; vtracer.convert_image_to_svg_py(sys.argv[1], sys.argv[2], colormode='binary', "
    "hierarchical='cutout', mode=sys.argv[3], filter_speckle=int(sys.argv[4]), path_precision=int(sys.argv[5]))"
)


def _run_vtracer(png: bytes, mode: str) -> str:
    import subprocess
    import sys
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory(prefix="fabagent_vt_") as d:
        src, dst = Path(d) / "in.png", Path(d) / "out.svg"
        src.write_bytes(png)
        try:
            proc = subprocess.run([sys.executable, "-I", "-c", _VTRACER_SCRIPT, str(src), str(dst), mode,
                                   str(C.POTRACE_TURDSIZE), str(C.TRACE_VTRACER_PRECISION)],
                                  capture_output=True, timeout=C.TRACE_VTRACER_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            raise ValidationFailed("slika", [f"vtracer nije zavrsio za {C.TRACE_VTRACER_TIMEOUT_S} s"]) from None
        if proc.returncode != 0 or not dst.is_file():
            err = proc.stderr.decode(errors="replace").strip().splitlines()[-1:] or [f"izlazni kod {proc.returncode}"]
            raise ValidationFailed("slika", [f"vtracer se srusio ili nije uspio ({err[0]}) - koristi potrace ili konture"])
        return dst.read_text(encoding="utf-8")


def vtracer_status() -> tuple[bool, str]:
    """(dostupan, opis). Rezultat se kesira po procesu (jedna probna konverzija)."""
    if "ok" not in _VTRACER_STATE:
        try:
            import importlib.util
            if importlib.util.find_spec("vtracer") is None:
                _VTRACER_STATE.update(ok=False, msg="vtracer nije instaliran (pip install vtracer)")
            else:
                probe = np.full((8, 8), True)
                probe[2:6, 2:6] = False
                _run_vtracer(bitmap_png(~probe), "polygon")
                _VTRACER_STATE.update(ok=True, msg="vtracer radi")
        except ValidationFailed as exc:
            _VTRACER_STATE.update(ok=False, msg="vtracer je instaliran, ali se rusi na ovom sistemu "
                                                f"({exc.problems[0]})")
    return _VTRACER_STATE["ok"], _VTRACER_STATE["msg"]


def engine_available(engine: str) -> bool:
    if engine == "vtracer":
        return vtracer_status()[0]
    return True


def parse_options(raw: dict) -> TraceOptions:
    """Iz forme (stringovi) u TraceOptions. Prazno = nezadano. Greske kao u validatoru."""
    def text(k):
        v = raw.get(k)
        return None if v is None or str(v).strip() == "" else str(v).strip()

    def num(k, kind):
        t = text(k)
        if t is None:
            return None
        if isinstance(raw.get(k), (int, float)) and not isinstance(raw.get(k), bool):
            return kind(raw[k])
        if "," in t or not re.match(r"^[+-]?\d+(\.\d+)?$", t):
            raise InvalidParam(f"{k}: '{t}' nije broj (koristi tacku)")
        if kind is int and "." in t:
            raise InvalidParam(f"{k}: '{t}' nije cijeli broj")
        return kind(t)

    mode = text("nacin_praga") or "fiksni"
    engine = text("motor") or "vlastiti"
    if mode not in MODES:
        raise InvalidParam(f"nacin_praga: '{mode}' nije dozvoljeno, izbor: {list(MODES)}")
    if engine not in ENGINES:
        raise InvalidParam(f"motor: '{engine}' nije dozvoljeno, izbor: {list(ENGINES)}")
    if not engine_available(engine):
        raise InvalidParam(f"motor: {vtracer_status()[1]} - izaberi potrace ili konture")
    inv = text("invertuj")
    if inv is not None and inv.lower() not in _BOOL:
        raise InvalidParam(f"invertuj: '{inv}' nije bool (true/false)")
    opts = TraceOptions(
        threshold=num("prag", int), cutoff=num("cutoff", int),
        invert=_BOOL.get((inv or "false").lower(), False) if not isinstance(raw.get("invertuj"), bool)
        else raw["invertuj"],
        denoise=num("uklanjanje_suma", int) or 0, simplify=num("pojednostavljenje", float) or 0.0,
        mode=mode, engine=engine)
    validate_options(opts)
    return opts


def validate_options(o: TraceOptions) -> None:
    if o.mode == "fiksni" and o.threshold is None:
        raise MissingParam("upload_slika", ["prag"])
    v = []
    if o.threshold is not None and not (C.IMAGE_THRESHOLD_MIN <= o.threshold <= C.IMAGE_THRESHOLD_MAX):
        v.append(RangeViolation("prag", o.threshold, C.IMAGE_THRESHOLD_MIN, C.IMAGE_THRESHOLD_MAX, ""))
    if o.cutoff is not None and not (0 <= o.cutoff <= C.IMAGE_THRESHOLD_MAX):
        v.append(RangeViolation("cutoff", o.cutoff, 0, C.IMAGE_THRESHOLD_MAX, ""))
    if o.cutoff is not None and o.threshold is not None and o.mode == "fiksni" and o.cutoff >= o.threshold:
        v.append(RangeViolation("cutoff", o.cutoff, 0, o.threshold - 1, "", "cutoff mora biti manji od praga"))
    if not (0 <= o.denoise <= C.TRACE_DENOISE_MAX):
        v.append(RangeViolation("uklanjanje_suma", o.denoise, 0, C.TRACE_DENOISE_MAX, ""))
    if not (0.0 <= o.simplify <= C.TRACE_SIMPLIFY_MAX_PX):
        v.append(RangeViolation("pojednostavljenje", o.simplify, 0, C.TRACE_SIMPLIFY_MAX_PX, "px"))
    if v:
        raise OutOfRange("upload_slika", v)


# ------------------------------------------------------------------ priprema bitmape
def load_gray(data: bytes) -> np.ndarray:
    import cv2
    arr = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_UNCHANGED)
    if arr is None:
        raise ValidationFailed("slika", ["slika nije citljiva (ocekivan PNG ili JPG)"])
    if arr.dtype != np.uint8:                  # 16-bitni PNG -> 8 bita
        arr = (arr / 257).astype(np.uint8)
    if arr.ndim == 2:
        return arr
    if arr.shape[2] == 4:                      # providno -> bijela pozadina
        alpha = arr[:, :, 3:4].astype(np.float64) / 255.0
        bgr = arr[:, :, :3].astype(np.float64) * alpha + 255.0 * (1 - alpha)
        arr = np.clip(np.round(bgr), 0, 255).astype(np.uint8)
    return cv2.cvtColor(arr[:, :, :3], cv2.COLOR_BGR2GRAY)


def binarize(gray: np.ndarray, o: TraceOptions) -> np.ndarray:
    """Vraca bool masku: True = crtez (tamno)."""
    import cv2
    g = gray
    if o.invert:
        g = 255 - g
    if o.denoise > 0:
        k = 2 * o.denoise + 1
        g = cv2.GaussianBlur(g, (k, k), 0)
    if o.mode == "fiksni":
        fg = g < o.threshold
    else:
        block = C.TRACE_ADAPTIVE_BLOCK_PX
        fg = cv2.adaptiveThreshold(g, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV,
                                   block, C.TRACE_ADAPTIVE_C) > 0
        if o.threshold is not None:
            fg &= g < o.threshold
    if o.cutoff is not None:
        fg &= g >= o.cutoff
    if o.denoise > 0:
        k = 2 * o.denoise + 1
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
        m = fg.astype(np.uint8) * 255
        m = cv2.morphologyEx(m, cv2.MORPH_OPEN, kernel)
        m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, kernel)
        fg = m > 0
    return fg


def bitmap_png(fg: np.ndarray) -> bytes:
    import cv2
    ok, buf = cv2.imencode(".png", np.where(fg, 0, 255).astype(np.uint8))
    return buf.tobytes()


# ------------------------------------------------------------------ trasiranje
def _fmt(v: float) -> str:
    return f"{v:.3f}".rstrip("0").rstrip(".") or "0"


def _poly_d(pts: np.ndarray) -> str:
    return "M " + " L ".join(f"{_fmt(x)} {_fmt(y)}" for x, y in pts) + " Z"


def _bezier(p0, p1, p2, p3, n):
    t = np.linspace(0, 1, n + 1)[1:, None]
    return ((1 - t) ** 3) * p0 + 3 * ((1 - t) ** 2) * t * p1 + 3 * (1 - t) * t * t * p2 + t ** 3 * p3


def _rdp(pts: np.ndarray, eps: float) -> np.ndarray:
    import cv2
    out = cv2.approxPolyDP(pts.astype(np.float32).reshape(-1, 1, 2), eps, True).reshape(-1, 2)
    return out.astype(np.float64)


def trace_vlastiti(fg: np.ndarray, o: TraceOptions) -> list[str]:
    """Podrazumijevani motor: nas kod iz `core/trasiranje.py`.

    Zasto je on podrazumijevani, a ne potrace: potrace je GPL v2+, pa bi program
    sa njim unutra morao ici sa izvornim kodom. Ovaj motor koristi samo numpy i
    standardnu biblioteku, pa ne vezuje nicije uslove za FabAgent.

    Daje poligone, ne Bezier krivulje. Za rez i gravuru to nije gubitak - G-kod
    je ionako niz pravih linija; krivulja se na kraju svejedno usitni.
    """
    from fabagent.core import trasiranje as TR
    polys = TR.trasiraj(fg, uprosti=o.simplify, min_povrsina=float(C.POTRACE_TURDSIZE))
    return TR.u_putanje(polys)


def trace_potrace(fg: np.ndarray, o: TraceOptions) -> list[str]:
    # potrace je GPL v2+ i vise nije podrazumijevani motor. Ostaje kao izbor za
    # one koji ga vec imaju, ali se smije i deinstalirati - tada ovaj motor samo
    # kaze da ga nema, umjesto da se web server srusi na ImportError.
    try:
        import potrace
    except ImportError:
        raise InvalidParam("motor: potrace nije instaliran na ovom racunaru - "
                           "izaberi 'vlastiti' (podrazumijevani) ili 'konture'") from None
    # potracer.Bitmap sam invertuje ulaz (True = svijetlo), pa mu se daju svijetli pikseli
    plist = potrace.Bitmap(~fg).trace(turdsize=C.POTRACE_TURDSIZE, alphamax=C.POTRACE_ALPHAMAX,
                                      opticurve=True, opttolerance=C.POTRACE_OPTTOLERANCE)
    paths = []
    for curve in plist:
        s = np.array([curve.start_point.x, curve.start_point.y])
        if o.simplify > 0:
            pts = [s]
            cur = s
            for seg in curve.segments:
                end = np.array([seg.end_point.x, seg.end_point.y])
                if seg.is_corner:
                    pts += [np.array([seg.c.x, seg.c.y]), end]
                else:
                    c1, c2 = np.array([seg.c1.x, seg.c1.y]), np.array([seg.c2.x, seg.c2.y])
                    n = max(2, int(math.ceil(np.linalg.norm(end - cur) / C.TRACE_FLATTEN_STEP_PX)))
                    pts += list(_bezier(cur, c1, c2, end, n))
                cur = end
            poly = _rdp(np.array(pts), o.simplify)
            if len(poly) >= 3:
                paths.append(_poly_d(poly))
            continue
        d = [f"M {_fmt(s[0])} {_fmt(s[1])}"]
        for seg in curve.segments:
            if seg.is_corner:
                d.append(f"L {_fmt(seg.c.x)} {_fmt(seg.c.y)} L {_fmt(seg.end_point.x)} {_fmt(seg.end_point.y)}")
            else:
                d.append("C " + " ".join(f"{_fmt(p.x)} {_fmt(p.y)}" for p in (seg.c1, seg.c2, seg.end_point)))
        paths.append(" ".join(d) + " Z")
    return paths


def trace_contours(fg: np.ndarray, o: TraceOptions) -> list[str]:
    import cv2
    mask = fg.astype(np.uint8) * 255
    method = cv2.CHAIN_APPROX_SIMPLE if o.simplify <= 0 else cv2.CHAIN_APPROX_NONE
    contours, _ = cv2.findContours(mask, cv2.RETR_CCOMP, method)
    paths = []
    for c in contours:
        pts = c.reshape(-1, 2).astype(np.float64) + 0.5     # centar piksela
        if o.simplify > 0:
            pts = _rdp(pts, o.simplify)
        if len(pts) >= 3 and abs(cv2.contourArea(pts.astype(np.float32))) >= C.POTRACE_TURDSIZE:
            paths.append(_poly_d(pts))
    return paths


def trace_vtracer(fg: np.ndarray, o: TraceOptions) -> list[str]:
    import xml.etree.ElementTree as ET

    svg = _run_vtracer(bitmap_png(fg), "spline" if o.simplify <= 0 else "polygon")
    root = ET.fromstring(svg)
    paths = []
    for el in root.iter():
        if el.tag.split("}")[-1] != "path":
            continue
        fill = (el.get("fill") or "#000").lower()
        if fill not in ("#000", "#000000", "black"):
            continue                              # binarni mod: samo crtez (crno)
        d = el.get("d") or ""
        tr = el.get("transform") or ""
        m = re.match(r"translate\(\s*([-\d.]+)[ ,]+([-\d.]+)\s*\)", tr)
        if m:
            paths.append((d, float(m.group(1)), float(m.group(2))))
        else:
            paths.append((d, 0.0, 0.0))
    return [f'<g transform="translate({_fmt(dx)},{_fmt(dy)})"><path d="{d}"/></g>' for d, dx, dy in paths]


def trace(fg: np.ndarray, o: TraceOptions) -> tuple[list[str], bool]:
    """Vraca (putanje, raw_elementi). raw_elementi=True znaci da su to vec SVG elementi (vtracer)."""
    if o.engine == "vlastiti":
        return trace_vlastiti(fg, o), False
    if o.engine == "konture":
        return trace_contours(fg, o), False
    if o.engine == "vtracer":
        return trace_vtracer(fg, o), True
    return trace_potrace(fg, o), False

"""Citanje LightBurn projekata: .lbrn (FormatVersion 0) i .lbrn2 (FormatVersion 1).

Struktura je utvrdjena iz stvarnih fajlova (LightBurn 1.2):
  * <Shape Type="Rect|Ellipse|Path|Group|Text|Bitmap" CutIndex="n"> sa <XForm>a b c d tx ty</XForm>
  * Rect (W, H, Cr) i Ellipse (Rx, Ry) su centrirani u (0, 0) svog XForm-a
  * Path, .lbrn2: <VertList>V{x} {y}c0x{..}c0y{..}c1x{..}c1y{..}...</VertList> ("c0x1" bez c0y = nema rucke),
    <PrimList>L{p0} {p1}B{p0} {p1}...</PrimList> ili "LineClosed"; liste se dijele preko VertID / PrimID
  * Path, .lbrn: <V vx vy c0x c0y c1x c1y/> i <P T="L|B" p0 p1/>
  * Bezier p0 -> p1 koristi rucku c0 tacke p0 i rucku c1 tacke p1
  * Group: XForm grupe se mnozi sa XForm-om djece; Text nosi konture u <BackupPath Type="Path">
  * Bitmap: slika (base64) W x H jedinica oko (0, 0), gornja ivica na +H/2
  * <CutSetting type="Cut|Scan|Offset"> i <CutSetting_Img type="Image">: index, speed (mm/s), maxPower,
    numPasses, interval, doOutput, hide, ditherMode
LightBurn koordinate su Y prema gore; radni ekran koristi Y prema dolje, pa se sve okrece oko visine stola.
"""

from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET

from fabagent.core import config as C
from fabagent.core.errors import ValidationFailed

MAX_BYTES = 64 * 1024 * 1024
LAYER_COUNT = 30                       # slojevi 00..29 (T1/T2 iz LightBurn-a nisu slojevi za rad)
KAPPA = 0.5522847498
DEFAULT_INTERVAL_MM = 0.1              # LightBurn ne upisuje interval kada je na polaznoj vrijednosti
IMAGE_THRESHOLD = 128
_NUM = r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?"
_VERT_RE = re.compile(rf"V({_NUM}) ({_NUM})((?:c[01][xy]{_NUM})*)")
_CTRL_RE = re.compile(rf"c([01])([xy])({_NUM})")
_PRIM_RE = re.compile(r"([BL])(\d+) (\d+)")
_MIME = (("iVBOR", "png"), ("/9j/", "jpeg"), ("Qk", "bmp"), ("R0lG", "gif"))
_DITHER = {"jarvis": ("dither", "jarvis"), "stucki": ("dither", "stucki"), "dither": ("dither", "floyd-steinberg"),
           "threshold": ("threshold", None), "grayscale": ("grayscale", None)}
IDENTITY = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def _mul(m, n):
    """m o n (prvo n, pa m); matrice (a, b, c, d, e, f): x' = a x + c y + e, y' = b x + d y + f."""
    return (m[0] * n[0] + m[2] * n[1], m[1] * n[0] + m[3] * n[1], m[0] * n[2] + m[2] * n[3], m[1] * n[2] + m[3] * n[3],
            m[0] * n[4] + m[2] * n[5] + m[4], m[1] * n[4] + m[3] * n[5] + m[5])


def _apply(m, x, y):
    return m[0] * x + m[2] * y + m[4], m[1] * x + m[3] * y + m[5]


def _xform(el) -> tuple:
    txt = el.findtext("XForm")
    if not txt:
        return IDENTITY
    try:
        v = tuple(float(t) for t in txt.split())
    except ValueError:
        return IDENTITY
    return v if len(v) == 6 and all(math.isfinite(t) for t in v) else IDENTITY


def _f(el, name: str, default: float = 0.0) -> float:
    try:
        v = float(el.get(name, default))
    except (TypeError, ValueError):
        return default
    return v if math.isfinite(v) else default


def _fmt(v: float) -> str:
    return f"{v:.4f}".rstrip("0").rstrip(".") or "0"


# ------------------------------------------------------------------ putanje
def _verts_v2(text: str) -> list[tuple]:
    out = []
    for m in _VERT_RE.finditer(text or ""):
        ctrl: dict[str, dict[str, float]] = {"0": {}, "1": {}}
        for c in _CTRL_RE.finditer(m.group(3)):
            ctrl[c.group(1)][c.group(2)] = float(c.group(3))
        pair = lambda d: (d["x"], d["y"]) if "x" in d and "y" in d else None   # noqa: E731 - "c0x1" bez y = nema rucke
        out.append((float(m.group(1)), float(m.group(2)), pair(ctrl["0"]), pair(ctrl["1"])))
    return out


def _verts_v1(shape) -> list[tuple]:
    out = []
    for v in shape.findall("V"):
        def pair(k, v=v):
            return (float(v.get(k + "x")), float(v.get(k + "y"))) if v.get(k + "x") is not None and v.get(k + "y") is not None else None
        out.append((_f(v, "vx"), _f(v, "vy"), pair("c0"), pair("c1")))
    return out


def _path_cmds(verts: list[tuple], prims) -> list[tuple]:
    """prims: "LineClosed" ili lista (vrsta, p0, p1). Vraca naredbe (slovo, tacke...)."""
    n = len(verts)
    if n < 2:
        return []
    if prims == "LineClosed":
        return [("M", verts[0][:2]), *[("L", v[:2]) for v in verts[1:]], ("Z",)]
    cmds, start, end = [], None, None
    for kind, p0, p1 in prims:
        if p0 >= n or p1 >= n:
            continue
        if end != p0:
            cmds.append(("M", verts[p0][:2]))
            start = p0
        if kind == "B":
            cmds.append(("C", verts[p0][2] or verts[p0][:2], verts[p1][3] or verts[p1][:2], verts[p1][:2]))
        else:
            cmds.append(("L", verts[p1][:2]))
        if p1 == start:
            cmds.append(("Z",))
            end = None
        else:
            end = p1
    return cmds


def _rect_cmds(w: float, h: float, r: float) -> list[tuple]:
    x0, y0, x1, y1 = -w / 2, -h / 2, w / 2, h / 2
    r = max(0.0, min(r, w / 2, h / 2))
    if r <= 0:
        return [("M", (x0, y0)), ("L", (x1, y0)), ("L", (x1, y1)), ("L", (x0, y1)), ("Z",)]
    k = r * (1 - KAPPA)
    return [("M", (x0 + r, y0)), ("L", (x1 - r, y0)), ("C", (x1 - k, y0), (x1, y0 + k), (x1, y0 + r)), ("L", (x1, y1 - r)),
            ("C", (x1, y1 - k), (x1 - k, y1), (x1 - r, y1)), ("L", (x0 + r, y1)), ("C", (x0 + k, y1), (x0, y1 - k), (x0, y1 - r)),
            ("L", (x0, y0 + r)), ("C", (x0, y0 + k), (x0 + k, y0), (x0 + r, y0)), ("Z",)]


def _ellipse_cmds(rx: float, ry: float) -> list[tuple]:
    kx, ky = rx * KAPPA, ry * KAPPA
    return [("M", (rx, 0.0)), ("C", (rx, ky), (kx, ry), (0.0, ry)), ("C", (-kx, ry), (-rx, ky), (-rx, 0.0)),
            ("C", (-rx, -ky), (-kx, -ry), (0.0, -ry)), ("C", (kx, -ry), (rx, -ky), (rx, 0.0)), ("Z",)]


def _d(cmds: list[tuple], m: tuple) -> str:
    parts = []
    for c in cmds:
        parts.append(c[0] + " ".join(f"{_fmt(x)} {_fmt(y)}" for x, y in (_apply(m, *p) for p in c[1:])))
    return " ".join(parts)


# ------------------------------------------------------------------ slojevi
def _layer(cs, ranges_note: list[str]) -> dict:
    val = {c.tag: c.get("Value") for c in cs}

    def num(key, default):
        try:
            v = float(val.get(key, default))
        except (TypeError, ValueError):
            return default
        return v if math.isfinite(v) else default
    idx = int(num("index", 0))
    name = val.get("name") or f"C{idx:02d}"
    lo_s, hi_s = (v * 60.0 for v in C.LAYER_SPEED_RANGE_MM_S)
    speed = round(num("speed", 0.0) * 60.0, 1)                     # LightBurn cuva mm/s (2.08333 -> 125 mm/min)
    interval = num("interval", DEFAULT_INTERVAL_MM)
    passes = int(num("numPasses", 1))
    clamp = {"brzina": (speed, lo_s, hi_s, "mm/min"), "interval": (interval, *C.LAYER_INTERVAL_RANGE_MM, "mm"),
             "broj prolaza": (passes, *C.LAYER_PASSES_RANGE, "")}
    fixed = {}
    for label, (v, lo, hi, unit) in clamp.items():
        fixed[label] = min(max(v, lo), hi)
        if fixed[label] != v:
            ranges_note.append(f"Sloj {idx:02d} ({name}): {label} {v:g} {unit} je izvan opsega FabAgent-a "
                               f"({lo:g}–{hi:g}) – postavljeno na {fixed[label]:g}.".replace("  ", " "))
    kind = cs.get("type") or "Cut"
    out = {"index": idx, "name": name, "mode": "Fill" if kind in ("Scan", "Offset") else "Line",
           "speed": fixed["brzina"], "power": min(max(num("maxPower", 0.0), 0.0), 100.0), "passes": int(fixed["broj prolaza"]),
           "interval": fixed["interval"], "output": val.get("doOutput") != "0", "show": val.get("hide") != "1",
           "priority": num("priority", 0.0), "kind": kind}
    if kind == "Image":
        mode, alg = _DITHER.get(val.get("ditherMode") or "", ("dither", "jarvis"))
        if (val.get("ditherMode") or "") not in _DITHER:
            ranges_note.append(f"Sloj slike {idx:02d} ({name}): režim '{val.get('ditherMode')}' ne postoji u FabAgent-u – "
                               "koristi se dither (jarvis).")
        out["image"] = {"mode": mode, "algorithm": alg or "jarvis", "threshold": IMAGE_THRESHOLD,
                        "negative": False}
    return out


# ------------------------------------------------------------------ dokument
def parse(data: bytes, bed_height: float | None = None) -> dict:
    """LightBurn projekat -> {layers, items (oblici i slike redom iz fajla), notes, skipped}."""
    if len(data) > MAX_BYTES:
        raise ValidationFailed("lbrn", [f"fajl je veci od {MAX_BYTES // (1024 * 1024)} MB"])
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise ValidationFailed("lbrn", [f"fajl nije ispravan LightBurn projekat (XML greska: {exc})"]) from None
    if root.tag != "LightBurnProject":
        raise ValidationFailed("lbrn", [f"fajl nije LightBurn projekat (korijenski element je <{root.tag}>)"])
    H = C.LASER_WORK_AREA_MM[1] if bed_height is None else bed_height
    flip = (1.0, 0.0, 0.0, -1.0, 0.0, H)                           # Y prema gore -> Y prema dolje
    notes: list[str] = []
    skipped: dict[str, int] = {}
    vec_layers, img_layers = {}, {}
    for cs in root:
        if cs.tag == "CutSetting":
            ly = _layer(cs, notes)
            vec_layers.setdefault(ly["index"], ly)
        elif cs.tag == "CutSetting_Img":
            ly = _layer(cs, notes)
            img_layers.setdefault(ly["index"], ly)

    raw: list[dict] = []                                           # redom kao u fajlu
    shared_v: dict[str, list] = {}
    shared_p: dict[str, object] = {}

    # Kopije iste slike nose podatke samo jednom; ostale ih nalaze preko SourceHash. Podaci se i ovdje
    # salju jednom ("sources"), a svaka slika nosi samo kljuc.
    sources: dict[str, str] = {}
    by_hash: dict[str, str] = {}

    def source_key(el) -> str:
        return by_hash.get(el.get("SourceHash") or "", "") if not el.get("Data") else el.get("_key", "")

    for n, el in enumerate(e for e in root.iter("Shape") if e.get("Type") == "Bitmap" and e.get("Data")):
        b64 = el.get("Data")
        mime = next((t for sig, t in _MIME if b64.startswith(sig)), "png")
        key = f"s{n}"
        sources[key] = f"data:image/{mime};base64,{b64}"
        el.set("_key", key)
        if el.get("SourceHash") is not None:
            by_hash.setdefault(el.get("SourceHash"), key)

    def cut_index(el) -> int:
        try:
            return int(el.get("CutIndex", "0"))
        except ValueError:
            return 0

    def walk(el, parent):
        kind = el.get("Type")
        m = _mul(parent, _xform(el))
        if kind == "Group":
            for ch in (el.find("Children") if el.find("Children") is not None else []):
                walk(ch, m)
            return
        if kind == "Text":
            bp = el.find("BackupPath")
            if bp is None:
                skipped["tekst bez sačuvane konture"] = skipped.get("tekst bez sačuvane konture", 0) + 1
            else:
                walk(bp, parent)                                   # BackupPath nosi isti XForm kao tekst
            return
        if kind == "Bitmap":
            w, h = _f(el, "W"), _f(el, "H")
            key = source_key(el)
            if key not in sources or w <= 0 or h <= 0:
                skipped["slika bez podataka"] = skipped.get("slika bez podataka", 0) + 1
                return
            # LightBurn slici upisuje Y nadolje u njen vlastiti XForm, pa taj
            # XForm cesto vec nosi negativan `d`. Ako se tome doda jos jedno
            # okretanje, slika izadje naopako (tekst se cita naopako), dok su
            # putanje ispravne. Zato se okretanje dodaje SAMO kad je potrebno:
            # u SVG-u slika stoji uspravno kad je `d` pozitivan.
            mat = _mul(flip, m)
            if mat[3] < 0.0:
                mat = _mul(mat, (1.0, 0.0, 0.0, -1.0, 0.0, 0.0))
            raw.append({"type": "image", "layer": cut_index(el), "src": key, "w": w, "h": h,
                        "matrix": [round(v, 9) for v in mat]})
            return
        if kind == "Rect":
            cmds = _rect_cmds(_f(el, "W"), _f(el, "H"), _f(el, "Cr"))
        elif kind == "Ellipse":
            cmds = _ellipse_cmds(_f(el, "Rx"), _f(el, "Ry"))
        elif kind == "Path":
            vl, pl = el.findtext("VertList"), el.findtext("PrimList")
            vid, pid = el.get("VertID"), el.get("PrimID")
            if vl is not None:
                verts = _verts_v2(vl)
                if vid is not None:
                    shared_v[vid] = verts
            elif el.find("V") is not None:
                verts = _verts_v1(el)
            else:
                verts = shared_v.get(vid or "", [])
            if pl is not None:
                prims = "LineClosed" if pl.strip() == "LineClosed" else [(k, int(a), int(b)) for k, a, b in _PRIM_RE.findall(pl)]
                if pid is not None:
                    shared_p[pid] = prims
            elif el.find("P") is not None:
                prims = [(p.get("T") or "L", int(p.get("p0", 0)), int(p.get("p1", 0))) for p in el.findall("P")]
            else:
                prims = shared_p.get(pid or "", [])
            cmds = _path_cmds(verts, prims)
        else:
            skipped[f"oblik {kind}"] = skipped.get(f"oblik {kind}", 0) + 1
            return
        if len(cmds) < 2:
            skipped["prazna putanja"] = skipped.get("prazna putanja", 0) + 1
            return
        raw.append({"type": "path", "layer": cut_index(el), "d": _d(cmds, _mul(flip, m))})

    for sh in root.findall("Shape"):
        walk(sh, IDENTITY)

    # slojevi: LightBurn ima odvojene postavke za vektore i slike sa istim brojem sloja
    def fit(i: int) -> int:
        return i if 0 <= i < LAYER_COUNT else LAYER_COUNT - 1
    vec_used = {fit(it["layer"]) for it in raw if it["type"] == "path"}
    img_used = {fit(it["layer"]) for it in raw if it["type"] == "image"}
    if any(not (0 <= it["layer"] < LAYER_COUNT) for it in raw):
        notes.append(f"Oblici sa slojevima izvan 00–29 (alatni slojevi T1/T2) su stavljeni u sloj {LAYER_COUNT - 1}.")
    img_target: dict[int, int] = {}
    taken = set(vec_used)
    for i in sorted(img_used):
        if i in taken:                                             # isti broj koriste i vektori i slike
            free = next((k for k in range(LAYER_COUNT) if k not in taken and k not in img_used), None)
            if free is None:
                free = i
            else:
                notes.append(f"Slike iz sloja {i:02d} su stavljene u sloj {free:02d} jer isti broj koriste i vektorski oblici "
                             "sa drugačijim postavkama.")
            img_target[i] = free
        else:
            img_target[i] = i
        taken.add(img_target[i])
    layers = []
    for i in sorted(vec_used):
        if i in vec_layers:
            layers.append(dict(vec_layers[i], index=i))
    for i in sorted(img_used):
        src = img_layers.get(i) or vec_layers.get(i)
        if src:
            layers.append(dict(src, index=img_target[i]))
    layers.sort(key=lambda ly: (ly["priority"], ly["index"]))
    default_img = {"mode": "dither", "algorithm": "jarvis", "threshold": IMAGE_THRESHOLD, "negative": False}
    items = []
    for it in raw:
        i = fit(it["layer"])
        if it["type"] == "image":
            items.append(dict(it, layer=img_target[i], opts=(img_layers.get(i) or {}).get("image", default_img)))
        else:
            items.append(dict(it, layer=i))
    if any(it["type"] == "image" for it in items):
        notes.append("Podešavanja slike iz LightBurn-a (gama, kontrast, svjetlina, izoštravanje) se ne prenose.")
    return {"layers": [{k: v for k, v in ly.items() if k not in ("priority", "kind", "image")} for ly in layers],
            "items": items, "sources": {k: v for k, v in sources.items() if any(it.get("src") == k for it in items)},
            "notes": notes, "skipped": skipped, "app_version": root.get("AppVersion", ""),
            "counts": {"paths": sum(it["type"] == "path" for it in items), "images": sum(it["type"] == "image" for it in items)}}

"""G-code generator za GRBL diodni laser (ArgoLaser MK20).

* jedinice mm (G21), apsolutne koordinate (G90)
* laser: M4 (dinamicka snaga, $32=1) ili M3 - iz config.GRBL_LASER_MODE_CMD
* G0 = brzi pomak (laser ugasen u GRBL laser modu), G1 = rad sa snagom S i brzinom F
* kraj programa uvijek M5 (laser ugasen)
* Cut/Score: konture (unutrasnje prije vanjskih, najbliza sljedeca), Engrave: popuna linijama
  (scanline, even-odd, dvosmjerno, prelet bez snage na krajevima)
* SVG/editor koordinate su gore-lijevo (y prema dolje); masina dolje-lijevo -> Y se okrece.

Koordinate se NE pogadjaju: dizajn se postavlja na sto eksplicitnim pomakom (X, Y u mm,
gornji lijevi ugao dokumenta) ili je vec raspored na cijelom stolu (editor / ploce 400x400).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from fabagent.core import config as C
from fabagent.core import geometry2d as g2
from fabagent.core.errors import InvalidGeometry, InvalidParam, MissingParam, OutOfRange, RangeViolation


@dataclass(frozen=True)
class LayerSetting:
    mode: str               # "Cut" | "Engrave" | "Score"
    speed_mm_s: float
    power_pct: float
    passes: int
    interval_mm: float | None = None   # samo Engrave

    def as_dict(self) -> dict:
        return {"mode": self.mode, "speed_mm_s": self.speed_mm_s, "power_pct": self.power_pct,
                "passes": self.passes, "interval_mm": self.interval_mm}

    @property
    def s_value(self) -> int:
        return int(round(self.power_pct / 100.0 * C.GRBL_S_MAX))

    @property
    def feed(self) -> float:
        return self.speed_mm_s * 60.0


def preset_layer_settings(preset) -> dict:
    """Polazne postavke slojeva iz preseta materijala (eksplicitni podaci iz presets.py)."""
    out = {}
    for layer in C.LAYERS:
        s = {"CUT": preset.cut, "ENGRAVE": preset.engrave, "SCORE": preset.score}[layer]
        if s is None:
            continue
        out[layer] = {"mode": C.LAYER_DEFAULT_MODE[layer], "speed_mm_s": s.speed_mm_s, "power_pct": s.power_pct,
                      "passes": s.passes, "interval_mm": s.line_interval_mm or preset.engrave.line_interval_mm}
    return out


def parse_layer_settings(raw: dict, used_layers: list[str]) -> dict[str, LayerSetting]:
    """raw: {layer: {mode, speed_mm_s, power_pct, passes, interval_mm}} - svi korisceni slojevi obavezni."""
    missing, violations, result = [], [], {}
    for layer in used_layers:
        d = raw.get(layer)
        if not isinstance(d, dict):
            missing.append(f"{layer} (postavke sloja)")
            continue
        keys = ["mode", "speed_mm_s", "power_pct", "passes"]
        if d.get("mode") == "Engrave":
            keys.append("interval_mm")
        lack = [k for k in keys if d.get(k) in (None, "")]
        if lack:
            missing += [f"{layer}.{k}" for k in lack]
            continue
        if d["mode"] not in C.LAYER_MODES:
            raise InvalidParam(f"{layer}.mode: '{d['mode']}' nije dozvoljeno, izbor: {list(C.LAYER_MODES)}")
        try:
            speed, power = float(d["speed_mm_s"]), float(d["power_pct"])
            passes = int(d["passes"])
            interval = float(d["interval_mm"]) if d["mode"] == "Engrave" else None
            if float(d["passes"]) != passes:
                raise ValueError
        except (TypeError, ValueError):
            raise InvalidParam(f"{layer}: brzina/snaga/prolazi/interval moraju biti brojevi (prolazi cijeli broj)") from None
        for name, v, (lo, hi) in ((f"{layer}.speed_mm_s", speed, C.LAYER_SPEED_RANGE_MM_S),
                                  (f"{layer}.power_pct", power, C.LAYER_POWER_RANGE_PCT),
                                  (f"{layer}.passes", passes, C.LAYER_PASSES_RANGE)):
            if not (lo <= v <= hi):
                violations.append(RangeViolation(name, v, lo, hi))
        if interval is not None and not (C.LAYER_INTERVAL_RANGE_MM[0] <= interval <= C.LAYER_INTERVAL_RANGE_MM[1]):
            violations.append(RangeViolation(f"{layer}.interval_mm", interval, *C.LAYER_INTERVAL_RANGE_MM))
        result[layer] = LayerSetting(d["mode"], speed, power, passes, interval)
    if missing:
        raise MissingParam("slojevi", missing)
    if violations:
        raise OutOfRange("slojevi", violations)
    return result


# ------------------------------------------------------------------ geometrija -> masina
class _Writer:
    def __init__(self, ox: float, oy: float):
        self.ox, self.oy = ox, oy
        self.lines: list[str] = []
        self.pos = np.array([0.0, 0.0])           # u koordinatama dokumenta (prije pomaka)
        self.burn_mm = 0.0
        self.travel_mm = 0.0
        self.burn_s = 0.0
        self.bed_w, self.bed_h = C.LASER_WORK_AREA_MM

    def xy(self, p) -> str:
        X = p[0] + self.ox
        Y = self.bed_h - (p[1] + self.oy)
        d = C.GCODE_DECIMALS
        return f"X{X:.{d}f} Y{Y:.{d}f}"

    def g0(self, p):
        p = np.asarray(p, float)
        self.travel_mm += float(np.linalg.norm(p - self.pos))
        self.lines.append(f"G0 {self.xy(p)}")
        self.pos = p

    def g1(self, p, s: int, feed: float, burn: bool = True):
        p = np.asarray(p, float)
        dist = float(np.linalg.norm(p - self.pos))
        if dist <= C.GEOMETRY_EPS_MM:
            return
        self.lines.append(f"G1 {self.xy(p)} S{s} F{feed:.0f}")
        if burn and s > 0:
            self.burn_mm += dist
        self.burn_s += dist / (feed / 60.0)
        self.pos = p


def _order_rings(rings: list[np.ndarray], start: np.ndarray) -> list[np.ndarray]:
    """Unutrasnje konture prije vanjskih (dubina ugnjezdjenja), unutar iste dubine najbliza sljedeca."""
    from fabagent.laser.design import cut_nesting_depth
    depths = cut_nesting_depth(rings) if len(rings) > 1 else [0] * len(rings)
    out, pos = [], start
    for depth in sorted(set(depths), reverse=True):
        pool = [r for r, dd in zip(rings, depths) if dd == depth]
        while pool:
            best = min(range(len(pool)), key=lambda i: (float(np.min(np.linalg.norm(pool[i] - pos, axis=1))), i))
            ring = pool.pop(best)
            k = int(np.argmin(np.linalg.norm(ring - pos, axis=1)))   # pocni od najblizeg vrha
            ring = np.roll(ring, -k, axis=0)
            out.append(ring)
            pos = ring[0]
    return out


def _vector_layer(w: _Writer, rings: list[np.ndarray], st: LayerSetting,
                  otvorene: list[np.ndarray] | None = None):
    for _ in range(st.passes):
        for ring in _order_rings(rings, w.pos):
            w.g0(ring[0])
            for p in ring[1:]:
                w.g1(p, st.s_value, st.feed)
            w.g1(ring[0], st.s_value, st.feed)      # zatvori konturu
        # Otvorene linije se prolaze od pocetka do kraja i tu se staje. Vracanje
        # na pocetak bi izgorjelo isti potez dva puta.
        for linija in _order_rings(list(otvorene or []), w.pos):
            w.g0(linija[0])
            for p in linija[1:]:
                w.g1(p, st.s_value, st.feed)


def scanline_spans(rings: list[np.ndarray], y: float) -> list[tuple[float, float]]:
    """Even-odd presjeci horizontale y sa konturama -> lista (x0, x1) segmenata za paljenje."""
    xs = []
    for r in rings:
        a, b = r, np.roll(r, -1, axis=0)
        cond = (a[:, 1] > y) != (b[:, 1] > y)
        if not cond.any():
            continue
        aa, bb = a[cond], b[cond]
        xs.extend((aa[:, 0] + (y - aa[:, 1]) * (bb[:, 0] - aa[:, 0]) / (bb[:, 1] - aa[:, 1])).tolist())
    xs.sort()
    return [(xs[i], xs[i + 1]) for i in range(0, len(xs) - 1, 2) if xs[i + 1] - xs[i] > C.GEOMETRY_EPS_MM]


def _fill_layer(w: _Writer, rings: list[np.ndarray], st: LayerSetting):
    x0, y0, x1, y1 = g2.bbox(rings)
    rows = int(math.ceil((y1 - y0) / st.interval_mm))
    over = C.ENGRAVE_OVERSCAN_MM
    for _ in range(st.passes):
        forward = True
        for k in range(rows):
            y = y0 + (k + 0.5) * st.interval_mm
            spans = scanline_spans(rings, y)
            if not spans:
                continue
            if not forward:
                spans = [(b, a) for a, b in reversed(spans)]
            sign = 1 if forward else -1
            w.g0((spans[0][0] - sign * over, y))
            for a, b in spans:
                w.g1((a, y), 0, st.feed, burn=False)
                w.g1((b, y), st.s_value, st.feed)
            w.g1((spans[-1][1] + sign * over, y), 0, st.feed, burn=False)
            forward = not forward


@dataclass
class GcodeResult:
    text: str
    stats: dict = field(default_factory=dict)


def _check_bounds(design, ox: float, oy: float) -> None:
    W, H = C.LASER_WORK_AREA_MM
    rings = ([r for it in design.items for r in it.rings]
             + [o for it in design.items for o in it.otvorene])
    if not rings:
        raise InvalidGeometry("dizajn nema nijednu putanju", "gcode")
    x0, y0, x1, y1 = g2.bbox(rings)
    over = C.ENGRAVE_OVERSCAN_MM if design.rings("ENGRAVE") else 0.0
    if x0 + ox - over < -C.GEOMETRY_EPS_MM or y0 + oy < -C.GEOMETRY_EPS_MM or \
            x1 + ox + over > W + C.GEOMETRY_EPS_MM or y1 + oy > H + C.GEOMETRY_EPS_MM:
        raise OutOfRange("pozicija", [RangeViolation(
            "pozicija", 0, None, None, "mm",
            f"dizajn ({x0 + ox:.1f}..{x1 + ox:.1f}, {y0 + oy:.1f}..{y1 + oy:.1f} mm) izlazi izvan radne povrsine "
            f"{W:g} x {H:g} mm (ukljucujuci prelet {over:g} mm kod graviranja)")])


def _kljuc_putanje(r, rez_mm: float = 0.05) -> tuple:
    """Otisak putanje neosjetljiv na pocetnu tacku i smjer obilaska.

    Rasterizuje se na 0.05 mm, pa dvije putanje koje se poklapaju unutar te
    tolerancije daju isti kljuc. Koristi se da se ista geometrija ne izgori
    dva puta (CorelDRAW izvozi liniju i kao .fil i kao .str).
    """
    t = [(round(float(x) / rez_mm), round(float(y) / rez_mm)) for x, y in r]
    if len(t) > 1 and t[0] == t[-1]:
        t = t[:-1]
    if not t:
        return ()
    n = len(t)
    k = min(range(n), key=lambda i: t[i])
    napr = tuple(t[(k + i) % n] for i in range(n))
    nazad = tuple(t[(k - i) % n] for i in range(n))
    return min(napr, nazad)


def _bez_duplih(rings, vec_izgorene: set) -> list:
    """Izbacuje putanje koje su vec izgorene u nekom ranijem sloju ili ranije
    u istom sloju. Bez ovoga laser dva puta prelazi preko iste linije."""
    ostaju = []
    for r in rings:
        k = _kljuc_putanje(r)
        if not k or k in vec_izgorene:
            continue
        vec_izgorene.add(k)
        ostaju.append(r)
    return ostaju


def generate(design, settings: dict[str, LayerSetting], offset: tuple[float, float], title: str = "") -> GcodeResult:
    ox, oy = offset
    _check_bounds(design, ox, oy)
    w = _Writer(ox, oy)
    w.lines += [f"; FabAgent G-code - {title}".rstrip(" -"),
                f"; masina: {C.LASER_MACHINE_NAME}, ishodiste {C.MACHINE_ORIGIN}, S max {C.GRBL_S_MAX}",
                f"; pomak dizajna na stolu: X{ox:g} Y{oy:g} mm (gornji lijevi ugao dokumenta)",
                "G21 ; mm", "G90 ; apsolutno", "M5", f"{C.GRBL_LASER_MODE_CMD} S0"]
    used = []
    izgorene: set = set()
    preskoceno = 0
    for layer in C.LAYER_EXECUTION_ORDER:
        rings = design.rings(layer)
        otvorene = design.otvorene(layer)
        if not rings and not otvorene:
            continue
        prije = len(rings) + len(otvorene)
        rings = _bez_duplih(rings, izgorene)
        otvorene = _bez_duplih(otvorene, izgorene)
        preskoceno += prije - len(rings) - len(otvorene)
        if not rings and not otvorene:
            continue
        st = settings[layer]
        used.append(layer)
        w.lines.append(f"; --- sloj {layer}: {st.mode}, {st.speed_mm_s:g} mm/s, {st.power_pct:g} %, "
                       f"{st.passes} prolaz(a)" + (f", interval {st.interval_mm:g} mm" if st.mode == "Engrave" else ""))
        if st.mode == "Engrave":
            _fill_layer(w, rings, st)
            if otvorene:
                _vector_layer(w, [], st, otvorene)
        else:
            _vector_layer(w, rings, st, otvorene)
    if preskoceno:
        w.lines.insert(5, f"; preskoceno {preskoceno} duplih putanja "
                          f"(ista geometrija vec izgorena)")
    w.lines += ["M5 ; laser ugasen", "G0 X0 Y0", "M2"]
    travel_s = w.travel_mm / (C.GRBL_TRAVEL_FEED_MM_MIN / 60.0)
    stats = {"lines": len(w.lines), "layers": used, "burn_mm": round(w.burn_mm, 1),
             "duple_preskocene": preskoceno,
             "travel_mm": round(w.travel_mm, 1), "time_s": round(w.burn_s + travel_s, 1),
             "offset_mm": [ox, oy], "settings": {k: settings[k].as_dict() for k in used}}
    return GcodeResult("\n".join(w.lines) + "\n", stats)


def frame_gcode(design, offset: tuple[float, float], feed_mm_s: float) -> GcodeResult:
    """Uokvirivanje: obilazak granicnog pravougaonika SAMO G0/G1 sa S0 i M5 - laser ostaje ugasen."""
    ox, oy = offset
    _check_bounds(design, ox, oy)
    x0, y0, x1, y1 = g2.bbox([r for it in design.items for r in it.rings])
    w = _Writer(ox, oy)
    feed = feed_mm_s * 60.0
    w.lines += ["; FabAgent FRAME - laser ugasen (M5, S0)", "G21", "G90", "M5"]
    w.g0((x0, y0))
    for p in ((x1, y0), (x1, y1), (x0, y1), (x0, y0)):
        w.lines.append(f"G1 {w.xy(p)} S0 F{feed:.0f}")
    w.lines += ["M5"]
    return GcodeResult("\n".join(w.lines) + "\n", {"bbox_mm": [x0 + ox, y0 + oy, x1 + ox, y1 + oy]})


def design_from_svg(svg_text: str):
    """SVG (FabAgent standard) -> LaserDesign. Neispravan SVG -> InvalidGeometry."""
    import xml.etree.ElementTree as ET

    from fabagent.laser.design import LaserDesign
    from fabagent.laser.svg_validator import SVG_NS, parse_path_d, validate_svg_text
    rep = validate_svg_text(svg_text)
    if not rep["ok"]:
        raise InvalidGeometry(rep["errors"], "gcode")
    root = ET.fromstring(svg_text)
    w, h = rep["document_mm"]
    d = LaserDesign(w, h)
    for g in root.findall(f"{{{SVG_NS}}}g"):
        for p in g.findall(f"{{{SVG_NS}}}path"):
            d.add(g.get("id"), parse_path_d(p.get("d")), p.get("id") or "")
    return d

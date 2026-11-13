"""Burence za vino (lezece bure) od ravnih ploca.

Konstrukcija:
  * prednja i zadnja strana: krug promjera D sa N proreza po obodu i rupom za bocu,
  * N dasaka: pravougaonik sa cepom na svakom kraju (cep ulazi u prorez strane) i
    dvije rupe za "prstenove" (kanap/zica) na 1/3 i 2/3 duzine,
  * opciono: baza (kutija otvorena sprijeda), dva sedla na kojima bure lezi i fioka.

Geometrija dasaka: vanjske stranice dasaka cine pravilni N-ugao sa apotemom
a_out = D/2 - rub; unutrasnje ivice susjednih dasaka se dodiruju, pa je sirina
daske w = 2 * a_in * tan(pi / N), a_in = a_out - debljina. Ugao izmedju dasaka je
360/N stepeni.

Svi dijelovi se rasporedjuju (nesting) na ploce velicine radne povrsine lasera, sa
razmakom NEST_GAP_MM izmedju dijelova; ako ne stanu na jednu plocu, prave se nove.
"""

from __future__ import annotations

import math

import numpy as np

from fabagent.core import config as C
from fabagent.core import geometry2d as g2
from fabagent.core.decisions import DecisionLog
from fabagent.core.errors import InvalidGeometry, InvalidParam
from fabagent.core.validator import ValidatedParams, raise_violations, require_range
from fabagent.laser.design import LaserDesign
from fabagent.laser.nesting import Part, pack
from fabagent.laser.presets import MaterialPreset, require_cut

NAME = "burence"


def _rect(x0, y0, x1, y1) -> np.ndarray:
    return np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1]], dtype=float)


def _rotated_rect(cx, cy, w_tan, h_rad, angle) -> np.ndarray:
    """Pravougaonik centriran u (cx, cy): w_tan po tangenti, h_rad po radijusu."""
    t = np.array([-math.sin(angle), math.cos(angle)])   # tangenta
    r = np.array([math.cos(angle), math.sin(angle)])    # radijus
    c = np.array([cx, cy])
    hw, hh = w_tan / 2, h_rad / 2
    return np.array([c - t * hw - r * hh, c + t * hw - r * hh, c + t * hw + r * hh, c - t * hw + r * hh])


def geometry(D: float, L: float, n: int, t: float) -> dict:
    a_out = D / 2 - C.BARREL_RIM_MM
    a_in = a_out - t
    w = 2 * a_in * math.tan(math.pi / n)
    tab = w * C.BARREL_TAB_WIDTH_RATIO
    return {"a_out": a_out, "a_in": a_in, "w": w, "tab": tab, "angle_deg": 360.0 / n,
            "stave_body": L - 2 * t, "stave_len": L}


def stave(L: float, w: float, tab: float, t: float) -> list[np.ndarray]:
    y0, y1 = (w - tab) / 2, (w + tab) / 2
    outline = np.array([
        [t, 0], [L - t, 0], [L - t, y0], [L, y0], [L, y1], [L - t, y1], [L - t, w],
        [t, w], [t, y1], [0, y1], [0, y0], [t, y0]], dtype=float)
    holes = [g2.circle(L * k / 3, w / 2, C.BARREL_RING_HOLE_D_MM / 2) for k in (1, 2)]
    return [outline] + holes


def end_plate(D: float, n: int, geo: dict, t: float, bottle_d: float) -> list[np.ndarray]:
    R = D / 2
    rings = [g2.circle(R, R, R)]
    rc = geo["a_out"] - t / 2
    c = C.BARREL_SLOT_CLEARANCE_MM
    for k in range(n):
        ang = 2 * math.pi * k / n
        rings.append(_rotated_rect(R + rc * math.cos(ang), R + rc * math.sin(ang), geo["tab"] + c, t + c, ang))
    rings.append(g2.circle(R, R, (bottle_d + C.BARREL_BOTTLE_CLEARANCE_MM) / 2))
    return rings


def base_parts(D: float, L: float, t: float) -> list[Part]:
    """Baza otvorena sprijeda + 2 sedla + fioka (spojevi lijepljenjem, sucelno)."""
    Wb = D * C.BARREL_BASE_WIDTH_RATIO
    Hb = D * C.BARREL_BASE_HEIGHT_RATIO
    R = D / 2
    cl = C.DRAWER_CLEARANCE_MM
    depth = R - math.sqrt(R * R - (Wb / 2) ** 2)          # dubina luka sedla
    Hs = depth + C.BARREL_SADDLE_MIN_MATERIAL_MM
    # sedlo: pravougaonik Wb x Hs sa lukom radijusa R odozgo
    arc_c = (Wb / 2, depth - R)                          # centar luka (iznad sedla, y prema dolje)
    xs = np.linspace(Wb, 0, g2.arc_segments(R, 2 * math.asin(Wb / (2 * R))) + 1)
    ys = arc_c[1] + np.sqrt(np.maximum(R * R - (xs - arc_c[0]) ** 2, 0.0))
    saddle = np.vstack([[[0, Hs], [Wb, Hs]], np.column_stack([xs, ys])])
    saddle = g2.dedupe(saddle)
    parts = [
        Part("baza_dno", [_rect(0, 0, L, Wb)]),
        Part("baza_vrh", [_rect(0, 0, L, Wb)]),
        Part("baza_bok_1", [_rect(0, 0, L, Hb - 2 * t)]),
        Part("baza_bok_2", [_rect(0, 0, L, Hb - 2 * t)]),
        Part("baza_ledja", [_rect(0, 0, Wb - 2 * t, Hb - 2 * t)]),
        Part("sedlo_1", [saddle]),
        Part("sedlo_2", [saddle.copy()]),
    ]
    # fioka: unutrasnjost baze minus zazor; prednja ploca pokriva otvor
    Wd = Wb - 2 * t - 2 * cl
    Hd = Hb - 2 * t - cl
    Ld = L - t - cl
    front = [_rect(0, 0, Wb, Hb), g2.circle(Wb / 2, Hb / 2, C.DRAWER_KNOB_HOLE_D_MM / 2)]
    parts += [
        Part("fioka_prednja", front),
        Part("fioka_dno", [_rect(0, 0, Ld - t, Wd)]),
        Part("fioka_bok_1", [_rect(0, 0, Ld - t, Hd - t)]),
        Part("fioka_bok_2", [_rect(0, 0, Ld - t, Hd - t)]),
        Part("fioka_ledja", [_rect(0, 0, Wd - 2 * t, Hd - t)]),
    ]
    return parts


def build(p: ValidatedParams, preset: MaterialPreset, log: DecisionLog) -> list[LaserDesign]:
    require_cut(preset)
    D, L, n, t = p["promjer_mm"], p["visina_mm"], p["broj_dasaka"], p["debljina_mm"]
    bottle = p["promjer_boce_mm"]
    if preset.thickness_mm is None or abs(preset.thickness_mm - t) > C.GEOMETRY_EPS_MM:
        raise InvalidParam(f"debljina_mm: {t:g} mm ne odgovara materijalu '{preset.key}' "
                           f"({preset.thickness_mm} mm); cepovi i prorezi zavise od stvarne debljine")
    geo = geometry(D, L, n, t)
    wall = C.LASER_MIN_WALL_MM
    R = D / 2
    slot_corner_r = math.hypot(geo["a_out"] + C.BARREL_SLOT_CLEARANCE_MM / 2,
                               (geo["tab"] + C.BARREL_SLOT_CLEARANCE_MM) / 2)
    hole_r = (bottle + C.BARREL_BOTTLE_CLEARANCE_MM) / 2
    min_w = C.BARREL_RING_HOLE_D_MM + 2 * wall
    raise_violations(NAME, [
        require_range(NAME, "broj_dasaka", n, None,
                      math.floor(math.pi / math.atan(min_w / (2 * geo["a_in"]))) if geo["a_in"] > 0 else 0,
                      unit="", note=f"daska bi bila uza od {min_w:g} mm (rupa za prsten + zidovi)"),
        require_range(NAME, "promjer_boce_mm", bottle, None,
                      2 * (geo["a_in"] - C.BARREL_SLOT_CLEARANCE_MM / 2 - wall) - C.BARREL_BOTTLE_CLEARANCE_MM,
                      note="rupa za bocu mora ostati unutar proreza za daske"),
        require_range(NAME, "visina_mm", L, 3 * C.BARREL_RING_HOLE_D_MM + 2 * t + 2 * wall, None,
                      note="daska mora imati mjesta za cepove i rupe"),
    ])
    rim_wall = R - slot_corner_r
    if rim_wall < wall - C.GEOMETRY_EPS_MM:
        raise InvalidGeometry(
            f"zid izmedju ugla proreza i oboda strane je {rim_wall:.2f} mm < {wall} mm "
            f"(cep {geo['tab']:.1f} mm je preširok za promjer {D:g} mm); povecaj broj_dasaka", NAME)
    log.record("geometrija", f"burence D={D:g} L={L:g} mm, {n} dasaka, ugao {geo['angle_deg']:.3f} deg",
               sirina_daske_mm=round(geo["w"], 3), cep_mm=round(geo["tab"], 3),
               apotema_vanjska_mm=round(geo["a_out"], 3), apotema_unutarnja_mm=round(geo["a_in"], 3),
               zid_iznad_proreza_mm=round(rim_wall, 3), rupa_za_bocu_mm=round(2 * hole_r, 3))

    parts = [Part("strana_prednja", end_plate(D, n, geo, t, bottle), allow_rotate=False),
             Part("strana_zadnja", end_plate(D, n, geo, t, bottle), allow_rotate=False)]
    parts += [Part(f"daska_{k + 1:02d}", stave(L, geo["w"], geo["tab"], t)) for k in range(n)]
    if p["ima_fioku"]:
        parts += base_parts(D, L, t)
        log.record("geometrija", "baza sa fiokom: "
                   f"{L:g} x {D * C.BARREL_BASE_WIDTH_RATIO:.1f} x {D * C.BARREL_BASE_HEIGHT_RATIO:.1f} mm "
                   "(omjeri iz config.py), spojevi sucelno - lijepiti")

    sw, sh = C.LASER_WORK_AREA_MM
    placements, sheets = pack(parts, sw, sh, C.NEST_GAP_MM, C.LASER_DOC_PADDING_MM)
    log.record("raspored", f"{len(parts)} dijelova na {sheets} ploca {sw:g}x{sh:g} mm, razmak {C.NEST_GAP_MM:g} mm",
               dijelovi={pl.part.name: [pl.sheet + 1, round(pl.x, 2), round(pl.y, 2), pl.rotated]
                         for pl in placements})
    designs = [LaserDesign(sw, sh) for _ in range(sheets)]
    for pl in placements:
        rings = [pl.transform(r) for r in pl.part.cut]
        # unutrasnje konture prve, vanjska zadnja (laser rezuje rupe prije odvajanja dijela)
        designs[pl.sheet].add("CUT", rings[1:], f"{pl.part.name}_unutra")
        designs[pl.sheet].add("CUT", rings[:1], pl.part.name)
    return designs

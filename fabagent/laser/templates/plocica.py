"""Plocica / drzac sa 2 ili 4 rupe za vijke i opcionim tekstom."""

from __future__ import annotations

import numpy as np

from fabagent.core import config as C
from fabagent.core import geometry2d as g2
from fabagent.core.decisions import DecisionLog
from fabagent.core.errors import InvalidGeometry
from fabagent.core.validator import ValidatedParams, raise_violations, require_range
from fabagent.laser.design import LaserDesign
from fabagent.laser.font import place_text, render_text
from fabagent.laser.presets import MaterialPreset, require_cut
from fabagent.laser.templates._common import text_clearance_problems

NAME = "plocica"


def hole_centers(w: float, h: float, n: int, off: float, x0: float, y0: float) -> list[tuple[float, float]]:
    if n == 2:
        return [(x0 + off, y0 + h / 2), (x0 + w - off, y0 + h / 2)]
    return [(x0 + off, y0 + off), (x0 + w - off, y0 + off),
            (x0 + w - off, y0 + h - off), (x0 + off, y0 + h - off)]


def build(p: ValidatedParams, preset: MaterialPreset, log: DecisionLog) -> LaserDesign:
    require_cut(preset)
    w, h, n = p["sirina"], p["visina"], p["broj_rupa"]
    d, off, rc = p["rupa_promjer"], p["rupa_odmak"], p["radijus_ugla"]
    r = d / 2
    wall = C.LASER_MIN_WALL_MM
    raise_violations(NAME, [
        require_range(NAME, "radijus_ugla", rc, 0, min(w, h) / 2, note="max pola krace stranice"),
        require_range(NAME, "rupa_odmak", off, r + wall, None, note="zid izmedju rupe i ivice"),
        require_range(NAME, "sirina", w, 2 * off + d + wall, None, note="rupe se ne smiju spojiti"),
        require_range(NAME, "visina", h, (2 * off + d + wall) if n == 4 else d + 2 * wall, None,
                      note="rupe/zid po visini"),
    ])

    pad = C.LASER_DOC_PADDING_MM
    design = LaserDesign(w + 2 * pad, h + 2 * pad)
    outline = g2.rounded_rect(pad, pad, pad + w, pad + h, rc)
    centers = hole_centers(w, h, n, off, pad, pad)
    holes = [g2.circle(cx, cy, r) for cx, cy in centers]
    for k, c in enumerate(centers):
        edge_wall = g2.min_distance_points_to_polyline(np.array([c]), outline) - r
        if edge_wall < wall - C.GEOMETRY_EPS_MM:
            raise InvalidGeometry(f"rupa #{k + 1}: zid do ivice {edge_wall:.2f} mm < {wall} mm "
                                  "(provjeri radijus_ugla i rupa_odmak)", NAME)
    log.record("geometrija", f"plocica {w:g}x{h:g} mm, {n} rupe D={d:g} mm na odmaku {off:g} mm")

    if p["tekst"]:
        geo = render_text(p["tekst"], p["visina_teksta"], C.LABEL_LETTER_SPACING_CELLS)
        rings = place_text(geo, pad + w / 2, pad + h / 2)
        problems = text_clearance_problems(rings, outline, holes, C.TEXT_EDGE_MARGIN_MM)
        if problems:
            raise InvalidGeometry(problems + [f"sirina teksta {geo.width:.1f} mm"], NAME)
        design.add("ENGRAVE", rings, "tekst")
        log.record("tekst", f"'{p['tekst']}' visina {geo.cap_height:g} mm, sirina {geo.width:.2f} mm")
    else:
        log.record("tekst", "bez teksta (tekst je prazan); visina_teksta se ne koristi")

    design.add("CUT", holes, "rupe")
    design.add("CUT", [outline], "kontura")
    return design

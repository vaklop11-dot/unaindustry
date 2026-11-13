"""Privjesak: krug, pravougaonik, srce, pas, macka + rupa za prsten + graviran tekst."""

from __future__ import annotations

import math

import numpy as np

from fabagent.core import config as C
from fabagent.core import geometry2d as g2
from fabagent.core.decisions import DecisionLog
from fabagent.core.errors import InvalidGeometry
from fabagent.core.validator import ValidatedParams, raise_violations, require_range
from fabagent.laser.design import LaserDesign
from fabagent.laser.font import place_text, render_text
from fabagent.laser.presets import MaterialPreset, require_cut
from fabagent.laser.templates._common import (horizontal_span, text_clearance_problems,
                                              vertical_entry_y)

NAME = "privesak"


def _heart(x0, y0, w, h) -> np.ndarray:
    t = np.arange(C.HEART_SAMPLES) * (2 * math.pi / C.HEART_SAMPLES)
    x = 16 * np.sin(t) ** 3
    y = -(13 * np.cos(t) - 5 * np.cos(2 * t) - 2 * np.cos(3 * t) - np.cos(4 * t))
    return g2.normalize_to_box(np.column_stack([x, y]), x0, y0, w, h)


def outline_for(shape: str, x0: float, y0: float, w: float, h: float, corner_r: float) -> np.ndarray:
    if shape == "krug":
        return g2.circle(x0 + w / 2, y0 + h / 2, w / 2)
    if shape == "pravougaonik":
        return g2.rounded_rect(x0, y0, x0 + w, y0 + h, corner_r)
    if shape == "srce":
        return _heart(x0, y0, w, h)
    control = C.DOG_HEAD_CONTROL if shape == "pas" else C.CAT_HEAD_CONTROL
    smooth = g2.chaikin(np.array(control, dtype=float), C.SHAPE_SMOOTHING_ITERATIONS)
    return g2.normalize_to_box(smooth, x0, y0, w, h)


def build(p: ValidatedParams, preset: MaterialPreset, log: DecisionLog) -> LaserDesign:
    require_cut(preset)
    shape, w, h = p["oblik"], p["sirina"], p["visina"]
    hole_d, wall = p["rupa_promjer"], p["rupa_odmak"]
    corner_r = p.get("radijus_ugla", 0.0)

    violations = []
    if shape == "krug":
        violations.append(require_range(NAME, "visina", h, w, w, note="za krug visina = sirina"))
    if shape == "pravougaonik":
        violations.append(require_range(NAME, "radijus_ugla", corner_r, 0, min(w, h) / 2,
                                        note="max je pola krace stranice"))
    raise_violations(NAME, violations)

    pad = C.LASER_DOC_PADDING_MM
    design = LaserDesign(w + 2 * pad, h + 2 * pad)
    outline = outline_for(shape, pad, pad, w, h, corner_r)
    if not g2.is_simple(outline):
        raise InvalidGeometry(f"kontura oblika '{shape}' nije jednostavna", NAME)
    log.record("geometrija", f"oblik '{shape}' {w:g}x{h:g} mm, {len(outline)} tacaka")

    cx = pad + w / 2
    top = vertical_entry_y(outline, cx)
    r = hole_d / 2
    hole_c = (cx, top + wall + r)
    hole = g2.circle(hole_c[0], hole_c[1], r)
    hole_wall = g2.min_distance_points_to_polyline(np.array([hole_c]), outline) - r
    log.record("geometrija", f"rupa za prsten D={hole_d:g} mm, centar ({hole_c[0]:.2f}, {hole_c[1]:.2f})",
               min_zid_mm=round(hole_wall, 3))
    if hole_wall < C.LASER_MIN_WALL_MM - C.GEOMETRY_EPS_MM:
        raise InvalidGeometry(
            f"zid oko rupe je {hole_wall:.2f} mm < min {C.LASER_MIN_WALL_MM} mm "
            f"(oblik '{shape}' je na tom mjestu preuzak; povecaj rupa_odmak ili dimenzije)", NAME)

    geo = render_text(p["tekst"], p["visina_teksta"], p["razmak_slova"])
    text_cy = pad + h * C.KEYCHAIN_TEXT_CENTER_V[shape] + p["tekst_pomak_y"]
    text_rings = place_text(geo, cx, text_cy)
    problems = text_clearance_problems(text_rings, outline, [hole], C.TEXT_EDGE_MARGIN_MM)
    if problems:
        available = horizontal_span(outline, text_cy) - 2 * C.TEXT_EDGE_MARGIN_MM
        raise InvalidGeometry(
            problems + [f"sirina teksta {geo.width:.1f} mm, raspolozivo na toj visini ~{available:.1f} mm; "
                        "smanji visina_teksta, skrati tekst ili povecaj privjesak"], NAME)
    log.record("tekst", f"'{p['tekst']}' visina {geo.cap_height:g} mm, sirina {geo.width:.2f} mm, "
                        f"centar y={text_cy:.2f}")

    design.add("ENGRAVE", text_rings, "tekst")
    design.add("CUT", [hole], "rupa")          # unutrasnje konture prve
    design.add("CUT", [outline], "kontura")
    return design

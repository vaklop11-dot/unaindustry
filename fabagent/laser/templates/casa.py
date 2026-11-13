"""Gravura na casi/termosu: ravna projekcija omotaca.

Cilindar (promjer_gore == promjer_dolje): pravougaonik obim x visina, 1:1 za rotary.
Konus: omotac je kruzni isjecak; tekst se savija tako da je duzina luka na visini
centra teksta jednaka stvarnoj duzini na casi.
"""

from __future__ import annotations

import math

import numpy as np

from fabagent.core import config as C
from fabagent.core import geometry2d as g2
from fabagent.core.decisions import DecisionLog
from fabagent.core.errors import InvalidGeometry
from fabagent.core.validator import ValidatedParams
from fabagent.laser.design import LaserDesign
from fabagent.laser.font import render_text
from fabagent.laser.presets import MaterialPreset

NAME = "casa"


def _instances(p: ValidatedParams, wrap_len: float) -> list[float]:
    n = p["broj_ponavljanja"]
    u0 = p["ugao_centra"] / 360.0 * wrap_len
    return [u0 + k * wrap_len / n for k in range(n)]


def _check_layout(geo, us: list[float], wrap_len: float, v_c: float, zone_len: float) -> list[str]:
    m = C.TEXT_EDGE_MARGIN_MM
    problems = []
    n = len(us)
    if n > 1 and geo.width > wrap_len / n - m:
        problems.append(f"{n} ponavljanja po {geo.width:.1f} mm ne stanu u obim {wrap_len:.1f} mm")
    for k, u in enumerate(us):
        left, right = u - geo.width / 2, u + geo.width / 2
        if left < m or right > wrap_len - m:
            problems.append(
                f"tekst #{k + 1} ({left:.1f}..{right:.1f} mm) prelazi preko sava omotaca "
                f"(0..{wrap_len:.1f} mm, margina {m} mm); promijeni ugao_centra")
    top = v_c - geo.cap_height / 2 + geo.top
    bottom = v_c + geo.cap_height / 2
    if top < m or bottom > zone_len - m:
        problems.append(f"tekst ({top:.1f}..{bottom:.1f} mm) izlazi iz zone graviranja "
                        f"(0..{zone_len:.1f} mm, margina {m} mm)")
    return problems


def build(p: ValidatedParams, preset: MaterialPreset, log: DecisionLog) -> LaserDesign:
    d_top, d_bot, zone_h = p["promjer_gore"], p["promjer_dolje"], p["visina_zone"]
    geo = render_text(p["tekst"], p["visina_teksta"], p["razmak_slova"])
    r_t, r_b = d_top / 2, d_bot / 2

    if d_top == d_bot:
        wrap = math.pi * d_top
        v_c = p["vertikalna_pozicija"] * zone_h
        us = _instances(p, wrap)
        problems = _check_layout(geo, us, wrap, v_c, zone_h)
        if problems:
            raise InvalidGeometry(problems, NAME)
        design = LaserDesign(wrap, zone_h)
        rings = []
        for u in us:
            offset = np.array([u - geo.width / 2, v_c - geo.cap_height / 2])
            rings.extend(poly + offset for poly in geo.polygons)
        design.add("ENGRAVE", rings, "tekst")
        design.guides.append(np.array([[0, 0], [wrap, 0], [wrap, zone_h], [0, zone_h]], float))
        log.record("geometrija", f"cilindar D={d_top:g} mm -> omotac {wrap:.3f} x {zone_h:g} mm")
        return design

    # --- konus
    slant = math.hypot(zone_h, r_t - r_b)
    r_big, r_small = max(r_t, r_b), min(r_t, r_b)
    l_big = slant * r_big / (r_big - r_small)
    l_small = l_big - slant
    top_is_big = r_t > r_b
    phi = 2 * math.pi * r_big / l_big          # ugao isjecka

    def rho(v):
        return (l_big - v) if top_is_big else (l_small + v)

    v_c = p["vertikalna_pozicija"] * slant
    rho_c = rho(v_c)
    wrap_c = phi * rho_c                        # obim na visini centra teksta
    us = _instances(p, wrap_c)
    problems = _check_layout(geo, us, wrap_c, v_c, slant)
    if problems:
        raise InvalidGeometry(problems, NAME)

    sign = -1.0 if top_is_big else 1.0         # apex ispod (vrh sire) ili iznad

    def to_xy(u, v):
        theta = (u - wrap_c / 2) / rho_c
        rr = rho(v)
        return np.column_stack([rr * np.sin(theta), sign * rr * np.cos(theta)])

    rings = []
    for u in us:
        for poly in geo.polygons:
            dense = g2.densify(poly, C.WARP_DENSIFY_MM)
            rings.append(to_xy(u - geo.width / 2 + dense[:, 0], v_c - geo.cap_height / 2 + dense[:, 1]))

    n_arc = g2.arc_segments(l_big, phi)
    th = np.linspace(-phi / 2, phi / 2, n_arc + 1)
    outer = np.column_stack([l_big * np.sin(th), sign * l_big * np.cos(th)])
    inner = np.column_stack([l_small * np.sin(th), sign * l_small * np.cos(th)])
    sector = np.vstack([outer, inner[::-1]])

    x0, y0, x1, y1 = g2.bbox([sector])
    shift = np.array([-x0, -y0])
    design = LaserDesign(x1 - x0, y1 - y0)
    design.add("ENGRAVE", [r + shift for r in rings], "tekst")
    design.guides.append(sector + shift)
    log.record("geometrija",
               f"konus D_gore={d_top:g} D_dolje={d_bot:g} H={zone_h:g}: isjecak R={l_big:.2f}/{l_small:.2f} mm, "
               f"ugao {math.degrees(phi):.2f} deg, obim na centru teksta {wrap_c:.2f} mm")
    log.warn("konusna projekcija: dokument je granicni pravougaonik isjecka; "
             "poravnanje na rotary uredjaju provjeriti probnim graviranjem")
    return design

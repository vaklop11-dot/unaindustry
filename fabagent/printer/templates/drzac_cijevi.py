"""Drzac (casica) za okrugli predmet na pravougaonoj bazi sa 2 rupe za vijke."""

from __future__ import annotations

import numpy as np

from fabagent.core import config as C
from fabagent.core.decisions import DecisionLog
from fabagent.core.validator import ValidatedParams, raise_violations, require_range
from fabagent.printer import mesher as M

NAME = "drzac_cijevi"


def build(p: ValidatedParams, log: DecisionLog) -> list[M.Body]:
    ri = p["unutarnji_promjer"] / 2
    ro = ri + p["debljina_zida"]
    h, b = p["visina"], p["debljina_baze"]
    L, Wb = p["duzina_baze"], p["sirina_baze"]
    R, s = p["rupa_promjer"] / 2, p["rupa_razmak"]
    wall = C.FDM_MIN_WALL_MM
    raise_violations(NAME, [
        require_range(NAME, "sirina_baze", Wb, 2 * (ro + wall), None, note="casica mora stati na bazu"),
        require_range(NAME, "sirina_baze", Wb, 2 * (R + wall), None, note="zid oko rupe za vijak"),
        require_range(NAME, "rupa_razmak", s, 2 * (ro + R + wall), None, note="rupe moraju biti izvan casice"),
        require_range(NAME, "rupa_razmak", s, None, L - 2 * (R + wall), note="zid do kraja baze"),
    ])
    cx, cy = L / 2, Wb / 2
    rect = np.array([[0, 0], [L, 0], [L, Wb], [0, Wb]], float)
    c_out = M.mesh_circle(cx, cy, ro)
    c_in = M.mesh_circle(cx, cy, ri)
    holes = [M.mesh_circle(cx - s / 2, cy, R), M.mesh_circle(cx + s / 2, cy, R)]
    body = M.assemble(NAME, [
        M.prism(rect, [c_out] + holes, 0.0, b),   # baza oko casice, sa rupama
        M.prism(c_out, [c_in], 0.0, b),           # prsten ispod zida casice
        M.prism(c_in, [], 0.0, b),                # dno casice
        M.prism(c_out, [c_in], b, b + h),         # zid casice
    ])
    log.record("geometrija", f"drzac D{2 * ri:g} (zid {ro - ri:g}) visina {h:g} na bazi {L:g}x{Wb:g}x{b:g}, "
                             f"rupe D{2 * R:g} na razmaku {s:g} mm")
    log.record("orijentacija", "baza na ploci")
    return [body]

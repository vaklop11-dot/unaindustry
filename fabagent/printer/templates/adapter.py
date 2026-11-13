"""Adapter/redukcija izmedju dva promjera: rotaciono tijelo iz profila (r, z).
Kraj 1 je dolje (na ploci), kraj 2 gore."""

from __future__ import annotations

import math

import numpy as np

from fabagent.core import config as C
from fabagent.core import geometry2d as g2
from fabagent.core.decisions import DecisionLog
from fabagent.core.errors import InvalidGeometry
from fabagent.core.validator import ValidatedParams, raise_violations, require_range
from fabagent.printer import mesher as M

NAME = "adapter"


def build(p: ValidatedParams, log: DecisionLog) -> list[M.Body]:
    ro1, ri1, L1 = p["d1_vanjski"] / 2, p["d1_unutarnji"] / 2, p["duzina1"]
    ro2, ri2, L2 = p["d2_vanjski"] / 2, p["d2_unutarnji"] / 2, p["duzina2"]
    Lt = p["duzina_prelaza"]
    wall = C.FDM_MIN_WALL_MM
    raise_violations(NAME, [
        require_range(NAME, "d1_unutarnji", p["d1_unutarnji"], None, p["d1_vanjski"] - 2 * wall,
                      note=f"zid kraja 1 >= {wall} mm"),
        require_range(NAME, "d2_unutarnji", p["d2_unutarnji"], None, p["d2_vanjski"] - 2 * wall,
                      note=f"zid kraja 2 >= {wall} mm"),
    ])
    z1, z2, z3 = L1, L1 + Lt, L1 + Lt + L2
    outer_chain = np.array([[ro1, 0], [ro1, z1], [ro2, z2], [ro2, z3]], float)
    inner_chain = np.array([[ri1, 0], [ri1, z1], [ri2, z2], [ri2, z3]], float)
    gap = g2.polyline_min_distance(g2.dedupe(outer_chain), g2.dedupe(inner_chain), False, False)
    if gap < wall - C.WALL_THICKNESS_TOLERANCE_MM:
        raise InvalidGeometry(
            f"stijenka u prelazu je {gap:.2f} mm < {wall} mm; produzi duzina_prelaza", NAME)
    profile = g2.dedupe(np.vstack([outer_chain, inner_chain[::-1]]))
    if not g2.is_simple(profile):
        raise InvalidGeometry("profil adaptera se sijece sam sa sobom", NAME)

    segments = g2.arc_segments(max(ro1, ro2), **M.MESH_ARC)
    body = M.assemble(NAME, [M.lathe(profile, segments)])
    log.record("geometrija", f"adapter D{2 * ro1:g}/{2 * ri1:g} -> D{2 * ro2:g}/{2 * ri2:g}, "
                             f"duzina {z3:g} mm, {segments} segmenata", min_stijenka_prelaza_mm=round(gap, 3))

    # Prepust (overhang) u orijentaciji kraj 1 dolje: siri se prema gore = prepust.
    def angle(dr):
        if dr <= 0:
            return 0.0
        return 90.0 if Lt == 0 else math.degrees(math.atan2(dr, Lt))

    for label, a in (("vanjska povrsina", angle(ro2 - ro1)), ("unutrasnji otvor", angle(ri1 - ri2))):
        if a > C.FDM_MAX_OVERHANG_DEG:
            log.warn(f"{label}: prepust {a:.1f} deg > {C.FDM_MAX_OVERHANG_DEG} deg - potrebna podrska "
                     "ili duzi prelaz")
    log.record("orijentacija", "kraj 1 na ploci, os rotacije vertikalna")
    return [body]

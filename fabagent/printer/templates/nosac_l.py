"""L-nosac sa filetom u unutrasnjem uglu i 4 rupe (2 po kraku).

Koordinate: x = horizontalni krak (0..duzina_a), y = sirina, z = vertikalni krak.
Sastav: horizontalna ploca (rupe po z) + ugaoni blok sa filetom + vertikalna ploca
(rupe po x); dijelovi se lijepe na ravnima x = t + r i z = t + r.
"""

from __future__ import annotations

import numpy as np

from fabagent.core import config as C
from fabagent.core.decisions import DecisionLog
from fabagent.core.validator import ValidatedParams, raise_violations, require_range
from fabagent.printer import mesher as M

NAME = "nosac_l"


def build(p: ValidatedParams, log: DecisionLog) -> list[M.Body]:
    A, B, W, t = p["duzina_a"], p["duzina_b"], p["sirina"], p["debljina"]
    d, s, e, r = p["rupa_promjer"], p["rupa_razmak"], p["rupa_od_kraja"], p["filet_radijus"]
    R = d / 2
    wall = C.FDM_MIN_WALL_MM
    flat_start = t + r            # gdje pocinje ravni dio kraka (iza fileta)
    raise_violations(NAME, [
        require_range(NAME, "rupa_od_kraja", e, R + wall, None, note="zid izmedju rupe i kraja"),
        require_range(NAME, "rupa_od_kraja", e, None, min(A, B) - flat_start - R - wall,
                      note="rupa mora biti na ravnom dijelu kraka, iza fileta"),
        require_range(NAME, "rupa_razmak", s, d + wall, None, note="zid izmedju dvije rupe"),
        require_range(NAME, "rupa_razmak", s, None, W - d - 2 * wall, note="zid do bocnih ivica"),
    ])

    y1, y2 = W / 2 - s / 2, W / 2 + s / 2
    xh = A - e
    zh = B - e
    horizontal = M.prism(
        np.array([[flat_start, 0], [A, 0], [A, W], [flat_start, W]], float),
        [M.mesh_circle(xh, y1, R), M.mesh_circle(xh, y2, R)], 0.0, t, M.FRAME_XY)
    corner = M.prism(M.fillet_corner_profile(t, r), [], -W, 0.0, M.FRAME_XZ)
    vertical = M.prism(
        np.array([[0, flat_start], [W, flat_start], [W, B], [0, B]], float),
        [M.mesh_circle(y1, zh, R), M.mesh_circle(y2, zh, R)], 0.0, t, M.FRAME_YZ)
    body = M.assemble(NAME, [horizontal, corner, vertical])
    log.record("geometrija", f"L-nosac {A:g}x{B:g}x{W:g} mm, debljina {t:g}, filet R{r:g}",
               rupe_horizontalno=[(round(xh, 3), round(y1, 3)), (round(xh, 3), round(y2, 3))],
               rupe_vertikalno=[(round(y1, 3), round(zh, 3)), (round(y2, 3), round(zh, 3))])
    log.record("orijentacija", "stampa: horizontalni krak na ploci, vertikalni krak gore (bez podrske)")
    return [body]

"""Kutija sa poklopcem koji ulazi u kutiju (rub sa zadanom zracnoscu).

Kutija:   donji prsten + dno (unutrasnja ploca) + gornji prsten (zidovi).
Poklopac: ploca (vanjski prsten + prsten ispod ruba + sredina) + rub.
Oba tijela idu na istu plocu (pored kutije po X, ili po Y ako ne stane).
"""

from __future__ import annotations

from fabagent.core import config as C
from fabagent.core.decisions import DecisionLog
from fabagent.core.errors import OutOfRange, RangeViolation
from fabagent.core.validator import ValidatedParams, raise_violations, require_range
from fabagent.printer import mesher as M

NAME = "kutija"


def _rect(x0, y0, x1, y1, r):
    return M.mesh_rounded_rect(x0, y0, x1, y1, max(0.0, r))


def build(p: ValidatedParams, log: DecisionLog) -> list[M.Body]:
    L, W, H = p["duzina"], p["sirina"], p["visina"]
    tw, tf, tol = p["debljina_zida"], p["debljina_dna"], p["tolerancija"]
    rc, lt = p["radijus_ugla"], p["debljina_poklopca"]
    lh, lw = p["visina_ruba_poklopca"], p["debljina_ruba_poklopca"]
    wall = C.FDM_MIN_WALL_MM
    inner_min = min(L, W) - 2 * tw
    raise_violations(NAME, [
        require_range(NAME, "radijus_ugla", rc, 0, min(L, W) / 2),
        require_range(NAME, "visina", H, tf + wall, None, note="mora biti visa od dna"),
        require_range(NAME, "visina_ruba_poklopca", lh, None, H - tf, note="rub ne smije biti dublji od kutije"),
        require_range(NAME, "debljina_ruba_poklopca", lw, None, (inner_min - 2 * tol - wall) / 2,
                      note="otvor unutar ruba poklopca mora ostati"),
    ])

    # Konture (koriste se identicne instance za dijelove koji se lijepe).
    outer = _rect(0, 0, L, W, rc)
    r_in = rc - tw
    inner = _rect(tw, tw, L - tw, W - tw, r_in)
    box = M.assemble("kutija", [
        M.prism(outer, [inner], 0.0, tf),
        M.prism(inner, [], 0.0, tf),
        M.prism(outer, [inner], tf, H),
    ])

    o = tw + tol
    lo_r = r_in - tol
    lip_outer = _rect(o, o, L - o, W - o, lo_r)
    i = o + lw
    lip_inner = _rect(i, i, L - i, W - i, lo_r - lw)
    lid = M.assemble("poklopac", [
        M.prism(outer, [lip_outer], 0.0, lt),
        M.prism(lip_outer, [lip_inner], 0.0, lt),
        M.prism(lip_inner, [], 0.0, lt),
        M.prism(lip_outer, [lip_inner], lt, lt + lh),
    ])
    log.record("geometrija", f"kutija {L:g}x{W:g}x{H:g} mm, zid {tw:g}, dno {tf:g}; "
                             f"poklopac {lt:g} mm + rub {lh:g} mm, zracnost {tol:g} mm po strani",
               unutrasnjost_mm=[round(L - 2 * tw, 3), round(W - 2 * tw, 3), round(H - tf, 3)],
               rub_vanjski_mm=[round(L - 2 * o, 3), round(W - 2 * o, 3)])

    gap = C.PRINT_PLATE_GAP_MM
    bx, by, _ = C.PRINTER_BUILD_VOLUME_MM
    if 2 * L + gap <= bx and W <= by:
        lid = lid.translated(dx=L + gap)
        log.record("raspored", f"poklopac pored kutije po X (razmak {gap:g} mm)")
    elif L <= bx and 2 * W + gap <= by:
        lid = lid.translated(dy=W + gap)
        log.record("raspored", f"poklopac pored kutije po Y (razmak {gap:g} mm)")
    else:
        raise OutOfRange(NAME, [RangeViolation("duzina", L, None, (bx - gap) / 2, "mm",
                                               "kutija i poklopac ne staju zajedno na plocu")])
    log.record("orijentacija", "kutija otvorom gore; poklopac plocom na stolu, rub gore")
    return [box, lid]

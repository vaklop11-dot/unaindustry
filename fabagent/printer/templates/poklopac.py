"""Okrugli poklopac koji se navlaci preko cijevi. Stampa se vrhom na ploci."""

from __future__ import annotations

from fabagent.core.decisions import DecisionLog
from fabagent.core.validator import ValidatedParams
from fabagent.printer import mesher as M

NAME = "poklopac"


def build(p: ValidatedParams, log: DecisionLog) -> list[M.Body]:
    ri = p["promjer_cijevi"] / 2 + p["tolerancija"]
    ro = ri + p["debljina_zida"]
    tt, h = p["debljina_vrha"], p["visina_ruba"]
    c_in = M.mesh_circle(0.0, 0.0, ri)
    c_out = M.mesh_circle(0.0, 0.0, ro)
    body = M.assemble(NAME, [
        M.prism(c_in, [], 0.0, tt),             # sredina vrha
        M.prism(c_out, [c_in], 0.0, tt),        # prsten vrha ispod ruba
        M.prism(c_out, [c_in], tt, tt + h),     # rub
    ])
    log.record("geometrija", f"poklopac za cijev D{p['promjer_cijevi']:g}: unutrasnji D{2 * ri:.3f}, "
                             f"vanjski D{2 * ro:.3f}, visina {tt + h:g} mm")
    log.record("orijentacija", "vrh poklopca na ploci, otvor gore (bez podrske)")
    return [body]

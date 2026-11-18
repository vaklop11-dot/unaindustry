"""Okretanje i rotacija crteza prije generisanja G-codea.

Radi na konturama u milimetrima, oko sredine samog crteza - ne oko ishodista
stola. Tako crtez ostaje na istom mjestu, samo okrenut.

Nista se ne skalira i nista se ne izmislja. Poslije svake transformacije
granice se ponovo provjeravaju u `core/gcode.py` (`_check_bounds`).
"""

from __future__ import annotations

import numpy as np

OPERACIJE = ("okreni_v", "okreni_h", "rot90", "rot180", "rot270")


def _okvir(grupe: list[np.ndarray]) -> tuple[float, float, float, float]:
    svi = np.vstack([np.asarray(g, dtype=float) for g in grupe])
    return (float(svi[:, 0].min()), float(svi[:, 1].min()),
            float(svi[:, 0].max()), float(svi[:, 1].max()))


def primijeni(grupe: list[np.ndarray], operacija: str) -> list[np.ndarray]:
    """Vraca nove konture. `grupe` ostaju netaknute."""
    if operacija not in OPERACIJE:
        raise ValueError(f"nepoznata operacija '{operacija}', izbor: {list(OPERACIJE)}")
    if not grupe:
        return []
    x0, y0, x1, y1 = _okvir(grupe)
    cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0

    out = []
    for g in grupe:
        a = np.asarray(g, dtype=float).copy()
        x, y = a[:, 0], a[:, 1]
        if operacija == "okreni_h":          # ogledalo lijevo-desno
            nx, ny = 2 * cx - x, y
        elif operacija == "okreni_v":        # ogledalo gore-dolje
            nx, ny = x, 2 * cy - y
        elif operacija == "rot180":
            nx, ny = 2 * cx - x, 2 * cy - y
        elif operacija == "rot90":           # u smjeru kazaljke
            nx, ny = cx + (y - cy), cy - (x - cx)
        else:                                # rot270, suprotno od kazaljke
            nx, ny = cx - (y - cy), cy + (x - cx)
        out.append(np.column_stack([nx, ny]))
    return out


def na_nulu(grupe: list[np.ndarray], margina_mm: float = 0.0) -> list[np.ndarray]:
    """Pomjera crtez tako da mu donji lijevi ugao okvira bude na (margina, margina).

    Koristi se poslije rotacije, kad crtez moze izaci izvan radne povrsine.
    """
    if not grupe:
        return []
    x0, y0, _, _ = _okvir(grupe)
    pom = np.array([margina_mm - x0, margina_mm - y0], dtype=float)
    return [np.asarray(g, dtype=float) + pom for g in grupe]


def dizajn_transformisan(dizajn, operacije: list[str], na_nulu_poslije: bool = True):
    """Novi LaserDesign sa primijenjenim operacijama, istim redom kako su zadane."""
    from fabagent.laser.design import LaserDesign

    zatvorene: dict[str, list] = {}
    otvorene: dict[str, list] = {}
    for it in dizajn.items:
        zatvorene.setdefault(it.layer, []).extend(it.rings)
        otvorene.setdefault(it.layer, []).extend(it.otvorene)

    # Sve konture se transformisu zajedno, da se okvir racuna nad cijelim
    # crtezom - inace bi svaki sloj dobio svoje srediste i razisli bi se.
    redoslijed = [(sloj, "z", i) for sloj, lst in zatvorene.items() for i in range(len(lst))]
    redoslijed += [(sloj, "o", i) for sloj, lst in otvorene.items() for i in range(len(lst))]
    sve = [zatvorene[s][i] if v == "z" else otvorene[s][i] for s, v, i in redoslijed]
    if not sve:
        return dizajn

    for op in operacije:
        sve = primijeni(sve, op)
    if na_nulu_poslije:
        sve = na_nulu(sve)

    novo_z: dict[str, list] = {}
    novo_o: dict[str, list] = {}
    for (sloj, vrsta, _), a in zip(redoslijed, sve):
        (novo_z if vrsta == "z" else novo_o).setdefault(sloj, []).append(a)

    d = LaserDesign(dizajn.width, dizajn.height)
    for sloj in set(list(novo_z) + list(novo_o)):
        d.add(sloj, novo_z.get(sloj, []), sloj.lower(), novo_o.get(sloj, []))
    return d

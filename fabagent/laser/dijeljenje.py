"""Dijeljenje prevelikog crteza na table koje stanu na radnu povrsinu.

Paketi za laser su nacrtani na velikom listu (npr. 547 x 328 mm) na kojem lezi
vise odvojenih komada. Mašina sa 400 x 400 mm takav list ne moze primiti.

Ovdje se **ne sijece geometrija**. Svaki komad ostaje cijel - samo se komadi
rasporede na vise tabli. Komad = jedna vanjska kontura sa svim konturama koje
leze unutar nje (rupe, utori, gravura, oznake).

Ako jedan jedini komad sam po sebi ne stane na radnu povrsinu, on se NE dijeli
i NE skalira, nego se prijavi imenom i dimenzijama - skaliranje bi bilo
pretpostavka o tome sta korisnik zeli.
"""

from __future__ import annotations

import numpy as np

from fabagent.core import config as C


def _bbox(r: np.ndarray) -> tuple[float, float, float, float]:
    return (float(r[:, 0].min()), float(r[:, 1].min()),
            float(r[:, 0].max()), float(r[:, 1].max()))


def _unutra(a: tuple, b: tuple, eps: float = 1e-6) -> bool:
    """Da li pravougaonik a lezi unutar pravougaonika b."""
    return (a[0] >= b[0] - eps and a[1] >= b[1] - eps
            and a[2] <= b[2] + eps and a[3] <= b[3] + eps)


class Komad:
    """Jedna vanjska kontura sa svime sto lezi unutar nje."""

    __slots__ = ("okvir", "zatvorene", "otvorene")

    def __init__(self, okvir: tuple):
        self.okvir = okvir
        self.zatvorene: list[tuple[str, np.ndarray]] = []
        self.otvorene: list[tuple[str, np.ndarray]] = []

    @property
    def sirina(self) -> float:
        return self.okvir[2] - self.okvir[0]

    @property
    def visina(self) -> float:
        return self.okvir[3] - self.okvir[1]


def komadi(slojevi: dict[str, list], otvorene: dict[str, list] | None = None) -> list[Komad]:
    """Grupise sve konture u komade po tome sta lezi unutar cega.

    slojevi:  {"CUT": [kontura, ...], "ENGRAVE": [...], "SCORE": [...]}
    otvorene: isto, za linije koje se ne zatvaraju
    """
    svi: list[tuple[str, np.ndarray, tuple]] = []
    for sloj, lista in (slojevi or {}).items():
        for r in lista:
            a = np.asarray(r, dtype=float)
            if len(a) >= 2:
                svi.append((sloj, a, _bbox(a)))
    otv: list[tuple[str, np.ndarray, tuple]] = []
    for sloj, lista in (otvorene or {}).items():
        for o in lista:
            a = np.asarray(o, dtype=float)
            if len(a) >= 2:
                otv.append((sloj, a, _bbox(a)))

    # Vanjske konture: one koje ne leze unutar nijedne druge. Od najvece ka
    # najmanjoj, da se rupa nikad ne proglasi komadom.
    svi.sort(key=lambda t: (t[2][2] - t[2][0]) * (t[2][3] - t[2][1]), reverse=True)
    vanjske: list[Komad] = []
    for sloj, a, bb in svi:
        dom = None
        for k in vanjske:
            if _unutra(bb, k.okvir):
                dom = k
                break
        if dom is None:
            k = Komad(bb)
            k.zatvorene.append((sloj, a))
            vanjske.append(k)
        else:
            dom.zatvorene.append((sloj, a))

    for sloj, a, bb in otv:
        dom = None
        for k in vanjske:
            if _unutra(bb, k.okvir):
                dom = k
                break
        if dom is None:                      # linija izvan svakog komada - sama je komad
            k = Komad(bb)
            k.otvorene.append((sloj, a))
            vanjske.append(k)
        else:
            dom.otvorene.append((sloj, a))
    return vanjske


def rasporedi(lista: list[Komad], sirina_mm: float, visina_mm: float,
              razmak_mm: float | None = None) -> tuple[list[list[tuple[Komad, float, float]]], list[Komad]]:
    """Slaze komade na table (police: red po red, visi prvi).

    Vraca (table, prevelici). Svaka tabla je lista (komad, pomak_x, pomak_y)
    gdje su pomaci koliko komad treba pomjeriti od svoje trenutne pozicije.
    """
    # Graviranje trazi prelet van crteza (ENGRAVE_OVERSCAN_MM), pa rub table
    # ne smije biti manji od njega - inace G-code trazi negativan X.
    if razmak_mm is None:
        razmak_mm = max(2.0, float(C.ENGRAVE_OVERSCAN_MM) + 0.5)
    prevelici = [k for k in lista
                 if k.sirina > sirina_mm + 1e-9 or k.visina > visina_mm + 1e-9]
    stanu = sorted((k for k in lista if k not in prevelici),
                   key=lambda k: k.visina, reverse=True)

    table: list[list[tuple[Komad, float, float]]] = []
    for k in stanu:
        smjesten = False
        for tabla in table:
            # police te table
            x = razmak_mm
            y = razmak_mm
            red_visina = 0.0
            for (p, px, py) in tabla:
                pass
            # jednostavno: ponovo slozi cijelu tablu sa novim komadom
            kandidat = [p for (p, _, _) in tabla] + [k]
            slog = _slozi(kandidat, sirina_mm, visina_mm, razmak_mm)
            if slog is not None:
                tabla[:] = slog
                smjesten = True
                break
        if not smjesten:
            slog = _slozi([k], sirina_mm, visina_mm, razmak_mm)
            if slog is None:
                prevelici.append(k)
            else:
                table.append(slog)
    return table, prevelici


def _slozi(lista: list[Komad], W: float, H: float, razmak: float):
    """Police: red po red. Vraca raspored ili None ako ne stane."""
    red = sorted(lista, key=lambda k: k.visina, reverse=True)
    x = razmak
    y = razmak
    red_visina = 0.0
    out = []
    for k in red:
        if x + k.sirina > W - razmak:
            x = razmak
            y += red_visina + razmak
            red_visina = 0.0
        if y + k.visina > H - razmak:
            return None
        out.append((k, x - k.okvir[0], y - k.okvir[1]))
        x += k.sirina + razmak
        red_visina = max(red_visina, k.visina)
    return out


def dizajn_table(tabla: list[tuple[Komad, float, float]], W: float, H: float):
    """Pravi LaserDesign od jedne table (komadi vec pomjereni na svoje mjesto)."""
    from fabagent.laser.design import LaserDesign

    d = LaserDesign(W, H)
    po_sloju: dict[str, list] = {}
    otv_po_sloju: dict[str, list] = {}
    for k, dx, dy in tabla:
        pom = np.array([dx, dy], dtype=float)
        for sloj, a in k.zatvorene:
            po_sloju.setdefault(sloj, []).append(a + pom)
        for sloj, a in k.otvorene:
            otv_po_sloju.setdefault(sloj, []).append(a + pom)
    for sloj in C.LAYERS:
        z = po_sloju.get(sloj, [])
        o = otv_po_sloju.get(sloj, [])
        if z or o:
            d.add(sloj, z, sloj.lower(), o)
    return d

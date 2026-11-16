"""Testovi vlastitog trasiranja (`core/trasiranje.py`).

Svaki test provjerava jednu tvrdnju iz modula. Brojevi su racunati rukom, ne
prepisani iz izlaza programa - inace test samo potvrdjuje ono sto kod vec radi.
"""

from __future__ import annotations

import numpy as np
import pytest

from fabagent.core import trasiranje as T


def kvadrat(h=20, w=20, r0=5, r1=15, c0=5, c1=15) -> np.ndarray:
    a = np.zeros((h, w), dtype=bool)
    a[r0:r1, c0:c1] = True
    return a


# ----------------------------------------------------------------- osnovno
def test_kvadrat_daje_cetiri_tacke_i_tacnu_povrsinu():
    """Kvadrat 10x10 piksela ima cetiri tjemena i povrsinu 100 px2.

    Granica ide po pikselima, pa bi bez izbacivanja kolinearnih tacaka ovdje
    bilo 40 tjemena umjesto 4.
    """
    polys = T.trasiraj(kvadrat())
    assert len(polys) == 1
    assert len(polys[0]) == 4
    assert abs(T.povrsina(polys[0])) == pytest.approx(100.0)


def test_prazna_slika_nema_kontura():
    assert T.trasiraj(np.zeros((8, 8), dtype=bool)) == []


def test_crtez_do_ivice_slike_se_ne_gubi():
    """Piksel na rubu nema susjeda; ako se to ne racuna kao pozadina, kontura nestane."""
    polys = T.trasiraj(np.ones((6, 6), dtype=bool))
    assert len(polys) == 1
    assert abs(T.povrsina(polys[0])) == pytest.approx(36.0)


# ------------------------------------------------------------------- rupe
def test_rupa_ima_suprotan_smjer_obilaska():
    """Vanjska kontura i rupa moraju imati suprotan predznak povrsine.

    Na tome pociva SVG nonzero pravilo - bez toga bi rupa bila puna.
    """
    a = kvadrat()
    a[8:12, 8:12] = False
    polys = T.trasiraj(a)
    assert len(polys) == 2
    p = sorted(T.povrsina(x) for x in polys)
    assert p[0] * p[1] < 0
    assert sorted(abs(x) for x in p) == pytest.approx([16.0, 100.0])


# ------------------------------------------------------------ odvojeni komadi
def test_dvije_mrlje_daju_dvije_konture():
    a = np.zeros((20, 20), dtype=bool)
    a[2:5, 2:5] = True
    a[12:18, 12:18] = True
    assert len(T.trasiraj(a)) == 2


def test_mala_mrlja_se_odbacuje():
    a = np.zeros((20, 20), dtype=bool)
    a[2:5, 2:5] = True          # 9 px2
    a[12:18, 12:18] = True      # 36 px2
    assert len(T.trasiraj(a, min_povrsina=20.0)) == 1


def test_dijagonala_ostaje_jedan_komad():
    """Dva piksela koja se dodiruju samo uglom su jedan komad materijala.

    Da se bira drugi nastavak na tjemenu, raspali bi se na dvije konture i rez
    bi presjekao most koji na ploci stvarno postoji.
    """
    a = np.zeros((10, 10), dtype=bool)
    a[3, 3] = True
    a[4, 4] = True
    assert len(T.trasiraj(a)) == 1


# ------------------------------------------------------- pojednostavljenje
def _krug(n=120, r=45):
    yy, xx = np.mgrid[0:n, 0:n]
    return ((xx - n // 2) ** 2 + (yy - n // 2) ** 2) < r ** 2


def test_rdp_smanjuje_broj_tacaka_a_cuva_povrsinu():
    sirovo = T.trasiraj(_krug())
    upro = T.trasiraj(_krug(), uprosti=1.0)
    assert len(upro[0]) < len(sirovo[0]) / 3
    assert abs(T.povrsina(upro[0])) == pytest.approx(abs(T.povrsina(sirovo[0])), rel=0.03)


def test_rdp_nula_ne_mijenja_nista():
    a = kvadrat()
    assert len(T.trasiraj(a, uprosti=0.0)[0]) == len(T.trasiraj(a)[0])


# ------------------------------------------------------------------ izlaz
def test_putanja_je_zatvorena_i_bez_krivulja():
    d = T.u_putanje(T.trasiraj(kvadrat()))[0]
    assert d.startswith("M ")
    assert d.endswith(" Z")
    assert " C " not in d
    assert d.count("L") == 3


def test_ulaz_mora_biti_dvodimenzionalan():
    with pytest.raises(ValueError):
        T.trasiraj(np.zeros((4, 4, 3), dtype=bool))


# --------------------------------------------- motor u toku trasiranja slike
def test_vlastiti_je_podrazumijevani_motor():
    from fabagent.core import image_trace as IT
    assert IT.TraceOptions().engine == "vlastiti"
    assert "vlastiti" in IT.ENGINES

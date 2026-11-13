"""Radni nalog: svaki broj dolazi iz posla ili preseta, nikad iz pretpostavke."""

import pytest

from fabagent.core import uputstvo as U

POSAO = {
    "job_id": "20261114-000320-slika_raster-1f6023",
    "type": "raster", "template": "slika_raster",
    "params": {"fajl": "slika.png", "materijal": "sperploca_3mm", "interval_mm": 0.14},
    "raster": {"pixels": [857, 1143], "interval_mm": 0.14, "png": "x_raster.png"},
    "estimates": {"vrijeme_s": 565.9},
    "summary": {"document_mm": {"width": 119.98, "height": 160.02}},
    "warnings": ["preset 'sperploca_3mm' NIJE verifikovan na masini"],
    "material_preset": {
        "key": "sperploca_3mm", "name": "Sperploca (breza/lipa) 3 mm", "thickness_mm": 3.0,
        "verified": False,
        "ENGRAVE": {"speed_mm_s": 100.0, "speed_mm_min": 6000.0, "power_pct": 30.0, "passes": 1},
        "CUT": {"speed_mm_s": 5.0, "speed_mm_min": 300.0, "power_pct": 100.0, "passes": 2},
        "SCORE": {"speed_mm_s": 60.0, "speed_mm_min": 3600.0, "power_pct": 20.0, "passes": 1},
    },
}


def test_nalog_nosi_dimenzije_iz_posla():
    n = U.build(POSAO)
    vrijednosti = dict(n["cinjenice"])
    assert "119.98 mm x 160.02 mm" == vrijednosti["Velicina na materijalu"]
    assert "857 x 1143 px" == vrijednosti["Rezolucija"]


def test_dpi_se_racuna_iz_razmaka_a_ne_pogadja():
    vrijednosti = dict(U.build(POSAO)["cinjenice"])
    assert vrijednosti["DPI (25.4 / razmak)"] == "181"   # 25.4 / 0.14


def test_vrijeme_je_citljivo():
    assert dict(U.build(POSAO)["cinjenice"])["Procjena trajanja"] == "9 min 26 s"


def test_neprovjeren_preset_je_oznacen_na_svakom_redu():
    n = U.build(POSAO)
    assert n["postavke"], "nema postavki masine"
    assert all(red["provjereno"] is False for red in n["postavke"])
    assert any("POLAZNE" in w for w in n["upozorenja"])


def test_brzina_i_snaga_dolaze_iz_preseta():
    red = {r["sloj"]: r for r in U.build(POSAO)["postavke"]}
    assert red["ENGRAVE"]["brzina_mm_min"] == "6000 mm/min"
    assert red["CUT"]["snaga"] == "100 %"
    assert red["CUT"]["prolaza"] == "2"


def test_podatak_koji_ne_postoji_se_ne_izmislja():
    prazan = {"job_id": "x", "type": "raster"}
    vrijednosti = dict(U.build(prazan)["cinjenice"])
    assert vrijednosti["Materijal"] == U.NEMA
    assert vrijednosti["Procjena trajanja"] == U.NEMA
    assert vrijednosti["Rezolucija"] == U.NEMA


def test_materijal_bez_rezanja_nema_red_za_rezanje():
    posao = dict(POSAO)
    posao["material_preset"] = dict(POSAO["material_preset"], CUT=None)
    slojevi = [r["sloj"] for r in U.build(posao)["postavke"]]
    assert "CUT" not in slojevi
    assert "ENGRAVE" in slojevi


def test_koraci_idu_redom_i_prazan_hod_je_prije_rada():
    naslovi = [k["naslov"] for k in U.build(POSAO)["koraci"]]
    assert naslovi.index("Prazan hod") < naslovi.index("Rad")
    assert naslovi[0] == "Materijal"


def test_nadzor_se_ne_trazi_stajanjem():
    """Operater ne mora stajati uz masinu - nadzor ide preko kamere i Stop dugmeta."""
    koraci = U.build(POSAO)["koraci"]
    tekst = " ".join(k["tekst"] for k in koraci).lower()
    assert "stoj uz" not in tekst and "stajanje uz masinu" in tekst
    assert "kamera" in tekst and "/machine" in tekst
    naslovi = [k["naslov"] for k in koraci]
    assert naslovi.index("Prije pokretanja - nadzor sa mjesta") < naslovi.index("Rad")


def test_tekstualna_verzija_sadrzi_sve_dijelove():
    t = U.kao_tekst(U.build(POSAO))
    for dio in ("POSTAVKE MASINE", "KORACI", "UPOZORENJA", "6000 mm/min", "Frame"):
        assert dio in t

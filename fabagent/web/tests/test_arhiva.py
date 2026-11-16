"""Testovi citanja ZIP arhiva sa diska (`core/arhiva.py` i rute `/arhiva`).

Paketi crteza stizu kao arhive od vise stotina megabajta. Ovi testovi drze dvije
tvrdnje: spisak se cita bez raspakivanja, i jedan crtez se vadi i prolazi kroz
isti tok kao da je prilozen rukom.
"""

from __future__ import annotations

import zipfile

import pytest

from fabagent.core import arhiva as AR
from fabagent.core.errors import InvalidParam
from fabagent.web.tests.test_routes import client                     # noqa: F401

SVG = (b'<svg xmlns="http://www.w3.org/2000/svg" width="20mm" height="20mm" '
       b'viewBox="0 0 20 20"><rect x="2" y="2" width="16" height="16" '
       b'fill="none" stroke="#ff0000" stroke-width="0.1"/></svg>')


@pytest.fixture()
def paket(tmp_path):
    """Arhiva sa 1000 crteza, nekoliko slika i par fajlova koji nisu za laser."""
    p = tmp_path / "paket.zip"
    with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as z:
        for i in range(1000):
            z.writestr(f"kutije/kutija_{i:04d}.svg", SVG)
        for i in range(5):
            z.writestr(f"pregledi/slika_{i}.jpg", b"\xff\xd8\xff" + bytes(200))
        z.writestr("citaj_me.txt", b"uputstvo")
        z.writestr("prazan_folder/", b"")
    return p


# ------------------------------------------------------------- spisak
def test_spisak_vidi_sve_crteze_i_preskace_ostalo(paket):
    s = AR.spisak(paket)
    assert s["podrzanih"] == 1005          # 1000 svg + 5 jpg
    assert s["preskoceno"] == 2            # txt + folder
    assert len(s["unosi"]) == 1005
    assert s["unosi"][0]["ime"].endswith(".svg")


def test_pretraga_suzava_spisak(paket):
    assert len(AR.spisak(paket, "kutija_000")["unosi"]) == 10
    assert AR.spisak(paket, "nepostojece")["unosi"] == []


def test_spisak_ne_raspakuje_arhivu(paket, tmp_path):
    """Poslije citanja spiska na disku ne smije nastati nijedan novi fajl."""
    prije = {p.name for p in tmp_path.iterdir()}
    AR.spisak(paket)
    assert {p.name for p in tmp_path.iterdir()} == prije


# ------------------------------------------------------------- vadjenje
def test_vadjenje_vraca_tacan_sadrzaj(paket):
    ime, data = AR.izvadi(paket, "kutije/kutija_0007.svg")
    assert ime == "kutija_0007.svg"        # bez foldera iz arhive
    assert data == SVG


def test_putanja_van_arhive_se_odbija(paket):
    """Ime se uzima iz spiska arhive, pa '../' ne moze proci."""
    with pytest.raises(InvalidParam):
        AR.izvadi(paket, "../tajna.txt")


def test_prevelik_unos_se_odbija(tmp_path, monkeypatch):
    p = tmp_path / "veliki.zip"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("ogroman.svg", b"x" * 2048)
    monkeypatch.setattr(AR, "MAX_UNOS_BAJTOVA", 1024)
    with pytest.raises(InvalidParam):
        AR.izvadi(p, "ogroman.svg")


def test_rar_se_imenuje_a_ne_otvara(tmp_path):
    p = tmp_path / "paket.rar"
    p.write_bytes(b"Rar!\x1a\x07\x00")
    with pytest.raises(InvalidParam) as e:
        AR.spisak(p)
    assert "prepakuj" in str(e.value)


def test_ostecen_zip(tmp_path):
    p = tmp_path / "pokvaren.zip"
    p.write_bytes(b"PK\x03\x04 ovo nije ispravan zip")
    with pytest.raises(InvalidParam):
        AR.spisak(p)


def test_nepostojeca_putanja(tmp_path):
    with pytest.raises(InvalidParam):
        AR.spisak(tmp_path / "nema.zip")


def test_arhive_u_folderu(paket, tmp_path):
    (tmp_path / "pod").mkdir()
    (tmp_path / "pod" / "drugi.zip").write_bytes(paket.read_bytes())
    imena = {a["ime"] for a in AR.arhive_u_folderu(tmp_path, dubina=2)}
    assert imena == {"paket.zip", "drugi.zip"}


# ---------------------------------------------------------------- rute
def test_stranica_arhive_se_otvara(client):
    r = client.get("/arhiva")
    assert r.status_code == 200
    assert "Arhive" in r.text


def test_ruta_izlistava_arhivu(client, paket):
    r = client.get("/arhiva", params={"folder": str(paket.parent), "zip": str(paket)})
    assert r.status_code == 200
    assert "kutija_0000.svg" in r.text
    assert "1005" in r.text                 # broj crteza za laser


def test_ruta_pravi_posao_iz_arhive(client, paket):
    """Crtez iz arhive mora proci isti put kao i prilozen rukom."""
    r = client.post("/arhiva/uzmi", data={"zip": str(paket), "ime": "kutije/kutija_0042.svg",
                                          "materijal": "sperploca_3mm"})
    assert r.status_code == 200, r.text[:300]
    assert "kutija_0042" in r.text


def test_ruta_bez_izabranog_crteza(client, paket):
    r = client.post("/arhiva/uzmi", data={"zip": str(paket), "materijal": "sperploca_3mm"})
    assert r.status_code == 422

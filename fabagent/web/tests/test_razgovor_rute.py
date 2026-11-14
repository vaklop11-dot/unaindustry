"""Razgovor kroz web: stanje ide kroz formu, pitanja ne gube korak, posao se pravi."""

import re

import pytest
from fastapi.testclient import TestClient

from fabagent.web.app import create_app

cv2 = pytest.importorskip("cv2")
import numpy as np  # noqa: E402

NALOG = "graviraj moju sliku 120 x 160 mm na sperplocu, sivi tonovi"
STANJE_RE = re.compile(r'name="stanje" value="([A-Za-z0-9_\-=]+)"')
TOKEN_RE = re.compile(r'name="token" value="([a-f0-9]*)"')


def slika_bajtovi() -> bytes:
    rng = np.random.default_rng(11)
    img = rng.integers(20, 55, size=(320, 240, 3), dtype=np.uint8)
    img[60:260, 60:180] = rng.integers(195, 240, size=(200, 120, 3), dtype=np.uint8)
    return cv2.imencode(".png", img)[1].tobytes()


@pytest.fixture
def klijent(tmp_path, monkeypatch):
    monkeypatch.setenv("FABAGENT_DATA_DIR", str(tmp_path))
    return TestClient(create_app())


def stanje_iz(tekst: str) -> str:
    m = STANJE_RE.search(tekst)
    assert m, "stranica mora nositi stanje razgovora"
    return m.group(1)


def posalji(klijent, stanje, unos, token="", fajl_ime="", files=None):
    r = klijent.post("/razgovor",
                     data={"stanje": stanje, "unos": unos, "token": token,
                           "fajl_ime": fajl_ime},
                     files=files)
    assert r.status_code == 200, r.text[:400]
    return r


def test_stranica_se_otvara(klijent):
    r = klijent.get("/razgovor")
    assert r.status_code == 200
    assert "Razgovor" in r.text
    assert STANJE_RE.search(r.text)


def test_nav_vodi_na_razgovor(klijent):
    assert 'href="/razgovor"' in klijent.get("/").text


def test_prazan_unos_se_odbija(klijent):
    s = stanje_iz(klijent.get("/razgovor").text)
    r = klijent.post("/razgovor", data={"stanje": s, "unos": "   "})
    assert r.status_code == 422


def test_pokvareno_stanje_pocinje_novi_razgovor(klijent):
    r = klijent.post("/razgovor", data={"stanje": "ovo-nije-base64-json", "unos": "zdravo"})
    assert r.status_code == 200
    assert "Prilozi fajl" in r.text or "Priloži" in r.text


def test_nalog_se_procita_i_prikaze(klijent):
    s = stanje_iz(klijent.get("/razgovor").text)
    t = posalji(klijent, s, NALOG).text
    assert "sperploca_3mm" in t
    assert "fotografija" in t


def test_pitanje_ne_gubi_korak(klijent):
    s = stanje_iz(klijent.get("/razgovor").text)
    t1 = posalji(klijent, s, NALOG).text
    t2 = posalji(klijent, stanje_iz(t1), "koji razmak linija?").text
    assert "DPI" in t2
    assert "Da nastavimo" in t2


def test_cijena_dobije_ne_znam(klijent):
    s = stanje_iz(klijent.get("/razgovor").text)
    t = posalji(klijent, s, "koja je cijena sperploce?").text
    assert "ne izmisljam" in t


def test_parametri_se_prikazuju_sa_izvorom(klijent):
    s = stanje_iz(klijent.get("/razgovor").text)
    r = posalji(klijent, s, NALOG, files={"fajl": ("motiv.png", slika_bajtovi(), "image/png")})
    token, t = TOKEN_RE.search(r.text).group(1), r.text
    for unos in ("0.15", "da"):
        t = posalji(klijent, stanje_iz(t), unos, token=token, fajl_ime="motiv.png").text
    assert "Postavke mašine" in t
    assert "mm/min" in t
    assert "preset" in t


def test_bez_fajla_nema_dugmeta_nego_blokada(klijent):
    """Ranije: odgovoris na sve, pritisnes dugme i dobijes 'nedostaju: fajl'."""
    s = stanje_iz(klijent.get("/razgovor").text)
    t = posalji(klijent, s, NALOG).text
    assert "Prilozi fajl" in t
    for unos in ("0.15", "da"):
        t = posalji(klijent, stanje_iz(t), unos).text
    assert "Napravi posao" not in t
    assert "Fajl nije prilozen" in t


def test_blokade_se_prikazuju(klijent):
    s = stanje_iz(klijent.get("/razgovor").text)
    t = posalji(klijent, s, "graviraj sliku 100 x 100 mm").text
    assert "zaustavlja posao" in t
    assert "Materijal" in t


def test_upozorenje_o_granicama_i_kerfu(klijent):
    s = stanje_iz(klijent.get("/razgovor").text)
    t = posalji(klijent, s, NALOG).text
    assert "$130" in t
    assert "Kerf" in t or "kerf" in t


def test_fajl_se_primi_i_zapamti(klijent):
    s = stanje_iz(klijent.get("/razgovor").text)
    r = posalji(klijent, s, NALOG, files={"fajl": ("motiv.png", slika_bajtovi(), "image/png")})
    assert "motiv.png" in r.text
    token = TOKEN_RE.search(r.text).group(1)
    assert token, "token fajla mora ostati u formi"
    t2 = posalji(klijent, stanje_iz(r.text), "0.15", token=token, fajl_ime="motiv.png").text
    assert TOKEN_RE.search(t2).group(1) == token


def test_bez_fajla_posao_se_ne_pravi(klijent):
    s = stanje_iz(klijent.get("/razgovor").text)
    t = posalji(klijent, s, NALOG).text
    for unos in ("0.15", "da"):
        t = posalji(klijent, stanje_iz(t), unos).text
    r = klijent.post("/razgovor/napravi", data={"stanje": stanje_iz(t)})
    assert r.status_code == 422
    assert "fajl" in r.text.lower()


def test_sa_blokadom_posao_se_ne_pravi(klijent):
    s = stanje_iz(klijent.get("/razgovor").text)
    t = posalji(klijent, s, "graviraj sliku 100 x 100 mm").text
    r = klijent.post("/razgovor/napravi", data={"stanje": stanje_iz(t)})
    assert r.status_code == 422


def test_cio_tok_pravi_posao(klijent):
    s = stanje_iz(klijent.get("/razgovor").text)
    r = posalji(klijent, s, NALOG, files={"fajl": ("motiv.png", slika_bajtovi(), "image/png")})
    token = TOKEN_RE.search(r.text).group(1)
    t = r.text
    for unos in ("0.15", "da"):
        t = posalji(klijent, stanje_iz(t), unos, token=token, fajl_ime="motiv.png").text
    assert "Napravi posao" in t
    r = klijent.post("/razgovor/napravi",
                     data={"stanje": stanje_iz(t), "token": token, "fajl_ime": "motiv.png"})
    assert r.status_code == 200, r.text[:400]
    assert "job" in r.text.lower()


def test_kamera_panel_je_na_stranici(klijent):
    """Nadzor sa mjesta: ziva slika stoji uz razgovor, ne na drugoj stranici."""
    t = klijent.get("/razgovor").text
    assert '/camera/stream' in t
    assert "Kamera" in t
    assert 'href="/camera"' in t


def test_nekalibrisana_kamera_se_javlja_u_upozorenjima(klijent):
    t = klijent.get("/razgovor").text
    assert "Nije kalibrisana" in t or "NIJE kalibrisana" in t

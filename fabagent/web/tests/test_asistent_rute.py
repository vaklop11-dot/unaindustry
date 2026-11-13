"""Asistent kroz web: nalog -> pitanja -> posao. Fajl se cuva izmedju koraka."""

import re

import pytest
from fastapi.testclient import TestClient

from fabagent.web.app import create_app

cv2 = pytest.importorskip("cv2")
import numpy as np  # noqa: E402

NALOG = "graviraj moju sliku 120 x 160 mm na sperplocu, bez pozadine, sivi tonovi"


def slika_bajtovi() -> bytes:
    """Svijetao motiv u sredini na tamnoj pozadini, 240x320."""
    rng = np.random.default_rng(11)
    img = rng.integers(20, 55, size=(320, 240, 3), dtype=np.uint8)
    img[60:260, 60:180] = rng.integers(195, 240, size=(200, 120, 3), dtype=np.uint8)
    return cv2.imencode(".png", img)[1].tobytes()


@pytest.fixture
def klijent(tmp_path, monkeypatch):
    monkeypatch.setenv("FABAGENT_DATA_DIR", str(tmp_path))
    return TestClient(create_app())


def procitaj(klijent):
    r = klijent.post("/asistent", data={"tekst": NALOG},
                     files={"fajl": ("motiv.png", slika_bajtovi(), "image/png")})
    assert r.status_code == 200, r.text[:300]
    return r


def test_stranica_se_otvara(klijent):
    assert klijent.get("/asistent").status_code == 200


def test_nalog_bez_teksta_se_odbija(klijent):
    r = klijent.post("/asistent", data={"tekst": "  "})
    assert r.status_code == 422
    assert "MissingParam" in r.text or "nedostaj" in r.text.lower()


def test_procitano_se_prikazuje(klijent):
    t = procitaj(klijent).text
    for ocekivano in ("fotografija", "sperploca_3mm", "120.0", "160.0", "grayscale"):
        assert ocekivano in t, ocekivano


def test_pita_samo_ono_sto_fali(klijent):
    t = procitaj(klijent).text
    assert 'name="interval_mm"' in t          # nije receno u nalogu
    assert 'name="materijal"' not in t        # jeste receno


def test_fajl_prezivi_izmedju_koraka(klijent):
    t = procitaj(klijent).text
    token = re.search(r'name="token" value="([a-f0-9]+)"', t).group(1)
    assert klijent.get(f"/asistent/fajl/{token}").status_code == 200


def test_bez_odgovora_na_pitanje_posao_se_ne_pravi(klijent):
    token = re.search(r'name="token" value="([a-f0-9]+)"', procitaj(klijent).text).group(1)
    r = klijent.post("/asistent/napravi", data={"token": token, "tekst": NALOG})
    assert r.status_code == 422
    assert "interval_mm" in r.text


def test_cio_tok_pravi_posao(klijent):
    token = re.search(r'name="token" value="([a-f0-9]+)"', procitaj(klijent).text).group(1)
    r = klijent.post("/asistent/napravi", data={
        "token": token, "tekst": NALOG, "interval_mm": "0.14",
        "okvir_lijevo": "0.15", "okvir_gore": "0.1", "okvir_desno": "0.9", "okvir_dolje": "0.9",
        "pozadina": "bijela",
    })
    assert r.status_code == 200, r.text[:400]
    assert "bez_pozadine" in r.text


def test_razmak_ispod_granice_se_odbija_sa_razlogom(klijent):
    token = re.search(r'name="token" value="([a-f0-9]+)"', procitaj(klijent).text).group(1)
    r = klijent.post("/asistent/napravi", data={
        "token": token, "tekst": NALOG, "interval_mm": "0.02"})
    assert r.status_code == 422
    assert "piksela" in r.text

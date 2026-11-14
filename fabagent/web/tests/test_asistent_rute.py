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


# ------------------- odgovori moraju prezivjeti korak (petlja u krug) ----------
ODGOVORENO_RE = re.compile(
    r'<input type="hidden" name="(posao|materijal|sirina_mm|visina_mm|rezim_slike'
    r'|interval_mm|komada)" value="([^"]*)"')
PITA_RE = re.compile(
    r'<(?:select|input) class="form-(?:select|control)"[^>]*name="(rezim_slike'
    r'|interval_mm|materijal|posao|sirina_mm)"')

NALOG_BEZ_REZIMA = "graviraj moju sliku 120 x 160 mm na sperplocu"


def _procitaj_bez_rezima(klijent):
    r = klijent.post("/asistent", data={"tekst": NALOG_BEZ_REZIMA},
                     files={"fajl": ("motiv.png", slika_bajtovi(), "image/png")})
    assert r.status_code == 200, r.text[:300]
    return r


def test_odgovor_se_vrati_u_formu(klijent):
    """Bez ovoga: odgovoris A pa pita B, odgovoris B pa opet pita A - u nedogled."""
    t = _procitaj_bez_rezima(klijent).text
    token = re.search(r'name="token" value="([a-f0-9]+)"', t).group(1)
    assert set(PITA_RE.findall(t)) == {"rezim_slike", "interval_mm"}

    r = klijent.post("/asistent/napravi", data={
        "token": token, "tekst": NALOG_BEZ_REZIMA, "rezim_slike": "grayscale"})
    assert r.status_code == 422                      # interval jos fali
    assert PITA_RE.findall(r.text) == ["interval_mm"]
    assert dict(ODGOVORENO_RE.findall(r.text)) == {"rezim_slike": "grayscale"}


def test_drugi_korak_zavrsi_posao_bez_vracanja_na_prvo(klijent):
    t = _procitaj_bez_rezima(klijent).text
    token = re.search(r'name="token" value="([a-f0-9]+)"', t).group(1)
    r = klijent.post("/asistent/napravi", data={
        "token": token, "tekst": NALOG_BEZ_REZIMA, "rezim_slike": "grayscale"})

    # Browser salje skrivena polja iz prethodnog koraka plus novi odgovor.
    podaci = {"token": token, "tekst": NALOG_BEZ_REZIMA, "interval_mm": "0.15"}
    podaci.update(dict(ODGOVORENO_RE.findall(r.text)))
    r2 = klijent.post("/asistent/napravi", data=podaci)
    assert r2.status_code == 200, r2.text[:400]
    assert PITA_RE.findall(r2.text) == []            # nista se ne pita ponovo


def test_neispravan_broj_se_ne_pamti_kao_odgovor(klijent):
    t = _procitaj_bez_rezima(klijent).text
    token = re.search(r'name="token" value="([a-f0-9]+)"', t).group(1)
    r = klijent.post("/asistent/napravi", data={
        "token": token, "tekst": NALOG_BEZ_REZIMA, "interval_mm": "ovo nije broj"})
    assert r.status_code == 422
    assert "interval_mm" not in dict(ODGOVORENO_RE.findall(r.text))


def test_nav_pokazuje_oznaku_koda(klijent):
    """Ako server servira stari kod, u navigaciji stoji staro vrijeme."""
    assert "kod:" in klijent.get("/asistent").text


def test_health_nosi_oznaku_koda(klijent):
    podaci = klijent.get("/health").json()
    assert "kod" in podaci and podaci["kod"]

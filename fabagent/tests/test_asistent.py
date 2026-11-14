"""Asistent cita nalog napisan obicnim rijecima. Sto ne pise - pita, ne pogadja."""

import pytest

from fabagent.core import asistent as A
from fabagent.core import config as C


def polja(pitanja):
    return [p["polje"] for p in pitanja]


# --- cita ono sto pise ------------------------------------------------------

def test_cita_cijeli_nalog():
    n = A.procitaj("graviraj moju sliku 120 x 160 mm na sperplocu, bez pozadine, sivi tonovi")
    assert n.posao == "fotografija"
    assert n.materijal == "sperploca_3mm"
    assert (n.sirina_mm, n.visina_mm) == (120.0, 160.0)
    assert n.rezim_slike == "grayscale"
    assert n.ukloni_pozadinu is True


def test_radi_i_bez_kvacica():
    n = A.procitaj("gravura na sperploci 80 mm")
    assert n.materijal == "sperploca_3mm"
    assert n.sirina_mm == 80.0


def test_kvacice_ne_smetaju():
    n = A.procitaj("Gravura na šperploči 80 mm, bez pozadine")
    assert n.materijal == "sperploca_3mm"
    assert n.ukloni_pozadinu is True


def test_dpi_se_pretvara_u_razmak():
    n = A.procitaj("foto 100 mm, 254 dpi")
    assert n.interval_mm == 0.1


def test_interval_se_cita_direktno():
    assert A.procitaj("foto 100 mm, interval 0,14 mm").interval_mm == 0.14


def test_broj_bez_jedinice_se_ne_uzima_kao_mjera():
    """'5 komada' nije 5 mm."""
    n = A.procitaj("gravura 5 komada na sperplocu")
    assert n.sirina_mm is None
    assert n.komada == 5


def test_prazan_nalog_ne_cita_nista():
    n = A.procitaj("")
    assert n.posao is None and n.materijal is None and n.sirina_mm is None


def test_svako_procitano_polje_ima_izvor():
    n = A.procitaj("rezi sperplocu 50 mm")
    for polje, podatak in n.procitano.items():
        assert podatak["iz"], polje


# --- pita ono sto ne pise ---------------------------------------------------

def test_pita_sve_kad_nalog_ne_kaze_nista():
    p = polja(A.pitanja(A.procitaj("treba mi nesto")))
    assert "posao" in p and "materijal" in p and "sirina_mm" in p


def test_ne_pita_ono_sto_je_vec_receno():
    p = polja(A.pitanja(A.procitaj(
        "foto gravura 120 x 160 mm na sperplocu, sivi tonovi, interval 0.14 mm")))
    assert p == []


def test_za_fotografiju_trazi_razmak_linija():
    p = polja(A.pitanja(A.procitaj("foto gravura 120 mm na sperplocu, sivi tonovi")))
    assert "interval_mm" in p


def test_za_rez_ne_trazi_razmak_linija():
    p = polja(A.pitanja(A.procitaj("rezi sperplocu 120 mm")))
    assert "interval_mm" not in p and "rezim_slike" not in p


def test_izbor_materijala_dolazi_iz_configa():
    pitanje = [p for p in A.pitanja(A.procitaj("gravura 50 mm")) if p["polje"] == "materijal"][0]
    assert [i["vrijednost"] for i in pitanje["izbor"]] == list(C.LASER_MATERIALS_ALL)


# --- korekcije --------------------------------------------------------------

def test_javlja_kad_je_veci_od_radne_povrsine():
    poruke = A.primjedbe(A.procitaj("gravura 900 x 900 mm na sperplocu"))
    assert any("radne povrsine" in m for m in poruke)


def test_javlja_kad_razmak_prelazi_granicu_piksela():
    n = A.procitaj("foto 120 x 160 mm na sperplocu, interval 0.1 mm, sivi tonovi")
    poruke = A.primjedbe(n)
    assert any("piksela" in m and "0.14" in m for m in poruke)


def test_granica_intervala_je_tacna():
    g = A.granica_intervala(120.0, 160.0)
    assert g == pytest.approx(0.1386, abs=0.0005)
    assert (120.0 / g) * (160.0 / g) <= C.RASTER_MAX_PIXELS * 1.01


def test_granica_bez_mjera_ne_izmislja_broj():
    assert A.granica_intervala(None, None) is None
    assert A.granica_intervala(120.0, None) is None


def test_parametri_nose_samo_ono_sto_je_poznato():
    v = A.u_parametre(A.procitaj("gravura na sperplocu"))
    assert v["materijal"] == "sperploca_3mm"
    assert "sirina_mm" not in v
    assert "interval_mm" not in v


# --------------------------------------------------------- kolicina vs dimenzije
def test_dimenzije_nisu_kolicina():
    """'120 x 160 mm' je dimenzija. Ranije je x iz dimenzije citan kao 120 komada."""
    n = A.procitaj("graviraj sliku 120 x 160 mm na sperplocu")
    assert n.komada is None
    assert (n.sirina_mm, n.visina_mm) == (120.0, 160.0)


@pytest.mark.parametrize("tekst,ocekivano", [
    ("napravi 5 kom privjeska", 5),
    ("treba mi 12 komada", 12),
    ("uradi to 3 puta", 3),
    ("5x privjesak", 5),
])
def test_kolicina_se_jos_cita(tekst, ocekivano):
    assert A.procitaj(tekst).komada == ocekivano


def test_kolicina_i_dimenzije_zajedno():
    n = A.procitaj("napravi 4 kom plocica 50 x 30 mm")
    assert n.komada == 4
    assert (n.sirina_mm, n.visina_mm) == (50.0, 30.0)

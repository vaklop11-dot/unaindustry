"""Testovi razgovornog asistenta: stanje, pitanja, znanje, provjera prije starta."""

from __future__ import annotations

import pytest

from fabagent.core import config as C
from fabagent.core import razgovor as R


# ------------------------------------------------------- pitanje ili odgovor
@pytest.mark.parametrize("tekst", [
    "koji razmak linija?", "Sta je kerf", "koliko snage", "zasto 500",
    "da li moze sperploca", "je li fokus vazan", "objasni frame", "kako radi stop",
])
def test_prepoznaje_pitanje(tekst):
    assert R.je_pitanje(tekst) is True


@pytest.mark.parametrize("tekst", [
    "0.12", "da", "ok", "sperploca_3mm", "graviraj sliku 120x160 mm", "100", "neka",
])
def test_prepoznaje_odgovor(tekst):
    assert R.je_pitanje(tekst) is False


def test_da_li_je_pitanje_a_da_je_potvrda():
    assert R.je_pitanje("da li") is True
    assert R.je_pitanje("da") is False
    assert R.je_prihvatanje("da") is True
    assert R.je_prihvatanje("da li") is False


# --------------------------------------------------------------- tok razgovora
def test_prvi_unos_postaje_nalog_i_cita_se():
    s = R.korak(R.novo(), "graviraj sliku 120 x 160 mm na sperplocu, sivi tonovi")
    n = R.nalog(s)
    assert n.posao == "fotografija"
    assert n.materijal == "sperploca_3mm"
    assert (n.sirina_mm, n.visina_mm) == (120.0, 160.0)
    assert n.rezim_slike == "grayscale"


def test_pitanje_ne_mijenja_korak_i_vraca_na_isto():
    s = R.korak(R.novo(), "graviraj sliku 120 x 160 mm na sperplocu, sivi tonovi")
    ceka_prije = s.cekam
    assert ceka_prije, "nesto se mora cekati nakon prvog naloga"
    s2 = R.korak(s, "koji razmak linija?")
    assert s2.cekam == ceka_prije
    assert R.nalog(s2).procitano == R.nalog(s).procitano
    zadnje = " ".join(d["tekst"] for d in s2.dnevnik if d["ko"] == "asistent")
    assert "razmak" in zadnje.lower()


def test_odgovor_na_broj_se_upise():
    s = R.korak(R.novo(), "graviraj sliku 120 x 160 mm na sperplocu, sivi tonovi")
    assert s.cekam == "interval_mm"
    s = R.korak(s, "0,12")                       # zapeta se cita kao tacka
    assert s.odgovori["interval_mm"] == 0.12
    assert R.nalog(s).interval_mm == 0.12


def test_da_prihvata_vrijednost_iz_preseta():
    s = R.korak(R.novo(), "graviraj sliku 100 x 100 mm na sperplocu, sivi tonovi")
    s = R.korak(s, "0.15")
    assert s.faza == "masina"
    assert s.cekam == "ENGRAVE.brzina_mm_s"
    s = R.korak(s, "da")
    zapis = s.masina["ENGRAVE.brzina_mm_s"]
    assert zapis["iz"] == "preset"
    assert zapis["vrijednost"] == pytest.approx(100.0)   # sperploca_3mm engrave


def test_svoj_broj_pise_operatera_kao_izvor():
    s = R.korak(R.novo(), "graviraj sliku 100 x 100 mm na sperplocu, sivi tonovi")
    s = R.korak(R.korak(s, "0.15"), "85")
    assert s.masina["ENGRAVE.brzina_mm_s"] == {"vrijednost": 85.0, "iz": "operater"}


def test_van_opsega_se_ne_upise():
    s = R.korak(R.novo(), "graviraj sliku 100 x 100 mm na sperplocu, sivi tonovi")
    s = R.korak(s, "0.15")
    previse = C.LAYER_SPEED_RANGE_MM_S[1] + 1
    s2 = R.korak(s, str(previse))
    assert "ENGRAVE.brzina_mm_s" not in s2.masina
    assert s2.cekam == "ENGRAVE.brzina_mm_s"
    assert any("opsega" in d["tekst"] for d in s2.dnevnik if d["ko"] == "asistent")


def test_izbor_iz_liste_se_prepozna():
    s = R.korak(R.novo(), "napravi nesto 100 x 100 mm")
    assert s.cekam == "posao"
    s = R.korak(s, "rez")
    assert R.nalog(s).posao == "rez"


def test_neprepoznat_izbor_ne_upise_nista():
    s = R.korak(R.novo(), "napravi nesto 100 x 100 mm")
    s2 = R.korak(s, "bezveze")
    assert "posao" not in s2.odgovori
    assert s2.cekam == "posao"


def test_cijeli_tok_dolazi_do_gotovo():
    s = R.korak(R.novo(), "graviraj sliku 100 x 100 mm na sperplocu, sivi tonovi")
    for unos in ("0.15", "da", "da", "da"):
        s = R.korak(s, unos)
    assert s.faza == "gotovo"
    assert s.cekam == ""
    blokade, _ = R.provjera_prije_starta(s)
    assert blokade == []


# ------------------------------------------------------------------- znanje
def test_kerf_kaze_da_nije_izmjeren():
    s = R.korak(R.novo(), "rezi sperplocu 100 x 100 mm")
    tekst = R.odgovor_na_pitanje(s, "sta je kerf?")
    assert "NEMA PODATKA" in tekst
    assert "kalibracija_kerf" in tekst


def test_kerf_cita_izmjerenu_vrijednost(monkeypatch):
    monkeypatch.setattr(C, "LASER_KERF_PO_MATERIJALU", {"sperploca_3mm": 0.18})
    s = R.korak(R.novo(), "rezi sperplocu 100 x 100 mm")
    tekst = R.odgovor_na_pitanje(s, "koliki je kerf?")
    assert "0.18" in tekst
    assert "NEMA PODATKA" not in tekst


def test_granice_kazu_da_nisu_potvrdjene():
    tekst = R.odgovor_na_pitanje(R.novo(), "koje su granice radne povrsine?")
    assert "$130" in tekst and "$131" in tekst
    assert "DEKLARISANA" in tekst


def test_granice_kad_su_potvrdjene(monkeypatch):
    monkeypatch.setattr(C, "LASER_BOUNDS_CONFIRMED", True)
    tekst = R.odgovor_na_pitanje(R.novo(), "koje su granice?")
    assert "potvrdjena" in tekst


def test_brzina_daje_i_mm_min():
    s = R.korak(R.novo(), "graviraj sliku 100 x 100 mm na sperplocu, sivi tonovi")
    tekst = R.odgovor_na_pitanje(s, "koja brzina?")
    assert "mm/min" in tekst
    assert "6000" in tekst          # 100 mm/s * 60

def test_snaga_daje_s_vrijednost():
    s = R.korak(R.novo(), "graviraj sliku 100 x 100 mm na sperplocu, sivi tonovi")
    tekst = R.odgovor_na_pitanje(s, "kolika snaga?")
    assert f"S{int(round(0.30 * C.GRBL_S_MAX))}" in tekst


def test_razmak_racuna_granicu_iz_mjera():
    s = R.korak(R.novo(), "graviraj sliku 120 x 160 mm na sperplocu, sivi tonovi")
    tekst = R.odgovor_na_pitanje(s, "koji razmak?")
    assert "piksela" in tekst
    assert "DPI" in tekst


@pytest.mark.parametrize("pitanje", [
    "koja je cijena sperploce u Doboju?",      # sadrzi 'sperploc' - ne smije dati preset
    "gdje da kupim mdf?",
    "koliki je PDV na gravuru?",
    "koliko da naplatim ovaj posao?",
    "ima li garancija na masinu?",
    "koja je prognoza za sutra?",
])
def test_nepoznata_tema_ne_izmislja(pitanje):
    tekst = R.odgovor_na_pitanje(R.novo(), pitanje)
    assert "ne izmisljam" in tekst
    assert "kerf" in tekst          # ponudi teme o kojima zna


def test_cijena_ne_pada_u_temu_materijala():
    s = R.korak(R.novo(), "rezi sperplocu 100 x 100 mm")
    assert R.tema("koja je cijena sperploce?") is None
    assert R.tema("sta je sperploca?") == "ovaj materijal"
    assert "ne izmisljam" in R.odgovor_na_pitanje(s, "koja je cijena sperploce?")


def test_materijali_dolaze_iz_preseta():
    tekst = R.odgovor_na_pitanje(R.novo(), "koji materijali postoje?")
    for key in C.LASER_MATERIALS_ALL:
        assert key in tekst


def test_bez_mreze_je_u_znanju():
    tekst = R.odgovor_na_pitanje(R.novo(), "trebas li internet?")
    assert "bez interneta" in tekst
    assert "API" in tekst


# ------------------------------------------------------------------ provjera
def test_blokada_kad_fali_materijal():
    s = R.korak(R.novo(), "graviraj sliku 100 x 100 mm")
    blokade, _ = R.provjera_prije_starta(s)
    assert any("Materijal" in b for b in blokade)


def test_blokada_kad_je_vece_od_radne_povrsine():
    veliko = C.LASER_WORK_AREA_MM[0] + 50
    s = R.korak(R.novo(), f"graviraj sliku {veliko:g} x 100 mm na sperplocu, sivi tonovi")
    blokade, _ = R.provjera_prije_starta(s)
    assert any("radne povrsine" in b for b in blokade)


def test_blokada_kad_materijal_ne_moze_rez():
    s = R.korak(R.novo(), "rezi eloksirani aluminij 50 x 50 mm")
    blokade, _ = R.provjera_prije_starta(s)
    assert any("ne reze" in b for b in blokade)


def test_upozorenje_da_preset_nije_provjeren():
    s = R.korak(R.novo(), "graviraj sliku 100 x 100 mm na sperplocu, sivi tonovi")
    _, upozorenja = R.provjera_prije_starta(s)
    assert any("NISU mjerene" in u for u in upozorenja)


def test_upozorenje_uvijek_trazi_frame_prije_starta():
    s = R.korak(R.novo(), "graviraj sliku 100 x 100 mm na sperplocu, sivi tonovi")
    _, upozorenja = R.provjera_prije_starta(s)
    assert any("Frame" in u for u in upozorenja)


def test_blokada_za_razmak_ispod_granice():
    s = R.korak(R.novo(), "graviraj sliku 300 x 300 mm na sperplocu, sivi tonovi")
    s = R.korak(s, "0.05")
    blokade, _ = R.provjera_prije_starta(s)
    assert any("piksela" in b for b in blokade)


# ------------------------------------------------------- stanje i parametri
def test_stanje_prezivi_serijalizaciju():
    s = R.korak(R.novo(), "graviraj sliku 120 x 160 mm na sperplocu, sivi tonovi")
    s = R.korak(s, "0.15")
    kopija = R.iz_dicta(s.kao_dict())
    assert kopija.kao_dict() == s.kao_dict()
    assert R.nalog(kopija).interval_mm == 0.15


def test_korak_ne_mijenja_ulazno_stanje():
    s = R.korak(R.novo(), "graviraj sliku 100 x 100 mm na sperplocu, sivi tonovi")
    prije = s.kao_dict()
    R.korak(s, "0.15")
    assert s.kao_dict() == prije


def test_parametri_masine_pokazuju_izvor():
    s = R.korak(R.novo(), "graviraj sliku 100 x 100 mm na sperplocu, sivi tonovi")
    s = R.korak(R.korak(s, "0.15"), "85")
    redovi = R.parametri_masine(s)
    assert redovi[0]["parametar"] == "Brzina"
    assert redovi[0]["iz"] == "operater"
    assert "mm/min" in redovi[0]["vrijednost"]


def test_slojevi_po_vrsti_posla():
    rez = R.korak(R.novo(), "rezi sperplocu 100 x 100 mm")
    assert R.slojevi(R.nalog(rez)) == ("CUT",)
    grav = R.korak(R.novo(), "graviraj sliku 100 x 100 mm na sperplocu, sivi tonovi")
    assert R.slojevi(R.nalog(grav)) == ("ENGRAVE",)
    assert R.slojevi(R.nalog(R.novo())) == ()


def test_prazan_unos_ne_mijenja_nista():
    s = R.korak(R.novo(), "graviraj sliku 100 x 100 mm na sperplocu, sivi tonovi")
    assert R.korak(s, "   ").kao_dict() == s.kao_dict()


def test_gdje_smo_nabraja_procitano():
    s = R.korak(R.novo(), "graviraj sliku 120 x 160 mm na sperplocu, sivi tonovi")
    tekst = R.odgovor_na_pitanje(s, "gdje smo?")
    assert "sperploca_3mm" in tekst


# ------------------------- pitanje sa izborom mora nabrojati ponudjene odgovore
def test_pitanje_sa_izborom_nabraja_odgovore():
    """U razgovoru nema padajuce liste, pa pitanje bez nabrojanih odgovora
    ostavlja covjeka da pogadja - i razgovor ulazi u petlju."""
    s = R.korak(R.novo(), "graviraj moju sliku 120 x 160 mm na sperplocu")
    p = R.sljedece_pitanje(s)
    assert p["polje"] == "rezim_slike"
    tekst = R.tekst_pitanja(p)
    for mod in ("grayscale", "dither", "threshold", "vectorize"):
        assert mod in tekst, mod
    izgovoreno = [d["tekst"] for d in s.dnevnik if d["ko"] == "asistent"][-1]
    assert "grayscale" in izgovoreno


def test_izbor_se_prima_i_po_oznaci():
    s = R.korak(R.novo(), "graviraj moju sliku 120 x 160 mm na sperplocu")
    s2 = R.korak(s, "sivi tonovi")
    assert s2.odgovori["rezim_slike"] == "grayscale"


def test_izbor_se_prima_i_po_vrijednosti():
    s = R.korak(R.novo(), "graviraj moju sliku 120 x 160 mm na sperplocu")
    assert R.korak(s, "dither").odgovori["rezim_slike"] == "dither"


def test_odbijeni_izbor_kaze_sta_je_dozvoljeno():
    s = R.korak(R.novo(), "graviraj moju sliku 120 x 160 mm na sperplocu")
    s2 = R.korak(s, "0.15")
    poruka = [d["tekst"] for d in s2.dnevnik if d["ko"] == "asistent"][-2]
    assert "ne prepoznajem" in poruka
    assert "grayscale" in poruka


def test_tok_bez_rezima_u_nalogu_dolazi_do_gotovo():
    """Nalog bez 'sivi tonovi': mora se moci odgovoriti na pitanje o rezimu."""
    s = R.korak(R.novo(), "graviraj moju sliku 120 x 160 mm na sperplocu")
    for unos in ("grayscale", "0.15", "da", "da", "da"):
        s = R.korak(s, unos)
    assert s.faza == "gotovo", [d["tekst"] for d in s.dnevnik][-3:]
    assert R.provjera_prije_starta(s)[0] == []


def test_nalog_sa_dimenzijama_ne_postavlja_komada():
    s = R.korak(R.novo(), "graviraj moju sliku 120 x 160 mm na sperplocu")
    assert R.nalog(s).komada is None
    procitano = [d["tekst"] for d in s.dnevnik if d["ko"] == "asistent"][0]
    assert "komada" not in procitano


def test_broj_sa_granicama_kaze_granice():
    s = R.korak(R.novo(), "napravi nesto")
    s = R.korak(s, "fotografija")
    s = R.korak(s, "sperploca_3mm")
    p = R.sljedece_pitanje(s)
    assert p["polje"] == "sirina_mm"
    tekst = R.tekst_pitanja(p)
    assert "najmanje" in tekst and "mm" in tekst

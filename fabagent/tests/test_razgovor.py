"""Testovi razgovornog asistenta: stanje, pitanja, znanje, provjera prije starta."""

from __future__ import annotations

import pytest

from fabagent.core import config as C
from fabagent.core import razgovor as R


def _novo(ime: str = "motiv.png") -> R.Stanje:
    """Razgovor sa vec prilozenim fajlom - fajl je prvi korak, pa ga testovi
    toka moraju imati. Testovi koji bas provjeravaju izostanak fajla koriste R.novo()."""
    s = R.novo()
    s.fajl = ime
    return s


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
    s = R.korak(_novo(), "graviraj sliku 120 x 160 mm na sperplocu, sivi tonovi")
    n = R.nalog(s)
    assert n.posao == "fotografija"
    assert n.materijal == "sperploca_3mm"
    assert (n.sirina_mm, n.visina_mm) == (120.0, 160.0)
    assert n.rezim_slike == "grayscale"


def test_pitanje_ne_mijenja_korak_i_vraca_na_isto():
    s = R.korak(_novo(), "graviraj sliku 120 x 160 mm na sperplocu, sivi tonovi")
    ceka_prije = s.cekam
    assert ceka_prije, "nesto se mora cekati nakon prvog naloga"
    s2 = R.korak(s, "koji razmak linija?")
    assert s2.cekam == ceka_prije
    assert R.nalog(s2).procitano == R.nalog(s).procitano
    zadnje = " ".join(d["tekst"] for d in s2.dnevnik if d["ko"] == "asistent")
    assert "razmak" in zadnje.lower()


def test_odgovor_na_broj_se_upise():
    s = R.korak(_novo(), "graviraj sliku 120 x 160 mm na sperplocu, sivi tonovi")
    assert s.cekam == "interval_mm"
    s = R.korak(s, "0,12")                       # zapeta se cita kao tacka
    assert s.odgovori["interval_mm"] == 0.12
    assert R.nalog(s).interval_mm == 0.12


def test_da_prihvata_sve_postavke_iz_preseta():
    """Jedno 'da' postavi sve tri - tri odvojena pitanja su tri prekida umjesto jednog."""
    s = R.korak(_novo(), "graviraj sliku 100 x 100 mm na sperplocu, sivi tonovi")
    s = R.korak(s, "0.15")
    assert s.faza == "masina"
    assert s.cekam == "ENGRAVE.postavke"
    s = R.korak(s, "da")
    for polje, ocekivano in (("brzina_mm_s", 100.0), ("snaga_posto", 30.0), ("prolaza", 1)):
        zapis = s.masina[f"ENGRAVE.{polje}"]
        assert zapis["iz"] == "preset"
        assert zapis["vrijednost"] == pytest.approx(ocekivano)
    assert R.sljedece_pitanje(s) is None


def test_imenovana_vrijednost_je_operaterova_ostalo_iz_preseta():
    s = R.korak(_novo(), "graviraj sliku 100 x 100 mm na sperplocu, sivi tonovi")
    s = R.korak(R.korak(s, "0.15"), "brzina 85")
    assert s.masina["ENGRAVE.brzina_mm_s"] == {"vrijednost": 85.0, "iz": "operater"}
    assert s.masina["ENGRAVE.snaga_posto"]["iz"] == "preset"
    assert R.sljedece_pitanje(s) is None


def test_vise_imenovanih_vrijednosti_odjednom():
    s = R.korak(_novo(), "graviraj sliku 100 x 100 mm na sperplocu, sivi tonovi")
    s = R.korak(R.korak(s, "0.15"), "brzina 85 snaga 40 prolaza 2")
    assert s.masina["ENGRAVE.brzina_mm_s"]["vrijednost"] == 85.0
    assert s.masina["ENGRAVE.snaga_posto"]["vrijednost"] == 40.0
    assert s.masina["ENGRAVE.prolaza"]["vrijednost"] == 2.0
    assert all(z["iz"] == "operater" for z in s.masina.values())


def test_goli_broj_bez_imena_se_ne_prihvata():
    """'85' ne kaze je li brzina ili snaga - ne pogadja se."""
    s = R.korak(_novo(), "graviraj sliku 100 x 100 mm na sperplocu, sivi tonovi")
    s2 = R.korak(R.korak(s, "0.15"), "85")
    assert "ENGRAVE.brzina_mm_s" not in s2.masina
    assert "imenovano" in [d["tekst"] for d in s2.dnevnik if d["ko"] == "asistent"][-2]


def test_van_opsega_se_ne_upise():
    s = R.korak(_novo(), "graviraj sliku 100 x 100 mm na sperplocu, sivi tonovi")
    s = R.korak(s, "0.15")
    previse = C.LAYER_SPEED_RANGE_MM_S[1] + 1
    s2 = R.korak(s, f"brzina {previse:g}")
    assert s2.masina == {}                       # nijedna se ne upise ako je jedna losa
    assert s2.cekam == "ENGRAVE.postavke"
    assert any("opsega" in d["tekst"] for d in s2.dnevnik if d["ko"] == "asistent")


def test_izbor_iz_liste_se_prepozna():
    s = R.korak(_novo(), "napravi nesto 100 x 100 mm")
    assert s.cekam == "posao"
    s = R.korak(s, "rez")
    assert R.nalog(s).posao == "rez"


def test_neprepoznat_izbor_ne_upise_nista():
    s = R.korak(_novo(), "napravi nesto 100 x 100 mm")
    s2 = R.korak(s, "bezveze")
    assert "posao" not in s2.odgovori
    assert s2.cekam == "posao"


def test_cijeli_tok_dolazi_do_gotovo():
    s = R.korak(_novo(), "graviraj sliku 100 x 100 mm na sperplocu, sivi tonovi")
    for unos in ("0.15", "da"):
        s = R.korak(s, unos)
    assert s.faza == "gotovo"
    assert s.cekam == ""
    blokade, _ = R.provjera_prije_starta(s)
    assert blokade == []


# ------------------------------------------------------------------- znanje
def test_kerf_kaze_da_nije_izmjeren():
    s = R.korak(_novo(), "rezi sperplocu 100 x 100 mm")
    tekst = R.odgovor_na_pitanje(s, "sta je kerf?")
    assert "NEMA PODATKA" in tekst
    assert "kalibracija_kerf" in tekst


def test_kerf_cita_izmjerenu_vrijednost(monkeypatch):
    monkeypatch.setattr(C, "LASER_KERF_PO_MATERIJALU", {"sperploca_3mm": 0.18})
    s = R.korak(_novo(), "rezi sperplocu 100 x 100 mm")
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
    s = R.korak(_novo(), "graviraj sliku 100 x 100 mm na sperplocu, sivi tonovi")
    tekst = R.odgovor_na_pitanje(s, "koja brzina?")
    assert "mm/min" in tekst
    assert "6000" in tekst          # 100 mm/s * 60

def test_snaga_daje_s_vrijednost():
    s = R.korak(_novo(), "graviraj sliku 100 x 100 mm na sperplocu, sivi tonovi")
    tekst = R.odgovor_na_pitanje(s, "kolika snaga?")
    assert f"S{int(round(0.30 * C.GRBL_S_MAX))}" in tekst


def test_razmak_racuna_granicu_iz_mjera():
    s = R.korak(_novo(), "graviraj sliku 120 x 160 mm na sperplocu, sivi tonovi")
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
    s = R.korak(_novo(), "rezi sperplocu 100 x 100 mm")
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
    s = R.korak(_novo(), "graviraj sliku 100 x 100 mm")
    blokade, _ = R.provjera_prije_starta(s)
    assert any("Materijal" in b for b in blokade)


def test_blokada_kad_je_vece_od_radne_povrsine():
    veliko = C.LASER_WORK_AREA_MM[0] + 50
    s = R.korak(_novo(), f"graviraj sliku {veliko:g} x 100 mm na sperplocu, sivi tonovi")
    blokade, _ = R.provjera_prije_starta(s)
    assert any("radne povrsine" in b for b in blokade)


def test_blokada_kad_materijal_ne_moze_rez():
    s = R.korak(_novo(), "rezi eloksirani aluminij 50 x 50 mm")
    blokade, _ = R.provjera_prije_starta(s)
    assert any("ne reze" in b for b in blokade)


def test_upozorenje_da_preset_nije_provjeren():
    s = R.korak(_novo(), "graviraj sliku 100 x 100 mm na sperplocu, sivi tonovi")
    _, upozorenja = R.provjera_prije_starta(s)
    assert any("NISU mjerene" in u for u in upozorenja)


def test_upozorenje_uvijek_trazi_frame_prije_starta():
    s = R.korak(_novo(), "graviraj sliku 100 x 100 mm na sperplocu, sivi tonovi")
    _, upozorenja = R.provjera_prije_starta(s)
    assert any("Frame" in u for u in upozorenja)


def test_blokada_za_razmak_ispod_granice():
    s = R.korak(_novo(), "graviraj sliku 300 x 300 mm na sperplocu, sivi tonovi")
    s = R.korak(s, "0.05")
    blokade, _ = R.provjera_prije_starta(s)
    assert any("piksela" in b for b in blokade)


# ------------------------------------------------------- stanje i parametri
def test_stanje_prezivi_serijalizaciju():
    s = R.korak(_novo(), "graviraj sliku 120 x 160 mm na sperplocu, sivi tonovi")
    s = R.korak(s, "0.15")
    kopija = R.iz_dicta(s.kao_dict())
    assert kopija.kao_dict() == s.kao_dict()
    assert R.nalog(kopija).interval_mm == 0.15


def test_korak_ne_mijenja_ulazno_stanje():
    s = R.korak(_novo(), "graviraj sliku 100 x 100 mm na sperplocu, sivi tonovi")
    prije = s.kao_dict()
    R.korak(s, "0.15")
    assert s.kao_dict() == prije


def test_parametri_masine_pokazuju_izvor():
    s = R.korak(_novo(), "graviraj sliku 100 x 100 mm na sperplocu, sivi tonovi")
    s = R.korak(R.korak(s, "0.15"), "brzina 85")
    redovi = R.parametri_masine(s)
    assert redovi[0]["parametar"] == "Brzina"
    assert redovi[0]["iz"] == "operater"
    assert "mm/min" in redovi[0]["vrijednost"]
    assert [r["iz"] for r in redovi] == ["operater", "preset", "preset"]


def test_slojevi_po_vrsti_posla():
    rez = R.korak(_novo(), "rezi sperplocu 100 x 100 mm")
    assert R.slojevi(R.nalog(rez)) == ("CUT",)
    grav = R.korak(_novo(), "graviraj sliku 100 x 100 mm na sperplocu, sivi tonovi")
    assert R.slojevi(R.nalog(grav)) == ("ENGRAVE",)
    assert R.slojevi(R.nalog(R.novo())) == ()


def test_prazan_unos_ne_mijenja_nista():
    s = R.korak(_novo(), "graviraj sliku 100 x 100 mm na sperplocu, sivi tonovi")
    assert R.korak(s, "   ").kao_dict() == s.kao_dict()


def test_gdje_smo_nabraja_procitano():
    s = R.korak(_novo(), "graviraj sliku 120 x 160 mm na sperplocu, sivi tonovi")
    tekst = R.odgovor_na_pitanje(s, "gdje smo?")
    assert "sperploca_3mm" in tekst


# ------------------------- pitanje sa izborom mora nabrojati ponudjene odgovore
def test_pitanje_sa_izborom_nabraja_odgovore():
    """U razgovoru nema padajuce liste, pa pitanje bez nabrojanih odgovora
    ostavlja covjeka da pogadja - i razgovor ulazi u petlju."""
    s = R.korak(_novo(), "graviraj moju sliku 120 x 160 mm na sperplocu")
    p = R.sljedece_pitanje(s)
    assert p["polje"] == "rezim_slike"
    tekst = R.tekst_pitanja(p)
    for mod in ("grayscale", "dither", "threshold", "vectorize"):
        assert mod in tekst, mod
    izgovoreno = [d["tekst"] for d in s.dnevnik if d["ko"] == "asistent"][-1]
    assert "grayscale" in izgovoreno


def test_izbor_se_prima_i_po_oznaci():
    s = R.korak(_novo(), "graviraj moju sliku 120 x 160 mm na sperplocu")
    s2 = R.korak(s, "sivi tonovi")
    assert s2.odgovori["rezim_slike"] == "grayscale"


def test_izbor_se_prima_i_po_vrijednosti():
    s = R.korak(_novo(), "graviraj moju sliku 120 x 160 mm na sperplocu")
    assert R.korak(s, "dither").odgovori["rezim_slike"] == "dither"


def test_odbijeni_izbor_kaze_sta_je_dozvoljeno():
    s = R.korak(_novo(), "graviraj moju sliku 120 x 160 mm na sperplocu")
    s2 = R.korak(s, "0.15")
    poruka = [d["tekst"] for d in s2.dnevnik if d["ko"] == "asistent"][-2]
    assert "ne prepoznajem" in poruka
    assert "grayscale" in poruka


def test_tok_bez_rezima_u_nalogu_dolazi_do_gotovo():
    """Nalog bez 'sivi tonovi': mora se moci odgovoriti na pitanje o rezimu."""
    s = R.korak(_novo(), "graviraj moju sliku 120 x 160 mm na sperplocu")
    for unos in ("grayscale", "0.15", "da"):
        s = R.korak(s, unos)
    assert s.faza == "gotovo", [d["tekst"] for d in s.dnevnik][-3:]
    assert R.provjera_prije_starta(s)[0] == []


def test_nalog_sa_dimenzijama_ne_postavlja_komada():
    s = R.korak(_novo(), "graviraj moju sliku 120 x 160 mm na sperplocu")
    assert R.nalog(s).komada is None
    procitano = [d["tekst"] for d in s.dnevnik if d["ko"] == "asistent"][0]
    assert "komada" not in procitano


def test_broj_sa_granicama_kaze_granice():
    s = R.korak(_novo(), "napravi nesto")
    s = R.korak(s, "fotografija")
    s = R.korak(s, "sperploca_3mm")
    p = R.sljedece_pitanje(s)
    assert p["polje"] == "sirina_mm"
    tekst = R.tekst_pitanja(p)
    assert "najmanje" in tekst and "mm" in tekst


# ------------ odgovor koji pocinje dvosmislenom rijecju nije pitanje -----------
@pytest.mark.parametrize("odgovor", [
    "treba mi 0.15", "moze 0.15", "ima 0.15", "da uzmemo preset",
    "neka ostane 100", "reci mu 85", "znaci 85",
])
def test_odgovor_sa_dvosmislenom_rijecju_nije_pitanje(odgovor):
    """'treba mi 0.15' je odgovor. Da se cita kao pitanje, isti korak bi se vrtio."""
    assert R.je_pitanje(odgovor) is False


@pytest.mark.parametrize("pitanje", [
    "da li moze sperploca", "je li fokus vazan", "moze li se rezati akril",
    "ima li kerf u alatu", "treba li air assist",
])
def test_li_oblik_je_pitanje(pitanje):
    assert R.je_pitanje(pitanje) is True


def test_odgovor_treba_mi_se_upise_a_ne_ponovi_pitanje():
    s = R.korak(_novo(), "graviraj moju sliku 120 x 160 mm na sperplocu")
    s = R.korak(s, "sivi tonovi")
    assert s.cekam == "interval_mm"
    s2 = R.korak(s, "treba mi 0.15")
    assert s2.odgovori["interval_mm"] == 0.15
    assert s2.cekam != "interval_mm"


# ---------------- vektorski fajl nema piksele: ne pitaj rasterska pitanja ------
def _sa_fajlom(ime, tekst="graviraj ovo 120 x 160 mm na sperplocu"):
    s = R.novo()
    s.fajl = ime
    return R.korak(s, tekst)


@pytest.mark.parametrize("ime", ["gradina.svg", "crtez.dxf", "plan.pdf", "GRADINA.SVG"])
def test_vektorski_fajl_ne_pita_za_rezim_i_razmak(ime):
    s = _sa_fajlom(ime)
    assert R.je_vektor(s) is True
    polja = [p["polje"] for p in R.pitanja_naloga(s)]
    assert "rezim_slike" not in polja
    assert "interval_mm" not in polja


@pytest.mark.parametrize("ime", ["slika.png", "foto.jpg", "foto.jpeg"])
def test_rasterski_fajl_i_dalje_pita(ime):
    s = _sa_fajlom(ime)
    assert R.je_vektor(s) is False
    polja = [p["polje"] for p in R.pitanja_naloga(s)]
    assert "rezim_slike" in polja


def test_svg_dolazi_do_gotovo_bez_rasterskih_pitanja():
    s = _sa_fajlom("gradina.svg")
    assert s.cekam == "ENGRAVE.postavke", [p["polje"] for p in R.pitanja_naloga(s)]
    s = R.korak(s, "da")
    assert s.faza == "gotovo"
    assert R.provjera_prije_starta(s)[0] == []


def test_fajl_prezivi_serijalizaciju():
    s = _sa_fajlom("gradina.svg")
    assert R.iz_dicta(s.kao_dict()).fajl == "gradina.svg"


# ------------------- fajl je korak, ne greska na kraju ------------------------
def test_fajl_se_trazi_prvi():
    """Bez fajla nema rezultata, a vrsta fajla odlucuje koja pitanja imaju smisla."""
    s = R.novo()
    p = R.sljedece_pitanje(s)
    assert p["polje"] == "fajl"


def test_bez_fajla_je_blokada_pa_dugme_ne_moze():
    s = R.korak(R.novo(), "graviraj sliku 120 x 160 mm na sperplocu, sivi tonovi")
    s = R.korak(s, "0.15")
    s = R.korak(s, "da")
    blokade, _ = R.provjera_prije_starta(s)
    assert any("Fajl" in b for b in blokade), blokade
    assert s.faza != "gotovo"


def test_dok_se_ceka_fajl_tekst_ide_u_nalog():
    """Kucanje ne moze dodati fajl - tekst se cita kao nalog, ne kao odgovor."""
    s = R.korak(R.novo(), "graviraj sliku 120 x 160 mm")
    assert s.cekam == "fajl"
    s = R.korak(s, "na sperplocu, sivi tonovi")
    n = R.nalog(s)
    assert n.materijal == "sperploca_3mm"
    assert n.rezim_slike == "grayscale"
    assert s.cekam == "fajl"


def test_sa_fajlom_tok_dolazi_do_gotovo():
    s = R.novo()
    s.fajl = "motiv.png"
    s = R.korak(s, "graviraj sliku 120 x 160 mm na sperplocu, sivi tonovi")
    s = R.korak(s, "0.15")
    s = R.korak(s, "da")
    assert s.faza == "gotovo"
    assert R.provjera_prije_starta(s)[0] == []


# --------------------------- kamera kao nadzor sa mjesta ----------------------
def test_kamera_ne_radi_je_upozorenje():
    poruke = R.provjera_kamere({"radi": False, "greska": "ne otvara se indeks 0"})
    assert poruke and "NE RADI" in poruke[0]
    assert "ne otvara se indeks 0" in poruke[0]


def test_nekalibrisana_kamera_je_upozorenje():
    poruke = R.provjera_kamere({"radi": True, "kalibrisana": False})
    assert poruke and "NIJE kalibrisana" in poruke[0]
    assert "/camera" in poruke[0]


def test_zastarjela_kalibracija_je_upozorenje():
    stara = C.CAMERA_CALIBRATION_MAX_AGE_H + 10
    poruke = R.provjera_kamere({"radi": True, "kalibrisana": True,
                                "kreirana": "2026-11-13T07:54:00", "sati": stara})
    assert poruke and "pomjerao kameru" in poruke[0]
    assert "2026-11-13 07:54" in poruke[0]


def test_svjeza_kalibracija_ne_javlja_nista():
    assert R.provjera_kamere({"radi": True, "kalibrisana": True, "sati": 1.0}) == []


def test_bez_podatka_o_kameri_nema_izmisljanja():
    assert R.provjera_kamere(None) == []


def test_kamera_ulazi_u_provjeru_prije_starta():
    s = _novo()
    s = R.korak(s, "graviraj sliku 100 x 100 mm na sperplocu, sivi tonovi")
    s = R.korak(R.korak(s, "0.15"), "da")
    _, bez = R.provjera_prije_starta(s)
    _, sa = R.provjera_prije_starta(s, {"radi": True, "kalibrisana": False})
    assert len(sa) == len(bez) + 1
    assert any("NIJE kalibrisana" in u for u in sa)


def test_znanje_o_kameri():
    tekst = R.odgovor_na_pitanje(R.novo(), "kako radi kamera?")
    assert "/camera" in tekst
    assert "4 ugla" in tekst
    assert "ne znaci stajanje uz masinu" in tekst

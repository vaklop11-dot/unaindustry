"""Razgovor: asistent koji kroz pitanja i odgovore postavi posao i parametre masine.

Razlika prema ``asistent.py``: asistent procita jedan nalog i vrati listu onoga
sto fali. Razgovor vodi vise koraka - pamti sta je dogovoreno, pita jedno po
jedno, odgovara na operaterova pitanja o parametrima, i na kraju uradi provjeru
prije starta.

Radi bez mreze i bez modela. Nema API kljuca i nema poziva van ovog racunara.
Svaki odgovor dolazi iz ``config.py``, iz preseta materijala ili iz izracuna nad
onim sto je operater rekao. Sto se ne nalazi tamo, asistent kaze da ne zna -
ne izmislja i ne popunjava vrijednost umjesto covjeka.

Stanje je obican dict (``kao_dict`` / ``iz_dicta``), pa se nosi kroz formu i ne
treba sesija na serveru. Isti ulaz uvijek daje isti izlaz, pa se moze testirati.

Tok:
    s = novo()
    s = korak(s, "graviraj sliku 120x160 mm na sperplocu")
    s = korak(s, "koji razmak linija?")   # pitanje: odgovori, korak se ne mijenja
    s = korak(s, "0.12")                  # odgovor: upise i ide dalje
    provjera_prije_starta(s)              # blokade i upozorenja
"""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass, field

from fabagent.core import asistent as A
from fabagent.core import config as C

FAZE = ("nalog", "masina", "provjera", "gotovo")

# Prva rijec po kojoj se unos cita kao pitanje, a ne kao odgovor. Samo nedvosmislene:
# "treba", "moze", "ima" i "da" su i pocetak pitanja i pocetak odgovora ("treba mi
# 0.15"), pa bi odgovor bio procitan kao pitanje i isti korak bi se vrtio u krug.
PITALICE = ("sta", "shta", "sto", "koji", "koja", "koje", "koliko", "zasto", "cemu",
            "kako", "gdje", "objasni")

# Pitanja sa "li" su nedvosmislena gdje god se pojave u recenici.
PITALICE_LI = ("da li", "je li", "jel ", "moze li", "mozes li", "treba li", "ima li",
               "smijem li", "smije li", "hoce li", "vrijedi li", "znaci li")

# Pitanja o ovome alat ne moze odgovoriti ni za jedan materijal, pa se provjerava
# PRIJE tema: inace "koja je cijena sperploce" pogodi temu materijala (jer sadrzi
# "sperploc") i dobije odgovor o presetu, a pitanje je bilo o cijeni.
IZVAN_ALATA = ("cijen", "kost", "kupi", "nabav", "dobavljac", "prodaj", "pdv",
               "porez", "faktur", "profit", "zarad", "marz", "placanj", "racun za",
               "garancij", "servis", "rezervni dio", "gdje naci", "gdje da nadjem")

# Rijeci kojima operater prihvata ponudjenu vrijednost iz preseta.
PRIHVATAM = ("da", "ok", "okej", "dobro", "neka", "prihvatam", "prihvati", "vazi",
             "potvrdjujem", "tako", "idemo", "slazem se", "nek bude", "ostavi")

# Polja parametara masine koja se dogovaraju, po sloju.
MASINA_POLJA = (
    ("brzina_mm_s", "Brzina", "mm/s"),
    ("snaga_posto", "Snaga", "%"),
    ("prolaza", "Prolaza", ""),
)

SLOJ_IME = {"CUT": "rezanje", "ENGRAVE": "graviranje", "SCORE": "zarezivanje"}
PRESET_POLJE = {"brzina_mm_s": "speed_mm_s", "snaga_posto": "power_pct", "prolaza": "passes"}

# Rijeci po kojima se iz jednog odgovora cita koji je parametar zadan:
# "brzina 85 snaga 40" postavi oba odjednom, ostalo ide iz preseta.
MASINA_RIJECI = {"brzina_mm_s": ("brzin",), "snaga_posto": ("snag",),
                 "prolaza": ("prolaz", "pass")}

# Vektorski fajl nema piksele, pa rasterska pitanja za njega nemaju smisla.
VEKTORSKI_EXT = (".svg", ".dxf", ".pdf")
RASTERSKA_PITANJA = ("rezim_slike", "interval_mm")


def _bez_kvaka(text: str) -> str:
    razlozeno = unicodedata.normalize("NFKD", (text or "").lower())
    bez = "".join(z for z in razlozeno if not unicodedata.combining(z))
    return bez.replace("đ", "dj").replace("Đ", "dj")


def _broj(text: str) -> float | None:
    """Prvi broj iz teksta, ili None. Zapeta se cita kao decimalna tacka."""
    ocisceno = (text or "").replace(",", ".").strip()
    for dio in ocisceno.replace("=", " ").split():
        try:
            return float(dio)
        except ValueError:
            continue
    return None


def _g(v) -> str:
    """Broj bez nepotrebnih nula: 6000.0 -> 6000, 0.10 -> 0.1."""
    if v is None:
        return "NEMA PODATKA"
    t = f"{float(v):.4f}".rstrip("0").rstrip(".")
    return t or "0"


# --------------------------------------------------------------------- stanje
@dataclass
class Stanje:
    """Sve sto je dogovoreno. Polje koje nije dogovoreno ne postoji u dictu."""

    tekst_naloga: str = ""
    odgovori: dict = field(default_factory=dict)   # polje naloga -> vrijednost
    masina: dict = field(default_factory=dict)     # "SLOJ.polje" -> {"vrijednost", "iz"}
    faza: str = "nalog"
    cekam: str = ""                                 # polje pitanja na koje se ceka odgovor
    fajl: str = ""                                  # ime prilozenog fajla
    dnevnik: list = field(default_factory=list)     # [{"ko": ..., "tekst": ...}]

    def kaze(self, ko: str, tekst: str) -> None:
        if tekst:
            self.dnevnik.append({"ko": ko, "tekst": tekst})

    def kao_dict(self) -> dict:
        return {"tekst_naloga": self.tekst_naloga, "odgovori": self.odgovori,
                "masina": self.masina, "faza": self.faza, "cekam": self.cekam,
                "fajl": self.fajl, "dnevnik": self.dnevnik}


def iz_dicta(data: dict | None) -> Stanje:
    d = data or {}
    return Stanje(
        tekst_naloga=str(d.get("tekst_naloga") or ""),
        odgovori=dict(d.get("odgovori") or {}),
        masina=dict(d.get("masina") or {}),
        faza=str(d.get("faza") or "nalog"),
        cekam=str(d.get("cekam") or ""),
        fajl=str(d.get("fajl") or ""),
        dnevnik=list(d.get("dnevnik") or []),
    )


def je_vektor(stanje: Stanje) -> bool:
    """Je li prilozeni fajl vektorski. Prazno ime = jos se ne zna."""
    return stanje.fajl.lower().endswith(VEKTORSKI_EXT)


def nepotrebna_pitanja(stanje: Stanje) -> tuple[str, ...]:
    """Pitanja koja za ovaj fajl nemaju smisla, pa se ne postavljaju.

    Vektorski fajl (SVG, DXF, PDF) nema piksele: 'kako se gravira' i 'razmak
    linija' su rasterska pitanja i na njih nema sta da se odgovori.
    """
    return RASTERSKA_PITANJA if je_vektor(stanje) else ()


def novo() -> Stanje:
    s = Stanje()
    s.kaze("asistent",
           "Prilozi fajl i napisi sta radimo, svojim rijecima. Npr: graviraj sliku "
           "120 x 160 mm na sperplocu, sivi tonovi. Mozes me i pitati bilo sta o "
           "parametrima - pitaj 'koji razmak linija' ili 'sta je kerf'. "
           "Start na kraju pritiskas ti.")
    return s


def nalog(stanje: Stanje) -> A.Nalog:
    """Nalog iz teksta, pa preko njega ono sto je dogovoreno u razgovoru."""
    n = A.procitaj(stanje.tekst_naloga)
    for polje, vrijednost in stanje.odgovori.items():
        if hasattr(n, polje):
            n.zabiljezi(polje, vrijednost, "dogovoreno u razgovoru")
    return n


def _preset(n: A.Nalog):
    if not n.materijal:
        return None
    try:
        from fabagent.laser.presets import get_preset
        return get_preset(n.materijal)
    except Exception:
        return None


def slojevi(n: A.Nalog) -> tuple[str, ...]:
    """Koji sloj posao koristi. Prazno ako se vrsta posla jos ne zna."""
    if n.posao == "rez":
        return ("CUT",)
    if n.posao in ("fotografija", "gravura"):
        return ("ENGRAVE",)
    return ()


def _preset_vrijednost(n: A.Nalog, sloj: str, polje: str):
    p = _preset(n)
    if p is None:
        return None
    try:
        op = p.setting(sloj)
    except Exception:
        return None
    return getattr(op, PRESET_POLJE[polje], None)


# ---------------------------------------------------------------------- znanje
def _znanje_razmak(stanje: Stanje) -> str:
    n = nalog(stanje)
    nisko, visoko = C.LAYER_INTERVAL_RANGE_MM
    red = [f"Razmak linija je udaljenost izmedju dva prelaza glave pri graviranju. "
           f"DPI je 25.4 podijeljeno sa razmakom, pa manji razmak znaci vise DPI i "
           f"duze graviranje. Alat prima od {_g(nisko)} do {_g(visoko)} mm."]
    iz_preseta = _preset_vrijednost(n, "ENGRAVE", "brzina_mm_s")
    p = _preset(n)
    if p is not None and p.engrave.line_interval_mm is not None:
        red.append(f"Preset '{p.name}' nosi {_g(p.engrave.line_interval_mm)} mm "
                   f"(DPI {25.4 / p.engrave.line_interval_mm:.0f}).")
    granica = A.granica_intervala(n.sirina_mm, n.visina_mm)
    if granica:
        predlog = math.ceil(granica * 100.0) / 100.0
        red.append(f"Na tvojim mjerama {_g(n.sirina_mm)} x {_g(n.visina_mm)} mm granica "
                   f"zbog {C.RASTER_MAX_PIXELS:,} piksela je {_g(predlog)} mm "
                   f"(DPI {25.4 / predlog:.0f}). Manji razmak od toga alat odbija.")
    red.append("Tacnu vrijednost daje Interval test na tvom materijalu. Dok ga ne uradis, "
               "preset je polazna tacka, ne mjera.")
    del iz_preseta
    return " ".join(red)


def _znanje_brzina(stanje: Stanje) -> str:
    n = nalog(stanje)
    nisko, visoko = C.LAYER_SPEED_RANGE_MM_S
    red = [f"Brzina je u presetu u mm/s, a masina i LightBurn traze mm/min - to je "
           f"x 60. Alat prima od {_g(nisko)} do {_g(visoko)} mm/s "
           f"(GRBL $110/$111 je {_g(C.GRBL_MAX_FEED_MM_MIN)} mm/min)."]
    for sloj in slojevi(n) or ("ENGRAVE", "CUT"):
        v = _preset_vrijednost(n, sloj, "brzina_mm_s")
        if v is not None:
            red.append(f"Preset za {SLOJ_IME[sloj]}: {_g(v)} mm/s = {_g(v * 60)} mm/min.")
    red.append("Presporo pregori, prebrzo ne odnese dovoljno. Brzina i snaga se "
               "mjere zajedno, test-gridom.")
    return " ".join(red)


def _znanje_snaga(stanje: Stanje) -> str:
    n = nalog(stanje)
    nisko, visoko = C.LAYER_POWER_RANGE_PCT
    red = [f"Snaga je u procentima maksimuma, od {_g(nisko)} do {_g(visoko)} %. "
           f"GRBL je dobija kao S vrijednost 0 do {C.GRBL_S_MAX}, pa je npr. 30 % "
           f"jednako S{int(round(0.30 * C.GRBL_S_MAX))}."]
    for sloj in slojevi(n) or ("ENGRAVE", "CUT"):
        v = _preset_vrijednost(n, sloj, "snaga_posto")
        if v is not None:
            red.append(f"Preset za {SLOJ_IME[sloj]}: {_g(v)} % "
                       f"(S{int(round(float(v) / 100.0 * C.GRBL_S_MAX))}).")
    return " ".join(red)


def _znanje_prolazi(stanje: Stanje) -> str:
    n = nalog(stanje)
    nisko, visoko = C.LAYER_PASSES_RANGE
    red = [f"Prolaz je jedan prelaz glave po istoj liniji. Alat prima {nisko} do {visoko}."]
    for sloj in slojevi(n) or ("CUT",):
        v = _preset_vrijednost(n, sloj, "prolaza")
        if v is not None:
            red.append(f"Preset za {SLOJ_IME[sloj]}: {v}.")
    red.append("Dva slabija prolaza daju cistiji rez i manje pregorjelu ivicu od "
               "jednog jakog, ali traju duze.")
    return " ".join(red)


def _znanje_kerf(stanje: Stanje) -> str:
    n = nalog(stanje)
    po_materijalu = dict(getattr(C, "LASER_KERF_PO_MATERIJALU", {}) or {})
    opsti = getattr(C, "LASER_KERF_MM", None)
    moj = po_materijalu.get(n.materijal) if n.materijal else None
    if moj is not None:
        return (f"Kerf za '{n.materijal}' je {_g(moj)} mm, izmjeren i upisan u config.py. "
                f"Kerf je sirina koju laser odnese; spoj koji ga ne racuna je ili labav "
                f"ili ne ulazi.")
    if opsti is not None:
        return (f"Kerf u config.py je {_g(opsti)} mm, ali nije izmjeren za '{n.materijal or 'ovaj materijal'}' "
                f"posebno. Kerf zavisi od materijala, debljine i fokusa.")
    return ("Kerf je sirina koju laser odnese. U alatu ga NEMA PODATKA - nije izmjeren, "
            "i namjerno nema pretpostavljene vrijednosti. Mjeri se tako da izrezes "
            "poznatu mjeru i izmjeris je kaliperom: kerf = nominalno minus izmjereno. "
            "Fajl za mjerenje je 'izlaz/1_kalibracija_kerf.svg'. Mjeri se po materijalu "
            "i debljini - za sperplocu 3 mm nije isti kao za 5 mm. Dok ga nema, svaki "
            "spoj koji mora da ulazi je pogadjanje.")


def _znanje_granice(stanje: Stanje) -> str:
    x, y = C.LASER_WORK_AREA_MM
    potvrdjeno = bool(getattr(C, "LASER_BOUNDS_CONFIRMED", False))
    if potvrdjeno:
        return (f"Radna povrsina je {_g(x)} x {_g(y)} mm, potvrdjena na masini kroz $$ "
                f"($130 i $131).")
    return (f"U config.py radna povrsina je {_g(x)} x {_g(y)} mm, ali je DEKLARISANA, "
            f"ne izmjerena - dolazi sa stranice proizvodjaca. Potvrdjuje se na masini: "
            f"upisi $$ u konzolu na /machine, pa procitaj $130 (max put X) i $131 "
            f"(max put Y). Dok to ne uradimo, posao koji ide blizu ivice je rizik.")


def _znanje_frame(stanje: Stanje) -> str:
    return ("Frame vozi okvir posla sa S0 i M5 - snaga nula, laser ugasen. Sluzi da "
            "vidis gdje posao pada na materijalu prije nego se laser upali, i da glava "
            "ne udari u stezaljku. Ako okvir ne lezi gdje treba, pomjeri materijal, "
            "ne fajl. To je i test osa X i Y bez ikakvog rizika.")


def _znanje_stop(stanje: Stanje) -> str:
    return (f"Stop na stranici /machine salje najprije ! (zaustavi kretanje) pa "
            f"soft-reset 0x18. GRBL na soft-reset gasi laser i brise bafer, pa posao "
            f"ne moze nastaviti sam. Za dalje: Otkljucaj ($X) pa Home "
            f"({C.GRBL_HOME_CMD}). Pause je ! i ~ i taj se moze nastaviti.")


def _znanje_home(stanje: Stanje) -> str:
    return (f"Home je {C.GRBL_HOME_CMD}. G28 se ne koristi jer na GRBL-u nije homing "
            f"nego voznja na snimljenu poziciju. Ishodiste masine je "
            f"{C.MACHINE_ORIGIN}, a SVG i editor racunaju gore-lijevo, pa se Y okrece "
            f"pri pravljenju G-koda.")


def _znanje_rezim(stanje: Stanje) -> str:
    cmd = C.GRBL_LASER_MODE_CMD
    objasnjenje = ("M4 je dinamicka snaga: GRBL je smanjuje kad glava ubrzava i "
                   "povecava kad se krece ravnomjerno, pa uglovi ne pregore. Trazi "
                   "$32=1. M3 je konstantna snaga.")
    return f"Alat koristi {cmd}. {objasnjenje}"


def _znanje_fokus(stanje: Stanje) -> str:
    n = nalog(stanje)
    p = _preset(n)
    debljina = f"Preset nosi debljinu {_g(p.thickness_mm)} mm. " if p and p.thickness_mm else ""
    return (f"Fokus se namjesta na gornju povrsinu materijala, i mjeri se za svaku "
            f"debljinu posebno. {debljina}Pogresan fokus mijenja i sirinu reza i ton "
            f"gravure, pa tjera da mijenjas snagu bez potrebe. Debljinu izmjeri "
            f"kaliperom - sperploca od '3 mm' je rijetko 3.00.")


def _znanje_materijali(stanje: Stanje) -> str:
    from fabagent.laser.presets import PRESETS
    red = []
    for key in C.LASER_MATERIALS_ALL:
        p = PRESETS.get(key)
        if p is None:
            continue
        reze = "rezanje i graviranje" if p.cut is not None else "samo graviranje"
        red.append(f"{p.name} [{key}] - {reze}. {p.note}")
    return "Materijali u presetima:\n- " + "\n- ".join(red)


def _znanje_materijal_ovaj(stanje: Stanje) -> str:
    n = nalog(stanje)
    p = _preset(n)
    if p is None:
        return ("Materijal jos nije izabran, pa ne mogu reci nista o njemu. "
                "Pitaj 'koji materijali' za spisak.")
    reze = "moze se rezati i gravirati" if p.cut is not None else "samo graviranje, ne reze se"
    red = [f"{p.name} [{p.key}]: {reze}."]
    if p.thickness_mm:
        red.append(f"Debljina u presetu: {_g(p.thickness_mm)} mm (nominalno - izmjeri kaliperom).")
    red.append(p.note)
    if not p.verified:
        red.append("Brzina i snaga za ovaj materijal NISU mjerene na tvojoj masini.")
    return " ".join(red)


def _znanje_gdje_smo(stanje: Stanje) -> str:
    n = nalog(stanje)
    if stanje.faza == "gotovo":
        return "Sve je uneseno. Ostaje da ti pokrenes - Home, pa Frame, pa Start na /machine."
    imamo = [f"{k} = {v['vrijednost']}" for k, v in n.procitano.items()]
    tekst = "Do sada imam: " + (", ".join(imamo) if imamo else "nista.")
    p = sljedece_pitanje(stanje)
    return f"{tekst} Sljedece mi treba: {tekst_pitanja(p)}" if p else tekst


def _znanje_kamera(stanje: Stanje) -> str:
    return (f"Kamera na laptopu gleda radnu povrsinu i daje zivu sliku dok laser radi. "
            f"Zato nadzor ne znaci stajanje uz masinu - gledas sa svog mjesta, a Pause i "
            f"Stop su na stranici /machine. Slika je na /camera, a uzivo je i ovdje u "
            f"razgovoru dok posao postavljas. Kalibracija: otvoris /camera i klikne se "
            f"4 ugla radne povrsine, cime se slika veze za koordinate lasera "
            f"({_g(C.LASER_WORK_AREA_MM[0])} x {_g(C.LASER_WORK_AREA_MM[1])} mm). "
            f"Kalibracija starija od {_g(C.CAMERA_CALIBRATION_MAX_AGE_H)} h se racuna "
            f"zastarjelom jer se kamera ili laser mogu pomjeriti a da se ne primijeti.")


def _znanje_bez_mreze(stanje: Stanje) -> str:
    return ("Radim bez interneta. Nema API kljuca i nema poziva van ovog racunara. "
            "Nisam model koji pogadja - svaki broj koji ti kazem dolazi iz config.py, "
            "iz preseta materijala ili iz izracuna nad onim sto si mi rekao. Ako "
            "podatka nema, kazem NEMA PODATKA.")


ZNANJE = (
    ("razmak linija / DPI", ("razmak", "interval", "dpi", "gustina linij", "linija po"),
     _znanje_razmak),
    ("brzina", ("brzin", "mm/s", "mm/min", "feed", "koliko brzo"), _znanje_brzina),
    ("snaga", ("snag", "jacin", "procen", "s vrijednost", "watt", "vat"), _znanje_snaga),
    ("prolazi", ("prolaz", "pass", "ponavljanj"), _znanje_prolazi),
    ("kerf", ("kerf", "sirina reza", "debljina reza", "odnese", "zazor", "tolerancij"),
     _znanje_kerf),
    ("granice radne povrsine", ("granic", "radna povrsin", "radni prostor", "koliko velik",
                                "max dimenzij", "$130", "$131", "$$"), _znanje_granice),
    ("frame / prazan hod", ("frame", "prazan hod", "okvir posla", "bez lasera",
                            "ugasen laser", "proba bez"), _znanje_frame),
    ("stop i pauza", ("stop", "pauz", "prekid", "zaustav", "panic", "gasi"), _znanje_stop),
    ("home", ("home", "nula", "ishodist", "pocetna pozicij", "$h", "g28"), _znanje_home),
    ("rezim lasera M3/M4", ("m3", "m4", "dinamicka snag", "rezim lasera", "$32"), _znanje_rezim),
    ("fokus i debljina", ("fokus", "debljin", "kaliper", "visina stola"), _znanje_fokus),
    ("spisak materijala", ("koji materijal", "koja materijal", "spisak materijal",
                           "materijali", "sta sve mozes rezati", "sta mogu rezati"),
     _znanje_materijali),
    ("ovaj materijal", ("materijal", "sperploc", "mdf", "pleksi", "akril", "koz",
                        "aluminij", "celik", "termos"), _znanje_materijal_ovaj),
    ("kamera i nadzor", ("kamer", "nadzor", "zivu sliku", "ziva slika", "uzivo",
                         "vidim sto", "snimak", "kalibracij"), _znanje_kamera),
    ("gdje smo", ("gdje smo", "sta sad", "sta dalje", "sta imamo", "sta je ostalo",
                  "dokle smo", "rekapitulacij"), _znanje_gdje_smo),
    ("rad bez mreze", ("internet", "mrez", "api", "kljuc", "offline", "off grid",
                       "model", "oblak", "cloud"), _znanje_bez_mreze),
)


def tema(unos: str) -> str | None:
    """Naziv teme na koju se pitanje odnosi, ili None. Prva pogodjena odlucuje."""
    t = _bez_kvaka(unos)
    if any(r in t for r in IZVAN_ALATA):
        return None
    for naziv, rijeci, _ in ZNANJE:
        if any(r in t for r in rijeci):
            return naziv
    return None


def _ne_znam() -> str:
    teme = ", ".join(naziv for naziv, _, _ in ZNANJE)
    return ("Na to nemam podatak u alatu, pa ne odgovaram - ne izmisljam. "
            f"Mogu o ovome: {teme}. Ako ti treba broj kojeg nema, on se mjeri na "
            "masini i onda ga upisemo.")


def odgovor_na_pitanje(stanje: Stanje, unos: str) -> str:
    """Odgovor iz znanja. Ako teme nema, kaze da ne zna - ne izmislja."""
    naziv = tema(unos)
    if naziv is None:
        return _ne_znam()
    for ime, _, gradi in ZNANJE:
        if ime == naziv:
            return gradi(stanje)
    return _ne_znam()


def je_pitanje(unos: str) -> bool:
    """Je li unos pitanje, a ne odgovor. Deterministicki: ? , 'li' oblik, ili prva rijec."""
    t = _bez_kvaka(unos).strip()
    if not t:
        return False
    if "?" in t:
        return True
    if any(oblik in f"{t} " for oblik in PITALICE_LI):
        return True
    return t.split()[0].strip(".,:;!") in PITALICE


def je_prihvatanje(unos: str) -> bool:
    t = _bez_kvaka(unos).strip().strip(".!")
    return t in PRIHVATAM


# -------------------------------------------------------------------- pitanja
def _procitaj_postavke(unos: str) -> dict:
    """{'brzina_mm_s': 85.0, ...} iz 'brzina 85 snaga 40'. Prazno ako nista nije imenovano."""
    t = _bez_kvaka(unos).replace(",", ".")
    nadjeno: dict[str, float] = {}
    for polje, rijeci in MASINA_RIJECI.items():
        for rijec in rijeci:
            m = re.search(rf"{rijec}\w*\s*[:=]?\s*(\d+(?:\.\d+)?)", t)
            if m:
                nadjeno[polje] = float(m.group(1))
                break
    return nadjeno


def _postavke_preseta(n: A.Nalog, sloj: str) -> dict:
    return {polje: _preset_vrijednost(n, sloj, polje) for polje, _, _ in MASINA_POLJA}


def _pitanje_masine(n: A.Nalog, stanje: Stanje) -> dict | None:
    """Jedno pitanje po sloju za sve tri postavke odjednom.

    Tri odvojena pitanja su za operatera tri prekida umjesto jednog; preset ionako
    nosi sve tri, pa se nude zajedno i jednim 'da' se uzimaju sve.
    """
    for sloj in slojevi(n):
        fale = [polje for polje, _, _ in MASINA_POLJA if f"{sloj}.{polje}" not in stanje.masina]
        if not fale:
            continue
        iz_preseta = _postavke_preseta(n, sloj)
        p = _preset(n)
        provjereno = bool(p.verified) if p else False
        imena = {polje: (ime, jed) for polje, ime, jed in MASINA_POLJA}

        ponuda = []
        for polje in fale:
            vrijednost = iz_preseta.get(polje)
            ime, jed = imena[polje]
            if vrijednost is None:
                continue
            if polje == "brzina_mm_s":
                ponuda.append(f"brzina {_g(vrijednost)} mm/s ({_g(float(vrijednost) * 60)} mm/min)")
            elif polje == "prolaza":
                ponuda.append(f"{int(vrijednost)} prolaz(a)")
            else:
                ponuda.append(f"snaga {_g(vrijednost)} {jed}".strip())

        if ponuda:
            oznaka = "" if provjereno else " Nije mjereno na tvojoj masini."
            pitanje = (f"Postavke za {SLOJ_IME[sloj]}. Preset nudi: {', '.join(ponuda)}."
                       f"{oznaka} Napisi 'da' da uzmemo sve, ili npr. 'brzina 85 snaga 40' "
                       f"da promijenis sto hoces - ostalo ide iz preseta.")
        else:
            trazeno = ", ".join(imena[polje][0].lower() for polje in fale)
            pitanje = (f"Postavke za {SLOJ_IME[sloj]} - preset za ovaj materijal ih nema. "
                       f"Upisi: {trazeno}, npr. 'brzina 85 snaga 40 prolaza 1'.")
        return {"polje": f"{sloj}.postavke", "pitanje": pitanje, "jedinica": "",
                "preset": iz_preseta, "provjereno": provjereno, "sloj": sloj, "fale": fale}
    return None


def _kljucevi_izbora(i: dict) -> list[str]:
    """Sto sve operater moze napisati za jedan ponudjeni odgovor."""
    v = _bez_kvaka(str(i["vrijednost"]))
    oznaka = _bez_kvaka(str(i.get("oznaka") or ""))
    kljucevi = [v, v.replace("_", " ")]
    if oznaka:
        kljucevi.append(oznaka)
        kljucevi.append(oznaka.split(" - ")[0].strip())    # "Sivi tonovi - snaga..." -> "sivi tonovi"
    return [k for k in dict.fromkeys(kljucevi) if k]


def _kratka_oznaka(i: dict) -> str:
    oznaka = str(i.get("oznaka") or "").split(" - ")[0].strip()
    v = str(i["vrijednost"])
    return f"{v} ({oznaka})" if oznaka and _bez_kvaka(oznaka) != _bez_kvaka(v) else v


def tekst_pitanja(pitanje: dict | None) -> str:
    """Pitanje kako se izgovara u razgovoru - sa ponudjenim odgovorima.

    U razgovoru covjek ne vidi padajucu listu, pa pitanje bez nabrojanih odgovora
    nema smisla: ne zna sta mu je dozvoljeno da napise.
    """
    if not pitanje:
        return ""
    tekst = str(pitanje["pitanje"])
    izbor = pitanje.get("izbor")
    if izbor:
        tekst += " Izaberi: " + ", ".join(_kratka_oznaka(i) for i in izbor) + "."
    elif pitanje.get("jedinica") and "." not in pitanje["polje"]:
        granice = []
        if pitanje.get("min") is not None:
            granice.append(f"najmanje {_g(pitanje['min'])}")
        if pitanje.get("max") is not None:
            granice.append(f"najvise {_g(pitanje['max'])}")
        if granice:
            tekst += f" ({', '.join(granice)} {pitanje['jedinica']})"
    return tekst


def pitanja_naloga(stanje: Stanje) -> list[dict]:
    """Pitanja o nalogu, bez onih koja za prilozeni fajl nemaju smisla."""
    preskoci = nepotrebna_pitanja(stanje)
    return [p for p in A.pitanja(nalog(stanje)) if p["polje"] not in preskoci]


def _pitanje_fajla(stanje: Stanje) -> dict | None:
    """Fajl se trazi PRVI: bez njega nema sta da se napravi, a vrsta fajla
    odlucuje i koja pitanja uopste imaju smisla (vektor nema piksele).

    Ranije se fajl nije trazio nigdje, pa bi operater odgovorio na sva pitanja,
    pritisnuo 'Napravi posao' i dobio gresku 'obavezni parametri nedostaju: fajl'.
    """
    if stanje.fajl:
        return None
    return {"polje": "fajl", "jedinica": "",
            "pitanje": "Prilozi fajl dugmetom za izbor fajla ispod (SVG, DXF, PDF, PNG, JPG). "
                       "Bez njega nema sta da se napravi. Nalog mozes pisati i sada - "
                       "citam ga dok fajl ne stigne."}


def sljedece_pitanje(stanje: Stanje) -> dict | None:
    """Pitanje na koje se ceka odgovor, po trenutnoj fazi. None kad nema vise."""
    p = _pitanje_fajla(stanje)
    if p is not None:
        return p
    preostalo = pitanja_naloga(stanje)
    if preostalo:
        return preostalo[0]
    return _pitanje_masine(nalog(stanje), stanje)


def _primi_odgovor(stanje: Stanje, pitanje: dict, unos: str) -> str:
    """Upise odgovor u stanje. Vrati potvrdu, ili objasnjenje zasto nije prihvaceno."""
    polje = pitanje["polje"]

    if polje.endswith(".postavke"):                    # sve postavke jednog sloja
        return _primi_postavke(stanje, pitanje, unos)

    if pitanje.get("izbor"):                           # izbor iz liste
        t = _bez_kvaka(unos).strip()
        for i in pitanje["izbor"]:
            for kljuc in _kljucevi_izbora(i):
                if t == kljuc or kljuc in t or t in kljuc:
                    stanje.odgovori[polje] = str(i["vrijednost"])
                    return f"Upisano: {i.get('oznaka') or i['vrijednost']}"
        dozvoljeno = ", ".join(_kratka_oznaka(i) for i in pitanje["izbor"])
        return f"'{unos}' ne prepoznajem. Dozvoljeno je: {dozvoljeno}."

    broj = _broj(unos)                                 # broj sa jedinicom
    if broj is None:
        return "Treba mi broj."
    if pitanje.get("min") is not None and broj < pitanje["min"]:
        return f"Najmanje je {_g(pitanje['min'])} {pitanje.get('jedinica', '')}".strip() + "."
    if pitanje.get("max") is not None and broj > pitanje["max"]:
        return f"Najvise je {_g(pitanje['max'])} {pitanje.get('jedinica', '')}".strip() + "."
    stanje.odgovori[polje] = broj
    return f"Upisano: {_g(broj)} {pitanje.get('jedinica', '')}".strip()


def _primi_postavke(stanje: Stanje, pitanje: dict, unos: str) -> str:
    """Jedan odgovor postavi sve tri postavke sloja: imenovano od operatera, ostalo iz preseta."""
    sloj = pitanje["sloj"]
    fale = list(pitanje["fale"])
    iz_preseta = pitanje["preset"] or {}
    zadano = _procitaj_postavke(unos)

    if not zadano and not je_prihvatanje(unos):
        return ("Treba mi 'da' da uzmemo preset, ili imenovano: "
                "npr. 'brzina 85', 'snaga 40', 'prolaza 2'. Mogu i sve odjednom.")

    for polje, broj in zadano.items():                 # prvo provjeri sve, pa upisi
        greska = _van_opsega(f"{sloj}.{polje}", broj)
        if greska:
            ime = next(i for p, i, _ in MASINA_POLJA if p == polje)
            return f"{ime}: {greska}"

    bez_preseta = [p for p in fale if p not in zadano and iz_preseta.get(p) is None]
    if bez_preseta:
        trazeno = ", ".join(next(i for p, i, _ in MASINA_POLJA if p == q).lower()
                            for q in bez_preseta)
        return (f"Preset nema: {trazeno}. Upisi i to, npr. "
                f"'{' '.join(q.split('_')[0] + ' 0' for q in bez_preseta)}'.")

    upisano = []
    for polje in fale:
        if polje in zadano:
            stanje.masina[f"{sloj}.{polje}"] = {"vrijednost": zadano[polje], "iz": "operater"}
            upisano.append(f"{polje.split('_')[0]} {_g(zadano[polje])} (tvoje)")
        else:
            stanje.masina[f"{sloj}.{polje}"] = {"vrijednost": iz_preseta[polje], "iz": "preset"}
            upisano.append(f"{polje.split('_')[0]} {_g(iz_preseta[polje])} (preset)")
    return f"Upisano za {SLOJ_IME[sloj]}: " + ", ".join(upisano) + "."


def _van_opsega(kljuc: str, broj: float) -> str | None:
    polje = kljuc.split(".", 1)[1]
    opsezi = {"brzina_mm_s": (C.LAYER_SPEED_RANGE_MM_S, "mm/s"),
              "snaga_posto": (C.LAYER_POWER_RANGE_PCT, "%"),
              "prolaza": (C.LAYER_PASSES_RANGE, "")}
    if polje not in opsezi:
        return None
    (nisko, visoko), jedinica = opsezi[polje]
    if not (nisko <= broj <= visoko):
        return (f"Van opsega. Dozvoljeno je od {_g(nisko)} do {_g(visoko)} "
                f"{jedinica}".strip() + ".")
    if polje == "prolaza" and broj != int(broj):
        return "Prolaza mora biti cijeli broj."
    return None


def _faza_dalje(stanje: Stanje) -> None:
    """Postavi fazu po tome sta je jos otvoreno."""
    n = nalog(stanje)
    if pitanja_naloga(stanje):
        stanje.faza = "nalog"
    elif _pitanje_masine(n, stanje) is not None:
        stanje.faza = "masina"
    else:
        blokade, _ = provjera_prije_starta(stanje)
        stanje.faza = "provjera" if blokade else "gotovo"


# ----------------------------------------------------------------------- korak
def korak(stanje: Stanje, unos: str) -> Stanje:
    """Jedan potez razgovora. Vrati novo stanje; ulaz se ne mijenja na mjestu."""
    s = iz_dicta(stanje.kao_dict())
    tekst = (unos or "").strip()
    if not tekst:
        return s
    s.kaze("operater", tekst)

    if je_pitanje(tekst):
        s.kaze("asistent", odgovor_na_pitanje(s, tekst))
        p = sljedece_pitanje(s)
        if p:
            s.cekam = p["polje"]
            s.kaze("asistent", f"Da nastavimo: {tekst_pitanja(p)}")
        return s

    p = sljedece_pitanje(s)
    if p is None:                     # nista se ne ceka: tekst je dopuna naloga
        s.tekst_naloga = f"{s.tekst_naloga} {tekst}".strip()
        _faza_dalje(s)
        novo_p = sljedece_pitanje(s)
        s.cekam = novo_p["polje"] if novo_p else ""
        s.kaze("asistent", f"Zapisano. {tekst_pitanja(novo_p)}" if novo_p
               else "Zapisano. Sve je uneseno.")
        return s

    # Fajl se ne upisuje kucanjem - dok se ceka fajl, tekst je nalog.
    # Isto vrijedi za prvi unos, kad se jos nista konkretno ne ceka.
    if p["polje"] == "fajl" or (not s.cekam and not s.tekst_naloga):
        s.tekst_naloga = f"{s.tekst_naloga} {tekst}".strip()
        procitano = nalog(s).procitano
        if procitano:
            spisak = ", ".join(f"{k} = {v['vrijednost']}" for k, v in procitano.items())
            s.kaze("asistent", f"Procitao sam: {spisak}.")
        else:
            s.kaze("asistent", "Iz toga nisam procitao ni jedan parametar, pa pitam sve.")
    else:
        s.kaze("asistent", _primi_odgovor(s, p, tekst))

    _faza_dalje(s)
    sljedece = sljedece_pitanje(s)
    s.cekam = sljedece["polje"] if sljedece else ""
    if sljedece:
        s.kaze("asistent", tekst_pitanja(sljedece))
    else:
        blokade, upozorenja = provjera_prije_starta(s)
        if blokade:
            s.kaze("asistent", "Ovo zaustavlja posao: " + " ".join(blokade))
        else:
            s.kaze("asistent", "Sve je uneseno. Provjera je ispod. Start pritiskas ti.")
        for u in upozorenja:
            s.kaze("asistent", u)
    return s


# --------------------------------------------------------- provjera i parametri
def provjera_kamere(kamera: dict | None) -> list[str]:
    """Sto o nadzoru kamerom mora da se zna prije nego se laser upali.

    Nadzor ne znaci stajanje uz masinu: kamera uperena u sto i otvorena u pregledu
    je ono sto cini nadzor sa mjesta mogucim. Zato se njeno stanje provjerava
    zajedno sa ostalim, a ne usput.
    """
    if kamera is None:
        return []
    if not kamera.get("radi", True):
        greska = kamera.get("greska") or "kamera se ne otvara"
        return [f"Kamera NE RADI ({greska}). Bez nje nemas sliku stola sa svog mjesta - "
                f"ili je popravi, ili budi uz masinu dok radi."]
    if not kamera.get("kalibrisana"):
        return ["Kamera radi ali NIJE kalibrisana, pa slika nije vezana za koordinate "
                "lasera. Otvori /camera i klikni 4 ugla radne povrsine."]
    sati = kamera.get("sati")
    if sati is not None and sati > C.CAMERA_CALIBRATION_MAX_AGE_H:
        kada = str(kamera.get("kreirana") or "")[:16].replace("T", " ")
        return [f"Kalibracija kamere je od {kada} ({sati:.0f} h). Ako si pomjerao kameru "
                f"ili laser, uradi je ponovo - inace slika pokazuje pogresno mjesto."]
    return []


def provjera_prije_starta(stanje: Stanje, kamera: dict | None = None) -> tuple[list[str], list[str]]:
    """(blokade, upozorenja). Blokada zaustavlja posao; upozorenje mora da se zna."""
    n = nalog(stanje)
    blokade: list[str] = []
    upozorenja: list[str] = list(provjera_kamere(kamera))

    if not stanje.fajl:
        blokade.append("Fajl nije prilozen.")
    if n.posao is None:
        blokade.append("Vrsta posla nije odredjena.")
    if n.materijal is None:
        blokade.append("Materijal nije izabran.")
    if n.sirina_mm is None:
        blokade.append("Sirina na materijalu nije zadana.")

    radna_s, radna_v = C.LASER_WORK_AREA_MM
    if n.sirina_mm and n.sirina_mm > radna_s:
        blokade.append(f"Sirina {_g(n.sirina_mm)} mm je veca od radne povrsine {_g(radna_s)} mm.")
    if n.visina_mm and n.visina_mm > radna_v:
        blokade.append(f"Visina {_g(n.visina_mm)} mm je veca od radne povrsine {_g(radna_v)} mm.")

    p = _preset(n)
    if n.posao == "rez" and p is not None and p.cut is None:
        blokade.append(f"Materijal '{p.name}' se diodnim laserom ne reze - po presetu samo gravura.")

    for sloj in slojevi(n):
        for polje, ime, _ in MASINA_POLJA:
            if f"{sloj}.{polje}" not in stanje.masina:
                blokade.append(f"{ime} za {SLOJ_IME[sloj]} nije dogovorena.")

    if p is not None and not p.verified:
        upozorenja.append(
            f"Brzina i snaga za '{p.name}' NISU mjerene na tvojoj masini - to su polazne "
            f"vrijednosti. Uradi test-grid na otpadu prije serije.")
    if p is not None and p.note:
        upozorenja.append(f"Materijal: {p.note}")

    if not bool(getattr(C, "LASER_BOUNDS_CONFIRMED", False)):
        upozorenja.append(
            f"Granice radne povrsine ({_g(radna_s)} x {_g(radna_v)} mm) nisu potvrdjene na "
            f"masini. Upisi $$ na /machine i procitaj $130 i $131.")

    kerf_po = dict(getattr(C, "LASER_KERF_PO_MATERIJALU", {}) or {})
    if n.materijal and n.materijal not in kerf_po and getattr(C, "LASER_KERF_MM", None) is None:
        upozorenja.append(
            "Kerf nije izmjeren. Za gravuru ne smeta; za rez i za spojeve koji moraju "
            "ulaziti jedan u drugi smeta. Mjeri se sa 'izlaz/1_kalibracija_kerf.svg'.")

    granica = A.granica_intervala(n.sirina_mm, n.visina_mm)
    if granica and n.interval_mm and n.interval_mm < granica:
        predlog = math.ceil(granica * 100.0) / 100.0
        blokade.append(
            f"Razmak {_g(n.interval_mm)} mm na {_g(n.sirina_mm)} x {_g(n.visina_mm)} mm "
            f"prelazi granicu od {C.RASTER_MAX_PIXELS:,} piksela. Najmanji koji prolazi "
            f"je {_g(predlog)} mm.")

    upozorenja.append(
        "Prvo Home, pa Frame. Frame vozi okvir sa ugasenim laserom - to je provjera osa "
        "X i Y bez rizika. Start tek kad okvir lezi tacno.")
    return blokade, upozorenja


def parametri_masine(stanje: Stanje) -> list[dict]:
    """Dogovoreni parametri, za tabelu. Prazno ako nista nije dogovoreno."""
    n = nalog(stanje)
    redovi = []
    for sloj in slojevi(n):
        for polje, ime, jedinica in MASINA_POLJA:
            zapis = stanje.masina.get(f"{sloj}.{polje}")
            if zapis is None:
                continue
            vrijednost = zapis["vrijednost"]
            prikaz = _g(vrijednost) if polje != "prolaza" else str(int(vrijednost))
            if polje == "brzina_mm_s":
                prikaz = f"{prikaz} mm/s = {_g(float(vrijednost) * 60)} mm/min"
            elif jedinica:
                prikaz = f"{prikaz} {jedinica}"
            redovi.append({"sloj": sloj, "operacija": SLOJ_IME[sloj], "parametar": ime,
                           "vrijednost": prikaz, "iz": zapis["iz"]})
    return redovi


def u_parametre(stanje: Stanje) -> dict:
    """Polja za web obradu: ono sto asistent daje, plus dogovoreni razmak linija."""
    n = nalog(stanje)
    vrijednosti = A.u_parametre(n)
    zapis = stanje.masina.get("ENGRAVE.brzina_mm_s")
    if zapis is not None:
        vrijednosti["brzina_mm_s"] = str(zapis["vrijednost"])
    zapis = stanje.masina.get("ENGRAVE.snaga_posto")
    if zapis is not None:
        vrijednosti["snaga_posto"] = str(zapis["vrijednost"])
    return vrijednosti

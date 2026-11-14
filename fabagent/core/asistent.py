"""Asistent: iz naloga napisanog obicnim rijecima pravi parametre posla.

Pravilo je isto kao svuda u FabAgent-u: sto se ne moze procitati iz naloga, NE
pogadja se nego se PITA. Asistent nikad ne popunjava vrijednost umjesto covjeka;
on samo prepoznaje ono sto je covjek vec napisao i trazi ostatak.

Radi bez mreze i bez modela: prepoznavanje je deterministicko (brojevi, mjerne
jedinice, imena materijala iz config.py, kljucne rijeci). Isti ulaz uvijek daje
isti izlaz, pa se moze testirati.

Tok:
    procitaj(tekst)        -> Nalog (sto je prepoznato + odakle)
    pitanja(nalog)         -> lista onoga sto jos fali, sa ponudjenim odgovorima
    u_parametre(nalog)     -> dict spreman za web obradu (ista polja kao forma)
"""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass, field

from fabagent.core import config as C

# Rijeci po kojima se prepoznaje vrsta posla. Prva pogodjena odlucuje.
# Korijeni rijeci, da padezi ne smetaju: "sliku", "slike", "slika" -> "slik".
# Redoslijed odlucuje: "graviraj moju sliku" je fotografija, ne gravura crteza.
POSLOVI = (
    ("fotografija", ("foto", "slik", "portret", "lice")),
    ("gravura", ("gravur", "graviraj", "urezi", "natpis", "tekst")),
    ("rez", ("rezi", "rez ", "rezanje", "isjeci", "kontura")),
)

# Rijeci koje ukljucuju uklanjanje pozadine.
POZADINA_RIJECI = ("bez pozadine", "ukloni pozadinu", "makni pozadinu", "izreži pozadinu",
                   "izrezi pozadinu", "samo mene", "samo lik", "bez zida", "bez kauca")

MODOVI = {
    "grayscale": ("sivi", "siva skala", "grayscale", "mekano", "tonovi", "foto"),
    "dither": ("dither", "tackice", "tacke", "raster", "jarvis", "stucki"),
    "threshold": ("threshold", "prag", "crno bijelo", "crno-bijelo"),
    "vectorize": ("obris", "obrisi", "vektor", "linija", "linije", "vectorize"),
}


def _bez_kvaka(text: str) -> str:
    razlozeno = unicodedata.normalize("NFKD", text.lower())
    bez = "".join(z for z in razlozeno if not unicodedata.combining(z))
    return bez.replace("đ", "dj").replace("Đ", "dj")


@dataclass
class Nalog:
    """Sto je procitano iz teksta. Polje koje je None nije procitano."""

    tekst: str = ""
    posao: str | None = None
    materijal: str | None = None
    sirina_mm: float | None = None
    visina_mm: float | None = None
    interval_mm: float | None = None
    rezim_slike: str | None = None
    ukloni_pozadinu: bool = False
    komada: int | None = None
    procitano: dict = field(default_factory=dict)

    def zabiljezi(self, polje: str, vrijednost, izvor: str) -> None:
        setattr(self, polje, vrijednost)
        self.procitano[polje] = {"vrijednost": vrijednost, "iz": izvor}


def _korijen(rijec: str) -> str:
    """Rijec bez zavrsnih samoglasnika, da padezi ne smetaju (sperploca -> sperploc)."""
    osnov = rijec
    while len(osnov) > 4 and osnov[-1] in "aeiou":
        osnov = osnov[:-1]
    return osnov


def _materijal_mape() -> dict[str, str]:
    """Korijen imena materijala -> kljuc. Imena dolaze iz config.py, ne rucno."""
    mapa: dict[str, str] = {}
    for key in C.LASER_MATERIALS_ALL:
        osnov = _bez_kvaka(key).replace("_", " ")
        mapa[osnov] = key
        prva = osnov.split()[0]
        mapa.setdefault(prva, key)
        mapa.setdefault(_korijen(prva), key)
    return mapa


def _dimenzije(tekst: str) -> list[float]:
    """Brojevi uz 'mm' ili oblik 120x160. Bez jedinice se ne uzima nista."""
    t = tekst.replace(",", ".")
    par = re.search(r"(\d+(?:\.\d+)?)\s*[x×]\s*(\d+(?:\.\d+)?)\s*(mm)?", t, re.I)
    if par:
        return [float(par.group(1)), float(par.group(2))]
    jedan = re.findall(r"(\d+(?:\.\d+)?)\s*mm\b", t, re.I)
    return [float(v) for v in jedan]


def procitaj(tekst: str) -> Nalog:
    """Procitaj nalog. Nista se ne popunjava - samo ono sto u tekstu zaista pise."""
    nalog = Nalog(tekst=tekst or "")
    t = _bez_kvaka(nalog.tekst)
    if not t.strip():
        return nalog

    for ime, rijeci in POSLOVI:
        if any(r in t for r in rijeci):
            nalog.zabiljezi("posao", ime, "rijec u nalogu")
            break

    mapa = _materijal_mape()
    for rijec, key in sorted(mapa.items(), key=lambda kv: -len(kv[0])):
        if rijec in t:
            nalog.zabiljezi("materijal", key, f"'{rijec}' u nalogu")
            break

    mjere = _dimenzije(nalog.tekst)
    if len(mjere) >= 2:
        nalog.zabiljezi("sirina_mm", mjere[0], "dimenzije u nalogu")
        nalog.zabiljezi("visina_mm", mjere[1], "dimenzije u nalogu")
    elif len(mjere) == 1:
        nalog.zabiljezi("sirina_mm", mjere[0], "sirina u nalogu")

    interval = re.search(r"interval\D{0,12}?(\d+(?:[.,]\d+)?)", t)
    if interval:
        nalog.zabiljezi("interval_mm", float(interval.group(1).replace(",", ".")),
                        "interval u nalogu")
    else:
        dpi = re.search(r"(\d{2,4})\s*dpi", t)
        if dpi and int(dpi.group(1)) > 0:
            nalog.zabiljezi("interval_mm", round(25.4 / int(dpi.group(1)), 4),
                            f"DPI {dpi.group(1)} u nalogu")

    for mod, rijeci in MODOVI.items():
        if any(r in t for r in rijeci):
            nalog.zabiljezi("rezim_slike", mod, "rijec u nalogu")
            break

    if any(_bez_kvaka(r) in t for r in POZADINA_RIJECI):
        nalog.zabiljezi("ukloni_pozadinu", True, "rijec u nalogu")

    # Kolicina: "5 kom", "5 komada", "5 puta", ili "5x" - ali NE "120 x 160 mm",
    # gdje je x izmedju dva broja i oznacava dimenziju, a ne broj komada.
    komada = re.search(r"(\d+)\s*(?:kom\b|komad|komada|puta)", t)
    if not komada:
        komada = re.search(r"(\d+)\s*[x×](?!\s*\d)", t)
    if komada:
        nalog.zabiljezi("komada", int(komada.group(1)), "kolicina u nalogu")

    return nalog


# Polja koja covjek moze odgovoriti na pitanje. Forma ih mora vracati natrag
# izmedju koraka, inace se nalog svaki put cita ispocetka i pitanja se vrte u
# krug: odgovoris A pa pita B, odgovoris B pa opet pita A.
POLJA_ODGOVORA = ("posao", "materijal", "sirina_mm", "visina_mm", "rezim_slike",
                  "interval_mm", "komada")
BROJNA_POLJA = {"sirina_mm": float, "visina_mm": float, "interval_mm": float, "komada": int}


def primijeni_odgovore(nalog: Nalog, odgovori: dict) -> dict:
    """Upise odgovore u nalog i vrati ono sto je primijenjeno, kao tekst.

    Vraceni dict ide u formu kao skrivena polja, pa odgovor dat u jednom koraku
    vrijedi i u sljedecem. Prazan ili neispravan odgovor se preskace - ne pogadja se.
    """
    primijenjeno: dict[str, str] = {}
    for polje in POLJA_ODGOVORA:
        sirovo = str(odgovori.get(polje) or "").strip()
        if not sirovo:
            continue
        pretvori = BROJNA_POLJA.get(polje)
        if pretvori is not None:
            try:
                vrijednost = pretvori(sirovo.replace(",", "."))
            except ValueError:
                continue
        else:
            vrijednost = sirovo
        nalog.zabiljezi(polje, vrijednost, "odgovor na pitanje")
        primijenjeno[polje] = sirovo
    return primijenjeno


def _izbor_materijala() -> list[dict]:
    from fabagent.web import labels as L
    return [{"vrijednost": k, "oznaka": L.choice_label("materijal", k)}
            for k in C.LASER_MATERIALS_ALL]


def pitanja(nalog: Nalog) -> list[dict]:
    """Sto jos fali da se posao moze napraviti. Prazna lista znaci da je spremno."""
    lista: list[dict] = []

    if nalog.posao is None:
        lista.append({
            "polje": "posao", "pitanje": "Sta se radi?",
            "izbor": [{"vrijednost": "fotografija", "oznaka": "Gravura fotografije"},
                      {"vrijednost": "gravura", "oznaka": "Gravura crteza ili teksta"},
                      {"vrijednost": "rez", "oznaka": "Rezanje po konturi"}],
        })

    if nalog.materijal is None:
        lista.append({"polje": "materijal", "pitanje": "Na kom materijalu?",
                      "izbor": _izbor_materijala()})

    if nalog.sirina_mm is None:
        lista.append({
            "polje": "sirina_mm",
            "pitanje": f"Kolika je sirina na materijalu, u mm? "
                       f"(radna povrsina je {C.LASER_WORK_AREA_MM[0]:g} x "
                       f"{C.LASER_WORK_AREA_MM[1]:g} mm)",
            "jedinica": "mm", "min": C.IMAGE_WIDTH_MIN_MM, "max": C.LASER_WORK_AREA_MM[0],
        })

    if nalog.posao in ("fotografija", "gravura") and nalog.rezim_slike is None:
        lista.append({
            "polje": "rezim_slike", "pitanje": "Kako se gravira?",
            "izbor": [{"vrijednost": "grayscale", "oznaka": "Sivi tonovi - snaga po pikselu"},
                      {"vrijednost": "dither", "oznaka": "Dither - tackice"},
                      {"vrijednost": "threshold", "oznaka": "Threshold - crno-bijelo"},
                      {"vrijednost": "vectorize", "oznaka": "Obrisi - linije"}],
        })

    if nalog.posao == "fotografija" and nalog.interval_mm is None:
        lista.append({
            "polje": "interval_mm",
            "pitanje": "Koji razmak linija, u mm? Dobija se Interval testom na materijalu; "
                       "DPI je 25.4 podijeljeno sa njim.",
            "jedinica": "mm",
        })

    return lista


def granica_intervala(sirina_mm: float | None, visina_mm: float | None) -> float | None:
    """Najmanji razmak linija koji jos stane u C.RASTER_MAX_PIXELS. None ako fale mjere."""
    if not sirina_mm or not visina_mm:
        return None
    povrsina = float(sirina_mm) * float(visina_mm)
    if povrsina <= 0:
        return None
    return round((povrsina / float(C.RASTER_MAX_PIXELS)) ** 0.5, 4)


def primjedbe(nalog: Nalog) -> list[str]:
    """Korekcije koje asistent predlaze. Ne mijenja nista sam - samo javlja."""
    out: list[str] = []
    sirina, visina = nalog.sirina_mm, nalog.visina_mm
    radna_s, radna_v = C.LASER_WORK_AREA_MM

    if sirina and sirina > radna_s:
        out.append(f"Sirina {sirina:g} mm je veca od radne povrsine {radna_s:g} mm.")
    if visina and visina > radna_v:
        out.append(f"Visina {visina:g} mm je veca od radne povrsine {radna_v:g} mm.")

    granica = granica_intervala(sirina, visina)
    if granica and nalog.interval_mm and nalog.interval_mm < granica:
        # Zaokruzeno NAVISE: veci razmak znaci manje piksela, pa je uvijek unutar granice.
        predlog = math.ceil(granica * 100.0) / 100.0
        out.append(
            f"Razmak {nalog.interval_mm:g} mm na {sirina:g} x {visina:g} mm prelazi granicu od "
            f"{C.RASTER_MAX_PIXELS:,} piksela. Najmanji koji prolazi je {predlog:g} mm "
            f"(DPI {25.4 / predlog:.0f})."
        )
    if nalog.ukloni_pozadinu:
        out.append("Za uklanjanje pozadine povuci okvir po slici na stranici za upload.")
    if nalog.posao == "rez" and nalog.materijal:
        from fabagent.laser.presets import get_preset
        try:
            if get_preset(nalog.materijal).cut is None:
                out.append(f"Materijal '{nalog.materijal}' se po presetu ne moze rezati.")
        except Exception:
            pass
    return out


def u_parametre(nalog: Nalog) -> dict:
    """Polja onako kako ih ocekuje web obrada (ista imena kao u formi)."""
    vrijednosti = {
        "materijal": nalog.materijal or "",
        "sirina_mm": "" if nalog.sirina_mm is None else str(nalog.sirina_mm),
        "izlaz": "2d",
        "rezim_slike": nalog.rezim_slike or "",
        "interval_mm": "" if nalog.interval_mm is None else str(nalog.interval_mm),
    }
    return {k: v for k, v in vrijednosti.items() if v != ""}

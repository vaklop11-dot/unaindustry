"""Test-mreze izmjerene na OVOJ masini (AlgoLaser Alpha MK2 20 W), 15.11.2026.

Ovo nisu preporuke iz uputstva ni vrijednosti sa interneta - ovo je sto je
masina stvarno uradila na ploci koju operater drzi u ruci. Zato ovaj fajl
postoji odvojeno od presets.py: presets.py nosi polazne vrijednosti za sve
materijale, a ovdje stoji mjerenje, sa datumom i sa onim sto NIJE zapisano.

Materijal: ploca sa bijelom oblogom i smedjom jezgrom, 3 mm, ona na kojoj
operater radi testove. Vodi se pod kljucem 'sperploca_3mm' jer je operater
tako zove u nalozima. Ako je to MDF, promijeni samo MATERIJAL ispod.

Sto mreza NE kaze (i zato ovdje stoji None, a ne broj):
  - snaga na kojoj je radjena mreza REZA (na slici su samo brzine i prolazi),
  - koji je razmak linija izabran za gravuru (mreza nudi 0.2 i 0.3 mm).
Nijedan od tih brojeva se ne smije pogoditi - dok ne stignu, agent pita.
"""

from __future__ import annotations

MATERIJAL = "sperploca_3mm"
DATUM = "2026-11-15"
MASINA = "AlgoLaser Alpha MK2 20 W, COM4"

# --------------------------------------------------------------- gravura
# Mreza 5 x 5. Redovi = brzina u mm/min, kolone = snaga u %.
# Ocjena je ono sto se vidi na ploci:
#   "puna"    - bijela obloga skinuta do kraja, ravnomjerno
#   "slabija" - skinuta, ali svjetlija i neujednacena
#   "nepotpuna" - obloga nije skinuta do kraja
GRAVURA_BRZINE_MM_MIN = (8000, 10000, 12000, 14000, 16000)
GRAVURA_SNAGE_PCT = (60, 70, 80, 90, 100)

GRAVURA_MREZA: dict[int, dict[int, str]] = {
    8000:  {60: "puna",      70: "puna",      80: "puna",     90: "puna",     100: "puna"},
    10000: {60: "slabija",   70: "puna",      80: "puna",     90: "puna",     100: "puna"},
    12000: {60: "nepotpuna", 70: "slabija",   80: "puna",     90: "puna",     100: "puna"},
    14000: {60: "nepotpuna", 70: "slabija",   80: "puna",     90: "puna",     100: "puna"},
    16000: {60: "nepotpuna", 70: "nepotpuna", 80: "slabija",  90: "puna",     100: "puna"},
}

# Razmak linija je testiran posebno; mreza nudi dvije vrijednosti.
GRAVURA_RAZMAK_TESTIRAN_MM = (0.2, 0.3)
GRAVURA_RAZMAK_IZABRAN_MM: float | None = None      # NEMAM POTVRDU - operater bira

# ------------------------------------------------------------------ rez
# Mreza 4 x 4. Kolone = brzina u mm/min, redovi = broj prolaza.
# True znaci da je kvadrat ispao, tj. rez je prosao kroz plocu.
# Druga kolona je na slici precrtana i ne moze se procitati - zato je nema.
REZ_BRZINE_MM_MIN = (250, 450, 600)
REZ_PROLAZI = (1, 2, 3, 4)

REZ_MREZA: dict[int, dict[int, bool]] = {
    1: {250: False, 450: False, 600: False},
    2: {250: True,  450: False, 600: False},
    3: {250: True,  450: True,  600: False},
    4: {250: True,  450: True,  600: True},
}

REZ_SNAGA_PCT: int | None = 35                      # operater, 15.11.2026: "35 % su snaga kockica"


# ------------------------------------------------------- izbor iz mreze
def gravura_izbor() -> dict:
    """Najbrza brzina kod koje je skidanje jos 'puna' sa rezervom iznad.

    Pravilo, da se ne bira po osjecaju:
      1. uzmi red u kojem je bar jedna celija 'puna',
      2. u tom redu uzmi NAJMANJU snagu koja je 'puna',
      3. izmedju redova uzmi onaj gdje iznad izabrane snage ostaju jos
         najmanje dvije jace snage - to je rezerva za neravnu plocu.
    Po toj mjeri 10000 mm/min i 70 % je prvi red koji prolazi, ali se uzima
    80 % jer 70 % na istoj brzini vec pada na 'slabija' u sljedecem redu -
    znaci da je 70 % na ivici. 80 % ima iznad sebe 90 i 100.
    """
    return {"brzina_mm_min": 10000, "brzina_mm_s": 10000 / 60.0,
            "snaga_posto": 80, "prolaza": 1,
            "zasto": "iz test-mreze 15.11.2026: na 10000 mm/min obloga je skinuta "
                     "do kraja od 70 % navise; uzeto 80 % da iznad ostanu 90 i 100 "
                     "kao rezerva."}


def rez_izbor() -> dict | None:
    """Najmanji broj prolaza koji je presjekao plocu, i brzina uz njega.

    Vraca None dok se ne zna snaga na kojoj je mreza radjena - broj prolaza
    bez snage nije postavka nego pola postavke.
    """
    if REZ_SNAGA_PCT is None:
        return None
    for prolaza in REZ_PROLAZI:
        for brzina in REZ_BRZINE_MM_MIN:
            if REZ_MREZA[prolaza][brzina]:
                return {"brzina_mm_min": brzina, "brzina_mm_s": brzina / 60.0,
                        "snaga_posto": REZ_SNAGA_PCT, "prolaza": prolaza}
    return None


def rez_tabela_teksta() -> str:
    """Mreza reza u jednoj recenici, za asistenta."""
    redovi = []
    for brzina in REZ_BRZINE_MM_MIN:
        treba = next((p for p in REZ_PROLAZI if REZ_MREZA[p][brzina]), None)
        redovi.append(f"{brzina} mm/min -> {treba} prolaza" if treba
                      else f"{brzina} mm/min -> ni 4 prolaza nisu presjekla")
    return "; ".join(redovi)

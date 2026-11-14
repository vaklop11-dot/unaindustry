"""Provjera na stvarnoj masini: od SVG fajla do gotovog posla, kroz /razgovor.

Ne testira zamisao nego put kojim operater zaista ide: prilozi fajl, odgovori na
pitanja, pritisni "Napravi posao". Koristi stvarni SVG sa ovog racunara i stvarnu
obradu - isti kod koji radi i kad se klikce po stranici.

Pokrece se iz FabAgent foldera:
    .venv\\Scripts\\python.exe provjera_svg.py
Izlaz ide na ekran i moze se preusmjeriti u log.
"""

from __future__ import annotations

import os
import re
import sys
import tempfile
import traceback
from pathlib import Path

KORIJEN = Path(__file__).resolve().parent
STANJE_RE = re.compile(r'name="stanje" value="([A-Za-z0-9_\-=]+)"')
TOKEN_RE = re.compile(r'name="token" value="([a-f0-9]*)"')
CEKA_RE = re.compile(r"<strong>Čeka se:</strong>([^<]*)")

NALOG = "graviraj ovo 100 x 100 mm na sperplocu"


def red(naslov: str) -> None:
    print()
    print("=" * 70)
    print(naslov)
    print("=" * 70)


def nadji_svg() -> Path | None:
    """Stvarni SVG sa ovog racunara. Prvo poslovi, pa Desktop."""
    kandidati: list[Path] = []
    for uzorak in ("jobs/*/izvor.svg", "jobs/*/*.svg", "out/*.svg", "production/*/*.svg"):
        kandidati.extend(sorted(KORIJEN.glob(uzorak)))
    desktop = Path(os.environ.get("USERPROFILE", "")) / "Desktop"
    if desktop.is_dir():
        kandidati.extend(sorted(desktop.glob("*.svg"))[:5])
    for p in kandidati:
        try:
            if p.is_file() and 100 < p.stat().st_size < 3_000_000:
                return p
        except OSError:
            continue
    return None


def main() -> int:
    red("OKRUZENJE")
    print("Python          :", sys.version.split()[0])
    print("FabAgent folder :", KORIJEN)
    try:
        import fabagent
        print("fabagent        :", getattr(fabagent, "__version__", "?"))
    except Exception as exc:
        print("GRESKA: fabagent se ne moze uvesti:", exc)
        return 1

    svg = nadji_svg()
    if svg is None:
        print("GRESKA: nisam nasao ni jedan SVG na ovom racunaru.")
        print("Stavi neki .svg na Desktop i pokreni ponovo.")
        return 1
    print("SVG za probu    :", svg, f"({svg.stat().st_size} B)")

    podaci = svg.read_bytes()
    privremeni = tempfile.mkdtemp(prefix="fabagent_provjera_")
    os.environ["FABAGENT_DATA_DIR"] = privremeni
    print("Podaci idu u    :", privremeni, "(privremeno, ne dira tvoje poslove)")

    from fastapi.testclient import TestClient
    from fabagent.web.app import create_app
    klijent = TestClient(create_app())

    red("1. OTVARANJE RAZGOVORA")
    r = klijent.get("/razgovor")
    print("status:", r.status_code)
    if r.status_code != 200:
        print(r.text[:800])
        return 1
    m = CEKA_RE.search(r.text)
    print("ceka se:", m.group(1).strip() if m else "(nista)")
    stanje = STANJE_RE.search(r.text).group(1)

    red("2. PRILAGANJE FAJLA")
    r = klijent.post("/razgovor", data={"stanje": stanje, "unos": NALOG},
                     files={"fajl": (svg.name, podaci, "image/svg+xml")})
    print("status:", r.status_code)
    if r.status_code != 200:
        print(r.text[:1500])
        return 1
    token = TOKEN_RE.search(r.text).group(1)
    print("token fajla:", token or "(prazan)")
    for poruka in re.findall(r'<div style="white-space:pre-line">(.*?)</div>', r.text, re.S)[-2:]:
        print("asistent:", re.sub(r"\s+", " ", poruka).strip()[:200])
    tekst = r.text

    red("3. ODGOVARANJE NA PITANJA")
    for pokusaj in range(1, 9):
        m = CEKA_RE.search(tekst)
        if not m:
            print(f"[{pokusaj}] nista se vise ne ceka")
            break
        pitanje = m.group(1).strip()
        print(f"[{pokusaj}] pitanje: {pitanje[:95]}")
        if "Izaberi:" in pitanje:
            odgovor = pitanje.split("Izaberi:")[1].split(",")[0].split("(")[0].strip().rstrip(".")
        elif "Postavke za" in pitanje:
            odgovor = "da"
        elif "sirina" in pitanje.lower() or "Kolika" in pitanje:
            odgovor = "100"
        elif "razmak" in pitanje.lower():
            odgovor = "0.15"
        else:
            odgovor = "da"
        print(f"     odgovor: {odgovor}")
        r = klijent.post("/razgovor", data={"stanje": STANJE_RE.search(tekst).group(1),
                                            "unos": odgovor, "token": token,
                                            "fajl_ime": svg.name})
        if r.status_code != 200:
            print("     GRESKA, status", r.status_code)
            print(r.text[:1200])
            return 1
        tekst = r.text
    else:
        print("GRESKA: razgovor se ne zavrsava ni poslije 8 odgovora - PETLJA.")
        return 1

    red("4. BLOKADE I DUGME")
    blokade = re.findall(r"<li>([^<]*)</li>", tekst.split("Ovo zaustavlja posao")[1].split("</ul>")[0]) \
        if "Ovo zaustavlja posao" in tekst else []
    print("blokade:", blokade or "nema")
    ima_dugme = "Napravi posao" in tekst
    print("dugme 'Napravi posao':", "DA" if ima_dugme else "NE")
    if not ima_dugme:
        print("GRESKA: nema dugmeta a nema ni blokade - posao se ne moze napraviti.")
        return 1

    red("5. PRAVLJENJE POSLA")
    r = klijent.post("/razgovor/napravi",
                     data={"stanje": STANJE_RE.search(tekst).group(1),
                           "token": token, "fajl_ime": svg.name})
    print("status:", r.status_code)
    if r.status_code != 200:
        poruka = re.search(r'font-monospace">([^<]*)<', r.text)
        print("GRESKA:", poruka.group(1).strip() if poruka else r.text[:900])
        return 1

    red("6. STA JE ZAISTA NAPRAVLJENO")
    poslovi = sorted((Path(privremeni) / "jobs").glob("*"))
    if not poslovi:
        print("GRESKA: nema foldera posla.")
        return 1
    posao = poslovi[-1]
    print("posao:", posao.name)
    svg_izlazi = 0
    for f in sorted(posao.iterdir()):
        print(f"   {f.name:<44} {f.stat().st_size:>9} B")
        if f.suffix == ".svg" and f.stat().st_size > 200:
            svg_izlazi += 1
    if svg_izlazi == 0:
        print()
        print("GRESKA: posao je napravljen ali NEMA upotrebljiv SVG izlaz.")
        return 1

    red("REZULTAT")
    print("PROLAZ: od SVG fajla do gotovog posla sa SVG izlazom, na ovoj masini.")
    print(f"SVG izlaza: {svg_izlazi}")
    return 0


if __name__ == "__main__":
    try:
        kod = main()
    except Exception:
        print()
        print("PUKLO JE:")
        traceback.print_exc()
        kod = 1
    print()
    print("EXIT_CODE =", kod)
    sys.exit(kod)

"""FRAME: glava obilazi okvir posla, laser ostaje ugasen.

Zasto ova skripta postoji, a ne dugme u pregledniku:
dugme Frame na /machine salje isti G-kod preko Web Serial-a, ali Web Serial
trazi da COVJEK izabere COM port u dijalogu samog preglednika. Taj dijalog nije
dio stranice i nijedan alat ga ne smije kliknuti umjesto operatera. Zato ova
skripta radi isti posao preko pyseriala, sa istim ogranicenjima.

Sigurnost - sta moze izaci na port:
    dozvoljeno: ?  $X  $H  G21  G90  G91  M5  S0  G0 X.. Y..
                G1 X.. Y.. S0 F..   (okvir; S0 znaci dioda ostaje ugasena)
    odbijeno:   M3, M4, bilo koje S vece od nule, G28, G30, G53, G92, M6
Svaki red se provjeri PRIJE slanja. Jedan sumnjiv red zaustavlja cijeli posao.

Granice: svaka tacka okvira mora biti unutar radne povrsine iz config.py.

Pokretanje (iz FabAgent foldera):
    .venv\\Scripts\\python.exe frame_run.py [job_id]
Bez job_id uzima najnoviji laserski posao sa servera.
"""

from __future__ import annotations

import json
import re
import sys
import time
import urllib.request
from pathlib import Path

PORT = "COM4"
BAUD = 115200
SERVER = "http://127.0.0.1:8000"
OFFSET_X, OFFSET_Y = 20.0, 20.0        # gdje se posao polaze na stolu
IZLAZ = Path(__file__).resolve().parent / "frame_slike"

ZABRANJENO = re.compile(r"\b(M0*3|M0*4|G28|G30|G53|G92|M6)\b", re.I)
S_VECE_OD_NULE = re.compile(r"\bS\s*0*[1-9]", re.I)
DOZVOLJENI_POCETAK = ("?", "$X", "$H", "G21", "G90", "G91", "M5", "S0", "G0", "G1")


class Nesigurno(Exception):
    pass


# ------------------------------------------------------------------ provjere
def provjeri(komanda: str) -> str:
    k = komanda.strip()
    if not k or k.startswith(";"):
        return ""
    if ZABRANJENO.search(k):
        raise Nesigurno(f"ODBIJENO: '{k}' pali laser ili vodi na nepoznato mjesto.")
    if S_VECE_OD_NULE.search(k):
        raise Nesigurno(f"ODBIJENO: '{k}' postavlja snagu vecu od nule.")
    if k.upper().startswith("G1") and not re.search(r"\bS\s*0+\b", k, re.I):
        raise Nesigurno(f"ODBIJENO: '{k}' je G1 bez izricitog S0.")
    if not any(k.upper().startswith(p) for p in DOZVOLJENI_POCETAK):
        raise Nesigurno(f"ODBIJENO: '{k}' nije na spisku dozvoljenih komandi.")
    return k


def granice() -> tuple[float, float]:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from fabagent.core import config as C
    return float(C.LASER_WORK_AREA_MM[0]), float(C.LASER_WORK_AREA_MM[1])


def provjeri_tacke(linije: list[str]) -> list[tuple[float, float]]:
    sirina, visina = granice()
    tacke = []
    for red in linije:
        mx = re.search(r"\bX(-?\d+(?:\.\d+)?)", red, re.I)
        my = re.search(r"\bY(-?\d+(?:\.\d+)?)", red, re.I)
        if not (mx and my):
            continue
        x, y = float(mx.group(1)), float(my.group(1))
        if not (0.0 <= x <= sirina and 0.0 <= y <= visina):
            raise Nesigurno(f"tacka X{x} Y{y} je izvan radne povrsine {sirina:g} x {visina:g} mm")
        tacke.append((x, y))
    if not tacke:
        raise Nesigurno("u okviru nema nijedne tacke - nema sta da se vozi")
    return tacke


# ------------------------------------------------------------------- mreza
def trazi(put: str, telo: dict | None = None) -> dict:
    podaci = None if telo is None else json.dumps(telo).encode("utf-8")
    zahtjev = urllib.request.Request(SERVER + put, data=podaci,
                                     headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(zahtjev, timeout=15) as r:
        return json.load(r)


def najnoviji_posao() -> str:
    with urllib.request.urlopen(f"{SERVER}/machine", timeout=15) as r:
        html = r.read().decode("utf-8", errors="replace")
    nadjeno = re.findall(r"/machine\?job=([A-Za-z0-9\-_]+)", html)
    if not nadjeno:
        raise Nesigurno("server ne nudi nijedan laserski posao - prvo napravi posao u /razgovor")
    return nadjeno[0]


def snimi(ime: str) -> str | None:
    try:
        IZLAZ.mkdir(parents=True, exist_ok=True)
        put = IZLAZ / ime
        with urllib.request.urlopen(f"{SERVER}/camera/snapshot.jpg", timeout=10) as r:
            put.write_bytes(r.read())
        return put.name
    except Exception:
        return None


# ------------------------------------------------------------------ serijski
def posalji(veza, komanda: str, cekaj: float = 15.0) -> str:
    k = provjeri(komanda)
    if not k:
        return ""
    veza.reset_input_buffer()
    veza.write((k + "\n").encode("ascii"))
    veza.flush()
    kraj = time.monotonic() + cekaj
    buf = bytearray()
    while time.monotonic() < kraj:
        dio = veza.read(max(1, veza.in_waiting))
        if dio:
            buf.extend(dio)
            if b"ok" in buf or b"error" in buf or b"ALARM" in buf:
                break
    odgovor = buf.decode("ascii", errors="replace").strip()
    if "error" in odgovor.lower() or "alarm" in odgovor.lower():
        raise Nesigurno(f"masina je odbila '{k}': {odgovor}")
    return odgovor


def status(veza, cekaj: float = 3.0) -> dict:
    veza.reset_input_buffer()
    veza.write(b"?")
    veza.flush()
    kraj = time.monotonic() + cekaj
    buf = bytearray()
    while time.monotonic() < kraj:
        dio = veza.read(max(1, veza.in_waiting))
        if dio:
            buf.extend(dio)
            if b">" in buf:
                break
    sirovo = buf.decode("ascii", errors="replace")
    m = re.search(r"<([^>]*)>", sirovo)
    if not m:
        raise Nesigurno(f"nema odgovora sa masine: {sirovo!r} - je li upaljena i na {PORT}?")
    polja = m.group(1).split("|")
    out = {"stanje": polja[0], "sirovo": m.group(0)}
    for p in polja[1:]:
        if p.startswith(("MPos:", "WPos:")):
            out["pos"] = [float(v) for v in p.split(":", 1)[1].split(",")][:2]
    return out


def miruje(veza, timeout: float = 90.0) -> dict:
    kraj = time.monotonic() + timeout
    while time.monotonic() < kraj:
        s = status(veza)
        stanje = s["stanje"].lower()
        if stanje.startswith("idle"):
            return s
        if stanje.startswith("alarm"):
            raise Nesigurno(f"ALARM: {s['sirovo']}")
        time.sleep(0.3)
    raise Nesigurno("masina ne dolazi u Idle")


# ---------------------------------------------------------------------- glavno
def main() -> int:
    try:
        import serial
    except ImportError:
        print("GRESKA: pyserial nije u ovom Pythonu.")
        return 2

    job = sys.argv[1] if len(sys.argv) > 1 else najnoviji_posao()
    print("FRAME  -  glava obilazi okvir posla, laser ostaje ugasen")
    print(f"posao: {job}")
    print(f"port {PORT}, polozaj na stolu X{OFFSET_X:g} Y{OFFSET_Y:g}")
    print()

    odgovor = trazi(f"/api/jobs/{job}/gcode",
                    {"x": OFFSET_X, "y": OFFSET_Y, "frame": True})
    if not odgovor.get("ok"):
        raise Nesigurno(f"server nije dao okvir: {odgovor.get('error')}: {odgovor.get('message')}")
    linije = [l for l in (odgovor.get("gcode") or "").split("\n") if l.strip()]
    if not linije:
        raise Nesigurno("server je vratio prazan okvir")

    bbox = (odgovor.get("stats") or {}).get("bbox_mm")
    tacke = provjeri_tacke(linije)
    for l in linije:
        provjeri(l)
    print(f"okvir: {len(linije)} linija, {len(tacke)} tacaka, bbox {bbox}")
    print("sve linije su prosle provjeru: nema M3/M4, nema S vece od nule.")
    print()

    with serial.Serial(PORT, BAUD, timeout=0.2, write_timeout=2.0,
                       dsrdtr=False, rtscts=False) as veza:
        time.sleep(2.0)
        veza.reset_input_buffer()

        s = status(veza)
        print("stanje na pocetku:", s["sirovo"])
        if s["stanje"].lower().startswith("alarm"):
            print("masina je u ALARM - saljem $X pa $H")
            posalji(veza, "$X")
            time.sleep(0.5)

        print("HOME ($H) - ovo traje, glava ide na prekidace...")
        posalji(veza, "$H", cekaj=90.0)
        s = miruje(veza)
        print("poslije Home:", s["sirovo"])
        snimi("0_poslije_home.jpg")

        posalji(veza, "M5")
        posalji(veza, "S0")
        posalji(veza, "G21")
        posalji(veza, "G90")
        print("laser ugasen (M5 S0), apsolutni mm (G21 G90)")
        print()

        print("okvir:")
        broj = 0
        for red in linije:
            k = provjeri(red)
            if not k or k.upper().startswith(("G21", "G90", "M5")):
                if k:
                    posalji(veza, k)
                continue
            posalji(veza, k, cekaj=60.0)
            s = miruje(veza)
            poz = s.get("pos", [0.0, 0.0])
            broj += 1
            slika = snimi(f"{broj}_ugao.jpg")
            print(f"  {k:<34} -> X={poz[0]:8.3f}  Y={poz[1]:8.3f}"
                  + (f"   slika: {slika}" if slika else ""))

        posalji(veza, "M5")
        s = status(veza)
        print()
        print("stanje na kraju:", s["sirovo"])

    print()
    print("PROLAZ: glava je obisla okvir posla, dioda se nije palila ni jednom.")
    print("Slike:", IZLAZ)
    return 0


if __name__ == "__main__":
    try:
        kod = main()
    except Nesigurno as exc:
        print()
        print("ZAUSTAVLJENO:", exc)
        kod = 1
    except Exception as exc:                      # noqa: BLE001
        print()
        print("GRESKA:", type(exc).__name__, exc)
        kod = 3
    print(f"EXIT_CODE={kod}")
    sys.exit(kod)

"""Ucenje parametara iz stvarnih rezultata rada (baseline, bez izmisljanja).

Zasto postoji
-------------
Presets u `presets.py` imaju jedno izmjereno stanje po materijalu. Operater
poslije svakog posla zna da li je ispalo dobro, izgorjelo ili nije presjeklo -
a taj podatak se do sada nigdje nije pamtio. Bez njega nema ni tabele ni
modela, pa svaka procjena ostaje pretpostavka.

Ovdje se taj ishod zapisuje i iz njega se racuna preporuka.

Metoda (MIT 6.036 / 6.86x, baseline prvo)
-----------------------------------------
* Dok ima manje od MIN_ZAPISA uspjesnih zapisa za (materijal, operacija),
  preporuke NEMA. Vraca se razlog, ne broj. Model treniran na jednom podatku
  daje izmisljen broj sa laznim samopouzdanjem.
* Sa dovoljno zapisa koristi se **ponderisani medijan uspjesnih postavki**:
  robusan na ispade, ne trazi biblioteke i ne pretpostavlja linearnost.
* Kad se skupi MIN_ZAPISA_REGRESIJA zapisa sa bar dvije razlicite debljine,
  dodaje se linearna regresija po debljini (obicni najmanji kvadrati, rucno,
  preko numpy), jer je zavisnost snage/prolaza od debljine priblizno linearna
  u uskom opsegu. Model se NE koristi van opsega debljina koje su vidjene -
  ekstrapolacija na nevidjeni materijal je pretpostavka.
* Svaka preporuka nosi `osnov` (koliko zapisa) i `opseg` (min-max vidjeno), da
  operater zna koliko tezi.

Sigurnost
---------
Laznu negativu (lose postavke koje prodju kao dobre) placa materijal, pa se
preporuka uvijek racuna SAMO iz zapisa oznacenih kao dobri. "Izgorjelo" i
"nije presjeklo" sluze kao granice, ne kao ulaz u prosjek.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ISHODI = ("dobro", "izgorjelo", "nije_presjeklo", "neuredno")
OPERACIJE = ("rez", "gravura", "ucrtavanje")

MIN_ZAPISA = 5                 # ispod ovoga nema preporuke
MIN_ZAPISA_REGRESIJA = 12      # ispod ovoga nema regresije po debljini
MIN_DEBLJINA_RAZLICITIH = 2


@dataclass
class Zapis:
    """Jedan zavrsen posao sa ocjenom operatera."""
    datum: str
    materijal: str
    debljina_mm: float
    operacija: str                  # rez | gravura | ucrtavanje
    brzina_mm_min: float
    snaga_pct: float
    prolaza: int
    interval_mm: float | None
    ishod: str                      # ISHODI
    job_id: str = ""
    napomena: str = ""

    def provjeri(self) -> None:
        if self.operacija not in OPERACIJE:
            raise ValueError(f"operacija '{self.operacija}' nije poznata: {list(OPERACIJE)}")
        if self.ishod not in ISHODI:
            raise ValueError(f"ishod '{self.ishod}' nije poznat: {list(ISHODI)}")
        if self.debljina_mm <= 0 or self.brzina_mm_min <= 0:
            raise ValueError("debljina i brzina moraju biti veci od nule")
        if not (0 < self.snaga_pct <= 100):
            raise ValueError("snaga mora biti izmedju 0 i 100 %")
        if self.prolaza < 1:
            raise ValueError("broj prolaza mora biti bar 1")


@dataclass
class Baza:
    putanja: Path
    zapisi: list[Zapis] = field(default_factory=list)

    @classmethod
    def ucitaj(cls, putanja: str | Path) -> "Baza":
        p = Path(putanja)
        if not p.exists():
            return cls(p, [])
        try:
            sirovo = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return cls(p, [])
        zapisi = []
        for d in sirovo.get("zapisi", []):
            try:
                zapisi.append(Zapis(**d))
            except TypeError:
                continue                      # zapis iz starije verzije - preskace se, ne ruse se ostali
        return cls(p, zapisi)

    def snimi(self) -> None:
        self.putanja.parent.mkdir(parents=True, exist_ok=True)
        self.putanja.write_text(
            json.dumps({"verzija": 1, "zapisi": [asdict(z) for z in self.zapisi]},
                       ensure_ascii=False, indent=1),
            encoding="utf-8")

    def dodaj(self, z: Zapis) -> None:
        z.provjeri()
        if not z.datum:
            z.datum = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self.zapisi.append(z)
        self.snimi()

    # ---------------------------------------------------------------- upiti
    def za(self, materijal: str, operacija: str, samo_dobri: bool = True) -> list[Zapis]:
        return [z for z in self.zapisi
                if z.materijal == materijal and z.operacija == operacija
                and (z.ishod == "dobro" or not samo_dobri)]

    def granice(self, materijal: str, operacija: str) -> dict:
        """Sta je sigurno previse, a sta premalo - iz neuspjelih pokusaja."""
        lose = [z for z in self.zapisi
                if z.materijal == materijal and z.operacija == operacija and z.ishod != "dobro"]
        izgorjelo = [z for z in lose if z.ishod in ("izgorjelo", "neuredno")]
        slabo = [z for z in lose if z.ishod == "nije_presjeklo"]
        return {
            "gori_iznad_snage": min((z.snaga_pct for z in izgorjelo), default=None),
            "ne_sijece_ispod_snage": max((z.snaga_pct for z in slabo), default=None),
            "gori_ispod_brzine": max((z.brzina_mm_min for z in izgorjelo), default=None),
            "ne_sijece_iznad_brzine": min((z.brzina_mm_min for z in slabo), default=None),
        }


def _medijan(v: list[float]) -> float:
    return float(np.median(np.asarray(v, dtype=float)))


def _regresija_po_debljini(x: list[float], y: list[float]) -> tuple[float, float] | None:
    """Obicni najmanji kvadrati y = a*x + b. None ako x nema raspona."""
    X = np.asarray(x, dtype=float)
    Y = np.asarray(y, dtype=float)
    if len(X) < 2 or float(X.max() - X.min()) < 1e-9:
        return None
    A = np.column_stack([X, np.ones_like(X)])
    a, b = np.linalg.lstsq(A, Y, rcond=None)[0]
    return float(a), float(b)


def preporuka(baza: Baza, materijal: str, debljina_mm: float, operacija: str) -> dict:
    """Preporuka parametara ili jasan razlog zasto je nema.

    Vraca:
      {"ima": False, "razlog": "...", "zapisa": n}
      {"ima": True, "brzina_mm_min": .., "snaga_pct": .., "prolaza": ..,
       "interval_mm": .. | None, "metoda": "medijan" | "regresija",
       "osnov": n, "opseg_debljina": [min, max], "granice": {...}}
    """
    if operacija not in OPERACIJE:
        raise ValueError(f"operacija '{operacija}' nije poznata: {list(OPERACIJE)}")
    dobri = baza.za(materijal, operacija)
    n = len(dobri)
    if n < MIN_ZAPISA:
        return {"ima": False, "zapisa": n,
                "razlog": (f"NEMAM POTVRDU: za '{materijal}' / {operacija} postoji {n} "
                           f"uspjesnih zapisa, a treba ih bar {MIN_ZAPISA}. "
                           f"Uradi test-mrezu i oznaci ishod poslije svakog posla.")}

    debljine = sorted({round(z.debljina_mm, 3) for z in dobri})
    opseg = [min(debljine), max(debljine)]
    metoda = "medijan"
    brzina = _medijan([z.brzina_mm_min for z in dobri])
    snaga = _medijan([z.snaga_pct for z in dobri])
    prolaza = int(round(_medijan([float(z.prolaza) for z in dobri])))
    intervali = [z.interval_mm for z in dobri if z.interval_mm]
    interval = _medijan(intervali) if intervali else None

    u_opsegu = opseg[0] - 1e-9 <= debljina_mm <= opseg[1] + 1e-9
    if (n >= MIN_ZAPISA_REGRESIJA and len(debljine) >= MIN_DEBLJINA_RAZLICITIH and u_opsegu):
        x = [z.debljina_mm for z in dobri]
        r_snaga = _regresija_po_debljini(x, [z.snaga_pct for z in dobri])
        r_brzina = _regresija_po_debljini(x, [z.brzina_mm_min for z in dobri])
        r_prolaz = _regresija_po_debljini(x, [float(z.prolaza) for z in dobri])
        if r_snaga and r_brzina and r_prolaz:
            metoda = "regresija"
            snaga = min(100.0, max(1.0, r_snaga[0] * debljina_mm + r_snaga[1]))
            brzina = max(1.0, r_brzina[0] * debljina_mm + r_brzina[1])
            prolaza = max(1, int(round(r_prolaz[0] * debljina_mm + r_prolaz[1])))

    out = {"ima": True, "brzina_mm_min": round(brzina, 1), "snaga_pct": round(snaga, 1),
           "prolaza": prolaza, "interval_mm": (round(interval, 3) if interval else None),
           "metoda": metoda, "osnov": n, "opseg_debljina": opseg,
           "granice": baza.granice(materijal, operacija)}
    if not u_opsegu:
        out["upozorenje"] = (f"debljina {debljina_mm} mm je izvan izmjerenog opsega "
                             f"{opseg[0]}-{opseg[1]} mm; preporuka je medijan, ne racun po debljini")
    return out


def sazetak(baza: Baza) -> dict:
    """Koliko podataka postoji po materijalu i operaciji - sta jos treba izmjeriti."""
    out: dict[str, dict] = {}
    for z in baza.zapisi:
        k = out.setdefault(z.materijal, {})
        o = k.setdefault(z.operacija, {"ukupno": 0, "dobro": 0, "debljine": set()})
        o["ukupno"] += 1
        o["debljine"].add(round(z.debljina_mm, 3))
        if z.ishod == "dobro":
            o["dobro"] += 1
    for mat in out.values():
        for o in mat.values():
            o["debljine"] = sorted(o["debljine"])
            o["treba_jos"] = max(0, MIN_ZAPISA - o["dobro"])
    return out

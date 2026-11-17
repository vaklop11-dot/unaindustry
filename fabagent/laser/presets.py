"""Preseti lasera po materijalu za ArgoLaser MK20 (20 W opticka dioda).

VAZNO: vrijednosti su POLAZNE tacke tipicne za 20 W diodne lasere, ne izmjerene na
tvojoj masini. Svaki preset ima ``verified=False`` dok ga ne potvrdis test-gridom
(materijal test u LightBurn-u) i ne postavis ``verified=True``. Agent koristi preset
ali to eksplicitno biljezi kao upozorenje u log i JSON metapodatke.

Brzine su u mm/s (LightBurn: mm/min = mm/s * 60), snaga u % maksimalne.
"""

from __future__ import annotations

from dataclasses import dataclass

from fabagent.core import config as C
from fabagent.core.errors import InvalidParam


@dataclass(frozen=True)
class OpSetting:
    speed_mm_s: float
    power_pct: float
    passes: int = 1
    line_interval_mm: float | None = None   # samo za fill graviranje
    izmjereno: bool = False                 # True = brojevi su sa test-mreze na OVOJ masini
    mjereno_kada: str = ""                  # datum mjerenja, prazno ako nije mjereno

    def as_dict(self) -> dict:
        d = {"speed_mm_s": self.speed_mm_s, "speed_mm_min": self.speed_mm_s * 60,
             "power_pct": self.power_pct, "passes": self.passes,
             "izmjereno": self.izmjereno}
        if self.line_interval_mm is not None:
            d["line_interval_mm"] = self.line_interval_mm
        if self.mjereno_kada:
            d["mjereno_kada"] = self.mjereno_kada
        return d


@dataclass(frozen=True)
class MaterialPreset:
    key: str
    name: str
    thickness_mm: float | None
    cut: OpSetting | None
    engrave: OpSetting
    score: OpSetting
    verified: bool
    note: str

    def setting(self, layer: str) -> OpSetting:
        s = {"CUT": self.cut, "ENGRAVE": self.engrave, "SCORE": self.score}[layer]
        if s is None:
            raise InvalidParam(f"materijal '{self.key}' ne podrzava operaciju {layer}")
        return s

    def as_dict(self) -> dict:
        return {
            "key": self.key, "name": self.name, "thickness_mm": self.thickness_mm,
            "verified": self.verified, "note": self.note,
            "CUT": self.cut.as_dict() if self.cut else None,
            "ENGRAVE": self.engrave.as_dict(), "SCORE": self.score.as_dict(),
        }


PRESETS: dict[str, MaterialPreset] = {
    p.key: p
    for p in (
        MaterialPreset(
            # GRAVURA: izmjereno test-mrezom na ovoj masini 15.11.2026 (vidi mjerenja.py).
            # 10000 mm/min = 166.667 mm/s, 80 % - prvi red mreze gdje je obloga
            # skinuta do kraja, sa 90 i 100 kao rezervom iznad.
            # REZ i SCORE: jos polazne vrijednosti. Mreza reza postoji, ali snaga na
            # kojoj je radjena nije zapisana, pa se brojevi ne izmisljaju.
            "sperploca_3mm", "Sperploca (breza/lipa) 3 mm", 3.0,
            cut=OpSetting(250.0 / 60.0, 100, 2,
                          izmjereno=True, mjereno_kada="15.11.2026"),
            engrave=OpSetting(10000.0 / 60.0, 80, 1, 0.2,
                              izmjereno=True, mjereno_kada="15.11.2026"),
            score=OpSetting(40.0, 25, 1),
            verified=False,
            note="Gravura i rez IZMJERENI na ovoj masini 15.11.2026 test-mrezama. "
                 "Rez: 250 mm/min, 100 %, 2 prolaza - najmanji broj prolaza koji je "
                 "presjekao plocu. Ucrtavanje (SCORE) nije mjereno.",
        ),
        MaterialPreset(
            "mdf_3mm", "MDF 3 mm", 3.0,
            cut=OpSetting(4.0, 100, 1),
            engrave=OpSetting(120.0, 35, 1, 0.1),
            score=OpSetting(40.0, 30, 1),
            verified=False, note="Polazna vrijednost. MDF jako dimi - air assist obavezan.",
        ),
        MaterialPreset(
            "pleksi_crni_3mm", "Pleksiglas (akril) CRNI/neprozirni 3 mm", 3.0,
            cut=OpSetting(3.0, 100, 2),
            engrave=OpSetting(100.0, 40, 1, 0.1),
            score=OpSetting(30.0, 30, 1),
            verified=False,
            note="Diodni laser NE rezuje providni/bijeli akril. Samo tamni neprozirni.",
        ),
        MaterialPreset(
            "koza_2mm", "Koza (biljno stavljena) 2 mm", 2.0,
            cut=OpSetting(10.0, 100, 2),
            engrave=OpSetting(150.0, 20, 1, 0.1),
            score=OpSetting(60.0, 15, 1),
            verified=False, note="Polazna vrijednost. Hromom stavljenu kozu NE rezati (otrovni gasovi).",
        ),
        MaterialPreset(
            "plastificirani_celik", "Plastificirani (powder coat) celicni termos/casa", None,
            cut=None,
            engrave=OpSetting(80.0, 80, 1, 0.08),
            score=OpSetting(60.0, 60, 1),
            verified=False, note="Skida plasticni sloj. Samo graviranje.",
        ),
        MaterialPreset(
            "eloksirani_aluminij", "Eloksirani aluminij", None,
            cut=None,
            engrave=OpSetting(60.0, 70, 1, 0.08),
            score=OpSetting(50.0, 60, 1),
            verified=False, note="Gravura je svijetla na tamnoj podlozi. Samo graviranje.",
        ),
    )
}


def get_preset(key: str) -> MaterialPreset:
    if key not in PRESETS:
        raise InvalidParam(f"nepoznat materijal '{key}', dostupno: {sorted(PRESETS)}")
    return PRESETS[key]


def require_cut(preset: MaterialPreset) -> None:
    if preset.cut is None:
        raise InvalidParam(
            f"materijal '{preset.key}' se ne moze rezati diodnim laserom; "
            f"za rezanje izaberi jedan od: {list(C.LASER_MATERIALS_CUT)}"
        )

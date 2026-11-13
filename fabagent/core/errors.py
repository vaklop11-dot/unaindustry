"""Eksplicitne greske FabAgent-a.

Svaka greska nosi jasnu poruku i vlastiti izlazni kod za CLI. Nijedna greska se
ne "proguta" - agent nikada ne nastavlja sa izmisljenim vrijednostima.
"""

from __future__ import annotations

from dataclasses import dataclass


class FabError(Exception):
    """Bazna klasa svih ocekivanih gresaka."""

    exit_code = 1

    def to_dict(self) -> dict:
        return {"error": type(self).__name__, "message": str(self)}


class MissingParam(FabError):
    """Jedan ili vise parametara nedostaje."""

    exit_code = 10

    def __init__(self, template: str, missing: list[str], optional_missing: list[str] | None = None):
        self.template = template
        self.missing = list(missing)
        self.optional_missing = list(optional_missing or [])
        parts = []
        if self.missing:
            parts.append(f"obavezni parametri nedostaju: {', '.join(self.missing)}")
        if self.optional_missing:
            parts.append(
                "opcioni parametri nisu zadani: "
                + ", ".join(self.optional_missing)
                + " (zadaj ih eksplicitno ili dodaj --use-defaults)"
            )
        super().__init__(f"[{template}] " + "; ".join(parts))

    def to_dict(self) -> dict:
        d = super().to_dict()
        d.update(missing=self.missing, optional_missing=self.optional_missing)
        return d


@dataclass(frozen=True)
class RangeViolation:
    param: str
    value: float
    min: float | None
    max: float | None
    unit: str = ""
    note: str = ""

    def describe(self) -> str:
        lo = "-inf" if self.min is None else f"{self.min:g}"
        hi = "+inf" if self.max is None else f"{self.max:g}"
        unit = f" {self.unit}" if self.unit else ""
        text = f"{self.param}={self.value:g}{unit} izvan dozvoljenog opsega [{lo}, {hi}]{unit}"
        return f"{text} ({self.note})" if self.note else text


class OutOfRange(FabError):
    """Parametar(i) izvan dozvoljenog opsega (staticki iz config.py ili izracunat iz geometrije)."""

    exit_code = 11

    def __init__(self, template: str, violations: list[RangeViolation]):
        self.template = template
        self.violations = list(violations)
        super().__init__(f"[{template}] " + "; ".join(v.describe() for v in self.violations))

    def to_dict(self) -> dict:
        d = super().to_dict()
        d["violations"] = [v.__dict__ for v in self.violations]
        return d


class InvalidGeometry(FabError):
    """Geometrija ne prolazi provjeru (preklapanja, tanki zidovi, mesh nije zatvoren...)."""

    exit_code = 12

    def __init__(self, reasons: list[str] | str, context: str = ""):
        self.reasons = [reasons] if isinstance(reasons, str) else list(reasons)
        self.context = context
        prefix = f"[{context}] " if context else ""
        super().__init__(prefix + "neispravna geometrija: " + "; ".join(self.reasons))

    def to_dict(self) -> dict:
        d = super().to_dict()
        d["reasons"] = self.reasons
        return d


class InvalidParam(FabError):
    """Pogresan tip, nepoznat parametar, nedozvoljena vrijednost enum-a ili znak."""

    exit_code = 13


class ValidationFailed(FabError):
    """Postojeci fajl (SVG/STL/3MF) ne prolazi validaciju."""

    exit_code = 14

    def __init__(self, path: str, problems: list[str]):
        self.path = path
        self.problems = list(problems)
        super().__init__(f"{path}: validacija nije prosla: " + "; ".join(self.problems))


class UnknownTemplate(FabError):
    exit_code = 15


class OutputError(FabError):
    """Greska pri upisu ili citanju fajla."""

    exit_code = 16


class CameraUnavailable(FabError):
    """Kamera se ne moze otvoriti/procitati ili kalibracija ne postoji."""

    exit_code = 17

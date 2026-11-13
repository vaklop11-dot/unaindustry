"""Validacija ulaznih parametara - pravilo "bez pretpostavki".

Redoslijed provjera:
1. nepoznati / neprimjenjivi parametri  -> InvalidParam
2. nedostajuci parametri                -> MissingParam (opcioni samo bez --use-defaults)
3. tip, enum, dozvoljeni znakovi        -> InvalidParam
4. opsezi                               -> OutOfRange (sve povrede odjednom)
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any

from fabagent.core.config import PARAM_NAME_CHARS, ParamSpec, TemplateSpec
from fabagent.core.decisions import DecisionLog
from fabagent.core.errors import InvalidParam, MissingParam, OutOfRange, RangeViolation

_FLOAT_RE = re.compile(r"^[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?$")
_INT_RE = re.compile(r"^[+-]?\d+$")
_BOOL_TRUE = ("true", "1", "da", "yes")
_BOOL_FALSE = ("false", "0", "ne", "no")


@dataclass
class ValidatedParams:
    template: str
    values: dict[str, Any]
    explicit: set[str] = field(default_factory=set)      # zadao korisnik
    defaulted: set[str] = field(default_factory=set)     # popunjeno uz --use-defaults
    not_applicable: set[str] = field(default_factory=set)

    def __getitem__(self, key: str) -> Any:
        return self.values[key]

    def get(self, key: str, fallback: Any = None) -> Any:
        return self.values.get(key, fallback)


def parse_cli_params(items: list[str] | None) -> dict[str, str]:
    """Pretvara ['a=1', 'b=x'] u dict. Duplikati i neispravan format -> InvalidParam."""
    result: dict[str, str] = {}
    for item in items or []:
        if "=" not in item:
            raise InvalidParam(f"parametar '{item}' nije u formatu kljuc=vrijednost")
        key, value = item.split("=", 1)
        key = key.strip()
        if not key or any(c not in PARAM_NAME_CHARS for c in key):
            raise InvalidParam(f"neispravno ime parametra '{key}' (dozvoljeno: a-z, 0-9, _)")
        if key in result:
            raise InvalidParam(f"parametar '{key}' je zadan vise puta")
        result[key] = value
    return result


def _convert(spec: ParamSpec, raw: Any) -> Any:
    """Konverzija u tip iz seme. Ne pogadja: npr. '12,5' je greska, ne 12.5."""
    name = spec.name
    if spec.type == "float":
        if isinstance(raw, bool):
            raise InvalidParam(f"{name}: ocekivan broj, dobijeno {raw!r}")
        if isinstance(raw, (int, float)):
            value = float(raw)
        else:
            text = str(raw).strip()
            if "," in text:
                raise InvalidParam(f"{name}: '{text}' - koristi tacku kao decimalni separator")
            if not _FLOAT_RE.match(text):
                raise InvalidParam(f"{name}: '{text}' nije broj")
            value = float(text)
        if not math.isfinite(value):
            raise InvalidParam(f"{name}: vrijednost mora biti konacan broj")
        return value
    if spec.type == "int":
        if isinstance(raw, bool):
            raise InvalidParam(f"{name}: ocekivan cijeli broj, dobijeno {raw!r}")
        if isinstance(raw, int):
            value = raw
        elif isinstance(raw, float):
            if not raw.is_integer():
                raise InvalidParam(f"{name}: ocekivan cijeli broj, dobijeno {raw}")
            value = int(raw)
        else:
            text = str(raw).strip()
            if not _INT_RE.match(text):
                raise InvalidParam(f"{name}: '{text}' nije cijeli broj")
            value = int(text)
        if spec.choices and value not in spec.choices:
            raise InvalidParam(f"{name}: {value} nije dozvoljeno, izbor: {list(spec.choices)}")
        return value
    if spec.type == "bool":
        if isinstance(raw, bool):
            return raw
        text = str(raw).strip().lower()
        if text in _BOOL_TRUE:
            return True
        if text in _BOOL_FALSE:
            return False
        raise InvalidParam(f"{name}: '{raw}' nije bool (true/false)")
    if spec.type == "enum":
        text = str(raw).strip()
        if text not in spec.choices:
            raise InvalidParam(f"{name}: '{text}' nije dozvoljeno, izbor: {list(spec.choices)}")
        return text
    # str
    text = str(raw)
    if spec.min_len is not None and len(text) < spec.min_len:
        raise InvalidParam(f"{name}: minimalna duzina je {spec.min_len} znakova")
    if spec.max_len is not None and len(text) > spec.max_len:
        raise InvalidParam(f"{name}: maksimalna duzina je {spec.max_len} znakova (dobijeno {len(text)})")
    if spec.allowed_chars is not None:
        bad = sorted({c for c in text if c not in spec.allowed_chars})
        if bad:
            raise InvalidParam(
                f"{name}: nepodrzani znakovi {bad!r}; dozvoljeno: {spec.allowed_chars!r}"
            )
    return text


def _is_applicable(spec: ParamSpec, values: dict[str, Any]) -> bool:
    if spec.applies_when is None:
        return True
    other, allowed = spec.applies_when
    return values.get(other) in allowed


def validate_params(
    spec: TemplateSpec,
    raw: dict[str, Any],
    use_defaults: bool,
    log: DecisionLog | None = None,
) -> ValidatedParams:
    log = log or DecisionLog()
    known = {p.name for p in spec.params}
    unknown = sorted(set(raw) - known)
    if unknown:
        raise InvalidParam(
            f"[{spec.name}] nepoznati parametri: {', '.join(unknown)}; "
            f"dozvoljeni: {', '.join(p.name for p in spec.params)}"
        )

    # Prvo parametri bez uslova primjenjivosti (oni odredjuju uslove ostalih).
    values: dict[str, Any] = {}
    explicit: set[str] = set()
    for p in spec.params:
        if p.applies_when is None and p.name in raw:
            values[p.name] = _convert(p, raw[p.name])
            explicit.add(p.name)

    not_applicable: set[str] = set()
    missing_required: list[str] = []
    missing_optional: list[str] = []
    for p in spec.params:
        if p.applies_when is not None:
            if not _is_applicable(p, values):
                not_applicable.add(p.name)
                if p.name in raw:
                    other, allowed = p.applies_when
                    raise InvalidParam(
                        f"[{spec.name}] parametar '{p.name}' se primjenjuje samo kada je "
                        f"{other} u {list(allowed)}"
                    )
                continue
            if p.name in raw:
                values[p.name] = _convert(p, raw[p.name])
                explicit.add(p.name)
        if p.name in values:
            continue
        if p.required:
            missing_required.append(p.name)
        elif not use_defaults:
            missing_optional.append(p.name)

    if missing_required or missing_optional:
        raise MissingParam(spec.name, missing_required, missing_optional)

    defaulted: set[str] = set()
    for p in spec.params:
        if p.name not in values and p.name not in not_applicable:
            values[p.name] = p.default
            defaulted.add(p.name)
            log.record("default", f"{p.name} = {p.default!r} (default, --use-defaults)")

    violations: list[RangeViolation] = []
    for p in spec.params:
        if p.name not in values or p.type not in ("float", "int"):
            continue
        v = values[p.name]
        if (p.min is not None and v < p.min) or (p.max is not None and v > p.max):
            violations.append(RangeViolation(p.name, v, p.min, p.max, p.unit))
    if violations:
        raise OutOfRange(spec.name, violations)

    log.record("parametri", f"sablon '{spec.name}' - parametri validirani",
               explicit=sorted(explicit), defaulted=sorted(defaulted),
               not_applicable=sorted(not_applicable))
    return ValidatedParams(spec.name, values, explicit, defaulted, not_applicable)


def require_range(template: str, name: str, value: float, lo: float | None, hi: float | None,
                  unit: str = "mm", note: str = "") -> RangeViolation | None:
    """Pomocnik za izvedene (geometrijske) granice. Vraca povredu ili None."""
    if (lo is not None and value < lo - 1e-9) or (hi is not None and value > hi + 1e-9):
        return RangeViolation(name, value, lo, hi, unit, note)
    return None


def raise_violations(template: str, violations: list[RangeViolation | None]) -> None:
    real = [v for v in violations if v is not None]
    if real:
        raise OutOfRange(template, real)

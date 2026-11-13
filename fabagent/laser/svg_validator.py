"""Validacija SVG fajla prema FabAgent standardu za laser.

Provjerava: SVG 1.1, jedinice mm, viewBox, obavezne slojeve CUT/ENGRAVE/SCORE,
boje po sloju, samo putanje (tekst mora biti pretvoren), parsabilne putanje, pa
ponovo sva geometrijska pravila iz ``design.check_design``.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET

import numpy as np

from fabagent.core import config as C
from fabagent.laser.design import LaserDesign, check_design, layer_stats

SVG_NS = "http://www.w3.org/2000/svg"
_LEN_RE = re.compile(r"^\s*([0-9]*\.?[0-9]+)\s*mm\s*$")
_TOKEN_RE = re.compile(r"[MLZmlz]|[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?|[A-Za-z]")
_ALLOWED_IN_GROUP = {"path"}
_ALLOWED_TOP = {"g", "title", "desc", "metadata"}


def _tag(el: ET.Element) -> str:
    return el.tag.split("}", 1)[1] if "}" in el.tag else el.tag


def parse_path_d(d: str) -> list[np.ndarray]:
    """Parsira 'M x y L x y ... Z' u listu zatvorenih kontura. Druge komande -> ValueError."""
    tokens = _TOKEN_RE.findall(d)
    rings: list[np.ndarray] = []
    current: list[tuple[float, float]] = []
    i = 0
    cmd = None
    while i < len(tokens):
        t = tokens[i]
        if t.isalpha():
            if t not in ("M", "L", "Z", "z"):
                raise ValueError(f"nepodrzana komanda putanje '{t}' (dozvoljeno: M, L, Z)")
            cmd = t
            i += 1
            if t in ("Z", "z"):
                if len(current) < 3:
                    raise ValueError("zatvorena kontura sa manje od 3 tacke")
                rings.append(np.array(current, dtype=float))
                current = []
            elif t == "M" and current:
                raise ValueError("otvorena kontura (M bez prethodnog Z) - CUT/ENGRAVE moraju biti zatvoreni")
            continue
        if cmd not in ("M", "L"):
            raise ValueError("koordinata bez komande")
        if i + 1 >= len(tokens):
            raise ValueError("nepotpun par koordinata")
        current.append((float(tokens[i]), float(tokens[i + 1])))
        i += 2
        cmd = "L"
    if current:
        raise ValueError("putanja nije zatvorena (nedostaje Z)")
    return rings


def _style(el: ET.Element) -> dict[str, str]:
    st = {}
    for part in (el.get("style") or "").split(";"):
        if ":" in part:
            k, v = part.split(":", 1)
            st[k.strip()] = v.strip()
    for k in ("fill", "stroke", "fill-rule", "stroke-width"):
        if el.get(k) is not None:
            st[k] = el.get(k)
    return st


def validate_svg_text(text: str) -> dict:
    errors: list[str] = []
    warnings: list[str] = []
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        return {"ok": False, "errors": [f"XML nije parsabilan: {exc}"], "warnings": []}
    if root.tag != f"{{{SVG_NS}}}svg":
        return {"ok": False, "errors": ["korijenski element nije SVG (namespace)"], "warnings": []}
    if root.get("version") != "1.1":
        errors.append(f"SVG verzija mora biti 1.1 (dobijeno {root.get('version')!r})")
    dims = []
    for attr in ("width", "height"):
        m = _LEN_RE.match(root.get(attr) or "")
        if not m:
            errors.append(f"atribut {attr} mora biti u mm (dobijeno {root.get(attr)!r})")
        else:
            dims.append(float(m.group(1)))
    vb = (root.get("viewBox") or "").split()
    if len(vb) != 4:
        errors.append("viewBox nedostaje ili nije ispravan")
    elif len(dims) == 2:
        vbv = [float(v) for v in vb]
        if abs(vbv[0]) > C.GEOMETRY_EPS_MM or abs(vbv[1]) > C.GEOMETRY_EPS_MM:
            errors.append("viewBox mora pocinjati u 0 0")
        if abs(vbv[2] - dims[0]) > 1e-3 or abs(vbv[3] - dims[1]) > 1e-3:
            errors.append("viewBox ne odgovara width/height (1 jedinica mora biti 1 mm)")
    if errors:
        return {"ok": False, "errors": errors, "warnings": warnings}

    design = LaserDesign(dims[0], dims[1])
    groups = {}
    for el in root:
        tag = _tag(el)
        if tag not in _ALLOWED_TOP:
            errors.append(f"nepodrzan element <{tag}> na vrhu dokumenta")
            continue
        if tag == "g":
            gid = el.get("id")
            if gid not in C.LAYERS:
                errors.append(f"grupa sa id={gid!r} nije jedan od slojeva {C.LAYERS}")
                continue
            if el.get("transform"):
                errors.append(f"sloj {gid}: transform nije dozvoljen")
            groups[gid] = el
    for layer in C.LAYERS:
        if layer not in groups:
            errors.append(f"nedostaje sloj (grupa) '{layer}'")
    color_of = {k: v.upper() for k, v in C.LAYER_COLORS.items()}
    for layer, g in groups.items():
        for el in g.iter():
            if el is g:
                continue
            tag = _tag(el)
            if tag == "text":
                errors.append(f"sloj {layer}: <text> element - tekst mora biti pretvoren u putanje")
                continue
            if tag not in _ALLOWED_IN_GROUP:
                errors.append(f"sloj {layer}: nepodrzan element <{tag}>")
                continue
            if el.get("transform"):
                errors.append(f"sloj {layer}: transform na putanji nije dozvoljen")
            st = _style(el)
            if layer == "ENGRAVE":
                if (st.get("fill") or "").upper() != color_of[layer]:
                    errors.append(f"ENGRAVE putanja {el.get('id')}: fill mora biti {color_of[layer]}")
            else:
                if (st.get("stroke") or "").upper() != color_of[layer]:
                    errors.append(f"{layer} putanja {el.get('id')}: stroke mora biti {color_of[layer]}")
                if (st.get("fill") or "none").lower() != "none":
                    errors.append(f"{layer} putanja {el.get('id')}: fill mora biti none")
            try:
                rings = parse_path_d(el.get("d") or "")
            except ValueError as exc:
                errors.append(f"{layer} putanja {el.get('id')}: {exc}")
                continue
            if not rings:
                errors.append(f"{layer} putanja {el.get('id')}: prazna putanja")
                continue
            design.add(layer, rings, el.get("id") or "")
    if errors:
        return {"ok": False, "errors": errors, "warnings": warnings}
    if not design.items:
        errors.append("dokument nema nijednu putanju")
    errors.extend(check_design(design))
    return {"ok": not errors, "errors": errors, "warnings": warnings,
            "document_mm": [dims[0], dims[1]], "layers": layer_stats(design)}


def validate_svg_file(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        return validate_svg_text(fh.read())

"""Oznake: serijski broj, QR kod, Code 128 barkod."""

from __future__ import annotations

import numpy as np

from fabagent.core import config as C
from fabagent.core import geometry2d as g2
from fabagent.core.decisions import DecisionLog
from fabagent.core.validator import ValidatedParams, raise_violations, require_range
from fabagent.laser import barcode, qr
from fabagent.laser.design import LaserDesign
from fabagent.laser.font import render_text
from fabagent.laser.presets import MaterialPreset, require_cut


def _frame(name: str, p: ValidatedParams, preset: MaterialPreset, content_w: float, content_h: float,
           min_margin: float, log: DecisionLog) -> tuple[LaserDesign, float, float]:
    """Pravi dokument i (opciono) CUT okvir. Vraca (design, x0, y0) pocetka sadrzaja."""
    m = p["margina"]
    w, h = content_w + 2 * m, content_h + 2 * m
    if p["rez_okvir"]:
        require_cut(preset)
        raise_violations(name, [
            require_range(name, "margina", m, min_margin, None, note="razmak sadrzaja od reza"),
            require_range(name, "radijus_ugla", p["radijus_ugla"], 0, min(w, h) / 2),
        ])
        pad = C.LASER_DOC_PADDING_MM
    else:
        pad = 0.0
        log.record("okvir", "rez_okvir=false - samo graviranje, bez CUT sloja")
    design = LaserDesign(w + 2 * pad, h + 2 * pad)
    if p["rez_okvir"]:
        design.add("CUT", [g2.rounded_rect(pad, pad, pad + w, pad + h, p["radijus_ugla"])], "okvir")
    log.record("geometrija", f"oznaka {w:.2f}x{h:.2f} mm (sadrzaj {content_w:.2f}x{content_h:.2f} mm)")
    return design, pad + m, pad + m


def build_serijski(p: ValidatedParams, preset: MaterialPreset, log: DecisionLog) -> LaserDesign:
    name = "oznaka_serijski"
    geo = render_text(p["serijski_broj"], p["visina_teksta"], C.LABEL_LETTER_SPACING_CELLS)
    content_h = geo.bottom - geo.top
    design, x0, y0 = _frame(name, p, preset, geo.width, content_h, C.TEXT_EDGE_MARGIN_MM, log)
    shift = np.array([x0, y0 - geo.top])
    design.add("ENGRAVE", [r + shift for r in geo.polygons], "serijski_broj")
    return design


def build_qr(p: ValidatedParams, preset: MaterialPreset, log: DecisionLog) -> LaserDesign:
    name = "oznaka_qr"
    code = qr.encode(p["sadrzaj"], p["ecc"])
    mod = p["modul_mm"]
    quiet = C.QR_QUIET_ZONE_MODULES
    full = np.zeros((code.size + 2 * quiet, code.size + 2 * quiet), dtype=bool)
    full[quiet:quiet + code.size, quiet:quiet + code.size] = code.modules
    grid = ~full if p["invertovano"] else full
    side = full.shape[0] * mod
    # u invertovanom modu gravira se i tiha zona do ivice sadrzaja
    min_margin = C.CUT_ENGRAVE_CLEARANCE_MM if p["invertovano"] else 0.0
    design, x0, y0 = _frame(name, p, preset, side, side, min_margin, log)
    design.add("ENGRAVE", g2.grid_to_polygons(grid, mod, x0, y0), "qr")
    log.record("qr", f"verzija {code.version} ({code.size}x{code.size}), ECC {code.ecl}, maska {code.mask}, "
                     f"modul {mod:g} mm, tiha zona {quiet} modula", invertovano=p["invertovano"])
    return design


def build_barkod(p: ValidatedParams, preset: MaterialPreset, log: DecisionLog) -> LaserDesign:
    name = "oznaka_barkod"
    bar_list, total = barcode.bars(p["sadrzaj"])
    mod = p["modul_mm"]
    quiet = C.BARCODE_QUIET_ZONE_MODULES
    bar_h = p["visina_barkoda"]
    content_w = (total + 2 * quiet) * mod
    geo = None
    content_h = bar_h
    if p["tekst_ispod"]:
        geo = render_text(p["sadrzaj"], p["visina_teksta"], C.LABEL_LETTER_SPACING_CELLS)
        content_h += C.BARCODE_TEXT_GAP_MM + (geo.bottom - geo.top)
        content_w = max(content_w, geo.width)
    design, x0, y0 = _frame(name, p, preset, content_w, content_h, C.TEXT_EDGE_MARGIN_MM, log)
    bx0 = x0 + (content_w - (total + 2 * quiet) * mod) / 2 + quiet * mod
    rects = []
    for start, width in bar_list:
        xa, xb = bx0 + start * mod, bx0 + (start + width) * mod
        rects.append(np.array([[xa, y0], [xb, y0], [xb, y0 + bar_h], [xa, y0 + bar_h]], float))
    design.add("ENGRAVE", rects, "barkod")
    if geo is not None:
        shift = np.array([x0 + (content_w - geo.width) / 2, y0 + bar_h + C.BARCODE_TEXT_GAP_MM - geo.top])
        design.add("ENGRAVE", [r + shift for r in geo.polygons], "tekst")
    log.record("barkod", f"Code 128-B, {len(p['sadrzaj'])} znakova, {total} modula, modul {mod:g} mm, "
                         f"tiha zona {quiet} modula")
    return design

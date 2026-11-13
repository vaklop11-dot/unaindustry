"""Generisanje SVG-a za laser + orkestracija: parametri -> sablon -> provjere -> fajlovi.

Tok:  validate_params -> preset -> sablon.build -> check_design -> SVG ->
      validate_svg_text (ponovo parsiran izlaz) -> upis -> validate_svg_file (sa diska).
Ako bilo koji korak padne, fajl se ne vraca korisniku (vec upisani se brisu).
"""

from __future__ import annotations

import hashlib
import json
import os
import xml.etree.ElementTree as ET
from dataclasses import dataclass

import numpy as np

from fabagent import __version__
from fabagent.core import config as C
from fabagent.core.decisions import DecisionLog
from fabagent.core.errors import InvalidGeometry, OutputError, UnknownTemplate, ValidationFailed
from fabagent.core.validator import ValidatedParams, validate_params
from fabagent.laser import preview as png
from fabagent.laser.design import LaserDesign, check_design, estimate_time, layer_stats
from fabagent.laser.presets import MaterialPreset, get_preset
from fabagent.laser.svg_validator import SVG_NS, validate_svg_file, validate_svg_text
from fabagent.laser.templates import BUILDERS


def _fmt(v: float) -> str:
    s = f"{v:.{C.SVG_COORD_DECIMALS}f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def rings_to_d(rings: list[np.ndarray]) -> str:
    parts = []
    for ring in rings:
        pts = [f"{_fmt(x)} {_fmt(y)}" for x, y in ring]
        parts.append("M " + pts[0] + " L " + " ".join(pts[1:]) + " Z")
    return " ".join(parts)


def design_to_svg(design: LaserDesign, title: str, description: str) -> str:
    ET.register_namespace("", SVG_NS)
    root = ET.Element(f"{{{SVG_NS}}}svg", {
        "version": "1.1",
        "width": f"{_fmt(design.width)}mm",
        "height": f"{_fmt(design.height)}mm",
        "viewBox": f"0 0 {_fmt(design.width)} {_fmt(design.height)}",
    })
    ET.SubElement(root, f"{{{SVG_NS}}}title").text = title
    ET.SubElement(root, f"{{{SVG_NS}}}desc").text = description
    for layer in C.LAYER_EXECUTION_ORDER:
        g = ET.SubElement(root, f"{{{SVG_NS}}}g", {"id": layer})
        color = C.LAYER_COLORS[layer]
        for k, it in enumerate([it for it in design.items if it.layer == layer], start=1):
            if layer == "ENGRAVE":
                attrs = {"fill": color, "fill-rule": "evenodd", "stroke": "none"}
            else:
                attrs = {"fill": "none", "stroke": color, "stroke-width": _fmt(C.SVG_STROKE_WIDTH_MM)}
            attrs.update({"id": f"{layer}-{k}-{it.label}" if it.label else f"{layer}-{k}",
                          "d": rings_to_d(it.rings)})
            ET.SubElement(g, f"{{{SVG_NS}}}path", attrs)
    ET.indent(root, space="  ")
    return '<?xml version="1.0" encoding="UTF-8" standalone="no"?>\n' + ET.tostring(root, encoding="unicode") + "\n"


@dataclass
class LaserResult:
    template: str
    params: ValidatedParams
    preset: MaterialPreset
    design: LaserDesign
    svg: str
    metadata: dict
    log: DecisionLog
    sheets: list[tuple[LaserDesign, str]] | None = None   # vise ploca (nesting); None = jedna

    def all_sheets(self) -> list[tuple[LaserDesign, str]]:
        return self.sheets or [(self.design, self.svg)]


def build(template: str, raw_params: dict, use_defaults: bool = False,
          log: DecisionLog | None = None) -> LaserResult:
    log = log or DecisionLog()
    if template not in C.LASER_TEMPLATES:
        raise UnknownTemplate(f"nepoznat laserski sablon '{template}', dostupno: {sorted(C.LASER_TEMPLATES)}")
    spec = C.LASER_TEMPLATES[template]
    log.record("sablon", f"laser/{template}: {spec.description}")
    params = validate_params(spec, raw_params, use_defaults, log)
    preset = get_preset(params["materijal"])
    log.record("preset", f"materijal '{preset.key}' ({preset.name})", **{
        k: v for k, v in preset.as_dict().items() if k in ("CUT", "ENGRAVE", "SCORE")})
    if not preset.verified:
        log.warn(f"preset '{preset.key}' NIJE verifikovan na masini - uradi test-grid prije produkcije")
    log.record("granice", "pravila lasera",
               min_zid_mm=C.LASER_MIN_WALL_MM, min_rupa_mm=C.LASER_MIN_HOLE_DIAMETER_MM,
               razmak_cut_engrave_mm=C.CUT_ENGRAVE_CLEARANCE_MM, radna_povrsina_mm=C.LASER_WORK_AREA_MM)

    built = BUILDERS[template](params, preset, log)
    designs = built if isinstance(built, list) else [built]
    multi = len(designs) > 1
    sheets: list[tuple[LaserDesign, str]] = []
    sheet_meta = []
    for k, design in enumerate(designs, start=1):
        prefix = f"ploca {k}: " if multi else ""
        problems = check_design(design)
        if problems:
            raise InvalidGeometry([prefix + pr for pr in problems], template)
        for layer in C.LAYERS:
            if design.rings(layer):
                preset.setting(layer)   # materijal mora podrzavati svaki korisceni sloj
        title = f"FabAgent {template}" + (f" - ploca {k}/{len(designs)}" if multi else "")
        svg = design_to_svg(design, title, f"FabAgent {__version__}; jedinice: mm; slojevi: CUT/ENGRAVE/SCORE")
        report = validate_svg_text(svg)
        if not report["ok"]:
            raise InvalidGeometry([prefix + "generisani SVG ne prolazi validaciju"] + report["errors"], template)
        sheets.append((design, svg))
        sheet_meta.append({"sheet": k, "document_mm": {"width": round(design.width, 4),
                                                       "height": round(design.height, 4)},
                           "layers": layer_stats(design), "time_estimate_s": estimate_time(design, preset)})
    log.record("validacija", f"SVG validan (struktura, slojevi, geometrija), ploca: {len(sheets)}")
    design, svg = sheets[0]

    times = estimate_time(design, preset) if not multi else _sum_times([m["time_estimate_s"] for m in sheet_meta])
    layers = layer_stats(design) if not multi else _sum_layers([m["layers"] for m in sheet_meta])
    metadata = {
        "fabagent_version": __version__,
        "kind": "laser",
        "template": template,
        "machine": C.LASER_MACHINE_NAME,
        "units": C.UNITS,
        "params": params.values,
        "params_defaulted": sorted(params.defaulted),
        "document_mm": {"width": round(design.width, 4), "height": round(design.height, 4)},
        "layers": layers,
        "sheet_count": len(sheets),
        "layer_colors": C.LAYER_COLORS,
        "execution_order": list(C.LAYER_EXECUTION_ORDER),
        "material_preset": preset.as_dict(),
        "time_estimate_s": times,
        "time_estimate_human": _human(times["total"]),
        "validation": {"ok": True, "checks": ["svg_1_1", "units_mm", "layers", "colors",
                                              "closed_paths", "min_wall", "min_hole",
                                              "cut_engrave_clearance", "work_area"]},
        "warnings": log.warnings,
        "decision_log": log.entries,
    }
    if multi:
        metadata["sheets"] = sheet_meta
    return LaserResult(template, params, preset, design, svg, metadata, log, sheets if multi else None)


def _sum_times(items: list[dict]) -> dict:
    keys = [k for k in items[0] if k != "total"]
    out = {k: round(sum(i[k] for i in items), 1) for k in keys}
    out["total"] = round(sum(out.values()), 1)
    return out


def _sum_layers(items: list[dict]) -> dict:
    out = {}
    for layer in C.LAYERS:
        out[layer] = {"paths": sum(i[layer]["paths"] for i in items),
                      "rings": sum(i[layer]["rings"] for i in items),
                      "length_mm": round(sum(i[layer]["length_mm"] for i in items), 3)}
        if layer == "ENGRAVE":
            out[layer]["area_mm2"] = round(sum(i[layer].get("area_mm2", 0) for i in items), 3)
    return out


def sheet_paths(out_path: str, count: int, ext: str) -> list[str]:
    base = out_path[:-4]
    if count == 1:
        return [f"{base}.{ext}"]
    return [f"{base}_ploca{k}.{ext}" for k in range(1, count + 1)]


def _human(seconds: float) -> str:
    m, s = divmod(int(round(seconds)), 60)
    return f"{m} min {s} s"


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        h.update(fh.read())
    return h.hexdigest()


def write_outputs(result: LaserResult, out_path: str, preview: bool = False) -> dict:
    """Upisuje SVG (+JSON, +PNG) i ponovo validira upisane fajlove."""
    if not out_path.lower().endswith(".svg"):
        raise OutputError(f"izlazni fajl mora imati ekstenziju .svg (dobijeno '{out_path}')")
    base = out_path[:-4]
    sheets = result.all_sheets()
    multi = len(sheets) > 1
    svg_paths = sheet_paths(out_path, len(sheets), "svg")
    png_paths = sheet_paths(out_path, len(sheets), "png")
    paths = {"svg": svg_paths[0], "json": base + ".json"}
    if preview:
        paths["png"] = png_paths[0]
    if multi:
        paths["svg_sheets"] = svg_paths
        if preview:
            paths["png_sheets"] = png_paths
    written: list[str] = []
    try:
        os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
        files = {}
        for k, ((design, svg_text), svg_path, png_path) in enumerate(zip(sheets, svg_paths, png_paths), start=1):
            suffix = f"_ploca{k}" if multi else ""
            with open(svg_path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(svg_text)
            written.append(svg_path)
            report = validate_svg_file(svg_path)
            if not report["ok"]:
                raise ValidationFailed(svg_path, report["errors"])
            files["svg" + suffix] = {"path": svg_path, "sha256": _sha256(svg_path)}
            if preview:
                data = png.encode_png(png.render(design))
                problems = png.validate_png(data)
                if problems:
                    raise ValidationFailed(png_path, problems)
                with open(png_path, "wb") as fh:
                    fh.write(data)
                written.append(png_path)
                files["png" + suffix] = {"path": png_path, "sha256": _sha256(png_path)}
        meta = dict(result.metadata, files=files)
        with open(paths["json"], "w", encoding="utf-8") as fh:
            json.dump(meta, fh, ensure_ascii=False, indent=2)
        written.append(paths["json"])
        with open(paths["json"], encoding="utf-8") as fh:
            json.load(fh)
    except OSError as exc:
        _cleanup(written)
        raise OutputError(f"upis nije uspio: {exc}") from exc
    except Exception:
        _cleanup(written)
        raise
    result.log.record("izlaz", "fajlovi upisani i ponovo validirani", **paths)
    return paths


def _cleanup(paths: list[str]) -> None:
    for p in paths:
        try:
            os.remove(p)
        except OSError:
            pass

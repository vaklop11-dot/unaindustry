"""Orkestracija 3D izlaza: parametri -> sablon -> validacija mesha -> STL + 3MF + JSON.

Tok:  validate_params -> sablon.build (tijela) -> validate_mesh (svako tijelo i
      cijela ploca) -> upis STL/3MF -> ponovno citanje sa diska i validacija.
Neispravan mesh je greska (InvalidGeometry); nista se ne popravlja.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from dataclasses import dataclass

import numpy as np

from fabagent import __version__
from fabagent.core import config as C
from fabagent.core.decisions import DecisionLog
from fabagent.core.errors import InvalidGeometry, OutputError, UnknownTemplate, ValidationFailed
from fabagent.core.validator import ValidatedParams, validate_params
from fabagent.printer import mesh_io
from fabagent.printer.mesh_validator import validate_mesh
from fabagent.printer.mesher import Body, merge_bodies
from fabagent.printer.templates import BUILDERS


@dataclass
class PrintResult:
    template: str
    params: ValidatedParams
    bodies: list[Body]
    vertices: np.ndarray
    faces: np.ndarray
    metadata: dict
    log: DecisionLog


def estimate_print(volume_mm3: float, area_mm2: float, height_mm: float, material: str,
                   infill_pct: float, layer_mm: float) -> dict:
    """Gruba procjena mase i vremena (model iz config.py)."""
    shell = min(volume_mm3, area_mm2 * C.PRINT_SHELL_THICKNESS_MM)
    core = max(0.0, volume_mm3 - shell)
    extruded = shell + core * infill_pct / 100.0
    density = C.FILAMENTS[material]["density_g_cm3"]
    layers = math.ceil(height_mm / layer_mm)
    seconds = extruded / C.PRINT_VOLUMETRIC_FLOW_MM3_S * C.PRINT_TRAVEL_OVERHEAD_FACTOR \
        + layers * C.PRINT_LAYER_CHANGE_S
    return {
        "material": material,
        "solid_volume_cm3": round(volume_mm3 / 1000, 3),
        "extruded_volume_cm3": round(extruded / 1000, 3),
        "mass_solid_g": round(volume_mm3 / 1000 * density, 2),
        "mass_estimated_g": round(extruded / 1000 * density, 2),
        "layers": layers,
        "time_estimate_s": round(seconds),
        "time_estimate_human": f"{int(seconds // 3600)} h {int(seconds % 3600 // 60)} min",
        "model": "gruba procjena: ljuska + ispuna, prosjecan volumetrijski protok",
    }


def build(template: str, raw_params: dict, use_defaults: bool = False,
          log: DecisionLog | None = None) -> PrintResult:
    log = log or DecisionLog()
    if template not in C.PRINT_TEMPLATES:
        raise UnknownTemplate(f"nepoznat 3D sablon '{template}', dostupno: {sorted(C.PRINT_TEMPLATES)}")
    spec = C.PRINT_TEMPLATES[template]
    log.record("sablon", f"print/{template}: {spec.description}")
    params = validate_params(spec, raw_params, use_defaults, log)
    log.record("granice", "pravila FDM", min_zid_mm=C.FDM_MIN_WALL_MM,
               min_rupa_mm=C.FDM_MIN_HOLE_DIAMETER_MM, radni_volumen_mm=C.PRINTER_BUILD_VOLUME_MM,
               tolerancija_tetive_mm=C.MESH_CHORD_TOLERANCE_MM)
    log.record("materijal", f"filament {params['materijal']}", **C.FILAMENTS[params["materijal"]])

    bodies = BUILDERS[template](params, log)
    errors, body_reports = [], []
    for b in bodies:
        rep = validate_mesh(b.vertices, b.faces, b.name)
        body_reports.append(rep)
        errors.extend(rep["errors"])
        for w in rep["warnings"]:
            log.warn(w)
    if errors:
        raise InvalidGeometry(errors, template)
    vertices, faces = merge_bodies(bodies)
    plate = validate_mesh(vertices, faces, "ploca")
    if not plate["ok"]:
        raise InvalidGeometry(plate["errors"], template)
    log.record("validacija", "mesh validan: watertight, manifold, orijentacija, volumen, zid, radni volumen")

    st = plate["stats"]
    metadata = {
        "fabagent_version": __version__,
        "kind": "print",
        "template": template,
        "printer": C.PRINTER_NAME,
        "units": C.UNITS,
        "params": params.values,
        "params_defaulted": sorted(params.defaulted),
        "dimensions_mm": st["bbox_mm"],
        "volume_mm3": st["volume_mm3"],
        "surface_area_mm2": st["area_mm2"],
        "mesh": {"vertices": st["vertices"], "faces": st["faces"], "watertight": st["watertight"],
                 "winding_consistent": st["winding_consistent"], "non_manifold_edges": st["non_manifold_edges"],
                 "min_wall_mm": st["wall_thickness"]["min_mm"], "bodies": st["bodies"]},
        "bodies": [{"name": b.name, **{k: r["stats"][k] for k in ("volume_mm3", "bbox_mm", "faces")},
                    "min_wall_mm": r["stats"]["wall_thickness"]["min_mm"]}
                   for b, r in zip(bodies, body_reports)],
        "estimate": estimate_print(st["volume_mm3"], st["area_mm2"], st["bbox_mm"]["size"][2],
                                   params["materijal"], params["ispuna_posto"], params["visina_sloja"]),
        "estimate_by_material": {
            m: estimate_print(st["volume_mm3"], st["area_mm2"], st["bbox_mm"]["size"][2], m,
                              params["ispuna_posto"], params["visina_sloja"])["mass_estimated_g"]
            for m in C.FILAMENTS},
        "validation": {"ok": True, "checks": ["watertight", "non_manifold", "winding", "positive_volume",
                                              "degenerate_faces", "min_wall_thickness", "build_volume"]},
        "warnings": log.warnings,
        "decision_log": log.entries,
    }
    return PrintResult(template, params, bodies, vertices, faces, metadata, log)


def _sha256(path: str) -> str:
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def validate_stl_file(path: str) -> dict:
    with open(path, "rb") as fh:
        data = fh.read()
    try:
        v, f = mesh_io.read_stl(data)
    except (ValueError, UnicodeDecodeError) as exc:
        return {"ok": False, "errors": [f"STL nije citljiv: {exc}"], "warnings": []}
    return validate_mesh(v, f, os.path.basename(path))


def validate_3mf_file(path: str) -> dict:
    with open(path, "rb") as fh:
        data = fh.read()
    try:
        bodies = mesh_io.read_3mf(data)
    except Exception as exc:  # zipfile/xml/value greske
        return {"ok": False, "errors": [f"3MF nije citljiv: {exc}"], "warnings": []}
    errors, warnings, stats = [], [], []
    for b in bodies:
        rep = validate_mesh(b.vertices, b.faces, b.name)
        errors += rep["errors"]
        warnings += rep["warnings"]
        stats.append(rep["stats"])
    return {"ok": not errors, "errors": errors, "warnings": warnings, "stats": stats}


def write_outputs(result: PrintResult, out_path: str) -> dict:
    """Upisuje <base>.stl, <base>.3mf, <base>.json i validira upisane fajlove."""
    low = out_path.lower()
    if not (low.endswith(".stl") or low.endswith(".3mf")):
        raise OutputError(f"izlaz mora imati ekstenziju .stl ili .3mf (dobijeno '{out_path}')")
    base = out_path[:-4]
    paths = {"stl": base + ".stl", "3mf": base + ".3mf", "json": base + ".json"}
    written: list[str] = []
    try:
        os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
        with open(paths["stl"], "wb") as fh:
            fh.write(mesh_io.stl_bytes(result.vertices, result.faces, result.template))
        written.append(paths["stl"])
        rep = validate_stl_file(paths["stl"])
        if not rep["ok"]:
            raise ValidationFailed(paths["stl"], rep["errors"])
        with open(paths["3mf"], "wb") as fh:
            fh.write(mesh_io.threemf_bytes(result.bodies))
        written.append(paths["3mf"])
        rep3 = validate_3mf_file(paths["3mf"])
        if not rep3["ok"]:
            raise ValidationFailed(paths["3mf"], rep3["errors"])
        meta = dict(result.metadata, files={
            k: {"path": paths[k], "sha256": _sha256(paths[k])} for k in ("stl", "3mf")})
        with open(paths["json"], "w", encoding="utf-8") as fh:
            json.dump(meta, fh, ensure_ascii=False, indent=2)
        written.append(paths["json"])
    except OSError as exc:
        _cleanup(written)
        raise OutputError(f"upis nije uspio: {exc}") from exc
    except Exception:
        _cleanup(written)
        raise
    result.log.record("izlaz", "fajlovi upisani i ponovo validirani sa diska", **paths)
    return paths


def _cleanup(paths: list[str]) -> None:
    for p in paths:
        try:
            os.remove(p)
        except OSError:
            pass

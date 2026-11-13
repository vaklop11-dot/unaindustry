"""Model laserskog dizajna + geometrijska pravila (koristi ga builder i validator).

Koordinate su u mm, ishodiste gore-lijevo, y prema dolje (isto kao SVG).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from fabagent.core import config as C
from fabagent.core import geometry2d as g2
from fabagent.laser.presets import MaterialPreset


@dataclass
class PathItem:
    layer: str                      # "CUT" | "ENGRAVE" | "SCORE"
    rings: list[np.ndarray]         # zatvorene konture
    label: str = ""

    def __post_init__(self) -> None:
        if self.layer not in C.LAYERS:
            raise ValueError(f"nepoznat sloj {self.layer}")


@dataclass
class LaserDesign:
    width: float
    height: float
    items: list[PathItem] = field(default_factory=list)
    guides: list[np.ndarray] = field(default_factory=list)   # samo preview, ne ide u SVG

    def rings(self, layer: str) -> list[np.ndarray]:
        return [r for it in self.items if it.layer == layer for r in it.rings]

    def add(self, layer: str, rings: list[np.ndarray], label: str = "") -> None:
        if rings:
            self.items.append(PathItem(layer, [np.asarray(r, dtype=float) for r in rings], label))


def _bbox_gap(a: np.ndarray, b: np.ndarray) -> float:
    amin, amax = a.min(axis=0), a.max(axis=0)
    bmin, bmax = b.min(axis=0), b.max(axis=0)
    dx = max(0.0, bmin[0] - amax[0], amin[0] - bmax[0])
    dy = max(0.0, bmin[1] - amax[1], amin[1] - bmax[1])
    return math.hypot(dx, dy)


def cut_nesting_depth(cut_rings: list[np.ndarray]) -> list[int]:
    """Broj drugih CUT kontura koje sadrze datu konturu (0 = vanjska, 1 = rupa...)."""
    depths = []
    for i, ring in enumerate(cut_rings):
        p = ring[:1]
        depth = 0
        for j, other in enumerate(cut_rings):
            if i != j and bool(g2.points_in_polygon(p, other)[0]):
                depth += 1
        depths.append(depth)
    return depths


def check_design(design: LaserDesign) -> list[str]:
    """Vraca listu problema (prazna = validno)."""
    problems: list[str] = []
    eps = C.GEOMETRY_EPS_MM
    if design.width <= 0 or design.height <= 0:
        return ["dimenzije dokumenta moraju biti pozitivne"]
    wa, ha = C.LASER_WORK_AREA_MM
    if design.width > wa + eps or design.height > ha + eps:
        problems.append(
            f"dokument {design.width:.2f}x{design.height:.2f} mm je veci od radne povrsine {wa}x{ha} mm"
        )
    for it in design.items:
        for ring in it.rings:
            if len(ring) < 3 or not np.isfinite(ring).all():
                problems.append(f"{it.layer}/{it.label}: kontura sa manje od 3 tacke ili neispravnim brojevima")
                continue
            if (ring[:, 0].min() < -eps or ring[:, 1].min() < -eps
                    or ring[:, 0].max() > design.width + eps or ring[:, 1].max() > design.height + eps):
                problems.append(f"{it.layer}/{it.label}: geometrija izlazi izvan dokumenta")
    if problems:
        return problems

    cut = design.rings("CUT")
    for i, ring in enumerate(cut):
        if not g2.is_simple(ring):
            problems.append(f"CUT kontura #{i} se sijece sama sa sobom")
    depths = cut_nesting_depth(cut)
    for i in range(len(cut)):
        for j in range(i + 1, len(cut)):
            if _bbox_gap(cut[i], cut[j]) >= C.LASER_MIN_WALL_MM:
                continue
            d = g2.polyline_min_distance(cut[i], cut[j])
            if d < C.LASER_MIN_WALL_MM - eps:
                problems.append(
                    f"CUT konture #{i} i #{j}: razmak {d:.3f} mm < min zid {C.LASER_MIN_WALL_MM} mm"
                )
    for i, (ring, depth) in enumerate(zip(cut, depths)):
        if depth % 2 == 1:   # rupa
            w = ring.max(axis=0) - ring.min(axis=0)
            if w.min() < C.LASER_MIN_HOLE_DIAMETER_MM - eps:
                problems.append(
                    f"CUT rupa #{i}: najmanja dimenzija {w.min():.3f} mm < min {C.LASER_MIN_HOLE_DIAMETER_MM} mm"
                )

    for layer in ("ENGRAVE", "SCORE"):
        rings = design.rings(layer)
        for k, ring in enumerate(rings):
            for i, c in enumerate(cut):
                if _bbox_gap(ring, c) >= C.CUT_ENGRAVE_CLEARANCE_MM:
                    continue
                d = g2.polyline_min_distance(ring, c)
                if d < C.CUT_ENGRAVE_CLEARANCE_MM - eps:
                    problems.append(
                        f"{layer} kontura #{k} je {d:.3f} mm od CUT konture #{i} "
                        f"(min {C.CUT_ENGRAVE_CLEARANCE_MM} mm) - preklapanje/premalen razmak"
                    )
            if cut:
                inside = sum(bool(g2.points_in_polygon(ring[:1], c)[0]) for c in cut)
                if inside % 2 == 0:
                    problems.append(f"{layer} kontura #{k} nije na materijalu (izvan dijela ili u rupi)")
    return problems


def layer_stats(design: LaserDesign) -> dict:
    stats = {}
    for layer in C.LAYERS:
        items = [it for it in design.items if it.layer == layer]
        rings = [r for it in items for r in it.rings]
        entry = {"paths": len(items), "rings": len(rings),
                 "length_mm": round(sum(g2.perimeter(r) for r in rings), 3)}
        if rings:
            x0, y0, x1, y1 = g2.bbox(rings)
            entry["bbox_mm"] = [round(v, 3) for v in (x0, y0, x1, y1)]
        if layer == "ENGRAVE":
            entry["area_mm2"] = round(sum(abs(sum(g2.signed_area(r) for r in it.rings)) for it in items), 3)
        stats[layer] = entry
    return stats


def estimate_time(design: LaserDesign, preset: MaterialPreset) -> dict:
    """Procjena vremena po sloju prema presetu (sekunde)."""
    result: dict[str, float] = {}
    for layer in ("CUT", "SCORE"):
        rings = design.rings(layer)
        if not rings:
            result[layer] = 0.0
            continue
        s = preset.setting(layer)
        length = sum(g2.perimeter(r) for r in rings)
        result[layer] = s.passes * (length / s.speed_mm_s + len(rings) * C.LASER_CONTOUR_OVERHEAD_S)
    rings = design.rings("ENGRAVE")
    if rings:
        s = preset.setting("ENGRAVE")
        x0, y0, x1, y1 = g2.bbox(rings)
        rows = math.ceil((y1 - y0) / s.line_interval_mm)
        result["ENGRAVE"] = s.passes * rows * ((x1 - x0) + 2 * C.ENGRAVE_OVERSCAN_MM) / s.speed_mm_s
    else:
        result["ENGRAVE"] = 0.0
    result = {k: round(v, 1) for k, v in result.items()}
    result["total"] = round(sum(result.values()), 1)
    return result

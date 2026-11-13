"""Zajednicki pomocnici laserskih sablona."""

from __future__ import annotations

import numpy as np

from fabagent.core import config as C
from fabagent.core import geometry2d as g2
from fabagent.core.errors import InvalidGeometry


def vertical_entry_y(outline: np.ndarray, x: float) -> float:
    """Najmanja y koordinata (najvisa tacka u y-dolje) gdje vertikala x=const sijece konturu."""
    a = outline
    b = np.roll(outline, -1, axis=0)
    ys = []
    for p, q in zip(a, b):
        if (p[0] - x) * (q[0] - x) <= 0 and abs(q[0] - p[0]) > C.GEOMETRY_EPS_MM:
            t = (x - p[0]) / (q[0] - p[0])
            ys.append(p[1] + t * (q[1] - p[1]))
        elif abs(p[0] - x) <= C.GEOMETRY_EPS_MM:
            ys.append(p[1])
    if not ys:
        raise InvalidGeometry(f"vertikala x={x:.3f} ne sijece konturu")
    return min(ys)


def horizontal_span(outline: np.ndarray, y: float) -> float:
    """Sirina konture na visini y (razlika krajnjih presjeka)."""
    a = outline
    b = np.roll(outline, -1, axis=0)
    xs = []
    for p, q in zip(a, b):
        if (p[1] - y) * (q[1] - y) <= 0 and abs(q[1] - p[1]) > C.GEOMETRY_EPS_MM:
            t = (y - p[1]) / (q[1] - p[1])
            xs.append(p[0] + t * (q[0] - p[0]))
    return (max(xs) - min(xs)) if len(xs) >= 2 else 0.0


def text_clearance_problems(text_rings: list[np.ndarray], outline: np.ndarray | None,
                            holes: list[np.ndarray], margin: float, what: str = "tekst") -> list[str]:
    """Provjera da je tekst na materijalu, >= margin od ivice i od rupa."""
    problems = []
    if not text_rings:
        return problems
    pts = np.vstack([r[:1] for r in text_rings])
    if outline is not None:
        inside = g2.points_in_polygon(pts, outline)
        if not inside.all():
            problems.append(f"{what} izlazi izvan oblika")
        else:
            d = min(g2.polyline_min_distance(r, outline) for r in text_rings)
            if d < margin - C.GEOMETRY_EPS_MM:
                problems.append(f"{what} je {d:.2f} mm od ivice (min {margin} mm)")
    for k, hole in enumerate(holes):
        if g2.points_in_polygon(pts, hole).any():
            problems.append(f"{what} prelazi preko rupe #{k + 1}")
            continue
        d = min(g2.polyline_min_distance(r, hole) for r in text_rings)
        if d < margin - C.GEOMETRY_EPS_MM:
            problems.append(f"{what} je {d:.2f} mm od rupe #{k + 1} (min {margin} mm)")
    return problems

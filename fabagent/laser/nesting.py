"""Raspored dijelova na ploce (nesting) - MaxRects, deterministicki.

Svaki dio je skup zatvorenih kontura u lokalnim koordinatama. Raspored koristi
granicni pravougaonik dijela + razmak; dio se moze rotirati za 90 stepeni. Ako svi
dijelovi ne stanu na jednu plocu, otvara se sljedeca (vise SVG ploca).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from fabagent.core import config as C
from fabagent.core.errors import InvalidGeometry


@dataclass
class Part:
    name: str
    cut: list[np.ndarray]                       # CUT konture (vanjska + rupe)
    engrave: list[np.ndarray] = field(default_factory=list)
    allow_rotate: bool = True

    def bounds(self) -> tuple[np.ndarray, np.ndarray]:
        pts = np.vstack(self.cut + self.engrave)
        return pts.min(axis=0), pts.max(axis=0)


@dataclass
class Placement:
    part: Part
    sheet: int
    x: float
    y: float
    rotated: bool

    def transform(self, ring: np.ndarray) -> np.ndarray:
        lo, hi = self.part.bounds()
        local = ring - lo
        if self.rotated:   # 90 deg: (x, y) -> (h - y, x)
            h = (hi - lo)[1]
            local = np.column_stack([h - local[:, 1], local[:, 0]])
        return local + np.array([self.x, self.y])


@dataclass
class _Rect:
    x: float
    y: float
    w: float
    h: float


class _MaxRectsBin:
    def __init__(self, w: float, h: float):
        self.w, self.h = w, h
        self.free = [_Rect(0, 0, w, h)]

    def find(self, w: float, h: float):
        """Best-short-side-fit; vraca (x, y) ili None."""
        best = None
        for r in self.free:
            if w <= r.w + 1e-9 and h <= r.h + 1e-9:
                key = (min(r.w - w, r.h - h), max(r.w - w, r.h - h), r.y, r.x)
                if best is None or key < best[0]:
                    best = (key, r.x, r.y)
        return None if best is None else (best[1], best[2])

    def place(self, x: float, y: float, w: float, h: float) -> None:
        used = _Rect(x, y, w, h)
        new_free = []
        for r in self.free:
            if (used.x >= r.x + r.w or used.x + used.w <= r.x or
                    used.y >= r.y + r.h or used.y + used.h <= r.y):
                new_free.append(r)
                continue
            if used.x > r.x:
                new_free.append(_Rect(r.x, r.y, used.x - r.x, r.h))
            if used.x + used.w < r.x + r.w:
                new_free.append(_Rect(used.x + used.w, r.y, r.x + r.w - used.x - used.w, r.h))
            if used.y > r.y:
                new_free.append(_Rect(r.x, r.y, r.w, used.y - r.y))
            if used.y + used.h < r.y + r.h:
                new_free.append(_Rect(r.x, used.y + used.h, r.w, r.y + r.h - used.y - used.h))
        # ukloni pravougaonike sadrzane u drugima
        pruned = []
        for i, a in enumerate(new_free):
            contained = False
            for j, b in enumerate(new_free):
                if i != j and a.x >= b.x - 1e-9 and a.y >= b.y - 1e-9 and \
                        a.x + a.w <= b.x + b.w + 1e-9 and a.y + a.h <= b.y + b.h + 1e-9:
                    if (a.x, a.y, a.w, a.h) != (b.x, b.y, b.w, b.h) or i > j:
                        contained = True
                        break
            if not contained and a.w > 1e-9 and a.h > 1e-9:
                pruned.append(a)
        self.free = pruned


def pack(parts: list[Part], sheet_w: float, sheet_h: float, gap: float, padding: float) -> tuple[list[Placement], int]:
    """Rasporedjuje dijelove. Vraca (placements, broj_ploca). Koordinate su na ploci
    (ukljucujuci padding). Redoslijed: najveca povrsina prvo (stabilno po imenu)."""
    usable_w = sheet_w - 2 * padding + gap
    usable_h = sheet_h - 2 * padding + gap
    order = sorted(range(len(parts)), key=lambda i: (-float(np.prod(parts[i].bounds()[1] - parts[i].bounds()[0])),
                                                     parts[i].name, i))
    bins: list[_MaxRectsBin] = []
    placements: list[Placement | None] = [None] * len(parts)
    for i in order:
        p = parts[i]
        lo, hi = p.bounds()
        w, h = (hi - lo) + gap
        options = [(w, h, False)] + ([(h, w, True)] if p.allow_rotate and abs(w - h) > 1e-9 else [])
        if all(ow > usable_w + 1e-9 or oh > usable_h + 1e-9 for ow, oh, _ in options):
            raise InvalidGeometry(
                f"dio '{p.name}' ({(hi - lo)[0]:.1f} x {(hi - lo)[1]:.1f} mm) je veci od ploce "
                f"{sheet_w:g} x {sheet_h:g} mm")
        placed = False
        for s, b in enumerate(bins):
            best = None
            for ow, oh, rot in options:
                pos = b.find(ow, oh)
                if pos is not None:
                    key = (pos[1], pos[0], rot)
                    if best is None or key < best[0]:
                        best = (key, pos, ow, oh, rot)
            if best is not None:
                _, (x, y), ow, oh, rot = best
                b.place(x, y, ow, oh)
                placements[i] = Placement(p, s, x + padding, y + padding, rot)
                placed = True
                break
        if not placed:
            b = _MaxRectsBin(usable_w, usable_h)
            bins.append(b)
            for ow, oh, rot in options:
                pos = b.find(ow, oh)
                if pos is not None:
                    b.place(pos[0], pos[1], ow, oh)
                    placements[i] = Placement(p, len(bins) - 1, pos[0] + padding, pos[1] + padding, rot)
                    break
    return [pl for pl in placements if pl is not None], len(bins)

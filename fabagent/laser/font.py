"""Blok font ugradjen u kod: tekst -> zatvorene konture (bez zavisnosti od fontova).

Svaki znak je mreza 5 x 7 celija (velika slova), plus 2 reda iznad za kvacice
(C, C', S, Z). Popunjene celije se spajaju u konture (``grid_to_polygons``), pa je
rezultat cist fill-engrave bez preklapanja. Nepodrzan znak je greska - nema zamjene.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from fabagent.core import config as C
from fabagent.core.errors import InvalidParam
from fabagent.core.geometry2d import grid_to_polygons

_CARON = ("01010", "00100")
_ACUTE = ("00010", "00100")
_NONE = ("00000", "00000")

# 7 redova po znaku (od vrha), "1" = popunjena celija.
_BASE: dict[str, tuple[str, ...]] = {
    "A": ("01110", "10001", "10001", "11111", "10001", "10001", "10001"),
    "B": ("11110", "10001", "10001", "11110", "10001", "10001", "11110"),
    "C": ("01110", "10001", "10000", "10000", "10000", "10001", "01110"),
    "D": ("11100", "10010", "10001", "10001", "10001", "10010", "11100"),
    "E": ("11111", "10000", "10000", "11110", "10000", "10000", "11111"),
    "F": ("11111", "10000", "10000", "11110", "10000", "10000", "10000"),
    "G": ("01110", "10001", "10000", "10111", "10001", "10001", "01111"),
    "H": ("10001", "10001", "10001", "11111", "10001", "10001", "10001"),
    "I": ("01110", "00100", "00100", "00100", "00100", "00100", "01110"),
    "J": ("00111", "00010", "00010", "00010", "00010", "10010", "01100"),
    "K": ("10001", "10010", "10100", "11000", "10100", "10010", "10001"),
    "L": ("10000", "10000", "10000", "10000", "10000", "10000", "11111"),
    "M": ("10001", "11011", "10101", "10101", "10001", "10001", "10001"),
    "N": ("10001", "10001", "11001", "10101", "10011", "10001", "10001"),
    "O": ("01110", "10001", "10001", "10001", "10001", "10001", "01110"),
    "P": ("11110", "10001", "10001", "11110", "10000", "10000", "10000"),
    "Q": ("01110", "10001", "10001", "10001", "10101", "10010", "01101"),
    "R": ("11110", "10001", "10001", "11110", "10100", "10010", "10001"),
    "S": ("01111", "10000", "10000", "01110", "00001", "00001", "11110"),
    "T": ("11111", "00100", "00100", "00100", "00100", "00100", "00100"),
    "U": ("10001", "10001", "10001", "10001", "10001", "10001", "01110"),
    "V": ("10001", "10001", "10001", "10001", "10001", "01010", "00100"),
    "W": ("10001", "10001", "10001", "10101", "10101", "10101", "01010"),
    "X": ("10001", "10001", "01010", "00100", "01010", "10001", "10001"),
    "Y": ("10001", "10001", "10001", "01010", "00100", "00100", "00100"),
    "Z": ("11111", "00001", "00010", "00100", "01000", "10000", "11111"),
    "Đ": ("11100", "10010", "10001", "11101", "10001", "10010", "11100"),  # Dj
    "0": ("01110", "10001", "10011", "10101", "11001", "10001", "01110"),
    "1": ("00100", "01100", "00100", "00100", "00100", "00100", "01110"),
    "2": ("01110", "10001", "00001", "00010", "00100", "01000", "11111"),
    "3": ("11111", "00010", "00100", "00010", "00001", "10001", "01110"),
    "4": ("00010", "00110", "01010", "10010", "11111", "00010", "00010"),
    "5": ("11111", "10000", "11110", "00001", "00001", "10001", "01110"),
    "6": ("00110", "01000", "10000", "11110", "10001", "10001", "01110"),
    "7": ("11111", "00001", "00010", "00100", "01000", "01000", "01000"),
    "8": ("01110", "10001", "10001", "01110", "10001", "10001", "01110"),
    "9": ("01110", "10001", "10001", "01111", "00001", "00010", "01100"),
    " ": ("00000",) * 7,
    "-": ("00000", "00000", "00000", "11111", "00000", "00000", "00000"),
    ".": ("00000", "00000", "00000", "00000", "00000", "01100", "01100"),
    ":": ("00000", "01100", "01100", "00000", "01100", "01100", "00000"),
    "/": ("00000", "00001", "00010", "00100", "01000", "10000", "00000"),
    "#": ("01010", "01010", "11111", "01010", "11111", "01010", "01010"),
    "+": ("00000", "00100", "00100", "11111", "00100", "00100", "00000"),
    ",": ("00000", "00000", "00000", "00000", "01100", "00100", "01000"),
    "!": ("00100", "00100", "00100", "00100", "00100", "00000", "00100"),
    "?": ("01110", "10001", "00001", "00010", "00100", "00000", "00100"),
    "(": ("00010", "00100", "01000", "01000", "01000", "00100", "00010"),
    ")": ("01000", "00100", "00010", "00010", "00010", "00100", "01000"),
    "_": ("00000", "00000", "00000", "00000", "00000", "00000", "11111"),
    "&": ("01100", "10010", "10100", "01000", "10101", "10010", "01101"),
}

# Slova sa kvacicom: (osnovno slovo, kvacica)
_ACCENTED = {
    "Č": ("C", _CARON),  # C sa kvacicom
    "Ć": ("C", _ACUTE),  # C sa crticom
    "Š": ("S", _CARON),
    "Ž": ("Z", _CARON),
}


def _glyph_rows(ch: str) -> tuple[str, ...]:
    if ch in _ACCENTED:
        base, accent = _ACCENTED[ch]
        return accent + _BASE[base]
    return _NONE + _BASE[ch]


GLYPHS: dict[str, np.ndarray] = {}
for _ch in list(_BASE) + list(_ACCENTED):
    _rows = _glyph_rows(_ch)
    GLYPHS[_ch] = np.array([[c == "1" for c in row] for row in _rows], dtype=bool)


def supported_chars() -> str:
    return "".join(sorted(GLYPHS))


@dataclass
class TextGeometry:
    polygons: list[np.ndarray]   # zatvorene konture, koordinate u mm
    width: float                 # sirina linije (od lijeve ivice prvog do desne ivice zadnjeg znaka)
    cap_height: float
    cell: float
    top: float                   # najvisa y koordinata (negativno ako ima kvacica)
    bottom: float

    @property
    def area(self) -> float:
        return self._area

    def bbox(self) -> tuple[float, float, float, float]:
        return (0.0, self.top, self.width, self.bottom)


def render_text(text: str, cap_height: float, spacing_cells: float) -> TextGeometry:
    """Tekst u konture. Vrh velikih slova je y=0, lijeva ivica x=0 (y prema dolje)."""
    bad = sorted({c for c in text if c not in GLYPHS})
    if bad:
        raise InvalidParam(f"font ne podrzava znakove {bad!r}; podrzano: {C.FONT_CHARSET!r}")
    if not text:
        raise InvalidParam("tekst je prazan")
    cell = cap_height / C.FONT_CAP_ROWS
    if cell < C.LASER_MIN_ENGRAVE_FEATURE_MM:
        raise InvalidParam(
            f"visina teksta {cap_height:g} mm daje celiju {cell:.3f} mm < min "
            f"{C.LASER_MIN_ENGRAVE_FEATURE_MM} mm (povecaj visinu teksta)"
        )
    polys: list[np.ndarray] = []
    advance = (C.FONT_CELL_COLS + spacing_cells) * cell
    filled_cells = 0
    has_accent = False
    for i, ch in enumerate(text):
        grid = GLYPHS[ch]
        filled_cells += int(grid.sum())
        if grid[: C.FONT_ACCENT_ROWS].any():
            has_accent = True
        x0 = i * advance
        y0 = -C.FONT_ACCENT_ROWS * cell
        polys.extend(grid_to_polygons(grid, cell, x0, y0))
    width = len(text) * C.FONT_CELL_COLS * cell + (len(text) - 1) * spacing_cells * cell
    top = -C.FONT_ACCENT_ROWS * cell if has_accent else 0.0
    geo = TextGeometry(polys, width, cap_height, cell, top, cap_height)
    geo._area = filled_cells * cell * cell
    return geo


def place_text(geo: TextGeometry, cx: float, cy_cap_center: float) -> list[np.ndarray]:
    """Pomjera tekst tako da je centar (sirina, visina velikih slova) u (cx, cy)."""
    dx = cx - geo.width / 2
    dy = cy_cap_center - geo.cap_height / 2
    return [p + np.array([dx, dy]) for p in geo.polygons]

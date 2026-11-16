"""Raster graviranje slike (LightBurn "Image" mod): Threshold, Dither, Grayscale, Negative.

Slika se preuzorkuje tako da je 1 piksel = 1 linija graviranja (interval u mm). Rezultat je
mapa snage 0..1 (udio snage sloja) koja ide direktno u G-code red po red (S po pikselu).
Dither: Floyd-Steinberg, Jarvis-Judice-Ninke, Stucki (error diffusion, deterministicki).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from fabagent.core import config as C
from fabagent.core.errors import InvalidParam, MissingParam, OutOfRange, RangeViolation, ValidationFailed

# (dx, dy, tezina), djelilac
KERNELS = {
    "floyd-steinberg": ([(1, 0, 7), (-1, 1, 3), (0, 1, 5), (1, 1, 1)], 16),
    "jarvis": ([(1, 0, 7), (2, 0, 5), (-2, 1, 3), (-1, 1, 5), (0, 1, 7), (1, 1, 5), (2, 1, 3),
                (-2, 2, 1), (-1, 2, 3), (0, 2, 5), (1, 2, 3), (2, 2, 1)], 48),
    "stucki": ([(1, 0, 8), (2, 0, 4), (-2, 1, 2), (-1, 1, 4), (0, 1, 8), (1, 1, 4), (2, 1, 2),
                (-2, 2, 1), (-1, 2, 2), (0, 2, 4), (1, 2, 2), (2, 2, 1)], 42),
}


@dataclass(frozen=True)
class RasterOptions:
    mode: str                       # threshold | dither | grayscale
    width_mm: float
    interval_mm: float              # velicina piksela = razmak linija
    negative: bool = False
    threshold: int | None = None    # threshold mod
    algorithm: str | None = None    # dither mod
    power_min_pct: float = 0.0      # grayscale: snaga za bijelo (udio snage sloja)

    def as_dict(self) -> dict:
        return dict(self.__dict__)


def validate(o: RasterOptions) -> None:
    if o.mode not in C.RASTER_MODES:
        raise InvalidParam(f"rezim_slike: '{o.mode}' nije dozvoljeno, izbor: {list(C.RASTER_MODES)}")
    missing = []
    if o.mode == "threshold" and o.threshold is None:
        missing.append("prag")
    if o.mode == "dither" and not o.algorithm:
        missing.append("algoritam")
    if missing:
        raise MissingParam("raster", missing)
    if o.mode == "dither" and o.algorithm not in C.DITHER_ALGORITHMS:
        raise InvalidParam(f"algoritam: '{o.algorithm}' nije dozvoljeno, izbor: {list(C.DITHER_ALGORITHMS)}")
    v = []
    if not (C.IMAGE_WIDTH_MIN_MM <= o.width_mm <= C.LASER_WORK_AREA_MM[0]):
        v.append(RangeViolation("sirina_mm", o.width_mm, C.IMAGE_WIDTH_MIN_MM, C.LASER_WORK_AREA_MM[0]))
    if not (C.LAYER_INTERVAL_RANGE_MM[0] <= o.interval_mm <= C.LAYER_INTERVAL_RANGE_MM[1]):
        v.append(RangeViolation("interval_mm", o.interval_mm, *C.LAYER_INTERVAL_RANGE_MM))
    if o.threshold is not None and not (C.IMAGE_THRESHOLD_MIN <= o.threshold <= C.IMAGE_THRESHOLD_MAX):
        v.append(RangeViolation("prag", o.threshold, C.IMAGE_THRESHOLD_MIN, C.IMAGE_THRESHOLD_MAX, ""))
    if not (C.RASTER_MIN_POWER_PCT_RANGE[0] <= o.power_min_pct <= C.RASTER_MIN_POWER_PCT_RANGE[1]):
        v.append(RangeViolation("min_snaga_posto", o.power_min_pct, *C.RASTER_MIN_POWER_PCT_RANGE, "%"))
    if v:
        raise OutOfRange("raster", v)


def resample(gray: np.ndarray, width_mm: float, interval_mm: float) -> np.ndarray:
    import cv2
    h, w = gray.shape
    nx = max(1, int(round(width_mm / interval_mm)))
    ny = max(1, int(round(h * width_mm / w / interval_mm)))
    if nx * ny > C.RASTER_MAX_PIXELS:
        raise ValidationFailed("slika", [
            f"{width_mm:g} mm uz interval {interval_mm:g} mm daje {nx}x{ny} = {nx * ny:,} linija-piksela "
            f"(max {C.RASTER_MAX_PIXELS:,}) - smanji sirinu ili povecaj interval"])
    interp = cv2.INTER_AREA if nx < w else cv2.INTER_LINEAR
    return cv2.resize(gray, (nx, ny), interpolation=interp)


def error_diffusion(gray: np.ndarray, algorithm: str) -> np.ndarray:
    """Vraca bool masku: True = pali laser (tamno)."""
    kernel, div = KERNELS[algorithm]
    h, w = gray.shape
    buf = [list(map(float, row)) for row in gray]
    out = np.zeros((h, w), dtype=bool)
    for y in range(h):
        row = buf[y]
        for x in range(w):
            old = row[x]
            new = 0.0 if old < 128.0 else 255.0
            err = old - new
            if new == 0.0:
                out[y, x] = True
            if err:
                for dx, dy, wt in kernel:
                    xx, yy = x + dx, y + dy
                    if 0 <= xx < w and yy < h:
                        buf[yy][xx] += err * wt / div
    return out


def power_map(gray_full: np.ndarray, o: RasterOptions) -> np.ndarray:
    """Mapa snage 0..1 (udio snage sloja), velicine (redova, kolona) = linije x pikseli."""
    validate(o)
    g = resample(gray_full, o.width_mm, o.interval_mm)
    if o.negative:
        g = 255 - g
    if o.mode == "threshold":
        return (g < o.threshold).astype(np.float64)
    if o.mode == "dither":
        return error_diffusion(g, o.algorithm).astype(np.float64)
    darkness = 1.0 - g.astype(np.float64) / 255.0              # grayscale: tamnije = jace
    lo = o.power_min_pct / 100.0
    p = lo + (1.0 - lo) * darkness
    p[darkness <= 0] = 0.0                                     # cisto bijelo se ne pali
    return p


def preview_png(pmap: np.ndarray) -> bytes:
    import cv2
    img = np.clip(np.round(255 * (1 - pmap)), 0, 255).astype(np.uint8)
    return cv2.imencode(".png", img)[1].tobytes()


def power_map_from_png(data: bytes) -> np.ndarray:
    import cv2
    img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_GRAYSCALE)
    return 1.0 - img.astype(np.float64) / 255.0


def raster_gcode(pmap: np.ndarray, interval_mm: float, speed_mm_s: float, power_pct: float, passes: int,
                 offset: tuple[float, float], title: str = "") -> tuple[str, dict]:
    """Red po red, dvosmjerno; S se mijenja samo kada se promijeni (run-length)."""
    from fabagent.core.errors import OutOfRange as OOR
    ny, nx = pmap.shape
    W, H = C.LASER_WORK_AREA_MM
    ox, oy = offset
    over = C.ENGRAVE_OVERSCAN_MM
    width, height = nx * interval_mm, ny * interval_mm
    # Prelet je zalet glave prije i poslije svake linije popune: dioda je ugasena,
    # ali glava mora fizicki izaci van slike. Zato X ne smije biti manji od preleta
    # ni blizi desnoj ivici od preleta - u Y preleta nema.
    # Ranije je poruka glasila "pozicija=0 izvan opsega [-inf, +inf]" i operater iz
    # nje nije mogao znati ni koji broj je los ni koji da upise.
    x_min, x_max = over, W - width - over
    y_min, y_max = 0.0, H - height
    if x_max < x_min or y_max < y_min:
        raise OOR("pozicija", [RangeViolation(
            "slika", max(width, height), None, None, "mm",
            f"slika {width:.1f}x{height:.1f} mm ne staje na radnu povrsinu "
            f"{W:g}x{H:g} mm ni u jednom polozaju (prelet {over:g} mm sa svake strane)")])
    if not (x_min <= ox <= x_max):
        raise OOR("X", [RangeViolation(
            "X", ox, round(x_min, 1), round(x_max, 1), "mm",
            f"slika je siroka {width:.1f} mm, a glava uzima jos {over:g} mm preleta sa "
            f"svake strane; upisi X izmedju {x_min:.1f} i {x_max:.1f} mm")])
    if not (y_min <= oy <= y_max):
        raise OOR("Y", [RangeViolation(
            "Y", oy, round(y_min, 1), round(y_max, 1), "mm",
            f"slika je visoka {height:.1f} mm; upisi Y izmedju {y_min:.1f} i {y_max:.1f} mm")])
    smax = power_pct / 100.0 * C.GRBL_S_MAX
    S = np.round(pmap * smax).astype(int)
    feed = speed_mm_s * 60.0
    d = C.GCODE_DECIMALS
    lines = [f"; FabAgent raster - {title}".rstrip(" -"), f"; {nx}x{ny} px, interval {interval_mm:g} mm, "
             f"{speed_mm_s:g} mm/s, max {power_pct:g} %", "G21", "G90", "M5", f"{C.GRBL_LASER_MODE_CMD} S0"]
    burn_px = 0
    burn_time = 0.0
    for _ in range(passes):
        forward = True
        for r in range(ny):
            row = S[r]
            nz = np.nonzero(row)[0]
            if len(nz) == 0:
                continue
            a, b = int(nz[0]), int(nz[-1]) + 1
            Y = H - (oy + (r + 0.5) * interval_mm)
            xs_edges = ox + np.arange(nx + 1) * interval_mm
            if forward:
                lines.append(f"G0 X{xs_edges[a] - over:.{d}f} Y{Y:.{d}f}")
                lines.append(f"G1 X{xs_edges[a]:.{d}f} S0 F{feed:.0f}")
                k = a
                while k < b:
                    e = k
                    while e < b and row[e] == row[k]:
                        e += 1
                    lines.append(f"G1 X{xs_edges[e]:.{d}f} S{row[k]}")
                    k = e
                lines.append(f"G1 X{xs_edges[b] + over:.{d}f} S0")
            else:
                lines.append(f"G0 X{xs_edges[b] + over:.{d}f} Y{Y:.{d}f}")
                lines.append(f"G1 X{xs_edges[b]:.{d}f} S0 F{feed:.0f}")
                k = b
                while k > a:
                    e = k
                    while e > a and row[e - 1] == row[k - 1]:
                        e -= 1
                    lines.append(f"G1 X{xs_edges[e]:.{d}f} S{row[k - 1]}")
                    k = e
                lines.append(f"G1 X{xs_edges[a] - over:.{d}f} S0")
            burn_px += int(np.count_nonzero(row))
            burn_time += ((b - a) * interval_mm + 2 * over) / speed_mm_s
            forward = not forward
    lines += ["M5 ; laser ugasen", "G0 X0 Y0", "M2"]
    stats = {"lines": len(lines), "pixels": [nx, ny], "burn_pixels": burn_px,
             "size_mm": [round(width, 3), round(height, 3)], "time_s": round(burn_time, 1),
             "offset_mm": [ox, oy]}
    return "\n".join(lines) + "\n", stats


def estimate_time_s(pmap: np.ndarray, interval_mm: float, speed_mm_s: float) -> float:
    total = 0.0
    for row in pmap:
        nz = np.nonzero(row)[0]
        if len(nz):
            total += ((nz[-1] - nz[0] + 1) * interval_mm + 2 * C.ENGRAVE_OVERSCAN_MM) / speed_mm_s
    return round(total, 1)


def rows_needed(height_mm: float, interval_mm: float) -> int:
    return int(math.ceil(height_mm / interval_mm))

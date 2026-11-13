"""USB kamera iznad radne povrsine + kalibracija perspektive (OpenCV).

Izvor slike:
  * cijeli broj ("0", "1", ...)  -> USB kamera (cv2.VideoCapture)
  * putanja do slike              -> staticka slika (za testiranje bez kamere)
Izvor se bira varijablom okruzenja FABAGENT_CAMERA (config.CAMERA_SOURCE_ENV).

Kalibracija: korisnik klikne 4 ugla radne povrsine na slici kamere, redom
  1) X=0,   Y=0      (gore lijevo u koordinatama lasera / SVG-a)
  2) X=max, Y=0      (gore desno)
  3) X=max, Y=max    (dolje desno)
  4) X=0,   Y=max    (dolje lijevo)
pa se racuna homografija cv2.getPerspectiveTransform (pikseli kamere -> mm lasera).
Tacke se provjeravaju (unutar slike, konveksan cetverougao u tom redoslijedu,
dovoljna povrsina) - pogresan redoslijed je greska, ne "popravlja" se.
"""

from __future__ import annotations

import json
import os
import sys
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np

from fabagent.core import config as C
from fabagent.core.errors import CameraUnavailable, InvalidGeometry, InvalidParam

CORNER_NAMES = ("gore lijevo (X=0, Y=0)", "gore desno (X=max, Y=0)",
                "dolje desno (X=max, Y=max)", "dolje lijevo (X=0, Y=max)")
CALIBRATION_FILE = "kalibracija.json"


def _cv2():
    try:
        import cv2
    except ImportError as exc:
        raise CameraUnavailable("OpenCV nije instaliran (pip install opencv-python-headless)") from exc
    return cv2


# ------------------------------------------------------------------ izvor slike
class CameraSource:
    def __init__(self, spec: str | None = None):
        self.spec = (spec if spec is not None else os.environ.get(C.CAMERA_SOURCE_ENV, C.CAMERA_DEFAULT_SOURCE)).strip()
        self.index = int(self.spec) if self.spec.isdigit() else None
        self._cap = None
        self._lock = threading.Lock()

    def describe(self) -> str:
        return f"USB kamera #{self.index}" if self.index is not None else f"staticka slika: {self.spec}"

    def read(self) -> np.ndarray:
        cv2 = _cv2()
        if self.index is None:
            if not Path(self.spec).is_file():
                raise CameraUnavailable(f"izvor kamere '{self.spec}' nije ni indeks kamere ni postojeca slika")
            frame = cv2.imread(self.spec, cv2.IMREAD_COLOR)
            if frame is None:
                raise CameraUnavailable(f"slika '{self.spec}' nije citljiva")
            return frame
        with self._lock:
            if self._cap is None or not self._cap.isOpened():
                backend = cv2.CAP_DSHOW if sys.platform == "win32" else cv2.CAP_ANY
                self._cap = cv2.VideoCapture(self.index, backend)
                if not self._cap.isOpened():
                    self._cap = None
                    raise CameraUnavailable(f"kamera #{self.index} se ne moze otvoriti (prikljucena? koristi je drugi program?)")
            ok, frame = self._cap.read()
        if not ok or frame is None:
            raise CameraUnavailable(f"kamera #{self.index} ne vraca sliku")
        return frame

    def release(self) -> None:
        with self._lock:
            if self._cap is not None:
                self._cap.release()
                self._cap = None


def encode_jpeg(frame: np.ndarray) -> bytes:
    cv2 = _cv2()
    ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), int(C.CAMERA_JPEG_QUALITY)])
    if not ok:
        raise CameraUnavailable("JPEG kodiranje slike nije uspjelo")
    return buf.tobytes()


# ------------------------------------------------------------------ kalibracija
@dataclass
class Calibration:
    points_px: list[list[float]]
    frame_size: list[int]           # [sirina, visina] u pikselima kamere
    area_mm: list[float]            # [X, Y] radne povrsine
    matrix: list[list[float]]       # 3x3, pikseli kamere -> mm lasera
    created: str
    source: str = ""

    @property
    def M(self) -> np.ndarray:
        return np.array(self.matrix, dtype=np.float64)

    def to_dict(self) -> dict:
        return dict(self.__dict__)


def _validate_points(pts: np.ndarray, frame_size: tuple[int, int]) -> None:
    if pts.shape != (4, 2) or not np.isfinite(pts).all():
        raise InvalidParam("kalibracija: potrebne su tacno 4 tacke (x, y)")
    w, h = frame_size
    for i, (x, y) in enumerate(pts):
        if not (0 <= x <= w and 0 <= y <= h):
            raise InvalidParam(f"kalibracija: tacka {i + 1} ({x:.0f}, {y:.0f}) je izvan slike {w}x{h}")
    crosses = []
    for i in range(4):
        a, b, c = pts[i], pts[(i + 1) % 4], pts[(i + 2) % 4]
        crosses.append((b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0]))
    if not all(cr > 0 for cr in crosses):
        raise InvalidGeometry(
            "tacke ne cine konveksan cetverougao u trazenom redoslijedu (" + ", ".join(CORNER_NAMES) +
            ") - klikni ponovo, redom u smjeru kazaljke na satu pocevsi od X=0, Y=0", "kalibracija")
    x, y = pts[:, 0], pts[:, 1]
    area = 0.5 * float(np.dot(x, np.roll(y, -1)) - np.dot(np.roll(x, -1), y))
    if area < C.CAMERA_MIN_QUAD_AREA_PX:
        raise InvalidGeometry(f"povrsina cetverougla je {area:.0f} px2 < {C.CAMERA_MIN_QUAD_AREA_PX:.0f} px2 - "
                              "tacke su preblizu", "kalibracija")


def compute_calibration(points_px, frame_size: tuple[int, int],
                        area_mm: tuple[float, float] = C.LASER_WORK_AREA_MM, source: str = "") -> Calibration:
    cv2 = _cv2()
    pts = np.asarray(points_px, dtype=np.float64)
    _validate_points(pts, frame_size)
    W, H = area_mm
    dst = np.array([[0, 0], [W, 0], [W, H], [0, H]], dtype=np.float32)
    M = cv2.getPerspectiveTransform(pts.astype(np.float32), dst)
    return Calibration(pts.tolist(), [int(frame_size[0]), int(frame_size[1])], [float(W), float(H)],
                       M.tolist(), datetime.now().replace(microsecond=0).isoformat(), source)


def px_to_mm(calib: Calibration, pts) -> np.ndarray:
    cv2 = _cv2()
    arr = np.asarray(pts, dtype=np.float64).reshape(-1, 1, 2)
    return cv2.perspectiveTransform(arr, calib.M).reshape(-1, 2)


def warp_to_workarea(frame: np.ndarray, calib: Calibration, px_per_mm: float = C.CAMERA_PX_PER_MM) -> np.ndarray:
    """Slika radne povrsine u koordinatama lasera: piksel (u, v) = (X*ppm, Y*ppm)."""
    cv2 = _cv2()
    h, w = frame.shape[:2]
    if [w, h] != list(calib.frame_size):
        raise CameraUnavailable(f"rezolucija kamere {w}x{h} se razlikuje od kalibracije "
                                f"{calib.frame_size[0]}x{calib.frame_size[1]} - ponovi kalibraciju")
    S = np.diag([px_per_mm, px_per_mm, 1.0])
    size = (int(round(calib.area_mm[0] * px_per_mm)), int(round(calib.area_mm[1] * px_per_mm)))
    return cv2.warpPerspective(frame, S @ calib.M, size, flags=cv2.INTER_LINEAR)


class CalibrationStore:
    def __init__(self, directory: Path):
        self.path = Path(directory) / CALIBRATION_FILE
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> Calibration | None:
        if not self.path.is_file():
            return None
        return Calibration(**json.loads(self.path.read_text(encoding="utf-8")))

    def save(self, calib: Calibration) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(calib.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)

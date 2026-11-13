"""Staticki preview 3D modela: ortografski z-buffer render u PNG (samo numpy).

Pogledi: sprijeda, sa strane, odozgo, izometrijski. Koristi postojeci PNG enkoder
iz laser.preview (bez novih zavisnosti, bez OpenGL-a).
"""

from __future__ import annotations

import math

import numpy as np

from fabagent.laser.preview import encode_png

RENDER_SIZE_PX = 360
RENDER_MARGIN_PX = 16
BACKGROUND = np.array([248, 249, 250], dtype=float)
BASE_COLOR = np.array([240, 140, 60], dtype=float)
AMBIENT = 0.35
LIGHT_CAM = np.array([0.35, 0.55, 0.76])
ISO_AZIMUTH_DEG = -45.0
ISO_ELEVATION_DEG = 35.264


def _rx(a):
    c, s = math.cos(a), math.sin(a)
    return np.array([[1, 0, 0], [0, c, -s], [0, s, c]])


def _rz(a):
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])


_FRONT = np.array([[1, 0, 0], [0, 0, 1], [0, -1, 0]], dtype=float)   # gleda prema +y
VIEWS: dict[str, tuple[str, np.ndarray]] = {
    "front": ("Sprijeda", _FRONT),
    "side": ("Sa strane (desno)", np.array([[0, 1, 0], [0, 0, 1], [1, 0, 0]], dtype=float)),
    "top": ("Odozgo", np.eye(3)),
    "iso": ("Izometrijski", _rx(math.radians(ISO_ELEVATION_DEG)) @ _FRONT @ _rz(math.radians(ISO_AZIMUTH_DEG))),
}


def render(vertices: np.ndarray, faces: np.ndarray, rot: np.ndarray, size: int = RENDER_SIZE_PX) -> np.ndarray:
    cam = (vertices - vertices.mean(axis=0)) @ rot.T
    lo, hi = cam[:, :2].min(axis=0), cam[:, :2].max(axis=0)
    span = max(float((hi - lo).max()), 1e-9)
    scale = (size - 2 * RENDER_MARGIN_PX) / span
    center = (lo + hi) / 2
    sx = (cam[:, 0] - center[0]) * scale + size / 2
    sy = size / 2 - (cam[:, 1] - center[1]) * scale
    depth = cam[:, 2]

    tri = cam[faces]
    normals = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    ln = np.linalg.norm(normals, axis=1)
    visible = (normals[:, 2] > 0) & (ln > 0)
    light = LIGHT_CAM / np.linalg.norm(LIGHT_CAM)
    shade = AMBIENT + (1 - AMBIENT) * np.clip((normals @ light) / np.where(ln > 0, ln, 1), 0, 1)

    img = np.tile(BACKGROUND, (size, size, 1))
    zbuf = np.full((size, size), -np.inf)
    for i in np.nonzero(visible)[0]:
        a, b, c = faces[i]
        xs = np.array([sx[a], sx[b], sx[c]])
        ys = np.array([sy[a], sy[b], sy[c]])
        x0, x1 = max(int(math.floor(xs.min())), 0), min(int(math.ceil(xs.max())), size - 1)
        y0, y1 = max(int(math.floor(ys.min())), 0), min(int(math.ceil(ys.max())), size - 1)
        if x1 < x0 or y1 < y0:
            continue
        px, py = np.meshgrid(np.arange(x0, x1 + 1) + 0.5, np.arange(y0, y1 + 1) + 0.5)
        det = (ys[1] - ys[2]) * (xs[0] - xs[2]) + (xs[2] - xs[1]) * (ys[0] - ys[2])
        if abs(det) < 1e-12:
            continue
        w0 = ((ys[1] - ys[2]) * (px - xs[2]) + (xs[2] - xs[1]) * (py - ys[2])) / det
        w1 = ((ys[2] - ys[0]) * (px - xs[2]) + (xs[0] - xs[2]) * (py - ys[2])) / det
        w2 = 1 - w0 - w1
        inside = (w0 >= -1e-6) & (w1 >= -1e-6) & (w2 >= -1e-6)
        if not inside.any():
            continue
        z = w0 * depth[a] + w1 * depth[b] + w2 * depth[c]
        region = zbuf[y0:y1 + 1, x0:x1 + 1]
        upd = inside & (z > region)
        region[upd] = z[upd]
        img[y0:y1 + 1, x0:x1 + 1][upd] = BASE_COLOR * shade[i]
    return np.clip(img, 0, 255).astype(np.uint8)


def render_views_png(vertices: np.ndarray, faces: np.ndarray) -> dict[str, bytes]:
    return {key: encode_png(render(vertices, faces, rot)) for key, (_, rot) in VIEWS.items()}

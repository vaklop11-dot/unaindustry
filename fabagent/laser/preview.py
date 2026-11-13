"""Preview PNG laserskog dizajna (cisti Python + numpy + zlib, bez PIL-a)."""

from __future__ import annotations

import math
import struct
import zlib

import numpy as np

from fabagent.core import config as C
from fabagent.laser.design import LaserDesign


def _fill_evenodd(img: np.ndarray, rings: list[np.ndarray], scale: float, rgb) -> None:
    h, w, _ = img.shape
    a = np.vstack([r for r in rings]) * scale
    b = np.vstack([np.roll(r, -1, axis=0) for r in rings]) * scale
    ys = np.arange(h) + 0.5
    ymin = np.minimum(a[:, 1], b[:, 1])
    ymax = np.maximum(a[:, 1], b[:, 1])
    for row in range(int(max(0, math.floor(ymin.min()))), int(min(h, math.ceil(ymax.max())))):
        y = ys[row]
        sel = (ymin <= y) & (ymax > y)
        if not sel.any():
            continue
        sa, sb = a[sel], b[sel]
        x = sa[:, 0] + (y - sa[:, 1]) * (sb[:, 0] - sa[:, 0]) / (sb[:, 1] - sa[:, 1])
        x.sort()
        for x0, x1 in zip(x[0::2], x[1::2]):
            c0 = max(0, int(math.ceil(x0 - 0.5)))
            c1 = min(w, int(math.floor(x1 - 0.5)) + 1)
            if c1 > c0:
                img[row, c0:c1] = rgb


def _stroke(img: np.ndarray, ring: np.ndarray, scale: float, rgb, closed: bool = True) -> None:
    h, w, _ = img.shape
    pts = ring * scale
    nxt = np.roll(pts, -1, axis=0) if closed else pts[1:]
    cur = pts if closed else pts[:-1]
    for p, q in zip(cur, nxt):
        n = max(2, int(np.linalg.norm(q - p) * 2) + 1)
        t = np.linspace(0, 1, n)[:, None]
        s = p + (q - p) * t
        xi = np.clip(s[:, 0].astype(int), 0, w - 1)
        yi = np.clip(s[:, 1].astype(int), 0, h - 1)
        img[yi, xi] = rgb


def render(design: LaserDesign) -> np.ndarray:
    scale = min(C.PREVIEW_PX_PER_MM,
                C.PREVIEW_MAX_SIDE_PX / max(design.width, design.height))
    w = max(1, int(math.ceil(design.width * scale)))
    h = max(1, int(math.ceil(design.height * scale)))
    img = np.empty((h, w, 3), dtype=np.uint8)
    img[:] = C.PREVIEW_BACKGROUND_RGB
    for g in design.guides:
        _stroke(img, g, scale, C.PREVIEW_GUIDE_RGB)
    for it in design.items:
        if it.layer == "ENGRAVE":
            _fill_evenodd(img, it.rings, scale, C.PREVIEW_ENGRAVE_RGB)
    for it in design.items:
        if it.layer != "ENGRAVE":
            rgb = C.PREVIEW_CUT_RGB if it.layer == "CUT" else C.PREVIEW_SCORE_RGB
            for r in it.rings:
                _stroke(img, r, scale, rgb)
    return img


def encode_png(img: np.ndarray) -> bytes:
    h, w, _ = img.shape
    raw = b"".join(b"\x00" + img[row].tobytes() for row in range(h))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")


def validate_png(data: bytes) -> list[str]:
    problems = []
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        problems.append("PNG potpis nije ispravan")
    if b"IHDR" not in data[:16 + 8] or not data.endswith(b"IEND\xaeB`\x82"):
        problems.append("PNG struktura nije ispravna")
    return problems

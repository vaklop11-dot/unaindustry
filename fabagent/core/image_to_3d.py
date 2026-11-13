"""Fotografija -> mapa visina -> 3D model (litofanija ili reljef), bez AI.

* litofanija: tamniji piksel = deblje (kada se osvijetli odpozadi, deblje propusta manje svjetla)
* reljef:     svjetliji piksel = vise (doslovna mapa visina)
Mesh: gornja povrsina je mreza visina, dno ravno (trouglovi lepeza iz centra), bocni zidovi
spajaju rubove - zatvoren (watertight) po konstrukciji; validira se istim validatorom kao ostali modeli.
"""

from __future__ import annotations

import numpy as np

from fabagent.core import config as C
from fabagent.core.errors import InvalidParam, MissingParam, OutOfRange, RangeViolation, ValidationFailed


def heightmap(gray: np.ndarray, width_mm: float, t_min: float, t_max: float, mode: str,
              negative: bool = False) -> tuple[np.ndarray, float]:
    """Vraca (visine (ny, nx) u mm, korak mreze u mm)."""
    import cv2
    if mode not in C.LITHO_MODES:
        raise InvalidParam(f"nacin_3d: '{mode}' nije dozvoljeno, izbor: {list(C.LITHO_MODES)}")
    v = []
    if not (C.LITHO_WIDTH_RANGE_MM[0] <= width_mm <= C.LITHO_WIDTH_RANGE_MM[1]):
        v.append(RangeViolation("sirina_mm", width_mm, *C.LITHO_WIDTH_RANGE_MM))
    if not (C.LITHO_MIN_THICKNESS_MM <= t_min <= C.LITHO_MAX_THICKNESS_MM):
        v.append(RangeViolation("min_debljina_mm", t_min, C.LITHO_MIN_THICKNESS_MM, C.LITHO_MAX_THICKNESS_MM))
    if not (t_min < t_max <= C.LITHO_MAX_THICKNESS_MM):
        v.append(RangeViolation("max_debljina_mm", t_max, t_min, C.LITHO_MAX_THICKNESS_MM, "mm",
                                "mora biti veca od min debljine"))
    if v:
        raise OutOfRange("litofanija", v)
    h, w = gray.shape
    step = 1.0 / C.LITHO_PX_PER_MM
    nx = int(round(width_mm / step)) + 1
    ny = int(round(width_mm * h / w / step)) + 1
    if nx * ny > C.LITHO_MAX_VERTICES:
        raise ValidationFailed("slika", [f"mreza {nx}x{ny} = {nx * ny:,} tacaka > max {C.LITHO_MAX_VERTICES:,} "
                                         "- smanji sirinu"])
    g = cv2.resize(gray, (nx, ny), interpolation=cv2.INTER_AREA if nx < w else cv2.INTER_LINEAR)
    f = g.astype(np.float64) / 255.0
    if negative:
        f = 1.0 - f
    if mode == "litofanija":
        f = 1.0 - f                       # tamnije = deblje
    return t_min + (t_max - t_min) * f, step


def heightmap_mesh(z: np.ndarray, step: float) -> tuple[np.ndarray, np.ndarray]:
    """Zatvoren mesh: gornja mreza + bocni zidovi + dno (lepeza iz centra dna)."""
    ny, nx = z.shape
    xs = np.arange(nx) * step
    ys = (ny - 1 - np.arange(ny)) * step              # red 0 slike je gore (najveci y)
    X, Y = np.meshgrid(xs, ys)
    top = np.column_stack([X.ravel(), Y.ravel(), z.ravel()])
    idx = np.arange(ny * nx).reshape(ny, nx)
    a, b = idx[:-1, :-1].ravel(), idx[:-1, 1:].ravel()
    c, d = idx[1:, :-1].ravel(), idx[1:, 1:].ravel()
    # red i je iznad reda i+1 (veci y): (a,b) gore, (c,d) dolje -> CCW gledano odozgo
    faces = [np.column_stack([a, c, d]), np.column_stack([a, d, b])]
    # rub gornje mreze u smjeru suprotnom od kazaljke (gledano odozgo)
    ring = np.concatenate([idx[-1, :-1], idx[:0:-1, -1], idx[0, :0:-1], idx[:-1, 0]])
    nb = len(ring)
    base = len(top)
    bottom = top[ring].copy()
    bottom[:, 2] = 0.0
    center = np.array([[xs[-1] / 2, ys[0] / 2, 0.0]])
    verts = np.vstack([top, bottom, center])
    bi = base + np.arange(nb)
    bn = base + (np.arange(nb) + 1) % nb
    tn = ring[(np.arange(nb) + 1) % nb]
    faces.append(np.column_stack([ring, bi, bn]))      # zidovi (normala prema van)
    faces.append(np.column_stack([ring, bn, tn]))
    ci = len(verts) - 1
    faces.append(np.column_stack([np.full(nb, ci), bn, bi]))   # dno (normala prema dolje)
    return verts, np.vstack(faces).astype(np.int64)


def build_lithophane(data: bytes, width_mm, t_min, t_max, mode, negative: bool = False):
    """Slika (bajtovi) -> (Body, izvjestaj validacije, info). Neispravan mesh -> greska."""
    from fabagent.core.errors import InvalidGeometry
    from fabagent.core.image_trace import load_gray
    from fabagent.printer.mesh_validator import validate_mesh
    from fabagent.printer.mesher import Body
    missing = [n for n, v in (("sirina_mm", width_mm), ("min_debljina_mm", t_min), ("max_debljina_mm", t_max),
                              ("nacin_3d", mode)) if v in (None, "")]
    if missing:
        raise MissingParam("litofanija", missing)
    gray = load_gray(data)
    z, step = heightmap(gray, width_mm, t_min, t_max, mode, negative)
    v, f = heightmap_mesh(z, step)
    # debljina litofanije je visina mape u svakoj tacki mreze (dno je ravno na z=0) - tacno, bez zraka
    zmin = float(z.min())
    at = np.unravel_index(int(np.argmin(z)), z.shape)
    wall = {"min_mm": round(zmin, 4), "method": "analiticki (mapa visina)",
            "at": [round(at[1] * step, 3), round((z.shape[0] - 1 - at[0]) * step, 3), 0.0], "misses": 0}
    rep = validate_mesh(v, f, "litofanija", min_wall=t_min, wall_override=wall)
    if not rep["ok"]:
        raise InvalidGeometry(rep["errors"], "litofanija")
    info = {"grid": [int(z.shape[1]), int(z.shape[0])], "step_mm": step, "mode": mode, "negative": negative,
            "thickness_mm": [t_min, t_max], "size_mm": rep["stats"]["bbox_mm"]["size"]}
    return Body("litofanija", v, f), rep, info

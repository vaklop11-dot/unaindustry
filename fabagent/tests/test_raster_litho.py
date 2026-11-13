"""Raster graviranje (threshold/dither/grayscale/negative) i 3D litofanija iz slike."""

import re

import numpy as np
import pytest
import trimesh

cv2 = pytest.importorskip("cv2")

from fabagent.core import config as C  # noqa: E402
from fabagent.core import image_to_3d as L  # noqa: E402
from fabagent.core import raster as R  # noqa: E402
from fabagent.core.errors import InvalidParam, MissingParam, OutOfRange, ValidationFailed  # noqa: E402
from fabagent.printer import mesh_io  # noqa: E402


def gradient(w=100, h=40):
    return np.tile(np.linspace(0, 255, w), (h, 1)).astype(np.uint8)


def png(img):
    return cv2.imencode(".png", img)[1].tobytes()


# ------------------------------------------------------------------ raster
@pytest.mark.parametrize("algo", C.DITHER_ALGORITHMS)
def test_dither_preserves_mean_tone(algo):
    g = np.full((60, 60), 64, np.uint8)               # 25 % sivo -> ~75 % tackica
    p = R.error_diffusion(g, algo)
    assert p.mean() == pytest.approx(0.75, abs=0.03)
    assert set(np.unique(p)) <= {False, True}
    assert np.array_equal(p, R.error_diffusion(g, algo))   # deterministicki


def test_dither_on_gradient_monotonic():
    p = R.power_map(gradient(), R.RasterOptions("dither", 50.0, 0.5, algorithm="floyd-steinberg"))
    left, right = p[:, :20].mean(), p[:, -20:].mean()
    assert left > 0.8 and right < 0.2


def test_grayscale_power_levels_and_negative():
    o = R.RasterOptions("grayscale", 50.0, 0.5, power_min_pct=10)
    p = R.power_map(gradient(), o)
    assert p.shape == (40, 100)                          # 50 mm / 0.5 = 100 px, visina po omjeru
    assert p[:, 0].mean() == pytest.approx(1.0, abs=0.02)   # crno = puna snaga
    assert p[:, -1].max() == 0.0                         # bijelo se ne pali
    assert p[:, 1:-1].min() >= 0.10 - 1e-9               # min snaga za sivo
    neg = R.power_map(gradient(), R.RasterOptions("grayscale", 50.0, 0.5, negative=True))
    assert neg[:, -1].mean() == pytest.approx(1.0, abs=0.02)


def test_threshold_raster():
    p = R.power_map(gradient(), R.RasterOptions("threshold", 50.0, 0.5, threshold=128))
    assert p[:, :45].all() and not p[:, 55:].any()


@pytest.mark.parametrize("o,exc", [
    (R.RasterOptions("dither", 50, 0.5), MissingParam),
    (R.RasterOptions("threshold", 50, 0.5), MissingParam),
    (R.RasterOptions("dither", 50, 0.5, algorithm="atkinson"), InvalidParam),
    (R.RasterOptions("sepia", 50, 0.5), InvalidParam),
    (R.RasterOptions("grayscale", 50, 2.0), OutOfRange),
    (R.RasterOptions("grayscale", 500, 0.5), OutOfRange),
])
def test_raster_option_errors(o, exc):
    with pytest.raises(exc):
        R.power_map(gradient(), o)


def test_raster_too_many_pixels():
    with pytest.raises(ValidationFailed):
        R.power_map(gradient(400, 400), R.RasterOptions("grayscale", 380.0, 0.05))


def test_raster_gcode_rows_and_power():
    p = R.power_map(gradient(), R.RasterOptions("grayscale", 50.0, 0.5))
    text, stats = R.raster_gcode(p, 0.5, 100.0, 60.0, 1, (10, 10), "test")
    assert text.splitlines()[-3].startswith("M5")
    s_vals = [int(m) for m in re.findall(r" S(\d+)", text)]
    assert max(s_vals) <= 600 and max(s_vals) >= 590      # 60 % od S max 1000
    g0 = [l for l in text.splitlines() if l.startswith("G0 X") and "Y" in l and l != "G0 X0 Y0"]
    assert len(g0) == 40                                  # jedan red = jedan prelaz
    assert stats["size_mm"] == [50.0, 20.0]
    # dvosmjerno: susjedni redovi pocinju na suprotnim stranama
    x0 = float(re.search(r"X(-?[\d.]+)", g0[0]).group(1))
    x1 = float(re.search(r"X(-?[\d.]+)", g0[1]).group(1))
    assert x0 < x1
    with pytest.raises(OutOfRange):
        R.raster_gcode(p, 0.5, 100.0, 60.0, 1, (380, 0))


def test_preview_png_roundtrip():
    p = R.power_map(gradient(), R.RasterOptions("dither", 50.0, 0.5, algorithm="stucki"))
    back = R.power_map_from_png(R.preview_png(p))
    assert np.array_equal(back > 0.5, p > 0.5)


# ------------------------------------------------------------------ 3D litofanija
def photo():
    img = np.full((80, 120), 230, np.uint8)
    cv2.circle(img, (60, 40), 25, 30, -1)            # tamni krug
    return img


@pytest.mark.parametrize("mode", C.LITHO_MODES)
def test_lithophane_watertight_and_heights(mode):
    body, rep, info = L.build_lithophane(png(photo()), 60.0, 0.8, 3.0, mode)
    tm = trimesh.Trimesh(body.vertices, body.faces, process=False)
    assert tm.is_watertight and tm.is_winding_consistent and tm.volume > 0
    size = rep["stats"]["bbox_mm"]["size"]
    assert size[0] == pytest.approx(60.0) and size[1] == pytest.approx(40.0)
    assert size[2] <= 3.0 + 1e-9
    z = body.vertices[:, 2]
    top = z[z > 0]
    assert top.min() >= 0.8 - 1e-9
    # centar je tamni krug: litofanija -> najdeblje, reljef -> najtanje
    cx = np.argmin(np.hypot(body.vertices[:, 0] - 30, body.vertices[:, 1] - 20) + (z == 0) * 1e9)
    f = 30 / 255                                        # siva vrijednost kruga
    expected = 0.8 + 2.2 * ((1 - f) if mode == "litofanija" else f)
    assert z[cx] == pytest.approx(expected, abs=0.05)


def test_lithophane_volume_matches_heightmap():
    body, rep, info = L.build_lithophane(png(photo()), 60.0, 1.0, 2.0, "reljef")
    gray = cv2.imdecode(np.frombuffer(png(photo()), np.uint8), cv2.IMREAD_GRAYSCALE)
    z, step = L.heightmap(gray, 60.0, 1.0, 2.0, "reljef")
    # zapremina ~ srednja visina (trapezno) * povrsina
    zt = (z[:-1, :-1] + z[1:, :-1] + z[:-1, 1:] + z[1:, 1:]) / 4
    assert rep["stats"]["volume_mm3"] == pytest.approx(float(zt.sum()) * step * step, rel=0.01)


def test_lithophane_files_and_errors(tmp_path):
    body, _, _ = L.build_lithophane(png(photo()), 50.0, 0.8, 3.0, "litofanija")
    data = mesh_io.stl_bytes(body.vertices, body.faces, "lito")
    v, f = mesh_io.read_stl(data)
    assert trimesh.Trimesh(v, f, process=False).is_watertight
    with pytest.raises(MissingParam):
        L.build_lithophane(png(photo()), None, 0.8, 3.0, "litofanija")
    with pytest.raises(OutOfRange):
        L.build_lithophane(png(photo()), 50.0, 0.3, 3.0, "litofanija")
    with pytest.raises(OutOfRange):
        L.build_lithophane(png(photo()), 50.0, 2.0, 1.0, "litofanija")
    with pytest.raises(InvalidParam):
        L.build_lithophane(png(photo()), 50.0, 0.8, 3.0, "skulptura")

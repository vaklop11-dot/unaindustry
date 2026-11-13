"""Radni ekran: G-code iz slobodnog crteza (linije, popuna, slike), Frame, tekst u konture."""

import base64
import re

import numpy as np
import pytest

from fabagent.core import config as C
from fabagent.core import workspace as W
from fabagent.core.errors import InvalidGeometry, InvalidParam, MissingParam, OutOfRange, ValidationFailed

cv2 = pytest.importorskip("cv2")

H = C.LASER_WORK_AREA_MM[1]


def layer(index=0, mode="Line", speed=600, power=50, passes=1, interval=0.1, output=True):
    return {"index": index, "mode": mode, "speed_mm_min": speed, "power_pct": power, "passes": passes,
            "interval_mm": interval, "output": output}


def square(x, y, s, lay=0, closed=True):
    return {"layer": lay, "closed": closed, "points": [[x, y], [x + s, y], [x + s, y + s], [x, y + s]]}


def moves(text):
    """[(G, X, Y, S)] za sve G0/G1 linije."""
    out = []
    for ln in text.splitlines():
        m = re.match(r"G([01]) X(-?[\d.]+) Y(-?[\d.]+)(?: S(\d+))?", ln)
        if m:
            out.append((int(m[1]), float(m[2]), float(m[3]), int(m[4]) if m[4] else None))
    return out


def png_data_url(img):
    return "data:image/png;base64," + base64.b64encode(cv2.imencode(".png", img)[1].tobytes()).decode()


# ------------------------------------------------------------------ linije
def test_line_layer_closed_shape():
    res = W.generate({"layers": [layer(power=50, speed=600)], "shapes": [square(10, 20, 30)]})
    lines = res.text.splitlines()
    assert lines[2:6] == ["G21 ; mm", "G90 ; apsolutno", "M5", "M4 S0"]
    assert lines[-3:] == ["M5 ; laser ugasen", "G0 X0 Y0", "M2"]
    mv = moves(res.text)[:-1]                                  # bez zavrsnog G0 X0 Y0
    assert mv[0][0] == 0 and [m[0] for m in mv[1:]] == [1, 1, 1, 1]      # G0 do pocetka, 4 stranice (zatvoreno)
    assert all(m[3] == 500 for m in mv[1:]) and " F600" in res.text       # 50 % od S max 1000, 600 mm/min
    xs, ys = {m[1] for m in mv}, {m[2] for m in mv}
    assert xs == {10.0, 40.0} and ys == {H - 20.0, H - 50.0}             # Y okrenut: ishodiste dolje-lijevo
    assert (mv[-1][1], mv[-1][2]) == (mv[0][1], mv[0][2])                # kontura se zatvara
    assert res.stats["bbox_mm"] == [10.0, H - 50.0, 40.0, H - 20.0]
    assert res.stats["burn_mm"] == pytest.approx(120.0) and res.stats["layers"] == [0]


def test_open_line_and_passes():
    shp = {"layer": 0, "closed": False, "points": [[10, 10], [60, 10], [60, 40]]}
    res = W.generate({"layers": [layer(passes=2)], "shapes": [shp]})
    mv = moves(res.text)[:-1]
    assert [m[0] for m in mv] == [0, 1, 1, 0, 1, 1]                       # 2 prolaza, bez zatvaranja
    assert res.stats["burn_mm"] == pytest.approx(160.0)
    assert (mv[3][1], mv[3][2]) == (60.0, H - 40.0)                       # drugi prolaz krece sa blizeg kraja


def test_layer_order_and_output_flag():
    doc = {"layers": [layer(3, power=10), layer(0, power=80), layer(7, output=False)],
           "shapes": [square(10, 10, 5, lay=0), square(50, 50, 5, lay=3), square(90, 90, 5, lay=7)]}
    res = W.generate(doc)
    assert res.stats["layers"] == [3, 0]                                  # redoslijed iz tabele, 07 iskljucen
    assert res.text.index("sloj 03") < res.text.index("sloj 00") and "sloj 07" not in res.text
    assert " X90" not in res.text and "S100 " in res.text and "S800 " in res.text
    with pytest.raises(InvalidGeometry):
        W.generate({"layers": [layer(0, output=False)], "shapes": [square(10, 10, 5)]})


# ------------------------------------------------------------------ popuna
def test_fill_layer_scanlines():
    doc = {"layers": [layer(mode="Fill", interval=0.5, power=30)],
           "shapes": [square(20, 20, 10), {"layer": 0, "closed": False, "points": [[50, 50], [70, 50]]}]}
    res = W.generate(doc)
    burn = [m for m in moves(res.text) if m[0] == 1 and m[3] == 300]
    assert len(burn) == 20                                                # 10 mm / 0.5 mm
    assert {round(abs(m[1] - 25.0), 3) for m in burn} == {5.0}            # svaki red pali do ruba kvadrata
    over = C.ENGRAVE_OVERSCAN_MM
    xs = [m[1] for m in moves(res.text)[:-1]]
    assert min(xs) == pytest.approx(20 - over) and max(xs) == pytest.approx(30 + over)
    assert len(res.stats["warnings"]) == 1 and "otvorenih" in res.stats["warnings"][0]
    assert res.stats["burn_mm"] == pytest.approx(200.0)


def test_fill_respects_holes():
    doc = {"layers": [layer(mode="Fill", interval=1.0)], "shapes": [square(20, 20, 20), square(25, 25, 10)]}
    res = W.generate(doc)
    assert res.stats["burn_mm"] == pytest.approx(20 * 20 - 10 * 10)       # rupa se ne pali


# ------------------------------------------------------------------ granice i greske
def test_bounds_and_errors():
    with pytest.raises(OutOfRange) as e:
        W.generate({"layers": [layer()], "shapes": [square(390, 10, 20)]})
    assert "izlazi izvan radne povrsine" in str(e.value)
    with pytest.raises(OutOfRange):                                       # prelet popune izlazi lijevo
        W.generate({"layers": [layer(mode="Fill")], "shapes": [square(1, 10, 20)]})
    W.generate({"layers": [layer()], "shapes": [square(1, 10, 20)]})      # ista pozicija kao linija je u redu
    with pytest.raises(MissingParam):
        W.generate({"layers": [layer(0)], "shapes": [square(10, 10, 5, lay=4)]})
    with pytest.raises(MissingParam):
        W.generate({"shapes": [square(10, 10, 5)]})
    with pytest.raises(OutOfRange):
        W.parse_layers([layer(power=120)])
    with pytest.raises(OutOfRange):
        W.parse_layers([layer(speed=20)])                                 # ispod 60 mm/min
    with pytest.raises(InvalidParam):
        W.parse_layers([layer(mode="Cut")])
    with pytest.raises(InvalidParam):
        W.parse_layers([layer(0), layer(0)])
    with pytest.raises(InvalidParam):
        W.parse_layers([layer(index=30)])
    with pytest.raises(InvalidParam):
        W.parse_shapes([{"layer": 0, "closed": True, "points": [[0, 0], ["a", 1]]}])


def test_frame_never_fires_laser():
    res = W.frame({"layers": [layer(mode="Fill")], "shapes": [square(10, 20, 30)]})
    assert "M4" not in res.text and "M3" not in res.text
    assert all(m[3] in (None, 0) for m in moves(res.text))
    assert res.text.strip().splitlines()[-1] == "M5"
    assert res.stats["bbox_mm"] == [10.0, H - 50.0, 40.0, H - 20.0]       # okvir oblika, bez preleta
    pts = {(m[1], m[2]) for m in moves(res.text)}
    assert pts == {(10.0, H - 50.0), (40.0, H - 50.0), (40.0, H - 20.0), (10.0, H - 20.0)}


# ------------------------------------------------------------------ slike
def test_image_threshold_and_rotation():
    img = np.full((40, 40), 255, np.uint8)
    img[:, :20] = 0                                                       # lijeva polovina crna
    base = {"layer": 0, "mode": "threshold", "threshold": 128, "data": png_data_url(img)}
    upright = dict(base, corners=[[100, 100], [120, 100], [120, 120], [100, 120]])
    res = W.generate({"layers": [layer(interval=0.5, power=100)], "images": [upright]})
    burn = [m for m in moves(res.text) if m[0] == 1 and m[3] and m[3] > 0]
    assert len(burn) == 40 and {m[3] for m in burn} == {1000}             # 20 mm / 0.5 mm redova
    assert all(100.0 <= m[1] <= 110.0 + 1e-6 for m in burn)               # pali samo lijevu polovinu
    turned = dict(base, corners=[[120, 100], [120, 120], [100, 120], [100, 100]])   # okrenuta 90 stepeni
    p, x0, y0 = W.image_power_map(W.parse_images([turned])[0], 0.5)
    assert (x0, y0) == (100.0, 100.0) and p.shape == (40, 40)
    assert p[:18].mean() > 0.95 and p[22:].mean() < 0.05                  # crna polovina je sada gore
    neg = dict(upright, negative=True)
    pn, _, _ = W.image_power_map(W.parse_images([neg])[0], 0.5)
    assert pn[:, :18].mean() < 0.05 and pn[:, 22:].mean() > 0.95


def test_image_modes_and_limits():
    grad = np.tile(np.linspace(0, 255, 50).astype(np.uint8), (50, 1))
    base = {"layer": 0, "corners": [[10, 10], [35, 10], [35, 35], [10, 35]], "data": png_data_url(grad)}
    gray = W.image_power_map(W.parse_images([dict(base, mode="grayscale")])[0], 0.5)[0]
    assert gray[:, 0].mean() > 0.9 and gray[:, -1].mean() < 0.1           # tamnije = jace
    dith = W.image_power_map(W.parse_images([dict(base, mode="dither", algorithm="jarvis")])[0], 0.5)[0]
    assert set(np.unique(dith)) == {0.0, 1.0} and 0.35 < dith.mean() < 0.65
    with pytest.raises(InvalidParam):
        W.parse_images([dict(base, mode="dither", algorithm="x")])
    with pytest.raises(MissingParam):
        W.parse_images([dict(base, mode="threshold")])
    with pytest.raises(InvalidParam):
        W.parse_images([dict(base, mode="grayscale", data="http://x/slika.png")])
    big = dict(base, mode="grayscale", corners=[[0, 0], [400, 0], [400, 400], [0, 400]])
    with pytest.raises(ValidationFailed):
        W.image_power_map(W.parse_images([big])[0], 0.1)


# ------------------------------------------------------------------ tekst
def _font():
    fonts = W.list_fonts()
    if not fonts:
        pytest.skip("na ovom racunaru nema TTF/OTF fontova")
    return next((f["id"] for f in fonts if f["id"].lower() == "arial.ttf"), fonts[0]["id"])


def test_text_to_outline():
    from svgpathtools import parse_path
    out = W.text_path("HB", _font(), 10)
    x0, x1, y0, y1 = parse_path(out["d"]).bbox()
    assert y0 == pytest.approx(-10.0, abs=0.4) and y1 == pytest.approx(0.0, abs=0.4)   # velika slova = 10 mm
    assert x1 - x0 > 8 and out["width_mm"] > 8 and out["missing"] == []
    assert out["d"].count("Z") >= 3                                       # H + B sa dvije rupe
    two = W.text_path("H\nH", _font(), 10)
    assert parse_path(two["d"]).bbox()[3] > 8                             # drugi red je ispod prvog


def test_text_errors():
    font = _font()
    with pytest.raises(MissingParam):
        W.text_path("", font, 10)
    with pytest.raises(OutOfRange):
        W.text_path("A", font, 0.2)
    with pytest.raises(InvalidParam):
        W.text_path("A", "../../tajna.ttf", 10)

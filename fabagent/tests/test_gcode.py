import math
import re

import numpy as np
import pytest

from fabagent.core import config as C
from fabagent.core import geometry2d as g2
from fabagent.core.errors import InvalidParam, MissingParam, OutOfRange
from fabagent.core.gcode import (LayerSetting, design_from_svg, frame_gcode, generate, parse_layer_settings,
                                 preset_layer_settings, scanline_spans)
from fabagent.laser.design import LaserDesign
from fabagent.laser.presets import PRESETS
from fabagent.laser.svg_builder import build, design_to_svg

MOVE = re.compile(r"^G([01]) X(-?\d+\.\d+) Y(-?\d+\.\d+)(?: S(\d+))?(?: F(\d+))?")
CUT = LayerSetting("Cut", speed_mm_s=10.0, power_pct=80.0, passes=1)


def moves(text):
    out = []
    for line in text.splitlines():
        m = MOVE.match(line)
        if m:
            g, x, y, s, f = m.groups()
            out.append((int(g), float(x), float(y), None if s is None else int(s), None if f is None else int(f)))
    return out


def circle_design(cx=50.0, cy=30.0, r=10.0):
    d = LaserDesign(100, 60)
    d.add("CUT", [g2.circle(cx, cy, r)], "krug")
    return d


def test_circle_gcode_structure():
    res = generate(circle_design(), {"CUT": CUT}, offset=(0, 0), title="krug")
    lines = res.text.splitlines()
    assert "G21 ; mm" in lines and "G90 ; apsolutno" in lines
    assert lines.index("M5") < lines.index(f"{C.GRBL_LASER_MODE_CMD} S0")
    assert lines[-3].startswith("M5")                       # laser ugasen na kraju
    mv = moves(res.text)
    assert mv[0][0] == 0                                    # prvo brzi pomak (laser ugasen)
    g1 = [m for m in mv if m[0] == 1]
    assert all(m[3] == 800 and m[4] == 600 for m in g1)     # S = 80 % od 1000, F = 10 mm/s * 60
    # sve tacke na kruznici, Y okrenut (masina dolje-lijevo): Y_m = 400 - 30
    H = C.LASER_WORK_AREA_MM[1]
    for _, x, y, _, _ in g1:
        assert math.hypot(x - 50, y - (H - 30)) == pytest.approx(10.0, abs=2e-3)
    # kontura je zatvorena: zadnji G1 zavrsava u tacki prvog G0
    assert (g1[-1][1], g1[-1][2]) == (mv[0][1], mv[0][2])
    assert res.stats["burn_mm"] == pytest.approx(2 * math.pi * 10, rel=1e-3)


def test_offset_and_passes():
    res = generate(circle_design(), {"CUT": LayerSetting("Cut", 10, 50, 3)}, offset=(100, 50))
    g1 = [m for m in moves(res.text) if m[0] == 1]
    xs = [m[1] for m in g1]
    assert min(xs) == pytest.approx(140, abs=1e-2) and max(xs) == pytest.approx(160, abs=1e-2)
    assert res.stats["burn_mm"] == pytest.approx(3 * 2 * math.pi * 10, rel=1e-3)


def test_out_of_work_area_rejected():
    with pytest.raises(OutOfRange):
        generate(circle_design(), {"CUT": CUT}, offset=(380, 0))


def test_inner_contours_cut_first():
    d = LaserDesign(100, 100)
    d.add("CUT", [g2.circle(50, 50, 40), g2.circle(50, 50, 5)], "dio")
    res = generate(d, {"CUT": CUT}, offset=(0, 0))
    first_g1 = next(m for m in moves(res.text) if m[0] == 1)
    H = C.LASER_WORK_AREA_MM[1]
    assert math.hypot(first_g1[1] - 50, first_g1[2] - (H - 50)) == pytest.approx(5, abs=1e-2)


def test_engrave_fill_scanlines_cover_square():
    d = LaserDesign(40, 40)
    sq = np.array([[10, 10], [30, 10], [30, 30], [10, 30]], float)
    hole = np.array([[15, 15], [25, 15], [25, 25], [15, 25]], float)
    d.add("ENGRAVE", [sq, hole], "kvadrat")
    st = LayerSetting("Engrave", 100, 30, 1, 0.5)
    res = generate(d, {"ENGRAVE": st}, offset=(0, 0))
    burn = [m for m in moves(res.text) if m[0] == 1 and m[3] and m[3] > 0]
    assert all(m[3] == 300 for m in burn)
    # 20 mm / 0.5 = 40 redova; 20 redova kroz rupu imaju 2 segmenta
    assert len(burn) == 40 + 20
    # povrsina = duzina paljenja * interval
    assert res.stats["burn_mm"] * 0.5 == pytest.approx(20 * 20 - 10 * 10, rel=1e-6)
    assert scanline_spans([sq, hole], 20.0) == [(10.0, 15.0), (25.0, 30.0)]


def test_frame_is_laser_off_only():
    res = frame_gcode(circle_design(), (0, 0), feed_mm_s=50)
    mv = moves(res.text)
    assert all(m[3] in (None, 0) for m in mv)            # nikad S > 0
    assert "M3" not in res.text and "M4" not in res.text
    assert len([m for m in mv if m[0] == 1]) == 4
    assert res.stats["bbox_mm"] == pytest.approx([40, 20, 60, 40], abs=C.ARC_CHORD_TOLERANCE_MM)   # krug je poligon


def test_layer_settings_validation_and_preset():
    pre = preset_layer_settings(PRESETS["sperploca_3mm"])
    assert set(pre) == {"CUT", "ENGRAVE", "SCORE"} and pre["ENGRAVE"]["interval_mm"] == 0.2   # IZMJERENO 15.11.2026
    assert "CUT" not in preset_layer_settings(PRESETS["eloksirani_aluminij"])
    ok = parse_layer_settings(pre, ["CUT", "ENGRAVE"])
    assert ok["CUT"].s_value == 1000     # 100 % od S max 1000, IZMJERENO 15.11.2026,
                                     # ispravljeno 17.11.2026 (ranije pogresno upisano 35 %)
    with pytest.raises(MissingParam):
        parse_layer_settings({"CUT": {"mode": "Cut", "speed_mm_s": 5}}, ["CUT"])
    with pytest.raises(OutOfRange):
        parse_layer_settings({"CUT": dict(pre["CUT"], power_pct=150)}, ["CUT"])
    with pytest.raises(InvalidParam):
        parse_layer_settings({"CUT": dict(pre["CUT"], mode="Laserbeam")}, ["CUT"])
    with pytest.raises(MissingParam):
        parse_layer_settings({"ENGRAVE": dict(pre["ENGRAVE"], interval_mm=None)}, ["ENGRAVE"])


def test_from_template_svg_roundtrip():
    res = build("privesak", C.LASER_TEMPLATES["privesak"].example)
    d = design_from_svg(res.svg)
    pre = parse_layer_settings(preset_layer_settings(res.preset), ["CUT", "ENGRAVE"])
    g = generate(d, pre, offset=(10, 10), title="privesak")
    text = g.text
    eng = text.index("; --- sloj ENGRAVE")
    cut = text.index("; --- sloj CUT")
    assert eng < cut                                       # graviranje prije rezanja
    assert g.stats["time_s"] > 0 and g.stats["layers"] == ["ENGRAVE", "CUT"]

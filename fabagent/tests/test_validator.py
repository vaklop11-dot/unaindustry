import math

import numpy as np
import pytest

from fabagent.core import config as C
from fabagent.core import geometry2d as g2
from fabagent.core.decisions import DecisionLog
from fabagent.core.errors import InvalidParam, MissingParam, OutOfRange
from fabagent.core.validator import parse_cli_params, validate_params

ALL_SPECS = list(C.LASER_TEMPLATES.values()) + list(C.PRINT_TEMPLATES.values())


# ------------------------------------------------------------- seme
@pytest.mark.parametrize("spec", ALL_SPECS, ids=lambda s: s.name)
def test_schema_defaults_within_range(spec):
    for p in spec.params:
        if p.required:
            continue
        if p.type in ("float", "int"):
            assert p.min <= p.default <= p.max, p.name
        if p.type == "enum":
            assert p.default in p.choices


@pytest.mark.parametrize("spec", ALL_SPECS, ids=lambda s: s.name)
def test_schema_example_is_complete_and_valid(spec):
    vp = validate_params(spec, spec.example, use_defaults=False)
    assert vp.defaulted == set()


# ------------------------------------------------------------- CLI parsiranje
def test_parse_cli_params_ok():
    assert parse_cli_params(["a=1", "tekst=A=B"]) == {"a": "1", "tekst": "A=B"}


@pytest.mark.parametrize("items", [["a"], ["a=1", "a=2"], ["A=1"], ["=1"]])
def test_parse_cli_params_errors(items):
    with pytest.raises(InvalidParam):
        parse_cli_params(items)


# ------------------------------------------------------------- pravila
SPEC = C.LASER_TEMPLATES["privesak"]
BASE = dict(SPEC.example)


def test_missing_required_lists_all():
    raw = dict(BASE)
    del raw["sirina"], raw["tekst"]
    with pytest.raises(MissingParam) as e:
        validate_params(SPEC, raw, use_defaults=True)
    assert set(e.value.missing) == {"sirina", "tekst"}


def test_missing_optional_without_flag_is_error():
    raw = dict(BASE)
    del raw["rupa_odmak"]
    with pytest.raises(MissingParam) as e:
        validate_params(SPEC, raw, use_defaults=False)
    assert e.value.optional_missing == ["rupa_odmak"]
    assert "--use-defaults" in str(e.value)


def test_use_defaults_fills_and_logs():
    raw = dict(BASE)
    del raw["rupa_odmak"]
    log = DecisionLog()
    vp = validate_params(SPEC, raw, use_defaults=True, log=log)
    assert vp["rupa_odmak"] == SPEC.param("rupa_odmak").default
    assert "rupa_odmak" in vp.defaulted
    assert any("rupa_odmak" in e["message"] for e in log.entries)


def test_out_of_range_reports_all():
    raw = dict(BASE, sirina=500, visina_teksta=1)
    with pytest.raises(OutOfRange) as e:
        validate_params(SPEC, raw, use_defaults=False)
    names = {v.param for v in e.value.violations}
    assert names == {"sirina", "visina_teksta"}
    assert "[15, 100]" in str(e.value)


@pytest.mark.parametrize("key,value", [
    ("sirina", "12,5"), ("sirina", "abc"), ("sirina", "nan"), ("oblik", "trokut"),
    ("tekst", "doboj"), ("nepoznat", "1"),
])
def test_invalid_values(key, value):
    raw = dict(BASE, **{key: value})
    with pytest.raises(InvalidParam):
        validate_params(SPEC, raw, use_defaults=False)


def test_conditional_param_not_applicable():
    raw = dict(BASE, radijus_ugla=2)  # oblik=krug
    with pytest.raises(InvalidParam):
        validate_params(SPEC, raw, use_defaults=False)


def test_conditional_param_required_when_applicable():
    raw = dict(BASE, oblik="pravougaonik")
    with pytest.raises(MissingParam) as e:
        validate_params(SPEC, raw, use_defaults=False)
    assert "radijus_ugla" in e.value.optional_missing


def test_int_choices():
    spec = C.LASER_TEMPLATES["plocica"]
    with pytest.raises(InvalidParam):
        validate_params(spec, dict(spec.example, broj_rupa=3), use_defaults=False)
    with pytest.raises(InvalidParam):
        validate_params(spec, dict(spec.example, broj_rupa="2.5"), use_defaults=False)


def test_bool_parsing():
    spec = C.LASER_TEMPLATES["oznaka_qr"]
    vp = validate_params(spec, dict(spec.example, rez_okvir="false"), use_defaults=False)
    assert vp["rez_okvir"] is False
    with pytest.raises(InvalidParam):
        validate_params(spec, dict(spec.example, rez_okvir="mozda"), use_defaults=False)


# ------------------------------------------------------------- geometrija 2D
def test_circle_chord_tolerance():
    r = 10.0
    pts = g2.circle(0, 0, r)
    mid = (pts + np.roll(pts, -1, axis=0)) / 2
    sagitta = r - np.linalg.norm(mid, axis=1)
    assert sagitta.max() <= C.ARC_CHORD_TOLERANCE_MM + 1e-12
    assert g2.signed_area(pts) > 0


def test_rounded_rect_area():
    poly = g2.rounded_rect(0, 0, 20, 10, 2)
    expected = 20 * 10 - (4 - math.pi) * 4
    # greska povrsine ~ obim_luka * 2/3 * tolerancija tetive
    assert abs(g2.signed_area(poly) - expected) < 2 * math.pi * 2 * C.ARC_CHORD_TOLERANCE_MM
    assert g2.is_simple(poly)


def test_points_in_polygon_and_distance():
    sq = np.array([[0, 0], [10, 0], [10, 10], [0, 10]], float)
    inside = g2.points_in_polygon(np.array([[5, 5], [15, 5]]), sq)
    assert inside.tolist() == [True, False]
    assert abs(g2.min_distance_points_to_polyline(np.array([[5, 3]]), sq) - 3) < 1e-12


def test_polyline_distance_and_intersection():
    a = np.array([[0, 0], [10, 0], [10, 10], [0, 10]], float)
    b = a + 20
    assert abs(g2.polyline_min_distance(a, b) - math.hypot(10, 10)) < 1e-9
    c = a + 5
    assert g2.polyline_min_distance(a, c) == 0.0


def test_is_simple_detects_bowtie():
    bowtie = np.array([[0, 0], [10, 10], [10, 0], [0, 10]], float)
    assert not g2.is_simple(bowtie)


def test_grid_to_polygons_ring_and_checkerboard():
    ring = np.ones((3, 3), bool)
    ring[1, 1] = False
    polys = g2.grid_to_polygons(ring, 1.0)
    assert len(polys) == 2
    areas = sorted(abs(g2.signed_area(p)) for p in polys)
    assert areas == [1.0, 9.0]
    # vanjska i unutrasnja kontura imaju suprotnu orijentaciju
    signs = {np.sign(g2.signed_area(p)) for p in polys}
    assert signs == {-1.0, 1.0}
    checker = np.array([[1, 0], [0, 1]], bool)
    polys = g2.grid_to_polygons(checker, 2.0)
    assert len(polys) == 2
    assert all(abs(abs(g2.signed_area(p)) - 4.0) < 1e-12 for p in polys)

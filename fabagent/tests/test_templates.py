"""Za svaki sablon: validni parametri prolaze, nedostajuci -> MissingParam,
van opsega -> OutOfRange, plus geometrijski specificni slucajevi."""

import pytest

from fabagent.core import config as C
from fabagent.core.errors import InvalidGeometry, InvalidParam, MissingParam, OutOfRange
from fabagent.laser import svg_builder

LASER = sorted(C.LASER_TEMPLATES)
PRINT = sorted(C.PRINT_TEMPLATES)


def _first_numeric(spec):
    for p in spec.params:
        if p.required and p.type in ("float", "int") and not p.choices:
            return p
    raise AssertionError("nema numerickog parametra")


def _print_build(name, params, use_defaults=False):
    from fabagent.printer import stl_builder
    return stl_builder.build(name, params, use_defaults)


def _builder(kind):
    return svg_builder.build if kind == "laser" else _print_build


CASES = [("laser", n) for n in LASER] + [("print", n) for n in PRINT]


def _spec(kind, name):
    return (C.LASER_TEMPLATES if kind == "laser" else C.PRINT_TEMPLATES)[name]


@pytest.mark.parametrize("kind,name", CASES)
def test_valid_params_pass(kind, name):
    res = _builder(kind)(name, _spec(kind, name).example)
    assert res.metadata["template"] == name


@pytest.mark.parametrize("kind,name", CASES)
def test_missing_required_param(kind, name):
    spec = _spec(kind, name)
    raw = dict(spec.example)
    req = [p.name for p in spec.params if p.required]
    del raw[req[0]]
    with pytest.raises(MissingParam) as e:
        _builder(kind)(name, raw, True)
    assert e.value.missing == [req[0]]


@pytest.mark.parametrize("kind,name", CASES)
def test_missing_optional_needs_flag(kind, name):
    spec = _spec(kind, name)
    optional = [p.name for p in spec.params if not p.required and p.applies_when is None]
    raw = {k: v for k, v in spec.example.items() if k not in optional}
    if not optional:   # sablon bez opcionih parametara: nista ne fali ni bez flaga
        assert _builder(kind)(name, raw).metadata["params_defaulted"] == []
        return
    with pytest.raises(MissingParam) as e:
        _builder(kind)(name, raw)
    assert set(e.value.optional_missing) == set(optional)
    res = _builder(kind)(name, raw, True)       # --use-defaults
    assert set(res.metadata["params_defaulted"]) == set(optional)


@pytest.mark.parametrize("kind,name", CASES)
def test_out_of_range_param(kind, name):
    spec = _spec(kind, name)
    p = _first_numeric(spec)
    raw = dict(spec.example, **{p.name: p.max + 1})
    with pytest.raises(OutOfRange) as e:
        _builder(kind)(name, raw)
    assert e.value.violations[0].param == p.name


# ------------------------------------------------------------- laser specificno
def _ex(name, **kw):
    return dict(C.LASER_TEMPLATES[name].example, **kw)


@pytest.mark.parametrize("shape", C.KEYCHAIN_SHAPES)
def test_keychain_all_shapes(shape):
    kw = dict(oblik=shape, sirina=45, visina=45 if shape != "pravougaonik" else 30,
              tekst="DOBOJ", visina_teksta=5)
    if shape == "pravougaonik":
        kw["radijus_ugla"] = 4
    res = svg_builder.build("privesak", _ex("privesak", **kw))
    assert len(res.design.rings("CUT")) == 2


def test_keychain_circle_requires_equal_dims():
    with pytest.raises(OutOfRange):
        svg_builder.build("privesak", _ex("privesak", visina=30))


def test_keychain_text_too_long_is_explicit_error():
    with pytest.raises(InvalidGeometry) as e:
        svg_builder.build("privesak", _ex("privesak", tekst="DOBOJDOBOJDOBOJ"))
    assert "smanji visina_teksta" in str(e.value)


def test_plate_hole_too_close_to_edge():
    with pytest.raises(OutOfRange) as e:
        svg_builder.build("plocica", _ex("plocica", rupa_promjer=8, rupa_odmak=4))
    assert e.value.violations[0].param == "rupa_odmak"


def test_plate_four_holes():
    res = svg_builder.build("plocica", _ex("plocica", broj_rupa=4, visina=60, tekst=""))
    assert len(res.design.rings("CUT")) == 5
    assert res.design.rings("ENGRAVE") == []


def test_cup_cylinder_wrap_is_circumference():
    import math
    res = svg_builder.build("casa", _ex("casa"))
    assert res.design.width == pytest.approx(math.pi * 75)


def test_cup_seam_crossing_rejected():
    with pytest.raises(InvalidGeometry) as e:
        svg_builder.build("casa", _ex("casa", ugao_centra=0))
    assert "sava" in str(e.value)


def test_cup_cone():
    res = svg_builder.build("casa", _ex("casa", promjer_gore=90, promjer_dolje=70))
    assert res.design.guides and res.design.rings("ENGRAVE")


def test_qr_inverted_needs_margin():
    with pytest.raises(OutOfRange):
        svg_builder.build("oznaka_qr", _ex("oznaka_qr", invertovano=True, margina=0))


def test_barcode_text_must_be_in_font():
    with pytest.raises(InvalidParam):
        svg_builder.build("oznaka_barkod", _ex("oznaka_barkod", sadrzaj="abc"))
    res = svg_builder.build("oznaka_barkod", _ex("oznaka_barkod", sadrzaj="abc", tekst_ispod=False))
    assert res.design.rings("ENGRAVE")


# ------------------------------------------------------------- print specificno
def _pex(name, **kw):
    return dict(C.PRINT_TEMPLATES[name].example, **kw)


def test_bracket_hole_too_close_to_fillet():
    with pytest.raises(OutOfRange):
        _print_build("nosac_l", _pex("nosac_l", rupa_od_kraja=30))


def test_bracket_holes_overlap():
    with pytest.raises(OutOfRange):
        _print_build("nosac_l", _pex("nosac_l", rupa_razmak=4))


def test_box_lid_tolerance_geometry():
    res = _print_build("kutija", _pex("kutija"))
    names = [b["name"] for b in res.metadata["bodies"]]
    assert names == ["kutija", "poklopac"]


def test_box_lip_taller_than_cavity():
    with pytest.raises(OutOfRange):
        _print_build("kutija", _pex("kutija", visina=10, visina_ruba_poklopca=9))


def test_adapter_thin_wall_rejected():
    with pytest.raises(OutOfRange):
        _print_build("adapter", _pex("adapter", d1_unutarnji=48.5))


def test_adapter_cone_wall_checked():
    # tanka stijenka samo u konusnom prelazu (kratak prelaz, veliki skok promjera)
    with pytest.raises((OutOfRange, InvalidGeometry)):
        _print_build("adapter", _pex("adapter", d1_vanjski=60, d1_unutarnji=56, d2_vanjski=20,
                                     d2_unutarnji=16, duzina_prelaza=1))


def test_cap_and_holder_build():
    assert _print_build("poklopac", _pex("poklopac")).metadata["mesh"]["watertight"]
    assert _print_build("drzac_cijevi", _pex("drzac_cijevi")).metadata["mesh"]["watertight"]


def test_holder_screw_holes_under_cup_rejected():
    with pytest.raises(OutOfRange):
        _print_build("drzac_cijevi", _pex("drzac_cijevi", rupa_razmak=30))

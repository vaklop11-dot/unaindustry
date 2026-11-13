import json
import xml.etree.ElementTree as ET

import numpy as np
import pytest

from fabagent.core import config as C
from fabagent.core import geometry2d as g2
from fabagent.core.errors import InvalidGeometry, InvalidParam, OutputError
from fabagent.laser import barcode, qr
from fabagent.laser.design import LaserDesign, check_design
from fabagent.laser.font import GLYPHS, render_text
from fabagent.laser.presets import PRESETS
from fabagent.laser.svg_builder import build, design_to_svg, write_outputs
from fabagent.laser.svg_validator import SVG_NS, parse_path_d, validate_svg_text

NS = {"s": SVG_NS}


def _square(x, y, s):
    return np.array([[x, y], [x + s, y], [x + s, y + s], [x, y + s]], float)


# ------------------------------------------------------------- SVG struktura
def test_svg_is_parsable_with_layers_and_mm_units():
    res = build("privesak", C.LASER_TEMPLATES["privesak"].example)
    root = ET.fromstring(res.svg)
    assert root.get("version") == "1.1"
    assert root.get("width").endswith("mm") and root.get("height").endswith("mm")
    w = float(root.get("width")[:-2])
    assert root.get("viewBox") == f"0 0 {root.get('width')[:-2]} {root.get('height')[:-2]}"
    assert w == pytest.approx(res.design.width)
    ids = [g.get("id") for g in root.findall("s:g", NS)]
    assert ids == list(C.LAYER_EXECUTION_ORDER)
    cut = root.find("s:g[@id='CUT']", NS)
    for path in cut.findall("s:path", NS):
        assert path.get("stroke") == C.LAYER_COLORS["CUT"] and path.get("fill") == "none"
    eng = root.find("s:g[@id='ENGRAVE']", NS)
    assert all(p.get("fill") == C.LAYER_COLORS["ENGRAVE"] for p in eng.findall("s:path", NS))
    assert root.findall(".//s:text", NS) == []   # tekst je u putanjama


def test_metadata_contents():
    res = build("privesak", C.LASER_TEMPLATES["privesak"].example)
    m = res.metadata
    assert m["layers"]["CUT"]["rings"] == 2
    assert m["layers"]["ENGRAVE"]["rings"] > 0
    assert m["time_estimate_s"]["total"] > 0
    assert m["material_preset"]["key"] == "sperploca_3mm"
    assert any(e["category"] == "preset" for e in m["decision_log"])
    json.dumps(m)  # serijalizabilno


def test_validator_detects_cut_engrave_overlap():
    d = LaserDesign(50, 50)
    d.add("CUT", [_square(5, 5, 40)])
    d.add("ENGRAVE", [_square(4, 10, 5)])          # sijece CUT liniju
    probs = check_design(d)
    assert any("CUT" in p and "ENGRAVE" in p for p in probs)
    report = validate_svg_text(design_to_svg(d, "t", "t"))
    assert not report["ok"]


def test_validator_detects_engrave_too_close_and_outside():
    d = LaserDesign(80, 50)
    d.add("CUT", [_square(5, 5, 40)])
    d.add("ENGRAVE", [_square(5.2, 10, 5)])         # 0.2 mm od reza
    d.add("ENGRAVE", [_square(60, 10, 5)])          # izvan dijela
    probs = check_design(d)
    assert any("0.200 mm" in p for p in probs)
    assert any("nije na materijalu" in p for p in probs)


def test_validator_min_wall_and_min_hole():
    d = LaserDesign(50, 50)
    d.add("CUT", [_square(5, 5, 40)])
    d.add("CUT", [g2.circle(7, 25, 1.0)])           # D=2 OK ali zid 1 mm
    d.add("CUT", [g2.circle(25, 25, 0.8)])          # D=1.6 < 2
    probs = check_design(d)
    assert any("min zid" in p for p in probs)
    assert any("rupa" in p and "min 2.0" in p for p in probs)


def test_validator_work_area():
    d = LaserDesign(C.LASER_WORK_AREA_MM[0] + 10, 10)
    d.add("CUT", [_square(1, 1, 5)])
    assert any("radne povrsine" in p for p in check_design(d))


def test_svg_validator_rejects_text_and_bad_units():
    good = build("plocica", C.LASER_TEMPLATES["plocica"].example).svg
    assert validate_svg_text(good)["ok"]
    bad_units = good.replace('mm"', 'px"', 1)
    assert not validate_svg_text(bad_units)["ok"]
    with_text = good.replace('<g id="CUT">', '<g id="CUT"><text>X</text>', 1)
    rep = validate_svg_text(with_text)
    assert not rep["ok"] and any("<text>" in e for e in rep["errors"])
    missing_layer = good.replace('<g id="SCORE" />', "")
    assert not validate_svg_text(missing_layer)["ok"]


def test_parse_path_d():
    rings = parse_path_d("M 0 0 L 10 0 10 10 Z M 1 1 L 2 1 2 2 Z")
    assert len(rings) == 2 and rings[0].shape == (3, 2)
    for bad in ("M 0 0 L 1 1 C 2 2 3 3 4 4 Z", "M 0 0 L 1 0 1 1"):
        with pytest.raises(ValueError):
            parse_path_d(bad)


# ------------------------------------------------------------- upis fajlova
def test_write_outputs_and_revalidate(tmp_path):
    res = build("oznaka_qr", C.LASER_TEMPLATES["oznaka_qr"].example)
    out = tmp_path / "qr.svg"
    paths = write_outputs(res, str(out), preview=True)
    assert out.exists()
    meta = json.loads(open(paths["json"], encoding="utf-8").read())
    assert meta["files"]["svg"]["sha256"]
    assert open(paths["png"], "rb").read(8) == b"\x89PNG\r\n\x1a\n"


def test_write_outputs_requires_svg_extension(tmp_path):
    res = build("plocica", C.LASER_TEMPLATES["plocica"].example)
    with pytest.raises(OutputError):
        write_outputs(res, str(tmp_path / "x.png"))


# ------------------------------------------------------------- font
def test_font_covers_charset_exactly():
    assert set(GLYPHS) == set(C.FONT_CHARSET)
    for ch, grid in GLYPHS.items():
        assert grid.shape == (C.FONT_ACCENT_ROWS + C.FONT_CAP_ROWS, C.FONT_CELL_COLS), ch


def test_render_text_dimensions_and_errors():
    geo = render_text("DOBOJ", 7.0, 1.0)
    assert geo.cell == pytest.approx(1.0)
    assert geo.width == pytest.approx(5 * 5 + 4 * 1)
    assert geo.top == 0.0 and geo.bottom == pytest.approx(7.0)
    assert render_text("Š", 7.0, 1.0).top < 0      # kvacica iznad
    with pytest.raises(InvalidParam):
        render_text("doboj", 7.0, 1.0)
    with pytest.raises(InvalidParam):
        render_text("A", 1.0, 1.0)                     # celija < min


# ------------------------------------------------------------- QR / barkod
@pytest.mark.parametrize("text,ecl", [("DOBOJ", "M"), ("https://example.com/p/123", "Q"),
                                      ("X" * 200, "L"), ("SN-1" * 30, "H")])
def test_qr_structure_and_reed_solomon(text, ecl):
    code = qr.encode(text, ecl)
    assert code.modules.shape == (code.size, code.size)
    assert qr.decode_format(code) == (ecl, code.mask)
    # tragaci (finder) u 3 ugla
    finder = code.modules[0:7, 0:7]
    assert finder[0].all() and finder[6].all() and finder[2:5, 2:5].all() and not finder[1, 1:6].any()
    assert (code.modules[6, 8:code.size - 8] == (np.arange(8, code.size - 8) % 2 == 0)).all()
    ecc_len = qr.ECC_CODEWORDS_PER_BLOCK[ecl][code.version]
    assert all(qr.rs_syndromes_zero(b, ecc_len) for b in code.blocks)


def test_qr_capacity_and_ascii():
    with pytest.raises(InvalidParam):
        qr.encode("Š", "M")
    with pytest.raises(InvalidParam):
        qr.encode("A" * 3000, "H")


def test_code128_table_and_checksum():
    assert all(sum(map(int, p)) == 11 for p in barcode.PATTERNS[:106])
    assert sum(map(int, barcode.PATTERNS[106])) == 13
    assert len(set(barcode.PATTERNS)) == 107
    # paritet: zbir sirina barova je paran
    assert all(sum(int(p[i]) for i in (0, 2, 4)) % 2 == 0 for p in barcode.PATTERNS[:106])
    # referentni primjer: "Wikipedia" -> kontrolna suma 88
    assert barcode.encode_values("Wikipedia")[-2] == 88
    bars, total = barcode.bars("HELLO")
    assert total == 11 * 7 + 13


# ------------------------------------------------------------- preseti
def test_presets_match_config():
    assert set(PRESETS) == set(C.LASER_MATERIALS_ALL)
    for key in C.LASER_MATERIALS_CUT:
        assert PRESETS[key].cut is not None
    for key in C.LASER_MATERIALS_ENGRAVE_ONLY:
        assert PRESETS[key].cut is None
    assert all(p.engrave.line_interval_mm for p in PRESETS.values())


def test_engrave_only_material_cannot_cut():
    ex = dict(C.LASER_TEMPLATES["oznaka_qr"].example, materijal="eloksirani_aluminij")
    with pytest.raises(InvalidParam):
        build("oznaka_qr", ex)
    ex["rez_okvir"] = False
    res = build("oznaka_qr", ex)
    assert res.design.rings("CUT") == []

import json
import math

import numpy as np
import pytest

from fabagent.core import config as C
from fabagent.core import geometry2d as g2
from fabagent.core.errors import InvalidGeometry, InvalidParam, OutOfRange
from fabagent.laser import svg_builder
from fabagent.laser.nesting import Part, pack
from fabagent.laser.svg_validator import validate_svg_file
from fabagent.laser.templates import burence as B

EX = C.LASER_TEMPLATES["burence"].example


def test_stave_math():
    geo = B.geometry(200, 150, 12, 3)
    assert geo["angle_deg"] == pytest.approx(30.0)
    a_in = 100 - C.BARREL_RIM_MM - 3
    assert geo["w"] == pytest.approx(2 * a_in * math.tan(math.pi / 12))
    # unutrasnje ivice susjednih dasaka se dodiruju: N * w = obim unutrasnjeg N-ugla
    assert 12 * geo["w"] == pytest.approx(2 * 12 * a_in * math.tan(math.pi / 12))


def test_stave_has_tabs_and_ring_holes():
    rings = B.stave(150, 50, 25, 3)
    assert len(rings) == 3
    outline = rings[0]
    assert g2.signed_area(outline) == pytest.approx(144 * 50 + 2 * 3 * 25)
    centers = [r.mean(axis=0) for r in rings[1:]]
    assert [c[0] for c in centers] == pytest.approx([50, 100])     # 1/3 i 2/3 duzine


def test_end_plate_slots_and_bottle_hole():
    geo = B.geometry(200, 150, 12, 3)
    rings = B.end_plate(200, 12, geo, 3, 75)
    assert len(rings) == 1 + 12 + 1
    hole = rings[-1]
    assert np.linalg.norm(hole - 100, axis=1).max() == pytest.approx((75 + C.BARREL_BOTTLE_CLEARANCE_MM) / 2)


def test_standard_barrel_builds_and_fits_sheets(tmp_path):
    res = svg_builder.build("burence", EX)
    sheets = res.all_sheets()
    assert res.metadata["sheet_count"] == len(sheets) >= 2
    w, h = C.LASER_WORK_AREA_MM
    for design, _ in sheets:
        assert design.width == w and design.height == h
    # 2 strane + 12 dasaka; svaka daska 3 konture, strana 14
    assert res.metadata["layers"]["CUT"]["rings"] == 2 * 14 + 12 * 3
    paths = svg_builder.write_outputs(res, str(tmp_path / "burence.svg"), preview=True)
    assert len(paths["svg_sheets"]) == len(sheets)
    for p in paths["svg_sheets"]:
        assert validate_svg_file(p)["ok"]
    meta = json.loads(open(paths["json"], encoding="utf-8").read())
    assert len(meta["sheets"]) == len(sheets)


def test_parts_do_not_overlap_and_keep_gap():
    res = svg_builder.build("burence", dict(EX, ima_fioku=True))
    for design, _ in res.all_sheets():
        outers = [it.rings[0] for it in design.items if not it.label.endswith("_unutra")]
        for i in range(len(outers)):
            for j in range(i + 1, len(outers)):
                d = g2.polyline_min_distance(outers[i], outers[j])
                assert d >= C.NEST_GAP_MM - 1e-6


def test_drawer_adds_parts():
    a = svg_builder.build("burence", EX).metadata["layers"]["CUT"]["rings"]
    b = svg_builder.build("burence", dict(EX, ima_fioku=True)).metadata["layers"]["CUT"]["rings"]
    assert b == a + 12 + 1    # 12 dijelova baze/fioke + rupa za rucicu


def test_thickness_must_match_material():
    with pytest.raises(InvalidParam):
        svg_builder.build("burence", dict(EX, debljina_mm=4))


def test_bottle_too_big_out_of_range():
    with pytest.raises(OutOfRange) as e:
        svg_builder.build("burence", dict(EX, promjer_mm=150, promjer_boce_mm=150))
    assert e.value.violations[0].param == "promjer_boce_mm"


def test_too_few_staves_tab_breaks_rim():
    # 6 dasaka na D 390: cep je toliko sirok da uglovi proreza izlaze kroz obod
    with pytest.raises(InvalidGeometry) as e:
        svg_builder.build("burence", dict(EX, promjer_mm=390, broj_dasaka=6))
    assert "povecaj broj_dasaka" in str(e.value)


def test_pack_deterministic_and_multi_sheet():
    parts = [Part(f"p{i}", [np.array([[0, 0], [150, 0], [150, 150], [0, 150]], float)]) for i in range(6)]
    a, n1 = pack(parts, 400, 400, 3, 1)
    b, n2 = pack(parts, 400, 400, 3, 1)
    assert n1 == n2 == 2
    assert [(p.sheet, p.x, p.y, p.rotated) for p in a] == [(p.sheet, p.x, p.y, p.rotated) for p in b]

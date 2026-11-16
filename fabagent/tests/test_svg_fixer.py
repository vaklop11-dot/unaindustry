import math

import numpy as np
import pytest

from fabagent.core import config as C
from fabagent.core import geometry2d as g2
from fabagent.core.errors import ValidationFailed
from fabagent.core.svg_fixer import fix_svg
from fabagent.laser.svg_validator import validate_svg_text


def _svg(body: str, w="100mm", h="100mm", vb="0 0 100 100") -> bytes:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="{vb}">'
            f"{body}</svg>").encode()


SQUARE = '<path d="M 10 10 L 60 10 L 60 60 L 10 60 Z" fill="none" stroke="#000"/>'


def test_clean_file_passes_and_is_valid():
    res = fix_svg(_svg(SQUARE))
    assert res.production_ready
    assert validate_svg_text(res.svg)["ok"]
    assert len(res.layers["CUT"]) == 1


def test_duplicate_lines_removed():
    # isti kvadrat dva puta, drugi pomjeren 0.03 mm i nacrtan u suprotnom smjeru
    dup = '<path d="M 10.03 10 L 10.03 60 L 60.03 60 L 60.03 10 Z" fill="none" stroke="#000"/>'
    line = '<line x1="70" y1="70" x2="90" y2="70" stroke="#000"/>'
    res = fix_svg(_svg(SQUARE + dup + line + line.replace('x1="70"', 'x1="70.02"')))
    assert len(res.layers["CUT"]) == 1
    assert any("Uklonjeno 2 duplih" in f for f in res.report.fixes)
    # preostala otvorena linija je blokirajuca (ne izmislja se zatvaranje)
    assert not res.production_ready
    assert any("otvorena putanja" in b for b in res.report.blocking)


def test_distinct_paths_outside_tolerance_kept():
    near = '<path d="M 10.2 10 L 60.2 10 L 60.2 60 L 10.2 60 Z" fill="none" stroke="#000"/>'
    res = fix_svg(_svg(SQUARE + near))
    assert len(res.layers["CUT"]) == 2          # 0.2 mm > 0.05 mm - nije duplikat
    assert not res.production_ready              # ali krse min zid 1.5 mm
    assert any("min zid" in b for b in res.report.blocking)


def test_open_paths_joined_into_closed_contour():
    # kvadrat od 4 odvojene linije, krajevi razmaknuti 0.05 mm, jedna obrnutog smjera
    lines = (
        '<line x1="10" y1="10" x2="50" y2="10" stroke="#000"/>'
        '<line x1="50.05" y1="10" x2="50" y2="50" stroke="#000"/>'
        '<line x1="10" y1="50" x2="50" y2="50.05" stroke="#000"/>'
        '<line x1="10" y1="50" x2="10" y2="10.05" stroke="#000"/>'
    )
    res = fix_svg(_svg(lines))
    assert res.production_ready, res.report.blocking
    assert len(res.layers["CUT"]) == 1
    ring = res.layers["CUT"][0]
    assert abs(abs(g2.signed_area(ring)) - 1600) < 5
    assert any("Spojeno 3 otvorenih" in f for f in res.report.fixes)


def test_path_with_almost_closed_end_is_closed():
    res = fix_svg(_svg('<path d="M 10 10 L 50 10 L 50 50 L 10 50 L 10 10.08" fill="none" stroke="#000"/>'))
    assert res.production_ready
    assert any("Zatvoreno 1" in f for f in res.report.fixes)


def test_open_gap_too_large_is_blocking():
    res = fix_svg(_svg('<path d="M 10 10 L 50 10 L 50 50 L 10 50 L 10 11" fill="none" stroke="#000"/>'))
    assert not res.production_ready
    assert res.open_paths and "1.00 mm" in res.report.blocking[0]


def test_raster_rejected():
    with pytest.raises(ValidationFailed) as e:
        fix_svg(_svg('<g><image href="data:image/png;base64,AAAA" width="10" height="10"/></g>'))
    assert "ugradjenu sliku" in str(e.value) and "Upload" in str(e.value)


@pytest.mark.parametrize("data", [b"nije xml", b"<html/>",
                                  b'<!DOCTYPE svg [<!ENTITY x "y">]><svg xmlns="http://www.w3.org/2000/svg"/>'])
def test_invalid_files_rejected(data):
    with pytest.raises(ValidationFailed):
        fix_svg(data)


def test_too_large_rejected():
    with pytest.raises(ValidationFailed):
        fix_svg(b" " * (C.UPLOAD_MAX_BYTES + 1))


def test_layer_classification_and_colors():
    body = (SQUARE.replace('stroke="#000"', 'stroke="#00ff00" stroke-width="2"')
            + '<circle cx="35" cy="35" r="5" fill="#333"/>'
            + '<rect x="15" y="15" width="10" height="5" fill="none" stroke="blue"/>')
    res = fix_svg(_svg(body))
    assert res.production_ready, res.report.blocking
    assert [len(res.layers[k]) for k in ("CUT", "ENGRAVE", "SCORE")] == [1, 1, 1]
    assert 'stroke="#FF0000"' in res.svg and 'stroke-width="0.1"' in res.svg
    assert 'stroke-width="2"' not in res.svg


def test_text_converted_to_paths():
    body = SQUARE + '<text x="35" y="40" font-size="10" text-anchor="middle" font-family="Arial">AB</text>'
    res = fix_svg(_svg(body))
    assert res.production_ready, res.report.blocking
    assert "<text" not in res.svg
    assert res.layers["ENGRAVE"]
    assert any("blok-fontom" in f for f in res.report.fixes)
    x0, y0, x1, y1 = g2.bbox(res.layers["ENGRAVE"])
    assert y1 == pytest.approx(40.0) and y1 - y0 == pytest.approx(7.0)     # visina = 0.7 * font-size
    assert (x0 + x1) / 2 == pytest.approx(35.0)


def test_lowercase_text_is_blocking_not_guessed():
    res = fix_svg(_svg(SQUARE + '<text x="20" y="40" font-size="10">abc</text>'))
    assert not res.production_ready
    assert any("abc" in b and "Object to Path" in b for b in res.report.blocking)


def test_units_px_and_transforms():
    # 96 px = 1 in = 25.4 mm; translate + scale u grupi
    body = ('<g transform="translate(10,10) scale(2)"><rect x="0" y="0" width="48" height="48" '
            'fill="none" stroke="#000"/></g>')
    res = fix_svg(_svg(body, w="384px", h="384px", vb="0 0 384 384"))
    assert res.width_mm == pytest.approx(101.6)
    x0, y0, x1, y1 = g2.bbox(res.layers["CUT"])
    assert x1 - x0 == pytest.approx(96 * 25.4 / 96)
    assert x0 == pytest.approx(10 * 25.4 / 96)
    assert any("1/96" in w for w in res.report.warnings)


def test_curves_flattened_within_tolerance():
    res = fix_svg(_svg('<circle cx="50" cy="50" r="20" fill="none" stroke="#000"/>'))
    ring = res.layers["CUT"][0]
    r = np.linalg.norm(ring - 50, axis=1)
    assert np.abs(r - 20).max() < 1e-6
    assert abs(abs(g2.signed_area(ring)) - math.pi * 400) < 1.0


def test_use_element_blocking():
    res = fix_svg(_svg(SQUARE + '<use href="#x"/>'))
    assert any("<use>" in b for b in res.report.blocking)


def test_geometry_outside_document_blocking():
    res = fix_svg(_svg('<rect x="80" y="80" width="40" height="40" fill="none" stroke="#000"/>'))
    assert not res.production_ready and any("izvan dokumenta" in b for b in res.report.blocking)


def test_obican_doctype_bez_unutrasnjih_deklaracija_prolazi():
    """Tako izvoze CorelDRAW i Illustrator - vanjski DTD, bez ENTITY.
    Ranije je padao svaki takav paket jer se trazilo '[' bilo gdje u zaglavlju."""
    svg = (b'<?xml version="1.0" encoding="UTF-8"?>\r\n'
           b'<!DOCTYPE svg PUBLIC "-//W3C//DTD SVG 1.1//EN"'
           b' "http://www.w3.org/Graphics/SVG/1.1/DTD/svg11.dtd">\r\n'
           b'<svg xmlns="http://www.w3.org/2000/svg" width="10mm" height="10mm"'
           b' viewBox="0 0 10 10" style="fill-rule:evenodd">'
           b'<path d="M0,0 L10,0 L10,10 L0,10 Z"/></svg>')
    r = fix_svg(svg, "corel.svg")
    assert r.svg


def test_doctype_sa_unutrasnjim_podskupom_i_dalje_pada():
    svg = (b'<!DOCTYPE svg [<!ELEMENT svg ANY>]>'
           b'<svg xmlns="http://www.w3.org/2000/svg"/>')
    with pytest.raises(ValidationFailed):
        fix_svg(svg, "zlo.svg")

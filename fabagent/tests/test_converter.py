"""converter.py: DXF/PDF/PNG/JPG -> SVG -> svg_fixer. Testni fajlovi se generisu programski."""

import io
import math

import numpy as np
import pytest

from fabagent.core import config as C
from fabagent.core import geometry2d as g2
from fabagent.core.converter import convert, detect_format
from fabagent.core.errors import InvalidParam, MissingParam, OutOfRange, ValidationFailed
from fabagent.core.svg_fixer import fix_svg

ezdxf = pytest.importorskip("ezdxf")
pymupdf = pytest.importorskip("pymupdf")


# ------------------------------------------------------------------ generatori
def make_dxf(units: int | None = 4, extra=None) -> bytes:
    doc = ezdxf.new("R2010")
    if units is not None:
        doc.header["$INSUNITS"] = units
    msp = doc.modelspace()
    # pravougaonik 60 x 40 od 4 odvojene linije (spaja ih svg_fixer)
    for a, b in (((0, 0), (60, 0)), ((60, 0), (60, 40)), ((60, 40), (0, 40)), ((0, 40), (0, 0))):
        msp.add_line(a, b)
    msp.add_circle((15, 20), 5)
    # zaobljeni pravougaonik kao LWPOLYLINE sa bulge (polukrugovi na krajevima)
    msp.add_lwpolyline([(30, 15, 0, 0, 0), (45, 15, 0, 0, 1), (45, 25, 0, 0, 0), (30, 25, 0, 0, 1)],
                       format="xyseb", close=True)
    msp.add_arc((52, 33), 3, 0, 180)
    msp.add_line((49, 33), (55, 33))   # zatvara luk u polukrug
    if extra:
        extra(msp)
    s = io.StringIO()
    doc.write(s)
    return s.getvalue().encode("utf-8")


def make_pdf(vector=True, image=False, text=False, pages=1) -> bytes:
    doc = pymupdf.open()
    for _ in range(pages):
        page = doc.new_page(width=200, height=150)          # pt
        if vector:
            page.draw_rect(pymupdf.Rect(20, 20, 120, 100), color=(0, 0, 0), width=1)
            page.draw_circle((160, 60), 20, color=(1, 0, 0), width=1)
            page.draw_rect(pymupdf.Rect(40, 40, 60, 60), color=None, fill=(0, 0, 0))
        if image:
            pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 20, 20), False)
            pix.clear_with(200)
            page.insert_image(pymupdf.Rect(130, 100, 190, 140), pixmap=pix)
        if text:
            page.insert_text((20, 130), "NATPIS", fontsize=12)
    return doc.tobytes()


def make_png(size=(120, 80), dark_rect=(20, 20, 70, 60), hole=(35, 30, 55, 50), fmt="png") -> bytes:
    w, h = size
    img = np.full((h, w), 255, np.uint8)
    x0, y0, x1, y1 = dark_rect
    img[y0:y1, x0:x1] = 20
    if hole:
        hx0, hy0, hx1, hy1 = hole
        img[hy0:hy1, hx0:hx1] = 255
    pix = pymupdf.Pixmap(pymupdf.csGRAY, w, h, img.tobytes(), False)
    return pix.tobytes(fmt)


# ------------------------------------------------------------------ opste
@pytest.mark.parametrize("name,fmt", [("a.svg", "svg"), ("A.DXF", "dxf"), ("x.pdf", "pdf"),
                                      ("s.png", "png"), ("s.jpeg", "jpg"), ("s.JPG", "jpg")])
def test_detect_format(name, fmt):
    assert detect_format(name) == fmt


def test_unsupported_format():
    with pytest.raises(InvalidParam):
        convert("crtez.ai", b"x")


# ------------------------------------------------------------------ DXF
def test_dxf_to_svg_and_fix():
    conv = convert("crtez.dxf", make_dxf())
    assert conv.svg.startswith(b"<svg") and not conv.blocking
    res = fix_svg(conv.svg, "crtez.svg")
    assert res.production_ready, res.report.blocking
    cut = res.layers["CUT"]
    assert len(cut) == 4          # okvir (spojen), krug, zaobljeni pravougaonik, polukrug
    areas = sorted(abs(g2.signed_area(r)) for r in cut)
    assert areas[-1] == pytest.approx(60 * 40, rel=1e-6)
    assert any(a == pytest.approx(math.pi * 25, rel=1e-2) for a in areas)      # tetive lukova
    assert any(a == pytest.approx(15 * 10 + math.pi * 25, rel=1e-2) for a in areas)   # bulge lukovi
    assert any(a == pytest.approx(math.pi * 9 / 2, rel=1e-2) for a in areas)
    assert any("Spojeno" in f for f in res.report.fixes)
    # y je preslikan (DXF y-gore): krug na y=20 od dna 40 mm visokog okvira -> 20 od vrha + padding
    x0, y0, x1, y1 = g2.bbox(cut)
    assert (x1 - x0, y1 - y0) == pytest.approx((60, 40))


def test_dxf_units_from_header_inches():
    conv = convert("crtez.dxf", make_dxf(units=1))
    res = fix_svg(conv.svg)
    x0, y0, x1, y1 = g2.bbox(res.layers["CUT"])
    assert x1 - x0 == pytest.approx(60 * 25.4)


def test_dxf_without_units_requires_choice():
    with pytest.raises(MissingParam) as e:
        convert("crtez.dxf", make_dxf(units=0))
    assert e.value.missing == ["jedinice"]
    conv = convert("crtez.dxf", make_dxf(units=0), dxf_units="cm")
    assert any("cm" in n for n in conv.notes)
    with pytest.raises(InvalidParam):
        convert("crtez.dxf", make_dxf(units=0), dxf_units="parsec")


def test_dxf_empty_and_unsupported():
    doc = ezdxf.new()
    s = io.StringIO()
    doc.write(s)
    with pytest.raises(ValidationFailed) as e:
        convert("prazan.dxf", s.getvalue().encode())
    assert "DXF je prazan" in str(e.value)
    conv = convert("t.dxf", make_dxf(extra=lambda m: m.add_text("ABC")))
    assert any("TEXT (1)" in b for b in conv.blocking)


def test_dxf_broken():
    with pytest.raises(ValidationFailed):
        convert("los.dxf", b"0\nSECTION\n2\nGARBAGE")


# ------------------------------------------------------------------ PDF
def test_pdf_vectors_to_svg_and_fix():
    conv = convert("crtez.pdf", make_pdf())
    assert not conv.blocking
    res = fix_svg(conv.svg)
    assert res.production_ready, res.report.blocking
    pt = 25.4 / 72
    cut = res.layers["CUT"]
    areas = sorted(abs(g2.signed_area(r)) for r in cut)
    assert areas[-1] == pytest.approx(100 * 80 * pt * pt, rel=1e-4)
    assert any(a == pytest.approx(math.pi * (20 * pt) ** 2, rel=0.01) for a in areas)
    assert len(res.layers["ENGRAVE"]) == 1      # popunjen kvadrat
    assert res.width_mm == pytest.approx(200 * pt)


def test_pdf_only_image_rejected():
    with pytest.raises(ValidationFailed) as e:
        convert("skeniran.pdf", make_pdf(vector=False, image=True))
    assert "PDF ne sadrzi vektore, samo sliku" in str(e.value)


def test_pdf_empty_rejected():
    with pytest.raises(ValidationFailed) as e:
        convert("prazan.pdf", make_pdf(vector=False))
    assert "prazan" in str(e.value)


def test_pdf_text_and_multipage_blocking():
    conv = convert("t.pdf", make_pdf(text=True))
    assert any("tekst" in b for b in conv.blocking)
    conv = convert("t.pdf", make_pdf(pages=2))
    assert any("2 stranice" in b for b in conv.blocking)


# ------------------------------------------------------------------ slike
@pytest.mark.parametrize("fmt,name", [("png", "logo.png"), ("jpg", "logo.jpg")])
def test_image_trace_and_fix(fmt, name):
    conv = convert(name, make_png(fmt=fmt), threshold=128, width_mm=60.0)
    res = fix_svg(conv.svg)
    assert res.production_ready, res.report.blocking
    eng = res.layers["ENGRAVE"]
    assert len(eng) == 2                    # vanjska kontura + rupa
    assert res.width_mm == pytest.approx(60.0) and res.height_mm == pytest.approx(40.0)
    mm = 60.0 / 120
    net = abs(sum(g2.signed_area(r) for r in eng))
    expected = (50 * 40 - 20 * 20) * mm * mm
    assert net == pytest.approx(expected, rel=0.05)     # potrace zaobljava uglove


def test_image_threshold_changes_result():
    data = make_png(dark_rect=(20, 20, 70, 60), hole=None)
    with pytest.raises(ValidationFailed) as e:
        convert("s.png", data, threshold=10, width_mm=50.0)      # 20 nije < 10
    assert "povecaj prag" in str(e.value)
    assert convert("s.png", data, threshold=30, width_mm=50.0).svg


def test_image_params_required_and_ranges():
    with pytest.raises(MissingParam) as e:
        convert("s.png", make_png())
    assert set(e.value.missing) == {"prag", "sirina_mm"}
    with pytest.raises(OutOfRange):
        convert("s.png", make_png(), threshold=300, width_mm=50.0)
    with pytest.raises(OutOfRange):
        convert("s.png", make_png(), threshold=128, width_mm=C.LASER_WORK_AREA_MM[0] + 1)


def test_image_too_many_pixels():
    side = int(math.sqrt(C.IMAGE_MAX_PIXELS)) + 10
    with pytest.raises(ValidationFailed):
        convert("big.png", make_png(size=(side, side)), threshold=128, width_mm=100.0)


def test_corrupt_image():
    with pytest.raises(ValidationFailed):
        convert("x.png", b"\x89PNG nije slika", threshold=128, width_mm=50.0)

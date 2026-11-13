"""Citanje LightBurn projekata (.lbrn / .lbrn2): oblici, grupe, tekst, slike, slojevi."""

import base64

import pytest
from svgpathtools import parse_path

from fabagent.core import config as C
from fabagent.core import lbrn
from fabagent.core.errors import ValidationFailed

H = C.LASER_WORK_AREA_MM[1]
PNG = base64.b64encode(bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108000000003a7e9b550000000a49444154789c6360000000020001e221bc330000000049454e44ae426082")).decode()


def doc(body: str, version: str = "1") -> bytes:
    return (f'<?xml version="1.0" encoding="UTF-8"?><LightBurnProject AppVersion="1.2.01" FormatVersion="{version}">'
            f'{body}</LightBurnProject>').encode()


def cut(index, kind="Cut", tag="CutSetting", **vals):
    inner = "".join(f'<{k} Value="{v}"/>' for k, v in dict(index=index, name=f"C{index:02d}", **vals).items())
    return f'<{tag} type="{kind}">{inner}</{tag}>'


def box(d):
    x0, x1, y0, y1 = parse_path(d).bbox()
    return [round(v, 3) for v in (x0, y0, x1, y1)]


def test_rect_ellipse_and_y_flip():
    out = lbrn.parse(doc(cut(2, speed=2.08333, maxPower=100) + cut(0, "Scan", speed=150, maxPower=80, numPasses=2, interval=0.12)
                         + '<Shape Type="Rect" CutIndex="2" W="90" H="55" Cr="0"><XForm>1 0 0 1 51.9367 38</XForm></Shape>'
                         + '<Shape Type="Ellipse" CutIndex="0" Rx="10" Ry="5"><XForm>1 0 0 1 100 300</XForm></Shape>'
                         + '<Shape Type="Rect" CutIndex="2" W="20" H="10" Cr="3"><XForm>0 1 -1 0 200 100</XForm></Shape>'))
    a, b, c = out["items"]
    assert box(a["d"]) == [6.937, H - 65.5, 96.937, H - 10.5] and a["layer"] == 2 and a["d"].endswith("Z")   # Y masine 10.5..65.5
    assert box(b["d"]) == [90.0, H - 305, 110.0, H - 295] and b["layer"] == 0 and b["d"].count("C") == 4
    assert box(c["d"]) == [195.0, H - 110, 205.0, H - 90] and c["d"].count("C") == 4                          # okrenut 90, zaobljen
    layers = {ly["index"]: ly for ly in out["layers"]}
    assert layers[2] == {"index": 2, "name": "C02", "mode": "Line", "speed": 125.0, "power": 100.0, "passes": 1,
                         "interval": 0.1, "output": True, "show": True}
    assert layers[0]["mode"] == "Fill" and layers[0]["speed"] == 9000.0 and layers[0]["passes"] == 2 and layers[0]["interval"] == 0.12
    assert out["counts"] == {"paths": 3, "images": 0} and out["skipped"] == {}


def test_path_v2_bezier_lines_shared_and_lineclosed():
    body = ('<Shape Type="Path" CutIndex="1" VertID="5" PrimID="7"><XForm>2 0 0 2 10 20</XForm>'
            '<VertList>V0 0c0x5c0y0c1x1V10 10c0x1c1x10c1y5V0 10c0x1c1x1</VertList><PrimList>B0 1L1 2L2 0</PrimList></Shape>'
            '<Shape Type="Path" CutIndex="1" VertID="6" PrimID="7"><XForm>1 0 0 1 100 0</XForm>'
            '<VertList>V0 0c0x5c0y0c1x1V10 10c0x1c1x10c1y5V0 10c0x1c1x1</VertList></Shape>'
            '<Shape Type="Path" CutIndex="1" VertID="8" PrimID="9"><XForm>1 0 0 1 0 0</XForm>'
            '<VertList>V0 0c0x1c1x1V30 0c0x1c1x1V30 20c0x1c1x1</VertList><PrimList>LineClosed</PrimList></Shape>'
            '<Shape Type="Path" CutIndex="1" VertID="10" PrimID="11"><XForm>1 0 0 1 0 0</XForm>'
            '<VertList>V0 0c0x1c1x1V5 0c0x1c1x1V50 50c0x1c1x1V60 50c0x1c1x1</VertList><PrimList>L0 1L2 3</PrimList></Shape>')
    out = lbrn.parse(doc(body))
    a, b, c, d = (it["d"] for it in out["items"])
    assert a == f"M10 {H - 20:g} C20 {H - 20:g} 30 {H - 30:g} 30 {H - 40:g} L10 {H - 40:g} L10 {H - 20:g} Z"   # rucke c0 prve i c1 druge tacke
    assert b.startswith("M100 400 C105 400 110 395 110 390") and b.endswith("Z")                             # PrimList preko PrimID
    assert c == "M0 400 L30 400 L30 380 Z"
    assert d == "M0 400 L5 400 M50 350 L60 350"                                                             # dvije otvorene linije


def test_format_v0_group_text_and_unknown():
    body = (cut(1) + '<Shape Type="Group" CutIndex="1"><XForm>1 0 0 1 100 50</XForm><Children>'
            '<Shape Type="Path" CutIndex="1"><XForm>1 0 0 1 0 0</XForm><V vx="0" vy="0"/><V vx="20" vy="0"/><V vx="20" vy="10" c1x="20" c1y="5"/>'
            '<P T="L" p0="0" p1="1"/><P T="B" p0="1" p1="2"/><P T="L" p0="2" p1="0"/></Shape>'
            '<Shape Type="Text" CutIndex="1" Str="AB" HasBackupPath="1"><BackupPath Type="Path" CutIndex="1"><XForm>1 0 0 1 5 5</XForm>'
            '<V vx="0" vy="0"/><V vx="4" vy="0"/><V vx="4" vy="4"/><P T="L" p0="0" p1="1"/><P T="L" p0="1" p1="2"/><P T="L" p0="2" p1="0"/></BackupPath>'
            '<XForm>1 0 0 1 5 5</XForm></Shape></Children></Shape>'
            '<Shape Type="Text" CutIndex="1" Str="bez"><XForm>1 0 0 1 0 0</XForm></Shape>'
            '<Shape Type="Nepoznat" CutIndex="1"><XForm>1 0 0 1 0 0</XForm></Shape>')
    out = lbrn.parse(doc(body, "0"))
    assert [box(it["d"]) for it in out["items"]] == [[100.0, H - 60, 120.0, H - 50], [105.0, H - 59, 109.0, H - 55]]   # grupa + dijete
    assert out["items"][0]["d"] == f"M100 {H - 50:g} L120 {H - 50:g} C120 {H - 50:g} 120 {H - 55:g} 120 {H - 60:g} L100 {H - 50:g} Z"
    assert out["skipped"] == {"tekst bez sačuvane konture": 1, "oblik Nepoznat": 1}


def test_bitmap_shared_data_and_layer_conflict():
    body = (cut(2, speed=2.08333, maxPower=100) + cut(2, "Image", "CutSetting_Img", speed=133.333, maxPower=80, interval=0.08, ditherMode="jarvis")
            + '<Shape Type="Rect" CutIndex="2" W="90" H="55" Cr="0"><XForm>1 0 0 1 50 40</XForm></Shape>'
            + f'<Shape Type="Bitmap" CutIndex="2" W="400" H="200" SourceHash="0" Data="{PNG}"><XForm>0.2 0 0 0.2 50 40</XForm></Shape>'
            + '<Shape Type="Bitmap" CutIndex="2" W="400" H="200" SourceHash="0"><XForm>0.2 0 0 0.2 150 40</XForm></Shape>'
            + '<Shape Type="Bitmap" CutIndex="2" W="400" H="200" SourceHash="9"><XForm>1 0 0 1 0 0</XForm></Shape>')
    out = lbrn.parse(doc(body))
    imgs = [it for it in out["items"] if it["type"] == "image"]
    assert len(imgs) == 2 and imgs[0]["src"] == imgs[1]["src"] and list(out["sources"]) == [imgs[0]["src"]]   # podaci samo jednom
    assert out["sources"][imgs[0]["src"]].startswith("data:image/png;base64,iVBOR")
    assert {it["layer"] for it in imgs} == {0} and out["items"][0]["layer"] == 2                             # slike premjestene iz sloja 02
    assert any("Slike iz sloja 02 su stavljene u sloj 00" in n for n in out["notes"])
    m = imgs[0]["matrix"]                                  # lokalno (x, y prema dolje) -> platno; centar slike u (50, 400 - 40)
    assert m == [0.2, 0.0, 0.0, 0.2, 50.0, H - 40.0] and imgs[1]["matrix"][4] == 150.0
    assert imgs[0]["opts"] == {"mode": "dither", "algorithm": "jarvis", "threshold": 128, "negative": False}
    layers = {ly["index"]: ly for ly in out["layers"]}
    assert layers[0]["speed"] == 8000.0 and layers[0]["interval"] == 0.08 and layers[2]["speed"] == 125.0
    assert out["skipped"] == {"slika bez podataka": 1}


def test_clamping_and_errors():
    out = lbrn.parse(doc(cut(3, speed=500, maxPower=150, numPasses=99, doOutput=0, hide=1)
                         + '<Shape Type="Rect" CutIndex="3" W="10" H="10" Cr="0"><XForm>1 0 0 1 0 0</XForm></Shape>'
                         + '<Shape Type="Rect" CutIndex="31" W="10" H="10" Cr="0"><XForm>1 0 0 1 0 0</XForm></Shape>'))
    ly = out["layers"][0]
    assert ly["speed"] == C.LAYER_SPEED_RANGE_MM_S[1] * 60 and ly["power"] == 100.0 and ly["passes"] == C.LAYER_PASSES_RANGE[1]
    assert ly["output"] is False and ly["show"] is False
    assert out["items"][1]["layer"] == 29 and len(out["notes"]) == 3
    with pytest.raises(ValidationFailed):
        lbrn.parse(b"ovo nije xml")
    with pytest.raises(ValidationFailed):
        lbrn.parse(b"<svg/>")

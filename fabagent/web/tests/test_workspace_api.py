"""Radni ekran: stranica /radni i JSON API (/api/ws/*)."""

import base64
import re

import numpy as np
import pytest
from fastapi.testclient import TestClient

cv2 = pytest.importorskip("cv2")

from fabagent.web.app import create_app  # noqa: E402

SQ = [[0, 0], [10, 0], [10, 10], [0, 10]]
SQ2 = [[5, 5], [15, 5], [15, 15], [5, 15]]
LAYER = {"index": 0, "mode": "Line", "speed_mm_min": 600, "power_pct": 40, "passes": 1, "interval_mm": 0.1}


@pytest.fixture()
def client(tmp_path):
    with TestClient(create_app(str(tmp_path / "data"), camera_source=str(tmp_path / "nema.png"))) as c:
        yield c


def test_workspace_page_is_local_and_complete(client):
    r = client.get("/radni")
    assert r.status_code == 200
    html = r.text
    for el in ("ws-canvas", "ws-tools", "ws-layers", "ws-palette", "ws-connect", "ws-frame", "ws-start", "ws-stop",
               "ws-pause", "ws-home", "ws-console", "ws-x", "ws-y", "ws-w", "ws-h"):
        assert f'id="{el}"' in html, el
    assert "/static/fabric.min.js" in html and "/static/workspace.js" in html and "Cuts / Layers" in html
    refs = re.findall(r'(?:src|href)="([^"]+)"', html)
    assert all(not u.startswith(("http://", "https://", "//")) for u in refs), refs   # nista sa interneta


def test_boolean_and_offset_api(client):
    r = client.post("/api/ws/boolean", json={"op": "union", "shapes": [[SQ], [SQ2]]})
    assert r.status_code == 200 and r.json()["area_mm2"] == 175.0 and len(r.json()["rings"]) == 1
    r = client.post("/api/ws/boolean", json={"op": "difference", "shapes": [[SQ], [SQ2]]})
    assert r.json()["area_mm2"] == 75.0
    r = client.post("/api/ws/boolean", json={"op": "intersection", "shapes": [[SQ], [SQ2]]})
    assert r.json()["area_mm2"] == 25.0
    r = client.post("/api/ws/boolean", json={"op": "difference", "shapes": [[SQ], [SQ2], [SQ]]})
    assert r.status_code == 422 and r.json()["error"] == "InvalidParam"
    r = client.post("/api/ws/boolean", json={"op": "union", "shapes": [[SQ]]})
    assert r.status_code == 422
    r = client.post("/api/ws/offset", json={"rings": [SQ], "distance": -2})
    assert r.status_code == 200 and sorted(map(tuple, r.json()["rings"][0])) == [(2, 2), (2, 8), (8, 2), (8, 8)]
    r = client.post("/api/ws/offset", json={"rings": [SQ], "distance": 0})
    assert r.status_code == 422


def test_gcode_api(client):
    doc = {"layers": [LAYER], "shapes": [{"layer": 0, "closed": True, "points": [[10, 10], [30, 10], [30, 30]]}]}
    r = client.post("/api/ws/gcode", json=doc)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["ok"] and not out["frame"] and "S400" in out["gcode"] and out["gcode"].rstrip().endswith("M2")
    assert out["stats"]["lines"] == out["gcode"].count("\n")
    fr = client.post("/api/ws/gcode", json=dict(doc, frame=True)).json()
    assert fr["frame"] and "M4" not in fr["gcode"] and "S400" not in fr["gcode"]
    r = client.post("/api/ws/gcode", json=dict(doc, shapes=[{"layer": 0, "closed": True,
                                                              "points": [[390, 10], [410, 10], [410, 30]]}]))
    assert r.status_code == 422 and r.json()["error"] == "OutOfRange"
    r = client.post("/api/ws/gcode", content="x=1", headers={"Content-Type": "application/x-www-form-urlencoded"})
    assert r.status_code == 415                                            # samo JSON (zastita od stranih stranica)


def test_text_and_fonts_api(client):
    fonts = client.get("/api/ws/fonts").json()["fonts"]
    if not fonts:
        pytest.skip("na ovom racunaru nema TTF/OTF fontova")
    assert all(set(f) == {"id", "name"} for f in fonts)                   # putanje na disku se ne salju
    r = client.post("/api/ws/text", json={"text": "FabAgent", "font": fonts[0]["id"], "height_mm": 8})
    assert r.status_code == 200 and r.json()["d"].startswith("M")
    r = client.post("/api/ws/text", json={"text": "A", "font": "nema.ttf", "height_mm": 8})
    assert r.status_code == 422 and r.json()["error"] == "InvalidParam"


def test_lbrn_api(client):
    xml = ('<LightBurnProject AppVersion="1.2.01" FormatVersion="1"><CutSetting type="Scan"><index Value="4"/><speed Value="100"/>'
           '<maxPower Value="60"/></CutSetting><Shape Type="Rect" CutIndex="4" W="30" H="20" Cr="0"><XForm>1 0 0 1 100 100</XForm></Shape>'
           '</LightBurnProject>')
    r = client.post("/api/ws/lbrn", json={"name": "a.lbrn2", "data": base64.b64encode(xml.encode()).decode()})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["ok"] and out["counts"] == {"paths": 1, "images": 0} and out["items"][0]["d"] == "M85 310 L115 310 L115 290 L85 290 Z"
    assert out["layers"][0]["index"] == 4 and out["layers"][0]["mode"] == "Fill" and out["layers"][0]["speed"] == 6000.0
    r = client.post("/api/ws/lbrn", json={"name": "a.lbrn2", "data": base64.b64encode(b"<svg/>").decode()})
    assert r.status_code == 422 and "LightBurn" in r.json()["message"]


def test_trace_shrinks_large_image(client):
    from fabagent.core import config as C
    side = int(C.IMAGE_MAX_PIXELS ** 0.5) + 400                              # vise piksela od granice trasiranja
    img = np.full((side, side), 255, np.uint8)
    cv2.circle(img, (side // 2, side // 2), side // 4, 0, -1)
    url = "data:image/png;base64," + base64.b64encode(cv2.imencode(".png", img)[1].tobytes()).decode()
    r = client.post("/api/ws/trace", json={"data": url, "threshold": 128, "width_mm": 100})
    assert r.status_code == 200, r.text
    assert "<path" in r.json()["svg"] and 'width="100mm"' in r.json()["svg"]


def test_convert_dxf_api(client):
    import io
    ezdxf = pytest.importorskip("ezdxf")

    def dxf(insunits):
        doc = ezdxf.new("R2010")
        doc.header["$INSUNITS"] = insunits
        msp = doc.modelspace()
        msp.add_lwpolyline([(0, 0), (40, 0), (40, 20), (0, 20)], close=True)
        msp.add_circle((20, 10), 5)
        buf = io.StringIO()
        doc.write(buf)
        return base64.b64encode(buf.getvalue().encode("utf-8")).decode()

    r = client.post("/api/ws/convert", json={"name": "dio.dxf", "data": dxf(4)})            # jedinice mm u fajlu
    assert r.status_code == 200, r.text
    # konverter ostavlja rub od 1 mm oko crteza: 40 x 20 mm -> dokument 42 x 22 mm
    assert r.json()["format"] == "dxf" and 'width="42mm"' in r.json()["svg"] and r.json()["svg"].count("<path") >= 2
    r = client.post("/api/ws/convert", json={"name": "dio.dxf", "data": dxf(0)})            # bez jedinica -> pita se
    assert r.status_code == 422 and "jedinice" in r.json()["message"]
    r = client.post("/api/ws/convert", json={"name": "dio.dxf", "data": dxf(0), "dxf_units": "mm"})
    assert r.status_code == 200 and 'width="42mm"' in r.json()["svg"]


def test_convert_and_trace_api(client):
    svg = '<svg xmlns="http://www.w3.org/2000/svg" width="10mm" height="10mm" viewBox="0 0 10 10"><path d="M0 0L5 5"/></svg>'
    r = client.post("/api/ws/convert", json={"name": "a.svg", "data": base64.b64encode(svg.encode()).decode()})
    assert r.status_code == 200 and r.json()["svg"] == svg and r.json()["format"] == "svg"
    r = client.post("/api/ws/convert", json={"name": "a.exe", "data": "AAAA"})
    assert r.status_code == 422
    img = np.full((60, 60), 255, np.uint8)
    cv2.circle(img, (30, 30), 18, 0, -1)
    url = "data:image/png;base64," + base64.b64encode(cv2.imencode(".png", img)[1].tobytes()).decode()
    r = client.post("/api/ws/trace", json={"data": url, "threshold": 128, "width_mm": 30})
    assert r.status_code == 200, r.text
    assert "<path" in r.json()["svg"] and 'width="30mm"' in r.json()["svg"]
    r = client.post("/api/ws/trace", json={"data": url, "threshold": "x", "width_mm": 30})
    assert r.status_code == 422

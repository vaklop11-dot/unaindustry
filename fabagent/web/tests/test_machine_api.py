"""API (/api/build ...), postavke slojeva, G-code, raspored iz editora, raster i 3D iz slike, stranice."""

import re

import numpy as np
import pytest
from fastapi.testclient import TestClient

cv2 = pytest.importorskip("cv2")

from fabagent.core import config as C  # noqa: E402
from fabagent.web.app import create_app  # noqa: E402

PRIVESAK = {"type": "laser", "template": "privesak", "params": C.LASER_TEMPLATES["privesak"].example}


@pytest.fixture()
def client(tmp_path):
    with TestClient(create_app(str(tmp_path / "data"), camera_source=str(tmp_path / "nema.png"))) as c:
        c.data_dir = tmp_path / "data"
        yield c


def build(client, body=PRIVESAK):
    r = client.post("/api/build", json=body)
    assert r.status_code == 200, r.text
    return r.json()


# ------------------------------------------------------------------ /api/build
def test_api_build_returns_files(client):
    out = build(client)
    assert out["ok"] and out["status"] == "preview" and out["type"] == "laser"
    svg = [p for n, p in out["files"].items() if n.endswith(".svg")][0]
    assert open(svg, encoding="utf-8").read().startswith("<?xml")
    assert out["urls"]["laser"] == f"/machine?job={out['job_id']}"


def test_api_build_and_approve_print(client):
    out = build(client, {"type": "print", "template": "nosac_l", "params": C.PRINT_TEMPLATES["nosac_l"].example,
                         "approve": True})
    assert out["status"] == "approved" and out["production_dir"]
    assert any(n.endswith(".stl") for n in out["files"])


def test_api_errors_same_as_cli(client):
    r = client.post("/api/build", json={"type": "laser", "template": "privesak", "params": {"oblik": "krug"}})
    assert r.status_code == 422 and r.json()["error"] == "MissingParam" and r.json()["exit_code"] == 10
    r = client.post("/api/build", json={"type": "laser", "template": "nema", "params": {}})
    assert r.json()["error"] == "UnknownTemplate"
    r = client.post("/api/build", json={"type": "rakete", "template": "x"})
    assert r.json()["error"] == "InvalidParam"


def test_api_requires_json_content_type(client):
    r = client.post("/api/build", data={"type": "laser"})          # obicna forma (moze je poslati bilo koji sajt)
    assert r.status_code == 415
    assert client.get("/api/templates").json()["privesak"]["type"] == "laser"


# ------------------------------------------------------------------ slojevi i G-code
def test_layers_defaults_save_and_validation(client):
    job = build(client)["job_id"]
    lay = client.get(f"/api/jobs/{job}/layers").json()
    assert lay["used"] == ["CUT", "ENGRAVE"] and lay["source"].startswith("preset")
    new = dict(lay["layers"])
    new["CUT"] = dict(new["CUT"], speed_mm_s=7, passes=2)
    r = client.put(f"/api/jobs/{job}/layers", json={"layers": new})
    assert r.json()["ok"]
    assert client.get(f"/api/jobs/{job}/layers").json()["source"] == "sacuvano u job.json"
    bad = dict(new, CUT=dict(new["CUT"], power_pct=180))
    r = client.put(f"/api/jobs/{job}/layers", json={"layers": bad})
    assert r.status_code == 422 and r.json()["error"] == "OutOfRange"


def test_gcode_requires_approval_and_position(client):
    job = build(client)["job_id"]
    r = client.post(f"/api/jobs/{job}/gcode", json={"x": 10, "y": 10})
    assert r.status_code == 409 and "odobri" in r.json()["message"].lower()
    frame = client.post(f"/api/jobs/{job}/gcode", json={"x": 10, "y": 10, "frame": True}).json()
    assert frame["ok"] and "S0" in frame["gcode"] and " S1" not in frame["gcode"]
    client.post(f"/api/jobs/{job}/approve", json={})
    r = client.post(f"/api/jobs/{job}/gcode", json={})
    assert r.status_code == 422 and r.json()["error"] == "MissingParam"
    r = client.post(f"/api/jobs/{job}/gcode", json={"x": 390, "y": 10})
    assert r.json()["error"] == "OutOfRange"
    out = client.post(f"/api/jobs/{job}/gcode", json={"x": 10, "y": 10}).json()
    assert out["ok"] and out["file"].endswith(".gcode")
    text = client.get(out["url"]).text
    assert "G21" in text and text.strip().splitlines()[-3].startswith("M5")
    prod = next((client.data_dir / "production").iterdir())
    assert (prod / out["file"]).is_file()                  # odobren job: G-code i u produkciji


def test_multi_sheet_burence_is_on_bed(client):
    out = build(client, {"type": "laser", "template": "burence", "params": C.LASER_TEMPLATES["burence"].example,
                         "approve": True})
    job = out["job_id"]
    r = client.post(f"/api/jobs/{job}/gcode", json={"x": 5, "y": 5})
    assert r.json()["error"] == "InvalidParam"             # raspored na stolu - pomak nije dozvoljen
    g2 = client.post(f"/api/jobs/{job}/gcode", json={"sheet": 2}).json()
    assert g2["file"].endswith("_ploca2.gcode")
    assert client.post(f"/api/jobs/{job}/gcode", json={"sheet": 9}).json()["error"] == "InvalidParam"


# ------------------------------------------------------------------ editor / raspored
def test_editor_layout_translation(client):
    job = build(client)["job_id"]
    data = client.get(f"/api/jobs/{job}/editor-data").json()
    assert data["ok"] and len(data["items"]) == 3 and data["bed_mm"] == list(C.LASER_WORK_AREA_MM)
    items = [{"index": it["index"], "matrix": [1, 0, 0, 1, 100, 50], "path_offset": [0, 0]} for it in data["items"]]
    out = client.post(f"/api/jobs/{job}/layout", json={"items": items}).json()
    assert out["ok"] and out["template"] == "privesak"
    lay = client.get(f"/api/jobs/{out['job_id']}/editor-data").json()
    assert lay["on_bed"] and lay["doc_mm"] == list(C.LASER_WORK_AREA_MM)
    # svaka tacka pomjerena za (100, 50)
    first_src = re.findall(r"M ([\d.]+) ([\d.]+)", data["items"][2]["d"])[0]
    first_new = re.findall(r"M ([\d.]+) ([\d.]+)", lay["items"][2]["d"])[0]
    assert float(first_new[0]) == pytest.approx(float(first_src[0]) + 100, abs=1e-3)
    assert float(first_new[1]) == pytest.approx(float(first_src[1]) + 50, abs=1e-3)
    assert client.get(f"/history/{out['job_id']}").status_code == 200


def test_editor_layout_rejects_outside_and_bad_input(client):
    job = build(client)["job_id"]
    n = len(client.get(f"/api/jobs/{job}/editor-data").json()["items"])
    far = [{"index": i, "matrix": [1, 0, 0, 1, 390, 0], "path_offset": [0, 0]} for i in range(n)]
    r = client.post(f"/api/jobs/{job}/layout", json={"items": far})
    assert r.status_code == 422 and r.json()["error"] == "InvalidGeometry"
    r = client.post(f"/api/jobs/{job}/layout", json={"items": far[:1]})
    assert r.json()["error"] == "InvalidParam"
    sing = [{"index": i, "matrix": [0, 0, 0, 0, 0, 0], "path_offset": [0, 0]} for i in range(n)]
    assert client.post(f"/api/jobs/{job}/layout", json={"items": sing}).json()["error"] == "InvalidParam"


def test_editor_and_machine_pages_local_assets(client):
    job = build(client)["job_id"]
    for url in (f"/editor/{job}", f"/machine?job={job}", "/machine"):
        html = client.get(url).text
        refs = re.findall(r'(?:src|href)="([^"]+)"', html)
        assert all(not r.startswith(("http://", "https://", "//")) for r in refs), url
    ed = client.get(f"/editor/{job}").text
    assert "/static/fabric.min.js" in ed and 'id="editor-canvas"' in ed and "ruler-top" in ed
    m = client.get(f"/machine?job={job}").text
    for b in ("m-connect", "m-home", "m-frame", "m-start", "m-pause", "m-stop", "m-status"):
        assert f'id="{b}"' in m
    assert "Otkopčano" in m and 'id="m-not-approved"' in m
    assert client.get("/static/fabric.min.js").status_code == 200


# ------------------------------------------------------------------ slika: raster i 3D
def _photo_png():
    img = np.tile(np.linspace(0, 255, 120), (80, 1)).astype(np.uint8)
    cv2.circle(img, (60, 40), 20, 0, -1)
    return cv2.imencode(".png", img)[1].tobytes()


def test_upload_image_dither_raster_and_gcode(client):
    r = client.post("/upload", data={"materijal": "sperploca_3mm", "izlaz": "2d", "rezim_slike": "dither",
                                     "algoritam": "stucki", "sirina_mm": "30", "interval_mm": "0.25"},
                    files={"fajl": ("foto.png", _photo_png(), "image/png")})
    assert r.status_code == 200, r.text[:300]
    assert 'id="img-raster"' in r.text and "dither" in r.text
    job = re.search(r'name="job_id" value="([^"]+)"', r.text).group(1)
    lay = client.get(f"/api/jobs/{job}/layers").json()
    assert lay["used"] == ["ENGRAVE"] and lay["locked_interval"] == 0.25
    client.post(f"/api/jobs/{job}/approve", json={})
    out = client.post(f"/api/jobs/{job}/gcode", json={"x": 20, "y": 20}).json()
    assert out["ok"] and out["stats"]["pixels"] == [120, 80]
    bad = dict(lay["layers"]["ENGRAVE"], interval_mm=0.1)
    assert client.put(f"/api/jobs/{job}/layers", json={"layers": {"ENGRAVE": bad}}).json()["error"] == "InvalidParam"


def test_upload_image_grayscale_requires_width(client):
    r = client.post("/upload", data={"materijal": "sperploca_3mm", "izlaz": "2d", "rezim_slike": "grayscale"},
                    files={"fajl": ("foto.png", _photo_png(), "image/png")})
    assert r.status_code == 422 and "MissingParam" in r.text and "sirina_mm" in r.text


def test_upload_image_vectorize_goes_to_score(client):
    r = client.post("/upload", data={"materijal": "sperploca_3mm", "izlaz": "2d", "rezim_slike": "vectorize",
                                     "prag": "100", "sirina_mm": "40"},
                    files={"fajl": ("foto.png", _photo_png(), "image/png")})
    assert r.status_code == 200, r.text[:300]
    assert "SCORE (plavo)" in r.text


def test_upload_image_lithophane(client):
    r = client.post("/upload", data={"izlaz": "3d", "nacin_3d": "litofanija", "sirina_mm": "60",
                                     "min_debljina_mm": "0.8", "max_debljina_mm": "3", "filament": "PLA"},
                    files={"fajl": ("foto.png", _photo_png(), "image/png")})
    assert r.status_code == 200, r.text[:400]
    assert "litofanija" in r.text and "✅" in r.text
    job = re.search(r'name="job_id" value="([^"]+)"', r.text).group(1)
    for v in ("front", "iso"):
        assert client.get(f"/jobs/{job}/view_{v}.png").status_code == 200
    r = client.post("/job/approve", data={"job_id": job}, follow_redirects=False)
    assert r.status_code == 303
    prod = next((client.data_dir / "production").iterdir())
    assert any(p.suffix == ".stl" for p in prod.iterdir())
    r = client.post("/upload", data={"izlaz": "3d", "nacin_3d": "litofanija", "sirina_mm": "60",
                                     "min_debljina_mm": "0.8", "filament": "PLA"},
                    files={"fajl": ("foto.png", _photo_png(), "image/png")})
    assert r.status_code == 422 and "max_debljina_mm" in r.text

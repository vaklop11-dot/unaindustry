"""Web: kamera (staticka slika umjesto USB kamere), kalibracija, overlay i napredno trasiranje."""

import json
import re

import numpy as np
import pytest
from fastapi.testclient import TestClient

cv2 = pytest.importorskip("cv2")

from fabagent.core import config as C  # noqa: E402
from fabagent.tests.test_camera import CORNERS, synthetic_frame  # noqa: E402
from fabagent.web.app import create_app  # noqa: E402


@pytest.fixture()
def frame_path(tmp_path):
    p = tmp_path / "kamera.png"
    cv2.imwrite(str(p), synthetic_frame())
    return p


@pytest.fixture()
def client(tmp_path, frame_path):
    with TestClient(create_app(str(tmp_path / "data"), camera_source=str(frame_path))) as c:
        c.data_dir = tmp_path / "data"
        yield c


def _calib_form(points=CORNERS, size=(640, 480)):
    form = {"frame_w": str(size[0]), "frame_h": str(size[1])}
    for i, (x, y) in enumerate(points, start=1):
        form[f"x{i}"], form[f"y{i}"] = str(x), str(y)
    return form


# ------------------------------------------------------------------ kamera
def test_camera_page_and_snapshot(client):
    html = client.get("/camera").text
    assert 'id="live"' in html and 'id="calib-img"' in html and 'id="no-calibration"' in html
    r = client.get("/camera/snapshot.jpg")
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg" and r.content[:2] == b"\xff\xd8"


def test_camera_stream_mjpeg(client):
    with client.stream("GET", "/camera/stream?frames=2") as r:
        assert r.headers["content-type"].startswith("multipart/x-mixed-replace")
        body = b"".join(r.iter_bytes())
    assert body.count(b"--frame") == 2 and body.count(b"\xff\xd8") >= 2


def test_calibrate_and_warp(client):
    assert client.get("/camera/warped.jpg").status_code == 503       # jos nije kalibrisano
    assert client.get("/camera/status").json()["calibrated"] is False
    r = client.post("/camera/calibrate", data=_calib_form())
    assert r.status_code == 200 and "Kalibracija sacuvana" in r.text and 'id="warped"' in r.text
    st = client.get("/camera/status").json()
    assert st["calibrated"] and st["area_mm"] == list(C.LASER_WORK_AREA_MM)
    saved = json.loads((client.data_dir / "kamera" / "kalibracija.json").read_text(encoding="utf-8"))
    assert saved["frame_size"] == [640, 480]
    w = client.get("/camera/warped.jpg")
    assert w.status_code == 200
    img = cv2.imdecode(np.frombuffer(w.content, np.uint8), cv2.IMREAD_COLOR)
    ppm = C.CAMERA_PX_PER_MM
    assert img.shape[:2] == (int(C.LASER_WORK_AREA_MM[1] * ppm), int(C.LASER_WORK_AREA_MM[0] * ppm))
    # marker gore lijevo (crveni u BGR = (0,0,255)) je na 20 mm od ugla
    px = img[int(20 * ppm), int(20 * ppm)].astype(int)
    assert px[2] > 180 and px[0] < 80


def test_calibrate_errors(client):
    r = client.post("/camera/calibrate", data=_calib_form(CORNERS[::-1]))
    assert r.status_code == 422 and "InvalidGeometry" in r.text and "redoslijedu" in r.text
    form = _calib_form()
    del form["x3"]
    r = client.post("/camera/calibrate", data=form)
    assert r.status_code == 422 and "MissingParam" in r.text
    r = client.post("/camera/calibrate", data=_calib_form(size=(1280, 720)))
    assert r.status_code == 422 and "osvjezi sliku" in r.text


def test_camera_unavailable_is_clear(tmp_path):
    with TestClient(create_app(str(tmp_path / "d"), camera_source=str(tmp_path / "nema.png"))) as c:
        html = c.get("/camera").text
        assert 'id="camera-error"' in html and "FABAGENT_CAMERA" in html
        r = c.get("/camera/snapshot.jpg")
        assert r.status_code == 503 and "nije ni indeks" in r.text


def test_laser_preview_has_camera_controls(client):
    ex = {k: (str(v).lower() if isinstance(v, bool) else str(v))
          for k, v in C.LASER_TEMPLATES["privesak"].example.items()}
    html = client.post("/laser/privesak/preview", data=ex).text
    for cls in ("cam-show", "cam-refresh", "cam-hide", "cam-x", "cam-y"):
        assert cls in html
    assert "Prikaži kameru" in html and "Osvježi kameru" in html and "Sakrij kameru" in html


# ------------------------------------------------------------------ napredno trasiranje
def _noisy_png():
    from fabagent.tests.test_image_trace import noisy_circle
    return cv2.imencode(".png", noisy_circle())[1].tobytes()


def test_upload_image_with_advanced_options_and_views(client):
    r = client.post("/upload", data={"materijal": "sperploca_3mm", "sirina_mm": "50", "prag": "128",
                                     "uklanjanje_suma": "2", "pojednostavljenje": "0.5", "motor": "konture"},
                    files={"fajl": ("krug.png", _noisy_png(), "image/png")})
    assert r.status_code == 200, r.text[:400]
    html = r.text
    assert "uklanjanje suma 2" in html and "RDP 0.5 px" in html and "motor konture" in html
    for btn in ("btn-show-result", "btn-show-original", "btn-show-bitmap"):
        assert f'id="{btn}"' in html
    assert 'id="retrace-form"' in html
    job_id = re.search(r'name="job_id" value="([^"]+)"', html).group(1)
    assert client.get(f"/jobs/{job_id}/obradjena_bitmapa.png").status_code == 200

    # ponovna obrada istog fajla sa drugim postavkama (bez uploada)
    r2 = client.post("/upload/retrace", data={"job_id": job_id, "materijal": "sperploca_3mm",
                                              "sirina_mm": "50", "prag": "128", "motor": "potrace"})
    assert r2.status_code == 200 and "motor potrace" in r2.text
    job2 = re.search(r'name="job_id" value="([^"]+)"', r2.text).group(1)
    assert job2 != job_id


def test_upload_image_option_errors(client):
    base = {"materijal": "sperploca_3mm", "sirina_mm": "50"}
    r = client.post("/upload", data=dict(base, prag="100", cutoff="150"),
                    files={"fajl": ("k.png", _noisy_png(), "image/png")})
    assert r.status_code == 422 and "OutOfRange" in r.text and "cutoff" in r.text
    r = client.post("/upload", data=dict(base, nacin_praga="adaptivni"),
                    files={"fajl": ("k.png", _noisy_png(), "image/png")})
    assert r.status_code == 200                       # adaptivni prag ne trazi threshold
    r = client.post("/upload", data=dict(base, prag="128", motor="vtracer"),
                    files={"fajl": ("k.png", _noisy_png(), "image/png")})
    from fabagent.core.image_trace import engine_available
    if not engine_available("vtracer"):
        assert r.status_code == 422 and "vtracer" in r.text


def test_pdf_with_only_image_is_traced(client):
    pymupdf = pytest.importorskip("pymupdf")
    doc = pymupdf.open()
    page = doc.new_page(width=300, height=300)
    page.insert_image(pymupdf.Rect(50, 50, 192, 192), stream=_noisy_png())     # 142 pt = 50.1 mm
    data = doc.tobytes()
    r = client.post("/upload", data={"materijal": "sperploca_3mm"},
                    files={"fajl": ("sken.pdf", data, "application/pdf")})
    assert r.status_code == 422 and "PDF ne sadrzi vektore, samo sliku" in r.text   # bez parametara
    r = client.post("/upload", data={"materijal": "sperploca_3mm", "prag": "128", "uklanjanje_suma": "2"},
                    files={"fajl": ("sken.pdf", data, "application/pdf")})
    assert r.status_code == 200, r.text[:400]
    assert "slika sa stranice 1" in r.text and "je trasirana" in r.text
    assert 'id="btn-show-bitmap"' in r.text

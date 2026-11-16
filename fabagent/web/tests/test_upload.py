"""Upload SVG kroz web + burence (vise ploca) kroz web."""

import json
import re

import pytest
from fastapi.testclient import TestClient

from fabagent.core import config as C
from fabagent.web.app import create_app

GOOD = (b'<svg xmlns="http://www.w3.org/2000/svg" width="100mm" height="80mm" viewBox="0 0 100 80">'
        b'<line x1="10" y1="10" x2="60" y2="10" stroke="#000" stroke-width="3"/>'
        b'<line x1="60.04" y1="10" x2="60" y2="60" stroke="#000"/>'
        b'<line x1="60" y1="60" x2="10" y2="60" stroke="#000"/>'
        b'<line x1="10" y1="60" x2="10" y2="10.03" stroke="#000"/>'
        b'<rect x="70" y="10" width="20" height="20" fill="none" stroke="#000"/>'
        b'<rect x="70" y="10" width="20" height="20" fill="none" stroke="#000"/>'
        b'<text x="35" y="40" font-size="10" text-anchor="middle">OK</text>'
        b'</svg>')
OPEN = (b'<svg xmlns="http://www.w3.org/2000/svg" width="100mm" height="80mm" viewBox="0 0 100 80">'
        b'<path d="M 10 10 L 60 10 L 60 60 L 10 60" fill="none" stroke="#000"/></svg>')
RASTER = (b'<svg xmlns="http://www.w3.org/2000/svg" width="10mm" height="10mm" viewBox="0 0 10 10">'
          b'<image href="data:image/png;base64,AAAA" width="10" height="10"/></svg>')


@pytest.fixture()
def client(tmp_path):
    with TestClient(create_app(str(tmp_path))) as c:
        c.data_dir = tmp_path
        yield c


def _upload(client, data, name="dizajn.svg", materijal="sperploca_3mm"):
    return client.post("/upload", data={"materijal": materijal},
                       files={"fajl": (name, data, "image/svg+xml")})


def _job_id(html):
    return re.search(r'name="job_id" value="([^"]+)"', html).group(1)


def test_index_has_upload_button(client):
    html = client.get("/").text
    assert 'href="/upload"' in html and "Uploaduj SVG" in html
    assert client.get("/upload").status_code == 200


def test_upload_fix_side_by_side_and_approve(client):
    r = _upload(client, GOOD)
    assert r.status_code == 200
    html = r.text
    assert 'id="img-original"' in html and 'id="img-fixed"' in html
    assert "Spojeno 3 otvorenih linija" in html
    assert "Uklonjeno 1 duplih linija" in html
    assert "blok-fontom" in html
    assert not re.search(r'id="btn-approve"[^>]*disabled', html)
    job_id = _job_id(html)
    r = client.post("/upload/approve", data={"job_id": job_id})
    assert r.status_code == 200 and "Odobreno i poslano u produkciju" in r.text
    prod = next((client.data_dir / "production").iterdir())
    assert prod.name.endswith("_svg_upload_dizajn")
    job = json.loads((prod / "job.json").read_text(encoding="utf-8"))
    assert job["type"] == "upload" and job["fix_report"]["fixes"]
    svg = (prod / "dizajn.svg").read_text(encoding="utf-8")
    assert "<text" not in svg and 'stroke-width="0.1"' in svg
    assert "izvor.svg" not in [p.name for p in prod.iterdir()]


def test_upload_with_unresolved_open_path_blocks_approve(client):
    r = _upload(client, OPEN)
    assert r.status_code == 200
    assert 'id="blocking"' in r.text and "otvorena putanja" in r.text
    assert re.search(r'id="btn-approve"[^>]*disabled', r.text)
    r = client.post("/upload/approve", data={"job_id": _job_id(r.text)})
    assert r.status_code == 422
    assert list((client.data_dir / "production").iterdir()) == []


def test_raster_rejected_with_message(client):
    r = _upload(client, RASTER)
    assert r.status_code == 422
    assert "ugradjenu sliku" in r.text and "Upload" in r.text
    assert list((client.data_dir / "jobs").iterdir()) == []


def test_upload_requires_material_and_file(client):
    r = _upload(client, GOOD, materijal="")
    assert r.status_code == 422 and "MissingParam" in r.text
    r = client.post("/upload", data={"materijal": "sperploca_3mm"})
    assert r.status_code == 422 and "MissingParam" in r.text
    r = _upload(client, GOOD, name="crtez.ai")      # .png je od sada podrzan; .ai nije
    assert r.status_code == 422 and "InvalidParam" in r.text


def test_upload_too_large(client):
    big = GOOD.replace(b"</svg>", b"<!--" + b"x" * C.UPLOAD_MAX_BYTES + b"--></svg>")
    r = _upload(client, big)
    assert r.status_code == 422 and "MB" in r.text


def test_upload_in_history(client):
    job_id = _job_id(_upload(client, GOOD).text)
    assert "Upload SVG" in client.get("/history").text
    detail = client.get(f"/history/{job_id}")
    assert detail.status_code == 200 and 'id="img-original"' in detail.text
    assert client.get(f"/jobs/{job_id}/izvor.svg").status_code == 200


def test_burence_multi_sheet_preview_and_approve(client):
    ex = {k: (str(v).lower() if isinstance(v, bool) else str(v))
          for k, v in C.LASER_TEMPLATES["burence"].example.items()}
    r = client.post("/laser/burence/preview", data=ex)
    assert r.status_code == 200
    assert "Ploča 2" in r.text and r.text.count('class="svg-container"') >= 2
    job_id = _job_id(r.text)
    r = client.post("/laser/burence/approve", data={"job_id": job_id})
    assert r.status_code == 200
    prod = next((client.data_dir / "production").iterdir())
    names = sorted(p.name for p in prod.iterdir())
    assert "burence_" in names[0]
    assert sum(n.endswith(".svg") for n in names) >= 2
    assert any(n.endswith("_ploca2.svg") for n in names)


def test_every_template_has_display_label():
    from fabagent.web import labels as L
    for name in list(C.LASER_TEMPLATES) + list(C.PRINT_TEMPLATES) + ["svg_upload"]:
        assert name in L.TEMPLATE_LABELS, name


# ------------------------------------------------------------------ interaktivni pregled
def test_interactive_preview_assets_and_layer_manager(client):
    js = client.get("/static/svg-pan-zoom.min.js")
    assert js.status_code == 200 and b"svg-pan-zoom v3.6.1" in js.content
    assert "/static/svg-pan-zoom.min.js" in client.get("/").text
    ex = {k: (str(v).lower() if isinstance(v, bool) else str(v))
          for k, v in C.LASER_TEMPLATES["privesak"].example.items()}
    html = client.post("/laser/privesak/preview", data=ex).text
    for layer in ("CUT", "ENGRAVE", "SCORE"):
        assert f'data-layer="{layer}"' in html
    assert 'id="preview-stats"' in html and "Procijenjeno vrijeme" in html and "Broj kontura" in html
    assert "<?xml" not in html.split('id="svg-container"', 1)[1][:200]


# ------------------------------------------------------------------ drugi formati
ezdxf = pytest.importorskip("ezdxf")
pymupdf = pytest.importorskip("pymupdf")


def _dxf_bytes(units=4):
    import io
    doc = ezdxf.new()
    doc.header["$INSUNITS"] = units
    msp = doc.modelspace()
    for a, b in (((0, 0), (50, 0)), ((50, 0), (50, 30)), ((50, 30), (0, 30)), ((0, 30), (0, 0))):
        msp.add_line(a, b)
    msp.add_circle((25, 15), 6)
    s = io.StringIO()
    doc.write(s)
    return s.getvalue().encode()


def test_upload_dxf(client):
    r = client.post("/upload", data={"materijal": "sperploca_3mm"},
                    files={"fajl": ("ploca.dxf", _dxf_bytes(), "application/dxf")})
    assert r.status_code == 200, r.text[:500]
    assert "Konverzija DXF" in r.text and "Spojeno" in r.text
    assert not re.search(r'id="btn-approve"[^>]*disabled', r.text)
    job_id = _job_id(r.text)
    assert client.post("/upload/approve", data={"job_id": job_id}).status_code == 200


def test_upload_dxf_without_units_asks(client):
    r = client.post("/upload", data={"materijal": "sperploca_3mm"},
                    files={"fajl": ("x.dxf", _dxf_bytes(units=0), "application/dxf")})
    assert r.status_code == 422 and "jedinice" in r.text
    r = client.post("/upload", data={"materijal": "sperploca_3mm", "jedinice": "mm"},
                    files={"fajl": ("x.dxf", _dxf_bytes(units=0), "application/dxf")})
    assert r.status_code == 200


def test_upload_pdf_vector_and_image_only(client):
    doc = pymupdf.open()
    page = doc.new_page(width=200, height=150)
    page.draw_rect(pymupdf.Rect(20, 20, 120, 100), color=(0, 0, 0), width=1)
    r = client.post("/upload", data={"materijal": "sperploca_3mm"},
                    files={"fajl": ("crtez.pdf", doc.tobytes(), "application/pdf")})
    assert r.status_code == 200 and "Konverzija PDF" in r.text
    doc = pymupdf.open()
    page = doc.new_page(width=200, height=150)
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 10, 10), False)
    pix.clear_with(100)
    page.insert_image(pymupdf.Rect(10, 10, 100, 100), pixmap=pix)
    r = client.post("/upload", data={"materijal": "sperploca_3mm"},
                    files={"fajl": ("sken.pdf", doc.tobytes(), "application/pdf")})
    assert r.status_code == 422 and "PDF ne sadrzi vektore, samo sliku" in r.text


def test_upload_png_with_threshold(client):
    import numpy as np
    img = np.full((60, 90), 255, np.uint8)
    img[10:50, 10:80] = 0
    png = pymupdf.Pixmap(pymupdf.csGRAY, 90, 60, img.tobytes(), False).tobytes("png")
    r = client.post("/upload", data={"materijal": "sperploca_3mm"},
                    files={"fajl": ("logo.png", png, "image/png")})
    assert r.status_code == 422 and "MissingParam" in r.text and "prag" in r.text
    r = client.post("/upload", data={"materijal": "sperploca_3mm", "prag": "128", "sirina_mm": "45"},
                    files={"fajl": ("logo.png", png, "image/png")})
    assert r.status_code == 200, r.text[:500]
    assert "Konverzija IMAGE" in r.text and "data:image/png;base64," in r.text
    r = client.post("/upload", data={"materijal": "sperploca_3mm", "prag": "12,5", "sirina_mm": "45"},
                    files={"fajl": ("logo.png", png, "image/png")})
    assert r.status_code == 422 and "InvalidParam" in r.text


def test_upload_unsupported_extension(client):
    r = client.post("/upload", data={"materijal": "sperploca_3mm"},
                    files={"fajl": ("crtez.ai", b"x", "application/octet-stream")})
    assert r.status_code == 422 and "nije podrzan" in r.text

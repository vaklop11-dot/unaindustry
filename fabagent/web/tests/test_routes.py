"""Web sloj: rute, greske identicne CLI-u, pregled, odobravanje, istorija, offline."""

import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from fabagent.core import config as C
from fabagent.core.errors import MissingParam
from fabagent.web import labels as L
from fabagent.web.app import create_app


@pytest.fixture()
def client(tmp_path):
    app = create_app(str(tmp_path))
    with TestClient(app) as c:
        c.data_dir = tmp_path
        yield c


def _form(example: dict) -> dict:
    return {k: (str(v).lower() if isinstance(v, bool) else str(v)) for k, v in example.items()}


def _job_id(html: str) -> str:
    m = re.search(r'name="job_id" value="([^"]+)"', html)
    assert m, "nema job_id u odgovoru"
    return m.group(1)


# ------------------------------------------------------------- osnovno
def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"


def test_index_lists_all_templates(client):
    html = client.get("/").text
    for name in list(C.LASER_TEMPLATES) + list(C.PRINT_TEMPLATES):
        assert f'href="/{"laser" if name in C.LASER_TEMPLATES else "print"}/{name}"' in html
        assert L.template_label(name) in html
    assert "3D štampa" in html


def test_pages_use_only_local_assets(client):
    for url in ("/", "/laser/privesak", "/print/nosac_l", "/history"):
        html = client.get(url).text
        refs = re.findall(r'(?:src|href)="([^"]+)"', html)
        assert all(not r.startswith(("http://", "https://", "//")) for r in refs), refs
    assert client.get("/static/bootstrap.min.css").status_code == 200
    assert client.get("/static/bootstrap.bundle.min.js").status_code == 200


@pytest.mark.parametrize("kind,specs", [("laser", C.LASER_TEMPLATES), ("print", C.PRINT_TEMPLATES)])
def test_every_form_renders_with_labels(client, kind, specs):
    for name, spec in specs.items():
        html = client.get(f"/{kind}/{name}").text
        for p in spec.params:
            assert f'name="{p.name}"' in html
            assert L.param_label(name, p) in html


def test_every_param_has_label():
    for spec in list(C.LASER_TEMPLATES.values()) + list(C.PRINT_TEMPLATES.values()):
        for p in spec.params:
            assert p.name in L.PARAM_LABELS, p.name


def test_label_with_unit():
    spec = C.PRINT_TEMPLATES["nosac_l"]
    assert L.param_label("nosac_l", spec.param("duzina_a")) == "Dužina kraka A (mm)"


def test_unknown_template_404(client):
    r = client.get("/laser/nepostoji")
    assert r.status_code == 404 and "UnknownTemplate" in r.text


def test_query_prefill_and_recommended(client):
    html = client.get("/laser/privesak?tekst=DOBOJ").text
    assert 'value="DOBOJ"' in html
    html = client.get("/laser/privesak?tekst=DOBOJ&preporuceno=1").text
    assert 'name="rupa_odmak"\n               value="2.5"' in html or 'value="2.5"' in html


# ------------------------------------------------------------- greske kao u CLI
def test_missing_param_same_as_cli(client):
    form = _form(C.LASER_TEMPLATES["privesak"].example)
    del form["sirina"]
    r = client.post("/laser/privesak/preview", data=form)
    assert r.status_code == 422
    assert "MissingParam" in r.text and "sirina" in r.text
    assert "Obavezno polje" in r.text
    assert 'value="DOBOJ"' in r.text          # unos nije izgubljen


def test_optional_missing_without_defaults(client):
    form = _form(C.LASER_TEMPLATES["privesak"].example)
    del form["rupa_odmak"]
    r = client.post("/laser/privesak/preview", data=form)
    assert r.status_code == 422 and "--use-defaults" in r.text
    r = client.post("/laser/privesak/preview", data=dict(form, use_defaults="1"))
    assert r.status_code == 200 and "rupa_odmak" in r.text   # prikazano kao preporuceno


def test_out_of_range_same_as_cli(client):
    form = dict(_form(C.LASER_TEMPLATES["privesak"].example), sirina="500")
    r = client.post("/laser/privesak/preview", data=form)
    assert r.status_code == 422 and "OutOfRange" in r.text and "[15, 100]" in r.text


def test_invalid_geometry_disables_approve(client):
    form = dict(_form(C.LASER_TEMPLATES["privesak"].example), tekst="DOBOJDOBOJDOBOJ")
    r = client.post("/laser/privesak/preview", data=form)
    assert r.status_code == 422 and "InvalidGeometry" in r.text
    assert re.search(r"<button[^>]*disabled[^>]*>Odobri", r.text)
    assert list((client.data_dir / "jobs").iterdir()) == []


def test_invalid_param_comma_decimal(client):
    form = dict(_form(C.LASER_TEMPLATES["privesak"].example), sirina="40,5")
    r = client.post("/laser/privesak/preview", data=form)
    assert r.status_code == 422 and "InvalidParam" in r.text and "tacku" in r.text


def test_print_invalid_geometry_banner(client):
    form = dict(_form(C.PRINT_TEMPLATES["adapter"].example), d1_vanjski="60", d1_unutarnji="56",
                d2_vanjski="20", d2_unutarnji="16", duzina_prelaza="1")
    r = client.post("/print/adapter/preview", data=form)
    assert r.status_code == 422 and "alert-danger" in r.text


# ------------------------------------------------------------- pregled + odobravanje
def test_laser_preview_and_approve(client):
    r = client.post("/laser/privesak/preview", data=_form(C.LASER_TEMPLATES["privesak"].example))
    assert r.status_code == 200
    assert 'id="svg-container"' in r.text and '<g id="CUT">' in r.text      # SVG je direktno u DOM-u
    assert "crvena = CUT" in r.text and "Preset nije provjeren" in r.text
    job_id = _job_id(r.text)

    r = client.post("/laser/privesak/approve", data={"job_id": job_id})
    assert r.status_code == 200 and "Odobreno i poslano u produkciju" in r.text
    prod = list((client.data_dir / "production").iterdir())
    assert len(prod) == 1 and re.match(r"\d{4}-\d{2}-\d{2}_\d{4}_privesak_doboj$", prod[0].name)
    job = json.loads((prod[0] / "job.json").read_text(encoding="utf-8"))
    assert job["type"] == "laser" and job["template"] == "privesak"
    assert job["preset"] == "sperploca_3mm" and job["preset_verified"] is False
    assert job["estimates"]["vrijeme_s"] > 0 and job["estimates"]["masa_g"] is None
    assert sorted(job["outputs"]) == sorted(p.name for p in prod[0].iterdir() if p.name != "job.json")
    assert any("NIJE verifikovan" in w for w in job["warnings"])

    # drugo odobravanje istog joba ne pravi duplikat
    client.post("/laser/privesak/approve", data={"job_id": job_id})
    assert len(list((client.data_dir / "production").iterdir())) == 1


def test_print_preview_renders_and_approve(client):
    r = client.post("/print/nosac_l/preview", data=_form(C.PRINT_TEMPLATES["nosac_l"].example))
    assert r.status_code == 200
    job_id = _job_id(r.text)
    for view in ("front", "side", "top", "iso"):
        img = client.get(f"/jobs/{job_id}/view_{view}.png")
        assert img.status_code == 200 and img.content[:8] == b"\x89PNG\r\n\x1a\n"
    assert "40.00 × 30.00 × 40.00 mm" in r.text and "✅" in r.text
    r = client.post("/print/nosac_l/approve", data={"job_id": job_id})
    assert r.status_code == 200
    prod = next((client.data_dir / "production").iterdir())
    names = sorted(p.name for p in prod.iterdir())
    assert [n for n in names if n != "job.json"] == sorted(
        [n for n in names if n.endswith((".stl", ".3mf", ".json")) and n != "job.json"])
    job = json.loads((prod / "job.json").read_text(encoding="utf-8"))
    assert job["estimates"]["masa_g"] > 0 and job["type"] == "print"


def test_tampered_file_blocks_approve(client):
    r = client.post("/laser/plocica/preview", data=_form(C.LASER_TEMPLATES["plocica"].example))
    job_id = _job_id(r.text)
    svg = next((client.data_dir / "jobs" / job_id).glob("*.svg"))
    svg.write_text(svg.read_text(encoding="utf-8") + "<!-- izmjena -->", encoding="utf-8")
    r = client.post("/laser/plocica/approve", data={"job_id": job_id})
    assert r.status_code == 422 and "ValidationFailed" in r.text
    assert list((client.data_dir / "production").iterdir()) == []


def test_approve_wrong_template_rejected(client):
    r = client.post("/laser/plocica/preview", data=_form(C.LASER_TEMPLATES["plocica"].example))
    r = client.post("/laser/privesak/approve", data={"job_id": _job_id(r.text)})
    assert r.status_code == 404


# ------------------------------------------------------------- istorija
def test_history_and_reopen(client):
    r = client.post("/laser/oznaka_qr/preview", data=_form(C.LASER_TEMPLATES["oznaka_qr"].example))
    job_id = _job_id(r.text)
    html = client.get("/history").text
    assert job_id in html and "pregled" in html
    detail = client.get(f"/history/{job_id}")
    assert detail.status_code == 200 and f"/laser/oznaka_qr?job={job_id}" in detail.text
    reopened = client.get(f"/laser/oznaka_qr?job={job_id}").text
    assert 'value="https://example.com/p/123"' in reopened      # isti parametri nazad u formi
    client.post("/laser/oznaka_qr/approve", data={"job_id": job_id})
    assert "odobreno" in client.get("/history").text


def test_job_file_access_is_restricted(client):
    r = client.post("/laser/plocica/preview", data=_form(C.LASER_TEMPLATES["plocica"].example))
    job_id = _job_id(r.text)
    assert client.get(f"/jobs/{job_id}/job.json").status_code == 404
    assert client.get("/jobs/../../etc/passwd").status_code == 404
    assert client.get("/history/nije-validan-id").status_code == 404


def test_cli_untouched_still_works(tmp_path):
    from fabagent.cli import main
    assert main(["laser", "build", "privesak", "--params", "oblik=krug", "--out",
                 str(tmp_path / "x.svg")]) == MissingParam.exit_code

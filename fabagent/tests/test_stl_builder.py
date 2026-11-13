import json
import math
import struct
import zipfile

import numpy as np
import pytest
import trimesh

from fabagent.core import config as C
from fabagent.core import geometry2d as g2
from fabagent.core.errors import InvalidGeometry, OutputError
from fabagent.printer import mesh_io
from fabagent.printer import mesher as M
from fabagent.printer.mesh_validator import ray_wall_thickness, validate_mesh
from fabagent.printer.stl_builder import (build, estimate_print, validate_3mf_file,
                                          validate_stl_file, write_outputs)
from fabagent.printer.triangulate import triangulate


# ------------------------------------------------------------- triangulacija
def test_triangulate_area_matches_polygon_with_holes():
    outer = g2.rounded_rect(0, 0, 80, 40, 5)
    holes = [g2.circle(10, 20, 3), g2.circle(70, 20, 3), g2.circle(40, 10, 4), g2.circle(40, 30, 4)]
    pts, tris = triangulate(outer, holes)
    a = pts[tris]
    cross = (a[:, 1, 0] - a[:, 0, 0]) * (a[:, 2, 1] - a[:, 0, 1]) - \
            (a[:, 1, 1] - a[:, 0, 1]) * (a[:, 2, 0] - a[:, 0, 0])
    assert (cross > 0).all()
    expected = g2.signed_area(outer) - sum(abs(g2.signed_area(h)) for h in holes)
    assert 0.5 * cross.sum() == pytest.approx(expected, rel=1e-9)


# ------------------------------------------------------------- primitivi
@pytest.mark.parametrize("name,pieces,volume", [
    ("kocka", [M.box(0, 0, 0, 10, 20, 5)], 1000.0),
    ("dvije_kocke", [M.box(0, 0, 0, 10, 10, 5), M.box(10, 0, 0, 20, 10, 5)], 1000.0),
    ("cilindar", [M.cylinder(0, 0, 5, 0, 10)], math.pi * 25 * 10),
    ("cijev", [M.tube(0, 0, 4, 6, 0, 10)], math.pi * 20 * 10),
    ("torus", [M.torus(10, 3)], 2 * math.pi ** 2 * 10 * 9),
])
def test_primitives_are_watertight(name, pieces, volume):
    body = M.assemble(name, pieces)
    tm = trimesh.Trimesh(body.vertices, body.faces, process=False)
    assert tm.is_watertight and tm.is_winding_consistent and tm.is_volume
    assert tm.volume == pytest.approx(volume, rel=0.02)   # torus: dvostruka aproksimacija tetivama


def test_fillet_profile_area():
    t, r = 4.0, 3.0
    prof = M.fillet_corner_profile(t, r)
    expected = (t + r) * t + t * r + r * r - math.pi * r * r / 4
    # konkavni luk aproksimiran tetivama: greska <= duzina_luka * tolerancija tetive
    assert g2.signed_area(prof) == pytest.approx(expected, abs=(math.pi * r / 2) * C.MESH_CHORD_TOLERANCE_MM)


# ------------------------------------------------------------- validator mesha
def test_open_mesh_is_rejected():
    body = M.assemble("k", [M.box(0, 0, 0, 10, 10, 10)])
    rep = validate_mesh(body.vertices, body.faces[:-1], "otvoren")
    assert not rep["ok"] and any("nije zatvoren" in e for e in rep["errors"])


def test_non_manifold_is_rejected():
    a = M.assemble("a", [M.box(0, 0, 0, 10, 10, 10)])
    b = a.translated(dx=10, dy=10)   # dijele samo jednu ivicu
    v, f = M.merge_bodies([a, b])
    flat = np.round(v[f].reshape(-1, 3), 6)
    verts, inv = np.unique(flat, axis=0, return_inverse=True)
    rep = validate_mesh(verts, inv.reshape(-1, 3), "nm")
    assert not rep["ok"] and any("non-manifold" in e for e in rep["errors"])


def test_inverted_normals_rejected():
    body = M.assemble("k", [M.box(0, 0, 0, 10, 10, 10)])
    rep = validate_mesh(body.vertices, body.faces[:, ::-1], "inv")
    assert not rep["ok"] and any("volumen" in e for e in rep["errors"])


def test_thin_wall_rejected():
    body = M.assemble("tanko", [M.box(0, 0, 0, 30, 30, 0.8)])
    rep = validate_mesh(body.vertices, body.faces, "tanko")
    assert not rep["ok"] and any("debljina zida" in e for e in rep["errors"])
    assert ray_wall_thickness(body.vertices, body.faces)["min_mm"] == pytest.approx(0.8, abs=1e-6)


def test_build_volume_rejected():
    bx = C.PRINTER_BUILD_VOLUME_MM[0]
    body = M.assemble("velik", [M.box(0, 0, 0, bx + 1, 10, 10)])
    rep = validate_mesh(body.vertices, body.faces, "velik")
    assert any("radni volumen" in e for e in rep["errors"])


def test_overlapping_pieces_are_not_silently_fixed():
    with pytest.raises(InvalidGeometry):
        b = M.assemble("x", [M.box(0, 0, 0, 10, 10, 10), M.box(0, 0, 0, 10, 10, 10)])
        rep = validate_mesh(b.vertices, b.faces, "x")
        if not rep["ok"]:
            raise InvalidGeometry(rep["errors"])


# ------------------------------------------------------------- sabloni + fajlovi
@pytest.mark.parametrize("name", sorted(C.PRINT_TEMPLATES))
def test_template_mesh_watertight_and_min_wall(name):
    res = build(name, C.PRINT_TEMPLATES[name].example)
    for b in res.bodies:
        tm = trimesh.Trimesh(b.vertices, b.faces, process=False)
        assert tm.is_watertight, b.name
        assert tm.is_winding_consistent, b.name
        assert tm.volume > 0
    assert res.metadata["mesh"]["min_wall_mm"] >= C.FDM_MIN_WALL_MM - C.WALL_THICKNESS_TOLERANCE_MM
    assert res.metadata["dimensions_mm"]["min"][2] == pytest.approx(0.0)


def test_bracket_volume_analytic():
    p = C.PRINT_TEMPLATES["nosac_l"].example
    res = build("nosac_l", p)
    A, B, W, t, r = p["duzina_a"], p["duzina_b"], p["sirina"], p["debljina"], p["filet_radijus"]
    R = p["rupa_promjer"] / 2
    x0 = t + r
    plates = ((A - x0) + (B - x0)) * W * t - 4 * math.pi * R * R * t
    corner = (x0 * t + t * r + r * r - math.pi * r * r / 4) * W
    assert res.metadata["volume_mm3"] == pytest.approx(plates + corner, rel=2e-3)


def test_metadata_mass_and_time():
    res = build("kutija", C.PRINT_TEMPLATES["kutija"].example)
    est = res.metadata["estimate"]
    assert est["mass_solid_g"] == pytest.approx(res.metadata["volume_mm3"] / 1000 * 1.24, rel=1e-3)
    assert 0 < est["mass_estimated_g"] <= est["mass_solid_g"]
    assert est["time_estimate_s"] > 0
    assert set(res.metadata["estimate_by_material"]) == {"PLA", "PETG"}
    assert estimate_print(1000, 600, 10, "PETG", 100, 0.2)["mass_estimated_g"] == pytest.approx(1.27)


def test_write_and_reread_stl_and_3mf(tmp_path):
    res = build("kutija", C.PRINT_TEMPLATES["kutija"].example)
    paths = write_outputs(res, str(tmp_path / "kutija.stl"))
    data = open(paths["stl"], "rb").read()
    (n,) = struct.unpack("<I", data[80:84])
    assert len(data) == 84 + 50 * n and not data.startswith(b"solid")
    assert validate_stl_file(paths["stl"])["ok"]
    tm = trimesh.load(paths["stl"])
    assert tm.is_watertight
    rep = validate_3mf_file(paths["3mf"])
    assert rep["ok"] and len(rep["stats"]) == 2      # kutija + poklopac
    with zipfile.ZipFile(paths["3mf"]) as z:
        assert "3D/3dmodel.model" in z.namelist()
    meta = json.loads(open(paths["json"], encoding="utf-8").read())
    assert meta["files"]["stl"]["sha256"]


def test_ascii_stl_and_broken_stl(tmp_path):
    body = M.assemble("k", [M.box(0, 0, 0, 10, 10, 10)])
    lines = ["solid t"]
    for f in body.faces:
        lines.append("facet normal 0 0 0\nouter loop")
        lines += [f"vertex {x} {y} {z}" for x, y, z in body.vertices[f]]
        lines.append("endloop\nendfacet")
    lines.append("endsolid t")
    p = tmp_path / "a.stl"
    p.write_text("\n".join(lines))
    assert validate_stl_file(str(p))["ok"]
    bad = tmp_path / "b.stl"
    bad.write_bytes(b"\x00" * 10)
    assert not validate_stl_file(str(bad))["ok"]


def test_output_extension_checked(tmp_path):
    res = build("poklopac", C.PRINT_TEMPLATES["poklopac"].example)
    with pytest.raises(OutputError):
        write_outputs(res, str(tmp_path / "x.obj"))


def test_adapter_overhang_warning():
    ex = dict(C.PRINT_TEMPLATES["adapter"].example, d1_vanjski=32, d1_unutarnji=27,
              d2_vanjski=50, d2_unutarnji=44, duzina_prelaza=8)
    res = build("adapter", ex)
    assert any("prepust" in w for w in res.metadata["warnings"])

"""Bulove operacije i offset: tacne povrsine na jednostavnim oblicima + poredjenje sa rasterom (cv2)."""

import math

import numpy as np
import pytest

from fabagent.core import polybool as pb
from fabagent.core.errors import InvalidGeometry, InvalidParam

cv2 = pytest.importorskip("cv2")

PX = 0.02      # mm po pikselu u rasterskom poredjenju


def sq(x, y, s):
    return [[x, y], [x + s, y], [x + s, y + s], [x, y + s]]


def circle(cx, cy, r, n=90):
    t = np.linspace(0, 2 * math.pi, n, endpoint=False)
    return np.column_stack([cx + r * np.cos(t), cy + r * np.sin(t)]).tolist()


def star(cx, cy, r1, r2, n=7, rot=0.0):
    t = rot + np.arange(2 * n) * math.pi / n
    r = np.where(np.arange(2 * n) % 2 == 0, r1, r2)
    return np.column_stack([cx + r * np.cos(t), cy + r * np.sin(t)]).tolist()


def mask(rings, size_mm=60.0):
    """Even-odd raster oblasti; ishodiste pomjereno za 10 mm da stane i prosirenje."""
    n = int(size_mm / PX)
    img = np.zeros((n, n), np.uint8)
    for r in rings:
        layer = np.zeros_like(img)
        cv2.fillPoly(layer, [np.round((np.asarray(r) + 10.0) / PX).astype(np.int32)], 1)
        img ^= layer
    return img.astype(bool)


def raster_area(m):
    return float(m.sum()) * PX * PX


# ------------------------------------------------------------------ Bulove operacije
def test_overlapping_squares_exact_areas():
    a, b = [sq(0, 0, 10)], [sq(5, 5, 10)]
    assert pb.area(pb.boolean(a, b, "union")) == pytest.approx(175.0)
    assert pb.area(pb.boolean(a, b, "intersection")) == pytest.approx(25.0)
    assert pb.area(pb.boolean(a, b, "difference")) == pytest.approx(75.0)
    assert pb.area(pb.boolean(a, b, "xor")) == pytest.approx(150.0)
    assert len(pb.boolean(a, b, "union")) == 1 and len(pb.boolean(a, b, "union")[0]) == 8


def test_shared_edge_is_removed():
    out = pb.boolean([sq(0, 0, 10)], [sq(10, 0, 10)], "union")
    assert len(out) == 1 and len(out[0]) == 4 and pb.area(out) == pytest.approx(200.0)
    assert pb.boolean([sq(0, 0, 10)], [sq(10, 0, 10)], "intersection") == []


def test_partly_shared_edge_and_t_junction():
    out = pb.boolean([sq(0, 0, 10)], [[[10, 2], [16, 2], [16, 6], [10, 6]]], "union")
    assert len(out) == 1 and pb.area(out) == pytest.approx(124.0) and len(out[0]) == 8


def test_identical_shapes():
    a = [sq(0, 0, 10)]
    assert pb.area(pb.boolean(a, a, "union")) == pytest.approx(100.0)
    assert pb.area(pb.boolean(a, a, "intersection")) == pytest.approx(100.0)
    assert pb.boolean(a, a, "difference") == []


def test_hole_is_kept_and_cut():
    frame = [sq(0, 0, 20), sq(5, 5, 10)]                  # okvir sa rupom, povrsina 300
    out = pb.boolean(frame, [[[-5, 8], [25, 8], [25, 12], [-5, 12]]], "difference")  # traka visine 4 presijeca okvir
    assert pb.area(out) == pytest.approx(300.0 - (20 * 4 - 10 * 4))
    assert len(out) == 2
    inner = pb.boolean([sq(0, 0, 20)], [sq(5, 5, 10)], "difference")
    assert len(inner) == 2 and pb.area(inner) == pytest.approx(300.0)


def test_touching_corner_and_disjoint():
    assert pb.area(pb.boolean([sq(0, 0, 10)], [sq(10, 10, 10)], "union")) == pytest.approx(200.0)
    out = pb.boolean([sq(0, 0, 10)], [sq(30, 0, 10)], "union")
    assert len(out) == 2 and pb.area(out) == pytest.approx(200.0)
    assert pb.boolean([sq(0, 0, 10)], [sq(30, 0, 10)], "intersection") == []


def test_union_of_many():
    out = pb.union([[sq(0, 0, 10)], [sq(8, 0, 10)], [sq(16, 0, 10)], [circle(13, 12, 6)]])
    ref = mask([sq(0, 0, 10)]) | mask([sq(8, 0, 10)]) | mask([sq(16, 0, 10)]) | mask([circle(13, 12, 6)])
    assert len(out) == 1 and pb.area(out) == pytest.approx(raster_area(ref), rel=0.01)


@pytest.mark.parametrize("seed", range(6))
def test_random_shapes_match_raster(seed):
    rng = np.random.default_rng(seed)
    a = [star(15 + rng.uniform(-2, 2), 15 + rng.uniform(-2, 2), rng.uniform(9, 13), rng.uniform(3, 6),
              n=int(rng.integers(4, 9)), rot=rng.uniform(0, 1))]
    b = [circle(18 + rng.uniform(-4, 4), 17 + rng.uniform(-4, 4), rng.uniform(5, 9), n=int(rng.integers(20, 80)))]
    ma, mb = mask(a), mask(b)
    for op, ref in (("union", ma | mb), ("intersection", ma & mb), ("difference", ma & ~mb), ("xor", ma ^ mb)):
        out = pb.boolean(a, b, op)
        assert pb.area(out) == pytest.approx(raster_area(ref), rel=0.02, abs=0.3), op
        assert raster_area(mask(out) ^ ref) < 0.03 * max(raster_area(ref), 1.0) + 0.5, op   # isti oblik, ne samo povrsina
    union_a, inter_a = pb.area(pb.boolean(a, b, "union")), pb.area(pb.boolean(a, b, "intersection"))
    assert union_a + inter_a == pytest.approx(pb.area(a) + pb.area(b), rel=1e-9)


def test_boolean_errors():
    with pytest.raises(InvalidParam):
        pb.boolean([sq(0, 0, 10)], [sq(1, 1, 2)], "spoji")
    with pytest.raises(InvalidGeometry):
        pb.boolean([[[0, 0], [1, 1]]], [sq(1, 1, 2)], "union")
    with pytest.raises(InvalidParam):
        pb.boolean("x", [sq(1, 1, 2)], "union")
    with pytest.raises(InvalidParam):
        pb.union([[sq(0, 0, 1)]])


# ------------------------------------------------------------------ offset
def test_offset_square():
    out = pb.offset([sq(0, 0, 10)], 1.0)
    assert len(out) == 1 and pb.area(out) == pytest.approx(100 + 40 + math.pi, abs=0.05)
    inn = pb.offset([sq(0, 0, 10)], -1.0)
    assert len(inn) == 1 and len(inn[0]) == 4 and pb.area(inn) == pytest.approx(64.0, abs=1e-6)
    assert pb.offset([sq(0, 0, 10)], -6.0) == []


def test_offset_circle_and_orientation_independent():
    c = circle(20, 20, 10, n=120)
    assert pb.area(pb.offset([c], 2.0)) == pytest.approx(math.pi * 144, rel=0.005)
    assert pb.area(pb.offset([c[::-1]], 2.0)) == pytest.approx(math.pi * 144, rel=0.005)
    assert pb.area(pb.offset([c], -2.0)) == pytest.approx(math.pi * 64, rel=0.005)


def test_offset_with_hole():
    frame = [sq(0, 0, 20), sq(8, 8, 4)]
    grown = pb.offset(frame, 1.0)                           # rupa 4x4 se suzi na 2x2
    assert len(grown) == 2 and pb.area(grown) == pytest.approx(400 + 80 + math.pi - 4, abs=0.05)
    closed = pb.offset(frame, 3.0)                          # rupa se zatvori
    assert len(closed) == 1
    shrunk = pb.offset(frame, -1.0)                         # rupa naraste (zaobljeni uglovi)
    assert len(shrunk) == 2 and pb.area(shrunk) == pytest.approx(18 * 18 - (16 + 16 + math.pi), abs=0.05)


@pytest.mark.parametrize("dist", [1.5, -0.8])
def test_offset_concave_matches_raster(dist):
    shape = [[[0, 0], [30, 0], [30, 10], [12, 10], [12, 30], [0, 30]], sq(3, 3, 5)]   # L-oblik sa rupom
    out = pb.offset(shape, dist)
    m = mask(shape)
    dt_out = cv2.distanceTransform((~m).astype(np.uint8), cv2.DIST_L2, 5) * PX
    dt_in = cv2.distanceTransform(m.astype(np.uint8), cv2.DIST_L2, 5) * PX
    ref = (m | (dt_out <= dist)) if dist > 0 else (dt_in > -dist)
    assert pb.area(out) == pytest.approx(raster_area(ref), rel=0.01)
    assert raster_area(mask(out) ^ ref) < 0.02 * raster_area(ref)


def test_offset_star_matches_raster():
    shape = [star(20, 20, 14, 5, n=6)]
    out = pb.offset(shape, 1.0)
    m = mask(shape)
    ref = m | (cv2.distanceTransform((~m).astype(np.uint8), cv2.DIST_L2, 5) * PX <= 1.0)
    assert len(out) == 1 and raster_area(mask(out) ^ ref) < 0.02 * raster_area(ref)


def test_offset_errors():
    with pytest.raises(InvalidParam):
        pb.offset([sq(0, 0, 10)], 0)
    with pytest.raises(InvalidParam):
        pb.offset([sq(0, 0, 10)], "x")

"""Napredna obrada slike: generisane test slike (krug sa sumom, invert, cutoff, adaptivni prag)."""

import math

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

from fabagent.core import config as C  # noqa: E402
from fabagent.core import geometry2d as g2  # noqa: E402
from fabagent.core import image_trace as T  # noqa: E402
from fabagent.core.converter import convert  # noqa: E402
from fabagent.core.errors import InvalidParam, MissingParam, OutOfRange, ValidationFailed  # noqa: E402
from fabagent.core.svg_fixer import fix_svg  # noqa: E402

R_PX = 60
SIZE = 200


def png(img: np.ndarray) -> bytes:
    return cv2.imencode(".png", img)[1].tobytes()


def noisy_circle(seed: int = 7, noise: float = 0.04, blobs: bool = True) -> np.ndarray:
    img = np.full((SIZE, SIZE), 255, np.uint8)
    cv2.circle(img, (SIZE // 2, SIZE // 2), R_PX, 0, -1)
    rng = np.random.default_rng(seed)              # deterministicki sum
    salt = rng.random((SIZE, SIZE)) < noise
    pepper = rng.random((SIZE, SIZE)) < noise
    img[salt] = 255
    img[pepper] = 0
    # par vecih mrlja (3x3) koje turdsize ne uklanja
    for x, y in ((20, 20), (175, 30), (30, 170), (170, 175)) if blobs else ():
        img[y:y + 3, x:x + 3] = 0
    return img


def trace_rings(img, width_mm=100.0, **trace):
    conv = convert("t.png", png(img), width_mm=width_mm, trace={k: str(v) for k, v in trace.items()})
    res = fix_svg(conv.svg)
    return conv, res


def _outer_rings(rings):
    return [r for r in rings if g2.signed_area(r) != 0]


# ------------------------------------------------------------------ denoise
def test_noise_without_denoise_gives_many_contours():
    _, res = trace_rings(noisy_circle(), prag=128)
    assert len(res.layers["ENGRAVE"]) > 5


def test_denoise_leaves_single_clean_circle():
    conv, res = trace_rings(noisy_circle(), prag=128, uklanjanje_suma=2)
    assert res.production_ready, res.report.blocking
    rings = res.layers["ENGRAVE"]
    assert len(rings) == 1
    mm_per_px = 100.0 / SIZE
    expected = math.pi * (R_PX * mm_per_px) ** 2
    assert abs(g2.signed_area(rings[0])) == pytest.approx(expected, rel=0.03)
    assert conv.bitmap_png.startswith(b"\x89PNG")
    assert "uklanjanje suma 2" in conv.notes[0]


def test_deterministic():
    a, _ = trace_rings(noisy_circle(), prag=128, uklanjanje_suma=2)
    b, _ = trace_rings(noisy_circle(), prag=128, uklanjanje_suma=2)
    assert a.svg == b.svg


# ------------------------------------------------------------------ invert / cutoff / adaptivni
def test_invert_for_light_on_dark():
    img = np.zeros((SIZE, SIZE), np.uint8)
    cv2.circle(img, (100, 100), R_PX, 255, -1)
    _, plain = trace_rings(img, prag=128)
    assert len(plain.layers["ENGRAVE"]) == 2            # bez inverta: tamna podloga = okvir sa rupom
    _, res = trace_rings(img, prag=128, invertuj="true")
    assert len(res.layers["ENGRAVE"]) == 1


def test_cutoff_ignores_darkest_pixels():
    img = np.full((SIZE, SIZE), 255, np.uint8)
    img[20:80, 20:80] = 10        # vrlo tamno -> ispod cutoff
    img[120:180, 120:180] = 120   # srednje -> crtez
    _, res = trace_rings(img, prag=200, cutoff=60)
    rings = res.layers["ENGRAVE"]
    assert len(rings) == 1
    x0, y0, x1, y1 = g2.bbox(rings)
    assert x0 > 50 and y0 > 50                          # samo donji desni kvadrat (mm)


def test_cutoff_must_be_below_threshold():
    with pytest.raises(OutOfRange):
        trace_rings(noisy_circle(), prag=100, cutoff=150)


def test_adaptive_threshold_handles_gradient():
    x = np.linspace(40, 255, SIZE)
    img = np.tile(x, (SIZE, 1)).astype(np.uint8)       # tamni gradijent lijevo
    cv2.circle(img, (140, 100), 30, 0, -1)
    _, fixed = trace_rings(img, prag=128)
    x0 = g2.bbox(fixed.layers["ENGRAVE"])[0]
    assert x0 < 5                                       # fiksni prag hvata i tamni dio gradijenta
    _, adaptive = trace_rings(img, nacin_praga="adaptivni", uklanjanje_suma=1)
    bx0, by0, bx1, by1 = g2.bbox(adaptive.layers["ENGRAVE"])
    mm = 100.0 / SIZE
    assert bx0 > (140 - 30 - 5) * mm and bx1 < (140 + 30 + 5) * mm   # samo krug


# ------------------------------------------------------------------ simplify / motori
def test_simplify_reduces_points_contours_engine():
    img = noisy_circle(noise=0.0, blobs=False)
    _, raw = trace_rings(img, prag=128, motor="konture")
    _, simp = trace_rings(img, prag=128, motor="konture", pojednostavljenje=2.0)
    n_raw = sum(len(r) for r in raw.layers["ENGRAVE"])
    n_simp = sum(len(r) for r in simp.layers["ENGRAVE"])
    assert n_simp < n_raw / 2
    # RDP tolerancija 2 px = 1 mm: povrsina ostaje bliska
    a_raw = abs(g2.signed_area(raw.layers["ENGRAVE"][0]))
    a_simp = abs(g2.signed_area(simp.layers["ENGRAVE"][0]))
    assert a_simp == pytest.approx(a_raw, rel=0.03)


def test_simplify_vlastitim_motorom_daje_poligone():
    conv, res = trace_rings(noisy_circle(noise=0.0, blobs=False), prag=128, pojednostavljenje=1.0)
    assert " C " not in conv.svg.decode()                # krivulje su pretvorene u poligon
    assert len(res.layers["ENGRAVE"]) == 1


@pytest.mark.skipif(not T.engine_available("vtracer"), reason="vtracer nije instaliran")
def test_vtracer_engine():
    conv, res = trace_rings(noisy_circle(noise=0.0, blobs=False), prag=128, motor="vtracer")
    assert res.production_ready, res.report.blocking
    rings = res.layers["ENGRAVE"]
    mm = 100.0 / SIZE
    net = abs(sum(g2.signed_area(r) for r in rings))
    assert net == pytest.approx(math.pi * (R_PX * mm) ** 2, rel=0.05)


# ------------------------------------------------------------------ validacija opcija
@pytest.mark.parametrize("raw,exc", [
    ({"prag": "abc"}, InvalidParam), ({"prag": "12,5"}, InvalidParam),
    ({"prag": "128", "motor": "nepoznat"}, InvalidParam),
    ({"prag": "128", "nacin_praga": "x"}, InvalidParam),
    ({"prag": "128", "invertuj": "mozda"}, InvalidParam),
    ({"prag": "128", "uklanjanje_suma": "11"}, OutOfRange),
    ({"prag": "128", "pojednostavljenje": "6"}, OutOfRange),
    ({"prag": "300"}, OutOfRange),
    ({"cutoff": "20"}, MissingParam),
])
def test_option_validation(raw, exc):
    with pytest.raises(exc):
        T.parse_options(raw)


def test_missing_threshold_and_width_listed_together():
    with pytest.raises(MissingParam) as e:
        convert("s.png", png(noisy_circle()), trace={"cutoff": "10"})
    assert set(e.value.missing) == {"prag", "sirina_mm"}


def test_alpha_transparent_background():
    rgba = np.zeros((SIZE, SIZE, 4), np.uint8)          # potpuno providno (crno, alfa 0)
    cv2.circle(rgba, (100, 100), R_PX, (0, 0, 0, 255), -1)
    _, res = trace_rings(rgba, prag=128)
    assert len(res.layers["ENGRAVE"]) == 1               # providno nije crtez

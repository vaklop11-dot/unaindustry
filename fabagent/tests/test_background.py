"""Uklanjanje pozadine: generisane test slike, bez mreze i bez gotovog modela."""

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

from fabagent.core import background as B  # noqa: E402
from fabagent.core import config as C  # noqa: E402
from fabagent.core.errors import (  # noqa: E402
    InvalidGeometry,
    InvalidParam,
    MissingParam,
    OutOfRange,
)

SIZE = 200


def scene(seed: int = 3) -> np.ndarray:
    """Svijetao motiv u sredini na tamnoj sumnjivoj pozadini (BGR)."""
    rng = np.random.default_rng(seed)
    img = rng.integers(20, 60, size=(SIZE, SIZE, 3), dtype=np.uint8)
    img[60:150, 70:140] = rng.integers(190, 235, size=(90, 70, 3), dtype=np.uint8)
    return img


def full_rect(**kwargs) -> B.BackgroundOptions:
    base = dict(left=0.25, top=0.2, right=0.8, bottom=0.85)
    base.update(kwargs)
    return B.BackgroundOptions(**base)


# --- parametri se ne izmisljaju -------------------------------------------

def test_okvir_je_obavezan():
    with pytest.raises(MissingParam) as err:
        B.validate_options(B.BackgroundOptions())
    assert set(err.value.missing) == {
        "okvir_lijevo", "okvir_gore", "okvir_desno", "okvir_dolje"
    }


def test_djelimican_okvir_prijavljuje_samo_ono_sto_fali():
    with pytest.raises(MissingParam) as err:
        B.validate_options(B.BackgroundOptions(left=0.1, top=0.1))
    assert err.value.missing == ["okvir_desno", "okvir_dolje"]


def test_okvir_van_opsega():
    with pytest.raises(OutOfRange):
        B.validate_options(full_rect(right=1.4))


def test_obrnut_okvir():
    with pytest.raises(InvalidGeometry):
        B.validate_options(full_rect(left=0.8, right=0.2))


def test_premali_okvir():
    with pytest.raises(InvalidGeometry):
        B.validate_options(full_rect(left=0.50, right=0.51, top=0.50, bottom=0.51))


def test_nepoznata_pozadina():
    with pytest.raises(InvalidParam):
        B.parse_options({"pozadina": "plava"})


def test_parse_ne_popunjava_okvir():
    o = B.parse_options({})
    assert (o.left, o.top, o.right, o.bottom) == (None, None, None, None)
    assert o.iterations == C.BG_GRABCUT_ITERATIONS
    assert o.feather_px == 0


def test_parse_odbija_zarez_kao_decimalu():
    with pytest.raises(InvalidParam):
        B.parse_options({"okvir_lijevo": "0,25"})


# --- geometrija okvira -----------------------------------------------------

def test_okvir_u_pikselima():
    o = full_rect()
    x, y, w, h = B.rect_pixels(o.left, o.top, o.right, o.bottom, (SIZE, SIZE))
    assert (x, y) == (50, 40)
    assert (w, h) == (110, 130)


def test_okvir_ostaje_u_slici():
    x, y, w, h = B.rect_pixels(0.0, 0.0, 1.0, 1.0, (SIZE, SIZE))
    assert x >= 0 and y >= 0
    assert x + w <= SIZE and y + h <= SIZE


# --- rezultat --------------------------------------------------------------

def test_motiv_ostaje_pozadina_pobijeli():
    out, info = B.remove_background(scene(), full_rect())
    assert out.shape == (SIZE, SIZE, 3)
    # ugao slike je pozadina -> bijelo
    assert out[5, 5].min() > 240
    # sredina motiva nije pobijeljena u cistu bijelu
    assert out[100, 100].min() < 240
    assert 1.0 < info["udio_motiva_posto"] < 98.0


def test_crna_pozadina():
    out, _ = B.remove_background(scene(), full_rect(background="crna"))
    assert out[5, 5].max() < 15


def test_providna_pozadina_daje_alfu():
    out, _ = B.remove_background(scene(), full_rect(background="providna"))
    assert out.shape == (SIZE, SIZE, 4)
    assert out[5, 5, 3] == 0


def test_isti_ulaz_daje_isti_izlaz():
    """GrabCut koristi RNG; bez fiksnog sjemena rezultat ne bi bio ponovljiv."""
    image = scene()
    first, _ = B.remove_background(image, full_rect())
    second, _ = B.remove_background(image, full_rect())
    assert np.array_equal(first, second)


def test_okvir_koji_promasi_motiv_prijavljuje_gresku():
    image = np.full((SIZE, SIZE, 3), 30, np.uint8)
    with pytest.raises(InvalidGeometry) as err:
        B.remove_background(image, full_rect())
    assert "motiv" in str(err.value)


def test_siva_slika_se_odbija():
    gray = np.zeros((SIZE, SIZE), np.uint8)
    with pytest.raises(Exception):
        B.subject_mask(gray, full_rect())


def test_bajtovi_ulaze_i_izlaze():
    ok, png = cv2.imencode(".png", scene())
    assert ok
    out, info = B.remove_background_bytes(png.tobytes(), full_rect())
    assert out[:8] == b"\x89PNG\r\n\x1a\n"
    assert info["udio_motiva_posto"] > 1.0


def test_neispravni_bajtovi_se_odbijaju():
    from fabagent.core.errors import ValidationFailed
    with pytest.raises(ValidationFailed):
        B.remove_background_bytes(b"ovo nije slika", full_rect())


def test_requested_je_false_bez_ijednog_okvira():
    assert B.parse_options({}).requested() is False
    assert B.parse_options({"okvir_lijevo": "0.2"}).requested() is True


def test_izvjestaj_sadrzi_odluke():
    _, info = B.remove_background(scene(), full_rect(iterations=3, feather_px=2))
    assert info["iteracije"] == 3
    assert info["omeksano_px"] == 2
    assert info["sjeme"] == C.BG_RNG_SEED
    assert info["pozadina"] == "bijela"

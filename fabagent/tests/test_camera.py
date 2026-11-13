"""Kamera i kalibracija - testirano sa statickom (sinteticki generisanom) slikom radne povrsine."""

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

from fabagent.core import camera as cam  # noqa: E402
from fabagent.core import config as C  # noqa: E402
from fabagent.core.errors import CameraUnavailable, InvalidGeometry, InvalidParam  # noqa: E402

FRAME = (640, 480)
# uglovi radne povrsine na slici kamere (perspektiva: gornja ivica uza), redom TL, TR, BR, BL
CORNERS = np.array([[150, 60], [500, 80], [600, 430], [60, 400]], dtype=np.float64)
MARK = {(0, 0): (0, 0, 255), (1, 0): (0, 255, 0), (1, 1): (255, 0, 0), (0, 1): (0, 255, 255)}


def synthetic_frame() -> np.ndarray:
    img = np.full((FRAME[1], FRAME[0], 3), 40, np.uint8)
    cv2.fillConvexPoly(img, CORNERS.astype(np.int32), (200, 200, 200))
    W, H = C.LASER_WORK_AREA_MM
    dst = np.array([[0, 0], [W, 0], [W, H], [0, H]], np.float32)
    Minv = cv2.getPerspectiveTransform(dst, CORNERS.astype(np.float32))
    # kvadratni markeri 40x40 mm u svakom uglu radne povrsine (nacrtani u perspektivi)
    for (u, v), color in MARK.items():
        x0, y0 = u * (W - 40), v * (H - 40)
        sq = np.array([[x0, y0], [x0 + 40, y0], [x0 + 40, y0 + 40], [x0, y0 + 40]], np.float32)
        poly = cv2.perspectiveTransform(sq.reshape(-1, 1, 2), Minv).reshape(-1, 2)
        cv2.fillConvexPoly(img, np.round(poly).astype(np.int32), color)
    return img


@pytest.fixture()
def frame_file(tmp_path):
    p = tmp_path / "kamera.png"
    cv2.imwrite(str(p), synthetic_frame())
    return p


def test_static_source_reads_image(frame_file):
    src = cam.CameraSource(str(frame_file))
    frame = src.read()
    assert frame.shape == (FRAME[1], FRAME[0], 3)
    assert "staticka" in src.describe()
    assert cam.encode_jpeg(frame)[:2] == b"\xff\xd8"


def test_missing_source_is_clear_error(tmp_path):
    with pytest.raises(CameraUnavailable):
        cam.CameraSource(str(tmp_path / "nema.png")).read()


def test_calibration_maps_corners_exactly():
    calib = cam.compute_calibration(CORNERS, FRAME)
    W, H = C.LASER_WORK_AREA_MM
    mm = cam.px_to_mm(calib, CORNERS)
    assert np.allclose(mm, [[0, 0], [W, 0], [W, H], [0, H]], atol=1e-6)


def test_warp_puts_markers_in_laser_corners():
    calib = cam.compute_calibration(CORNERS, FRAME)
    warped = cam.warp_to_workarea(synthetic_frame(), calib)
    ppm = C.CAMERA_PX_PER_MM
    W, H = C.LASER_WORK_AREA_MM
    assert warped.shape[:2] == (round(H * ppm), round(W * ppm))
    for (u, v), color in MARK.items():
        # centar markera: 20 mm od ugla
        x = (u * (W - 40) + 20) * ppm
        y = (v * (H - 40) + 20) * ppm
        px = warped[int(y), int(x)].astype(int)
        assert np.abs(px - np.array(color)).max() < 40, ((u, v), px)
    # centar radne povrsine je siva podloga
    assert np.abs(warped[int(H * ppm / 2), int(W * ppm / 2)].astype(int) - 200).max() < 20


def test_wrong_order_rejected():
    with pytest.raises(InvalidGeometry):
        cam.compute_calibration(CORNERS[::-1], FRAME)          # suprotan smjer
    with pytest.raises(InvalidGeometry):
        cam.compute_calibration(CORNERS[[0, 2, 1, 3]], FRAME)  # ukrsteno


def test_out_of_frame_and_tiny_rejected():
    bad = CORNERS.copy()
    bad[2] = [700, 430]
    with pytest.raises(InvalidParam):
        cam.compute_calibration(bad, FRAME)
    tiny = np.array([[10, 10], [30, 10], [30, 30], [10, 30]], float)
    with pytest.raises(InvalidGeometry):
        cam.compute_calibration(tiny, FRAME)
    with pytest.raises(InvalidParam):
        cam.compute_calibration(CORNERS[:3], FRAME)


def test_resolution_change_requires_recalibration():
    calib = cam.compute_calibration(CORNERS, FRAME)
    small = cv2.resize(synthetic_frame(), (320, 240))
    with pytest.raises(CameraUnavailable):
        cam.warp_to_workarea(small, calib)


def test_calibration_store_roundtrip(tmp_path):
    store = cam.CalibrationStore(tmp_path / "kamera")
    assert store.load() is None
    calib = cam.compute_calibration(CORNERS, FRAME, source="test")
    store.save(calib)
    loaded = store.load()
    assert np.allclose(loaded.M, calib.M) and loaded.source == "test"

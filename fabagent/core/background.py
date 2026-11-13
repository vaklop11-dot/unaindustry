"""Uklanjanje pozadine sa fotografije prije graviranja (OpenCV GrabCut).

Zasto GrabCut a ne gotov model za segmentaciju: FabAgent u runtime-u ne ide na
mrezu, a biblioteke tipa rembg skidaju tezine modela pri prvom pokretanju.
GrabCut radi iz OpenCV-a koji je vec zavisnost, bez ijednog bajta sa mreze.

Deterministicki: GrabCut inicijalizuje GMM pomocu k-means koji koristi generator
slucajnih brojeva, pa se prije svakog poziva postavlja fiksno sjeme
(``cv2.setRNGSeed(C.BG_RNG_SEED)``). Isti ulaz daje isti izlaz.

Maska se racuna na umanjenoj kopiji (najduza stranica ``C.BG_MASK_WORK_PX``) pa
se vraca na punu velicinu. Na punoj rezoluciji telefonske slike GrabCut traje
par minuta, sto je neupotrebljivo u web formi; umanjena kopija daje istu masku
za nekoliko sekundi jer je granica motiva grublja od jednog piksela.

Okviri su OBAVEZNI onoliko koliko su zadani - alat ne pogadja gdje je motiv:
  - vanjski okvir (obavezan): unutar njega se trazi motiv
  - "sigurni motiv" (opciono): ono sto se ne smije odbaciti, za slucaj kad su
    motiv i pozadina slicne boje (svijetla odjeca na svijetlom zidu)
  - "izbaci" (opciono): sigurna pozadina, za predmete koji su usli u okvir

Opciona obrada se primjenjuje SAMO ako je zadana - nezadana obrada znaci "bez te
obrade", ne izmisljenu vrijednost.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from fabagent.core import config as C
from fabagent.core.errors import (
    InvalidGeometry,
    InvalidParam,
    MissingParam,
    OutOfRange,
    RangeViolation,
    ValidationFailed,
)

TEMPLATE = "uklanjanje_pozadine"

_RECT_KEYS = ("okvir_lijevo", "okvir_gore", "okvir_desno", "okvir_dolje")
_SURE_KEYS = ("motiv_lijevo", "motiv_gore", "motiv_desno", "motiv_dolje")
_DROP_KEYS = ("izbaci_lijevo", "izbaci_gore", "izbaci_desno", "izbaci_dolje")

#: Polja forme koja ukljucuju uklanjanje pozadine. Ako nijedno nije zadano,
#: slika prolazi nepromijenjena.
RECT_FIELDS = _RECT_KEYS + _SURE_KEYS + _DROP_KEYS
FIELDS = RECT_FIELDS + ("iteracije", "omeksaj_ivicu", "pozadina")


def _cv2():
    try:
        import cv2
    except ImportError as exc:  # pragma: no cover - zavisi od okruzenja
        raise ValidationFailed(
            "slika",
            ["OpenCV nije instaliran (pip install opencv-python-headless)"],
        ) from exc
    return cv2


@dataclass(frozen=True)
class BackgroundOptions:
    """Okviri su u djelovima slike: 0.0 = lijeva/gornja ivica, 1.0 = desna/donja."""

    left: float | None = None
    top: float | None = None
    right: float | None = None
    bottom: float | None = None
    sure_left: float | None = None
    sure_top: float | None = None
    sure_right: float | None = None
    sure_bottom: float | None = None
    drop_left: float | None = None
    drop_top: float | None = None
    drop_right: float | None = None
    drop_bottom: float | None = None
    iterations: int = C.BG_GRABCUT_ITERATIONS
    feather_px: int = 0
    background: str = C.BG_BACKGROUNDS[0]

    def has_outer(self) -> bool:
        return all(v is not None for v in (self.left, self.top, self.right, self.bottom))

    def outer_given(self) -> bool:
        return any(v is not None for v in (self.left, self.top, self.right, self.bottom))

    def has_sure_subject(self) -> bool:
        return all(v is not None for v in
                   (self.sure_left, self.sure_top, self.sure_right, self.sure_bottom))

    def sure_subject_given(self) -> bool:
        return any(v is not None for v in
                   (self.sure_left, self.sure_top, self.sure_right, self.sure_bottom))

    def has_drop(self) -> bool:
        return all(v is not None for v in
                   (self.drop_left, self.drop_top, self.drop_right, self.drop_bottom))

    def drop_given(self) -> bool:
        return any(v is not None for v in
                   (self.drop_left, self.drop_top, self.drop_right, self.drop_bottom))

    def requested(self) -> bool:
        """Da li je korisnik uopste trazio uklanjanje pozadine."""
        return self.outer_given() or self.sure_subject_given() or self.drop_given()


def parse_options(raw: dict) -> BackgroundOptions:
    """Pretvori vrijednosti iz forme u BackgroundOptions, bez popunjavanja."""

    def number(key: str) -> float | None:
        text = str(raw.get(key, "")).strip()
        if text == "":
            return None
        try:
            return float(text)
        except ValueError:
            raise InvalidParam(f"{key}: '{text}' nije broj (koristi tacku)") from None

    def whole(key: str, fallback: int) -> int:
        text = str(raw.get(key, "")).strip()
        if text == "":
            return fallback
        try:
            return int(text)
        except ValueError:
            raise InvalidParam(f"{key}: '{text}' nije cijeli broj") from None

    background = str(raw.get("pozadina", "") or C.BG_BACKGROUNDS[0]).strip().lower()
    if background not in C.BG_BACKGROUNDS:
        raise InvalidParam(
            f"pozadina: '{background}' nije dozvoljeno, izbor: {list(C.BG_BACKGROUNDS)}"
        )

    return BackgroundOptions(
        left=number("okvir_lijevo"), top=number("okvir_gore"),
        right=number("okvir_desno"), bottom=number("okvir_dolje"),
        sure_left=number("motiv_lijevo"), sure_top=number("motiv_gore"),
        sure_right=number("motiv_desno"), sure_bottom=number("motiv_dolje"),
        drop_left=number("izbaci_lijevo"), drop_top=number("izbaci_gore"),
        drop_right=number("izbaci_desno"), drop_bottom=number("izbaci_dolje"),
        iterations=whole("iteracije", C.BG_GRABCUT_ITERATIONS),
        feather_px=whole("omeksaj_ivicu", 0),
        background=background,
    )


def _rect_pair_checks(name_l: str, name_r: str, name_t: str, name_b: str,
                      left: float, top: float, right: float, bottom: float) -> list[str]:
    reasons = []
    if right <= left:
        reasons.append(f"{name_r} mora biti veci od {name_l}")
    if bottom <= top:
        reasons.append(f"{name_b} mora biti veci od {name_t}")
    return reasons


def validate_options(o: BackgroundOptions) -> None:
    """Odbije nepotpun ili nemoguc okvir. Nista se ne popunjava umjesto korisnika."""
    if not o.has_outer():
        missing = [key for key, value in zip(
            _RECT_KEYS, (o.left, o.top, o.right, o.bottom)) if value is None]
        raise MissingParam(TEMPLATE, missing)

    violations: list[RangeViolation] = []
    for key, value in zip(RECT_FIELDS, (
            o.left, o.top, o.right, o.bottom,
            o.sure_left, o.sure_top, o.sure_right, o.sure_bottom,
            o.drop_left, o.drop_top, o.drop_right, o.drop_bottom)):
        if value is not None and not 0.0 <= float(value) <= 1.0:
            violations.append(RangeViolation(key, float(value), 0.0, 1.0,
                                             note="dio sirine/visine slike"))
    lo, hi = C.BG_ITERATIONS_RANGE
    if not lo <= o.iterations <= hi:
        violations.append(RangeViolation("iteracije", float(o.iterations), lo, hi))
    if not 0 <= o.feather_px <= C.BG_FEATHER_MAX_PX:
        violations.append(RangeViolation("omeksaj_ivicu", float(o.feather_px),
                                         0, C.BG_FEATHER_MAX_PX, unit="px"))
    if violations:
        raise OutOfRange(TEMPLATE, violations)

    if o.background not in C.BG_BACKGROUNDS:
        raise InvalidParam(
            f"pozadina: '{o.background}' nije dozvoljeno, izbor: {list(C.BG_BACKGROUNDS)}"
        )

    reasons = _rect_pair_checks(*_RECT_KEYS[:1], _RECT_KEYS[2], _RECT_KEYS[1], _RECT_KEYS[3],
                                o.left, o.top, o.right, o.bottom)
    if reasons:
        raise InvalidGeometry(reasons, TEMPLATE)

    area_pct = (o.right - o.left) * (o.bottom - o.top) * 100.0
    if area_pct < C.BG_MIN_RECT_PCT:
        raise InvalidGeometry(
            [f"okvir pokriva {area_pct:.2f} % slike, najmanje je {C.BG_MIN_RECT_PCT:g} %"],
            TEMPLATE,
        )

    if o.sure_subject_given() and not o.has_sure_subject():
        raise MissingParam(TEMPLATE, [key for key, value in zip(
            _SURE_KEYS, (o.sure_left, o.sure_top, o.sure_right, o.sure_bottom))
            if value is None])
    if o.has_sure_subject():
        reasons = _rect_pair_checks(_SURE_KEYS[0], _SURE_KEYS[2], _SURE_KEYS[1], _SURE_KEYS[3],
                                    o.sure_left, o.sure_top, o.sure_right, o.sure_bottom)
        if (o.sure_left < o.left or o.sure_top < o.top
                or o.sure_right > o.right or o.sure_bottom > o.bottom):
            reasons.append("sigurni motiv mora biti unutar vanjskog okvira")
        if reasons:
            raise InvalidGeometry(reasons, TEMPLATE)

    if o.drop_given() and not o.has_drop():
        raise MissingParam(TEMPLATE, [key for key, value in zip(
            _DROP_KEYS, (o.drop_left, o.drop_top, o.drop_right, o.drop_bottom))
            if value is None])
    if o.has_drop():
        reasons = _rect_pair_checks(_DROP_KEYS[0], _DROP_KEYS[2], _DROP_KEYS[1], _DROP_KEYS[3],
                                    o.drop_left, o.drop_top, o.drop_right, o.drop_bottom)
        if reasons:
            raise InvalidGeometry(reasons, TEMPLATE)


def rect_pixels(left: float, top: float, right: float, bottom: float,
                size: tuple[int, int]) -> tuple[int, int, int, int]:
    """Okvir u pikselima (x, y, sirina, visina) za sliku velicine (sirina, visina)."""
    width, height = int(size[0]), int(size[1])
    if width <= 0 or height <= 0:
        raise ValidationFailed("slika", ["slika nema pozitivne dimenzije"])
    x0 = max(0, min(int(round(left * width)), width - 1))
    y0 = max(0, min(int(round(top * height)), height - 1))
    x1 = max(x0 + 1, min(int(round(right * width)), width))
    y1 = max(y0 + 1, min(int(round(bottom * height)), height))
    return x0, y0, x1 - x0, y1 - y0


def subject_mask(bgr: np.ndarray, o: BackgroundOptions) -> np.ndarray:
    """Maska motiva (0 ili 255) u punoj velicini ulazne slike."""
    cv2 = _cv2()
    if bgr.ndim != 3 or bgr.shape[2] != 3:
        raise ValidationFailed("slika", ["ocekivana slika u boji (3 kanala)"])

    height, width = bgr.shape[:2]
    longest = max(height, width)
    if longest > C.BG_MASK_WORK_PX:
        scale = C.BG_MASK_WORK_PX / float(longest)
        work = cv2.resize(bgr, (max(1, int(round(width * scale))),
                                max(1, int(round(height * scale)))),
                          interpolation=cv2.INTER_AREA)
    else:
        work = bgr
    wh, ww = work.shape[:2]

    x, y, w, h = rect_pixels(o.left, o.top, o.right, o.bottom, (ww, wh))

    # Fiksno sjeme: GrabCut inicijalizuje GMM k-means-om koji koristi RNG.
    cv2.setRNGSeed(C.BG_RNG_SEED)
    background_model = np.zeros((1, 65), np.float64)
    foreground_model = np.zeros((1, 65), np.float64)

    if o.has_sure_subject() or o.has_drop():
        mask = np.full((wh, ww), cv2.GC_BGD, np.uint8)
        mask[y:y + h, x:x + w] = cv2.GC_PR_FGD
        if o.has_sure_subject():
            sx, sy, sw, sh = rect_pixels(o.sure_left, o.sure_top,
                                         o.sure_right, o.sure_bottom, (ww, wh))
            mask[sy:sy + sh, sx:sx + sw] = cv2.GC_FGD
        if o.has_drop():
            dx, dy, dw, dh = rect_pixels(o.drop_left, o.drop_top,
                                         o.drop_right, o.drop_bottom, (ww, wh))
            mask[dy:dy + dh, dx:dx + dw] = cv2.GC_BGD
        cv2.grabCut(work, mask, None, background_model, foreground_model,
                    int(o.iterations), cv2.GC_INIT_WITH_MASK)
    else:
        mask = np.zeros((wh, ww), np.uint8)
        cv2.grabCut(work, mask, (x, y, w, h), background_model, foreground_model,
                    int(o.iterations), cv2.GC_INIT_WITH_RECT)

    subject = np.where((mask == cv2.GC_FGD) | (mask == cv2.GC_PR_FGD), 255, 0).astype(np.uint8)

    share = float(subject.mean()) / 255.0 * 100.0
    if share < C.BG_MIN_SUBJECT_PCT:
        raise InvalidGeometry(
            [f"motiv zauzima {share:.2f} % slike - okvir je promasio motiv ili je "
             f"motiv iste boje kao pozadina (zadaj 'sigurni motiv')"], TEMPLATE)
    if share > C.BG_MAX_SUBJECT_PCT:
        raise InvalidGeometry(
            [f"motiv zauzima {share:.2f} % slike - pozadina nije odvojena, suzi okvir"],
            TEMPLATE)

    if work is not bgr:
        subject = cv2.resize(subject, (width, height), interpolation=cv2.INTER_LINEAR)
        subject = np.where(subject >= 128, 255, 0).astype(np.uint8)

    if o.feather_px > 0:
        kernel = 2 * int(o.feather_px) + 1
        subject = cv2.GaussianBlur(subject, (kernel, kernel), 0)
    return subject


def remove_background(bgr: np.ndarray, o: BackgroundOptions) -> tuple[np.ndarray, dict]:
    """Slika sa zamijenjenom pozadinom i izvjestaj o odluci.

    ``pozadina="providna"`` daje BGRA (alfa = maska motiva), ostalo daje BGR.
    """
    validate_options(o)
    cv2 = _cv2()
    mask = subject_mask(bgr, o)
    alpha = mask.astype(np.float32) / 255.0
    share = float(mask.mean()) / 255.0 * 100.0

    if o.background == "providna":
        out = cv2.cvtColor(bgr, cv2.COLOR_BGR2BGRA)
        out[:, :, 3] = mask
    else:
        fill = 255.0 if o.background == "bijela" else 0.0
        out = (bgr.astype(np.float32) * alpha[:, :, None]
               + fill * (1.0 - alpha[:, :, None]))
        out = np.clip(out, 0, 255).astype(np.uint8)

    info = {
        "okvir_px": rect_pixels(o.left, o.top, o.right, o.bottom,
                                (bgr.shape[1], bgr.shape[0])),
        "sigurni_motiv": o.has_sure_subject(),
        "izbaceni_okvir": o.has_drop(),
        "udio_motiva_posto": round(share, 2),
        "iteracije": int(o.iterations),
        "omeksano_px": int(o.feather_px),
        "pozadina": o.background,
        "sjeme": C.BG_RNG_SEED,
    }
    return out, info


def remove_background_bytes(data: bytes, o: BackgroundOptions) -> tuple[bytes, dict]:
    """Ulaz su bajtovi slike (PNG/JPG), izlaz su PNG bajtovi bez pozadine.

    Ovo je ulazna tacka za web tok (``_process_upload``): slika se ocisti prije
    nego udje u trasiranje, raster gravuru ili litofaniju.
    """
    cv2 = _cv2()
    bgr = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if bgr is None:
        raise ValidationFailed("slika", ["slika nije citljiva (ocekivan PNG ili JPG)"])
    out, info = remove_background(bgr, o)
    ok, encoded = cv2.imencode(".png", out)
    if not ok:
        raise ValidationFailed("slika", ["PNG kodiranje slike nije uspjelo"])
    return encoded.tobytes(), info

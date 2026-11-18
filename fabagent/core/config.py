"""Centralna konfiguracija FabAgent-a.

Ovdje zive SVE granice, tolerance, materijali, konstante algoritama i seme
parametara sablona. Kod u ostalim modulima ne smije sadrzavati "magicne brojeve" -
svaka brojcana odluka referencira konstantu iz ovog fajla (ili iz laser/presets.py
za parametre lasera po materijalu).

Vrijednosti oznacene sa ``PROVJERITI`` zavise od konkretne masine i moraju se
uskladiti sa stvarnim uredjajem prije produkcije.
"""

from __future__ import annotations

import string
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Opste
# ---------------------------------------------------------------------------
UNITS = "mm"
GEOMETRY_EPS_MM = 1e-6          # numericka tolerancija za poredjenje koordinata

# ---------------------------------------------------------------------------
# Masine
# ---------------------------------------------------------------------------
LASER_MACHINE_NAME = "ArgoLaser MK20 (20 W opticka dioda)"
# IZMJERENO na masini 14.11.2026, COM4, komandom $$: $130 = 400.000, $131 = 400.000.
# Stranica proizvodjaca navodi 400 x 410 mm - masina kaze 400 x 400. Vrijedi masina.
LASER_WORK_AREA_MM = (400.0, 400.0)            # IZMJERENO ($130, $131)
PRINTER_NAME = "FDM printer"
PRINTER_BUILD_VOLUME_MM = (220.0, 220.0, 250.0)  # PROVJERITI: radni volumen (X, Y, Z)

# ---------------------------------------------------------------------------
# Laser - geometrijska pravila
# ---------------------------------------------------------------------------
LASER_MIN_WALL_MM = 1.5             # NEMAM POTVRDU - nije mjereno na materijalu
LASER_MIN_WALL_IZMJERENO = False    # dok je False, tanji zid je UPOZORENJE, ne blokada.
                                    # Komercijalni paketi redovno imaju 1.1-1.5 mm i rezu
                                    # se bez problema; blokirati posao zbog pretpostavljenog
                                    # broja znaci zaustaviti rad bez dokaza. Kad se izmjeri,
                                    # upisi stvarnu vrijednost i postavi ovo na True.
LASER_MIN_HOLE_DIAMETER_MM = 2.0    # NEMAM POTVRDU - nije mjereno
LASER_MIN_HOLE_IZMJERENO = False    # dok je False, manja rupa je UPOZORENJE, ne blokada
CUT_ENGRAVE_CLEARANCE_MM = 0.5      # min razmak ENGRAVE/SCORE geometrije od CUT linije
TEXT_EDGE_MARGIN_MM = 1.0           # min razmak teksta od ivice dijela (>= clearance)
LASER_MIN_ENGRAVE_FEATURE_MM = 0.3  # min velicina "piksela" teksta / QR modula
ARC_CHORD_TOLERANCE_MM = 0.01       # max odstupanje tetive od luka (2D konture)
ARC_MIN_SEGMENTS = 16
ARC_MAX_SEGMENTS = 720
SHAPE_SMOOTHING_ITERATIONS = 4      # Chaikin iteracije za organske oblike (pas, macka)
HEART_SAMPLES = 400                 # broj tacaka parametarske krivulje srca
LASER_DOC_PADDING_MM = 1.0          # prazan rub dokumenta oko dijela (rez nije na ivici)
LABEL_LETTER_SPACING_CELLS = 1.0    # razmak slova na oznakama (u celijama fonta)
WARP_DENSIFY_MM = 0.25              # max duzina ivice prije savijanja teksta na konus

SVG_COORD_DECIMALS = 4
SVG_STROKE_WIDTH_MM = 0.1
LAYERS = ("CUT", "ENGRAVE", "SCORE")
LAYER_COLORS = {"CUT": "#FF0000", "ENGRAVE": "#000000", "SCORE": "#0000FF"}
LAYER_EXECUTION_ORDER = ("ENGRAVE", "SCORE", "CUT")  # rez uvijek zadnji

# Procjena vremena lasera
LASER_CONTOUR_OVERHEAD_S = 0.3      # ubrzanje/pozicioniranje po konturi i prolazu
ENGRAVE_OVERSCAN_MM = 2.5           # prelet na svakom kraju linije pri fill graviranju

# Preview PNG
PREVIEW_PX_PER_MM = 8.0
PREVIEW_MAX_SIDE_PX = 4000
PREVIEW_BACKGROUND_RGB = (255, 255, 255)
PREVIEW_ENGRAVE_RGB = (40, 40, 40)
PREVIEW_CUT_RGB = (230, 0, 0)
PREVIEW_SCORE_RGB = (0, 0, 230)
PREVIEW_GUIDE_RGB = (190, 190, 190)

# ---------------------------------------------------------------------------
# Font (blok font, tekst -> zatvorene konture; laser ne treba fontove)
# ---------------------------------------------------------------------------
FONT_CELL_COLS = 5          # sirina znaka u celijama
FONT_CAP_ROWS = 7           # visina velikog slova u celijama (= visina_teksta)
FONT_ACCENT_ROWS = 2        # celije iznad za kvacice (C, S, Z, C')
FONT_CHARSET = "ABCDEFGHIJKLMNOPQRSTUVWXYZČĆŠŽĐ0123456789 -.:/#+,!?()_&"
ASCII_PRINTABLE = "".join(chr(c) for c in range(32, 127))

# ---------------------------------------------------------------------------
# QR / barkod
# ---------------------------------------------------------------------------
QR_QUIET_ZONE_MODULES = 4           # ISO/IEC 18004 zahtijeva min 4 modula
QR_MIN_VERSION = 1
QR_MAX_VERSION = 40
BARCODE_QUIET_ZONE_MODULES = 10     # Code 128: min 10x sirina modula
BARCODE_TEXT_GAP_MM = 1.0           # razmak barkod -> tekst ispod

# ---------------------------------------------------------------------------
# FDM stampa - geometrijska pravila
# ---------------------------------------------------------------------------
FDM_MIN_WALL_MM = 1.2
FDM_MIN_HOLE_DIAMETER_MM = 2.0
FDM_MAX_OVERHANG_DEG = 45.0          # preko ovoga -> upozorenje (potrebna podrska)
MESH_CHORD_TOLERANCE_MM = 0.02
MESH_MIN_SEGMENTS = 24
MESH_MAX_SEGMENTS = 128
MESH_MERGE_DECIMALS = 6              # spajanje vrhova (zaokruzivanje na 1e-6 mm)
MESH_MIN_FACE_AREA_MM2 = 1e-9
WALL_RAY_MAX_SAMPLES = 1500          # max broj zraka za provjeru debljine zida
WALL_RAY_START_OFFSET_MM = 1e-4      # pomak starta zrake od povrsine
WALL_THICKNESS_TOLERANCE_MM = MESH_CHORD_TOLERANCE_MM + 1e-3  # tetive lukova smanjuju zid najvise za toleranciju tetive
RAY_CHUNK = 128                      # broj zraka po numpy bloku
PRINT_PLATE_GAP_MM = 5.0             # razmak izmedju tijela na ploci (kutija + poklopac)

FILAMENTS = {
    "PLA": {"density_g_cm3": 1.24, "nozzle_c": 210, "bed_c": 60},
    "PETG": {"density_g_cm3": 1.27, "nozzle_c": 240, "bed_c": 80},
}

# Gruba procjena vremena stampe
PRINT_SHELL_THICKNESS_MM = 0.9       # ~2 perimetra x 0.45 mm
PRINT_VOLUMETRIC_FLOW_MM3_S = 8.0    # prosjecan protok (ukljucuje sporije perimetre)
PRINT_TRAVEL_OVERHEAD_FACTOR = 1.3   # dodatak za kretanje bez ekstruzije
PRINT_LAYER_CHANGE_S = 2.0

# ---------------------------------------------------------------------------
# Materijali za laser (kljucevi moraju postojati u laser/presets.py)
# ---------------------------------------------------------------------------
LASER_MATERIALS_CUT = ("sperploca_3mm", "mdf_3mm", "pleksi_crni_3mm", "koza_2mm")
LASER_MATERIALS_ENGRAVE_ONLY = ("plastificirani_celik", "eloksirani_aluminij")
LASER_MATERIALS_ALL = LASER_MATERIALS_CUT + LASER_MATERIALS_ENGRAVE_ONLY

# Privjesak: oblici i pozicija centra teksta (udio visine dijela, 0 = vrh).
KEYCHAIN_SHAPES = ("krug", "pravougaonik", "srce", "pas", "macka")
KEYCHAIN_TEXT_CENTER_V = {
    "krug": 0.60,
    "pravougaonik": 0.60,
    "srce": 0.54,
    "pas": 0.62,
    "macka": 0.66,
}
# Kontrolni poligoni (normalizovani 0..1, y prema dolje) za organske oblike.
DOG_HEAD_CONTROL = (
    (0.50, 0.08), (0.70, 0.10), (0.85, 0.12), (1.00, 0.30), (0.97, 0.58),
    (0.86, 0.62), (0.78, 0.52), (0.78, 0.75), (0.65, 0.92), (0.50, 0.98),
    (0.35, 0.92), (0.22, 0.75), (0.22, 0.52), (0.14, 0.62), (0.03, 0.58),
    (0.00, 0.30), (0.15, 0.12), (0.30, 0.10),
)
CAT_HEAD_CONTROL = (
    (0.50, 0.22), (0.68, 0.20), (0.88, 0.00), (0.95, 0.35), (1.00, 0.60),
    (0.92, 0.85), (0.72, 0.98), (0.50, 1.00), (0.28, 0.98), (0.08, 0.85),
    (0.00, 0.60), (0.05, 0.35), (0.12, 0.00), (0.32, 0.20),
)


# ---------------------------------------------------------------------------
# Seme parametara
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ParamSpec:
    """Definicija jednog parametra: tip, opseg, obaveznost.

    ``default`` postoji samo za opcione parametre i primjenjuje se ISKLJUCIVO kada
    korisnik eksplicitno trazi ``--use-defaults``.
    ``applies_when`` = (ime_parametra, (dozvoljene vrijednosti,)) - parametar je
    primjenjiv samo kada drugi parametar ima jednu od tih vrijednosti.
    """

    name: str
    type: str  # "float" | "int" | "str" | "enum" | "bool"
    description: str
    required: bool = True
    default: Any = None
    min: float | None = None
    max: float | None = None
    choices: tuple = ()
    unit: str = ""
    min_len: int | None = None
    max_len: int | None = None
    allowed_chars: str | None = None
    applies_when: tuple | None = None

    def __post_init__(self) -> None:
        if self.type not in ("float", "int", "str", "enum", "bool"):
            raise ValueError(f"{self.name}: nepoznat tip {self.type}")
        if self.required and self.default is not None:
            raise ValueError(f"{self.name}: obavezan parametar ne smije imati default")
        if not self.required and self.default is None:
            raise ValueError(f"{self.name}: opcioni parametar mora imati default")
        if self.type == "enum" and not self.choices:
            raise ValueError(f"{self.name}: enum bez izbora")


@dataclass(frozen=True)
class TemplateSpec:
    name: str
    kind: str  # "laser" | "print"
    description: str
    params: tuple[ParamSpec, ...]
    example: dict = field(default_factory=dict)

    def param(self, name: str) -> ParamSpec:
        for p in self.params:
            if p.name == name:
                return p
        raise KeyError(name)


def _f(name, desc, lo, hi, unit="mm", default=None, applies_when=None):
    return ParamSpec(name, "float", desc, required=default is None, default=default,
                     min=lo, max=hi, unit=unit, applies_when=applies_when)


def _i(name, desc, lo, hi, default=None, choices=()):
    return ParamSpec(name, "int", desc, required=default is None, default=default,
                     min=lo, max=hi, choices=choices)


def _e(name, desc, choices, default=None):
    return ParamSpec(name, "enum", desc, required=default is None, default=default,
                     choices=tuple(choices))


def _s(name, desc, min_len, max_len, chars, default=None):
    return ParamSpec(name, "str", desc, required=default is None, default=default,
                     min_len=min_len, max_len=max_len, allowed_chars=chars)


def _b(name, desc, default=None):
    return ParamSpec(name, "bool", desc, required=default is None, default=default)


# ---------------------------- LASER ----------------------------------------
_LASER_TEMPLATES = (
    TemplateSpec(
        "privesak", "laser",
        "Privjesak (krug, pravougaonik, srce, pas, macka) sa rupom za prsten i graviranim tekstom.",
        (
            _e("oblik", "oblik privjeska", KEYCHAIN_SHAPES),
            _f("sirina", "vanjska sirina", 15, 100),
            _f("visina", "vanjska visina (za krug mora biti jednaka sirini)", 15, 100),
            _e("materijal", "materijal (preset lasera)", LASER_MATERIALS_CUT),
            _s("tekst", "tekst za graviranje (velika slova)", 1, 16, FONT_CHARSET),
            _f("visina_teksta", "visina velikog slova", 3, 25),
            _f("rupa_promjer", "promjer rupe za prsten", LASER_MIN_HOLE_DIAMETER_MM, 10),
            _f("rupa_odmak", "materijal izmedju ivice i rupe", LASER_MIN_WALL_MM, 15, default=2.5),
            _f("radijus_ugla", "radijus uglova", 0, 50, default=3.0,
               applies_when=("oblik", ("pravougaonik",))),
            _f("razmak_slova", "razmak izmedju slova (u celijama fonta)", 0.5, 3, unit="celija",
               default=1.0),
            _f("tekst_pomak_y", "vertikalni pomak teksta (+ prema dolje)", -20, 20, default=0.0),
        ),
        example={"oblik": "krug", "sirina": 40, "visina": 40, "materijal": "sperploca_3mm",
                 "tekst": "DOBOJ", "visina_teksta": 6, "rupa_promjer": 5,
                 "rupa_odmak": 2.5, "razmak_slova": 1, "tekst_pomak_y": 0},
    ),
    TemplateSpec(
        "casa", "laser",
        "Gravura na casi/termosu: ravna projekcija omotaca (cilindar ili konus).",
        (
            _f("promjer_gore", "promjer na vrhu zone graviranja", 30, 150),
            _f("promjer_dolje", "promjer na dnu zone graviranja", 30, 150),
            _f("visina_zone", "vertikalna visina zone graviranja", 10, 300),
            _e("materijal", "materijal povrsine", LASER_MATERIALS_ALL),
            _s("tekst", "tekst za graviranje", 1, 24, FONT_CHARSET),
            _f("visina_teksta", "visina velikog slova", 3, 80),
            _f("ugao_centra", "ugao centra teksta po obimu", 0, 360, unit="deg", default=180.0),
            _f("vertikalna_pozicija", "centar teksta (0=vrh zone, 1=dno)", 0, 1, unit="", default=0.5),
            _i("broj_ponavljanja", "koliko puta se tekst ponavlja po obimu", 1, 8, default=1),
            _f("razmak_slova", "razmak izmedju slova (celije)", 0.5, 3, unit="celija", default=1.0),
        ),
        example={"promjer_gore": 75, "promjer_dolje": 75, "visina_zone": 90,
                 "materijal": "plastificirani_celik", "tekst": "KAFA", "visina_teksta": 15,
                 "ugao_centra": 180, "vertikalna_pozicija": 0.5, "broj_ponavljanja": 1,
                 "razmak_slova": 1},
    ),
    TemplateSpec(
        "plocica", "laser",
        "Plocica/drzac sa 2 ili 4 rupe za vijke i opcionim tekstom.",
        (
            _f("sirina", "sirina", 20, 390),
            _f("visina", "visina", 20, 390),
            _e("materijal", "materijal", LASER_MATERIALS_CUT),
            _i("broj_rupa", "broj rupa (2 = lijevo/desno, 4 = uglovi)", 2, 4, choices=(2, 4)),
            _f("rupa_promjer", "promjer rupe za vijak", LASER_MIN_HOLE_DIAMETER_MM, 12),
            _f("rupa_odmak", "udaljenost centra rupe od ivice", 2.5, 50),
            _f("radijus_ugla", "radijus uglova", 0, 50, default=2.0),
            _s("tekst", "tekst (prazno = bez teksta)", 0, 32, FONT_CHARSET, default=""),
            _f("visina_teksta", "visina velikog slova", 3, 60, default=6.0),
        ),
        example={"sirina": 120, "visina": 40, "materijal": "sperploca_3mm", "broj_rupa": 2,
                 "rupa_promjer": 4, "rupa_odmak": 6, "radijus_ugla": 3, "tekst": "KUCA 12",
                 "visina_teksta": 10},
    ),
    TemplateSpec(
        "oznaka_serijski", "laser",
        "Oznaka sa serijskim brojem (tekst u putanjama), opciono izrezana.",
        (
            _s("serijski_broj", "serijski broj", 1, 24, FONT_CHARSET),
            _f("visina_teksta", "visina znakova", 2, 30),
            _e("materijal", "materijal", LASER_MATERIALS_ALL),
            _f("margina", "margina oko sadrzaja", 1, 20, default=3.0),
            _b("rez_okvir", "izrezati okvir oznake", default=True),
            _f("radijus_ugla", "radijus uglova okvira", 0, 20, default=1.5),
        ),
        example={"serijski_broj": "SN-2026-0001", "visina_teksta": 4, "materijal": "mdf_3mm",
                 "margina": 3, "rez_okvir": True, "radijus_ugla": 1.5},
    ),
    TemplateSpec(
        "oznaka_qr", "laser",
        "Oznaka sa QR kodom (ISO/IEC 18004, byte mod), opciono izrezana.",
        (
            _s("sadrzaj", "sadrzaj QR koda (ASCII)", 1, 300, ASCII_PRINTABLE),
            _f("modul_mm", "velicina jednog QR modula", LASER_MIN_ENGRAVE_FEATURE_MM, 3),
            _e("materijal", "materijal", LASER_MATERIALS_ALL),
            _e("ecc", "nivo korekcije gresaka", ("L", "M", "Q", "H"), default="M"),
            _f("margina", "margina izvan tihe zone", 0, 20, default=2.0),
            _b("rez_okvir", "izrezati okvir oznake", default=True),
            _f("radijus_ugla", "radijus uglova okvira", 0, 20, default=1.5),
            _b("invertovano", "gravirati svijetle module (za materijale koji posvijetle)",
               default=False),
        ),
        example={"sadrzaj": "https://example.com/p/123", "modul_mm": 0.8, "materijal": "sperploca_3mm",
                 "ecc": "M", "margina": 2, "rez_okvir": True, "radijus_ugla": 1.5,
                 "invertovano": False},
    ),
    TemplateSpec(
        "oznaka_barkod", "laser",
        "Oznaka sa Code 128 (B) barkodom i opcionim tekstom ispod.",
        (
            _s("sadrzaj", "sadrzaj barkoda (ASCII)", 1, 40, ASCII_PRINTABLE),
            _f("modul_mm", "sirina najuzeg bara", LASER_MIN_ENGRAVE_FEATURE_MM, 1.5),
            _f("visina_barkoda", "visina barova", 5, 60),
            _e("materijal", "materijal", LASER_MATERIALS_ALL),
            _b("tekst_ispod", "ispisati sadrzaj ispod barkoda", default=True),
            _f("visina_teksta", "visina teksta ispod", 2, 15, default=3.0),
            _f("margina", "margina izvan tihe zone", 0, 20, default=2.0),
            _b("rez_okvir", "izrezati okvir oznake", default=True),
            _f("radijus_ugla", "radijus uglova okvira", 0, 20, default=1.5),
        ),
        example={"sadrzaj": "FAB-000123", "modul_mm": 0.4, "visina_barkoda": 12,
                 "materijal": "sperploca_3mm", "tekst_ispod": True, "visina_teksta": 3,
                 "margina": 2, "rez_okvir": True, "radijus_ugla": 1.5},
    ),
    TemplateSpec(
        "burence", "laser",
        "Burence za vino: daske sa cepovima, prednja/zadnja strana sa rupom za bocu, opciono baza sa fiokom. "
        "Dijelovi se automatski rasporedjuju na ploce.",
        (
            _f("promjer_mm", "promjer prednje/zadnje strane", 80, 390),
            _f("visina_mm", "ukupna duzina bureta (od prednje do zadnje strane)", 40, 390),
            _i("broj_dasaka", "broj dasaka po obimu", 6, 24),
            _f("debljina_mm", "debljina materijala (mora odgovarati materijalu)", 2, 6),
            _f("promjer_boce_mm", "promjer boce", 20, 150),
            _b("ima_fioku", "dodati bazu sa fiokom"),
            _e("materijal", "materijal (preset lasera)", LASER_MATERIALS_CUT),
        ),
        example={"promjer_mm": 200, "visina_mm": 150, "broj_dasaka": 12, "debljina_mm": 3,
                 "promjer_boce_mm": 75, "ima_fioku": False, "materijal": "sperploca_3mm"},
    ),
)

# Burence - konstrukcijske konstante
NEST_GAP_MM = 3.0                    # razmak izmedju dijelova na ploci
BARREL_RIM_MM = 4.0                  # koliko strana viri izvan dasaka (radijalno)
BARREL_TAB_WIDTH_RATIO = 0.5         # sirina cepa = udio sirine daske
BARREL_SLOT_CLEARANCE_MM = 0.1       # zazor proreza za cep (ukupno, po dimenziji)
BARREL_RING_HOLE_D_MM = 3.0          # rupe za "prstenove" (kanap/zica) na 1/3 i 2/3
BARREL_BOTTLE_CLEARANCE_MM = 2.0     # rupa za bocu = promjer boce + zazor
BARREL_BASE_WIDTH_RATIO = 0.6        # sirina baze = udio promjera bureta
BARREL_BASE_HEIGHT_RATIO = 0.3       # visina baze = udio promjera bureta
BARREL_SADDLE_MIN_MATERIAL_MM = 15.0 # materijal ispod luka sedla
DRAWER_CLEARANCE_MM = 1.0            # zazor fioke po strani
DRAWER_KNOB_HOLE_D_MM = 6.0          # rupa za rucicu fioke

# Upload SVG - automatski popravak
UPLOAD_MAX_BYTES = 10 * 1024 * 1024
# Arhive se citaju sa diska, ne salju kroz formu. Spisak od 1000 crteza se
# procita za oko 150 ms i na arhivi od 15 GB, jer ZIP nosi spisak na kraju.
ARHIVA_DUBINA = 2                    # koliko nivoa podfoldera se pretrazuje za .zip
ARHIVA_MAX_PRIKAZ = 300              # koliko unosa stane na stranicu; ostalo se suzava pretragom
UPLOAD_JOIN_TOL_MM = 0.1             # krajevi blizi od ovoga se spajaju
UPLOAD_DUP_TOL_MM = 0.05             # putanje koje se poklapaju unutar ovoga su duplikati
UPLOAD_CURVE_STEP_MM = 0.2           # korak uzorkovanja krivulja (Bezier, luk)
UPLOAD_TEXT_CAP_RATIO = 0.7          # visina velikog slova / font-size (blok font zamjena)
CSS_PX_PER_IN = 96.0                 # SVG/CSS standard: 1 px = 1/96 in
MM_PER_IN = 25.4
PT_PER_IN = 72.0

# ---------------------------- PRINT ----------------------------------------
_PRINT_COMMON = (
    _e("materijal", "filament", tuple(FILAMENTS)),
    _f("ispuna_posto", "ispuna (infill)", 5, 100, unit="%", default=20.0),
    _f("visina_sloja", "visina sloja", 0.08, 0.32, default=0.2),
)

_PRINT_TEMPLATES = (
    TemplateSpec(
        "nosac_l", "print",
        "L-nosac sa filetom u unutrasnjem uglu i 4 rupe za vijke (2 po kraku).",
        (
            _f("duzina_a", "vanjska duzina horizontalnog kraka", 15, 200),
            _f("duzina_b", "vanjska visina vertikalnog kraka", 15, 200),
            _f("sirina", "sirina nosaca", 10, 150),
            _f("debljina", "debljina krakova", FDM_MIN_WALL_MM, 15),
            _f("rupa_promjer", "promjer rupe za vijak", FDM_MIN_HOLE_DIAMETER_MM, 12),
            _f("rupa_razmak", "razmak centara rupa po sirini", 4, 140),
            _f("rupa_od_kraja", "udaljenost centra rupe od slobodnog kraja kraka", 3, 100),
            _f("filet_radijus", "radijus fileta u unutrasnjem uglu", 0, 20, default=3.0),
        ) + _PRINT_COMMON,
        example={"duzina_a": 40, "duzina_b": 40, "sirina": 30, "debljina": 4,
                 "rupa_promjer": 4.5, "rupa_razmak": 16, "rupa_od_kraja": 10,
                 "filet_radijus": 4, "materijal": "PETG", "ispuna_posto": 40,
                 "visina_sloja": 0.2},
    ),
    TemplateSpec(
        "kutija", "print",
        "Kutija sa poklopcem koji ulazi unutra (zadana tolerancija), oba dijela na istoj ploci.",
        (
            _f("duzina", "vanjska duzina", 20, 200),
            _f("sirina", "vanjska sirina", 20, 200),
            _f("visina", "vanjska visina kutije (bez poklopca)", 10, 200),
            _f("debljina_zida", "debljina zida", FDM_MIN_WALL_MM, 10),
            _f("debljina_dna", "debljina dna", FDM_MIN_WALL_MM, 10),
            _f("tolerancija", "zracnost poklopca po strani", 0.05, 1.0),
            _f("radijus_ugla", "vanjski radijus uglova", 0, 30, default=3.0),
            _f("debljina_poklopca", "debljina ploce poklopca", FDM_MIN_WALL_MM, 10, default=2.0),
            _f("visina_ruba_poklopca", "visina ruba koji ulazi u kutiju", 2, 30, default=5.0),
            _f("debljina_ruba_poklopca", "debljina ruba poklopca", FDM_MIN_WALL_MM, 10, default=1.6),
        ) + _PRINT_COMMON,
        example={"duzina": 80, "sirina": 60, "visina": 40, "debljina_zida": 2,
                 "debljina_dna": 2, "tolerancija": 0.25, "radijus_ugla": 4,
                 "debljina_poklopca": 2, "visina_ruba_poklopca": 6,
                 "debljina_ruba_poklopca": 1.6, "materijal": "PLA", "ispuna_posto": 20,
                 "visina_sloja": 0.2},
    ),
    TemplateSpec(
        "adapter", "print",
        "Adapter/redukcija izmedju dva promjera cijevi (rotaciono tijelo).",
        (
            _f("d1_vanjski", "vanjski promjer kraja 1 (dolje)", 5, 200),
            _f("d1_unutarnji", "unutarnji promjer kraja 1", 2, 196),
            _f("duzina1", "duzina kraja 1", 2, 200),
            _f("d2_vanjski", "vanjski promjer kraja 2 (gore)", 5, 200),
            _f("d2_unutarnji", "unutarnji promjer kraja 2", 2, 196),
            _f("duzina2", "duzina kraja 2", 2, 200),
            _f("duzina_prelaza", "duzina konusnog prelaza", 0, 200),
        ) + _PRINT_COMMON,
        example={"d1_vanjski": 50, "d1_unutarnji": 44, "duzina1": 25, "d2_vanjski": 32,
                 "d2_unutarnji": 27, "duzina2": 25, "duzina_prelaza": 20,
                 "materijal": "PETG", "ispuna_posto": 100, "visina_sloja": 0.2},
    ),
    TemplateSpec(
        "poklopac", "print",
        "Okrugli poklopac koji se navlaci preko cijevi (stampa se vrhom na ploci).",
        (
            _f("promjer_cijevi", "vanjski promjer cijevi", 5, 200),
            _f("tolerancija", "zracnost po strani", 0.05, 1.0),
            _f("debljina_zida", "debljina zida ruba", FDM_MIN_WALL_MM, 10),
            _f("visina_ruba", "visina ruba", 2, 100),
            _f("debljina_vrha", "debljina vrha", FDM_MIN_WALL_MM, 10),
        ) + _PRINT_COMMON,
        example={"promjer_cijevi": 32, "tolerancija": 0.2, "debljina_zida": 2,
                 "visina_ruba": 12, "debljina_vrha": 2, "materijal": "PETG",
                 "ispuna_posto": 100, "visina_sloja": 0.2},
    ),
    TemplateSpec(
        "drzac_cijevi", "print",
        "Drzac (casica) za okrugli predmet na bazi sa 2 rupe za vijke.",
        (
            _f("unutarnji_promjer", "unutarnji promjer casice", 5, 150),
            _f("debljina_zida", "debljina zida casice", FDM_MIN_WALL_MM, 10),
            _f("visina", "visina casice iznad baze", 5, 150),
            _f("debljina_baze", "debljina baze (ujedno dno casice)", FDM_MIN_WALL_MM, 15),
            _f("duzina_baze", "duzina baze", 20, 220),
            _f("sirina_baze", "sirina baze", 10, 220),
            _f("rupa_promjer", "promjer rupe za vijak", FDM_MIN_HOLE_DIAMETER_MM, 10),
            _f("rupa_razmak", "razmak centara rupa (po duzini)", 10, 210),
        ) + _PRINT_COMMON,
        example={"unutarnji_promjer": 30, "debljina_zida": 2.4, "visina": 25,
                 "debljina_baze": 3, "duzina_baze": 70, "sirina_baze": 40,
                 "rupa_promjer": 4, "rupa_razmak": 56, "materijal": "PETG",
                 "ispuna_posto": 30, "visina_sloja": 0.2},
    ),
)

LASER_TEMPLATES: dict[str, TemplateSpec] = {t.name: t for t in _LASER_TEMPLATES}
PRINT_TEMPLATES: dict[str, TemplateSpec] = {t.name: t for t in _PRINT_TEMPLATES}

# Znakovi dozvoljeni u nazivima parametara sa CLI-a
PARAM_NAME_CHARS = string.ascii_lowercase + string.digits + "_"

# Konverzija drugih formata (core/converter.py)
IMAGE_THRESHOLD_MIN = 1              # piksel < prag -> tamno (dio crteza)
IMAGE_THRESHOLD_MAX = 255
IMAGE_WIDTH_MIN_MM = 5.0
IMAGE_MAX_PIXELS = 1_500_000         # trasiranje u cistom Pythonu - veca slika = sporije
IMAGE_ALPHA_OPAQUE = 128             # alfa ispod ovoga = providno (pozadina)
POTRACE_TURDSIZE = 2                 # mrlje manje od ovoliko piksela se zanemaruju
POTRACE_ALPHAMAX = 1.0               # zadrzano zbog vtracera; vlastiti motor ga ne koristi
POTRACE_OPTTOLERANCE = 0.2           # tolerancija optimizacije krivulja (potrace standard)

# Napredna obrada slike (core/image_trace.py)
TRACE_DENOISE_MAX = 10               # nivo uklanjanja suma (kernel = 2*nivo+1 px)
TRACE_SIMPLIFY_MAX_PX = 5.0          # max RDP tolerancija u pikselima
TRACE_ADAPTIVE_BLOCK_PX = 31         # velicina susjedstva za adaptivni prag (neparno)
TRACE_ADAPTIVE_C = 5                 # konstanta oduzeta od lokalnog prosjeka (adaptivni prag)
TRACE_FLATTEN_STEP_PX = 0.5          # korak uzorkovanja Bezier krivulja prije RDP
TRACE_VTRACER_PRECISION = 3          # broj decimala u vtracer putanjama
TRACE_VTRACER_TIMEOUT_S = 60         # vtracer radi u zasebnom procesu (izolacija od pada)

# Kamera (core/camera.py)
CAMERA_SOURCE_ENV = "FABAGENT_CAMERA"   # indeks kamere (npr. "0") ili putanja do slike za test
CAMERA_DEFAULT_SOURCE = "0"
CAMERA_PX_PER_MM = 2.0               # rezolucija transformisane slike radne povrsine
CAMERA_JPEG_QUALITY = 80
CAMERA_STREAM_FPS = 8.0
CAMERA_MIN_QUAD_AREA_PX = 2000.0     # min povrsina cetverougla kalibracije (u pikselima kamere)
CAMERA_OVERLAY_OPACITY = 0.6

# ---------------------------------------------------------------------------
# Upravljanje laserom - GRBL (core/gcode.py, web /machine)
# IZMJERENO sa masine 14.11.2026, COM4 (USB VID:PID 303A:4003), komandom $$:
#   $30=1000  $32=1  $100=100  $101=100  $110=20040  $111=18040  $130=400  $131=400
# Odgovor na '?': <Idle|MPos:0.000,0.000,0.000|...>
# ---------------------------------------------------------------------------
GRBL_S_MAX = 1000                    # $30 - IZMJERENO
GRBL_LASER_MODE_CMD = "M4"           # M4 = dinamicka snaga; masina ima $32=1, pa M4 radi
# $110 = 20040 (X), $111 = 18040 (Y). Uzima se sporija osa: dijagonalni pokret je
# ogranicen objema, pa bi brzina po brzoj osi bila broj koji masina ne moze drzati.
GRBL_MAX_FEED_MM_MIN = 18040.0       # IZMJERENO (min od $110, $111)
GRBL_TRAVEL_FEED_MM_MIN = 18040.0    # prazan hod: G0 ide po $110/$111, ne po izmisljenom broju
GRBL_BAUD_RATE = 115200
GRBL_HOME_CMD = "$H"                 # homing ciklus (G28 na GRBL-u ide na snimljenu poziciju, nije homing)
MACHINE_ORIGIN = "dolje-lijevo"      # ishodiste masine; SVG/editor koriste gore-lijevo -> Y se okrece
GCODE_DECIMALS = 3

# Sirina koju laser odnese (kerf). None znaci NIJE IZMJERENO - nema pretpostavljene
# vrijednosti jer kerf zavisi od materijala, debljine i fokusa. Mjeri se tako da se
# izreze poznata mjera i izmjeri kaliperom: kerf = nominalno - izmjereno.
# Kad se izmjeri, upisi po materijalu: {"sperploca_3mm": 0.18, ...}
LASER_KERF_MM: float | None = None
LASER_KERF_PO_MATERIJALU: dict[str, float] = {}

# Granice radne povrsine su potvrdjene na masini komandom $$ ($130 = max put X,
# $131 = max put Y), 14.11.2026 na portu COM4.
LASER_BOUNDS_CONFIRMED = True

# Nakon koliko sati se kalibracija kamere smatra zastarjelom. Kamera ili laser se
# pomjere i ne primijeti se; kalibracija koja vise ne odgovara daje lazan osjecaj
# nadzora. Nadzor sa mjesta zavisi od toga da slika zaista pokriva radnu povrsinu.
CAMERA_CALIBRATION_MAX_AGE_H = 24.0
LAYER_MODES = ("Cut", "Engrave", "Score")
LAYER_DEFAULT_MODE = {"CUT": "Cut", "ENGRAVE": "Engrave", "SCORE": "Score"}
LAYER_SPEED_RANGE_MM_S = (1.0, GRBL_MAX_FEED_MM_MIN / 60.0)
LAYER_POWER_RANGE_PCT = (0.0, 100.0)
LAYER_PASSES_RANGE = (1, 20)
LAYER_INTERVAL_RANGE_MM = (0.03, 1.0)

# Raster graviranje slike (core/raster.py)
RASTER_MODES = ("threshold", "dither", "grayscale")
DITHER_ALGORITHMS = ("floyd-steinberg", "jarvis", "stucki")
RASTER_MAX_PIXELS = 1_000_000        # error diffusion je cisti Python - veca slika = sporo
RASTER_MIN_POWER_PCT_RANGE = (0.0, 100.0)

# 3D iz slike (core/image_to_3d.py) - litofanija / reljef
LITHO_PX_PER_MM = 2.0                # gustina mreze (0.5 mm), ~ sirina mlaznice
LITHO_MAX_VERTICES = 120_000
LITHO_MIN_THICKNESS_MM = 0.6         # najtanji dio litofanije (FDM, 0.4 mm mlaznica)
LITHO_MAX_THICKNESS_MM = 10.0
LITHO_WIDTH_RANGE_MM = (20.0, 200.0)
LITHO_MODES = ("litofanija", "reljef")   # litofanija: tamnije = deblje; reljef: svjetlije = vise
RAY_ELEMENT_BUDGET = 3_000_000       # max (zraka x trouglova) u jednom numpy bloku pri provjeri zida
LITHO_LAYER_HEIGHT_MM = 0.12          # procjena vremena/mase litofanije (tipicna visina sloja)

# ---------------------------------------------------------------------------
# Uklanjanje pozadine (core/background.py) - OpenCV GrabCut, bez mreze
# ---------------------------------------------------------------------------
BG_GRABCUT_ITERATIONS = 5            # polazni broj iteracija GrabCut-a
BG_ITERATIONS_RANGE = (1, 15)
BG_RNG_SEED = 20261113               # fiksno sjeme: GrabCut k-means koristi RNG
BG_MIN_SUBJECT_PCT = 1.0             # ispod ovoga je okvir promasio motiv
BG_MAX_SUBJECT_PCT = 98.0            # iznad ovoga pozadina nije odvojena
BG_FEATHER_MAX_PX = 20               # max omeksavanje ivice maske
BG_MIN_RECT_PCT = 1.0                # najmanja dozvoljena povrsina okvira (% slike)
BG_BACKGROUNDS = ("bijela", "crna", "providna")   # bijela = ne gravira se
BG_MASK_WORK_PX = 900                # maska se racuna na umanjenoj kopiji (brzina)

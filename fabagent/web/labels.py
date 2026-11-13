"""Citljiva imena (bosanski) za sablone, parametre, jedinice i izbore.

config.py se ne mijenja: ovdje je samo prikaz. Ako mapiranje fali, koristi se
sirovo ime parametra (fallback), tako da nova polja u config.py uvijek rade.
"""

from __future__ import annotations

from fabagent.core.config import ParamSpec

TEMPLATE_LABELS: dict[str, tuple[str, str]] = {
    # ime: (prikazni naziv, ikona)
    "privesak": ("Privjesak", "\U0001F511"),
    "casa": ("Čaša / termos", "☕"),
    "plocica": ("Pločica sa rupama", "\U0001F4CB"),
    "oznaka_serijski": ("Oznaka – serijski broj", "\U0001F522"),
    "oznaka_qr": ("Oznaka – QR kod", "\U0001F533"),
    "oznaka_barkod": ("Oznaka – barkod", "\U0001F4CA"),
    "nosac_l": ("L-nosač", "\U0001F4D0"),
    "kutija": ("Kutija sa poklopcem", "\U0001F4E6"),
    "adapter": ("Adapter / redukcija", "\U0001F527"),
    "poklopac": ("Okrugli poklopac", "⭕"),
    "drzac_cijevi": ("Držač cijevi", "\U0001F529"),
    "burence": ("Burence za vino", "\U0001F377"),
    "svg_upload": ("Uploadovan SVG", "\U0001F4E4"),
    "slika_raster": ("Raster gravura slike", "\U0001F5BC"),
    "litofanija": ("3D litofanija / reljef", "\U0001F9CA"),
    "raspored": ("Raspored na stolu", "\U0001F4D0"),
}

# Opsta imena parametara.
PARAM_LABELS: dict[str, str] = {
    "oblik": "Oblik",
    "sirina": "Širina",
    "visina": "Visina",
    "materijal": "Materijal",
    "tekst": "Tekst",
    "visina_teksta": "Visina teksta",
    "rupa_promjer": "Promjer rupe",
    "rupa_odmak": "Odmak rupe od ivice",
    "radijus_ugla": "Radijus uglova",
    "razmak_slova": "Razmak slova",
    "tekst_pomak_y": "Vertikalni pomak teksta",
    "promjer_gore": "Promjer na vrhu",
    "promjer_dolje": "Promjer na dnu",
    "visina_zone": "Visina zone graviranja",
    "ugao_centra": "Ugao centra teksta",
    "vertikalna_pozicija": "Vertikalna pozicija (0 = vrh, 1 = dno)",
    "broj_ponavljanja": "Broj ponavljanja po obimu",
    "broj_rupa": "Broj rupa",
    "serijski_broj": "Serijski broj",
    "margina": "Margina",
    "rez_okvir": "Izrezati okvir",
    "sadrzaj": "Sadržaj",
    "modul_mm": "Veličina modula",
    "ecc": "Nivo korekcije grešaka",
    "invertovano": "Invertovano graviranje",
    "visina_barkoda": "Visina barkoda",
    "tekst_ispod": "Tekst ispod barkoda",
    "duzina_a": "Dužina kraka A",
    "duzina_b": "Dužina kraka B",
    "debljina": "Debljina",
    "rupa_razmak": "Razmak rupa",
    "rupa_od_kraja": "Rupa od kraja kraka",
    "filet_radijus": "Radijus fileta",
    "ispuna_posto": "Ispuna (infill)",
    "visina_sloja": "Visina sloja",
    "duzina": "Dužina",
    "debljina_zida": "Debljina zida",
    "debljina_dna": "Debljina dna",
    "tolerancija": "Zračnost (tolerancija)",
    "debljina_poklopca": "Debljina poklopca",
    "visina_ruba_poklopca": "Visina ruba poklopca",
    "debljina_ruba_poklopca": "Debljina ruba poklopca",
    "d1_vanjski": "Kraj 1 – vanjski promjer",
    "d1_unutarnji": "Kraj 1 – unutarnji promjer",
    "duzina1": "Kraj 1 – dužina",
    "d2_vanjski": "Kraj 2 – vanjski promjer",
    "d2_unutarnji": "Kraj 2 – unutarnji promjer",
    "duzina2": "Kraj 2 – dužina",
    "duzina_prelaza": "Dužina prelaza",
    "promjer_cijevi": "Vanjski promjer cijevi",
    "visina_ruba": "Visina ruba",
    "debljina_vrha": "Debljina vrha",
    "unutarnji_promjer": "Unutarnji promjer",
    "duzina_baze": "Dužina baze",
    "sirina_baze": "Širina baze",
    "debljina_baze": "Debljina baze",
    "promjer_mm": "Promjer bureta",
    "visina_mm": "Dužina bureta",
    "broj_dasaka": "Broj dasaka",
    "debljina_mm": "Debljina materijala",
    "promjer_boce_mm": "Promjer boce",
    "ima_fioku": "Baza sa fiokom",
}

# Posebna imena kada isti parametar znaci nesto drugo u odredjenom sablonu.
TEMPLATE_PARAM_LABELS: dict[tuple[str, str], str] = {
    ("kutija", "visina"): "Visina kutije",
    ("drzac_cijevi", "visina"): "Visina čašice",
    ("privesak", "rupa_promjer"): "Promjer rupe za prsten",
    ("plocica", "rupa_odmak"): "Centar rupe od ivice",
}

UNIT_LABELS = {"mm": "mm", "deg": "°", "%": "%", "celija": "ćelija", "": ""}

CHOICE_LABELS: dict[str, dict[str, str]] = {
    "oblik": {"krug": "Krug", "pravougaonik": "Pravougaonik", "srce": "Srce", "pas": "Pas", "macka": "Mačka"},
    "ecc": {"L": "L (~7 %)", "M": "M (~15 %)", "Q": "Q (~25 %)", "H": "H (~30 %)"},
    "materijal": {
        "sperploca_3mm": "Šperploča 3 mm",
        "mdf_3mm": "MDF 3 mm",
        "pleksi_crni_3mm": "Pleksiglas crni 3 mm",
        "koza_2mm": "Koža 2 mm",
        "plastificirani_celik": "Plastificirani čelik (termos)",
        "eloksirani_aluminij": "Eloksirani aluminij",
        "PLA": "PLA",
        "PETG": "PETG",
    },
}

ERROR_TITLES = {
    "MissingParam": "Nedostaju parametri",
    "OutOfRange": "Vrijednost izvan dozvoljenog opsega",
    "InvalidGeometry": "Geometrija nije ispravna",
    "InvalidParam": "Neispravan parametar",
    "ValidationFailed": "Fajl nije prošao validaciju",
    "UnknownTemplate": "Nepoznat šablon",
    "OutputError": "Greška pri upisu fajla",
}


def template_label(name: str) -> str:
    return TEMPLATE_LABELS.get(name, (name, ""))[0]


def template_icon(name: str) -> str:
    return TEMPLATE_LABELS.get(name, ("", "■"))[1] or "■"


def unit_label(unit: str) -> str:
    return UNIT_LABELS.get(unit, unit)


def param_label(template: str, spec: ParamSpec) -> str:
    """Npr. duzina_a -> 'Dužina kraka A (mm)'."""
    base = TEMPLATE_PARAM_LABELS.get((template, spec.name)) or PARAM_LABELS.get(spec.name, spec.name)
    unit = unit_label(spec.unit) if spec.type in ("float", "int") else ""
    return f"{base} ({unit})" if unit else base


def choice_label(param: str, value) -> str:
    return CHOICE_LABELS.get(param, {}).get(str(value), str(value))

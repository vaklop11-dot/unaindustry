"""Radni nalog za operatera: iz podataka o poslu pravi uputstvo za masinu.

Nista se ne izmislja. Svaki broj u nalogu dolazi iz job.json ili iz preseta
materijala (``laser/presets.py``). Ako podatak ne postoji, u nalogu stoji
``NEMA PODATKA`` - nikad pretpostavljena vrijednost.

Preset koji nije verifikovan na masini se oznacava na svakom mjestu gdje se
njegov broj pojavi, a ne samo jednom u dnu stranice.

Redoslijed koraka je redoslijed rada za operatera i ne mijenja se po poslu:
materijal -> postavljanje -> fokus -> postavke -> prazan hod -> rad -> provjera.
"""

from __future__ import annotations

NEMA = "NEMA PODATKA"


def _broj(value, jedinica: str = "", decimala: int = 2) -> str:
    if value is None:
        return NEMA
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    text = f"{number:.{decimala}f}"
    if "." in text:                      # 6000 ne smije postati 6
        text = text.rstrip("0").rstrip(".")
    return f"{text} {jedinica}".strip()


def _vrijeme(seconds) -> str:
    if seconds is None:
        return NEMA
    total = int(round(float(seconds)))
    minutes, sec = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours} h {minutes} min"
    if minutes:
        return f"{minutes} min {sec} s"
    return f"{sec} s"


def preset_posla(job: dict) -> dict:
    """Preset materijala iz posla. Ako posao nosi samo kljuc, dohvati ga iz presets.py.

    Ne izmislja materijal: ako kljuc ne postoji ili nije poznat, vraca prazno.
    """
    preset = job.get("material_preset")
    if isinstance(preset, dict) and preset:
        return preset
    key = job.get("preset") or (job.get("params") or {}).get("materijal")
    if not key:
        return {}
    try:
        from fabagent.laser.presets import get_preset
        return get_preset(str(key)).as_dict()
    except Exception:
        return {}


def _operacija(preset: dict | None, layer: str) -> dict | None:
    if not preset:
        return None
    op = preset.get(layer)
    return op if isinstance(op, dict) else None


def postavke_masine(job: dict) -> list[dict]:
    """Red po operaciji (ENGRAVE/CUT/SCORE) sa brzinom, snagom i brojem prolaza."""
    preset = preset_posla(job)
    verified = bool(preset.get("verified")) if preset else False
    redovi = []
    for layer, naziv in (("ENGRAVE", "Graviranje"), ("CUT", "Rezanje"), ("SCORE", "Zarezivanje")):
        op = _operacija(preset, layer)
        if op is None:
            continue
        redovi.append({
            "operacija": naziv,
            "sloj": layer,
            "brzina_mm_min": _broj(op.get("speed_mm_min"), "mm/min", 0),
            "snaga": _broj(op.get("power_pct"), "%", 0),
            "prolaza": str(op.get("passes") or NEMA),
            "interval_mm": _broj(op.get("line_interval_mm"), "mm", 3)
            if op.get("line_interval_mm") is not None else "-",
            "provjereno": verified,
        })
    return redovi


def build(job: dict) -> dict:
    """Radni nalog kao struktura. Prikaz je odvojen (web sablon ili tekst)."""
    summary = job.get("summary") or {}
    document = summary.get("document_mm") or job.get("document_mm") or {}
    raster = job.get("raster") or {}
    params = job.get("params") or {}
    preset = preset_posla(job)

    interval = raster.get("interval_mm", params.get("interval_mm"))
    dpi = None
    if interval:
        try:
            dpi = 25.4 / float(interval)
        except (TypeError, ValueError, ZeroDivisionError):
            dpi = None

    cinjenice = [
        ("Posao", job.get("job_id") or NEMA),
        ("Izvor", params.get("fajl") or (job.get("sources") or [NEMA])[0]),
        ("Materijal", preset.get("name") or params.get("materijal") or NEMA),
        ("Debljina materijala", _broj(preset.get("thickness_mm"), "mm")),
        ("Velicina na materijalu",
         f"{_broj(document.get('width'), 'mm')} x {_broj(document.get('height'), 'mm')}"),
        ("Rezolucija", f"{raster.get('pixels')[0]} x {raster.get('pixels')[1]} px"
         if raster.get("pixels") else NEMA),
        ("Razmak linija", _broj(interval, "mm", 3)),
        ("DPI (25.4 / razmak)", _broj(dpi, "", 0)),
        ("Procjena trajanja", _vrijeme((job.get("estimates") or {}).get("vrijeme_s"))),
    ]

    koraci = [
        {"naslov": "Materijal",
         "tekst": f"Stavi {preset.get('name') or params.get('materijal') or NEMA} na sto. "
                  f"Povrsina mora biti cista i ravna; iskrivljena ploca daje neujednacenu gravuru."},
        {"naslov": "Postavljanje",
         "tekst": f"Komad mora primiti {_broj(document.get('width'), 'mm')} x "
                  f"{_broj(document.get('height'), 'mm')} plus rub koji drzis rukom ili stezaljkom. "
                  f"Stezaljke ne smiju biti u polju gravure."},
        {"naslov": "Fokus",
         "tekst": "Namjesti fokus na gornju povrsinu materijala. Fokus se mjeri za svaku "
                  "debljinu posebno; pogresan fokus mijenja i sirinu reza i ton gravure."},
        {"naslov": "Postavke",
         "tekst": "Prepisi brzinu, snagu i broj prolaza iz tabele ispod. Razmak linija postavi na "
                  f"{_broj(interval, 'mm', 3)} (to je DPI {_broj(dpi, '', 0)})."},
        {"naslov": "Prazan hod",
         "tekst": "Prvo Home, pa Frame. Frame vozi okvir posla sa ugasenim laserom (S0/M5). "
                  "Gledaj da glava ne izadje van ploce i da ne udari u stezaljku. "
                  "Ako okvir ne lezi gdje treba, pomjeri materijal, ne fajl."},
        {"naslov": "Prije pokretanja - nadzor sa mjesta",
         "tekst": "Nadzor ne znaci stajanje uz masinu. Prije starta namjesti tako da sve "
                  "mozes iz stolice: kamera uperena u sto i otvorena u pregledu, stranica "
                  "/machine otvorena jer su Pause i Stop na njoj, aparat za gasenje nadohvat "
                  "ruke. Sto ocisti od otpadaka i ostataka materijala - oni se pale, a ne komad "
                  "koji gravira."},
        {"naslov": "Rad",
         "tekst": "Tek kad je okvir tacan, pokreni. Ukljuci odvod dima i air assist ako ga imas. "
                  "Ostani u prostoriji i gledaj kameru; Stop na stranici /machine gasi laser "
                  "(soft-reset). Ne izlazi dok masina radi, bez obzira na procijenjeno trajanje."},
        {"naslov": "Provjera",
         "tekst": "Nakon zavrsetka provjeri ton i dubinu. Ako je presvijetlo smanji brzinu ili "
                  "podigni snagu; ako je pregorjelo obrnuto. Izmjerenu vrijednost upisi u preset "
                  "da sljedeci komad ne pogadjas."},
    ]

    upozorenja = list(job.get("warnings") or [])
    if preset and not preset.get("verified", False):
        upozorenja.append(
            f"Brzina i snaga za '{preset.get('name') or preset.get('key')}' su POLAZNE "
            "vrijednosti, nisu izmjerene na tvojoj masini. Uradi test-grid prije serije."
        )
    if job.get("blocking"):
        upozorenja.extend(job["blocking"])

    return {
        "job_id": job.get("job_id") or NEMA,
        "naslov": f"Radni nalog - {job.get('template') or job.get('type') or 'posao'}",
        "cinjenice": cinjenice,
        "postavke": postavke_masine(job),
        "koraci": koraci,
        "upozorenja": upozorenja,
        "slika": (job.get("raster") or {}).get("png"),
    }


def kao_tekst(nalog: dict) -> str:
    """Isti nalog kao obican tekst, za stampu ili slanje operateru."""
    linije = [nalog["naslov"], "=" * len(nalog["naslov"]), ""]
    for naziv, vrijednost in nalog["cinjenice"]:
        linije.append(f"{naziv:<24} {vrijednost}")
    linije.append("")

    if nalog["postavke"]:
        linije.append("POSTAVKE MASINE")
        linije.append(f"{'Operacija':<14}{'Brzina':<14}{'Snaga':<8}{'Prolaza':<9}{'Razmak':<10}")
        for red in nalog["postavke"]:
            oznaka = "" if red["provjereno"] else "  (nije provjereno)"
            linije.append(
                f"{red['operacija']:<14}{red['brzina_mm_min']:<14}{red['snaga']:<8}"
                f"{red['prolaza']:<9}{red['interval_mm']:<10}{oznaka}"
            )
        linije.append("")

    linije.append("KORACI")
    for index, korak in enumerate(nalog["koraci"], start=1):
        linije.append(f"{index}. {korak['naslov']}")
        linije.append(f"   {korak['tekst']}")
    linije.append("")

    if nalog["upozorenja"]:
        linije.append("UPOZORENJA")
        for w in nalog["upozorenja"]:
            linije.append(f"! {w}")
        linije.append("")
    return "\n".join(linije)

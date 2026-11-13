# FabAgent

Deterministički generator proizvodnih fajlova: laser SVG (ArgoLaser MK20) i FDM STL/3MF.
Zavisnosti: `numpy`, `trimesh` (samo za nezavisnu provjeru mesha). Bez mreže u runtime-u.

```bash
python -m venv .venv && .venv/Scripts/python -m pip install -e .[test]
fabagent laser list
fabagent laser info privesak
fabagent laser build privesak --params oblik=krug sirina=40 visina=40 materijal=sperploca_3mm tekst=DOBOJ visina_teksta=6 rupa_promjer=5 rupa_odmak=2.5 razmak_slova=1 tekst_pomak_y=0 --out out/privesak.svg --preview
fabagent print build nosac_l --params duzina_a=40 duzina_b=40 sirina=30 debljina=4 rupa_promjer=4.5 rupa_razmak=16 rupa_od_kraja=10 filet_radijus=4 materijal=PETG ispuna_posto=40 visina_sloja=0.2 --out out/nosac.stl
fabagent validate out/nosac.3mf
python -m pytest
```

- Opcioni parametri se popunjavaju **samo** uz `--use-defaults` (i to se loguje).
- `-v` loguje svaku odluku; sve odluke su i u `<izlaz>.json` (`decision_log`).
- Izlazni kodovi: 0 OK, 2 CLI upotreba, 10 MissingParam, 11 OutOfRange, 12 InvalidGeometry,
  13 InvalidParam, 14 ValidationFailed, 15 UnknownTemplate, 16 OutputError, 1 neočekivano.

## Prije produkcije provjeriti
- `core/config.py`: `LASER_WORK_AREA_MM` i `PRINTER_BUILD_VOLUME_MM` (stavljene polazne vrijednosti).
- `laser/presets.py`: svi preseti su `verified=False` — polazne vrijednosti za 20 W diodu.
  Uradi test-grid u LightBurn-u, upiši izmjerene brzine i postavi `verified=True`.

## Ograničenja
- Tekst je blok-font 5×7 (velika slova A–Z, Č Ć Š Ž Đ, cifre, osnovni znakovi); mala slova su greška.
- 3D geometrija se gradi lijepljenjem prizmi/rotacionih tijela (nema opšteg boolean-a);
  novi oblici traže novi šablon.
- Code 128 je provjeren strukturno (tabela, paritet, kontrolna suma); QR je dodatno dekodiran OpenCV-om tokom razvoja.

## Web interfejs (lokalno, offline)
```bash
.venv/Scripts/python -m pip install -e .[web]
.venv/Scripts/python -m fabagent.web          # ili dvoklik na Pokreni_FabAgent.bat
```
Otvara `http://127.0.0.1:8000/` (server sluša samo na 127.0.0.1). Galerija šablona → forma
(generisana iz config.py, imena u `web/labels.py`) → **Pregledaj** → **Odobri i pošalji u produkciju**
ili **Doradi**. Pregledi idu u `jobs/<id>/`, odobreni buildovi u
`production/YYYY-MM-DD_HHMM_<sablon>_<slug>/` sa `job.json`. Lokacija se mijenja varijablom
`FABAGENT_DATA_DIR`. `GET /health` vraća `{"status": "ok"}`.

## Upload SVG i automatski popravak (`core/svg_fixer.py`)
Web: dugme **Uploaduj SVG** na početnoj (`/upload`). Odbija raster (`<image>`), fajlove > 10 MB i
DOCTYPE/ENTITY. Spaja otvorene linije (< 0.1 mm), uklanja duple (< 0.05 mm), tekst pretvara u
putanje blok-fontom, postavlja stroke 0.1 mm i slojeve (popunjeno → ENGRAVE, plavo → SCORE, ostalo → CUT).
Što se ne može popraviti bez pretpostavke ide u listu blokirajućih grešaka i odobravanje je onemogućeno.
Koristi `svgpathtools` za Bezier krivulje i lukove.

## Burence za vino (`laser/templates/burence.py`)
Daske sa čepovima i rupama za prstenove (1/3 i 2/3), prednja/zadnja strana sa prorezima i rupom za
bocu, opciono baza + sedla + fioka. Dijelovi se raspoređuju (`laser/nesting.py`, MaxRects, razmak 3 mm)
na ploče veličine `LASER_WORK_AREA_MM`; više ploča → `naziv_ploca1.svg`, `naziv_ploca2.svg`, ...
```bash
fabagent laser build burence --params promjer_mm=200 visina_mm=150 broj_dasaka=12 debljina_mm=3 promjer_boce_mm=75 ima_fioku=false materijal=sperploca_3mm --out out/burence.svg --preview
```

## Upload drugih formata (`core/converter.py`)
```bash
.venv/Scripts/python -m pip install "ezdxf>=1.3" "pymupdf>=1.24,<2" "potracer>=0.0.4"
```
(ili `pip install -e .[convert]`). `pymupdf` je ograničen na 1.x jer 2.x povlači ML pakete (onnxruntime).
- **DXF** (ezdxf): LINE, CIRCLE, ARC, LWPOLYLINE (bulge), POLYLINE, ELLIPSE, blokovi (INSERT). Jedinice iz
  `$INSUNITS`; ako ih nema, bira se u formi (mm/cm/in). TEXT, HATCH, SPLINE... → blokirajuća greška.
- **PDF** (pymupdf): vektorski crteži jedine stranice sa crtežima. Samo slika → „PDF ne sadrži vektore, samo sliku“.
  Tekst (fontovi) ili više stranica → blokirajuća greška.
- **PNG/JPG** (potrace): prag 1–255 i ciljna širina u mm su obavezni (DPI se ne pogađa); oblici idu u ENGRAVE.
Svaki konvertovani SVG obavezno prolazi kroz `svg_fixer.py`.

## Interaktivni pregled
SVG je direktno u stranici; `svg-pan-zoom` 3.6.1 (lokalno u `web/static/`) daje zoom točkićem i pomjeranje
povlačenjem; „Layer Manager“ pali/gasi CUT/ENGRAVE/SCORE.

## Napredna obrada slika (`core/image_trace.py`)
```bash
.venv/Scripts/python -m pip install opencv-python-headless
```
Upload PNG/JPG (ili PDF koji sadrži samo sliku): threshold, cutoff, invert, uklanjanje šuma (0–10),
RDP pojednostavljenje (0–5 px), fiksni/adaptivni prag, motor potrace / OpenCV konture / vtracer.
Opcione obrade se primjenjuju samo ako su zadane. Na rezultatu: „Prikaži rezultat / original / obrađena bitmapa“
i „Podesi i ponovo obradi“ (isti fajl, nove postavke).

**vtracer**: na Pythonu 3.14 + Windows (vtracer 0.6.15) ruši proces (access violation), pa se pokreće samo
izolovano u zasebnom procesu i automatski isključuje kada ne radi (`pip install -e .[vtracer]` - eksperimentalno).

## Kamera i kalibracija (`core/camera.py`, stranica `/camera`)
```bash
set FABAGENT_CAMERA=0                       # USB kamera #0 (default)
set FABAGENT_CAMERA=C:\put\do\slike.png     # proba bez kamere
.venv/Scripts/python -m fabagent.web
```
Na `/camera`: uživo slika (MJPEG), klik na 4 ugla radne površine redom (X0Y0 gore lijevo → gore desno →
dolje desno → dolje lijevo) → `cv2.getPerspectiveTransform`; kalibracija se čuva u `kamera/kalibracija.json`.
U pregledu lasera: „Prikaži kameru / Osvježi kameru / Sakrij kameru“ + pozicija posla na stolu (X, Y mm).

## Upravljanje laserom (GRBL, Web Serial) – `/machine`
Bez dodatnih paketa: browser (Chrome/Edge) razgovara direktno sa COM portom lasera.
- **Poveži se sa laserom** → izbor COM porta (115200 baud), status: Otkopčano / Povezano / U toku / Pauza / Alarm.
- **Home** (`$H`), **Otključaj** (`$X`), **Frame** (okvir posla, samo S0/M5 – laser ugašen), **Start** (samo
  odobren job + sigurnosna potvrda), **Pause/Nastavi** (`!`/`~`), **Stop** (`!` + soft-reset 0x18 → laser ugašen).
- G-code (`core/gcode.py`): G21/G90, M4 dinamička snaga, G0 bez lasera, G1 sa S/F iz Layer Managera, kraj M5.
  Y se okreće (mašina dolje-lijevo). Konture: unutrašnje prije vanjskih; Engrave: dvosmjerne linije sa preletom.
- Konstante mašine (`$30` S max, ishodište, brzine) su u `core/config.py` – **PROVJERITI** za svoju mašinu.

## Editor rasporeda – `/editor/<job>`
fabric.js 5.3.0 (lokalno): sto 400×400 mm, ravnala, pomjeranje/rotacija (skaliranje zaključano dok ga ne
odključaš), X/Y u koordinatama mašine. „Sačuvaj raspored“ pravi novi job koji ponovo prolazi sve provjere.
Layer Manager (mod, brzina, snaga, prolazi, interval) se čuva u `job.json` (i u produkcijskom `job.json`).

## Slika: raster gravura i 3D litofanija
- Image Mode: **Threshold** (vektor), **Dither** (Floyd–Steinberg / Jarvis / Stucki), **Grayscale** (snaga po pikselu),
  **Vectorize** (obrisi → SCORE), **Negative**. Raster = PNG mapa snage + G-code red po red.
- **3D**: litofanija (tamnije = deblje) ili reljef (svjetlije = više) → watertight STL/3MF (`core/image_to_3d.py`).

## API za skripte / asistenta
```bash
curl -X POST http://127.0.0.1:8000/api/build -H "Content-Type: application/json" \
  -d '{"type":"laser","template":"privesak","params":{"oblik":"krug","sirina":40,"visina":40,"materijal":"sperploca_3mm","tekst":"DOBOJ","visina_teksta":6,"rupa_promjer":5,"rupa_odmak":2.5,"razmak_slova":1,"tekst_pomak_y":0}}'
```
Vraća `job_id`, putanje fajlova i upozorenja (greške: isti `MissingParam`/`OutOfRange`... kao CLI).
Ostalo: `GET /api/templates`, `GET /api/jobs/<id>`, `POST /api/jobs/<id>/approve`, `GET|PUT /api/jobs/<id>/layers`,
`POST /api/jobs/<id>/gcode` (`{"x":..,"y":..,"sheet":1,"frame":false}`), `POST /api/jobs/<id>/layout`.
POST zahtjevi moraju imati `Content-Type: application/json` (strane web stranice ih ne mogu poslati).

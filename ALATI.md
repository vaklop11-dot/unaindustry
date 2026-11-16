# ALATI — Faza 0, provjera prije integracije

Datum: 16.11.2026. Dopunjeno nakon izbacivanja copyleft paketa.
Sve ispod je pročitano iz fajla na ovom računaru ili sa zvanične stranice alata.
Što nije provjereno, stoji pod **NEMAM POTVRDU**.

---

## 1. Licence — stanje poslije 16.11.2026.

Svi paketi koje proizvod stvarno uvozi su **permisivni**. FabAgent se može
prodavati zatvoren.

| Paket | Verzija | Licenca | Smeta prodaji? |
|---|---|---|---|
| numpy | 2.5.4 | BSD-3 | ne |
| trimesh | 5.2.0 | MIT | ne |
| svgpathtools | 1.8.0 | MIT | ne |
| ezdxf | 1.4.5 | **MIT** (pročitano iz `ezdxf-1.4.5.dist-info/licenses/LICENSE`) | ne |
| opencv-python-headless | 5.0.0 | Apache-2.0 | ne |
| fastapi / uvicorn / jinja2 | — | MIT / BSD / BSD | ne |

### Izbačeno 16.11.2026.

| Paket | Bila licenca | Čime je zamijenjen |
|---|---|---|
| **pymupdf** | AGPL-3.0 ili plaćena Artifex licenca | `fabagent/core/pdf_citac.py` — vlastiti čitač PDF-a |
| **potracer** | GPL v2+ | `fabagent/core/trasiranje.py` — vlastito trasiranje |

`pymupdf` ostaje samo u `test` dodatku, i to **isključivo da testovi mogu
napraviti PDF za provjeru**. Proizvod ga ne uvozi nigdje — provjereno sa
`grep -rn "import pymupdf"` nad `fabagent/`, van testova nema nijednog pogotka.
Ono što se isporučuje korisniku ne sadrži AGPL kod.

---

## 2. Pet alata iz zadatka

| Alat | URL | Verzija | Licenca | Upotreba | Python 3.14 / Windows |
|---|---|---|---|---|---|
| **ezdxf** | github.com/mozman/ezdxf | 1.4.5, instaliran | **MIT** | biblioteka | **Da** — METADATA navodi `Python :: 3.14` |
| **Boxes.py** | github.com/florianfesti/boxes | nije instaliran | **GPL v3+** | biblioteka / Inkscape dodatak | NEMAM POTVRDU |
| **kerfer** | NEMAM POTVRDU | — | — | — | — |
| **svg-mcp** | NEMAM POTVRDU | — | — | — | — |
| **svg-canvas-mcp** | NEMAM POTVRDU | — | — | — | — |

Za tri alata bez URL-a: pod tim imenima postoji više različitih projekata raznih
autora. Bez linka od Nermina se ne pogađa.

---

## 3. Faza 4 (Boxes.py) vjerovatno nije potrebna

Prstasti spoj sa kompenzacijom kerfa **već postoji** u
`Desktop\image-to-lightburn-svg\src\geometry.py` (7 009 B): materijal duži za
kerf/2 sa svake strane, urez kraći za kerf/2, kraj na ivici ploče se ne mijenja.
Uz njega `src\box_generator.py` i `tests\test_box_fit.py`, koji vadi stvarne
segmente iz SVG-a i simulira rez. Ukupno 51 test.

Spajanje tog koda u FabAgent nema licencni problem. Boxes.py bi donio GPL v3+ i
tuđu geometriju koju treba iznova mjeriti na mašini.

---

## 4. Vlastiti kod koji je zamijenio tuđi

### `core/trasiranje.py` — bitmapa u konture

1. **Granica između piksela** — za svaki puni piksel gledaju se četiri susjeda i
   upisuje usmjerena ivica tako da crtež ostaje lijevo od hoda. Rupe time dobiju
   suprotan smjer obilaska i SVG nonzero pravilo ih samo napravi.
2. **Spajanje u petlje** — na tjemenu gdje se dva piksela dodiruju uglom bira se
   nastavak koji ih drži kao jedan komad.
3. **Douglas-Peucker sa stekom** — slika od 1,5 Mpx daje konturu od desetina
   hiljada tačaka; rekurzija bi pukla.

12 testova. Krug 204 → 16 tačaka, površina zadržana 99,94 %.

### `core/pdf_citac.py` — čitanje PDF-a

Čita: MediaBox, putanje (`m l c v y re h`), transformacije (`cm`, `q/Q`), boje
popune i linije (`g rg k G RG K`), ugrađene slike (DCTDecode, FlateDecode, 1/2/4/8
bita, ICCBased prostori boja), objektne tokove (PDF 1.5+).
Prijavljuje i staje: tekst bez fonta, šifrovan PDF, nepodržan filter.
Struktura se ne čita preko xref tabele nego pretragom za `N G obj` — tako se
pročita i fajl sa pokvarenom tabelom.

23 testa, svi na PDF-ovima napravljenim rukom, bajt po bajt.

---

## 5. NEMAM POTVRDU

1. URL, verzija i licenca za `kerfer`, `svg-mcp`, `svg-canvas-mcp` — **treba link**.
2. Da li Boxes.py radi na Python 3.14 / Windows.
3. Koji MCP klijent bi pozivao ta dva MCP servera.
4. Kako vlastito trasiranje radi na fotografiji od 1,5 Mpx — brzina nije mjerena.
5. Kako vlastiti čitač čita PDF izvezen iz CorelDRAW-a, Illustratora i Inkscapea
   — testiran je na PDF-ovima koje smo sami napravili i na onima iz pymupdf-a.

---

```
FAZA 0 — gotovo
Izmijenjeni fajlovi: pyproject.toml, core/converter.py, core/image_trace.py,
                     core/config.py; novi: core/pdf_citac.py, core/trasiranje.py,
                     tests/test_pdf_citac.py, tests/test_trasiranje.py
Testovi: 606 passed, 1 skipped
NEMAM POTVRDU: tri URL-a; Boxes.py na 3.14; brzina na velikoj slici;
               PDF iz Corela/Illustratora/Inkscapea
Nermin treba uraditi: dati tri linka; otvoriti svoj PDF i svoju sliku kroz /upload
```

"""Trasiranje bitmape u zatvorene konture - vlastiti kod, bez tudjih biblioteka.

Zasto postoji: dosadasnji motor je bio `potracer` (GPL v2+). GPL trazi da se
izvorni kod isporuci korisniku programa, pa se FabAgent sa njim unutra ne moze
prodavati zatvoren. Ovaj modul radi isti posao koristeci samo numpy (BSD) i
standardnu biblioteku. Nijedan red nije prepisan iz potrace-a ni iz bilo kojeg
drugog projekta.

Kako radi, u tri koraka:

1. GRANICA IZMEDJU PIKSELA
   Piksel (r, c) je kvadrat [c, c+1] x [r, r+1]. Gdje crtez dodiruje pozadinu,
   tu lezi granica. Za svaki puni piksel gledaju se cetiri susjeda i upisuje se
   usmjerena ivica tako da crtez UVIJEK ostaje sa lijeve strane hoda. Ta jedna
   odluka rjesava i rupe: vanjska kontura i rupa dobiju suprotan smjer obilaska,
   pa SVG nonzero pravilo samo napravi rupu, bez ijedne dodatne provjere.

2. SPAJANJE U PETLJE
   Ivice se nizu kraj na pocetak dok se petlja ne zatvori. Na mjestu gdje se dva
   piksela dodiruju samo tjemenom postoje dva nastavka; bira se uvijek isti
   (najostriji desni skret), sto znaci da dijagonalni dodir ostaje JEDAN komad.
   Za rez je to ispravno: dijagonalni most je materijal koji drzi.

3. POJEDNOSTAVLJENJE
   Douglas-Peucker, pisan ovdje, sa stekom umjesto rekurzije - slika od 1.5 Mpx
   zna dati konturu od desetina hiljada tacaka i rekurzija bi pukla.

Ulaz je bool matrica (True = crtez). Izlaz su poligoni u pikselima, pa onaj ko
ih koristi sam odlucuje o mjerilu i mm.
"""

from __future__ import annotations

import math

import numpy as np

# Cetiri usmjerene ivice punog piksela (r, c), u koordinatama ugla:
#   x = kolona, y = red, y raste nadolje.
# Smjer je biran tako da je puni piksel lijevo od hoda.
#   gore  desno: (c+1, r)   -> (c, r)
#   desno gore:  (c+1, r+1) -> (c+1, r)
#   dolje desno: (c, r+1)   -> (c+1, r+1)
#   lijevo dolje:(c, r)     -> (c, r+1)


def granicne_ivice(fg: np.ndarray) -> dict[tuple[int, int], list[tuple[int, int]]]:
    """Usmjerene ivice granice. Kljuc je pocetno tjeme, vrijednost su krajevi.

    Susjed izvan slike racuna se kao pozadina - inace bi crtez koji dodiruje
    ivicu slike ostao bez granice i nestao iz rezultata.
    """
    if fg.ndim != 2:
        raise ValueError("trasiranje: ocekujem 2D bool matricu")
    m = fg.astype(bool)
    h, w = m.shape
    if h == 0 or w == 0:
        return {}

    gore = np.zeros_like(m)
    gore[1:, :] = m[:-1, :]
    dolje = np.zeros_like(m)
    dolje[:-1, :] = m[1:, :]
    lijevo = np.zeros_like(m)
    lijevo[:, 1:] = m[:, :-1]
    desno = np.zeros_like(m)
    desno[:, :-1] = m[:, 1:]

    ivice: dict[tuple[int, int], list[tuple[int, int]]] = {}

    def dodaj(rr, cc, a, b):
        for r, c in zip(rr.tolist(), cc.tolist()):
            p = a(r, c)
            ivice.setdefault(p, []).append(b(r, c))

    r1, c1 = np.nonzero(m & ~gore)
    dodaj(r1, c1, lambda r, c: (c + 1, r), lambda r, c: (c, r))
    r2, c2 = np.nonzero(m & ~desno)
    dodaj(r2, c2, lambda r, c: (c + 1, r + 1), lambda r, c: (c + 1, r))
    r3, c3 = np.nonzero(m & ~dolje)
    dodaj(r3, c3, lambda r, c: (c, r + 1), lambda r, c: (c + 1, r + 1))
    r4, c4 = np.nonzero(m & ~lijevo)
    dodaj(r4, c4, lambda r, c: (c, r), lambda r, c: (c, r + 1))
    return ivice


def _izbor(ulaz: tuple[int, int], kandidati: list[tuple[int, int]],
           tjeme: tuple[int, int]) -> tuple[int, int]:
    """Koji nastavak uzeti kad ih ima vise (dijagonalni dodir dva piksela).

    Na tjemenu gdje se dva piksela dodiruju samo uglom postoje dva nastavka.
    Uzima se onaj sa najvecim uglom skreta, sto prelazi na dijagonalnog susjeda
    i drzi ih kao JEDAN komad (8-povezanost). Drugi izbor bi ih razdvojio na dva
    - a na materijalu taj dijagonalni most postoji i drzi.

    Provjereno testom `test_dijagonala_ostaje_jedan_komad`.
    """
    if len(kandidati) == 1:
        return kandidati[0]
    dx0, dy0 = ulaz

    def ugao(k):
        dx, dy = k[0] - tjeme[0], k[1] - tjeme[1]
        # Skalarni i vektorski proizvod daju ugao skreta u [-pi, pi].
        # atan2(krst, skalar) daje ugao skreta; najveci ugao je onaj koji
        # prelazi na dijagonalnog susjeda umjesto da se vrati oko istog piksela.
        krst = dx0 * dy - dy0 * dx
        skalar = dx0 * dx + dy0 * dy
        return math.atan2(krst, skalar)

    return max(kandidati, key=ugao)


def petlje(fg: np.ndarray) -> list[np.ndarray]:
    """Sve zatvorene konture crteza, u pikselima. Rupe su ukljucene (obrnut smjer)."""
    ivice = granicne_ivice(fg)
    preostalo = {k: list(v) for k, v in ivice.items()}
    rezultat: list[np.ndarray] = []

    for pocetak in sorted(ivice):
        while preostalo.get(pocetak):
            tacke = [pocetak]
            tjeme = pocetak
            ulaz = (0, 0)
            while True:
                nastavci = preostalo.get(tjeme)
                if not nastavci:
                    break                       # ne bi smjelo; granica je uvijek zatvorena
                sljedeci = _izbor(ulaz, nastavci, tjeme) if ulaz != (0, 0) else nastavci[0]
                nastavci.remove(sljedeci)
                if not nastavci:
                    preostalo.pop(tjeme, None)
                ulaz = (sljedeci[0] - tjeme[0], sljedeci[1] - tjeme[1])
                tjeme = sljedeci
                if tjeme == tacke[0]:
                    break
                tacke.append(tjeme)
            if len(tacke) >= 3:
                rezultat.append(np.array(tacke, dtype=np.float64))
    return rezultat


def povrsina(poly: np.ndarray) -> float:
    """Povrsina sa predznakom (Gaussova formula). Predznak nosi smjer obilaska."""
    if len(poly) < 3:
        return 0.0
    x, y = poly[:, 0], poly[:, 1]
    return 0.5 * float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))


def _bez_kolinearnih(poly: np.ndarray, eps: float = 1e-9) -> np.ndarray:
    """Izbaci tacke koje leze na pravoj izmedju susjeda.

    Granica po pikselima ide u stepenicama, pa svaka ravna ivica duza od jednog
    piksela nosi suvisna tjemena. Bez ovoga i najobicniji kvadrat ima na desetine
    tacaka umjesto cetiri.
    """
    n = len(poly)
    if n < 3:
        return poly
    p, s = np.roll(poly, 1, axis=0), np.roll(poly, -1, axis=0)
    a, b = poly - p, s - poly
    krst = a[:, 0] * b[:, 1] - a[:, 1] * b[:, 0]
    drzi = np.abs(krst) > eps
    return poly[drzi] if drzi.sum() >= 3 else poly


def uprosti_rdp(poly: np.ndarray, eps: float) -> np.ndarray:
    """Douglas-Peucker za ZATVORENU konturu, bez rekurzije.

    Zatvorena kontura nema prirodne krajeve, pa se uzimaju dvije najudaljenije
    tacke kao fiksni krajevi i svaki luk se uproscava posebno.
    """
    if eps <= 0 or len(poly) < 4:
        return poly
    n = len(poly)
    d2 = ((poly - poly[0]) ** 2).sum(axis=1)
    i1 = int(np.argmax(d2))
    if i1 == 0:
        return poly
    cuvaj = np.zeros(n, dtype=bool)
    cuvaj[0] = cuvaj[i1] = True

    for a, b in ((0, i1), (i1, n)):
        stek = [(a, b if b <= n else n)]
        while stek:
            poc, kraj = stek.pop()
            if kraj - poc < 2:
                continue
            k = kraj % n
            p0, p1 = poly[poc], poly[k]
            seg = p1 - p0
            duz = float(np.hypot(*seg))
            dio = poly[poc + 1:kraj]
            if len(dio) == 0:
                continue
            if duz < 1e-12:
                odst = np.hypot(*(dio - p0).T)
            else:
                v = dio - p0
                krst = seg[0] * v[:, 1] - seg[1] * v[:, 0]
                odst = np.abs(krst) / duz
            j = int(np.argmax(odst))
            if float(odst[j]) > eps:
                idx = poc + 1 + j
                cuvaj[idx] = True
                stek.append((poc, idx))
                stek.append((idx, kraj))
    out = poly[cuvaj]
    return out if len(out) >= 3 else poly


def trasiraj(fg: np.ndarray, uprosti: float = 0.0, min_povrsina: float = 0.0) -> list[np.ndarray]:
    """Bitmapa -> poligoni u pikselima.

    uprosti      - RDP tolerancija u pikselima; 0 znaci bez uproscavanja
    min_povrsina - konture manje povrsine se odbacuju (mrlje, prasina na skenu)
    """
    izlaz = []
    for p in petlje(fg):
        q = _bez_kolinearnih(p)
        if uprosti > 0:
            q = uprosti_rdp(q, uprosti)
        if len(q) >= 3 and abs(povrsina(q)) >= min_povrsina:
            izlaz.append(q)
    return izlaz


def _fmt(v: float) -> str:
    return f"{v:.3f}".rstrip("0").rstrip(".") or "0"


def u_putanju(poly: np.ndarray) -> str:
    """Poligon -> SVG `d`, zatvoren sa Z."""
    return "M " + " L ".join(f"{_fmt(x)} {_fmt(y)}" for x, y in poly) + " Z"


def u_putanje(polys) -> list[str]:
    """Vise poligona -> lista SVG `d` nizova."""
    return ["M " + " L ".join(f"{_fmt(x)} {_fmt(y)}" for x, y in p) + " Z" for p in polys]

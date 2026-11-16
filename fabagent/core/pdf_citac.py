"""Citanje PDF-a - vlastiti kod, bez tudjih biblioteka.

Zasto postoji: dosadasnji citac je bio `pymupdf`, koji ide pod AGPL-3.0 ili pod
placenu Artifex licencu. AGPL trazi da korisnik programa dobije izvorni kod, pa
se FabAgent sa njim unutra ne moze prodavati zatvoren. Ovaj modul cita ono sto
laseru treba, koristeci samo standardnu biblioteku (`zlib`, `re`) i numpy.

Sta se cita, a sta ne:
  DA   velicina stranice (MediaBox), putanje (m, l, c, v, y, re, h), transformacije
       (cm, q/Q), boje popune i linije, ugradjene slike (DCTDecode i FlateDecode)
  NE   tekst - font se ne moze pretvoriti u krivulje bez samog fonta, pa se tekst
       prijavljuje kao prepreka, isto kao i ranije
  NE   zasticen (sifrovan) PDF - prijavljuje se i staje

Struktura PDF-a se ne cita preko xref tabele nego se fajl pretrazi za sve
"N G obj ... endobj" i za objektne tokove (/Type /ObjStm). Razlog: xref tabela u
izvezenim fajlovima zna biti pokvarena, a ovako se procita i takav fajl.
"""

from __future__ import annotations

import re
import zlib

import numpy as np


class PdfGreska(Exception):
    """Sve sto sprjecava citanje PDF-a. Poziva se iz convertera i pretvara u poruku."""


# ------------------------------------------------------------- leksika
_BROJ = re.compile(rb"[+-]?(?:\d+\.?\d*|\.\d+)")
_IME = re.compile(rb"/([^\s/\[\]<>(){}%]*)")
_OBJ = re.compile(rb"(\d+)\s+(\d+)\s+obj\b")


def _preskoci(b: bytes, i: int) -> int:
    """Preskoci razmake i komentare."""
    n = len(b)
    while i < n:
        c = b[i:i + 1]
        if c in b" \t\r\n\f\x00":
            i += 1
        elif c == b"%":
            while i < n and b[i:i + 1] not in b"\r\n":
                i += 1
        else:
            break
    return i


def _ime_dekod(s: bytes) -> str:
    """#20 u imenu je heks zapis znaka."""
    out = bytearray()
    i = 0
    while i < len(s):
        if s[i:i + 1] == b"#" and i + 2 < len(s):
            try:
                out.append(int(s[i + 1:i + 3], 16))
                i += 3
                continue
            except ValueError:
                pass
        out.append(s[i])
        i += 1
    return out.decode("latin-1")


class Ref:
    """Pokazivac na drugi objekat: '12 0 R'."""

    __slots__ = ("broj",)

    def __init__(self, broj: int):
        self.broj = broj

    def __repr__(self):
        return f"Ref({self.broj})"


def _citaj_objekat(b: bytes, i: int):
    """Procitaj jedan PDF objekat od pozicije i. Vrati (vrijednost, nova_pozicija)."""
    i = _preskoci(b, i)
    if i >= len(b):
        return None, i
    c = b[i:i + 1]

    if c == b"<" and b[i + 1:i + 2] == b"<":                  # rjecnik
        i += 2
        d = {}
        while True:
            i = _preskoci(b, i)
            if b[i:i + 2] == b">>":
                return d, i + 2
            if b[i:i + 1] != b"/":
                _, i = _citaj_objekat(b, i)                    # neocekivano; preskoci
                if i >= len(b):
                    return d, i
                continue
            m = _IME.match(b, i)
            kljuc = _ime_dekod(m.group(1))
            vr, i = _citaj_objekat(b, m.end())
            d[kljuc] = vr

    if c == b"[":                                              # niz
        i += 1
        niz = []
        while True:
            i = _preskoci(b, i)
            if b[i:i + 1] == b"]":
                return niz, i + 1
            if i >= len(b):
                return niz, i
            vr, i = _citaj_objekat(b, i)
            niz.append(vr)

    if c == b"/":                                              # ime
        m = _IME.match(b, i)
        return "/" + _ime_dekod(m.group(1)), m.end()

    if c == b"(":                                              # niz znakova
        i += 1
        dubina, out = 1, bytearray()
        while i < len(b):
            z = b[i:i + 1]
            if z == b"\\":
                out += b[i:i + 2]
                i += 2
                continue
            if z == b"(":
                dubina += 1
            elif z == b")":
                dubina -= 1
                if dubina == 0:
                    return bytes(out), i + 1
            out += z
            i += 1
        return bytes(out), i

    if c == b"<":                                              # heks niz
        kraj = b.find(b">", i)
        if kraj < 0:
            return b"", len(b)
        return bytes.fromhex(re.sub(rb"\s", b"", b[i + 1:kraj]).decode("latin-1", "ignore")
                             .ljust(0, "0") or "00"), kraj + 1

    if b[i:i + 4] == b"true":
        return True, i + 4
    if b[i:i + 5] == b"false":
        return False, i + 5
    if b[i:i + 4] == b"null":
        return None, i + 4

    m = _BROJ.match(b, i)
    if m:
        # Moze biti pocetak reference "12 0 R"
        ref = re.match(rb"(\d+)\s+(\d+)\s+R\b", b[i:i + 32])
        if ref and b"." not in m.group(0):
            return Ref(int(ref.group(1))), i + ref.end()
        tekst = m.group(0)
        return (float(tekst) if b"." in tekst else int(tekst)), m.end()

    return None, i + 1                                          # nepoznat znak


# ------------------------------------------------------------- dokument
class Dokument:
    """Procitan PDF: mapa objekata i spisak stranica."""

    def __init__(self, objekti: dict, sirovo: bytes):
        self.objekti = objekti
        self.sirovo = sirovo

    def razrijesi(self, v):
        """Prati reference dok se ne dodje do prave vrijednosti."""
        vidjeno = set()
        while isinstance(v, Ref):
            if v.broj in vidjeno:
                return None                                     # kruzna referenca
            vidjeno.add(v.broj)
            v = self.objekti.get(v.broj, (None, None))[0]
        return v

    def tok(self, broj: int) -> bytes:
        """Raspakovan sadrzaj toka (stream) datog objekta."""
        rj, sirovi = self.objekti.get(broj, (None, None))
        if sirovi is None:
            return b""
        return _raspakuj(self, rj or {}, sirovi)

    @property
    def stranice(self) -> list[dict]:
        out = []
        for broj, (rj, _) in sorted(self.objekti.items()):
            if isinstance(rj, dict) and rj.get("Type") == "/Page":
                out.append(rj)
        return out


def _raspakuj(doc: "Dokument", rj: dict, sirovi: bytes) -> bytes:
    filtri = doc.razrijesi(rj.get("Filter"))
    if filtri is None:
        return sirovi
    if isinstance(filtri, str):
        filtri = [filtri]
    data = sirovi
    for f in filtri:
        if f in ("/FlateDecode", "/Fl"):
            try:
                data = zlib.decompress(data)
            except zlib.error:
                try:
                    data = zlib.decompressobj().decompress(data)   # pokvaren kraj
                except zlib.error as exc:
                    raise PdfGreska(f"tok se ne moze raspakovati: {exc}") from exc
        elif f in ("/DCTDecode", "/DCT", "/JPXDecode"):
            return data                                            # slika, ostaje kakva jeste
        else:
            raise PdfGreska(f"PDF koristi filter {f}, koji ovaj citac ne podrzava")
    predikt = doc.razrijesi(rj.get("DecodeParms")) or {}
    if isinstance(predikt, dict) and doc.razrijesi(predikt.get("Predictor", 1)) not in (None, 1):
        data = _bez_prediktora(data, doc, predikt)
    return data


def _bez_prediktora(data: bytes, doc: "Dokument", parms: dict) -> bytes:
    """PNG prediktor (Predictor >= 10) - svaki red nosi bajt sa nacinom predikcije."""
    kolona = int(doc.razrijesi(parms.get("Columns", 1)) or 1)
    boja = int(doc.razrijesi(parms.get("Colors", 1)) or 1)
    bpc = int(doc.razrijesi(parms.get("BitsPerComponent", 8)) or 8)
    bpp = max(1, (boja * bpc + 7) // 8)
    red_bajtova = (kolona * boja * bpc + 7) // 8
    out = bytearray()
    prethodni = bytearray(red_bajtova)
    i = 0
    while i + 1 + red_bajtova <= len(data):
        nacin = data[i]
        red = bytearray(data[i + 1:i + 1 + red_bajtova])
        i += 1 + red_bajtova
        for j in range(red_bajtova):
            a = red[j - bpp] if j >= bpp else 0
            b = prethodni[j]
            c = prethodni[j - bpp] if j >= bpp else 0
            if nacin == 1:
                red[j] = (red[j] + a) & 0xFF
            elif nacin == 2:
                red[j] = (red[j] + b) & 0xFF
            elif nacin == 3:
                red[j] = (red[j] + (a + b) // 2) & 0xFF
            elif nacin == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                red[j] = (red[j] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 0xFF
        out += red
        prethodni = red
    return bytes(out)


def ucitaj(data: bytes) -> Dokument:
    """Procitaj PDF iz bajtova. Baca PdfGresku na sve sto se ne moze procitati."""
    if not data.lstrip()[:5].startswith(b"%PDF-"):
        raise PdfGreska("fajl ne pocinje sa %PDF- - ovo nije PDF")

    objekti: dict[int, tuple] = {}
    for m in _OBJ.finditer(data):
        broj = int(m.group(1))
        vr, kraj = _citaj_objekat(data, m.end())
        sirovi = None
        k = _preskoci(data, kraj)
        if data[k:k + 6] == b"stream":
            k += 6
            if data[k:k + 2] == b"\r\n":
                k += 2
            elif data[k:k + 1] in (b"\n", b"\r"):
                k += 1
            kraj_toka = data.find(b"endstream", k)
            if kraj_toka < 0:
                kraj_toka = len(data)
            sirovi = data[k:kraj_toka]
        objekti[broj] = (vr, sirovi)

    doc = Dokument(objekti, data)

    for broj, (rj, _) in list(objekti.items()):                 # sifrovanje
        if isinstance(rj, dict) and "Encrypt" in rj:
            raise PdfGreska("PDF je zasticen (sifrovan) - skini zastitu pa pokusaj ponovo")
    if b"/Encrypt" in data and any(isinstance(rj, dict) and rj.get("Filter") == "/Standard"
                                   for rj, _ in objekti.values()):
        raise PdfGreska("PDF je zasticen (sifrovan) - skini zastitu pa pokusaj ponovo")

    _raspakuj_objektne_tokove(doc)
    if not doc.stranice:
        raise PdfGreska("u PDF-u nema nijedne stranice")
    return doc


def _raspakuj_objektne_tokove(doc: Dokument) -> None:
    """PDF 1.5+ pakuje objekte u /ObjStm. Bez ovoga stranice se ne vide."""
    for broj, (rj, sirovi) in list(doc.objekti.items()):
        if not (isinstance(rj, dict) and rj.get("Type") == "/ObjStm" and sirovi is not None):
            continue
        try:
            sadrzaj = _raspakuj(doc, rj, sirovi)
        except PdfGreska:
            continue
        n = int(doc.razrijesi(rj.get("N", 0)) or 0)
        prvi = int(doc.razrijesi(rj.get("First", 0)) or 0)
        glava = sadrzaj[:prvi].split()
        for k in range(n):
            try:
                br = int(glava[2 * k])
                pom = int(glava[2 * k + 1])
            except (IndexError, ValueError):
                break
            vr, _ = _citaj_objekat(sadrzaj, prvi + pom)
            doc.objekti.setdefault(br, (vr, None))


# ---------------------------------------------------- sadrzaj stranice
def sadrzaj_stranice(doc: Dokument, stranica: dict) -> bytes:
    """Spojeni i raspakovani tokovi sadrzaja jedne stranice."""
    c = stranica.get("Contents")
    brojevi = []
    if isinstance(c, Ref):
        brojevi = [c.broj]
    elif isinstance(c, list):
        brojevi = [x.broj for x in c if isinstance(x, Ref)]
    return b"\n".join(doc.tok(b) for b in brojevi)


def mediabox(doc: Dokument, stranica: dict) -> tuple[float, float, float, float]:
    """MediaBox u tackama. Nasljedjuje se od roditelja ako ga stranica nema."""
    cvor, dubina = stranica, 0
    while cvor is not None and dubina < 32:
        mb = doc.razrijesi(cvor.get("MediaBox"))
        if isinstance(mb, list) and len(mb) == 4:
            v = [float(doc.razrijesi(x) or 0) for x in mb]
            return (min(v[0], v[2]), min(v[1], v[3]), max(v[0], v[2]), max(v[1], v[3]))
        cvor = doc.razrijesi(cvor.get("Parent"))
        dubina += 1
    return (0.0, 0.0, 612.0, 792.0)                              # US Letter, PDF podrazumijevani


# ------------------------------------------------------- operatori crtanja
_OPERANDI = re.compile(
    rb"(?P<broj>[+-]?(?:\d+\.?\d*|\.\d+))"
    rb"|/(?P<ime>[^\s/\[\]<>(){}%]*)"
    rb"|(?P<op>[A-Za-z'\"*]+)"
    rb"|(?P<ostalo>[\[\]<>(){}])"
)


def _mnozi(a, b):
    """Mnozenje PDF matrica [a b c d e f]."""
    return (a[0] * b[0] + a[1] * b[2], a[0] * b[1] + a[1] * b[3],
            a[2] * b[0] + a[3] * b[2], a[2] * b[1] + a[3] * b[3],
            a[4] * b[0] + a[5] * b[2] + b[4], a[4] * b[1] + a[5] * b[3] + b[5])


def _tacka(m, x, y):
    return (m[0] * x + m[2] * y + m[4], m[1] * x + m[3] * y + m[5])


def _bezier(p0, p1, p2, p3, n=16):
    t = np.linspace(0.0, 1.0, n + 1)[1:]
    x = ((1 - t) ** 3 * p0[0] + 3 * (1 - t) ** 2 * t * p1[0]
         + 3 * (1 - t) * t ** 2 * p2[0] + t ** 3 * p3[0])
    y = ((1 - t) ** 3 * p0[1] + 3 * (1 - t) ** 2 * t * p1[1]
         + 3 * (1 - t) * t ** 2 * p2[1] + t ** 3 * p3[1])
    return list(zip(x.tolist(), y.tolist()))


def _u_rgb(op: str, br: list) -> tuple:
    """PDF boje: g (siva), rg (RGB), k (CMYK), sc/scn (po prostoru boja)."""
    if op == "g" and len(br) >= 1:
        v = br[-1]
        return (v, v, v)
    if op == "rg" and len(br) >= 3:
        return tuple(br[-3:])
    if op == "k" and len(br) >= 4:
        c, m, y, kk = br[-4:]
        return ((1 - c) * (1 - kk), (1 - m) * (1 - kk), (1 - y) * (1 - kk))
    if len(br) >= 3:
        return tuple(br[-3:])
    if len(br) >= 1:
        v = br[-1]
        return (v, v, v)
    return (0.0, 0.0, 0.0)


def putanje(sadrzaj: bytes, visina_pt: float) -> dict:
    """Prodji kroz tok sadrzaja i izvadi putanje u milimetrima.

    PDF-u je ishodiste dolje-lijevo, a SVG-u gore-lijevo, pa se Y okrece ovdje -
    jednom, na jednom mjestu, da se dalje nigdje ne mora misliti o tome.

    Vraca: {"putanje": [...], "ima_teksta": bool, "zatvorene": [...],
             "slike": [{"ime": "/Im0", "sirina_pt": w, "visina_pt": h}],
             "boje": [{"popuna": "#rrggbb"|None, "linija": "#rrggbb"|None}, ...]}

    Popuna i linija se pamte jer o njima ovisi sloj: popunjeno ide u gravuru,
    samo linija u rez. Bez toga bi svaki crtez zavrsio kao rez.

    Operator `Do` crta sliku u jedinicni kvadrat pomnozen trenutnom matricom,
    pa iz te matrice izlazi stvarna velicina slike na stranici.
    """
    stek: list = []
    ctm = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
    operandi: list = []
    putanja: list = []
    pod: list = []
    izlaz: list = []
    zatvorene: list = []
    ima_teksta = False
    slike: list = []
    boje: list = []
    boja_popune = (0.0, 0.0, 0.0)
    boja_linije = (0.0, 0.0, 0.0)
    trenutna = (0.0, 0.0)
    pocetak = (0.0, 0.0)
    u_tekstu = False

    def zavrsi_pod(zatvoreno=False):
        nonlocal pod
        if len(pod) >= 2:
            putanja.append((list(pod), zatvoreno))
        pod = []

    def hex_boje(rgb):
        r, g, b = (max(0, min(255, int(round(c * 255)))) for c in rgb)
        return f"#{r:02x}{g:02x}{b:02x}"

    def ispusti(popunjeno=False, linijom=True):
        nonlocal putanja
        for tacke, zatv in putanja:
            izlaz.append(tacke)
            zatvorene.append(zatv)
            boje.append({"popuna": hex_boje(boja_popune) if popunjeno else None,
                         "linija": hex_boje(boja_linije) if linijom else None})
        putanja = []

    for m in _OPERANDI.finditer(sadrzaj):
        if m.group("broj") is not None:
            operandi.append(float(m.group("broj")))
            continue
        if m.group("ime") is not None:
            operandi.append("/" + _ime_dekod(m.group("ime")))
            continue
        if m.group("op") is None:
            continue
        op = m.group("op").decode("latin-1")
        br = [x for x in operandi if isinstance(x, float)]

        if op == "q":
            stek.append(ctm)
        elif op == "Q":
            ctm = stek.pop() if stek else ctm
        elif op == "cm" and len(br) >= 6:
            ctm = _mnozi(tuple(br[-6:]), ctm)
        elif op == "BT":
            u_tekstu = True
        elif op == "ET":
            u_tekstu = False
        elif op in ("Tj", "TJ", "'", '"') and u_tekstu:
            ima_teksta = True
        elif op == "m" and len(br) >= 2:
            zavrsi_pod()
            trenutna = pocetak = _tacka(ctm, br[-2], br[-1])
            pod = [trenutna]
        elif op == "l" and len(br) >= 2:
            trenutna = _tacka(ctm, br[-2], br[-1])
            pod.append(trenutna)
        elif op in ("c", "v", "y") and pod:
            if op == "c" and len(br) >= 6:
                p1 = _tacka(ctm, br[-6], br[-5])
                p2 = _tacka(ctm, br[-4], br[-3])
                p3 = _tacka(ctm, br[-2], br[-1])
            elif op == "v" and len(br) >= 4:
                p1 = trenutna
                p2 = _tacka(ctm, br[-4], br[-3])
                p3 = _tacka(ctm, br[-2], br[-1])
            elif len(br) >= 4:
                p1 = _tacka(ctm, br[-4], br[-3])
                p3 = _tacka(ctm, br[-2], br[-1])
                p2 = p3
            else:
                operandi = []
                continue
            pod.extend(_bezier(trenutna, p1, p2, p3))
            trenutna = p3
        elif op == "h":
            if pod and pod[0] != pod[-1]:
                pod.append(pod[0])
            zavrsi_pod(True)
            trenutna = pocetak
        elif op == "re" and len(br) >= 4:
            zavrsi_pod()
            x, y, w, hh = br[-4:]
            uglovi = [_tacka(ctm, x, y), _tacka(ctm, x + w, y),
                      _tacka(ctm, x + w, y + hh), _tacka(ctm, x, y + hh)]
            uglovi.append(uglovi[0])
            putanja.append((uglovi, True))
            trenutna = pocetak = uglovi[0]
        elif op in ("g", "rg", "k", "sc", "scn") and br:
            boja_popune = _u_rgb(op, br)
        elif op in ("G", "RG", "K", "SC", "SCN") and br:
            boja_linije = _u_rgb(op.lower(), br)
        elif op in ("S", "s", "f", "F", "f*", "B", "B*", "b", "b*"):
            if op in ("s", "b", "b*") and pod and pod[0] != pod[-1]:
                pod.append(pod[0])
            zavrsi_pod(op in ("s", "b", "b*", "f", "F", "f*", "B", "B*"))
            ispusti(popunjeno=op in ("f", "F", "f*", "B", "B*", "b", "b*"),
                    linijom=op in ("S", "s", "B", "B*", "b", "b*"))
        elif op == "Do":
            ime = next((x for x in reversed(operandi) if isinstance(x, str)), None)
            if ime:
                sx = (ctm[0] ** 2 + ctm[1] ** 2) ** 0.5
                sy = (ctm[2] ** 2 + ctm[3] ** 2) ** 0.5
                slike.append({"ime": ime, "sirina_pt": sx, "visina_pt": sy})
        elif op == "n":
            zavrsi_pod()
            putanja = []
        operandi = []

    zavrsi_pod()
    ispusti()

    # PDF: Y raste nagore. SVG: nadolje. Okrece se jednom, ovdje.
    okrenute = [[(x, visina_pt - y) for x, y in t] for t in izlaz]
    return {"putanje": okrenute, "ima_teksta": ima_teksta, "zatvorene": zatvorene,
            "slike": slike, "boje": boje}


def slike_stranice(doc: Dokument, stranica: dict) -> list[dict]:
    """Ugradjene slike stranice: {'broj', 'sirina', 'visina', 'filter'}."""
    res = doc.razrijesi(stranica.get("Resources")) or {}
    xo = doc.razrijesi(res.get("XObject")) or {}
    out = []
    for _, ref in (xo.items() if isinstance(xo, dict) else []):
        if not isinstance(ref, Ref):
            continue
        rj = doc.razrijesi(ref)
        if not (isinstance(rj, dict) and rj.get("Subtype") == "/Image"):
            continue
        f = doc.razrijesi(rj.get("Filter"))
        out.append({"broj": ref.broj,
                    "sirina": int(doc.razrijesi(rj.get("Width", 0)) or 0),
                    "visina": int(doc.razrijesi(rj.get("Height", 0)) or 0),
                    "filter": f if isinstance(f, str) else (f[0] if isinstance(f, list) and f else None)})
    return out


def _broj_kanala(doc: "Dokument", cs) -> int:
    """Koliko kanala nosi prostor boja. 0 znaci da ga ovaj citac ne zna.

    ICCBased je najcesci u izvozima iz programa za crtanje: ime ne kaze nista,
    broj kanala stoji u /N samog toka, pa se mora razrijesiti.
    """
    cs = doc.razrijesi(cs)
    if isinstance(cs, str):
        return {"/DeviceRGB": 3, "/DeviceGray": 1, "/DeviceCMYK": 4,
                "/CalRGB": 3, "/CalGray": 1, "/G": 1, "/RGB": 3, "/CMYK": 4}.get(cs, 0)
    if isinstance(cs, list) and cs:
        ime = doc.razrijesi(cs[0])
        if ime == "/ICCBased" and len(cs) > 1:
            tok_rj = doc.razrijesi(cs[1])
            if isinstance(tok_rj, dict):
                n = doc.razrijesi(tok_rj.get("N"))
                if n in (1, 3, 4):
                    return int(n)
            return 0
        if ime in ("/CalRGB",):
            return 3
        if ime in ("/CalGray",):
            return 1
        if ime in ("/DeviceN",) and len(cs) > 1:
            imena = doc.razrijesi(cs[1])
            return len(imena) if isinstance(imena, list) else 0
        return _broj_kanala(doc, ime)
    return 0


def _uzorci(data: bytes, w: int, h: int, boja: int, bpc: int) -> np.ndarray:
    """Sirovi niz uzoraka -> matrica (h, w, boja) u 8 bita.

    Svaki red je poravnat na cijeli bajt, pa se visak bitova na kraju reda
    odbacuje. Bez toga se slika "kosi" - svaki sljedeci red pomjeren udesno.
    """
    red_bajtova = (w * boja * bpc + 7) // 8
    treba = red_bajtova * h
    if len(data) < treba:
        raise PdfGreska("slika u PDF-u je nepotpuna")
    sirovo = np.frombuffer(data[:treba], dtype=np.uint8).reshape(h, red_bajtova)
    if bpc == 8:
        return sirovo[:, :w * boja].reshape(h, w, boja)
    bitovi = np.unpackbits(sirovo, axis=1)                  # (h, red_bajtova*8)
    po_uzorku = bitovi[:, :w * boja * bpc].reshape(h, w * boja, bpc)
    tezine = (1 << np.arange(bpc - 1, -1, -1)).astype(np.uint16)
    vrijednosti = (po_uzorku * tezine).sum(axis=2)
    skala = 255 // ((1 << bpc) - 1)
    return (vrijednosti * skala).astype(np.uint8).reshape(h, w, boja)


def slika_kao_png(doc: Dokument, broj: int) -> bytes:
    """Ugradjena slika -> PNG bajtovi, spremni za postojeci tok trasiranja.

    DCTDecode je vec JPEG i ide kakav jeste. FlateDecode je sirovi niz uzoraka,
    pa se slozi u matricu po /Width, /Height i /Colors i upakuje u PNG.
    """
    rj, sirovi = doc.objekti.get(broj, (None, None))
    if sirovi is None or not isinstance(rj, dict):
        raise PdfGreska("slika iz PDF-a se ne moze procitati")
    f = doc.razrijesi(rj.get("Filter"))
    if isinstance(f, list):
        f = f[0] if f else None
    if f in ("/DCTDecode", "/DCT"):
        return sirovi                                   # JPEG, bez diranja
    if f == "/JPXDecode":
        raise PdfGreska("slika u PDF-u je JPEG2000, koji ovaj citac ne podrzava")

    data = _raspakuj(doc, rj, sirovi)
    w = int(doc.razrijesi(rj.get("Width", 0)) or 0)
    h = int(doc.razrijesi(rj.get("Height", 0)) or 0)
    bpc = int(doc.razrijesi(rj.get("BitsPerComponent", 8)) or 8)
    cs = doc.razrijesi(rj.get("ColorSpace"))
    maska = bool(doc.razrijesi(rj.get("ImageMask")))
    boja = 1 if maska else _broj_kanala(doc, cs)
    if not (w and h and boja) or bpc not in (1, 2, 4, 8):
        raise PdfGreska("slika u PDF-u je u zapisu koji ovaj citac ne podrzava "
                        f"(sirina {w}, visina {h}, boja {cs}, {bpc} bita)")
    a = _uzorci(data, w, h, boja, bpc)
    if maska:
        # ImageMask: uzorak 0 crta, 1 ne crta. /Decode [1 0] okrece znacenje.
        dek = doc.razrijesi(rj.get("Decode"))
        if isinstance(dek, list) and len(dek) == 2 and float(doc.razrijesi(dek[0]) or 0) == 1:
            a = 255 - a
    if boja == 4:                                       # CMYK -> RGB
        c = a.astype(np.float32) / 255.0
        a = ((1 - c[..., :3]) * (1 - c[..., 3:4]) * 255).astype(np.uint8)
        boja = 3
    import cv2                                          # Apache-2.0, vec je zavisnost
    bgr = a[:, :, ::-1] if boja == 3 else a
    ok, buf = cv2.imencode(".png", bgr)
    if not ok:
        raise PdfGreska("slika iz PDF-a se ne moze upakovati u PNG")
    return buf.tobytes()

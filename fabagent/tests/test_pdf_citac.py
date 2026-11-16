"""Testovi vlastitog citaca PDF-a (`core/pdf_citac.py`).

PDF-ovi se ovdje prave rukom, bajt po bajt, bez ijedne biblioteke. Tako test
ne ovisi o alatu koji je zamijenjen i provjerava bas ono sto citac mora znati.
"""

from __future__ import annotations

import zlib

import numpy as np
import pytest

from fabagent.core import pdf_citac as P


def napravi_pdf(sadrzaj: bytes, komprimuj: bool = False, mediabox: bytes = b"[0 0 200 100]",
                dodatni: bytes = b"", resursi: bytes = b"") -> bytes:
    """Najmanji ispravan PDF sa jednom stranicom i jednim tokom sadrzaja."""
    tok = zlib.compress(sadrzaj) if komprimuj else sadrzaj
    filt = b"/Filter /FlateDecode " if komprimuj else b""
    djelovi = [
        b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n",
        b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n",
        b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox " + mediabox +
        b" /Contents 4 0 R " + resursi + b">>\nendobj\n",
        b"4 0 obj\n<< /Length " + str(len(tok)).encode() + b" " + filt + b">>\nstream\n" +
        tok + b"\nendstream\nendobj\n",
        dodatni,
    ]
    return b"%PDF-1.4\n" + b"".join(djelovi) + b"trailer\n<< /Root 1 0 R >>\n%%EOF\n"


def prva(pdf: bytes):
    d = P.ucitaj(pdf)
    st = d.stranice[0]
    mb = P.mediabox(d, st)
    return d, st, mb, P.putanje(P.sadrzaj_stranice(d, st), mb[3] - mb[1])


# ------------------------------------------------------------- osnovno
def test_pravougaonik_re_daje_zatvorenu_putanju():
    """Operator `re` je pravougaonik; mora izaci pet tacaka i zatvorena putanja."""
    _, _, mb, r = prva(napravi_pdf(b"10 20 50 30 re S\n"))
    assert mb == (0.0, 0.0, 200.0, 100.0)
    assert len(r["putanje"]) == 1
    assert r["zatvorene"][0] is True
    xs = [p[0] for p in r["putanje"][0]]
    ys = [p[1] for p in r["putanje"][0]]
    assert (min(xs), max(xs)) == (10.0, 60.0)
    # PDF mjeri Y odozdo, SVG odozgo: 100-20=80, 100-50=50
    assert (min(ys), max(ys)) == (50.0, 80.0)


def test_matrica_cm_skalira_tacke():
    """`cm` mijenja koordinatni sistem; bez njega bi crtez bio u pogresnoj mjeri."""
    _, _, _, r = prva(napravi_pdf(b"q 2 0 0 2 0 0 cm 0 0 m 10 10 l S Q\n"))
    assert r["putanje"][0] == [(0.0, 100.0), (20.0, 80.0)]


def test_q_vraca_prethodnu_matricu():
    _, _, _, r = prva(napravi_pdf(b"q 3 0 0 3 0 0 cm Q 0 0 m 10 0 l S\n"))
    assert r["putanje"][0] == [(0.0, 100.0), (10.0, 100.0)]


def test_flate_tok_se_raspakuje():
    _, _, _, r = prva(napravi_pdf(b"5 5 m 15 5 l S\n", komprimuj=True))
    assert r["putanje"][0] == [(5.0, 95.0), (15.0, 95.0)]


def test_bezier_se_pretvara_u_tacke():
    _, _, _, r = prva(napravi_pdf(b"0 0 m 0 50 50 50 50 0 c S\n"))
    assert len(r["putanje"][0]) == 17          # pocetna + 16 koraka krivulje


def test_tekst_se_prijavljuje():
    """Tekst bez fonta se ne moze rezati, pa mora biti prepoznat kao prepreka."""
    _, _, _, r = prva(napravi_pdf(b"BT /F1 12 Tf 10 10 Td (Zdravo) Tj ET\n"))
    assert r["ima_teksta"] is True


def test_bez_teksta_nema_laznog_prijavljivanja():
    _, _, _, r = prva(napravi_pdf(b"10 10 m 20 20 l S\n"))
    assert r["ima_teksta"] is False


# --------------------------------------------------------------- boje
def test_popuna_i_linija_se_razlikuju():
    """Popunjeno ide u gravuru, samo linija u rez - zato boje moraju biti odvojene."""
    _, _, _, r = prva(napravi_pdf(b"0 0 0 rg 1 0 0 RG 10 10 30 30 re B\n"))
    b = r["boje"][0]
    assert b["popuna"] == "#000000"
    assert b["linija"] == "#ff0000"


def test_samo_linija_nema_popunu():
    _, _, _, r = prva(napravi_pdf(b"10 10 30 30 re S\n"))
    assert r["boje"][0]["popuna"] is None
    assert r["boje"][0]["linija"] == "#000000"


def test_siva_boja_g():
    _, _, _, r = prva(napravi_pdf(b"0.5 g 10 10 30 30 re f\n"))
    assert r["boje"][0]["popuna"] == "#808080"


# --------------------------------------------------------- odbijanje
def test_nije_pdf():
    with pytest.raises(P.PdfGreska):
        P.ucitaj(b"ovo nije pdf")


def test_prazan_pdf_bez_stranica():
    with pytest.raises(P.PdfGreska):
        P.ucitaj(b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\n%%EOF\n")


def test_operator_n_ne_crta():
    """`n` zatvara putanju bez crtanja - takav crtez ne smije zavrsiti u rezu."""
    _, _, _, r = prva(napravi_pdf(b"10 10 m 20 20 l n\n"))
    assert r["putanje"] == []


# --------------------------------------------------------------- slike
def _pdf_sa_slikom(uzorci: bytes, w: int, h: int, bpc: int, prostor: bytes) -> bytes:
    slika = (b"5 0 obj\n<< /Type /XObject /Subtype /Image /Width " + str(w).encode() +
             b" /Height " + str(h).encode() + b" /BitsPerComponent " + str(bpc).encode() +
             b" /ColorSpace " + prostor + b" /Length " + str(len(uzorci)).encode() +
             b" >>\nstream\n" + uzorci + b"\nendstream\nendobj\n")
    return napravi_pdf(b"q 60 0 0 40 10 10 cm /Im0 Do Q\n", dodatni=slika,
                       resursi=b"/Resources << /XObject << /Im0 5 0 R >> >> ")


def test_slika_8_bita_siva():
    uzorci = bytes(range(0, 256, 4))[:64]                 # 8 x 8 px
    d = P.ucitaj(_pdf_sa_slikom(uzorci, 8, 8, 8, b"/DeviceGray"))
    st = d.stranice[0]
    slike = P.slike_stranice(d, st)
    assert len(slike) == 1 and slike[0]["sirina"] == 8
    png = P.slika_kao_png(d, slike[0]["broj"])
    assert png[:4] == b"\x89PNG"


def test_slika_1_bit_se_raspakuje():
    """Skenirani crtezi su cesto 1 bit po pikselu - bez raspakivanja se kose."""
    red = bytes([0b10101010])                              # 8 px u jednom bajtu
    d = P.ucitaj(_pdf_sa_slikom(red * 8, 8, 8, 1, b"/DeviceGray"))
    slike = P.slike_stranice(d, d.stranice[0])
    png = P.slika_kao_png(d, slike[0]["broj"])
    assert png[:4] == b"\x89PNG"


def test_velicina_slike_dolazi_iz_matrice():
    """`Do` crta sliku u jedinicni kvadrat pomnozen matricom - odatle mm na materijalu."""
    d = P.ucitaj(_pdf_sa_slikom(bytes(64), 8, 8, 8, b"/DeviceGray"))
    st = d.stranice[0]
    r = P.putanje(P.sadrzaj_stranice(d, st), 100.0)
    assert r["slike"][0]["sirina_pt"] == pytest.approx(60.0)
    assert r["slike"][0]["visina_pt"] == pytest.approx(40.0)


def test_nepoznat_prostor_boja_se_prijavljuje():
    d = P.ucitaj(_pdf_sa_slikom(bytes(64), 8, 8, 8, b"/NekiNepoznatiProstor"))
    slike = P.slike_stranice(d, d.stranice[0])
    with pytest.raises(P.PdfGreska):
        P.slika_kao_png(d, slike[0]["broj"])


# ------------------------------------------------- raspakivanje uzoraka
@pytest.mark.parametrize("bpc,ulaz,ocekivano", [
    (1, bytes([0b10000000]), 255),
    (2, bytes([0b11000000]), 255),
    (4, bytes([0b11110000]), 255),
    (8, bytes([255]), 255),
])
def test_uzorci_skaliraju_na_osam_bita(bpc, ulaz, ocekivano):
    a = P._uzorci(ulaz, 1, 1, 1, bpc)
    assert a.shape == (1, 1, 1)
    assert int(a[0, 0, 0]) == ocekivano


def test_redovi_su_poravnati_na_bajt():
    """Red od 3 piksela pri 1 bitu zauzima cijeli bajt; visak se odbacuje."""
    data = bytes([0b11100000, 0b00000000])                 # red 1: 111, red 2: 000
    a = P._uzorci(data, 3, 2, 1, 1)
    assert a[0, :, 0].tolist() == [255, 255, 255]
    assert a[1, :, 0].tolist() == [0, 0, 0]


def test_nepotpuna_slika():
    with pytest.raises(P.PdfGreska):
        P._uzorci(bytes(3), 10, 10, 1, 8)

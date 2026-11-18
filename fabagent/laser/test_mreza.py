"""Test mreza materijala (kao LightBurn: Laser Tools -> Material Test).

Sta radi
--------
Pravi mrezu kvadratica. Svaki red je jedna SNAGA, svaka kolona jedna BRZINA.
Svaki kvadratic se izvodi sa svojim parametrima, pa se na jednom komadu
materijala odjednom vidi gdje pocinje da gori, a gdje jos ne sijece.

Zasto G-code direktno
---------------------
Posao u FabAgentu ima tri sloja (CUT, ENGRAVE, SCORE) i jedan skup postavki po
sloju. Test mreza trazi razlicite postavke za svaki kvadratic, pa se G-code
pise ovdje, red po red, sa `S` i `F` upisanim uz svaki kvadratic.

Veza sa ucenjem
---------------
`celije()` vraca spisak (red, kolona, brzina, snaga, prolaza) sa oznakom svake
celije. Kad operater pogleda izgorjelu plocu, za svaku celiju upise ishod u
`laser/ucenje.py` i time nastaje baza na kojoj model moze da uci. Bez ovoga se
svaki test rucno prepisuje i podaci se gube.

Nista se ne izmislja: brzine i snage zadaje operater. Laser se ne pokrece.
"""

from __future__ import annotations

from dataclasses import dataclass

from fabagent.core import config as C
from fabagent.core.errors import InvalidParam, OutOfRange, RangeViolation

KVADRAT_MM = (3.0, 50.0)
RAZMAK_MM = (1.0, 30.0)
MAX_CELIJA = 400


@dataclass(frozen=True)
class Celija:
    red: int
    kolona: int
    brzina_mm_min: float
    snaga_pct: float
    prolaza: int
    x_mm: float
    y_mm: float
    strana_mm: float

    @property
    def oznaka(self) -> str:
        return f"R{self.red + 1}K{self.kolona + 1}"


def _broj_liste(ime: str, v, mn: float, mx: float) -> list[float]:
    if not v:
        raise InvalidParam(f"{ime}: lista ne smije biti prazna")
    out = []
    for x in v:
        try:
            f = float(x)
        except (TypeError, ValueError) as exc:
            raise InvalidParam(f"{ime}: '{x}' nije broj") from exc
        if not (mn <= f <= mx):
            raise OutOfRange(ime, [RangeViolation(ime, f, mn, mx, "", f"{ime} izvan opsega")])
        out.append(f)
    return out


def celije(brzine, snage, *, prolaza: int = 1, strana_mm: float = 10.0,
           razmak_mm: float = 3.0, pocetak_x_mm: float = 10.0,
           pocetak_y_mm: float = 10.0) -> list[Celija]:
    """Raspored kvadratica. Red = snaga, kolona = brzina."""
    br = _broj_liste("brzina", brzine, 1.0, float(C.GRBL_MAX_FEED_MM_MIN)
                     if hasattr(C, "GRBL_MAX_FEED_MM_MIN") else 20000.0)
    sn = _broj_liste("snaga", snage, 1.0, 100.0)
    if not (KVADRAT_MM[0] <= strana_mm <= KVADRAT_MM[1]):
        raise OutOfRange("strana", [RangeViolation("strana", strana_mm, *KVADRAT_MM, "mm",
                                                   "strana kvadratica izvan opsega")])
    if not (RAZMAK_MM[0] <= razmak_mm <= RAZMAK_MM[1]):
        raise OutOfRange("razmak", [RangeViolation("razmak", razmak_mm, *RAZMAK_MM, "mm",
                                                   "razmak izvan opsega")])
    if prolaza < 1:
        raise InvalidParam("prolaza: mora biti bar 1")
    if len(br) * len(sn) > MAX_CELIJA:
        raise InvalidParam(f"mreza ima {len(br) * len(sn)} celija, najvise je {MAX_CELIJA}")

    W, H = C.LASER_WORK_AREA_MM
    korak = strana_mm + razmak_mm
    sirina = pocetak_x_mm + len(br) * korak - razmak_mm
    visina = pocetak_y_mm + len(sn) * korak - razmak_mm
    if sirina > W or visina > H:
        raise OutOfRange("mreza", [RangeViolation(
            "mreza", max(sirina, visina), 0, min(W, H), "mm",
            f"mreza {sirina:.1f} x {visina:.1f} mm ne stane na radnu povrsinu {W:g} x {H:g} mm - "
            f"smanji broj vrijednosti, stranu kvadratica ili razmak")])

    out: list[Celija] = []
    for r, s in enumerate(sn):
        for k, b in enumerate(br):
            out.append(Celija(red=r, kolona=k, brzina_mm_min=b, snaga_pct=s, prolaza=prolaza,
                              x_mm=pocetak_x_mm + k * korak,
                              y_mm=pocetak_y_mm + r * korak,
                              strana_mm=strana_mm))
    return out


def gcode(lista: list[Celija], *, materijal: str = "", debljina_mm: float | None = None,
          puna_ispuna: bool = False, interval_mm: float = 0.1) -> str:
    """G-code mreze. Svaki kvadratic nosi svoju snagu i brzinu.

    puna_ispuna=False -> samo obris kvadratica (za test reza)
    puna_ispuna=True  -> popunjavanje linijama na `interval_mm` (za test gravure)
    """
    if not lista:
        raise InvalidParam("mreza: nema nijedne celije")
    H = float(C.LASER_WORK_AREA_MM[1])
    d = C.GCODE_DECIMALS
    smax = int(C.GRBL_S_MAX)

    def xy(x: float, y: float) -> str:
        return f"X{x:.{d}f} Y{H - y:.{d}f}"

    L = [f"; FabAgent test mreza materijala{(' - ' + materijal) if materijal else ''}"
         + (f", debljina {debljina_mm:g} mm" if debljina_mm else ""),
         f"; masina: {C.LASER_MACHINE_NAME}, S max {smax}, ishodiste {C.MACHINE_ORIGIN}",
         f"; celija: {len(lista)}, kvadratic {lista[0].strana_mm:g} mm",
         "; red = snaga, kolona = brzina; oznake celija su u izvjestaju mreze",
         "; VAZNO: Start pritiska operater. Ovaj fajl se samo zapisuje.",
         "G21 ; mm", "G90 ; apsolutno", "M5", f"{C.GRBL_LASER_MODE_CMD} S0"]

    for c in lista:
        s_val = max(1, min(smax, int(round(c.snaga_pct / 100.0 * smax))))
        feed = f"F{c.brzina_mm_min:.0f}"
        L.append(f"; --- {c.oznaka}: {c.brzina_mm_min:g} mm/min, {c.snaga_pct:g} %, "
                 f"{c.prolaza} prolaz(a)")
        x0, y0, a = c.x_mm, c.y_mm, c.strana_mm
        for _ in range(c.prolaza):
            if puna_ispuna:
                y = y0
                naprijed = True
                while y <= y0 + a + 1e-9:
                    p1, p2 = (x0, x0 + a) if naprijed else (x0 + a, x0)
                    L.append(f"G0 {xy(p1, y)} S0")
                    L.append(f"G1 {xy(p2, y)} S{s_val} {feed}")
                    y += interval_mm
                    naprijed = not naprijed
            else:
                L.append(f"G0 {xy(x0, y0)} S0")
                for px, py in ((x0 + a, y0), (x0 + a, y0 + a), (x0, y0 + a), (x0, y0)):
                    L.append(f"G1 {xy(px, py)} S{s_val} {feed}")
        L.append("M5 S0")

    L += ["M5 ; laser ugasen", "G0 X0 Y0", "M2"]
    return "\n".join(L) + "\n"


def svg(lista: list[Celija], materijal: str = "") -> str:
    """Pregled mreze sa oznakama, da operater zna koji kvadratic je koji."""
    W, H = C.LASER_WORK_AREA_MM
    d = []
    for c in lista:
        d.append(f'<rect x="{c.x_mm:.3f}" y="{H - c.y_mm - c.strana_mm:.3f}" '
                 f'width="{c.strana_mm:.3f}" height="{c.strana_mm:.3f}" '
                 f'fill="none" stroke="#ff0000" stroke-width="0.2"/>')
        d.append(f'<text x="{c.x_mm:.3f}" y="{H - c.y_mm - c.strana_mm - 0.6:.3f}" '
                 f'font-size="2.2" fill="#555">{c.oznaka}</text>')
    naslov = f"Test mreza {materijal}".strip()
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{W:g}mm" height="{H:g}mm" '
            f'viewBox="0 0 {W:g} {H:g}">'
            f'<title>{naslov}</title>' + "".join(d) + "</svg>")


def izvjestaj(lista: list[Celija]) -> list[dict]:
    """Spisak celija za upis ishoda (ide u laser/ucenje.py)."""
    return [{"oznaka": c.oznaka, "red": c.red + 1, "kolona": c.kolona + 1,
             "brzina_mm_min": c.brzina_mm_min, "snaga_pct": c.snaga_pct,
             "prolaza": c.prolaza, "x_mm": round(c.x_mm, 2), "y_mm": round(c.y_mm, 2)}
            for c in lista]


def gcode_okvir(celije_izvjestaj: list[dict], strana_mm: float = 10.0) -> str:
    """Okvir oko cijele mreze, laser ugasen (Frame). Samo G0/G1 sa S0 i M5."""
    if not celije_izvjestaj:
        raise InvalidParam("mreza: nema celija za okvir")
    xs = [c["x_mm"] for c in celije_izvjestaj]
    ys = [c["y_mm"] for c in celije_izvjestaj]
    a = float(celije_izvjestaj[0].get("strana_mm") or strana_mm)
    x0, y0 = min(xs), min(ys)
    x1, y1 = max(xs) + a, max(ys) + a
    H = float(C.LASER_WORK_AREA_MM[1])
    d = C.GCODE_DECIMALS
    xy = lambda x, y: f"X{x:.{d}f} Y{H - y:.{d}f}"          # noqa: E731
    feed = f"F{C.GRBL_TRAVEL_FEED_MM_MIN:.0f}"
    L = ["; FabAgent okvir test mreze - laser ostaje ugasen",
         "G21 ; mm", "G90 ; apsolutno", "M5", "S0",
         f"G0 {xy(x0, y0)}"]
    for px, py in ((x1, y0), (x1, y1), (x0, y1), (x0, y0)):
        L.append(f"G1 {xy(px, py)} S0 {feed}")
    L += ["M5", "M2"]
    return "\n".join(L) + "\n"

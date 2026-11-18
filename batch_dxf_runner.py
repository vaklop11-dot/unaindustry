"""Paketna obrada: ZIP -> samo DXF iz njega -> SVG -> G-code.

Pokretanje (iz foldera FabAgent, sa .venv):

    .venv\\Scripts\\python.exe batch_dxf_runner.py "C:\\Users\\PC\\Desktop\\NAPRAVITI"
    .venv\\Scripts\\python.exe batch_dxf_runner.py "C:\\...\\102921_Candy Box House.zip"
    .venv\\Scripts\\python.exe batch_dxf_runner.py <putanja> --debljina 3 --izlaz izlaz\\dxf

SVG iz arhiva se NE dira - uzimaju se iskljucivo .dxf fajlovi (ezdxf).
Laser se ne pokrece: ovo samo pise .nc fajlove na disk.
"""

from __future__ import annotations

import argparse
import sys
import zipfile
from pathlib import Path

KORIJEN = Path(__file__).resolve().parent
if str(KORIJEN) not in sys.path:
    sys.path.insert(0, str(KORIJEN))

from fabagent.core import config as C                      # noqa: E402
from fabagent.core import gcode as G                       # noqa: E402
from fabagent.core.converter import SUPPORTED_EXT, convert  # noqa: E402
from fabagent.core.svg_fixer import fix_svg                # noqa: E402
from fabagent.laser import dijeljenje as DJ                 # noqa: E402
from fabagent.laser import transformacije as TF            # noqa: E402
from fabagent.laser.presets import get_preset               # noqa: E402


def crtezi_iz_zipa(zp: Path, ekstenzije: tuple[str, ...]) -> list[tuple[str, bytes]]:
    """Imena i sadrzaj svih podrzanih unosa u arhivi. Arhiva se ne raspakuje."""
    out: list[tuple[str, bytes]] = []
    with zipfile.ZipFile(zp) as z:
        for ime in z.namelist():
            if ime.endswith("/"):
                continue
            ext = "." + ime.rsplit(".", 1)[-1].lower() if "." in ime else ""
            if ext in ekstenzije:
                out.append((ime.rsplit("/", 1)[-1], z.read(ime)))
    return out


def _postavke(materijal: str) -> dict:
    """LayerSetting po sloju iz presets.py. Nista se ne izmislja - ako preset
    za materijal nema taj sloj, sloj se preskace."""
    p = get_preset(materijal)
    izbor = {}
    for sloj, op in (("CUT", p.cut), ("ENGRAVE", p.engrave), ("SCORE", p.score)):
        if op is None:
            continue
        izbor[sloj] = G.LayerSetting(
            mode="Engrave" if sloj == "ENGRAVE" else "Cut",
            speed_mm_s=op.speed_mm_s,
            power_pct=op.power_pct,
            passes=op.passes,
            interval_mm=getattr(op, "interval_mm", 0.1) or 0.1,
        )
    return izbor


def obradi_fajl(ime: str, podaci: bytes, materijal: str, izlaz: Path, dijeli: bool,
                operacije: list[str] | None = None,
                sirina_mm: float | None = None, prag: int | None = None) -> dict:
    red = {"fajl": ime, "status": "", "duple": 0, "poruka": ""}
    try:
        konv = convert(ime, podaci, dxf_units="mm",
                       width_mm=sirina_mm, threshold=prag)
        popravljen = fix_svg(konv.svg, ime)
        post = _postavke(materijal)
        W, H = float(C.LASER_WORK_AREA_MM[0]), float(C.LASER_WORK_AREA_MM[1])

        otv: dict[str, list] = {}
        for o in popravljen.open_paths or []:
            otv.setdefault("CUT", []).append(o)

        if popravljen.design is not None and not dijeli:
            table = [None]
            prevelici = []
        else:
            lista = DJ.komadi(popravljen.layers, otv)
            table, prevelici = DJ.rasporedi(lista, W, H)

        if not table:
            red["status"] = "PAD"
            red["poruka"] = "nijedan komad ne stane na radnu povrsinu"
            return red

        napisano = []
        for i, tabla in enumerate(table, 1):
            d = popravljen.design if tabla is None else DJ.dizajn_table(tabla, W, H)
            if operacije:
                d = TF.dizajn_transformisan(d, operacije)
            rez = G.generate(d, post, (0.0, 0.0), title=f"{ime} dio {i}")
            nc = izlaz / (Path(ime).stem + (f"_dio{i}.nc" if len(table) > 1 else ".nc"))
            nc.write_text(rez.text, encoding="utf-8")
            napisano.append(nc.name)
            red["duple"] += int(rez.stats.get("duple_preskocene", 0))

        red["status"] = "OK"
        red["poruka"] = f"{len(napisano)} tabla/i: {', '.join(napisano)}"
        if prevelici:
            red["poruka"] += (f" | {len(prevelici)} komad(a) ne stane ni sam: "
                              + ", ".join(f"{k.sirina:.0f}x{k.visina:.0f}mm" for k in prevelici[:3]))
    except Exception as exc:
        red["status"] = "PAD"
        red["poruka"] = f"{type(exc).__name__}: {str(exc)[:120]}"
    return red


def main() -> int:
    a = argparse.ArgumentParser(description="ZIP -> DXF -> G-code, bez raspakivanja na disk")
    a.add_argument("putanja", help="ZIP fajl ili folder sa ZIP arhivama")
    a.add_argument("--materijal", default="sperploca_3mm", help="kljuc iz presets.py")
    a.add_argument("--izlaz", default="izlaz/dxf_paket", help="folder za .nc fajlove")
    a.add_argument("--samo", default="", help="obradi samo fajlove ciji naziv sadrzi ovaj tekst")
    a.add_argument("--sirina-mm", type=float, default=None, dest="sirina_mm",
                   help="sirina u mm za JPG/PNG/BMP (slike nemaju mjeru u sebi)")
    a.add_argument("--prag", type=int, default=None, help="prag 0-255 za slike")
    a.add_argument("--formati", default=",".join(SUPPORTED_EXT),
                   help="koje ekstenzije uzeti iz arhive")
    a.add_argument("--okreni", default="", help="okreni_v | okreni_h | rot90 | rot180 | rot270, "
                                               "vise njih odvoji zarezom")
    a.add_argument("--bez-dijeljenja", action="store_true",
                   help="ne dijeli preveliki crtez na table (tada preveliki pada)")
    arg = a.parse_args()

    p = Path(arg.putanja)
    if not p.exists():
        print(f"GRESKA: ne postoji: {p}")
        return 2
    if p.is_file() and p.suffix.lower() != ".zip":
        arhive = []                       # pojedinacan crtez, bez arhive
    else:
        arhive = sorted(p.glob("*.zip")) if p.is_dir() else [p]
        if not arhive:
            print(f"GRESKA: nema nijedne .zip arhive u {p}")
            return 2

    ops = [o.strip() for o in arg.okreni.split(",") if o.strip()]
    for o in ops:
        if o not in TF.OPERACIJE:
            print(f"GRESKA: '{o}' nije operacija. Izbor: {', '.join(TF.OPERACIJE)}")
            return 2

    ekstenzije = tuple(e if e.startswith(".") else "." + e
                       for e in (x.strip().lower() for x in arg.formati.split(",")) if e)

    izlaz = Path(arg.izlaz)
    izlaz.mkdir(parents=True, exist_ok=True)

    ukupno = ok = pao = preskocen = duplih = 0
    if not arhive:                        # jedan fajl, direktno
        ukupno = 1
        r = obradi_fajl(p.name, p.read_bytes(), arg.materijal, izlaz,
                        not arg.bez_dijeljenja, ops, arg.sirina_mm, arg.prag)
        print(f"   {r['status']:10} {p.name}  {r['poruka']}")
        ok = 1 if r["status"] == "OK" else 0
        pao = 1 - ok
    for zp in arhive:
        try:
            unosi = crtezi_iz_zipa(zp, ekstenzije)
        except zipfile.BadZipFile as exc:
            print(f"\n== {zp.name}\n   GRESKA: arhiva nije citljiva: {exc}")
            continue
        if arg.samo:
            unosi = [(i, d) for i, d in unosi if arg.samo.lower() in i.lower()]
        print(f"\n== {zp.name}  ({len(unosi)} crteza)")
        for ime, podaci in unosi:
            ukupno += 1
            r = obradi_fajl(ime, podaci, arg.materijal, izlaz, not arg.bez_dijeljenja, ops,
                            arg.sirina_mm, arg.prag)
            duplih += r["duple"]
            if r["status"] == "OK":
                ok += 1
                dup = f"  [duplih izbaceno: {r['duple']}]" if r["duple"] else ""
                print(f"   OK         {ime}  ->  {r['poruka']}{dup}")
            elif r["status"] == "PRESKOCENO":
                preskocen += 1
                print(f"   PRESKOCENO {ime}  {r['poruka']}")
            else:
                pao += 1
                print(f"   PAD        {ime}  {r['poruka']}")

    print(f"\nCrteza: {ukupno}   G-code napisan: {ok}   preskoceno: {preskocen}   palo: {pao}")
    print(f"Duplih putanja izbaceno ukupno: {duplih}")
    print(f"Fajlovi su u: {izlaz.resolve()}")
    print(f"Masina: {C.LASER_MACHINE_NAME}. Start pritiskas ti, ovaj program laser ne pokrece.")
    return 0 if pao == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

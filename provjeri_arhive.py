"""Provjera svih arhiva kroz pravi FabAgent lanac, bez raspakivanja i bez kopiranja.

Zasto ovako
-----------
Arhive su desetine gigabajta i stoje na ovom racunaru. Ne mogu se slati nigdje,
a provjera mora da ide kroz ISTI kod koji radi kad operater klikne u aplikaciji:
converter -> svg_fixer -> (dijeljenje na ploce) -> gcode. Sve sto ovdje padne,
palo bi i u aplikaciji.

Sta radi
--------
1. Nadje sve arhive (.zip i .tar*) u zadanom folderu i podfolderima.
2. Za svaku procita SPISAK (bez raspakivanja), uzme crteze (.svg .dxf .pdf),
   i svaki provuce kroz lanac.
3. Zapise izvjestaj: sta prolazi, sta pada i zasto, grupisano po razlogu.

Nista se ne mijenja na disku osim izvjestaja. Laser se ne dira.

Pokretanje
----------
    .venv\\Scripts\\python.exe provjeri_arhive.py "C:\\Users\\PC\\Desktop"
    .venv\\Scripts\\python.exe provjeri_arhive.py "C:\\Users\\PC\\Desktop" --po-arhivi 3
    .venv\\Scripts\\python.exe provjeri_arhive.py "C:\\Users\\PC\\Desktop" --nastavi
"""

from __future__ import annotations

import argparse
import collections
import json
import sys
import time
import traceback
import zipfile
from pathlib import Path

KORIJEN = Path(__file__).resolve().parent
if str(KORIJEN) not in sys.path:
    sys.path.insert(0, str(KORIJEN))

from fabagent.core import arhiva as AR                       # noqa: E402
from fabagent.core.converter import IMAGE_EXT, convert       # noqa: E402
from fabagent.core.errors import FabError                    # noqa: E402
from fabagent.core.svg_fixer import fix_svg                  # noqa: E402

CRTEZ_EXT = (".svg", ".dxf", ".pdf")
MAX_BAJTOVA_CRTEZA = 40 * 1024 * 1024          # preko ovoga je skoro sigurno slika u vektoru


def _razlog(exc: Exception) -> str:
    """Kratka, grupisiva oznaka greske - da izvjestaj ne bude 1000 razlicitih redova."""
    t = type(exc).__name__
    p = str(exc)
    for kljuc in ("veci od radne povrsine", "min zid", "otvorena putanja",
                  "nema podrzanih entiteta", "DOCTYPE", "ugradjenu sliku",
                  "nije parsabilan", "nije SVG", "kontura sa manje od 3",
                  "ne moze rezati", "jedinice"):
        if kljuc in p:
            return f"{t}: {kljuc}"
    return f"{t}: {p[:70]}"


def provjeri_crtez(ime: str, podaci: bytes, materijal: str) -> tuple[str, str]:
    """Vraca (status, detalj). Status: OK | PLOCE | PAD."""
    from fabagent.core import config as C
    try:
        conv = convert(ime, podaci, dxf_units="mm")
        res = fix_svg(conv.svg, ime)
    except FabError as exc:
        return "PAD", _razlog(exc)
    except Exception as exc:                                  # noqa: BLE001
        return "PAD", _razlog(exc)

    W, H = C.LASER_WORK_AREA_MM
    if res.design is not None:
        return "OK", f"{res.width_mm:.0f}x{res.height_mm:.0f} mm"
    if res.width_mm > W or res.height_mm > H:
        try:
            from fabagent.laser import dijeljenje as DJ
            otv: dict = {}
            for o in (res.open_paths or []):
                otv.setdefault("CUT", []).append(o)
            table, prevelici = DJ.rasporedi(DJ.komadi(res.layers, otv), float(W), float(H))
            if table and not prevelici:
                return "PLOCE", f"{res.width_mm:.0f}x{res.height_mm:.0f} mm -> {len(table)} ploca"
            if prevelici:
                return "PAD", f"komad ne stane ni sam ({len(prevelici)})"
        except Exception as exc:                              # noqa: BLE001
            return "PAD", _razlog(exc)
    return "PAD", (res.report.blocking or ["nepoznato"])[0][:70]


def main() -> int:
    a = argparse.ArgumentParser(description="Provjera arhiva kroz FabAgent lanac")
    a.add_argument("folder", help="folder sa arhivama (pretrazuje se i u dubinu)")
    a.add_argument("--materijal", default="sperploca_3mm")
    a.add_argument("--po-arhivi", type=int, default=0,
                   help="najvise toliko crteza po arhivi (0 = svi)")
    a.add_argument("--dubina", type=int, default=3)
    a.add_argument("--izvjestaj", default="izvjestaj_arhiva.md")
    a.add_argument("--nastavi", action="store_true",
                   help="preskoci arhive koje su vec provjerene u prethodnom pokretanju")
    arg = a.parse_args()

    korijen = Path(arg.folder).expanduser()
    if not korijen.is_dir():
        print(f"GRESKA: '{korijen}' nije folder")
        return 2

    stanje_p = Path(arg.izvjestaj).with_suffix(".stanje.json")
    gotove: set[str] = set()
    if arg.nastavi and stanje_p.exists():
        try:
            gotove = set(json.loads(stanje_p.read_text(encoding="utf-8")))
            print(f"Nastavak: {len(gotove)} arhiva je vec provjereno.")
        except (OSError, json.JSONDecodeError):
            gotove = set()

    try:
        arhive = AR.arhive_u_folderu(korijen, dubina=arg.dubina)
    except FabError as exc:
        print(f"GRESKA: {exc}")
        return 2
    print(f"Nadjeno arhiva: {len(arhive)}\n")

    razlozi: collections.Counter = collections.Counter()
    primjeri: dict[str, str] = {}
    po_arhivi: list[dict] = []
    uk_ok = uk_ploce = uk_pad = uk_crteza = 0
    poc = time.time()

    for n, info in enumerate(arhive, 1):
        put = info["putanja"]
        if put in gotove:
            continue
        mb = info["bajtova"] / 1024 / 1024
        print(f"[{n}/{len(arhive)}] {info['ime']}  ({mb:.0f} MB)", flush=True)
        t0 = time.time()
        try:
            sp = AR.spisak(put)
        except (FabError, zipfile.BadZipFile) as exc:
            print(f"    arhiva se ne cita: {exc}")
            po_arhivi.append({"arhiva": info["ime"], "greska": str(exc)[:120]})
            razlozi["arhiva se ne cita"] += 1
            primjeri.setdefault("arhiva se ne cita", info["ime"])
            gotove.add(put)
            continue

        crtezi = [u for u in sp["unosi"]
                  if u["ext"] in CRTEZ_EXT and u["ext"] not in IMAGE_EXT
                  and u["bajtova"] <= MAX_BAJTOVA_CRTEZA]
        if arg.po_arhivi:
            crtezi = crtezi[:arg.po_arhivi]
        red = {"arhiva": info["ime"], "crteza": len(crtezi), "ok": 0, "ploce": 0, "pad": 0}

        for u in crtezi:
            uk_crteza += 1
            try:
                ime, podaci = AR.izvadi(put, u["ime"])
            except Exception as exc:                          # noqa: BLE001
                red["pad"] += 1
                uk_pad += 1
                r = f"vadjenje: {type(exc).__name__}"
                razlozi[r] += 1
                primjeri.setdefault(r, f"{info['ime']} / {u['ime']}")
                continue
            status, detalj = provjeri_crtez(ime, podaci, arg.materijal)
            if status == "OK":
                red["ok"] += 1
                uk_ok += 1
            elif status == "PLOCE":
                red["ploce"] += 1
                uk_ploce += 1
            else:
                red["pad"] += 1
                uk_pad += 1
                razlozi[detalj] += 1
                primjeri.setdefault(detalj, f"{info['ime']} / {ime}")

        red["sekundi"] = round(time.time() - t0, 1)
        po_arhivi.append(red)
        gotove.add(put)
        print(f"    crteza {red['crteza']}: OK {red['ok']}, ploce {red['ploce']}, "
              f"palo {red['pad']}  ({red['sekundi']} s)", flush=True)

        stanje_p.write_text(json.dumps(sorted(gotove), ensure_ascii=False), encoding="utf-8")
        _zapisi(arg.izvjestaj, po_arhivi, razlozi, primjeri,
                uk_crteza, uk_ok, uk_ploce, uk_pad, time.time() - poc)

    _zapisi(arg.izvjestaj, po_arhivi, razlozi, primjeri,
            uk_crteza, uk_ok, uk_ploce, uk_pad, time.time() - poc)
    print(f"\nGOTOVO. Crteza {uk_crteza}: OK {uk_ok}, na vise ploca {uk_ploce}, palo {uk_pad}")
    print(f"Izvjestaj: {Path(arg.izvjestaj).resolve()}")
    return 0


def _zapisi(putanja, po_arhivi, razlozi, primjeri, uk, ok, ploce, pad, sek) -> None:
    L = ["# Izvjestaj provjere arhiva", "",
         f"Crteza provjereno: **{uk}** · prolazi: **{ok}** · ide na vise ploca: **{ploce}** · "
         f"pada: **{pad}** · trajanje: {sek / 60:.1f} min", ""]
    if razlozi:
        L += ["## Razlozi padanja (od najcesceg)", "",
              "| puta | razlog | primjer |", "|---:|---|---|"]
        for r, c in razlozi.most_common():
            L.append(f"| {c} | {r} | `{primjeri.get(r, '')}` |")
        L.append("")
    L += ["## Po arhivi", "", "| arhiva | crteza | OK | ploce | palo | s |", "|---|---:|---:|---:|---:|---:|"]
    for r in po_arhivi:
        if "greska" in r:
            L.append(f"| {r['arhiva']} | — | — | — | — | _{r['greska']}_ |")
        else:
            L.append(f"| {r['arhiva']} | {r['crteza']} | {r['ok']} | {r['ploce']} | "
                     f"{r['pad']} | {r['sekundi']} |")
    Path(putanja).write_text("\n".join(L) + "\n", encoding="utf-8")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nPrekinuto. Pokreni ponovo sa --nastavi da nastavis odakle si stao.")
        raise SystemExit(1) from None
    except Exception:                                          # noqa: BLE001
        traceback.print_exc()
        raise SystemExit(1) from None

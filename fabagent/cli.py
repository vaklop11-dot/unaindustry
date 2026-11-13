"""Komandna linija FabAgent-a.

    fabagent laser list
    fabagent laser info <sablon>
    fabagent laser build <sablon> --params k=v ... --out fajl.svg [--use-defaults] [--preview]
    fabagent print list | info <sablon> | build <sablon> --params ... --out fajl.stl
    fabagent validate <fajl.svg|fajl.stl|fajl.3mf>

Izlazni kodovi: 0 OK, 2 pogresna upotreba CLI-a, 10 MissingParam, 11 OutOfRange,
12 InvalidGeometry, 13 InvalidParam, 14 ValidationFailed, 15 UnknownTemplate,
16 OutputError, 1 neocekivana greska.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import traceback

from fabagent import __version__
from fabagent.core import config as C
from fabagent.core.errors import FabError, InvalidParam, OutputError, UnknownTemplate, ValidationFailed
from fabagent.core.validator import parse_cli_params

EXIT_OK = 0
EXIT_UNEXPECTED = 1


def _templates(kind: str) -> dict:
    return C.LASER_TEMPLATES if kind == "laser" else C.PRINT_TEMPLATES


def _cmd_list(kind: str, as_json: bool) -> int:
    t = _templates(kind)
    if as_json:
        print(json.dumps({n: s.description for n, s in t.items()}, ensure_ascii=False, indent=2))
    else:
        for name, spec in t.items():
            print(f"{name:18s} {spec.description}")
    return EXIT_OK


def _describe(p) -> dict:
    d = {"type": p.type, "required": p.required, "description": p.description}
    if p.unit:
        d["unit"] = p.unit
    if p.min is not None or p.max is not None:
        d["range"] = [p.min, p.max]
    if p.choices:
        d["choices"] = list(p.choices)
    if p.min_len is not None:
        d["length"] = [p.min_len, p.max_len]
    if not p.required:
        d["default"] = p.default
    if p.applies_when:
        d["applies_when"] = {p.applies_when[0]: list(p.applies_when[1])}
    return d


def _cmd_info(kind: str, name: str, as_json: bool) -> int:
    t = _templates(kind)
    if name not in t:
        raise UnknownTemplate(f"nepoznat sablon '{name}', dostupno: {sorted(t)}")
    spec = t[name]
    info = {"template": name, "kind": kind, "description": spec.description,
            "params": {p.name: _describe(p) for p in spec.params}, "example": spec.example}
    if as_json:
        print(json.dumps(info, ensure_ascii=False, indent=2))
        return EXIT_OK
    print(f"{name} ({kind}): {spec.description}\n")
    for title, req in (("OBAVEZNI parametri", True), ("OPCIONI parametri (default samo uz --use-defaults)", False)):
        print(title + ":")
        for p in spec.params:
            if p.required != req:
                continue
            d = _describe(p)
            extra = []
            if "range" in d:
                extra.append(f"opseg [{p.min:g}, {p.max:g}]{(' ' + p.unit) if p.unit else ''}")
            if "choices" in d:
                extra.append(f"izbor {list(p.choices)}")
            if "length" in d:
                extra.append(f"duzina {p.min_len}..{p.max_len}")
            if not req:
                extra.append(f"default {p.default!r}")
            if p.applies_when:
                extra.append(f"samo kada {p.applies_when[0]} u {list(p.applies_when[1])}")
            print(f"  {p.name:24s} {p.type:5s} {p.description}; " + "; ".join(extra))
        print()
    ext = "svg" if kind == "laser" else "stl"
    args = " ".join(f"{k}={v}" for k, v in spec.example.items())
    print(f"Primjer:\n  fabagent {kind} build {name} --params {args} --out {name}.{ext}")
    return EXIT_OK


def _cmd_build(kind: str, args) -> int:
    raw = parse_cli_params(args.params)
    if kind == "laser":
        from fabagent.laser import svg_builder as builder
        result = builder.build(args.template, raw, args.use_defaults)
        paths = builder.write_outputs(result, args.out, preview=args.preview)
    else:
        if args.preview:
            raise InvalidParam("--preview je dostupan samo za laser")
        from fabagent.printer import stl_builder as builder
        result = builder.build(args.template, raw, args.use_defaults)
        paths = builder.write_outputs(result, args.out)
    m = result.metadata
    if args.json:
        print(json.dumps({"ok": True, "files": paths, "metadata": m}, ensure_ascii=False, indent=2, default=str))
        return EXIT_OK
    print(f"OK  {kind}/{args.template}")
    for k, v in paths.items():
        print(f"  {k:5s} {v}")
    if kind == "laser":
        d = m["document_mm"]
        print(f"  dokument {d['width']:.2f} x {d['height']:.2f} mm")
        for layer in C.LAYERS:
            s = m["layers"][layer]
            print(f"  {layer:8s} putanja {s['paths']}, kontura {s['rings']}, duzina {s['length_mm']:.1f} mm")
        print(f"  procjena vremena: {m['time_estimate_human']} (preset {m['material_preset']['key']})")
    else:
        size = m["dimensions_mm"]["size"]
        e = m["estimate"]
        print(f"  dimenzije {size[0]:.2f} x {size[1]:.2f} x {size[2]:.2f} mm, volumen {m['volume_mm3'] / 1000:.2f} cm3")
        print(f"  mesh: {m['mesh']['faces']} trouglova, watertight={m['mesh']['watertight']}, "
              f"min zid {m['mesh']['min_wall_mm']} mm")
        print(f"  {e['material']}: ~{e['mass_estimated_g']} g, ~{e['time_estimate_human']} (gruba procjena)")
    for w in m["warnings"]:
        print(f"  UPOZORENJE: {w}")
    return EXIT_OK


def _cmd_validate(path: str, as_json: bool) -> int:
    if not os.path.isfile(path):
        raise OutputError(f"fajl ne postoji: {path}")
    low = path.lower()
    if low.endswith(".svg"):
        from fabagent.laser.svg_validator import validate_svg_file
        report = validate_svg_file(path)
    elif low.endswith(".stl"):
        from fabagent.printer.stl_builder import validate_stl_file
        report = validate_stl_file(path)
    elif low.endswith(".3mf"):
        from fabagent.printer.stl_builder import validate_3mf_file
        report = validate_3mf_file(path)
    else:
        raise InvalidParam("podrzani formati za validaciju: .svg, .stl, .3mf")
    if as_json:
        print(json.dumps(dict(report, file=path), ensure_ascii=False, indent=2, default=str))
    elif report["ok"]:
        print(f"OK  {path} je validan")
    for w in report.get("warnings", []):
        print(f"  UPOZORENJE: {w}", file=sys.stderr)
    if not report["ok"]:
        raise ValidationFailed(path, report["errors"])
    return EXIT_OK


def make_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="fabagent", description="Deterministicki generator proizvodnih fajlova.")
    ap.add_argument("--version", action="version", version=f"fabagent {__version__}")
    ap.add_argument("-v", "--verbose", action="store_true", help="loguj svaku odluku na stderr")
    ap.add_argument("--json", action="store_true", help="izlaz u JSON formatu")
    sub = ap.add_subparsers(dest="command", required=True)
    for kind, title in (("laser", "laser (SVG)"), ("print", "3D stampa (STL/3MF)")):
        sp = sub.add_parser(kind, help=title)
        ssub = sp.add_subparsers(dest="action", required=True)
        ssub.add_parser("list", help="lista sablona")
        info = ssub.add_parser("info", help="parametri sablona")
        info.add_argument("template")
        b = ssub.add_parser("build", help="generisi fajlove")
        b.add_argument("template")
        b.add_argument("--params", nargs="+", default=[], metavar="KLJUC=VRIJEDNOST")
        b.add_argument("--out", required=True)
        b.add_argument("--use-defaults", action="store_true",
                       help="eksplicitno dozvoli default vrijednosti za nezadane opcione parametre")
        b.add_argument("--preview", action="store_true", help="(laser) i PNG preview")
    v = sub.add_parser("validate", help="validiraj postojeci SVG/STL/3MF")
    v.add_argument("file")
    return ap


def main(argv: list[str] | None = None) -> int:
    ap = make_parser()
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(levelname)s %(message)s", stream=sys.stderr)
    try:
        if args.command == "validate":
            return _cmd_validate(args.file, args.json)
        if args.action == "list":
            return _cmd_list(args.command, args.json)
        if args.action == "info":
            return _cmd_info(args.command, args.template, args.json)
        return _cmd_build(args.command, args)
    except FabError as exc:
        if args.json:
            print(json.dumps(dict(exc.to_dict(), ok=False, exit_code=exc.exit_code), ensure_ascii=False,
                             indent=2, default=str))
        print(f"GRESKA ({type(exc).__name__}): {exc}", file=sys.stderr)
        return exc.exit_code
    except Exception as exc:  # neocekivano - nikad tiho
        print(f"NEOCEKIVANA GRESKA: {type(exc).__name__}: {exc}", file=sys.stderr)
        if args.verbose:
            traceback.print_exc()
        return EXIT_UNEXPECTED


if __name__ == "__main__":
    sys.exit(main())

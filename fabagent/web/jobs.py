"""Buildovi iz web interfejsa: pregled (jobs/<id>/) i odobravanje (production/...).

Web sloj ne generise geometriju sam - zove postojece svg_builder / stl_builder,
koji vec validiraju ulaz i izlazne fajlove. Kod odobravanja se fajlovi sa diska
ponovo validiraju i provjerava im se SHA-256, pa se kopira tacno ono sto je
korisnik vidio u pregledu.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import secrets
import shutil
import unicodedata
from datetime import datetime
from pathlib import Path

from fabagent.core.errors import OutputError, ValidationFailed

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR_ENV = "FABAGENT_DATA_DIR"
JOB_ID_RE = re.compile(r"^[0-9]{8}-[0-9]{6}-[a-z0-9_]+-[0-9a-f]{6}$")
SLUG_SOURCE_PARAMS = ("tekst", "serijski_broj", "sadrzaj")
SLUG_MAX_LEN = 24
JOB_FILE = "job.json"
STATUS_PREVIEW = "preview"
STATUS_APPROVED = "approved"


def slugify(text: str) -> str:
    text = text.replace("Đ", "Dj").replace("đ", "dj")
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return text[:SLUG_MAX_LEN].strip("-")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _now() -> datetime:
    return datetime.now().replace(microsecond=0)


def _ploce_ako_ne_stane(res) -> list:
    """Ako crtez ne stane na sto, vrati listu dizajna po plocama; inace [].

    Koristi `laser/dijeljenje.py`: komad = vanjska kontura sa svime unutar nje.
    Geometrija se ne sijece i nista se ne skalira.
    """
    from fabagent.core import config as _C
    W, H = _C.LASER_WORK_AREA_MM
    if res.width_mm <= W + 1e-6 and res.height_mm <= H + 1e-6:
        return [], []
    from fabagent.laser import dijeljenje as _DJ
    otv = {}
    for o in (res.open_paths or []):
        otv.setdefault("CUT", []).append(o)
    komadi = _DJ.komadi(res.layers, otv)
    if not komadi:
        return [], []
    table, prevelici = _DJ.rasporedi(komadi, float(W), float(H))
    # Komad koji ne stane ni sam se NE skaliva i NE sijece - prijavljuje se
    # imenom i mjerama. Tiho ispustanje bi znacilo da operater izreze posao
    # kojem fali dio, a to se vidi tek na materijalu.
    return [_DJ.dizajn_table(t, float(W), float(H)) for t in table], prevelici


class JobStore:
    def __init__(self, root: str | os.PathLike | None = None):
        root = Path(root or os.environ.get(DATA_DIR_ENV) or PROJECT_ROOT)
        self.jobs_dir = root / "jobs"
        self.production_dir = root / "production"
        # Ishodi poslova (ucenje parametara). Jedan fajl, uz poslove.
        self.ishodi_putanja = root / "ishodi.json"
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        self.production_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------- pregled
    def create_preview(self, kind: str, template: str, raw: dict, use_defaults: bool) -> dict:
        """Gradi fajlove u jobs/<id>/. Svaka FabError greska se propagira neizmijenjena."""
        now = _now()
        job_id = f"{now:%Y%m%d-%H%M%S}-{template}-{secrets.token_hex(3)}"
        slug = next((slugify(str(raw[k])) for k in SLUG_SOURCE_PARAMS if raw.get(k)), "") or job_id[-6:]
        job_dir = self.jobs_dir / job_id
        job_dir.mkdir(parents=True)
        base = f"{template}_{slug}"
        try:
            if kind == "laser":
                record = self._laser(template, raw, use_defaults, job_dir, base)
            else:
                record = self._print(template, raw, use_defaults, job_dir, base)
        except Exception:
            shutil.rmtree(job_dir, ignore_errors=True)
            raise
        record.update(job_id=job_id, created=now.isoformat(), type=kind, template=template,
                      slug=slug, params_raw=dict(raw), use_defaults=use_defaults, status=STATUS_PREVIEW)
        self._save(record)
        return record

    def _laser(self, template, raw, use_defaults, job_dir: Path, base: str) -> dict:
        from fabagent.laser import svg_builder
        res = svg_builder.build(template, raw, use_defaults)
        paths = svg_builder.write_outputs(res, str(job_dir / f"{base}.svg"), preview=True)
        m = res.metadata
        preset = m["material_preset"]
        svgs = paths.get("svg_sheets", [paths["svg"]])
        pngs = paths.get("png_sheets", [paths["png"]])
        return {
            "params": m["params"],
            "params_defaulted": m["params_defaulted"],
            "outputs": [Path(p).name for p in svgs + pngs + [paths["json"]]],
            "sheets": [{"svg": Path(s).name, "png": Path(p).name,
                        "document_mm": sm["document_mm"], "time_s": sm["time_estimate_s"]["total"]}
                       for s, p, sm in zip(svgs, pngs, m.get("sheets") or [
                           {"document_mm": m["document_mm"], "time_estimate_s": m["time_estimate_s"]}])],
            "renders": [],
            "preset": preset["key"],
            "preset_name": preset["name"],
            "preset_verified": preset["verified"],
            "warnings": m["warnings"],
            "estimates": {"vrijeme_s": m["time_estimate_s"]["total"], "masa_g": None},
            "summary": {
                "document_mm": m["document_mm"],
                "layers": m["layers"],
                "time_human": m["time_estimate_human"],
                "time_by_layer_s": m["time_estimate_s"],
            },
        }

    def _print(self, template, raw, use_defaults, job_dir: Path, base: str) -> dict:
        from fabagent.printer import stl_builder
        from fabagent.web.render3d import render_views_png
        res = stl_builder.build(template, raw, use_defaults)
        paths = stl_builder.write_outputs(res, str(job_dir / f"{base}.stl"))
        renders = []
        for view, png in render_views_png(res.vertices, res.faces).items():
            name = f"view_{view}.png"
            (job_dir / name).write_bytes(png)
            renders.append(name)
        m = res.metadata
        est = m["estimate"]
        return {
            "params": m["params"],
            "params_defaulted": m["params_defaulted"],
            "outputs": [Path(paths[k]).name for k in ("stl", "3mf", "json")],
            "renders": renders,
            "preset": m["params"]["materijal"],
            "preset_name": m["params"]["materijal"],
            "preset_verified": None,
            "warnings": m["warnings"],
            "estimates": {"vrijeme_s": est["time_estimate_s"], "masa_g": est["mass_estimated_g"]},
            "summary": {
                "size_mm": m["dimensions_mm"]["size"],
                "volume_cm3": round(m["volume_mm3"] / 1000, 2),
                "mass_g": est["mass_estimated_g"],
                "mass_by_material_g": m["estimate_by_material"],
                "time_human": est["time_estimate_human"],
                "time_min": round(est["time_estimate_s"] / 60),
                "mesh": m["mesh"],
                "bodies": [{"name": b["name"], "min_wall_mm": b["min_wall_mm"]} for b in m["bodies"]],
            },
        }

    # ------------------------------------------------------------- upload SVG
    def create_upload(self, filename: str, data: bytes, materijal: str, *, threshold: int | None = None,
                      width_mm: float | None = None, dxf_units: str | None = None,
                      trace: dict | None = None) -> dict:
        """Uploadovani SVG -> svg_fixer -> (ako nema blokirajucih gresaka) produkcijski SVG.
        Odbijeni fajlovi (raster, ne-SVG...) dizu ValidationFailed i ne prave job."""
        from fabagent.core import config as C
        from fabagent.core.decisions import DecisionLog
        from fabagent.core.errors import InvalidParam, MissingParam
        from fabagent.core.svg_fixer import fix_svg
        from fabagent.core.validator import ValidatedParams
        from fabagent.laser import svg_builder
        from fabagent.laser.design import estimate_time, layer_stats
        from fabagent.laser.presets import get_preset

        if not materijal:
            raise MissingParam("svg_upload", ["materijal"])
        if materijal not in C.LASER_MATERIALS_ALL:
            raise InvalidParam(f"materijal: '{materijal}' nije dozvoljeno, izbor: {list(C.LASER_MATERIALS_ALL)}")
        preset = get_preset(materijal)
        from fabagent.core.converter import convert
        conv = convert(filename, data, threshold=threshold, width_mm=width_mm, dxf_units=dxf_units, trace=trace)
        res = fix_svg(conv.svg, Path(filename).stem + ".svg")
        rep = res.report
        # Crtez veci od stola nije greska nego posao za vise ploca. Komadi se
        # NE sijeku - svaki ostaje cijel, samo se rasporede na onoliko ploca
        # koliko treba. Bez ovoga je svaki paket iz arhive zavrsavao kao
        # "dokument je veci od radne povrsine" i nije se mogao odobriti.
        ploce, prevelici = _ploce_ako_ne_stane(res)
        if prevelici:
            rep.blocking.append(
                f"{len(prevelici)} komad(a) ne stane na radnu povrsinu ni sam: "
                + ", ".join(f"{k.sirina:.0f}x{k.visina:.0f} mm" for k in prevelici[:5])
                + " - takav komad se ne moze rezati na ovoj masini bez smanjivanja crteza")
        rep.fixes[:0] = [f"Konverzija {conv.source_format.upper()} -> SVG: {n}" for n in conv.notes]
        rep.blocking[:0] = list(conv.blocking)
        if res.layers["CUT"] and preset.cut is None:
            rep.blocking.append(f"materijal '{materijal}' se ne moze rezati, a fajl ima CUT konture")
        log = DecisionLog()
        log.record("upload", f"fajl '{filename}' ({len(data)} B), materijal '{materijal}'", **rep.stats)
        for f in rep.fixes:
            log.record("popravak", f)
        # Upozorenje se gradi po slojevima koje preset stvarno ima, a ne po jednoj
        # zastavici: gravura i rez mogu biti izmjereni test-mrezom dok ucrtavanje
        # nije. Jedna zajednicka poruka tada vice na posao koji je izmjeren, pa je
        # operater prestane citati - a to je gore nego da je nema.
        nemjereno = [ime for ime, op in (("rez", preset.cut),
                                         ("gravura", preset.engrave),
                                         ("ucrtavanje", preset.score))
                     if op is not None and not getattr(op, "izmjereno", False)]
        if nemjereno:
            log.warn(f"preset '{preset.key}': {', '.join(nemjereno)} nije mjereno na "
                     f"ovoj masini - uradi test-grid prije serije")
        warnings = rep.warnings + log.warnings

        now = _now()
        job_id = f"{now:%Y%m%d-%H%M%S}-svg_upload-{secrets.token_hex(3)}"
        slug = slugify(Path(filename).stem) or job_id[-6:]
        job_dir = self.jobs_dir / job_id
        job_dir.mkdir(parents=True)
        try:
            ext = Path(filename).suffix.lower().replace(".jpeg", ".jpg")
            source_name = f"izvor{ext}"
            (job_dir / source_name).write_bytes(data)
            sources = [source_name]
            if conv.source_format == "svg":
                original_view = source_name
            elif conv.preview_source is not None:            # DXF/PDF: SVG prije popravka
                (job_dir / "izvor_konvertovano.svg").write_bytes(conv.preview_source)
                sources.append("izvor_konvertovano.svg")
                original_view = "izvor_konvertovano.svg"
            else:                                            # slika: prikazuje se sama slika
                original_view = source_name
            bitmap_view = None
            if conv.bitmap_png is not None:                  # medjukorak obrade slike
                (job_dir / "obradjena_bitmapa.png").write_bytes(conv.bitmap_png)
                sources.append("obradjena_bitmapa.png")
                bitmap_view = "obradjena_bitmapa.png"
                if original_view.endswith(".pdf"):           # PDF sa slikom se ne moze prikazati kao <img>
                    original_view = bitmap_view
            design_for_stats = res.design
            outputs, renders, sheets = [], [], []
            if ploce:
                from fabagent.laser.svg_builder import design_to_svg
                sheets = []
                for i, d_ploca in enumerate(ploce, 1):
                    ime = f"{slug}_ploca{i}.svg"
                    (job_dir / ime).write_text(
                        design_to_svg(d_ploca, f"{slug} ploca {i}",
                                      f"FabAgent: ploca {i} od {len(ploce)}"),
                        encoding="utf-8")
                    t_i = estimate_time(d_ploca, preset)
                    sheets.append({"svg": ime, "png": None,
                                   "document_mm": {"width": round(d_ploca.width, 4),
                                                   "height": round(d_ploca.height, 4)},
                                   "time_s": None if t_i is None else t_i["total"]})
                    outputs.append(ime)
                shown = outputs[0]
                design_for_stats = ploce[0]
                times = estimate_time(ploce[0], preset)
                rep.blocking = [b for b in rep.blocking if "radne povrsine" not in b]
                rep.fixes.append(f"Crtez je veci od stola: razdijeljen na {len(ploce)} ploce "
                                 f"{C.LASER_WORK_AREA_MM[0]:g} x {C.LASER_WORK_AREA_MM[1]:g} mm, "
                                 f"bez sjecenja komada")
                renders = []
            elif res.production_ready and not rep.blocking:
                params = ValidatedParams("svg_upload", {"fajl": filename, "materijal": materijal})
                times = estimate_time(res.design, preset)
                metadata = {
                    "kind": "laser", "template": "svg_upload", "source_file": filename,
                    "machine": C.LASER_MACHINE_NAME, "units": C.UNITS, "params": params.values,
                    "params_defaulted": [], "document_mm": {"width": round(res.width_mm, 4),
                                                            "height": round(res.height_mm, 4)},
                    "layers": layer_stats(res.design), "layer_colors": C.LAYER_COLORS,
                    "material_preset": preset.as_dict(), "time_estimate_s": times,
                    "fix_report": rep.as_dict(), "warnings": warnings, "decision_log": log.entries,
                }
                result = svg_builder.LaserResult("svg_upload", params, preset, res.design, res.svg, metadata, log)
                paths = svg_builder.write_outputs(result, str(job_dir / f"{slug}.svg"), preview=True)
                outputs = [Path(paths[k]).name for k in ("svg", "png", "json")]
                shown = outputs[0]
            else:
                (job_dir / "pregled.svg").write_text(res.svg, encoding="utf-8")
                renders = ["pregled.svg"]
                shown = "pregled.svg"
                times = None
        except Exception:
            shutil.rmtree(job_dir, ignore_errors=True)
            raise
        from fabagent.laser.design import LaserDesign
        stats_design = design_for_stats or LaserDesign(res.width_mm, res.height_mm)
        if design_for_stats is None:
            for layer, rings in res.layers.items():
                stats_design.add(layer, rings, layer.lower())
        human = "-" if times is None else f"{int(times['total'] // 60)} min {int(round(times['total'] % 60))} s"
        record = {
            "job_id": job_id, "created": now.isoformat(), "type": "upload", "template": "svg_upload",
            "slug": slug, "params": {"fajl": filename, "materijal": materijal},
            "params_raw": {"fajl": filename, "materijal": materijal}, "params_defaulted": [],
            "use_defaults": False, "status": STATUS_PREVIEW,
            "outputs": outputs, "renders": renders, "sources": sources, "original_view": original_view,
            "bitmap_view": bitmap_view,
            "conversion": {"format": conv.source_format, "notes": conv.notes,
                           "threshold": threshold, "width_mm": width_mm, "dxf_units": dxf_units,
                           "trace_raw": {k: v for k, v in (trace or {}).items() if v not in (None, "")},
                           "trace_options": conv.trace_options},
            "sheets": (sheets if ploce else
                       [{"svg": shown, "png": None,
                         "document_mm": {"width": res.width_mm, "height": res.height_mm},
                         "time_s": None if times is None else times["total"]}]),
            "preset": preset.key, "preset_name": preset.name, "preset_verified": preset.verified,
            "warnings": warnings, "fix_report": rep.as_dict(), "blocking": list(rep.blocking),
            "estimates": {"vrijeme_s": None if times is None else times["total"], "masa_g": None},
            "summary": {"document_mm": {"width": round(res.width_mm, 3), "height": round(res.height_mm, 3)},
                        "layers": layer_stats(stats_design), "time_human": human,
                        "time_by_layer_s": times or {"ENGRAVE": "-", "CUT": "-"}},
        }
        self._save(record)
        return record

    # ------------------------------------------------------------- citanje
    def _dir(self, job_id: str) -> Path:
        if not JOB_ID_RE.match(job_id or ""):
            raise OutputError(f"neispravan ID joba: {job_id!r}")
        d = self.jobs_dir / job_id
        if not (d / JOB_FILE).is_file():
            raise OutputError(f"job {job_id} ne postoji")
        return d

    def get(self, job_id: str) -> dict:
        return json.loads((self._dir(job_id) / JOB_FILE).read_text(encoding="utf-8"))

    def list(self) -> list[dict]:
        jobs = []
        for d in self.jobs_dir.iterdir():
            f = d / JOB_FILE
            if d.is_dir() and JOB_ID_RE.match(d.name) and f.is_file():
                jobs.append(json.loads(f.read_text(encoding="utf-8")))
        return sorted(jobs, key=lambda j: (j["created"], j["job_id"]), reverse=True)

    def file_path(self, job_id: str, name: str) -> Path:
        """Samo fajlovi koje je job sam napravio (zastita od ../ putanja)."""
        rec = self.get(job_id)
        if name not in rec["outputs"] + rec["renders"] + rec.get("sources", []):
            raise OutputError(f"fajl {name!r} ne pripada jobu {job_id}")
        return self._dir(job_id) / name

    def _save(self, record: dict) -> None:
        path = self.jobs_dir / record["job_id"] / JOB_FILE
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)

    # ------------------------------------------------------------- odobravanje
    def _revalidate(self, rec: dict) -> None:
        d = self._dir(rec["job_id"])
        meta_name = next(n for n in rec["outputs"] if n.endswith(".json"))
        meta = json.loads((d / meta_name).read_text(encoding="utf-8"))
        for entry in meta["files"].values():
            p = d / Path(entry["path"]).name
            if not p.is_file() or _sha256(p) != entry["sha256"]:
                raise ValidationFailed(str(p), ["fajl je izmijenjen ili nedostaje nakon pregleda (SHA-256)"])
        if rec["type"] == "raster":
            from fabagent.core.raster import power_map_from_png
            png = next(n for n in rec["outputs"] if n.endswith(".png"))
            pm = power_map_from_png((d / png).read_bytes())
            if list(pm.shape[::-1]) != rec["raster"]["pixels"]:
                raise ValidationFailed(png, ["dimenzije raster slike se ne poklapaju sa jobom"])
            return
        if rec["type"] == "litho":
            from fabagent.printer import mesh_io
            from fabagent.printer.mesh_validator import validate_mesh
            stl = next(n for n in rec["outputs"] if n.endswith(".stl"))
            v, f = mesh_io.read_stl((d / stl).read_bytes())
            z = v[:, 2]
            t_min = rec["params"]["min_debljina_mm"]
            wall = {"min_mm": float(z[z > 0].min()), "method": "analiticki (mapa visina)", "misses": 0}
            report = validate_mesh(v, f, stl, min_wall=t_min, wall_override=wall)
            if not report["ok"]:
                raise ValidationFailed(stl, report["errors"])
            return
        if rec["type"] in ("laser", "upload"):
            from fabagent.laser.svg_validator import validate_svg_file
            for svg in (n for n in rec["outputs"] if n.endswith(".svg")):
                report = validate_svg_file(str(d / svg))
                if not report["ok"]:
                    raise ValidationFailed(svg, report["errors"])
        else:
            from fabagent.printer.stl_builder import validate_3mf_file, validate_stl_file
            for name, fn in ((".stl", validate_stl_file), (".3mf", validate_3mf_file)):
                f = next(n for n in rec["outputs"] if n.endswith(name))
                report = fn(str(d / f))
                if not report["ok"]:
                    raise ValidationFailed(f, report["errors"])

    def approve(self, job_id: str) -> dict:
        rec = self.get(job_id)
        if rec["status"] == STATUS_APPROVED:
            return rec          # vec odobren - ne pravi duplikat
        if rec.get("blocking"):
            raise ValidationFailed(rec["job_id"], ["fajl ima nerijesene greske"] + rec["blocking"])
        if not rec["outputs"]:
            raise ValidationFailed(rec["job_id"], ["nema produkcijskih fajlova"])
        self._revalidate(rec)
        now = _now()
        name = f"{now:%Y-%m-%d_%H%M}_{rec['template']}_{rec['slug']}"
        target = self.production_dir / name
        n = 2
        while target.exists():
            target = self.production_dir / f"{name}_{n}"
            n += 1
        target.mkdir(parents=True)
        src = self._dir(job_id)
        for f in rec["outputs"]:
            shutil.copy2(src / f, target / f)
        job = {
            "job_id": rec["job_id"],
            "timestamp": now.isoformat(),
            "type": rec["type"],
            "template": rec["template"],
            "params": rec["params"],
            "params_defaulted": rec["params_defaulted"],
            "preset": rec["preset"],
            "preset_verified": rec["preset_verified"],
            "warnings": rec["warnings"],
            "estimates": rec["estimates"],
            "outputs": rec["outputs"],
            "checksums_sha256": {f: _sha256(target / f) for f in rec["outputs"]},
            "fix_report": rec.get("fix_report"),
            "layer_settings": rec.get("layer_settings"),
            "preview_created": rec["created"],
        }
        (target / JOB_FILE).write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")
        rec.update(status=STATUS_APPROVED, approved=now.isoformat(), production_dir=str(target))
        self._save(rec)
        return rec

    # ============================================================= slika -> raster / 3D
    def _new_dir(self, template: str, filename: str):
        now = _now()
        job_id = f"{now:%Y%m%d-%H%M%S}-{template}-{secrets.token_hex(3)}"
        slug = slugify(Path(filename).stem) or job_id[-6:]
        job_dir = self.jobs_dir / job_id
        job_dir.mkdir(parents=True)
        return now, job_id, slug, job_dir

    @staticmethod
    def _num(raw: dict, k: str, kind=float):
        from fabagent.core.errors import InvalidParam
        t = str(raw.get(k) or "").strip()
        if t == "":
            return None
        try:
            if "," in t:
                raise ValueError
            return kind(t)
        except ValueError:
            raise InvalidParam(f"{k}: '{t}' nije broj (koristi tacku)") from None

    def create_raster(self, filename: str, data: bytes, materijal: str, raw: dict) -> dict:
        """Slika -> raster graviranje (threshold / dither / grayscale) -> PNG mape snage + JSON."""
        from fabagent.core import config as C
        from fabagent.core import raster as R
        from fabagent.core.errors import InvalidParam, MissingParam
        from fabagent.core.image_trace import load_gray
        from fabagent.laser.presets import get_preset

        if not materijal:
            raise MissingParam("raster", ["materijal"])
        if materijal not in C.LASER_MATERIALS_ALL:
            raise InvalidParam(f"materijal: '{materijal}' nije dozvoljeno, izbor: {list(C.LASER_MATERIALS_ALL)}")
        preset = get_preset(materijal)
        notes = []
        width = self._num(raw, "sirina_mm")
        if width is None:
            raise MissingParam("raster", ["sirina_mm"])
        interval = self._num(raw, "interval_mm")
        if interval is None:
            interval = preset.engrave.line_interval_mm
            notes.append(f"interval linija {interval:g} mm iz preseta {preset.key}")
        opts = R.RasterOptions(mode=str(raw.get("rezim_slike") or ""), width_mm=width, interval_mm=interval,
                               negative=str(raw.get("negativ") or "").lower() in ("true", "on", "1"),
                               threshold=self._num(raw, "prag", int), algorithm=(raw.get("algoritam") or None),
                               power_min_pct=self._num(raw, "min_snaga_posto") or 0.0)
        pmap = R.power_map(load_gray(data), opts)
        ny, nx = pmap.shape
        if not (pmap > 0).any():
            raise ValidationFailed("slika", ["nakon obrade nema nijednog piksela za graviranje"])
        now, job_id, slug, job_dir = self._new_dir("slika_raster", filename)
        ext = Path(filename).suffix.lower().replace(".jpeg", ".jpg")
        try:
            (job_dir / f"izvor{ext}").write_bytes(data)
            png_name, json_name = f"{slug}_raster.png", f"{slug}_raster.json"
            (job_dir / png_name).write_bytes(R.preview_png(pmap))
            back = R.power_map_from_png((job_dir / png_name).read_bytes())
            if back.shape != pmap.shape:
                raise ValidationFailed(png_name, ["upisana raster slika nije ispravna"])
            t = R.estimate_time_s(pmap, interval, preset.engrave.speed_mm_s) * preset.engrave.passes
            meta = {"kind": "raster", "source_file": filename, "options": opts.as_dict(),
                    "pixels": [nx, ny], "size_mm": [round(nx * interval, 3), round(ny * interval, 3)],
                    "material_preset": preset.as_dict(), "time_estimate_s": t, "notes": notes,
                    "files": {"png": {"path": png_name, "sha256": _sha256(job_dir / png_name)}}}
            (job_dir / json_name).write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            shutil.rmtree(job_dir, ignore_errors=True)
            raise
        # Rasterski posao koristi SAMO sloj ENGRAVE, pa ga i ocjenjuje po njemu.
        # Materijal u cjelini moze biti neverifikovan zbog reza, a gravura ipak
        # izmjerena test-mrezom - tada upozorenje na gravuru ne stoji.
        gravura_mjerena = bool(getattr(preset.engrave, "izmjereno", False))
        warnings = [] if (preset.verified or gravura_mjerena) else [
            f"preset '{preset.key}' NIJE verifikovan na masini - uradi test-grid prije produkcije"]
        rec = {
            "job_id": job_id, "created": now.isoformat(), "type": "raster", "template": "slika_raster",
            "slug": slug, "status": STATUS_PREVIEW, "use_defaults": False,
            "params": {"fajl": filename, "materijal": materijal, **opts.as_dict()},
            "params_raw": {k: v for k, v in raw.items() if v not in (None, "")}, "params_defaulted": [],
            "outputs": [png_name, json_name], "renders": [], "sources": [f"izvor{ext}"],
            "raster": {"pixels": [nx, ny], "interval_mm": interval, "png": png_name},
            "preset": preset.key, "preset_name": preset.name,
            "preset_verified": preset.verified or gravura_mjerena,
            "warnings": warnings, "notes": notes,
            "estimates": {"vrijeme_s": t, "masa_g": None},
            "summary": {"document_mm": {"width": round(nx * interval, 3), "height": round(ny * interval, 3)},
                        "time_human": f"{int(t // 60)} min {int(round(t % 60))} s",
                        "pixels": [nx, ny], "burn_ratio": round(float((pmap > 0).mean()), 4)},
        }
        self._save(rec)
        return rec

    def create_test_mreza(self, materijal: str, debljina_mm, celije, nc: str, svg_tekst: str,
                          gravura: bool) -> dict:
        """Test mreza kao posao: ulazi u Istoriju i otvara se na laseru.

        Bez ovoga je mreza bila samo fajl za preuzimanje - operater bi je slao
        rucno, a ishod po celiji ne bi imao gdje da se zakaci.
        """
        import json as _json
        from fabagent.laser.presets import get_preset

        now, job_id, slug, job_dir = self._new_dir("test_mreza", materijal or "mreza")
        nc_ime, svg_ime, cel_ime = f"{slug}.nc", f"{slug}.svg", "celije.json"
        (job_dir / nc_ime).write_text(nc, encoding="utf-8")
        (job_dir / svg_ime).write_text(svg_tekst, encoding="utf-8")
        (job_dir / cel_ime).write_text(_json.dumps(celije, ensure_ascii=False, indent=1),
                                       encoding="utf-8")
        try:
            preset = get_preset(materijal)
            p_ime, p_ver = preset.name, preset.verified
        except Exception:                                  # noqa: BLE001
            p_ime, p_ver = materijal, False
        brzine = sorted({c["brzina_mm_min"] for c in celije})
        snage = sorted({c["snaga_pct"] for c in celije})
        rec = {
            "job_id": job_id, "created": now.isoformat(), "type": "test_mreza",
            "template": "test_mreza", "slug": slug, "status": STATUS_PREVIEW,
            "use_defaults": False,
            "params": {"materijal": materijal, "debljina_mm": debljina_mm,
                       "brzine": brzine, "snage": snage,
                       "prolaza": celije[0]["prolaza"] if celije else 1,
                       "gravura": gravura},
            "params_raw": {}, "params_defaulted": [],
            "outputs": [nc_ime, svg_ime, cel_ime], "renders": [], "sources": [],
            "celije": celije, "gcode": nc_ime,
            "preset": materijal, "preset_name": p_ime, "preset_verified": p_ver,
            "warnings": ["Test mreza: svaki kvadratic ima svoje parametre. Start pritiska operater."],
            "notes": [f"{len(celije)} kvadratica: {len(snage)} snaga x {len(brzine)} brzina"],
            "estimates": {"vrijeme_s": None, "masa_g": None},
            "summary": {"celija": len(celije), "brzine": brzine, "snage": snage},
        }
        self._save(rec)
        return rec

    def create_litho(self, filename: str, data: bytes, raw: dict) -> dict:
        """Slika -> mapa visina -> STL/3MF (litofanija ili reljef)."""
        from fabagent.core import config as C
        from fabagent.core.errors import InvalidParam, MissingParam
        from fabagent.core.image_to_3d import build_lithophane
        from fabagent.printer import mesh_io
        from fabagent.printer.stl_builder import estimate_print
        from fabagent.web.render3d import render_views_png

        material = str(raw.get("filament") or "")
        if not material:
            raise MissingParam("litofanija", ["filament"])
        if material not in C.FILAMENTS:
            raise InvalidParam(f"filament: '{material}' nije dozvoljeno, izbor: {list(C.FILAMENTS)}")
        negative = str(raw.get("negativ") or "").lower() in ("true", "on", "1")
        params = {"sirina_mm": self._num(raw, "sirina_mm"), "min_debljina_mm": self._num(raw, "min_debljina_mm"),
                  "max_debljina_mm": self._num(raw, "max_debljina_mm"), "nacin_3d": raw.get("nacin_3d") or None,
                  "negativ": negative, "filament": material}
        body, rep, info = build_lithophane(data, params["sirina_mm"], params["min_debljina_mm"],
                                           params["max_debljina_mm"], params["nacin_3d"], negative)
        now, job_id, slug, job_dir = self._new_dir("litofanija", filename)
        ext = Path(filename).suffix.lower().replace(".jpeg", ".jpg")
        st = rep["stats"]
        try:
            (job_dir / f"izvor{ext}").write_bytes(data)
            stl, tmf, js = f"{slug}_lito.stl", f"{slug}_lito.3mf", f"{slug}_lito.json"
            (job_dir / stl).write_bytes(mesh_io.stl_bytes(body.vertices, body.faces, "litofanija"))
            (job_dir / tmf).write_bytes(mesh_io.threemf_bytes([body]))
            v2, f2 = mesh_io.read_stl((job_dir / stl).read_bytes())
            if len(f2) != len(body.faces):
                raise ValidationFailed(stl, ["upisani STL nema isti broj trouglova"])
            mesh_io.read_3mf((job_dir / tmf).read_bytes())
            renders = []
            for view, png in render_views_png(body.vertices, body.faces).items():
                (job_dir / f"view_{view}.png").write_bytes(png)
                renders.append(f"view_{view}.png")
            est = estimate_print(st["volume_mm3"], st["area_mm2"], st["bbox_mm"]["size"][2], material,
                                 100.0, C.LITHO_LAYER_HEIGHT_MM)
            meta = {"kind": "litho", "source_file": filename, "params": params, "info": info, "mesh": st,
                    "estimate": est, "files": {k: {"path": n, "sha256": _sha256(job_dir / n)}
                                               for k, n in (("stl", stl), ("3mf", tmf))}}
            (job_dir / js).write_text(json.dumps(meta, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        except Exception:
            shutil.rmtree(job_dir, ignore_errors=True)
            raise
        mass_by = {m: estimate_print(st["volume_mm3"], st["area_mm2"], st["bbox_mm"]["size"][2], m, 100.0,
                                     C.LITHO_LAYER_HEIGHT_MM)["mass_estimated_g"] for m in C.FILAMENTS}
        rec = {
            "job_id": job_id, "created": now.isoformat(), "type": "litho", "template": "litofanija",
            "slug": slug, "status": STATUS_PREVIEW, "use_defaults": False, "params": params,
            "params_raw": {k: v for k, v in raw.items() if v not in (None, "")}, "params_defaulted": [],
            "outputs": [stl, tmf, js], "renders": renders, "sources": [f"izvor{ext}"],
            "preset": material, "preset_name": material, "preset_verified": None,
            "warnings": rep["warnings"],
            "estimates": {"vrijeme_s": est["time_estimate_s"], "masa_g": est["mass_estimated_g"]},
            "summary": {"size_mm": st["bbox_mm"]["size"], "volume_cm3": round(st["volume_mm3"] / 1000, 2),
                        "mass_g": est["mass_estimated_g"], "mass_by_material_g": mass_by,
                        "time_human": est["time_estimate_human"], "time_min": round(est["time_estimate_s"] / 60),
                        "mesh": {"watertight": st["watertight"], "winding_consistent": st["winding_consistent"],
                                 "non_manifold_edges": st["non_manifold_edges"], "faces": st["faces"],
                                 "bodies": st["bodies"], "min_wall_mm": st["wall_thickness"]["min_mm"]},
                        "bodies": [{"name": "litofanija", "min_wall_mm": st["wall_thickness"]["min_mm"]}],
                        "info": info},
        }
        self._save(rec)
        return rec

    # ============================================================= slojevi i G-code
    @staticmethod
    def is_laser_job(rec: dict) -> bool:
        return rec.get("type") == "test_mreza" or (rec["type"] in ("laser", "upload", "raster"))

    def designs(self, rec: dict) -> list:
        if rec.get("type") == "test_mreza":
            return []          # mreza nema dizajn: parametri su u samom G-code-u
        from fabagent.core.gcode import design_from_svg
        d = self._dir(rec["job_id"])
        svgs = [sh["svg"] for sh in rec.get("sheets") or []] or [n for n in rec["outputs"] if n.endswith(".svg")]
        return [design_from_svg((d / n).read_text(encoding="utf-8")) for n in svgs if n in rec["outputs"]]

    def used_layers(self, rec: dict) -> list:
        from fabagent.core import config as C
        if rec["type"] == "raster":
            return ["ENGRAVE"]
        used = set()
        for des in self.designs(rec):
            used |= {layer for layer in C.LAYERS if des.rings(layer)}
        return [layer for layer in C.LAYERS if layer in used]

    def on_bed(self, rec: dict) -> bool:
        if rec.get("type") == "test_mreza":
            return True        # mreza je vec na apsolutnim koordinatama stola
        """Dizajn je vec rasporedjen na cijelom stolu (editor, ploce iz nestinga) -> pomak je 0, 0."""
        from fabagent.core import config as C
        if rec.get("on_bed"):
            return True
        if rec["type"] == "raster":
            return False
        W, H = C.LASER_WORK_AREA_MM
        return all(abs(d.width - W) < 1e-6 and abs(d.height - H) < 1e-6 for d in self.designs(rec))

    def layer_settings(self, rec: dict) -> tuple:
        """(postavke, izvor). Sacuvane postavke imaju prednost; inace polazne iz preseta materijala."""
        from fabagent.core.gcode import preset_layer_settings
        from fabagent.laser.presets import get_preset
        if rec.get("layer_settings"):
            return rec["layer_settings"], "sacuvano u job.json"
        pre = preset_layer_settings(get_preset(rec["preset"]))
        if rec["type"] == "raster":
            pre["ENGRAVE"] = dict(pre["ENGRAVE"], interval_mm=rec["raster"]["interval_mm"])
        used = self.used_layers(rec)
        return {k: v for k, v in pre.items() if k in used}, f"preset {rec['preset']}"

    def save_layer_settings(self, job_id: str, raw: dict) -> dict:
        from fabagent.core.errors import InvalidParam
        from fabagent.core.gcode import parse_layer_settings
        rec = self.get(job_id)
        if not self.is_laser_job(rec):
            raise InvalidParam("postavke slojeva postoje samo za laserske jobove")
        parsed = parse_layer_settings(raw, self.used_layers(rec))
        if rec["type"] == "raster":
            if parsed["ENGRAVE"].mode != "Engrave":
                raise InvalidParam("ENGRAVE.mode: raster slika se moze samo gravirati (Engrave)")
            if abs(parsed["ENGRAVE"].interval_mm - rec["raster"]["interval_mm"]) > 1e-9:
                raise InvalidParam(f"ENGRAVE.interval_mm: raster je napravljen za interval "
                                   f"{rec['raster']['interval_mm']:g} mm - za drugi interval ponovo obradi sliku")
        rec["layer_settings"] = {k: v.as_dict() for k, v in parsed.items()}
        self._save(rec)
        if rec.get("production_dir"):
            pj = Path(rec["production_dir"]) / JOB_FILE
            if pj.is_file():
                job = json.loads(pj.read_text(encoding="utf-8"))
                job["layer_settings"] = rec["layer_settings"]
                pj.write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")
        return rec

    SIDRA = ("dolje-lijevo", "dolje-sredina", "dolje-desno",
             "sredina-lijevo", "sredina", "sredina-desno",
             "gore-lijevo", "gore-sredina", "gore-desno")

    @staticmethod
    def _pomak_po_sidru(des, sidro: str, tacka) -> tuple:
        """Pomak tako da zadano SIDRO crteza padne na zadanu tacku stola.

        Kao "Job Origin" u LightBurn-u: operater bira hoce li zadana tacka biti
        donji lijevi ugao posla, sredina, gornji desni... Ranije je crtez uvijek
        isao gornjim lijevim uglom dokumenta i operater je racunao sam.
        """
        import numpy as _np

        from fabagent.core.errors import InvalidParam as _IP
        if sidro not in JobStore.SIDRA:
            raise _IP(f"sidro: '{sidro}' nije dozvoljeno, izbor: {list(JobStore.SIDRA)}")
        prsteni = ([r for it in des.items for r in it.rings]
                   + [o for it in des.items for o in it.otvorene])
        if not prsteni:
            return tuple(tacka)
        t = _np.vstack([_np.asarray(r, dtype=float) for r in prsteni])
        x0, y0 = float(t[:, 0].min()), float(t[:, 1].min())
        x1, y1 = float(t[:, 0].max()), float(t[:, 1].max())
        if sidro == "sredina":
            vert = horiz = "sredina"
        else:
            vert, _, horiz = sidro.partition("-")
        px = {"lijevo": x0, "sredina": (x0 + x1) / 2.0, "desno": x1}[horiz]
        # Y u dokumentu raste nadolje, a na stolu nagore: "dolje" je veci Y.
        py = {"gore": y0, "sredina": (y0 + y1) / 2.0, "dolje": y1}[vert]
        return (float(tacka[0]) - px, float(tacka[1]) - py)

    def make_gcode(self, job_id: str, offset, sheet: int = 1, frame: bool = False,
                   frame_speed_mm_s: float | None = None, sidro: str = "") -> dict:
        """G-code za job (ploca `sheet`). Pomak je obavezan osim za raspored na cijelom stolu."""
        import numpy as np

        from fabagent.core import gcode as G
        from fabagent.core import raster as R
        from fabagent.core.errors import InvalidParam, MissingParam
        from fabagent.laser.design import LaserDesign
        rec = self.get(job_id)
        if rec.get("type") == "test_mreza":
            # Test mreza nema dizajn ni slojeve: svaki kvadratic ima svoje
            # parametre upisane u sam G-code. Zato se tekst cita iz fajla
            # kakav jeste, bez ponovnog racunanja i bez pomaka.
            tekst = self.file_path(job_id, rec["gcode"]).read_text(encoding="utf-8")
            if frame:
                from fabagent.laser import test_mreza as TM
                tekst = TM.gcode_okvir(rec.get("celije") or [])
            return {"text": tekst, "file": None, "path": None,
                    "stats": {"celija": len(rec.get("celije") or []),
                              "lines": tekst.count("\n"), "layers": ["TEST"]}}
        if not self.is_laser_job(rec):
            raise InvalidParam("G-code postoji samo za laserske jobove")
        if rec.get("blocking"):
            raise ValidationFailed(job_id, ["job ima nerijesene greske"] + rec["blocking"])
        if self.on_bed(rec):
            if offset is not None and tuple(offset) != (0.0, 0.0):
                raise InvalidParam("pozicija: raspored je vec na cijelom stolu (pomak mora biti 0, 0)")
            offset = (0.0, 0.0)
        elif offset is None:
            raise MissingParam("gcode", ["pozicija_x", "pozicija_y"])
        settings_raw, _ = self.layer_settings(rec)
        settings = G.parse_layer_settings(settings_raw, self.used_layers(rec))
        d = self._dir(job_id)
        if rec["type"] == "raster":
            pm = R.power_map_from_png((d / rec["raster"]["png"]).read_bytes())
            st = settings["ENGRAVE"]
            if frame:
                ny, nx = pm.shape
                w, h = nx * st.interval_mm, ny * st.interval_mm
                box = LaserDesign(w, h)
                box.add("CUT", [np.array([[0, 0], [w, 0], [w, h], [0, h]], float)], "okvir")
                res = G.frame_gcode(box, offset, frame_speed_mm_s or st.speed_mm_s)
                return {"text": res.text, "stats": res.stats, "file": None}
            text, stats = R.raster_gcode(pm, st.interval_mm, st.speed_mm_s, st.power_pct, st.passes, offset,
                                         rec["slug"])
        else:
            designs = self.designs(rec)
            if not (1 <= sheet <= len(designs)):
                raise InvalidParam(f"ploca: {sheet} ne postoji (job ima {len(designs)})")
            des = designs[sheet - 1]
            if sidro:
                offset = self._pomak_po_sidru(des, sidro, offset or (0.0, 0.0))
            if frame:
                speed = frame_speed_mm_s or min(s.speed_mm_s for s in settings.values())
                res = G.frame_gcode(des, offset, speed)
                return {"text": res.text, "stats": res.stats, "file": None}
            res = G.generate(des, settings, offset, f"{rec['template']} {rec['slug']}")
            text, stats = res.text, res.stats
        suffix = f"_ploca{sheet}" if len(rec.get("sheets") or []) > 1 else ""
        name = f"{rec['slug']}{suffix}.gcode"
        (d / name).write_text(text, encoding="utf-8")
        entry = {"file": name, "sheet": sheet, "offset_mm": list(offset), "sha256": _sha256(d / name),
                 "created": _now().isoformat(), "stats": {k: v for k, v in stats.items() if k != "settings"},
                 "layer_settings": settings_raw}
        rec["gcode"] = [g for g in rec.get("gcode", []) if g["file"] != name] + [entry]
        self._save(rec)
        if rec.get("production_dir"):
            shutil.copy2(d / name, Path(rec["production_dir"]) / name)
        return {"text": text, "stats": stats, "file": name, "path": str(d / name)}

    def gcode_path(self, job_id: str, name: str) -> Path:
        rec = self.get(job_id)
        if name not in [g["file"] for g in rec.get("gcode", [])]:
            raise OutputError(f"G-code {name!r} ne pripada jobu {job_id}")
        return self._dir(job_id) / name

    # ============================================================= raspored iz editora
    def create_layout(self, job_id: str, items: list) -> dict:
        """Transformacije iz editora (fabric.js) -> novi job sa rasporedom na cijelom stolu.

        items[i] = {"index": i, "matrix": [a, b, c, d, e, f], "path_offset": [x, y]} za svaku putanju
        (redoslijed kao u SVG-u): p' = M * (p - path_offset). Rezultat prolazi sve provjere."""
        import numpy as np

        from fabagent.core import config as C
        from fabagent.core.decisions import DecisionLog
        from fabagent.core.errors import InvalidGeometry, InvalidParam
        from fabagent.core.validator import ValidatedParams
        from fabagent.laser import svg_builder
        from fabagent.laser.design import LaserDesign, check_design, estimate_time, layer_stats
        from fabagent.laser.presets import get_preset
        rec = self.get(job_id)
        if rec["type"] not in ("laser", "upload"):
            raise InvalidParam("raspored u editoru postoji samo za vektorske laserske jobove")
        if rec.get("blocking"):
            raise ValidationFailed(job_id, ["job ima nerijesene greske"] + rec["blocking"])
        designs = self.designs(rec)
        if len(designs) != 1:
            raise InvalidParam("editor podrzava jobove sa jednom plocom (vise ploca vec ima automatski raspored)")
        src = designs[0]
        if not isinstance(items, list) or len(items) != len(src.items):
            raise InvalidParam(f"raspored mora imati {len(src.items)} objekata")
        W, H = C.LASER_WORK_AREA_MM
        out = LaserDesign(W, H)
        transforms = []
        try:
            ordered = sorted(items, key=lambda t: int(t["index"]))
        except (KeyError, TypeError, ValueError):
            raise InvalidParam("svaki objekat rasporeda mora imati indeks") from None
        for k, (it, tr) in enumerate(zip(src.items, ordered)):
            if int(tr["index"]) != k:
                raise InvalidParam("indeksi objekata rasporeda nisu 0..n-1")
            try:
                a, b, c, dd, e, f = (float(v) for v in tr["matrix"])
                px, py = (float(v) for v in tr["path_offset"])
            except (KeyError, TypeError, ValueError):
                raise InvalidParam(f"objekat {k}: matrica (6 brojeva) i path_offset (2 broja) su obavezni") from None
            det = a * dd - b * c
            if not all(math.isfinite(v) for v in (a, b, c, dd, e, f, px, py)) or abs(det) < 1e-9:
                raise InvalidParam(f"objekat {k}: neispravna transformacija")
            M = np.array([[a, c, e], [b, dd, f]])
            rings = [np.column_stack([r[:, 0] - px, r[:, 1] - py, np.ones(len(r))]) @ M.T for r in it.rings]
            out.add(it.layer, rings, it.label)
            transforms.append({"index": k, "matrix": [a, b, c, dd, e, f], "scale_det": round(det, 6)})
        problems = check_design(out)
        if problems:
            raise InvalidGeometry(problems, "raspored")
        preset = get_preset(rec["preset"])
        log = DecisionLog()
        log.record("raspored", f"raspored iz editora za job {job_id}", objekata=len(transforms))
        if any(abs(abs(t["scale_det"]) - 1) > 1e-6 for t in transforms):
            log.warn("neki objekti su skalirani u editoru - dimenzije se razlikuju od originala")
        svg = svg_builder.design_to_svg(out, f"FabAgent raspored {rec['slug']}", "raspored na stolu iz editora")
        now, new_id, _, job_dir = self._new_dir(rec["template"] if rec["type"] == "laser" else "raspored",
                                                rec["slug"])
        base = f"{rec['slug']}_raspored"
        try:
            times = estimate_time(out, preset)
            meta = {"kind": "laser", "template": rec["template"], "source_job": job_id, "layout": transforms,
                    "document_mm": {"width": W, "height": H}, "layers": layer_stats(out),
                    "material_preset": preset.as_dict(), "time_estimate_s": times,
                    "warnings": log.warnings, "decision_log": log.entries}
            params = ValidatedParams(rec["template"], dict(rec["params"]))
            result = svg_builder.LaserResult(rec["template"], params, preset, out, svg, meta, log)
            paths = svg_builder.write_outputs(result, str(job_dir / f"{base}.svg"), preview=True)
        except Exception:
            shutil.rmtree(job_dir, ignore_errors=True)
            raise
        new = {k: v for k, v in rec.items() if k not in ("status", "approved", "production_dir", "gcode",
                                                          "job_id", "created")}
        new.update(job_id=new_id, created=now.isoformat(), status=STATUS_PREVIEW, on_bed=True,
                   source_job=job_id, layout=transforms, renders=[],
                   outputs=[Path(paths[k]).name for k in ("svg", "png", "json")],
                   sheets=[{"svg": Path(paths["svg"]).name, "png": Path(paths["png"]).name,
                            "document_mm": {"width": W, "height": H}, "time_s": times["total"]}],
                   warnings=list(dict.fromkeys(rec.get("warnings", []) + log.warnings)),
                   estimates={"vrijeme_s": times["total"], "masa_g": None},
                   summary={"document_mm": {"width": W, "height": H}, "layers": layer_stats(out),
                            "time_human": f"{int(times['total'] // 60)} min {int(round(times['total'] % 60))} s",
                            "time_by_layer_s": times})
        if rec["type"] == "upload":
            new.update(sources=[], blocking=[], original_view=None, bitmap_view=None)
            new["type"] = "laser"
            new["template"] = "raspored"
        self._save(new)
        return new

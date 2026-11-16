"""JSON API (za skripte i asistenta), editor rasporeda i upravljanje laserom.

* POST zahtjevi na /api/* moraju imati Content-Type: application/json - browser to ne moze poslati
  sa druge stranice bez CORS preflight-a (koji ovaj server ne odobrava), pa strane web stranice ne
  mogu pokretati build-ove na ovom racunaru.
* Slanje G-code-a na laser radi browser (Web Serial) - server samo generise G-code. G-code za
  rezanje/graviranje se izdaje samo za ODOBRENE jobove; Frame (laser ugasen) za sve.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse

from fabagent.core import config as C
from fabagent.core.errors import FabError, InvalidParam, OutputError, UnknownTemplate
from fabagent.web.jobs import STATUS_APPROVED

HTTP_UNPROCESSABLE = 422
HTTP_CONFLICT = 409
HTTP_UNSUPPORTED_MEDIA = 415
HTTP_NOT_FOUND = 404


def _err(exc: FabError, status: int = HTTP_UNPROCESSABLE) -> JSONResponse:
    return JSONResponse(dict(exc.to_dict(), ok=False, exit_code=exc.exit_code), status_code=status)


async def _json_body(request: Request) -> dict:
    ctype = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if ctype != "application/json":
        raise _MediaType()
    try:
        body = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise InvalidParam("tijelo zahtjeva nije ispravan JSON") from None
    if not isinstance(body, dict):
        raise InvalidParam("tijelo zahtjeva mora biti JSON objekat")
    return body


class _MediaType(Exception):
    pass


def job_summary(store, rec: dict) -> dict:
    d = store.jobs_dir / rec["job_id"]
    files = {n: str(d / n) for n in rec["outputs"]}
    files.update({g["file"]: str(d / g["file"]) for g in rec.get("gcode", [])})
    out = {"ok": True, "job_id": rec["job_id"], "type": rec["type"], "template": rec["template"],
           "status": rec["status"], "params": rec["params"], "files": files,
           "production_dir": rec.get("production_dir"), "warnings": rec.get("warnings", []),
           "blocking": rec.get("blocking", []), "estimates": rec.get("estimates"),
           "summary": rec.get("summary"), "urls": {"pregled": f"/history/{rec['job_id']}"}}
    if store.is_laser_job(rec):
        out["urls"]["laser"] = f"/machine?job={rec['job_id']}"
        if rec["type"] in ("laser", "upload") and len(rec.get("sheets") or [None]) == 1:
            out["urls"]["editor"] = f"/editor/{rec['job_id']}"
    return out


def register(app, store, page, error_view, preview_ctx) -> None:
    from fabagent.web.app import KINDS

    @app.exception_handler(_MediaType)
    async def _media(request: Request, exc: _MediaType):
        return JSONResponse({"ok": False, "error": "UnsupportedMediaType",
                             "message": "POST na /api/* mora imati Content-Type: application/json"},
                            status_code=HTTP_UNSUPPORTED_MEDIA)

    # ------------------------------------------------------------ JSON API
    @app.get("/api/templates")
    def api_templates():
        out = {}
        for kind, (_, specs) in KINDS.items():
            for name, spec in specs.items():
                out[name] = {"type": kind, "description": spec.description, "example": spec.example,
                             "params": {p.name: {"type": p.type, "required": p.required, "min": p.min,
                                                 "max": p.max, "choices": list(p.choices), "default": p.default,
                                                 "unit": p.unit} for p in spec.params}}
        return out

    @app.post("/api/build")
    async def api_build(request: Request):
        """{"type": "laser"|"print", "template": "...", "params": {...}, "use_defaults": false, "approve": false}"""
        try:
            body = await _json_body(request)
            kind, template = body.get("type"), body.get("template")
            if kind not in KINDS:
                raise InvalidParam(f"type: '{kind}' nije dozvoljeno, izbor: {list(KINDS)}")
            if template not in KINDS[kind][1]:
                raise UnknownTemplate(f"nepoznat sablon '{template}', dostupno: {sorted(KINDS[kind][1])}")
            params = body.get("params") or {}
            if not isinstance(params, dict):
                raise InvalidParam("params mora biti JSON objekat")
            use_defaults = body.get("use_defaults", False)
            approve = body.get("approve", False)
            if not isinstance(use_defaults, bool) or not isinstance(approve, bool):
                raise InvalidParam("use_defaults i approve moraju biti true/false")
            rec = store.create_preview(kind, template, params, use_defaults)
            if approve:
                rec = store.approve(rec["job_id"])
        except FabError as exc:
            return _err(exc)
        return job_summary(store, rec)

    @app.get("/api/jobs/{job_id}")
    def api_job(job_id: str):
        try:
            return job_summary(store, store.get(job_id))
        except FabError as exc:
            return _err(exc, HTTP_NOT_FOUND)

    @app.post("/api/jobs/{job_id}/approve")
    async def api_approve(job_id: str, request: Request):
        try:
            await _json_body(request)
            return job_summary(store, store.approve(job_id))
        except FabError as exc:
            return _err(exc)

    @app.get("/api/jobs/{job_id}/layers")
    def api_layers(job_id: str):
        try:
            rec = store.get(job_id)
            if not store.is_laser_job(rec):
                raise InvalidParam("postavke slojeva postoje samo za laserske jobove")
            settings, source = store.layer_settings(rec)
            return {"ok": True, "layers": settings, "source": source, "used": store.used_layers(rec),
                    "modes": list(C.LAYER_MODES), "colors": C.LAYER_COLORS,
                    "ranges": {"speed_mm_s": C.LAYER_SPEED_RANGE_MM_S, "power_pct": C.LAYER_POWER_RANGE_PCT,
                               "passes": C.LAYER_PASSES_RANGE, "interval_mm": C.LAYER_INTERVAL_RANGE_MM},
                    "locked_interval": rec["raster"]["interval_mm"] if rec["type"] == "raster" else None}
        except FabError as exc:
            return _err(exc)

    @app.put("/api/jobs/{job_id}/layers")
    async def api_save_layers(job_id: str, request: Request):
        try:
            body = await _json_body(request)
            rec = store.save_layer_settings(job_id, body.get("layers") or {})
            return {"ok": True, "layers": rec["layer_settings"]}
        except FabError as exc:
            return _err(exc)

    @app.post("/api/jobs/{job_id}/gcode")
    async def api_gcode(job_id: str, request: Request):
        """{"x": mm, "y": mm, "sheet": 1, "frame": false}. Rezanje/graviranje samo za odobrene jobove."""
        try:
            body = await _json_body(request)
            rec = store.get(job_id)
            frame = body.get("frame", False)
            if not isinstance(frame, bool):
                raise InvalidParam("frame mora biti true/false")
            if not frame and rec["status"] != STATUS_APPROVED:
                return JSONResponse({"ok": False, "error": "NotApproved",
                                     "message": "Job nije odobren - odobri ga prije slanja na laser "
                                                "(Frame je dozvoljen jer je laser ugasen)."},
                                    status_code=HTTP_CONFLICT)
            offset = None
            if body.get("x") is not None or body.get("y") is not None:
                try:
                    offset = (float(body["x"]), float(body["y"]))
                except (KeyError, TypeError, ValueError):
                    raise InvalidParam("x i y moraju oba biti brojevi (mm)") from None
            sheet = body.get("sheet", 1)
            if not isinstance(sheet, int):
                raise InvalidParam("sheet mora biti cijeli broj")
            res = store.make_gcode(job_id, offset, sheet=sheet, frame=frame)
        except FabError as exc:
            return _err(exc)
        out = {"ok": True, "frame": frame, "stats": res["stats"], "lines": res["text"].count("\n")}
        if res["file"]:
            out.update(file=res["file"], path=res["path"], url=f"/api/jobs/{job_id}/gcode/{res['file']}")
        else:
            out["gcode"] = res["text"]
        return out

    @app.get("/api/jobs/{job_id}/gcode/{name}")
    def api_gcode_file(job_id: str, name: str):
        try:
            path = store.gcode_path(job_id, name)
        except FabError as exc:
            return _err(exc, HTTP_NOT_FOUND)
        return PlainTextResponse(path.read_text(encoding="utf-8"), headers={"Cache-Control": "no-store"})

    @app.post("/api/jobs/{job_id}/layout")
    async def api_layout(job_id: str, request: Request):
        try:
            body = await _json_body(request)
            rec = store.create_layout(job_id, body.get("items"))
        except FabError as exc:
            return _err(exc)
        return job_summary(store, rec)

    @app.get("/api/jobs/{job_id}/editor-data")
    def api_editor_data(job_id: str):
        from fabagent.laser.svg_builder import rings_to_d
        try:
            rec = store.get(job_id)
            if rec["type"] not in ("laser", "upload"):
                raise InvalidParam("editor je samo za vektorske laserske jobove")
            designs = store.designs(rec)
            if len(designs) != 1:
                raise InvalidParam("job ima vise ploca - raspored je vec automatski")
            d = designs[0]
            items = [{"index": i, "layer": it.layer, "label": it.label, "d": rings_to_d(it.rings)}
                     for i, it in enumerate(d.items)]
        except FabError as exc:
            return _err(exc)
        return {"ok": True, "bed_mm": list(C.LASER_WORK_AREA_MM), "doc_mm": [d.width, d.height],
                "items": items, "colors": C.LAYER_COLORS, "on_bed": store.on_bed(rec)}

    # ------------------------------------------------------------ stranice
    @app.get("/editor/{job_id}", response_class=HTMLResponse)
    def editor_page(request: Request, job_id: str):
        rec = store.get(job_id)
        return page(request, "editor.html", {"job": rec})

    @app.get("/machine", response_class=HTMLResponse)
    def machine_page(request: Request, job: str = ""):
        """Bez ?job= uzmi najnoviji laserski posao.

        Ranije je "Laser" u meniju vodio na prazan ekran: posao napravljen u
        razgovoru ostajao je na svojoj stranici, a ovdje su Frame i Start bili
        iskljuceni bez objasnjenja. Operater koji stoji uz masinu ne smije
        traziti svoj posao po spisku - zadnji napravljen je skoro uvijek taj.
        """
        rec = store.get(job) if job else None
        sam_izabrao = False
        if rec is None:
            laserski = [j for j in store.list() if store.is_laser_job(j)]
            if laserski:
                rec = store.get(laserski[0]["job_id"])
                sam_izabrao = True
        ctx = {"job": rec, "baud": C.GRBL_BAUD_RATE, "home_cmd": C.GRBL_HOME_CMD,
               "bed": C.LASER_WORK_AREA_MM, "origin": C.MACHINE_ORIGIN,
               "sam_izabrao": sam_izabrao}
        if rec is not None:
            if not store.is_laser_job(rec):
                raise OutputError(f"job {job} nije laserski")
            ctx["on_bed"] = store.on_bed(rec)
            ctx["sheet_count"] = len(rec.get("sheets") or [None])
        ctx["laser_jobs"] = [j for j in store.list() if store.is_laser_job(j)][:20]
        return page(request, "machine.html", ctx)

    @app.post("/job/approve", response_class=HTMLResponse)
    async def job_approve(request: Request):
        """Generic odobravanje (raster, litofanija, raspored) - vraca na stranicu joba."""
        form = dict(await request.form())
        job_id = str(form.get("job_id", ""))
        rec = store.get(job_id)
        try:
            store.approve(job_id)
        except FabError as exc:
            return page(request, result_template(rec), dict(preview_ctx(rec), banner=error_view(exc)[0]),
                        HTTP_UNPROCESSABLE)
        return RedirectResponse(f"/history/{job_id}", status_code=303)


def result_template(rec: dict) -> str:
    return {"raster": "raster_result.html", "litho": "litho_result.html", "upload": "upload_result.html"}.get(
        rec["type"], "layout_result.html")

"""Lokalni web interfejs FabAgent-a (FastAPI + Jinja2, server-side render).

Tanki sloj iznad postojecih funkcija: svg_builder.build / stl_builder.build /
validatori. Greske su iste klase i poruke kao u CLI-u (MissingParam, OutOfRange,
InvalidGeometry, InvalidParam). Sve se servira lokalno (bez CDN-a).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import secrets
import base64
import hashlib
from pathlib import Path
from urllib.parse import urlencode

from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from fabagent import __version__
from fabagent.core import config as C
from fabagent.core import camera as cam
from fabagent.core import background as bg
from fabagent.core.errors import (CameraUnavailable, FabError, InvalidParam, MissingParam, OutOfRange,
                                  OutputError, UnknownTemplate)
from fabagent.web import labels as L
from fabagent.web.jobs import STATUS_APPROVED, JobStore

WEB_DIR = Path(__file__).resolve().parent
KINDS = {"laser": ("Laser", C.LASER_TEMPLATES), "print": ("3D štampa", C.PRINT_TEMPLATES)}
KIND_LABELS = {"laser": "Laser", "print": "3D štampa", "upload": "Upload SVG", "raster": "Raster slika",
               "litho": "3D litofanija"}
IMAGE_MODES = ("threshold", "dither", "grayscale", "vectorize")
SIDEBAR_JOBS = 12
CONTROL_FIELDS = ("use_defaults", "job_id")
HTTP_UNPROCESSABLE = 422
HTTP_NOT_FOUND = 404
HTTP_SERVICE_UNAVAILABLE = 503
TRACE_FIELDS = ("prag", "cutoff", "invertuj", "uklanjanje_suma", "pojednostavljenje", "nacin_praga", "motor")
RASTER_FIELDS = ("rezim_slike", "algoritam", "negativ", "min_snaga_posto", "interval_mm")
LITHO_FIELDS = ("izlaz", "nacin_3d", "min_debljina_mm", "max_debljina_mm", "filament")
BACKGROUND_FIELDS = bg.FIELDS
UPLOAD_VALUE_FIELDS = (TRACE_FIELDS + RASTER_FIELDS + LITHO_FIELDS + BACKGROUND_FIELDS
                       + ("sirina_mm", "jedinice"))
MEDIA_TYPES = {".svg": "image/svg+xml", ".png": "image/png", ".json": "application/json",
               ".jpg": "image/jpeg", ".pdf": "application/pdf", ".dxf": "application/dxf",
               ".stl": "model/stl", ".3mf": "model/3mf"}


# ---------------------------------------------------------------- forma
def _fmt_num(v) -> str:
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def _to_form_value(spec, value) -> str:
    if value is None:
        return ""
    if spec.type == "bool":
        if isinstance(value, bool):
            return "true" if value else "false"
        return str(value).lower()
    if spec.type in ("float", "int") and isinstance(value, (int, float)):
        return _fmt_num(value)
    return str(value)


def build_fields(kind: str, template: str, values: dict, errors: dict) -> list[dict]:
    spec = KINDS[kind][1][template]
    fields = []
    for p in spec.params:
        hint = []
        if p.type in ("float", "int") and not p.choices:
            hint.append(f"Dozvoljeno: {_fmt_num(p.min)} – {_fmt_num(p.max)} {L.unit_label(p.unit)}".strip())
        if p.type == "str":
            hint.append(f"Dužina: {p.min_len}–{p.max_len} znakova")
            if p.allowed_chars == C.FONT_CHARSET:
                hint.append("samo VELIKA slova, cifre i - . : / # + , ! ? ( ) _ &")
        if not p.required:
            dv = L.choice_label(p.name, p.default) if p.type == "enum" else _to_form_value(p, p.default)
            if p.type == "bool":
                dv = "da" if p.default else "ne"
            hint.append(f"preporučeno: {dv if dv != '' else '(prazno)'}")
        if p.choices and p.type == "enum":
            choices = [(c, L.choice_label(p.name, c)) for c in p.choices]
        elif p.choices:
            choices = [(str(c), str(c)) for c in p.choices]
        else:
            choices = []
        fields.append({
            "name": p.name,
            "label": L.param_label(template, p),
            "type": p.type,
            "widget": "select" if (choices or p.type == "bool") else ("number" if p.type in ("float", "int") else "text"),
            "choices": choices if p.type != "bool" else [("true", "Da"), ("false", "Ne")],
            "required": p.required,
            "min": p.min, "max": p.max,
            "step": "1" if p.type == "int" else "any",
            "maxlength": p.max_len,
            "default": _to_form_value(p, p.default) if not p.required else "",
            "value": values.get(p.name, ""),
            "error": errors.get(p.name),
            "hint": "; ".join(hint),
            "applies_when": ({"param": p.applies_when[0], "values": list(p.applies_when[1])}
                             if p.applies_when else None),
            "description": p.description,
        })
    return fields


def form_to_raw(kind: str, template: str, form: dict) -> dict:
    """Prazna polja = nezadana (nikad se ne izmisljaju). Samo poznata imena idu dalje;
    nepoznata takodje (core ih odbija kao InvalidParam, isto kao CLI)."""
    raw = {}
    for k, v in form.items():
        if k in CONTROL_FIELDS:
            continue
        v = str(v).strip() if k not in ("tekst", "sadrzaj", "serijski_broj") else str(v)
        if v != "":
            raw[k] = v
    return raw


def error_view(exc: FabError) -> tuple[dict, dict]:
    """(banner, greske po polju)."""
    field_errors: dict[str, str] = {}
    lines: list[str] = []
    if isinstance(exc, MissingParam):
        for n in exc.missing:
            field_errors[n] = "Obavezno polje – upiši vrijednost."
        for n in exc.optional_missing:
            field_errors[n] = "Nije zadano – upiši vrijednost ili uključi „Koristi preporučene vrijednosti“."
    elif isinstance(exc, OutOfRange):
        for v in exc.violations:
            field_errors[v.param] = v.describe()
    elif isinstance(exc, InvalidParam):
        # poruke oblika "ime_parametra: opis" vezuju se uz polje
        head, sep, rest = str(exc).partition(":")
        if sep and head.strip() and " " not in head.strip() and not head.startswith("["):
            field_errors[head.strip()] = rest.strip()
    reasons = getattr(exc, "reasons", None)
    if reasons:
        lines = list(reasons)
    banner = {"cls": type(exc).__name__, "title": L.ERROR_TITLES.get(type(exc).__name__, "Greška"),
              "message": str(exc), "lines": lines, "exit_code": exc.exit_code}
    return banner, field_errors


def _trace_engine_status() -> tuple[bool, str]:
    from fabagent.core.image_trace import vtracer_status
    return vtracer_status()


def _asset_version() -> str:
    """Hash sadrzaja statickih fajlova - browser ne koristi stari kes nakon nadogradnje."""
    h = hashlib.sha256()
    for f in sorted((WEB_DIR / "static").iterdir()):
        if f.is_file():
            h.update(f.name.encode())
            h.update(f.read_bytes())
    return h.hexdigest()[:10]


def _oznaka_koda() -> str:
    """Vrijeme najnovije izmjene koda, kao 'dd.mm. HH:MM'.

    Racuna se jednom, pri dizanju servera. Ako je kod izmijenjen a server nije
    ponovo pokrenut, u navigaciji stoji staro vrijeme - odmah se vidi da proces
    servira stari kod. Bez ovoga se nova stranica cini da ne postoji.
    """
    from datetime import datetime
    korijen = WEB_DIR.parent
    najnoviji = 0.0
    for f in korijen.rglob("*"):
        if f.suffix not in (".py", ".html", ".css", ".js") or "__pycache__" in f.parts:
            continue
        try:
            najnoviji = max(najnoviji, f.stat().st_mtime)
        except OSError:
            continue
    if not najnoviji:
        return "nepoznato"
    return datetime.fromtimestamp(najnoviji).strftime("%d.%m. %H:%M")


def _parse_number(name: str, text: str, kind):
    """Prazno -> None (core ce prijaviti MissingParam); neispravno -> InvalidParam. Bez pogadjanja."""
    if text == "":
        return None
    try:
        if "," in text:
            raise ValueError
        return kind(text)
    except ValueError:
        raise InvalidParam(f"{name}: '{text}' nije {'cijeli ' if kind is int else ''}broj (koristi tacku)") from None


def _svg_inline(path: Path) -> str:
    """SVG za ubacivanje u DOM - SAMO fajlovi koje je generisao FabAgent (nikad korisnicki original)."""
    text = path.read_text(encoding="utf-8")
    if text.startswith("<?xml"):
        text = text.split("?>", 1)[1]
    return text.strip()


def _svg_data_uri(path: Path) -> str:
    return "data:image/svg+xml;base64," + base64.b64encode(path.read_bytes()).decode("ascii")


# ---------------------------------------------------------------- aplikacija
def create_app(data_dir: str | None = None, camera_source: str | None = None) -> FastAPI:
    camera = cam.CameraSource(camera_source)

    @contextlib.asynccontextmanager
    async def lifespan(_app):
        yield
        camera.release()             # oslobodi USB kameru pri gasenju servera

    app = FastAPI(title="FabAgent", version=__version__, docs_url=None, redoc_url=None, openapi_url=None,
                  lifespan=lifespan)
    store = JobStore(data_dir)
    app.state.store = store
    calib_store = cam.CalibrationStore(store.jobs_dir.parent / "kamera")
    app.state.camera = camera
    templates = Jinja2Templates(directory=str(WEB_DIR / "templates"))
    oznaka_koda = _oznaka_koda()
    templates.env.globals.update(L=L, version=__version__, KINDS=KINDS, KIND_LABELS=KIND_LABELS, C=C,
                                 asset_v=_asset_version(), trace_engine_status=_trace_engine_status,
                                 oznaka_koda=oznaka_koda)
    app.mount("/static", StaticFiles(directory=str(WEB_DIR / "static")), name="static")

    def page(request: Request, name: str, ctx: dict, status: int = 200) -> HTMLResponse:
        ctx = dict(ctx, request=request, sidebar_jobs=store.list()[:SIDEBAR_JOBS])
        return templates.TemplateResponse(request, name, ctx, status_code=status)

    def spec_or_404(kind: str, template: str):
        if kind not in KINDS:
            raise UnknownTemplate(f"nepoznata vrsta '{kind}'")
        if template not in KINDS[kind][1]:
            raise UnknownTemplate(f"nepoznat šablon '{template}', dostupno: {sorted(KINDS[kind][1])}")
        return KINDS[kind][1][template]

    def preview_ctx(job: dict) -> dict:
        ctx = {"job": job}
        if job["type"] in ("laser", "upload"):
            sheets = job.get("sheets") or [{"svg": next(n for n in job["outputs"] if n.endswith(".svg")),
                                             "png": None}]
            ctx["sheet_views"] = [dict(sh, uri=_svg_data_uri(store.file_path(job["job_id"], sh["svg"])),
                                       inline=_svg_inline(store.file_path(job["job_id"], sh["svg"])))
                                  for sh in sheets]
        if job["type"] in ("raster", "litho") and job.get("sources"):
            src = store.file_path(job["job_id"], job["sources"][0])
            mime = "image/jpeg" if src.suffix == ".jpg" else MEDIA_TYPES.get(src.suffix, "image/png")
            ctx["original_uri"] = f"data:{mime};base64," + base64.b64encode(src.read_bytes()).decode("ascii")
        if job["type"] == "raster":
            png = store.file_path(job["job_id"], job["raster"]["png"]).read_bytes()
            ctx["raster_uri"] = "data:image/png;base64," + base64.b64encode(png).decode("ascii")
        if job["type"] == "upload":
            name = job.get("original_view", "izvor.svg")
            path = store.file_path(job["job_id"], name)
            mime = MEDIA_TYPES.get(path.suffix, "application/octet-stream")
            if path.suffix == ".jpg":
                mime = "image/jpeg"
            ctx["original_uri"] = f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode("ascii")
            if job.get("bitmap_view"):
                bmp = store.file_path(job["job_id"], job["bitmap_view"]).read_bytes()
                ctx["bitmap_uri"] = "data:image/png;base64," + base64.b64encode(bmp).decode("ascii")
        return ctx

    def form_page(request, kind, template, values, *, use_defaults=False, job=None, banner=None,
                  field_errors=None, status=200, approved=False):
        spec = spec_or_404(kind, template)
        ctx = {
            "kind": kind, "kind_label": KINDS[kind][0], "template": template, "spec": spec,
            "fields": build_fields(kind, template, values, field_errors or {}),
            "use_defaults": use_defaults, "banner": banner, "approved": approved,
            "share_query": urlencode({k: v for k, v in values.items() if v != ""}),
        }
        if job is not None:
            ctx.update(preview_ctx(job))
        return page(request, "form.html", ctx, status)

    @app.exception_handler(UnknownTemplate)
    async def _unknown(request: Request, exc: UnknownTemplate):
        return page(request, "error.html", {"banner": error_view(exc)[0]}, HTTP_NOT_FOUND)

    @app.exception_handler(OutputError)
    async def _output(request: Request, exc: OutputError):
        return page(request, "error.html", {"banner": error_view(exc)[0]}, HTTP_NOT_FOUND)

    @app.get("/health")
    def health():
        return {"status": "ok", "version": __version__, "kod": oznaka_koda}

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request):
        return page(request, "index.html", {})

    @app.get("/history", response_class=HTMLResponse)
    def history(request: Request):
        return page(request, "history.html", {"jobs": store.list()})

    @app.get("/history/{job_id}", response_class=HTMLResponse)
    def history_detail(request: Request, job_id: str):
        job = store.get(job_id)
        if job["type"] in ("upload", "raster", "litho") or (
                job["type"] == "laser" and job["template"] not in C.LASER_TEMPLATES):
            from fabagent.web.api import result_template
            return page(request, result_template(job), preview_ctx(job))
        spec_or_404(job["type"], job["template"])
        return page(request, "history_detail.html", dict(preview_ctx(job),
                    reopen=f"/{job['type']}/{job['template']}?job={job_id}"))

    # ---------------------------------------------------------- asistent
    def _pending_dir():
        d = store.jobs_dir.parent / "pending"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def asistent_page(request, nalog=None, token="", fajl_ime="", banner=None, status=200,
                      values=None, odgovoreno=None):
        from fabagent.core import asistent as A
        from fabagent.core.converter import SUPPORTED_EXT
        ctx = {"nalog": nalog, "token": token, "fajl_ime": fajl_ime, "banner": banner,
               "accept": ",".join(SUPPORTED_EXT), "pitanja": [], "primjedbe": [],
               "slika_url": "", "values": values or {}, "odgovoreno": odgovoreno or {}}
        if nalog is not None:
            ctx["pitanja"] = A.pitanja(nalog)
            ctx["primjedbe"] = A.primjedbe(nalog)
            if token and fajl_ime:
                ctx["slika_url"] = f"/asistent/fajl/{token}"
        return page(request, "asistent.html", ctx, status)

    @app.get("/asistent", response_class=HTMLResponse)
    def asistent_form(request: Request):
        return asistent_page(request)

    @app.post("/asistent", response_class=HTMLResponse)
    async def asistent_citaj(request: Request):
        from fabagent.core import asistent as A
        form = await request.form()
        tekst = str(form.get("tekst") or "").strip()
        if not tekst:
            return asistent_page(request, banner=error_view(MissingParam("asistent", ["tekst"]))[0],
                                 status=HTTP_UNPROCESSABLE)
        token, ime = "", ""
        f = form.get("fajl")
        if f is not None and getattr(f, "filename", ""):
            data = await f.read(C.UPLOAD_MAX_BYTES + 1)
            token = secrets.token_hex(8)
            ime = Path(f.filename).name
            folder = _pending_dir() / token
            folder.mkdir(parents=True)
            (folder / ime).write_bytes(data)
        return asistent_page(request, A.procitaj(tekst), token, ime)

    @app.get("/asistent/fajl/{token}")
    def asistent_fajl(token: str):
        folder = _pending_dir() / token
        fajlovi = sorted(folder.glob("*")) if folder.is_dir() else []
        if not fajlovi:
            raise OutputError(f"privremeni fajl '{token}' ne postoji")
        return FileResponse(fajlovi[0])

    @app.post("/asistent/napravi", response_class=HTMLResponse)
    async def asistent_napravi(request: Request):
        from fabagent.core import asistent as A
        form = await request.form()
        tekst = str(form.get("tekst") or "")
        token = str(form.get("token") or "")
        nalog = A.procitaj(tekst)

        # Svi odgovori iz forme - i oni dati sada, i oni koje forma vraca iz
        # prethodnog koraka. Bez ovog drugog pitanja se vrte u krug.
        odgovoreno = A.primijeni_odgovore(nalog, {k: form.get(k) for k in A.POLJA_ODGOVORA})

        preostalo = [p["polje"] for p in A.pitanja(nalog)]
        folder = _pending_dir() / token if token else None
        fajlovi = sorted(folder.glob("*")) if folder and folder.is_dir() else []
        ime = fajlovi[0].name if fajlovi else ""

        if preostalo:
            return asistent_page(request, nalog, token, ime,
                                 banner=error_view(MissingParam("asistent", preostalo))[0],
                                 status=HTTP_UNPROCESSABLE, odgovoreno=odgovoreno)
        if not fajlovi:
            return asistent_page(request, nalog, token, ime,
                                 banner=error_view(MissingParam("asistent", ["fajl"]))[0],
                                 status=HTTP_UNPROCESSABLE, odgovoreno=odgovoreno)

        values = {k: str(form.get(k) or "").strip() for k in UPLOAD_VALUE_FIELDS}
        values.update(A.u_parametre(nalog))
        materijal = values.pop("materijal", "") or nalog.materijal or ""
        try:
            job = _process_upload(ime, fajlovi[0].read_bytes(), materijal, values)
        except FabError as exc:
            return asistent_page(request, nalog, token, ime,
                                 banner=error_view(exc)[0], status=HTTP_UNPROCESSABLE,
                                 odgovoreno=odgovoreno)
        from fabagent.web.api import result_template
        return page(request, result_template(job), preview_ctx(job))

    # ---------------------------------------------------------- razgovor
    # Stanje razgovora ide kroz formu kao base64 JSON, pa server ne drzi sesiju.
    # Sto se ne procita iz tokena, pocinje novi razgovor - nikad polupopunjen.
    def _razgovor_token(stanje) -> str:
        sirovo = json.dumps(stanje.kao_dict(), ensure_ascii=False).encode("utf-8")
        return base64.urlsafe_b64encode(sirovo).decode("ascii")

    def _razgovor_iz_tokena(token: str):
        from fabagent.core import razgovor as Rz
        if not token:
            return Rz.novo()
        try:
            sirovo = base64.urlsafe_b64decode(token.encode("ascii")).decode("utf-8")
            return Rz.iz_dicta(json.loads(sirovo))
        except Exception:
            return Rz.novo()

    def razgovor_page(request, stanje, token="", fajl_ime="", banner=None, status=200):
        from fabagent.core import razgovor as Rz
        from fabagent.core.converter import SUPPORTED_EXT
        blokade, upozorenja = Rz.provjera_prije_starta(stanje)
        pitanje = Rz.sljedece_pitanje(stanje)
        ctx = {"stanje": stanje, "stanje_token": _razgovor_token(stanje),
               "token": token, "fajl_ime": fajl_ime or stanje.fajl, "banner": banner,
               "accept": ",".join(SUPPORTED_EXT), "pitanje": pitanje,
               "pitanje_tekst": Rz.tekst_pitanja(pitanje),
               "procitano": Rz.nalog(stanje).procitano,
               "parametri": Rz.parametri_masine(stanje), "blokade": blokade,
               "upozorenja": upozorenja, "teme": [n for n, _, _ in Rz.ZNANJE],
               "spremno": stanje.faza == "gotovo" and not blokade}
        return page(request, "razgovor.html", ctx, status)

    @app.get("/razgovor", response_class=HTMLResponse)
    def razgovor_novi(request: Request):
        from fabagent.core import razgovor as Rz
        return razgovor_page(request, Rz.novo())

    @app.post("/razgovor", response_class=HTMLResponse)
    async def razgovor_korak(request: Request):
        from fabagent.core import razgovor as Rz
        form = await request.form()
        stanje = _razgovor_iz_tokena(str(form.get("stanje") or ""))
        token = str(form.get("token") or "")
        fajl_ime = str(form.get("fajl_ime") or "")

        f = form.get("fajl")
        if f is not None and getattr(f, "filename", ""):
            data = await f.read(C.UPLOAD_MAX_BYTES + 1)
            token = secrets.token_hex(8)
            fajl_ime = Path(f.filename).name
            folder = _pending_dir() / token
            folder.mkdir(parents=True, exist_ok=True)
            (folder / fajl_ime).write_bytes(data)
            stanje.fajl = fajl_ime
            poruka = f"Fajl primljen: {fajl_ime}"
            if Rz.je_vektor(stanje):
                poruka += (" - vektorski fajl, pa ne pitam kako se gravira ni za razmak "
                           "linija; to su pitanja za sliku sa pikselima.")
            stanje.kaze("asistent", poruka)
        elif fajl_ime and not stanje.fajl:
            stanje.fajl = fajl_ime

        unos = str(form.get("unos") or "").strip()
        if unos:
            stanje = Rz.korak(stanje, unos)
        elif stanje.fajl:
            # Fajl je stigao bez teksta: reci sta je sljedece, ne cekaj da pita.
            sljedece = Rz.sljedece_pitanje(stanje)
            stanje.cekam = sljedece["polje"] if sljedece else ""
            stanje.kaze("asistent", Rz.tekst_pitanja(sljedece) if sljedece
                        else "Sve je uneseno. Provjera je ispod. Start pritiskas ti.")
        elif not fajl_ime:
            return razgovor_page(request, stanje, token, fajl_ime,
                                 banner=error_view(MissingParam("razgovor", ["unos"]))[0],
                                 status=HTTP_UNPROCESSABLE)
        return razgovor_page(request, stanje, token, fajl_ime)

    @app.post("/razgovor/napravi", response_class=HTMLResponse)
    async def razgovor_napravi(request: Request):
        from fabagent.core import razgovor as Rz
        form = await request.form()
        stanje = _razgovor_iz_tokena(str(form.get("stanje") or ""))
        token = str(form.get("token") or "")
        fajl_ime = str(form.get("fajl_ime") or "")

        blokade, _ = Rz.provjera_prije_starta(stanje)
        if blokade:
            return razgovor_page(request, stanje, token, fajl_ime,
                                 banner=error_view(InvalidParam("; ".join(blokade)))[0],
                                 status=HTTP_UNPROCESSABLE)

        folder = _pending_dir() / token if token else None
        fajlovi = sorted(folder.glob("*")) if folder and folder.is_dir() else []
        if not fajlovi:
            return razgovor_page(request, stanje, token, fajl_ime,
                                 banner=error_view(MissingParam("razgovor", ["fajl"]))[0],
                                 status=HTTP_UNPROCESSABLE)

        values = {k: "" for k in UPLOAD_VALUE_FIELDS}
        values.update(Rz.u_parametre(stanje))
        materijal = values.pop("materijal", "") or (Rz.nalog(stanje).materijal or "")
        try:
            job = _process_upload(fajlovi[0].name, fajlovi[0].read_bytes(), materijal, values)
        except FabError as exc:
            return razgovor_page(request, stanje, token, fajl_ime,
                                 banner=error_view(exc)[0], status=HTTP_UNPROCESSABLE)
        from fabagent.web.api import result_template
        return page(request, result_template(job), preview_ctx(job))

    # ------------------------------------------------------- radni nalog
    @app.get("/jobs/{job_id}/uputstvo", response_class=HTMLResponse)
    def job_uputstvo(request: Request, job_id: str):
        from fabagent.core import uputstvo as U
        return page(request, "uputstvo.html", {"nalog": U.build(store.get(job_id))})

    @app.get("/jobs/{job_id}/uputstvo.txt", response_class=Response)
    def job_uputstvo_txt(job_id: str):
        from fabagent.core import uputstvo as U
        tekst = U.kao_tekst(U.build(store.get(job_id)))
        return Response(tekst, media_type="text/plain; charset=utf-8")

    @app.get("/jobs/{job_id}/{name}")
    def job_file(job_id: str, name: str):
        path = store.file_path(job_id, name)
        return FileResponse(path, media_type=MEDIA_TYPES.get(path.suffix, "application/octet-stream"),
                            filename=name if path.suffix in (".stl", ".3mf", ".json") else None)

    for kind in KINDS:
        def make_routes(kind=kind):
            @app.get(f"/{kind}/{{template}}", response_class=HTMLResponse, name=f"{kind}_form")
            def form(request: Request, template: str):
                spec = spec_or_404(kind, template)
                q = dict(request.query_params)
                job = None
                use_defaults = q.pop("use_defaults", "") in ("1", "true", "on")
                if "job" in q:
                    job = store.get(q.pop("job"))
                    if job["type"] != kind or job["template"] != template:
                        raise OutputError(f"job {job['job_id']} ne pripada šablonu {kind}/{template}")
                    values = {k: str(v) for k, v in job["params_raw"].items()}
                    use_defaults = job["use_defaults"]
                else:
                    values = {k: v for k, v in q.items() if k != "preporuceno"}
                if q.get("preporuceno") == "1":
                    for p in spec.params:
                        if not p.required and not values.get(p.name):
                            values[p.name] = _to_form_value(p, p.default)
                return form_page(request, kind, template, values, use_defaults=use_defaults, job=job,
                                 approved=bool(job and job["status"] == STATUS_APPROVED))

            @app.post(f"/{kind}/{{template}}/preview", response_class=HTMLResponse)
            async def preview(request: Request, template: str):
                spec_or_404(kind, template)
                form = dict(await request.form())
                use_defaults = form.get("use_defaults") in ("1", "true", "on")
                raw = form_to_raw(kind, template, form)
                try:
                    job = store.create_preview(kind, template, raw, use_defaults)
                except FabError as exc:
                    banner, field_errors = error_view(exc)
                    return form_page(request, kind, template, raw, use_defaults=use_defaults,
                                     banner=banner, field_errors=field_errors, status=HTTP_UNPROCESSABLE)
                return form_page(request, kind, template, raw, use_defaults=use_defaults, job=job)

            @app.post(f"/{kind}/{{template}}/approve", response_class=HTMLResponse)
            async def approve(request: Request, template: str):
                spec_or_404(kind, template)
                form = dict(await request.form())
                job = store.get(str(form.get("job_id", "")))
                if job["type"] != kind or job["template"] != template:
                    raise OutputError(f"job {job['job_id']} ne pripada šablonu {kind}/{template}")
                values = {k: str(v) for k, v in job["params_raw"].items()}
                try:
                    job = store.approve(job["job_id"])
                except FabError as exc:
                    banner, _ = error_view(exc)
                    return form_page(request, kind, template, values, use_defaults=job["use_defaults"],
                                     job=job, banner=banner, status=HTTP_UNPROCESSABLE)
                return form_page(request, kind, template, values, use_defaults=job["use_defaults"],
                                 job=job, approved=True)

        make_routes()

    # ------------------------------------------------------------ upload SVG
    def upload_page(request, banner=None, materijal="", status=200, values=None):
        from fabagent.core.converter import DXF_UNIT_CHOICES, SUPPORTED_EXT
        return page(request, "upload.html", {"banner": banner, "materijal": materijal, "values": values or {},
                                             "max_mb": C.UPLOAD_MAX_BYTES // (1024 * 1024),
                                             "accept": ",".join(SUPPORTED_EXT), "dxf_units": list(DXF_UNIT_CHOICES)},
                    status)

    @app.get("/upload", response_class=HTMLResponse)
    def upload_form(request: Request):
        return upload_page(request)

    def _process_upload(filename: str, data: bytes, materijal: str, values: dict) -> dict:
        from fabagent.core.converter import IMAGE_EXT, detect_format
        fmt = detect_format(filename)
        is_image = Path(filename).suffix.lower() in IMAGE_EXT
        # Uklanjanje pozadine je prvi korak i pokrece se samo ako je zadan okvir.
        # Primjenjuje se prije sva tri toka (vektor, raster, litofanija), pa
        # dalje ide vec ocistena slika.
        if is_image:
            bg_opts = bg.parse_options(values)
            if bg_opts.requested():
                data, _ = bg.remove_background_bytes(data, bg_opts)
                filename = Path(filename).stem + "_bez_pozadine.png"
                fmt = detect_format(filename)
        if is_image and values.get("izlaz") == "3d":
            return store.create_litho(filename, data, {k: values.get(k, "") for k in LITHO_FIELDS + ("sirina_mm",)}
                                      | {"negativ": values.get("negativ", "")})
        if is_image and values.get("izlaz") not in ("", None, "2d"):
            raise InvalidParam(f"izlaz: '{values.get('izlaz')}' nije dozvoljeno, izbor: ['2d', '3d']")
        mode = values.get("rezim_slike") or ""
        if is_image and mode and mode not in IMAGE_MODES:
            raise InvalidParam(f"rezim_slike: '{mode}' nije dozvoljeno, izbor: {list(IMAGE_MODES)}")
        if is_image and mode in ("dither", "grayscale"):
            return store.create_raster(filename, data, materijal,
                                       {k: values.get(k, "") for k in RASTER_FIELDS + ("sirina_mm", "prag")})
        if values.get("negativ") in ("true", "on", "1") and not values.get("invertuj"):
            values = dict(values, invertuj="true")      # Negative = invert za vektorsko trasiranje
        width = None
        trace = None
        if fmt in ("pdf",) or Path(filename).suffix.lower() in IMAGE_EXT:
            trace = {k: values.get(k, "") for k in TRACE_FIELDS}
            if mode == "vectorize":
                trace["obrisi"] = "true"                 # obrisi (konture) umjesto popunjenih oblika
        if Path(filename).suffix.lower() in IMAGE_EXT:
            width = _parse_number("sirina_mm", values.get("sirina_mm", ""), float)
        return store.create_upload(filename, data, materijal, width_mm=width,
                                   dxf_units=(values.get("jedinice") or None) if fmt == "dxf" else None,
                                   trace=trace)

    @app.post("/upload", response_class=HTMLResponse)
    async def upload(request: Request):
        form = await request.form()
        materijal = str(form.get("materijal") or "")
        values = {k: str(form.get(k) or "").strip() for k in UPLOAD_VALUE_FIELDS}
        f = form.get("fajl")
        try:
            if f is None or not getattr(f, "filename", ""):
                raise MissingParam("upload", ["fajl"])
            data = await f.read(C.UPLOAD_MAX_BYTES + 1)
            job = _process_upload(f.filename, data, materijal, values)
        except FabError as exc:
            return upload_page(request, error_view(exc)[0], materijal, HTTP_UNPROCESSABLE, values)
        from fabagent.web.api import result_template
        return page(request, result_template(job), preview_ctx(job))

    @app.post("/upload/retrace", response_class=HTMLResponse)
    async def upload_retrace(request: Request):
        """Ponovna obrada istog izvornog fajla sa drugim postavkama (bez ponovnog uploada)."""
        form = await request.form()
        src = store.get(str(form.get("job_id", "")))
        if src["type"] != "upload":
            raise OutputError(f"job {src['job_id']} nije upload")
        values = {k: str(form.get(k) or "").strip() for k in UPLOAD_VALUE_FIELDS}
        materijal = str(form.get("materijal") or src["params"]["materijal"])
        data = store.file_path(src["job_id"], src["sources"][0]).read_bytes()
        try:
            job = _process_upload(src["params"]["fajl"], data, materijal, values)
        except FabError as exc:
            ctx = dict(preview_ctx(src), banner=error_view(exc)[0], retrace_values=values)
            return page(request, "upload_result.html", ctx, HTTP_UNPROCESSABLE)
        return page(request, "upload_result.html", preview_ctx(job))

    # ------------------------------------------------------------ kamera
    def camera_ctx(banner=None, ok=None) -> dict:
        calib = calib_store.load()
        available, error = True, None
        size = None
        try:
            frame = camera.read()
            size = [int(frame.shape[1]), int(frame.shape[0])]
        except FabError as exc:
            available, error = False, str(exc)
        return {"camera_source": camera.describe(), "camera_ok": available, "camera_error": error,
                "frame_size": size, "calibration": calib, "banner": banner, "ok": ok,
                "corner_names": cam.CORNER_NAMES, "area_mm": C.LASER_WORK_AREA_MM}

    def _jpeg(frame) -> Response:
        return Response(cam.encode_jpeg(frame), media_type="image/jpeg",
                        headers={"Cache-Control": "no-store"})

    @app.get("/camera", response_class=HTMLResponse)
    def camera_page(request: Request):
        return page(request, "camera.html", camera_ctx())

    @app.get("/camera/status")
    def camera_status():
        calib = calib_store.load()
        return {"source": camera.describe(), "calibrated": calib is not None,
                "area_mm": list(C.LASER_WORK_AREA_MM), "px_per_mm": C.CAMERA_PX_PER_MM,
                "calibration": calib.to_dict() if calib else None}

    @app.get("/camera/snapshot.jpg")
    def camera_snapshot():
        return _jpeg(camera.read())

    @app.get("/camera/warped.jpg")
    def camera_warped():
        calib = calib_store.load()
        if calib is None:
            raise CameraUnavailable("kamera nije kalibrisana - otvori /camera i klikni 4 ugla radne povrsine")
        return _jpeg(cam.warp_to_workarea(camera.read(), calib))

    @app.get("/camera/stream")
    async def camera_stream(request: Request, frames: int = 0):
        """MJPEG stream (multipart/x-mixed-replace). frames>0 ogranicava broj slika (testovi)."""
        camera.read()                                  # greska odmah, prije pocetka streama

        async def gen():
            sent = 0
            while frames <= 0 or sent < frames:
                if await request.is_disconnected():
                    break
                frame = await run_in_threadpool(camera.read)
                jpg = cam.encode_jpeg(frame)
                yield (b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                       + str(len(jpg)).encode() + b"\r\n\r\n" + jpg + b"\r\n")
                sent += 1
                await asyncio.sleep(1.0 / C.CAMERA_STREAM_FPS)

        return StreamingResponse(gen(), media_type="multipart/x-mixed-replace; boundary=frame",
                                 headers={"Cache-Control": "no-store"})

    @app.post("/camera/calibrate", response_class=HTMLResponse)
    async def camera_calibrate(request: Request):
        form = await request.form()
        try:
            pts = []
            for i in range(1, 5):
                x = _parse_number(f"x{i}", str(form.get(f"x{i}") or "").strip(), float)
                y = _parse_number(f"y{i}", str(form.get(f"y{i}") or "").strip(), float)
                if x is None or y is None:
                    raise MissingParam("kalibracija", [f"tacka {i} ({cam.CORNER_NAMES[i - 1]})"])
                pts.append([x, y])
            frame = camera.read()
            size = (int(frame.shape[1]), int(frame.shape[0]))
            fw = _parse_number("sirina_slike", str(form.get("frame_w") or "").strip(), int)
            fh = _parse_number("visina_slike", str(form.get("frame_h") or "").strip(), int)
            if (fw, fh) != size:
                raise InvalidParam(f"kliknuto na slici {fw}x{fh}, a kamera sada daje {size[0]}x{size[1]} - osvjezi sliku")
            calib = cam.compute_calibration(pts, size, C.LASER_WORK_AREA_MM, camera.describe())
            calib_store.save(calib)
        except FabError as exc:
            return page(request, "camera.html", camera_ctx(banner=error_view(exc)[0]), HTTP_UNPROCESSABLE)
        return page(request, "camera.html", camera_ctx(ok="Kalibracija sacuvana."))

    @app.exception_handler(CameraUnavailable)
    async def _camera_unavailable(request: Request, exc: CameraUnavailable):
        if request.url.path.endswith((".jpg", "/stream")):
            return Response(str(exc), status_code=HTTP_SERVICE_UNAVAILABLE, media_type="text/plain; charset=utf-8")
        return page(request, "error.html", {"banner": error_view(exc)[0]}, HTTP_SERVICE_UNAVAILABLE)

    @app.post("/upload/approve", response_class=HTMLResponse)
    async def upload_approve(request: Request):
        form = dict(await request.form())
        job = store.get(str(form.get("job_id", "")))
        if job["type"] != "upload":
            raise OutputError(f"job {job['job_id']} nije upload")
        try:
            job = store.approve(job["job_id"])
        except FabError as exc:
            return page(request, "upload_result.html", dict(preview_ctx(job), banner=error_view(exc)[0]),
                        HTTP_UNPROCESSABLE)
        return page(request, "upload_result.html", preview_ctx(job))

    from fabagent.web import api as _api
    _api.register(app, store, page, error_view, preview_ctx)
    from fabagent.web import workspace_api as _ws
    _ws.register(app, page)

    @app.get("/favicon.ico")
    def favicon():
        return RedirectResponse("/static/favicon.svg")

    return app


app = create_app()

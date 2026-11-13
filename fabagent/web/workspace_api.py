"""Radni ekran (/radni): stranica i JSON API za crtanje, geometriju, tekst, uvoz i G-code.

Sve POST rute traze Content-Type: application/json (isto pravilo kao /api/* u api.py). Server samo
racuna - na laser salje browser preko Web Serial-a.
"""

from __future__ import annotations

import base64
import binascii

from fastapi import Request
from fastapi.responses import HTMLResponse

from fabagent.core import config as C
from fabagent.core import polybool as PB
from fabagent.core import workspace as W
from fabagent.core.errors import FabError, InvalidParam, MissingParam
from fabagent.web.api import _err, _json_body

RING_DECIMALS = 4
TRACE_THRESHOLD_DEFAULT = 128         # polazna vrijednost u dijalogu "Trasiraj sliku" (korisnik je mijenja)


def _rings_out(rings) -> list:
    return [[[round(float(x), RING_DECIMALS), round(float(y), RING_DECIMALS)] for x, y in r] for r in rings]


def _shrink_for_trace(raw: bytes) -> bytes:
    """Sliku sa vise piksela od config.IMAGE_MAX_PIXELS smanji (sa usrednjavanjem) i vrati kao PNG."""
    import math

    import cv2
    import numpy as np
    img = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_UNCHANGED)
    if img is None:
        return raw                                   # neka je odbije trasiranje sa svojom porukom
    h, w = img.shape[:2]
    if w * h <= C.IMAGE_MAX_PIXELS:
        return raw
    k = math.sqrt(C.IMAGE_MAX_PIXELS / (w * h))
    small = cv2.resize(img, (max(1, int(w * k)), max(1, int(h * k))), interpolation=cv2.INTER_AREA)
    return cv2.imencode(".png", small)[1].tobytes()


def register(app, page) -> None:
    @app.get("/radni", response_class=HTMLResponse)
    def workspace_page(request: Request):
        lo, hi = (v * 60.0 for v in C.LAYER_SPEED_RANGE_MM_S)
        cfg = {"bed": list(C.LASER_WORK_AREA_MM), "baud": C.GRBL_BAUD_RATE, "home": C.GRBL_HOME_CMD,
               "layerCount": W.LAYER_COUNT, "modes": list(W.MODES), "imageModes": list(C.RASTER_MODES),
               "dither": list(C.DITHER_ALGORITHMS), "machine": C.LASER_MACHINE_NAME,
               "ranges": {"speed": [lo, hi], "power": list(C.LAYER_POWER_RANGE_PCT),
                          "passes": list(C.LAYER_PASSES_RANGE), "interval": list(C.LAYER_INTERVAL_RANGE_MM)},
               "textHeight": list(W.TEXT_HEIGHT_RANGE_MM), "traceThreshold": TRACE_THRESHOLD_DEFAULT,
               "jogFeed": C.GRBL_TRAVEL_FEED_MM_MIN}
        return page(request, "workspace.html", {"ws_cfg": cfg})

    @app.get("/api/ws/fonts")
    def ws_fonts():
        return {"ok": True, "fonts": W.list_fonts()}

    @app.post("/api/ws/text")
    async def ws_text(request: Request):
        try:
            body = await _json_body(request)
            return dict(W.text_path(body.get("text"), body.get("font"), body.get("height_mm")), ok=True)
        except FabError as exc:
            return _err(exc)

    @app.post("/api/ws/boolean")
    async def ws_boolean(request: Request):
        """{"op": "union"|"intersection"|"difference", "shapes": [[prsten, ...], ...]} - prsten = [[x, y], ...]"""
        try:
            body = await _json_body(request)
            op, shapes = body.get("op"), body.get("shapes")
            if not isinstance(shapes, list) or len(shapes) < 2:
                raise InvalidParam("shapes: potrebna su bar 2 oblika")
            if op == "union":
                rings = PB.union(shapes)
            elif len(shapes) != 2:
                raise InvalidParam("presjek i oduzimanje rade sa tacno 2 oblika")
            else:
                rings = PB.boolean(shapes[0], shapes[1], op)
        except FabError as exc:
            return _err(exc)
        return {"ok": True, "rings": _rings_out(rings), "area_mm2": round(PB.area(rings), 3) if rings else 0.0}

    @app.post("/api/ws/offset")
    async def ws_offset(request: Request):
        """{"rings": [prsten, ...], "distance": mm} - plus prema van, minus prema unutra."""
        try:
            body = await _json_body(request)
            rings = PB.offset(body.get("rings"), body.get("distance"))
        except FabError as exc:
            return _err(exc)
        return {"ok": True, "rings": _rings_out(rings)}

    @app.post("/api/ws/gcode")
    async def ws_gcode(request: Request):
        """{"layers": [...], "shapes": [...], "images": [...], "frame": false} -> G-code (tekst) i statistika."""
        try:
            body = await _json_body(request)
            frame = body.get("frame", False)
            if not isinstance(frame, bool):
                raise InvalidParam("frame mora biti true/false")
            res = W.frame(body) if frame else W.generate(body)
        except FabError as exc:
            return _err(exc)
        return {"ok": True, "frame": frame, "gcode": res.text, "stats": res.stats}

    @app.post("/api/ws/convert")
    async def ws_convert(request: Request):
        """{"name": "crtez.dxf", "data": "<base64>", "dxf_units": "mm"} -> SVG tekst (DXF / PDF sa vektorima)."""
        from fabagent.core import converter
        try:
            body = await _json_body(request)
            name, data = body.get("name"), body.get("data")
            if not isinstance(name, str) or not isinstance(data, str) or not name or not data:
                raise MissingParam("uvoz", ["name", "data"])
            try:
                raw = base64.b64decode(data, validate=True)
            except (binascii.Error, ValueError):
                raise InvalidParam("data: neispravan base64") from None
            conv = converter.convert(name, raw, dxf_units=body.get("dxf_units") or None)
        except FabError as exc:
            return _err(exc)
        return {"ok": True, "svg": conv.svg.decode("utf-8", errors="replace"), "notes": conv.notes,
                "format": conv.source_format}

    @app.post("/api/ws/lbrn")
    async def ws_lbrn(request: Request):
        """{"name": "posao.lbrn2", "data": "<base64>"} -> slojevi, oblici (SVG putanje u mm) i slike iz LightBurn projekta."""
        from fabagent.core import lbrn
        try:
            body = await _json_body(request)
            data = body.get("data")
            if not isinstance(data, str) or not data:
                raise MissingParam("uvoz", ["data"])
            try:
                raw = base64.b64decode(data, validate=True)
            except (binascii.Error, ValueError):
                raise InvalidParam("data: neispravan base64") from None
            return dict(lbrn.parse(raw), ok=True)
        except FabError as exc:
            return _err(exc)

    @app.post("/api/ws/trace")
    async def ws_trace(request: Request):
        """{"data": "data:image/...;base64,...", "threshold": 0..255, "width_mm": mm} -> SVG kontura slike.
        Slika veca od config.IMAGE_MAX_PIXELS se prije trasiranja smanji (trasiranje je sporo na velikim slikama)."""
        from fabagent.core import converter
        try:
            body = await _json_body(request)
            raw = _shrink_for_trace(W.decode_data_url(body.get("data")))
            threshold = body.get("threshold")
            if isinstance(threshold, bool) or not isinstance(threshold, int):
                raise InvalidParam("threshold: mora biti cijeli broj 0..255")
            width = body.get("width_mm")
            if isinstance(width, bool) or not isinstance(width, (int, float)):
                raise InvalidParam("width_mm: mora biti broj")
            conv = converter.image_to_svg(raw, threshold, float(width))
        except FabError as exc:
            return _err(exc)
        return {"ok": True, "svg": conv.svg.decode("utf-8"), "notes": conv.notes}

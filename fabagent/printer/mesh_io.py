"""Citanje/pisanje binarnog STL-a i 3MF-a (stdlib + numpy)."""

from __future__ import annotations

import io
import struct
import xml.etree.ElementTree as ET
import zipfile

import numpy as np

from fabagent import __version__
from fabagent.printer.mesher import Body

STL_HEADER_LEN = 80
STL_RECORD = np.dtype([("normal", "<f4", 3), ("v", "<f4", (3, 3)), ("attr", "<u2")])
NS_3MF = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
NS_CT = "http://schemas.openxmlformats.org/package/2006/content-types"
NS_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
REL_3DMODEL = "http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"
MODEL_PATH = "3D/3dmodel.model"


def face_normals(v: np.ndarray, f: np.ndarray) -> np.ndarray:
    n = np.cross(v[f[:, 1]] - v[f[:, 0]], v[f[:, 2]] - v[f[:, 0]])
    ln = np.linalg.norm(n, axis=1, keepdims=True)
    return np.divide(n, ln, out=np.zeros_like(n), where=ln > 0)


def stl_bytes(vertices: np.ndarray, faces: np.ndarray, name: str) -> bytes:
    header = f"FabAgent {__version__} binary STL: {name}".encode("ascii", "replace")[:STL_HEADER_LEN]
    # zaglavlje NE smije pocinjati sa "solid" (zbunjuje ASCII detekciju)
    header = header.ljust(STL_HEADER_LEN, b" ")
    rec = np.zeros(len(faces), dtype=STL_RECORD)
    rec["normal"] = face_normals(vertices, faces)
    rec["v"] = vertices[faces]
    return header + struct.pack("<I", len(faces)) + rec.tobytes()


def read_stl(data: bytes) -> tuple[np.ndarray, np.ndarray]:
    """Binarni ili ASCII STL -> (vrhovi, trouglovi). Vrhovi se spajaju po tacnoj vrijednosti."""
    if len(data) >= 84:
        (n,) = struct.unpack("<I", data[80:84])
        if len(data) == 84 + n * STL_RECORD.itemsize:
            rec = np.frombuffer(data, dtype=STL_RECORD, count=n, offset=84)
            tri = rec["v"].astype(np.float64)
            return _index(tri)
    text = data.decode("ascii", errors="strict")
    if not text.lstrip().lower().startswith("solid"):
        raise ValueError("fajl nije ni binarni ni ASCII STL")
    coords = [list(map(float, line.split()[1:4])) for line in text.splitlines()
              if line.strip().lower().startswith("vertex")]
    if not coords or len(coords) % 3:
        raise ValueError("ASCII STL: broj vrhova nije djeljiv sa 3")
    return _index(np.array(coords).reshape(-1, 3, 3))


def _index(tri: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    verts, inv = np.unique(tri.reshape(-1, 3), axis=0, return_inverse=True)
    return verts, inv.reshape(-1, 3)


def _fmt(v: float) -> str:
    return f"{v:.6f}".rstrip("0").rstrip(".") or "0"


def threemf_bytes(bodies: list[Body]) -> bytes:
    ET.register_namespace("", NS_3MF)
    model = ET.Element(f"{{{NS_3MF}}}model", {"unit": "millimeter", "{http://www.w3.org/XML/1998/namespace}lang": "en-US"})
    meta = ET.SubElement(model, f"{{{NS_3MF}}}metadata", {"name": "Application"})
    meta.text = f"FabAgent {__version__}"
    res = ET.SubElement(model, f"{{{NS_3MF}}}resources")
    build = ET.SubElement(model, f"{{{NS_3MF}}}build")
    for oid, body in enumerate(bodies, start=1):
        obj = ET.SubElement(res, f"{{{NS_3MF}}}object", {"id": str(oid), "type": "model", "name": body.name})
        mesh = ET.SubElement(obj, f"{{{NS_3MF}}}mesh")
        vs = ET.SubElement(mesh, f"{{{NS_3MF}}}vertices")
        for x, y, z in body.vertices:
            ET.SubElement(vs, f"{{{NS_3MF}}}vertex", {"x": _fmt(x), "y": _fmt(y), "z": _fmt(z)})
        ts = ET.SubElement(mesh, f"{{{NS_3MF}}}triangles")
        for a, b, c in body.faces:
            ET.SubElement(ts, f"{{{NS_3MF}}}triangle", {"v1": str(a), "v2": str(b), "v3": str(c)})
        ET.SubElement(build, f"{{{NS_3MF}}}item", {"objectid": str(oid)})
    model_xml = b'<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(model, encoding="utf-8")
    content_types = (
        f'<?xml version="1.0" encoding="UTF-8"?>\n<Types xmlns="{NS_CT}">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>'
        "</Types>"
    )
    rels = (
        f'<?xml version="1.0" encoding="UTF-8"?>\n<Relationships xmlns="{NS_REL}">'
        f'<Relationship Target="/{MODEL_PATH}" Id="rel0" Type="{REL_3DMODEL}"/></Relationships>'
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", content_types)
        z.writestr("_rels/.rels", rels)
        z.writestr(MODEL_PATH, model_xml)
    return buf.getvalue()


def read_3mf(data: bytes) -> list[Body]:
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = set(z.namelist())
        for required in ("[Content_Types].xml", "_rels/.rels"):
            if required not in names:
                raise ValueError(f"3MF: nedostaje {required}")
        rels = ET.fromstring(z.read("_rels/.rels"))
        targets = [r.get("Target", "").lstrip("/") for r in rels
                   if r.get("Type") == REL_3DMODEL]
        if not targets or targets[0] not in names:
            raise ValueError("3MF: nema 3D modela u relacijama")
        model = ET.fromstring(z.read(targets[0]))
    if model.get("unit", "millimeter") != "millimeter":
        raise ValueError(f"3MF: jedinica mora biti millimeter (dobijeno {model.get('unit')})")
    ns = {"m": NS_3MF}
    bodies = []
    for obj in model.findall("m:resources/m:object", ns):
        vs = obj.findall("m:mesh/m:vertices/m:vertex", ns)
        ts = obj.findall("m:mesh/m:triangles/m:triangle", ns)
        v = np.array([[float(e.get("x")), float(e.get("y")), float(e.get("z"))] for e in vs])
        f = np.array([[int(e.get("v1")), int(e.get("v2")), int(e.get("v3"))] for e in ts], dtype=np.int64)
        if len(f) and (f.min() < 0 or f.max() >= len(v)):
            raise ValueError(f"3MF objekat {obj.get('id')}: indeks trougla van opsega")
        bodies.append(Body(obj.get("name") or f"object{obj.get('id')}", v, f))
    if not bodies:
        raise ValueError("3MF: nema objekata")
    return bodies

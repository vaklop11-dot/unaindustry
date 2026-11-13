"""Parametarski primitivi i sklapanje mesha (bez boolean biblioteka).

Princip: svako tijelo se sastavlja od dijelova koji su zatvoreni po konstrukciji
(prizma od poligona sa rupama, rotaciono tijelo). Dijelovi se lijepe po stranicama
koje se tacno poklapaju: takve stranice imaju identicne trouglove suprotne
orijentacije i uklanjaju se u paru. Sve ostalo (T-spojevi, preklapanja) ostaje
vidljivo validatoru - nista se tiho ne "popravlja".

Primitivi: kocka (box), cilindar, cijev, torus, ploca sa rupama (rupa), L-ugao sa
filetom (filet).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from fabagent.core import config as C
from fabagent.core import geometry2d as g2
from fabagent.core.errors import InvalidGeometry
from fabagent.printer.triangulate import triangulate

# Okviri (kolone = osa u, v, w u xyz). Svi su desnoruki (det = +1).
FRAME_XY = np.array([[1, 0, 0], [0, 1, 0], [0, 0, 1]], dtype=float).T   # u=x, v=y, w=z
FRAME_XZ = np.array([[1, 0, 0], [0, 0, 1], [0, -1, 0]], dtype=float).T  # u=x, v=z, w=-y
FRAME_YZ = np.array([[0, 1, 0], [0, 0, 1], [1, 0, 0]], dtype=float).T   # u=y, v=z, w=x

MESH_ARC = dict(tol=C.MESH_CHORD_TOLERANCE_MM, min_seg=C.MESH_MIN_SEGMENTS, max_seg=C.MESH_MAX_SEGMENTS)


def mesh_circle(cx: float, cy: float, r: float) -> np.ndarray:
    return g2.circle(cx, cy, r, **MESH_ARC)


def mesh_rounded_rect(x0, y0, x1, y1, r) -> np.ndarray:
    return g2.rounded_rect(x0, y0, x1, y1, r, **MESH_ARC)


def mesh_arc(cx, cy, r, a0, a1) -> np.ndarray:
    return g2.arc(cx, cy, r, a0, a1, **MESH_ARC)


def _key(p: np.ndarray) -> tuple:
    return tuple(np.round(p, C.MESH_MERGE_DECIMALS))


def _quad(q: list[np.ndarray]) -> list[tuple]:
    """Kanonska podjela cetverougla: dijagonala iz leksikografski najmanjeg vrha.
    Dva dijela koja dijele isti cetverougao dobiju iste trouglove."""
    keys = [_key(p) for p in q]
    k = min(range(4), key=lambda i: keys[i])
    if k % 2 == 0:
        return [(q[0], q[1], q[2]), (q[0], q[2], q[3])]
    return [(q[1], q[2], q[3]), (q[1], q[3], q[0])]


def prism(outer: np.ndarray, holes: list[np.ndarray], w0: float, w1: float,
          frame: np.ndarray = FRAME_XY) -> np.ndarray:
    """Zatvorena prizma: poligon (u, v) sa rupama, izvucen od w0 do w1. Vraca (T,3,3)."""
    if w1 <= w0:
        raise InvalidGeometry("prizma mora imati pozitivnu debljinu")
    outer = g2.ensure_orientation(np.asarray(outer, float), ccw=True)
    holes = [g2.ensure_orientation(np.asarray(h, float), ccw=False) for h in holes]
    pts2, tris = triangulate(outer, holes)

    def to3(uv: np.ndarray, w: float) -> np.ndarray:
        local = np.column_stack([uv, np.full(len(uv), w)])
        return local @ frame.T

    top = to3(pts2, w1)
    bot = to3(pts2, w0)
    out = [top[tris], bot[tris[:, ::-1]]]
    walls = []
    for ring in [outer] + holes:
        r0, r1 = to3(ring, w0), to3(ring, w1)
        n = len(ring)
        for i in range(n):
            j = (i + 1) % n
            walls.extend(_quad([r0[i], r0[j], r1[j], r1[i]]))
    out.append(np.array(walls))
    return np.concatenate(out, axis=0)


def lathe(profile: np.ndarray, segments: int) -> np.ndarray:
    """Rotaciono tijelo oko z ose. profile: zatvorena kontura (r, z), r > 0."""
    prof = g2.ensure_orientation(np.asarray(profile, float), ccw=True)
    if (prof[:, 0] <= C.GEOMETRY_EPS_MM).any():
        raise InvalidGeometry("profil rotacionog tijela mora imati r > 0")
    ang = np.arange(segments) * (2 * math.pi / segments)
    ca, sa = np.cos(ang), np.sin(ang)
    tris = []
    n = len(prof)
    for i in range(n):
        p, q = prof[i], prof[(i + 1) % n]
        for k in range(segments):
            m = (k + 1) % segments
            pk = np.array([p[0] * ca[k], p[0] * sa[k], p[1]])
            pm = np.array([p[0] * ca[m], p[0] * sa[m], p[1]])
            qk = np.array([q[0] * ca[k], q[0] * sa[k], q[1]])
            qm = np.array([q[0] * ca[m], q[0] * sa[m], q[1]])
            tris.extend(_quad([pk, pm, qm, qk]))
    return np.array(tris)


# ------------------------------------------------------------- primitivi
def box(x0, y0, z0, x1, y1, z1) -> np.ndarray:
    return prism(np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1]], float), [], z0, z1)


def cylinder(cx, cy, r, z0, z1) -> np.ndarray:
    return prism(mesh_circle(cx, cy, r), [], z0, z1)


def tube(cx, cy, r_in, r_out, z0, z1) -> np.ndarray:
    return prism(mesh_circle(cx, cy, r_out), [mesh_circle(cx, cy, r_in)], z0, z1)


def torus(major_r: float, minor_r: float) -> np.ndarray:
    if minor_r >= major_r:
        raise InvalidGeometry("torus: mali radijus mora biti manji od velikog")
    prof = mesh_circle(major_r, 0.0, minor_r)
    seg = g2.arc_segments(major_r + minor_r, **MESH_ARC)
    return lathe(prof, seg)


def plate_with_holes(outline: np.ndarray, holes: list[np.ndarray], z0: float, z1: float) -> np.ndarray:
    return prism(outline, holes, z0, z1)


def fillet_corner_profile(t: float, r: float) -> np.ndarray:
    """Profil (u, v) unutrasnjeg ugla L-profila debljine t sa filetom radijusa r.
    Zauzima [0, t+r] x [0, t+r]; zalijepi se na krakove na u=t+r i v=t+r."""
    x0 = t + r
    pts = [np.array([[0.0, 0.0], [x0, 0.0], [x0, t]])]
    if r > C.GEOMETRY_EPS_MM:
        arc = mesh_arc(x0, x0, r, -math.pi / 2, -math.pi)
        pts.append(arc[1:-1])
    pts.append(np.array([[t, x0], [0.0, x0]]))
    return g2.dedupe(np.vstack(pts))


# ------------------------------------------------------------- sklapanje
@dataclass
class Body:
    name: str
    vertices: np.ndarray   # (V, 3)
    faces: np.ndarray      # (F, 3) int

    def translated(self, dx=0.0, dy=0.0, dz=0.0) -> "Body":
        return Body(self.name, self.vertices + np.array([dx, dy, dz]), self.faces.copy())


def assemble(name: str, pieces: list[np.ndarray]) -> Body:
    """Spaja dijelove: zajednicki vrhovi + uklanjanje parova zalijepljenih trouglova."""
    soup = np.concatenate(pieces, axis=0)
    flat = np.round(soup.reshape(-1, 3), C.MESH_MERGE_DECIMALS)
    verts, inv = np.unique(flat, axis=0, return_inverse=True)
    faces = inv.reshape(-1, 3)
    degenerate = (faces[:, 0] == faces[:, 1]) | (faces[:, 1] == faces[:, 2]) | (faces[:, 0] == faces[:, 2])
    if degenerate.any():
        raise InvalidGeometry(f"{name}: {int(degenerate.sum())} degenerisanih trouglova pri sklapanju")
    sorted_f = np.sort(faces, axis=1)
    _, first, inverse, counts = np.unique(sorted_f, axis=0, return_index=True,
                                          return_inverse=True, return_counts=True)
    inverse = inverse.reshape(-1)
    if (counts > 2).any():
        raise InvalidGeometry(f"{name}: vise od dva trougla na istom mjestu")
    remove = np.zeros(len(faces), dtype=bool)
    for g in np.nonzero(counts == 2)[0]:
        idx = np.nonzero(inverse == g)[0]
        a, b = faces[idx[0]], faces[idx[1]]
        ra = np.roll(a, -int(np.argmin(a)))
        rb = np.roll(b, -int(np.argmin(b)))
        if np.array_equal(ra, rb):
            raise InvalidGeometry(f"{name}: dupli trougao iste orijentacije (preklapanje dijelova)")
        remove[idx] = True
    faces = faces[~remove]
    used = np.unique(faces)
    remap = np.full(len(verts), -1, dtype=np.int64)
    remap[used] = np.arange(len(used))
    return Body(name, verts[used], remap[faces])


def merge_bodies(bodies: list[Body]) -> tuple[np.ndarray, np.ndarray]:
    verts, faces, off = [], [], 0
    for b in bodies:
        verts.append(b.vertices)
        faces.append(b.faces + off)
        off += len(b.vertices)
    return np.vstack(verts), np.vstack(faces)

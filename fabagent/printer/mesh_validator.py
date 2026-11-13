"""Validacija mesha za FDM: zatvorenost (watertight), manifold ivice, konzistentna
orijentacija, pozitivan volumen, degenerisani trouglovi, min debljina zida
(ray casting), radni volumen printera. Neispravan mesh -> greska, bez popravki."""

from __future__ import annotations

import numpy as np
import trimesh

from fabagent.core import config as C


def _edge_stats(faces: np.ndarray) -> dict:
    directed = np.concatenate([faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]]])
    undirected = np.sort(directed, axis=1)
    _, ucounts = np.unique(undirected, axis=0, return_counts=True)
    _, dcounts = np.unique(directed, axis=0, return_counts=True)
    return {
        "edges": int(len(ucounts)),
        "boundary_edges": int((ucounts == 1).sum()),
        "non_manifold_edges": int((ucounts > 2).sum()),
        "inconsistent_directed_edges": int((dcounts > 1).sum()),
    }


def _components(n_vertices: int, faces: np.ndarray) -> int:
    parent = np.arange(n_vertices)

    def find(x):
        root = x
        while parent[root] != root:
            root = parent[root]
        while parent[x] != root:
            parent[x], x = root, parent[x]
        return root

    for a, b, c in faces:
        ra, rb, rc = find(a), find(b), find(c)
        parent[rb] = ra
        parent[find(rc)] = ra
    used = np.unique(faces)
    return len({find(v) for v in used})


def ray_wall_thickness(vertices: np.ndarray, faces: np.ndarray,
                       max_samples: int = C.WALL_RAY_MAX_SAMPLES) -> dict:
    """Debljina zida: iz centra (uzorka) trouglova zraka ide unutra (-normala) do
    prvog presjeka (Moller-Trumbore). Vraca min debljinu i njenu lokaciju."""
    v0, v1, v2 = vertices[faces[:, 0]], vertices[faces[:, 1]], vertices[faces[:, 2]]
    e1, e2 = v1 - v0, v2 - v0
    normals = np.cross(e1, e2)
    ln = np.linalg.norm(normals, axis=1)
    valid_faces = np.nonzero(ln > 0)[0]
    stride = max(1, int(np.ceil(len(valid_faces) / max_samples)))
    sample = valid_faces[::stride]
    dirs = -normals[sample] / ln[sample, None]
    origins = (v0[sample] + v1[sample] + v2[sample]) / 3.0 + dirs * C.WALL_RAY_START_OFFSET_MM
    tmin_all = np.full(len(sample), np.inf)
    eps = 1e-12
    chunk = max(1, min(C.RAY_CHUNK, C.RAY_ELEMENT_BUDGET // max(1, len(faces))))   # ogranicena memorija
    for s in range(0, len(sample), chunk):
        O = origins[s:s + chunk][:, None, :]
        D = dirs[s:s + chunk][:, None, :]
        h = np.cross(D, e2[None, :, :])
        a = np.einsum("ijk,ijk->ij", np.broadcast_to(e1[None], h.shape), h)
        ok = np.abs(a) > eps
        f = np.divide(1.0, a, out=np.zeros_like(a), where=ok)
        sv = O - v0[None, :, :]
        u = f * np.einsum("ijk,ijk->ij", sv, h)
        q = np.cross(sv, np.broadcast_to(e1[None], sv.shape))
        v = f * np.einsum("ijk,ijk->ij", np.broadcast_to(D, q.shape), q)
        t = f * np.einsum("ijk,ijk->ij", np.broadcast_to(e2[None], q.shape), q)
        hit = ok & (u >= -eps) & (v >= -eps) & (u + v <= 1 + eps) & (t > eps)
        tt = np.where(hit, t, np.inf)
        tmin_all[s:s + chunk] = tt.min(axis=1)
    misses = int(np.isinf(tmin_all).sum())
    finite = np.isfinite(tmin_all)
    if not finite.any():
        return {"min_mm": None, "samples": int(len(sample)), "misses": misses}
    k = int(np.argmin(np.where(finite, tmin_all, np.inf)))
    thickness = float(tmin_all[k] + C.WALL_RAY_START_OFFSET_MM)
    return {"min_mm": round(thickness, 4), "samples": int(len(sample)), "misses": misses,
            "at": [round(float(c), 3) for c in (origins[k] - dirs[k] * C.WALL_RAY_START_OFFSET_MM)]}


def validate_mesh(vertices: np.ndarray, faces: np.ndarray, name: str = "mesh",
                  min_wall: float = C.FDM_MIN_WALL_MM, check_build_volume: bool = True,
                  wall_override: dict | None = None) -> dict:
    """wall_override: debljina zida izracunata analiticki od strane pozivaoca (npr. mapa visina:
    debljina = visina u svakoj tacki) - tada se preskace spora provjera zrakama."""
    errors: list[str] = []
    warnings: list[str] = []
    stats: dict = {"name": name, "vertices": int(len(vertices)), "faces": int(len(faces))}
    if len(faces) == 0 or len(vertices) == 0:
        return {"ok": False, "errors": [f"{name}: prazan mesh"], "warnings": [], "stats": stats}
    if not np.isfinite(vertices).all():
        errors.append(f"{name}: koordinate nisu konacni brojevi")
    if faces.min() < 0 or faces.max() >= len(vertices):
        return {"ok": False, "errors": [f"{name}: indeksi trouglova van opsega"], "warnings": [], "stats": stats}

    areas = 0.5 * np.linalg.norm(np.cross(vertices[faces[:, 1]] - vertices[faces[:, 0]],
                                          vertices[faces[:, 2]] - vertices[faces[:, 0]]), axis=1)
    n_degen = int((areas < C.MESH_MIN_FACE_AREA_MM2).sum())
    if n_degen:
        errors.append(f"{name}: {n_degen} degenerisanih trouglova (povrsina ~0)")

    es = _edge_stats(faces)
    stats.update(es)
    if es["boundary_edges"]:
        errors.append(f"{name}: mesh nije zatvoren ({es['boundary_edges']} otvorenih ivica)")
    if es["non_manifold_edges"]:
        errors.append(f"{name}: {es['non_manifold_edges']} non-manifold ivica")
    if es["inconsistent_directed_edges"]:
        errors.append(f"{name}: nekonzistentna orijentacija trouglova ({es['inconsistent_directed_edges']} ivica)")

    tm = trimesh.Trimesh(vertices=vertices, faces=faces, process=False)
    stats["watertight"] = bool(tm.is_watertight)
    stats["winding_consistent"] = bool(tm.is_winding_consistent)
    stats["is_volume"] = bool(tm.is_volume)
    if not tm.is_watertight and not es["boundary_edges"] and not es["non_manifold_edges"]:
        errors.append(f"{name}: trimesh: mesh nije watertight")
    volume = float(tm.volume)
    stats["volume_mm3"] = round(volume, 3)
    stats["area_mm2"] = round(float(tm.area), 3)
    if volume <= 0:
        errors.append(f"{name}: volumen nije pozitivan ({volume:.3f} mm3) - normale okrenute unutra")
    stats["bodies"] = _components(len(vertices), faces)

    lo, hi = vertices.min(axis=0), vertices.max(axis=0)
    stats["bbox_mm"] = {"min": [round(float(x), 4) for x in lo], "max": [round(float(x), 4) for x in hi],
                        "size": [round(float(x), 4) for x in hi - lo]}
    if check_build_volume:
        bv = C.PRINTER_BUILD_VOLUME_MM
        if any(s > b + C.GEOMETRY_EPS_MM for s, b in zip(hi - lo, bv)):
            errors.append(f"{name}: dimenzije {tuple(round(float(s), 2) for s in hi - lo)} mm "
                          f"premasuju radni volumen {bv} mm")
        if abs(lo[2]) > C.GEOMETRY_EPS_MM:
            warnings.append(f"{name}: najniza tacka nije na z=0 (z_min={lo[2]:.4f})")

    if not errors:
        wall = wall_override if wall_override is not None else ray_wall_thickness(vertices, faces)
        stats["wall_thickness"] = wall
        if wall["min_mm"] is None:
            errors.append(f"{name}: provjera debljine zida nije pogodila nijednu povrsinu")
        elif wall["min_mm"] < min_wall - C.WALL_THICKNESS_TOLERANCE_MM:
            errors.append(f"{name}: min debljina zida {wall['min_mm']:.3f} mm < {min_wall} mm "
                          f"(kod tacke {wall['at']})")
        if wall["misses"]:
            warnings.append(f"{name}: {wall['misses']} zraka bez presjeka pri provjeri zida")
    return {"ok": not errors, "errors": errors, "warnings": warnings, "stats": stats}

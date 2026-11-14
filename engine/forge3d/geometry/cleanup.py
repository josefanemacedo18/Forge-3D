"""Limpeza e reparo de malhas antes da análise."""

from __future__ import annotations

import numpy as np


def weld(vertices: np.ndarray, faces: np.ndarray, tol: float) -> tuple[np.ndarray, np.ndarray]:
    """Funde vértices a menos de ``tol`` (mm) e remove faces degeneradas/duplicadas."""
    if len(vertices) == 0 or len(faces) == 0:
        return vertices.reshape(-1, 3), faces.reshape(-1, 3)
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components
    from scipy.spatial import cKDTree

    n = len(vertices)
    pairs = cKDTree(vertices).query_pairs(r=tol, output_type="ndarray")
    if len(pairs):
        graph = coo_matrix((np.ones(len(pairs), dtype=np.int8), (pairs[:, 0], pairs[:, 1])), shape=(n, n))
        _, labels = connected_components(graph, directed=False)
    else:
        labels = np.arange(n)
    _, first, inverse = np.unique(labels, return_index=True, return_inverse=True)
    new_vertices = vertices[first]
    new_faces = inverse[faces]
    # Faces degeneradas (índices repetidos).
    ok = (new_faces[:, 0] != new_faces[:, 1]) & (new_faces[:, 1] != new_faces[:, 2]) & (new_faces[:, 0] != new_faces[:, 2])
    new_faces = new_faces[ok]
    # Faces com área (quase) nula.
    if len(new_faces):
        tri = new_vertices[new_faces]
        area2 = np.linalg.norm(np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0]), axis=1)
        new_faces = new_faces[area2 > (tol * tol) * 1e-3]
    # Duplicadas, inclusive com orientação invertida (faces de dois lados).
    if len(new_faces):
        key = np.sort(new_faces, axis=1)
        _, first = np.unique(key, axis=0, return_index=True)
        new_faces = new_faces[np.sort(first)]
    # Remove vértices órfãos.
    used = np.unique(new_faces)
    remap = -np.ones(len(new_vertices), dtype=np.int64)
    remap[used] = np.arange(len(used))
    return new_vertices[used], remap[new_faces]


def to_trimesh(vertices: np.ndarray, faces: np.ndarray):
    import trimesh

    return trimesh.Trimesh(vertices=vertices, faces=faces, process=False, validate=False)


def split_components(mesh) -> list:
    """Componentes conexos pelas arestas compartilhadas."""
    if len(mesh.faces) == 0:
        return []
    comps = mesh.split(only_watertight=False)
    return [c for c in comps if len(c.faces) > 0]


def repair(mesh) -> tuple[object, bool]:
    """Tenta tornar a malha fechada e com normais para fora.

    Retorna (malha, fechada).
    """
    import trimesh

    m = mesh.copy()
    try:
        trimesh.repair.fix_winding(m)
    except Exception:
        pass
    if not m.is_watertight:
        try:
            trimesh.repair.fill_holes(m)
        except Exception:
            pass
    closed = bool(m.is_watertight and m.is_winding_consistent)
    if closed:
        try:
            trimesh.repair.fix_inversion(m)
        except Exception:
            pass
        closed = bool(m.volume > 0)
    return m, closed


def diag(mesh) -> float:
    b = mesh.bounds
    return float(np.linalg.norm(b[1] - b[0]))

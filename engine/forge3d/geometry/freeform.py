"""Preparo de formas livres para o Revit: malha fechada e leve.

O Revit cria sólidos de forma livre a partir de malhas fechadas (manifold).
Malhas abertas são fechadas por remalhamento volumétrico; malhas pesadas são
simplificadas para manter a família rápida (padrão: até ~15 mil triângulos no
total, distribuídos pelas peças).
"""

from __future__ import annotations

import numpy as np

from .primitives import FreeformMesh


def _to_mesh(prim: FreeformMesh):
    import trimesh

    return trimesh.Trimesh(np.asarray(prim.vertices, float), np.asarray(prim.faces, int), process=True)


def voxel_remesh(mesh, max_cells: int = 160):
    """Fecha uma malha aberta por voxelização + marching cubes."""
    ext = float((mesh.bounds[1] - mesh.bounds[0]).max())
    pitch = max(ext / max_cells, 0.5)
    vox = mesh.voxelized(pitch=pitch)
    try:
        vox = vox.fill()
    except Exception:
        pass
    mc = vox.marching_cubes
    mc.apply_transform(vox.transform)
    return mc


def prepare(prim: FreeformMesh, max_faces: int) -> FreeformMesh:
    import trimesh

    mesh = _to_mesh(prim)
    notes = list(prim.notes)
    if not mesh.is_watertight:
        try:
            trimesh.repair.fill_holes(mesh)
        except Exception:
            pass
    if not mesh.is_watertight:
        mesh = voxel_remesh(mesh)
        notes.append("superfície aberta fechada por remalhamento volumétrico")
    if len(mesh.faces) > max_faces:
        try:
            simplified = mesh.simplify_quadric_decimation(face_count=max(int(max_faces), 64))
            if len(simplified.faces) > 0:
                mesh = simplified
                notes.append(f"simplificada para {len(mesh.faces)} triângulos")
        except Exception as exc:  # pragma: no cover - depende da biblioteca nativa
            notes.append(f"simplificação indisponível ({exc})")
    try:
        trimesh.repair.fix_winding(mesh)
        trimesh.repair.fix_inversion(mesh)
    except Exception:
        pass
    out = FreeformMesh(
        vertices=np.asarray(mesh.vertices, float),
        faces=np.asarray(mesh.faces, np.int64),
        watertight=bool(mesh.is_watertight),
    )
    out.name, out.source, out.color, out.material_name = prim.name, prim.source, prim.color, prim.material_name
    out.volume = float(abs(mesh.volume)) if mesh.is_watertight else prim.volume
    out.detail = prim.detail
    out.deviation = prim.deviation
    out.notes = notes
    return out


def distribute_budget(prims: list[FreeformMesh], total_faces: int, min_faces: int = 200) -> list[int]:
    """Divide o orçamento de triângulos proporcionalmente à área de cada peça."""
    if not prims:
        return []
    areas = []
    for p in prims:
        tri = p.vertices[p.faces]
        a = 0.5 * np.linalg.norm(np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0]), axis=1).sum()
        areas.append(max(float(a), 1e-9))
    areas = np.asarray(areas)
    share = areas / areas.sum()
    return [max(min_faces, int(total_faces * s)) for s in share]

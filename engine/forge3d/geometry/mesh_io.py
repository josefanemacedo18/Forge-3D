"""Leitura de arquivos 3D preservando partes (objetos/grupos) e cores.

Formatos lidos diretamente: OBJ, STL, PLY, OFF, GLB/GLTF, DAE (Collada), 3MF.
FBX, 3DS, MAX e SKP são convertidos antes pelo plugin (3ds Max em lote ou o
importador do próprio Revit) para OBJ em milímetros com Z para cima.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from ..errors import EngineError

SUPPORTED = {".obj", ".stl", ".ply", ".off", ".glb", ".gltf", ".dae", ".3mf"}
# Formatos cuja convenção é Y para cima.
Y_UP_FORMATS = {".glb", ".gltf"}

_HEADER_RE = re.compile(r"#\s*forge3d[^\n]*", re.IGNORECASE)


@dataclass
class Part:
    name: str
    vertices: np.ndarray  # (n, 3) float64
    faces: np.ndarray  # (m, 3) int64
    color: tuple[int, int, int] = (180, 180, 180)
    material: str = ""


@dataclass
class LoadedModel:
    parts: list[Part]
    path: str
    format: str
    declared_units: str | None = None  # "mm", "cm", "m", "in", "ft" quando o arquivo informa
    declared_up: str | None = None  # "Z" ou "Y"
    warnings: list[str] = field(default_factory=list)


def _read_forge3d_header(path: Path) -> dict[str, str]:
    """Lê o cabeçalho '# forge3d units=mm up=Z' escrito pelos nossos exportadores."""
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as fh:
            head = fh.read(2048)
    except OSError:
        return {}
    m = _HEADER_RE.search(head)
    if not m:
        return {}
    out = {}
    for kv in m.group(0).split()[1:]:
        if "=" in kv:
            k, v = kv.split("=", 1)
            out[k.strip().lower()] = v.strip()
    return out


def _color_of(geom) -> tuple[tuple[int, int, int], str]:
    visual = getattr(geom, "visual", None)
    name = ""
    try:
        if visual is not None and visual.kind == "texture":
            mat = visual.material
            name = getattr(mat, "name", "") or ""
            col = None
            for attr in ("diffuse", "main_color", "baseColorFactor"):
                c = getattr(mat, attr, None)
                if c is not None:
                    col = np.asarray(c).ravel()
                    break
            if col is not None and col.size >= 3:
                if col.max() <= 1.0 and col.dtype.kind == "f":
                    col = col * 255
                return (int(col[0]), int(col[1]), int(col[2])), name
        elif visual is not None and visual.kind == "face":
            fc = np.asarray(visual.face_colors)
            if len(fc):
                vals, counts = np.unique(fc[:, :3], axis=0, return_counts=True)
                c = vals[counts.argmax()]
                return (int(c[0]), int(c[1]), int(c[2])), name
        elif visual is not None and visual.kind == "vertex":
            vc = np.asarray(visual.vertex_colors)
            if len(vc):
                c = np.median(vc[:, :3], axis=0)
                return (int(c[0]), int(c[1]), int(c[2])), name
    except Exception:  # cores são opcionais; nunca impedem a leitura
        pass
    return (180, 180, 180), name


def load_model(path: str | os.PathLike) -> LoadedModel:
    import trimesh

    p = Path(path)
    if not p.exists():
        raise EngineError(f"Arquivo não encontrado: {p}")
    ext = p.suffix.lower()
    if ext not in SUPPORTED:
        raise EngineError(
            f"Formato '{ext}' não é lido diretamente pelo motor. Formatos aceitos: "
            + ", ".join(sorted(SUPPORTED))
        )
    header = _read_forge3d_header(p) if ext == ".obj" else {}
    kwargs = {}
    if ext == ".obj":
        kwargs = {"split_objects": True, "group_material": True}
    try:
        loaded = trimesh.load(str(p), force="scene", process=False, **kwargs)
    except Exception as exc:  # pragma: no cover - mensagens do trimesh variam
        raise EngineError(f"Não foi possível ler '{p.name}': {exc}") from exc

    parts: list[Part] = []
    warnings: list[str] = []
    scene = loaded
    # Geometria com as transformações do grafo de cena aplicadas.
    for node_name in scene.graph.nodes_geometry:
        transform, geom_name = scene.graph[node_name]
        geom = scene.geometry.get(geom_name)
        if geom is None or not hasattr(geom, "faces") or len(getattr(geom, "faces", [])) == 0:
            continue
        verts = np.asarray(geom.vertices, dtype=np.float64)
        if transform is not None:
            hom = np.hstack([verts, np.ones((len(verts), 1))])
            verts = (hom @ np.asarray(transform, dtype=np.float64).T)[:, :3]
        faces = np.asarray(geom.faces, dtype=np.int64)
        color, mat_name = _color_of(geom)
        name = str(node_name or geom_name or f"Parte {len(parts) + 1}")
        parts.append(Part(name=name, vertices=verts, faces=faces, color=color, material=mat_name))
    if not parts:
        raise EngineError(f"O arquivo '{p.name}' não contém geometria com faces.")

    declared_up = header.get("up", "").upper() or None
    if declared_up is None and ext in Y_UP_FORMATS:
        declared_up = "Y"
    units = header.get("units")
    if units is None and ext in (".glb", ".gltf"):
        units = "m"  # glTF é definido em metros
    if units is None and ext == ".3mf":
        units = "mm"
    return LoadedModel(parts=parts, path=str(p), format=ext.lstrip("."), declared_units=units,
                       declared_up=declared_up, warnings=warnings)


def save_obj(path: str | os.PathLike, parts: list[Part], header: str = "# forge3d units=mm up=Z") -> None:
    """Grava partes em OBJ (usado em testes e na conversão de formatos)."""
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(header + "\n")
        offset = 1
        for part in parts:
            fh.write(f"o {part.name}\n")
            for v in part.vertices:
                fh.write(f"v {v[0]:.6f} {v[1]:.6f} {v[2]:.6f}\n")
            for f in part.faces:
                fh.write(f"f {f[0] + offset} {f[1] + offset} {f[2] + offset}\n")
            offset += len(part.vertices)

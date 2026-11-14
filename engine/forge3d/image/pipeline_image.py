"""Imagem -> FamilySpec.

Estratégias:
- ``revolucao``: silhueta simétrica e cheia (vaso, poste, luminária, coluna);
- ``extrusao``: silhueta extrudada na profundidade (painéis, placas, recortes);
- ``ia``: reconstrução 3D completa pelo TripoSR (módulo opcional);
- ``auto``: escolhe pela forma da silhueta e pela disponibilidade da IA.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np

from .. import hardware
from ..errors import EngineError
from ..geometry.mesh_io import Part, save_obj
from ..geometry.orientation import UP_ROTATIONS, guess_up_axis
from ..pipeline import ConvertOptions, ConvertResult, convert_mesh_file
from ..planner.naming import sanitize_name
from . import ai, strategies
from .silhouette import Silhouette, extract, load_image, save_preview

STRATEGIES = ("auto", "revolucao", "extrusao", "ia")


@dataclass
class ImageOptions:
    strategy: str = "auto"
    reference: str = "altura"  # altura | largura
    reference_mm: float | None = None
    depth_mm: float | None = None
    level: str = "C"
    category: str = "GenericModel"
    family_name: str | None = None
    use_ai_mask: bool = True


def row_fill(sil: Silhouette) -> float:
    """Fração das linhas da silhueta sem vãos entre a borda esquerda e a direita."""
    x0, y0, x1, y1 = sil.bbox
    full = 0
    total = 0
    for y in range(y0, y1):
        xs = np.nonzero(sil.mask[y])[0]
        if len(xs) == 0:
            continue
        total += 1
        span = xs.max() - xs.min() + 1
        if len(xs) >= 0.97 * span:
            full += 1
    return full / total if total else 0.0


def suggest_strategy(sil: Silhouette, ai_ok: bool) -> tuple[str, str]:
    fill = row_fill(sil)
    if sil.symmetry >= 0.9 and fill >= 0.97:
        return "revolucao", f"silhueta simétrica ({sil.symmetry:.0%}) e cheia ({fill:.0%} das linhas)"
    if ai_ok:
        return "ia", "silhueta sem simetria de rotação; reconstrução completa por IA"
    return "extrusao", "silhueta sem simetria de rotação e módulo de IA não instalado"


def _keep_significant(mesh, min_ratio: float = 0.02):
    """Remove fragmentos soltos (artefatos da reconstrução por IA)."""
    import trimesh

    parts = mesh.split(only_watertight=False)
    if len(parts) <= 1:
        return mesh

    def size(m):
        try:
            return abs(float(m.volume)) if m.is_watertight else float(m.area) * 1e-3
        except Exception:
            return 0.0

    sizes = [size(m) for m in parts]
    total = sum(sizes) or 1.0
    keep = [m for m, s in zip(parts, sizes) if s >= min_ratio * total]
    return trimesh.util.concatenate(keep) if keep else mesh


def analyze_image(path: str, opts: ImageOptions, work_dir: str | None = None) -> dict:
    img = load_image(path)
    sil = extract(img, use_ai=opts.use_ai_mask)
    st = ai.status()
    strategy, reason = suggest_strategy(sil, st["available"])
    work = Path(work_dir or tempfile.mkdtemp(prefix="forge3d_"))
    work.mkdir(parents=True, exist_ok=True)
    preview = save_preview(img, sil, str(work / "silhueta.png"))
    return {
        "silhouetteMethod": sil.method, "symmetry": round(sil.symmetry, 3), "rowFill": round(row_fill(sil), 3),
        "bboxPx": list(sil.bbox), "aspect": round(sil.height_px / max(sil.width_px, 1), 4),
        "suggestedStrategy": strategy, "reason": reason, "preview": preview,
        "ai": st, "imageSize": [img.width, img.height],
    }


def _scale(sil: Silhouette, opts: ImageOptions, warnings: list[str]) -> float:
    ref = opts.reference_mm
    if ref is None or ref <= 0:
        ref = 1000.0
        warnings.append("Medida de referência não informada: altura assumida de 1000 mm. Informe uma medida real para a escala correta.")
        return ref / sil.height_px
    if opts.reference == "largura":
        return ref / sil.width_px
    return ref / sil.height_px


def convert_image(path: str, opts: ImageOptions | None = None, progress: Callable[[float, str], None] | None = None,
                  cancel: Callable[[], bool] | None = None, work_dir: str | None = None) -> ConvertResult:
    opts = opts or ImageOptions()
    if opts.strategy not in STRATEGIES:
        raise EngineError(f"Estratégia desconhecida: {opts.strategy}")
    report = progress or (lambda f, m: None)
    warnings: list[str] = []
    report(0.02, "Lendo a imagem")
    img = load_image(path)
    report(0.08, "Separando o objeto do fundo")
    sil = extract(img, use_ai=opts.use_ai_mask)
    st = ai.status()
    strategy, reason = (opts.strategy, "escolhida pelo usuário") if opts.strategy != "auto" else suggest_strategy(sil, st["available"])
    if strategy == "ia" and not st["available"]:
        raise EngineError("Módulo de IA não instalado: " + st["reason"] + " Use as estratégias Revolução ou Extrusão.")
    mm_per_px = _scale(sil, opts, warnings)
    report(0.15, "Gerando a geometria")
    if strategy == "revolucao":
        mesh = strategies.revolution_mesh(sil, mm_per_px)
        if sil.symmetry < 0.85:
            warnings.append(f"A silhueta não é muito simétrica ({sil.symmetry:.0%}); a revolução usa a média dos dois lados.")
    elif strategy == "extrusao":
        width_mm = sil.width_px * mm_per_px
        depth = opts.depth_mm
        if depth is None or depth <= 0:
            depth = float(np.clip(0.15 * width_mm, 10.0, 600.0))
            warnings.append(f"Profundidade não informada: {depth:.0f} mm assumidos. Ajuste o parâmetro na família.")
        mesh = strategies.extrusion_mesh(sil, mm_per_px, depth)
    else:
        prof = hardware.detect().profile
        mesh = ai.reconstruct(img, sil.mask, prof, progress=lambda f, m: report(0.15 + 0.45 * f, m), cancel=cancel)
        # Orientação: Z para cima pela mesma heurística dos arquivos 3D.
        v = np.asarray(mesh.vertices, dtype=float)
        up, _, _ = guess_up_axis(v, np.asarray(mesh.faces), "glb", None)
        v = v @ UP_ROTATIONS[up].T
        ext = v.max(0) - v.min(0)
        target = opts.reference_mm or 1000.0
        size = ext[2] if opts.reference != "largura" else ext[0]
        if size <= 0:
            raise EngineError("A IA gerou uma geometria sem dimensão.")
        mesh.vertices = v * (target / size)
        mesh = _keep_significant(mesh)
        warnings.append("Geometria reconstruída por IA: confira a forma e as proporções na prévia.")
    work = Path(work_dir or tempfile.mkdtemp(prefix="forge3d_"))
    work.mkdir(parents=True, exist_ok=True)
    obj_path = work / (sanitize_name(Path(path).stem, "imagem", 60).replace(" ", "_") + "_forge3d.obj")
    save_obj(obj_path, [Part("objeto", np.asarray(mesh.vertices), np.asarray(mesh.faces))])
    name = opts.family_name or sanitize_name(Path(path).stem.replace("_", " "), "Forge3D Familia", 80)
    # Malhas da IA são aproximadas: a tolerância de reconhecimento acompanha
    # essa precisão (1% do tamanho) para ainda achar revoluções e caixas.
    fit_tol = 1.0
    if strategy == "ia":
        ext = np.asarray(mesh.extents, dtype=float)
        fit_tol = max(2.0, 0.01 * float(ext.max()))
    result = convert_mesh_file(
        str(obj_path),
        ConvertOptions(level=opts.level, units="mm", up_axis="Z", category=opts.category, family_name=name,
                       fit_tol=fit_tol),
        progress=lambda f, m: report(0.6 + 0.38 * f, m), cancel=cancel)
    preview = save_preview(img, sil, str(work / "silhueta.png"))
    spec = result.spec
    with open(path, "rb") as fh:
        sha = hashlib.sha256(fh.read()).hexdigest()
    spec["report"]["warnings"] = warnings + spec["report"]["warnings"]
    spec["report"]["image"] = {
        "path": os.path.abspath(path), "sha256": sha, "strategy": strategy, "strategyReason": reason,
        "silhouetteMethod": sil.method, "symmetry": round(sil.symmetry, 3), "mmPerPixel": round(mm_per_px, 6),
        "preview": preview,
    }
    result.summary["strategy"] = strategy
    result.summary["warnings"] = spec["report"]["warnings"]
    report(1.0, "Concluído")
    return result

"""Do modelo carregado às primitivas no sistema da família."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable

import numpy as np

from ..errors import CancelledError, EngineError
from . import freeform
from .classify import Options as ClassifyOptions
from .classify import classify_component
from .cleanup import split_components, to_trimesh, weld
from .mesh_io import LoadedModel
from .orientation import UNIT_TO_MM, UP_ROTATIONS, framing_offset, guess_units, guess_up_axis, read_dae_unit, read_dae_up_axis
from .primitives import FreeformMesh, Primitive

ProgressFn = Callable[[float, str], None]


@dataclass
class DecomposeOptions:
    units: str = "auto"  # auto | mm | cm | dm | m | in | ft
    up_axis: str = "auto"  # auto | Z | Y | -Z | -Y | X | -X
    fit_tol: float = 1.0  # mm — desvio aceito em aproximações
    allow_approx: bool = True
    sheet_thickness: float = 3.0
    max_freeform_faces: int = 15000
    detail_volume_ratio: float = 0.002
    detail_size_ratio: float = 0.05


@dataclass
class ComponentInfo:
    source: str
    method: str
    kinds: list[str]
    volume: float
    notes: list[str] = field(default_factory=list)


@dataclass
class Decomposition:
    primitives: list[Primitive]
    envelope: tuple[float, float, float]
    units: str
    units_reason: str
    up_axis: str
    up_reason: str
    transform: np.ndarray  # 4x4: coordenadas do arquivo -> família (mm)
    components: list[ComponentInfo]
    warnings: list[str]
    total_volume: float
    timings: dict[str, float]


def _check_cancel(cancel: Callable[[], bool] | None) -> None:
    if cancel is not None and cancel():
        raise CancelledError()


def decompose(model: LoadedModel, opts: DecomposeOptions | None = None,
              progress: ProgressFn | None = None, cancel: Callable[[], bool] | None = None) -> Decomposition:
    opts = opts or DecomposeOptions()
    report = progress or (lambda f, m: None)
    t0 = time.perf_counter()
    timings: dict[str, float] = {}
    warnings: list[str] = list(model.warnings)

    all_v = np.vstack([p.vertices for p in model.parts])
    offsets = np.cumsum([0] + [len(p.vertices) for p in model.parts])
    all_f = np.vstack([p.faces + offsets[i] for i, p in enumerate(model.parts)])

    # Unidades.
    if opts.units != "auto":
        unit, unit_reason = opts.units, "informada pelo usuário"
    elif model.declared_units:
        unit, unit_reason = model.declared_units, "declarada no arquivo"
    elif model.format == "dae" and (meter := read_dae_unit(model.path)):
        scale_mm = meter * 1000.0
        unit = min(UNIT_TO_MM, key=lambda u: abs(UNIT_TO_MM[u] - scale_mm))
        unit_reason = "declarada no arquivo Collada"
    else:
        g = guess_units(float((all_v.max(0) - all_v.min(0)).max()))
        unit, unit_reason = g.unit, "estimada pelo tamanho (" + g.reason + ")"
        if g.confidence < 0.6:
            warnings.append(f"Unidade do arquivo estimada como {unit}; confira as dimensões na prévia.")
    if unit not in UNIT_TO_MM:
        raise EngineError(f"Unidade desconhecida: {unit}")
    scale = UNIT_TO_MM[unit]

    # Eixo vertical.
    if opts.up_axis != "auto":
        up, up_reason = opts.up_axis, "informado pelo usuário"
    elif model.declared_up:
        up, up_reason = model.declared_up, "declarado no arquivo"
    elif model.format == "dae" and (dae_up := read_dae_up_axis(model.path)):
        up, up_reason = dae_up, "declarado no arquivo Collada"
    else:
        up, conf, why = guess_up_axis(all_v, all_f, model.format, [p.vertices for p in model.parts])
        up_reason = "estimado (" + why + ")"
        if conf < 0.65:
            warnings.append(f"Eixo vertical estimado como {up}; se o objeto aparecer deitado, troque o eixo na prévia.")
    if up not in UP_ROTATIONS:
        raise EngineError(f"Eixo vertical inválido: {up}")
    R = UP_ROTATIONS[up]

    transformed = (all_v @ R.T) * scale
    offset = framing_offset(transformed)
    T = np.eye(4)
    T[:3, :3] = R * scale
    T[:3, 3] = offset
    family_v = transformed + offset
    lo, hi = family_v.min(0), family_v.max(0)
    envelope = tuple(float(x) for x in (hi - lo))
    if min(envelope) <= 0:
        raise EngineError("O modelo não tem volume (alguma dimensão é zero).")
    diag = float(np.linalg.norm(hi - lo))
    tol = max(0.05, 1e-5 * diag)
    weld_tol = min(max(1e-4, 1e-6 * diag), 0.01)
    copts = ClassifyOptions(tol=tol, fit_tol=opts.fit_tol, sheet_thickness=opts.sheet_thickness,
                            allow_approx=opts.allow_approx)
    timings["preparo"] = time.perf_counter() - t0
    report(0.05, "Modelo carregado e orientado")

    # Componentes.
    t1 = time.perf_counter()
    comps = []
    dropped = 0
    min_size = max(0.5, 1e-4 * diag)
    for i, part in enumerate(model.parts):
        _check_cancel(cancel)
        pv = family_v[offsets[i]:offsets[i + 1]]
        v, f = weld(pv, part.faces, weld_tol)
        if len(f) == 0:
            continue
        for c in split_components(to_trimesh(v, f)):
            ext = c.bounds[1] - c.bounds[0]
            if float(np.linalg.norm(ext)) < min_size:
                dropped += 1
                continue
            comps.append((part, c))
    if dropped:
        warnings.append(f"{dropped} fragmento(s) minúsculo(s) ignorado(s) (menores que {min_size:.2g} mm).")
    if not comps:
        raise EngineError("Nenhuma peça com tamanho útil foi encontrada no modelo.")
    timings["componentes"] = time.perf_counter() - t1

    # Classificação.
    t2 = time.perf_counter()
    primitives: list[Primitive] = []
    infos: list[ComponentInfo] = []
    for k, (part, comp) in enumerate(comps):
        _check_cancel(cancel)
        report(0.05 + 0.8 * k / len(comps), f"Analisando peça {k + 1} de {len(comps)}")
        try:
            res = classify_component(comp, copts)
        except Exception as exc:  # nunca derruba o processo: vira forma livre
            from .classify import _freeform
            from .cleanup import repair

            fixed, closed = repair(comp)
            res_prims = [_freeform(fixed, closed)]
            infos.append(ComponentInfo(part.name, "forma livre (erro na análise)", ["mesh"], res_prims[0].volume,
                                       [f"análise falhou: {exc}"]))
            for p in res_prims:
                p.source, p.color, p.material_name = part.name, part.color, part.material
            primitives += res_prims
            continue
        for p in res.primitives:
            p.source, p.color, p.material_name = part.name, part.color, part.material
        infos.append(ComponentInfo(part.name, res.method, [p.kind for p in res.primitives],
                                   float(sum(p.volume for p in res.primitives)), res.notes))
        primitives += res.primitives
    timings["classificação"] = time.perf_counter() - t2

    # Formas livres: fechadas e dentro do orçamento.
    t3 = time.perf_counter()
    free = [p for p in primitives if isinstance(p, FreeformMesh)]
    if free:
        report(0.88, "Preparando formas livres")
        budgets = freeform.distribute_budget(free, opts.max_freeform_faces)
        prepared = {id(p): freeform.prepare(p, b) for p, b in zip(free, budgets)}
        primitives = [prepared.get(id(p), p) for p in primitives]
    timings["formas livres"] = time.perf_counter() - t3

    total_volume = float(sum(p.volume for p in primitives))
    for p in primitives:
        b = p.bounds()
        size = float(np.linalg.norm(b[1] - b[0]))
        if p.volume < opts.detail_volume_ratio * total_volume and size < opts.detail_size_ratio * diag:
            p.detail = True
    report(0.95, "Decomposição concluída")
    timings["total"] = time.perf_counter() - t0
    return Decomposition(primitives, envelope, unit, unit_reason, up, up_reason, T, infos, warnings,
                         total_volume, timings)

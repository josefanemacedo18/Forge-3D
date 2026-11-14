"""Fluxo completo: arquivo 3D -> FamilySpec validado e testado."""

from __future__ import annotations

import copy
import dataclasses
import hashlib
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .errors import EngineError
from .geometry.decompose import DecomposeOptions, decompose
from .geometry.mesh_io import load_model
from .planner.naming import sanitize_name
from .planner.planner import PlanOptions, plan
from .spec import evaluate, validate

ProgressFn = Callable[[float, str], None]


@dataclass
class ConvertOptions:
    level: str = "C"
    units: str = "auto"
    up_axis: str = "auto"
    category: str = "GenericModel"
    family_name: str | None = None
    fit_tol: float = 1.0
    allow_approx: bool = True
    sheet_thickness: float = 3.0
    max_freeform_faces: int = 15000
    compute_fidelity: bool = True
    target_size: float | None = None  # mm — medida real conhecida
    target_axis: str = "z"


@dataclass
class ConvertResult:
    spec: dict
    summary: dict = field(default_factory=dict)


def file_sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _safe_at(spec: dict, pid: str, value: float) -> bool:
    """A família continua válida com o parâmetro ``pid`` = ``value``?"""
    from .spec.validate import MIN_PLANE_GAP_MM, parameter_values, plane_positions

    ov = {pid: value}
    try:
        values = parameter_values(spec, ov)
        if any(not values[q["id"]] > 0 for q in spec["parameters"] if q["kind"] == "length" and q.get("formula")):
            return False
        planes = plane_positions(spec, values)
        # Cota rotulada menor que 1 mm: o Revit não consegue manter o plano.
        for pl in spec["planes"]:
            if abs(planes[pl["id"]] - planes[pl["parent"]]) < MIN_PLANE_GAP_MM:
                return False
        # Planos de peças diferentes podem se cruzar (cada um tem a sua cota);
        # o que não pode é um sólido se inverter, se cruzar ou ficar fino demais.
        solids = evaluate.evaluate_solids(spec, ov, build_meshes=False)
        return not any(s.problems for s in solids)
    except Exception:
        return False


MAX_FACTOR = 10.0  # acima de 10x o valor original, não há limite a informar


def _safe_range(spec: dict) -> dict[str, tuple[float, float | None]]:
    """Faixa segura (mínimo, máximo) de cada parâmetro editável.

    Fora dela, alguma peça se inverteria, se cruzaria ou teria aresta menor que
    1 mm (o Revit recusa). Máximo None = sem limite até 10x o valor.
    """
    out: dict[str, tuple[float, float | None]] = {}
    for p in spec["parameters"]:
        if p["kind"] != "length" or p.get("formula") or p["role"] not in ("envelope", "semantic"):
            continue
        base = float(p["value"])
        lo, hi = 0.01, 1.0
        for _ in range(16):
            mid = (lo + hi) / 2
            if _safe_at(spec, p["id"], base * mid):
                hi = mid
            else:
                lo = mid
        vmin = round(base * hi, 1)
        vmax: float | None = None
        if not _safe_at(spec, p["id"], base * MAX_FACTOR):
            lo, hi = 1.0, MAX_FACTOR
            for _ in range(14):
                mid = (lo + hi) / 2
                if _safe_at(spec, p["id"], base * mid):
                    lo = mid
                else:
                    hi = mid
            vmax = round(base * lo, 1)
        out[p["id"]] = (vmin, vmax)
    return out


MAX_RELAX_ROUNDS = 4


def plan_robust(dec, plan_opts: PlanOptions, report: ProgressFn | None = None) -> dict:
    """Planeja e solta as restrições que não aguentam ±10% de flexão.

    Cada rodada replaneja a partir das primitivas originais, soltando o
    contorno, os raios ou a altura das peças culpadas. Um parâmetro que quebra
    a família com uma variação pequena não serve para o usuário: a peça fica
    com a forma fixa (e o relatório explica por quê).
    """
    report = report or (lambda f, m: None)
    pristine = copy.deepcopy(dec.primitives)
    spec = plan(dataclasses.replace(dec, primitives=copy.deepcopy(pristine)), plan_opts)
    validate.validate(spec)
    if plan_opts.level == "A":
        return spec
    relax: dict[str, set[str]] = {k: set(v) for k, v in plan_opts.relax.items()}
    for _ in range(MAX_RELAX_ROUNDS):
        culprits = evaluate.flex_culprits(spec)
        grew = False
        for sid, what in culprits.items():
            cur = relax.setdefault(sid, set())
            if not what <= cur:
                cur |= what
                grew = True
        if not grew:
            break
        report(0.86, "Ajustando restrições que não aguentam flexão")
        spec = plan(dataclasses.replace(dec, primitives=copy.deepcopy(pristine)),
                    dataclasses.replace(plan_opts, relax=relax))
        validate.validate(spec)
    return spec


def convert_mesh_file(path: str, opts: ConvertOptions | None = None, progress: ProgressFn | None = None,
                      cancel: Callable[[], bool] | None = None) -> ConvertResult:
    opts = opts or ConvertOptions()
    report = progress or (lambda f, m: None)
    if opts.level not in ("A", "B", "C"):
        raise EngineError(f"Nível inválido: {opts.level}")
    t0 = time.perf_counter()
    report(0.0, "Lendo arquivo")
    model = load_model(path)
    dec = decompose(model, DecomposeOptions(units=opts.units, up_axis=opts.up_axis, fit_tol=opts.fit_tol,
                                            allow_approx=opts.allow_approx, sheet_thickness=opts.sheet_thickness,
                                            max_freeform_faces=opts.max_freeform_faces,
                                            target_size=opts.target_size, target_axis=opts.target_axis),
                    progress=lambda f, m: report(0.05 + 0.75 * f, m), cancel=cancel)
    report(0.82, "Planejando parâmetros")
    name = opts.family_name or sanitize_name(Path(path).stem.replace("_", " "), "Forge3D Familia", 80)
    spec = plan_robust(dec, PlanOptions(level=opts.level, family_name=name, category=opts.category,
                                        source_name=Path(path).name), report)
    report(0.9, "Teste de flexão")
    flex = evaluate.flex_test(spec)
    ranges = _safe_range(spec) if spec["planes"] else {}
    for p in spec["parameters"]:
        if p["id"] in ranges:
            p["min"], p["max"] = ranges[p["id"]]
    spec["report"]["flex"] = {"ok": flex.ok, "failures": flex.failures[:20], "checked": flex.checked}
    if not flex.ok:
        spec["report"]["warnings"].append(
            "Alguns parâmetros não aceitam variações grandes: respeite a faixa segura (mínimo e máximo) de cada um.")
    spec["report"]["source"] = {
        "path": str(path), "format": model.format, "sha256": file_sha256(path),
        "units": dec.units, "unitsReason": dec.units_reason, "upAxis": dec.up_axis, "upReason": dec.up_reason,
        "components": [{"source": c.source, "method": c.method, "kinds": c.kinds} for c in dec.components],
        "transform": [[round(float(v), 9) for v in row] for row in dec.transform],
    }
    if opts.compute_fidelity:
        report(0.95, "Medindo fidelidade")
        try:
            import numpy as np
            import trimesh

            verts = []
            faces = []
            off = 0
            T = dec.transform
            for part in model.parts:
                v = part.vertices @ T[:3, :3].T + T[:3, 3]
                verts.append(v)
                faces.append(part.faces + off)
                off += len(v)
            ref = trimesh.Trimesh(np.vstack(verts), np.vstack(faces), process=False)
            spec["report"]["fidelity"] = evaluate.fidelity(spec, ref)
        except Exception as exc:  # fidelidade é informativa
            spec["report"]["fidelity"] = {"error": str(exc)}
    spec["report"]["timings"] = {**{k: round(v, 3) for k, v in dec.timings.items()},
                                 "pipeline": round(time.perf_counter() - t0, 3)}
    report(1.0, "Concluído")
    summary = {
        "name": spec["family"]["name"], "requestedLevel": opts.level, "achievedLevel": spec["family"]["achievedLevel"],
        "parts": len(spec["solids"]), "planes": len(spec["planes"]), "parameters": len(spec["parameters"]),
        "envelope": spec["envelope"], "coverage": spec["report"]["parametricCoverage"],
        "levelCBlockers": spec["report"]["levelCBlockers"], "warnings": spec["report"]["warnings"],
    }
    return ConvertResult(spec, summary)

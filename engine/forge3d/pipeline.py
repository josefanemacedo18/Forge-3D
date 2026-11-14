"""Fluxo completo: arquivo 3D -> FamilySpec validado e testado."""

from __future__ import annotations

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


def _min_safe_values(spec: dict) -> dict[str, float]:
    """Menor valor seguro (sem inverter geometria) para cada parâmetro editável."""
    from .spec.validate import parameter_values, plane_positions

    out: dict[str, float] = {}
    for p in spec["parameters"]:
        if p["kind"] != "length" or p.get("formula") or p["role"] not in ("envelope", "semantic"):
            continue
        base = float(p["value"])
        lo, hi = 0.01, 1.0
        for _ in range(18):
            mid = (lo + hi) / 2
            ov = {p["id"]: base * mid}
            ok = True
            try:
                values = parameter_values(spec, ov)
                if any(not values[q["id"]] > 0 for q in spec["parameters"] if q["kind"] == "length" and q.get("formula")):
                    ok = False
                else:
                    plane_positions(spec, values)
                    solids = evaluate.evaluate_solids(spec, ov, build_meshes=False)
                    orig = {pl["id"]: pl["position"] for pl in spec["planes"]}
                    orig.update({"@x0": 0.0, "@y0": 0.0, "@z0": 0.0})
                    if any(s.problems for s in solids) or evaluate._order_problems(spec, plane_positions(spec, values), orig):
                        ok = False
            except Exception:
                ok = False
            if ok:
                hi = mid
            else:
                lo = mid
        out[p["id"]] = round(base * hi, 1)
    return out


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
                                            max_freeform_faces=opts.max_freeform_faces),
                    progress=lambda f, m: report(0.05 + 0.75 * f, m), cancel=cancel)
    report(0.82, "Planejando parâmetros")
    name = opts.family_name or sanitize_name(Path(path).stem.replace("_", " "), "Forge3D Familia", 80)
    spec = plan(dec, PlanOptions(level=opts.level, family_name=name, category=opts.category,
                                 source_name=Path(path).name))
    report(0.88, "Validando")
    validate.validate(spec)
    report(0.9, "Teste de flexão")
    flex = evaluate.flex_test(spec)
    mins = _min_safe_values(spec) if spec["planes"] else {}
    for p in spec["parameters"]:
        if p["id"] in mins:
            p["min"] = mins[p["id"]]
    spec["report"]["flex"] = {"ok": flex.ok, "failures": flex.failures[:20], "checked": flex.checked}
    if not flex.ok:
        spec["report"]["warnings"].append(
            "Alguns parâmetros não aceitam reduções grandes (veja o valor mínimo de cada um no relatório).")
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

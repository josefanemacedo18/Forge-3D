"""Validação do FamilySpec: estrutura (JSON Schema) e semântica.

A validação semântica confere as mesmas regras que o construtor do Revit
precisa para funcionar sem erros: árvore de planos sem ciclos, cada plano com
cota coerente com o parâmetro que a rotula, fórmulas resolvíveis, referências
existentes e nomes únicos.
"""

from __future__ import annotations

import json
import math
from functools import lru_cache
from importlib import resources
from typing import Any

from . import formula as fx

ROOTS = {"@x0": ("x", 0.0), "@y0": ("y", 0.0), "@z0": ("z", 0.0)}
POSITION_TOL = 0.01  # mm
# O Revit rejeita curvas menores que ~0,78 mm (ShortCurveTolerance = 1/32").
MIN_SEGMENT_MM = 1.0
MIN_PLANE_GAP_MM = 1.0
PERP_AXES = {"z": ("x", "y"), "x": ("y", "z"), "y": ("x", "z")}


class SpecValidationError(ValueError):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("FamilySpec inválido:\n- " + "\n- ".join(errors))


@lru_cache(maxsize=1)
def _schema() -> dict:
    text = resources.files("forge3d.spec").joinpath("familyspec.schema.json").read_text(encoding="utf-8")
    return json.loads(text)


def schema_errors(spec: dict) -> list[str]:
    try:
        import jsonschema
    except ImportError:  # pragma: no cover - jsonschema é dependência declarada
        return []
    validator = jsonschema.Draft202012Validator(_schema())
    errors = []
    for err in sorted(validator.iter_errors(spec), key=lambda e: list(e.absolute_path)):
        path = "/".join(str(p) for p in err.absolute_path) or "(raiz)"
        errors.append(f"[estrutura] {path}: {err.message}")
    return errors


def parameter_values(spec: dict, overrides: dict[str, float] | None = None) -> dict[str, Any]:
    """Valores finais por id de parâmetro (com fórmulas avaliadas).

    ``overrides`` permite trocar valores de parâmetros sem fórmula (por id),
    como faz um teste de flexão.
    """
    params = spec["parameters"]
    by_name = {p["name"]: p for p in params}
    values: dict[str, float] = {}
    formulas: dict[str, str] = {}
    for p in params:
        if p["kind"] not in ("length", "angle", "number", "integer"):
            continue
        if p.get("formula"):
            formulas[p["name"]] = p["formula"]
        else:
            v = p["value"]
            if overrides and p["id"] in overrides:
                v = overrides[p["id"]]
            values[p["name"]] = float(v)
    evaluated = fx.evaluate_all(values, formulas)
    result: dict[str, Any] = {}
    for p in params:
        if p["name"] in evaluated:
            result[p["id"]] = evaluated[p["name"]]
        else:
            result[p["id"]] = p["value"]
    del by_name
    return result


def plane_positions(spec: dict, values: dict[str, Any]) -> dict[str, float]:
    """Posição de cada plano a partir da árvore pai→filho e dos parâmetros.

    A direção (lado) de cada plano em relação ao pai é a da geometria original,
    como acontece no Revit: a cota rotulada fixa a distância, não o lado.
    """
    planes = {p["id"]: p for p in spec["planes"]}
    original = {pid: p["position"] for pid, p in planes.items()}
    for rid, (_, pos) in ROOTS.items():
        original[rid] = pos
    out: dict[str, float] = {rid: pos for rid, (_, pos) in ROOTS.items()}

    def pos(pid: str, stack: tuple[str, ...] = ()) -> float:
        if pid in out:
            return out[pid]
        if pid in stack:
            raise SpecValidationError([f"Ciclo na árvore de planos envolvendo '{pid}'."])
        p = planes[pid]
        parent_pos = pos(p["parent"], stack + (pid,))
        sign = 1.0 if original[pid] >= original[p["parent"]] else -1.0
        out[pid] = parent_pos + sign * float(values[p["driver"]])
        return out[pid]

    for pid in planes:
        pos(pid)
    return out


def anchor_position(anchor: dict, planes_pos: dict[str, float], original_pos: dict[str, float], values: dict) -> float:
    """Posição de uma âncora. Sem cota (driver nulo) a âncora fica fixa na
    posição absoluta registrada; com cota, acompanha o plano pai."""
    if anchor["driver"] is None:
        return float(anchor["position"])
    parent = anchor["parent"]
    sign = 1.0 if anchor["position"] >= original_pos[parent] else -1.0
    return planes_pos[parent] + sign * float(values[anchor["driver"]])


def semantic_errors(spec: dict) -> list[str]:
    errors: list[str] = []
    params = spec.get("parameters", [])
    planes = spec.get("planes", [])
    solids = spec.get("solids", [])
    materials = spec.get("materials", [])

    # --- parâmetros -------------------------------------------------------
    pids: dict[str, dict] = {}
    names_ci: dict[str, str] = {}
    for p in params:
        if p["id"] in pids:
            errors.append(f"Parâmetro com id repetido: '{p['id']}'.")
        pids[p["id"]] = p
        key = p["name"].casefold()
        if key in names_ci:
            errors.append(f"Nome de parâmetro repetido: '{p['name']}'.")
        names_ci[key] = p["id"]
    errors += fx.check_names(p["name"] for p in params)
    names = {p["name"] for p in params}
    for p in params:
        f = p.get("formula")
        if f:
            if p["kind"] not in ("length", "angle", "number", "integer"):
                errors.append(f"Parâmetro '{p['name']}' do tipo {p['kind']} não pode ter fórmula numérica.")
                continue
            try:
                refs = fx.referenced_names(f)
            except fx.FormulaError as exc:
                errors.append(str(exc))
                continue
            for r in refs:
                if r not in names:
                    errors.append(f"Fórmula de '{p['name']}' cita parâmetro inexistente '{r}'.")
        if p["kind"] == "material" and p["value"] is not None:
            if p["value"] not in {m["id"] for m in materials}:
                errors.append(f"Parâmetro de material '{p['name']}' aponta para material inexistente '{p['value']}'.")
        if p["kind"] == "length" and not p.get("formula"):
            if not isinstance(p["value"], (int, float)) or p["value"] <= 0:
                errors.append(f"Parâmetro de comprimento '{p['name']}' precisa de valor positivo (atual: {p['value']}).")
    if errors:
        return errors

    try:
        values = parameter_values(spec)
    except fx.FormulaError as exc:
        return errors + [str(exc)]

    for p in params:
        if p["kind"] == "length" and p.get("formula"):
            v = values[p["id"]]
            if not (v > 0):
                errors.append(f"Fórmula de '{p['name']}' resulta em {v:.3f} mm; comprimentos rotulados precisam ser positivos.")
            elif abs(v - float(p["value"])) > POSITION_TOL * 10:
                errors.append(
                    f"Valor declarado de '{p['name']}' ({p['value']}) difere do resultado da fórmula ({v:.3f})."
                )

    # --- planos --------------------------------------------------------------
    plane_ids: dict[str, dict] = {}
    plane_names: set[str] = set()
    for pl in planes:
        if pl["id"] in plane_ids or pl["id"] in ROOTS:
            errors.append(f"Plano com id repetido ou reservado: '{pl['id']}'.")
        plane_ids[pl["id"]] = pl
        if pl["name"] in plane_names:
            errors.append(f"Nome de plano repetido: '{pl['name']}'.")
        plane_names.add(pl["name"])
    axis_of = {pid: pl["axis"] for pid, pl in plane_ids.items()}
    axis_of.update({rid: ax for rid, (ax, _) in ROOTS.items()})
    original = {pid: pl["position"] for pid, pl in plane_ids.items()}
    original.update({rid: pos for rid, (_, pos) in ROOTS.items()})
    for pl in planes:
        parent = pl["parent"]
        if parent not in axis_of:
            errors.append(f"Plano '{pl['id']}' tem pai inexistente '{parent}'.")
            continue
        if axis_of[parent] != pl["axis"]:
            errors.append(f"Plano '{pl['id']}' (eixo {pl['axis']}) tem pai '{parent}' de outro eixo.")
        drv = pids.get(pl["driver"])
        if drv is None:
            errors.append(f"Plano '{pl['id']}' é rotulado por parâmetro inexistente '{pl['driver']}'.")
            continue
        if drv["kind"] != "length":
            errors.append(f"Plano '{pl['id']}' é rotulado por parâmetro que não é comprimento ('{drv['name']}').")
            continue
        dist = abs(pl["position"] - original[parent])
        if dist < MIN_PLANE_GAP_MM:
            errors.append(f"Plano '{pl['id']}' está colado ao pai '{parent}' (distância {dist:.3f} mm); o Revit não aceita cota nula.")
        if abs(dist - float(values[drv["id"]])) > POSITION_TOL * 10:
            errors.append(
                f"Plano '{pl['id']}': distância ao pai ({dist:.3f} mm) difere do valor de '{drv['name']}' ({values[drv['id']]:.3f} mm)."
            )
    if errors:
        return errors
    try:
        positions = plane_positions(spec, values)
    except SpecValidationError as exc:
        return errors + exc.errors
    for pl in planes:
        if abs(positions[pl["id"]] - pl["position"]) > POSITION_TOL * 10:
            errors.append(f"Plano '{pl['id']}' não reproduz sua posição pela árvore de cotas.")

    # --- materiais ----------------------------------------------------------
    mat_ids = set()
    for m in materials:
        if m["id"] in mat_ids:
            errors.append(f"Material com id repetido: '{m['id']}'.")
        mat_ids.add(m["id"])

    # --- sólidos -------------------------------------------------------------
    sids = set()
    for s in solids:
        sid = s["id"]
        if sid in sids:
            errors.append(f"Sólido com id repetido: '{sid}'.")
        sids.add(sid)
        mat = s.get("material")
        if mat is not None:
            mp = pids.get(mat)
            if mp is None or mp["kind"] != "material":
                errors.append(f"Sólido '{sid}' usa parâmetro de material inexistente '{mat}'.")
        kind = s["kind"]
        if kind == "box":
            for ax in ("x", "y", "z"):
                a, b = s[ax]
                for ref in (a, b):
                    if axis_of.get(ref) != ax:
                        errors.append(f"Caixa '{sid}': face {ax} referencia plano '{ref}' inexistente ou de outro eixo.")
                if a in axis_of and b in axis_of and not (original[a] < original[b] - 0.05):
                    errors.append(f"Caixa '{sid}': planos {ax} fora de ordem ou coincidentes ('{a}', '{b}').")
        elif kind in ("cylinder", "revolution"):
            u_ax, v_ax = PERP_AXES[s["axis"]]
            for key, ax in (("u", u_ax), ("v", v_ax)):
                anc = s["center"][key]
                if axis_of.get(anc["parent"]) != ax:
                    errors.append(f"{kind} '{sid}': âncora {key} usa plano '{anc['parent']}' inexistente ou de outro eixo.")
                    continue
                if anc["driver"] is None:
                    pass  # âncora fixa na posição absoluta
                else:
                    d = pids.get(anc["driver"])
                    if d is None or d["kind"] != "length":
                        errors.append(f"{kind} '{sid}': âncora {key} rotulada por parâmetro inválido '{anc['driver']}'.")
                    else:
                        dist = abs(anc["position"] - original[anc["parent"]])
                        if abs(dist - float(values[d["id"]])) > POSITION_TOL * 10:
                            errors.append(f"{kind} '{sid}': âncora {key} incoerente com '{d['name']}'.")
            ext = s.get("extent")
            if ext is not None:
                for ref in ext:
                    if axis_of.get(ref) != s["axis"]:
                        errors.append(f"{kind} '{sid}': extensão usa plano '{ref}' inexistente ou de outro eixo.")
                if all(r in axis_of for r in ext) and not original[ext[0]] < original[ext[1]] - 0.05:
                    errors.append(f"{kind} '{sid}': planos de extensão fora de ordem.")
            if kind == "cylinder":
                dia = s["diameter"]
                if dia["driver"] is not None:
                    d = pids.get(dia["driver"])
                    if d is None or d["kind"] != "length":
                        errors.append(f"Cilindro '{sid}': diâmetro rotulado por parâmetro inválido.")
                    elif abs(float(values[d["id"]]) - dia["value"]) > POSITION_TOL * 10:
                        errors.append(f"Cilindro '{sid}': diâmetro difere do parâmetro '{d['name']}'.")
            else:
                prof = s["profile"]
                if any(seg["start"][0] < -POSITION_TOL or seg["end"][0] < -POSITION_TOL for seg in prof):
                    errors.append(f"Revolução '{sid}': perfil cruza o eixo (raio negativo).")
                errors += _loop_errors(prof, f"Revolução '{sid}'")
        elif kind == "extrusion":
            for i, loop in enumerate(s["loops"]):
                errors += _loop_errors(loop, f"Extrusão '{sid}' laço {i}")
            if "extent" in s:
                for ref in s["extent"]:
                    if axis_of.get(ref) != s["axis"]:
                        errors.append(f"Extrusão '{sid}': extensão usa plano '{ref}' inválido.")
                if all(r in axis_of for r in s["extent"]) and not original[s["extent"][0]] < original[s["extent"][1]] - 0.05:
                    errors.append(f"Extrusão '{sid}': planos de extensão fora de ordem.")
                u_ax, v_ax = PERP_AXES[s["axis"]]
                for al in s.get("alignments", []):
                    try:
                        seg = s["loops"][al["loop"]][al["segment"]]
                    except IndexError:
                        errors.append(f"Extrusão '{sid}': alinhamento aponta para segmento inexistente.")
                        continue
                    if seg["type"] != "line":
                        errors.append(f"Extrusão '{sid}': só segmentos retos podem ser alinhados.")
                        continue
                    pax = axis_of.get(al["plane"])
                    if pax not in (u_ax, v_ax):
                        errors.append(f"Extrusão '{sid}': alinhamento com plano '{al['plane']}' de eixo inválido.")
                        continue
                    idx = 0 if pax == u_ax else 1
                    c = original[al["plane"]]
                    if abs(seg["start"][idx] - c) > 0.05 or abs(seg["end"][idx] - c) > 0.05:
                        errors.append(f"Extrusão '{sid}': segmento alinhado não está sobre o plano '{al['plane']}'.")
            elif "frame" not in s:
                errors.append(f"Extrusão '{sid}' precisa de 'extent' ou de 'frame' + 'depth'.")
        elif kind == "mesh":
            nv = len(s["vertices"])
            if nv < 4 or len(s["faces"]) < 4:
                errors.append(f"Malha '{sid}' tem geometria insuficiente.")
            for f in s["faces"]:
                if max(f) >= nv:
                    errors.append(f"Malha '{sid}' referencia vértice inexistente.")
                    break

    # --- tipos ------------------------------------------------------------------
    type_names = set()
    for t in spec.get("types", []):
        if t["name"] in type_names:
            errors.append(f"Tipo de família repetido: '{t['name']}'.")
        type_names.add(t["name"])
        for k in t["values"]:
            if k not in pids:
                errors.append(f"Tipo '{t['name']}' define parâmetro inexistente '{k}'.")
            elif pids[k].get("formula"):
                errors.append(f"Tipo '{t['name']}' define '{k}', que é calculado por fórmula.")
    if not spec.get("types"):
        errors.append("O spec precisa de ao menos um tipo de família.")
    return errors


def _loop_errors(loop: list[dict], label: str) -> list[str]:
    errors = []
    n = len(loop)
    for i, seg in enumerate(loop):
        nxt = loop[(i + 1) % n]
        if math.dist(seg["end"], nxt["start"]) > 0.05:
            errors.append(f"{label}: segmentos {i} e {(i + 1) % n} não se conectam.")
        if math.dist(seg["start"], seg["end"]) < MIN_SEGMENT_MM:
            errors.append(f"{label}: segmento {i} muito curto (< {MIN_SEGMENT_MM:g} mm); o Revit rejeita.")
    return errors


def validate(spec: dict) -> None:
    errors = schema_errors(spec)
    if not errors:
        errors = semantic_errors(spec)
    if errors:
        raise SpecValidationError(errors)

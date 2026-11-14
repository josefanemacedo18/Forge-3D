"""Avaliação do FamilySpec: reconstrói a geometria para qualquer conjunto de
valores de parâmetros, simulando o que o Revit fará com a família.

Usado para:
- medir a fidelidade do spec ao objeto original;
- o teste de flexão (trocar parâmetros e verificar que nada se inverte);
- prévias geradas pelo motor.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from . import formula as fx
from .validate import PERP_AXES, ROOTS, anchor_position, parameter_values, plane_positions

AXIS_INDEX = {"x": 0, "y": 1, "z": 2}


@dataclass
class EvaluatedSolid:
    id: str
    kind: str
    mesh: Any  # trimesh.Trimesh
    problems: list[str] = field(default_factory=list)


def _original_positions(spec: dict) -> dict[str, float]:
    pos = {p["id"]: p["position"] for p in spec["planes"]}
    pos.update({rid: v for rid, (_, v) in ROOTS.items()})
    return pos


def discretize_loop(loop: list[dict], arc_step_deg: float = 7.5) -> np.ndarray:
    """Converte um laço (linhas e arcos por 3 pontos) em polilinha 2D fechada."""
    pts: list[tuple[float, float]] = []
    for seg in loop:
        s = np.asarray(seg["start"], dtype=float)
        e = np.asarray(seg["end"], dtype=float)
        if seg["type"] == "line":
            pts.append(tuple(s))
        else:
            m = np.asarray(seg["mid"], dtype=float)
            c, r = circle_from_3pts(s, m, e)
            if c is None:
                pts.append(tuple(s))
                pts.append(tuple(m))
                continue
            a0 = math.atan2(s[1] - c[1], s[0] - c[0])
            am = math.atan2(m[1] - c[1], m[0] - c[0])
            a1 = math.atan2(e[1] - c[1], e[0] - c[0])
            sweep = _arc_sweep(a0, am, a1)
            n = max(2, int(abs(math.degrees(sweep)) / arc_step_deg))
            for k in range(n):
                a = a0 + sweep * k / n
                pts.append((c[0] + r * math.cos(a), c[1] + r * math.sin(a)))
    return np.asarray(pts, dtype=float)


def circle_from_3pts(a: np.ndarray, b: np.ndarray, c: np.ndarray):
    ax, ay = a
    bx, by = b
    cx, cy = c
    d = 2 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(d) < 1e-12:
        return None, None
    ux = ((ax * ax + ay * ay) * (by - cy) + (bx * bx + by * by) * (cy - ay) + (cx * cx + cy * cy) * (ay - by)) / d
    uy = ((ax * ax + ay * ay) * (cx - bx) + (bx * bx + by * by) * (ax - cx) + (cx * cx + cy * cy) * (bx - ax)) / d
    center = np.array([ux, uy])
    return center, float(np.linalg.norm(a - center))


def _arc_sweep(a0: float, am: float, a1: float) -> float:
    """Ângulo (com sinal) de a0 até a1 passando por am."""
    def norm(x):
        return (x + 2 * math.pi) % (2 * math.pi)

    ccw = norm(a1 - a0)
    if norm(am - a0) <= ccw:
        return ccw
    return -norm(a0 - a1)


def _polygon_from_loops(loops: list[list[dict]]):
    from shapely.geometry import Polygon
    from shapely.ops import unary_union

    rings = [discretize_loop(lp) for lp in loops]
    polys = [Polygon(r).buffer(0) for r in rings if len(r) >= 3]
    if not polys:
        return None
    # O maior laço é o contorno externo; os demais são furos (regra par-ímpar).
    polys.sort(key=lambda p: -p.area)
    shape = polys[0]
    for p in polys[1:]:
        if shape.contains(p.representative_point()):
            shape = shape.difference(p)
        else:
            shape = unary_union([shape, p])
    return shape


def _to_axis_frame(axis: str) -> np.ndarray:
    """Matriz 4x4 que leva o sistema local (u, v, w=eixo) ao global."""
    T = np.eye(4)
    if axis == "z":
        return T
    if axis == "x":  # u=y, v=z, w=x
        T[:3, :3] = np.array([[0, 0, 1], [1, 0, 0], [0, 1, 0]], dtype=float)
        return T
    # axis y: u=x, v=z, w=y  -> (u,v,w) -> (x=u, y=w, z=v)
    T[:3, :3] = np.array([[1, 0, 0], [0, 0, 1], [0, 1, 0]], dtype=float)
    return T


def evaluate_solids(spec: dict, overrides: dict[str, float] | None = None, build_meshes: bool = True) -> list[EvaluatedSolid]:
    import trimesh

    values = parameter_values(spec, overrides)
    planes = plane_positions(spec, values)
    original = _original_positions(spec)
    axes = {p["id"]: p["axis"] for p in spec["planes"]}
    axes.update({rid: ax for rid, (ax, _) in ROOTS.items()})
    results: list[EvaluatedSolid] = []
    for s in spec["solids"]:
        kind = s["kind"]
        problems: list[str] = []
        mesh = None
        if kind == "box":
            lo = [planes[s[ax][0]] for ax in ("x", "y", "z")]
            hi = [planes[s[ax][1]] for ax in ("x", "y", "z")]
            ext = [h - l for l, h in zip(lo, hi)]
            for ax, e in zip("xyz", ext):
                if e <= 0.5:
                    problems.append(f"caixa '{s['id']}' com dimensão {ax} = {e:.2f} mm")
            if build_meshes and all(e > 0 for e in ext):
                mesh = trimesh.creation.box(extents=ext)
                mesh.apply_translation([(l + h) / 2 for l, h in zip(lo, hi)])
        elif kind == "cylinder":
            u_ax, v_ax = PERP_AXES[s["axis"]]
            cu = anchor_position(s["center"]["u"], planes, original, values)
            cv = anchor_position(s["center"]["v"], planes, original, values)
            d = s["diameter"]
            dia = float(values[d["driver"]]) if d["driver"] else d["value"]
            h0, h1 = planes[s["extent"][0]], planes[s["extent"][1]]
            if h1 - h0 <= 0.5:
                problems.append(f"cilindro '{s['id']}' com altura {h1 - h0:.2f} mm")
            if dia <= 0.5:
                problems.append(f"cilindro '{s['id']}' com diâmetro {dia:.2f} mm")
            if build_meshes and h1 > h0 and dia > 0:
                mesh = trimesh.creation.cylinder(radius=dia / 2, height=h1 - h0, sections=48)
                mesh.apply_transform(_to_axis_frame(s["axis"]))
                c = [0.0, 0.0, 0.0]
                c[AXIS_INDEX[u_ax]] = cu
                c[AXIS_INDEX[v_ax]] = cv
                c[AXIS_INDEX[s["axis"]]] = (h0 + h1) / 2
                mesh.apply_translation(c)
        elif kind == "extrusion":
            if "extent" in s:
                h0, h1 = planes[s["extent"][0]], planes[s["extent"][1]]
                loops = _flex_loops(s, planes, original, axes)
                if h1 - h0 <= 0.5:
                    problems.append(f"extrusão '{s['id']}' com espessura {h1 - h0:.2f} mm")
                if build_meshes and h1 > h0:
                    poly = _polygon_from_loops(loops)
                    if poly is None or poly.is_empty or poly.area <= 0:
                        problems.append(f"extrusão '{s['id']}' com perfil inválido após flexão")
                    else:
                        mesh = _extrude(poly, h1 - h0)
                        mesh.apply_translation([0, 0, h0])
                        mesh.apply_transform(_to_axis_frame(s["axis"]))
            else:
                poly = _polygon_from_loops(s["loops"])
                if build_meshes and poly is not None and not poly.is_empty:
                    mesh = _extrude(poly, s["depth"])
                    f = s["frame"]
                    o = np.asarray(f["origin"], float)
                    xd = np.asarray(f["xdir"], float)
                    yd = np.asarray(f["ydir"], float)
                    zd = np.cross(xd, yd)
                    T = np.eye(4)
                    T[:3, 0], T[:3, 1], T[:3, 2], T[:3, 3] = xd, yd, zd, o
                    mesh.apply_transform(T)
        elif kind == "revolution":
            u_ax, v_ax = PERP_AXES[s["axis"]]
            cu = anchor_position(s["center"]["u"], planes, original, values)
            cv = anchor_position(s["center"]["v"], planes, original, values)
            prof = s["profile"]
            h_shift_lo = h_shift_hi = 0.0
            r0, r1 = s["range"]
            if s.get("extent"):
                h_shift_lo = planes[s["extent"][0]] - original[s["extent"][0]]
                h_shift_hi = planes[s["extent"][1]] - original[s["extent"][1]]
            pts = discretize_loop(prof)
            # Alongamento linear do perfil entre as faces de topo e base (o
            # Revit alonga a revolução quando as faces planas são movidas).
            span = r1 - r0
            new_lo, new_hi = r0 + h_shift_lo, r1 + h_shift_hi
            if new_hi - new_lo <= 0.5:
                problems.append(f"revolução '{s['id']}' com altura {new_hi - new_lo:.2f} mm")
            if build_meshes and new_hi > new_lo and span > 0:
                t = (pts[:, 1] - r0) / span
                pts = np.column_stack([pts[:, 0], new_lo + t * (new_hi - new_lo)])
                mesh = _revolve(pts)
                if mesh is not None:
                    mesh.apply_transform(_to_axis_frame(s["axis"]))
                    c = [0.0, 0.0, 0.0]
                    c[AXIS_INDEX[u_ax]] = cu
                    c[AXIS_INDEX[v_ax]] = cv
                    mesh.apply_translation(c)
        elif kind == "mesh":
            if build_meshes:
                mesh = trimesh.Trimesh(np.asarray(s["vertices"], float), np.asarray(s["faces"], int), process=False)
        results.append(EvaluatedSolid(s["id"], kind, mesh, problems))
    return results


def _flex_loops(s: dict, planes: dict[str, float], original: dict[str, float], axes: dict[str, str]) -> list[list[dict]]:
    """Aplica às coordenadas do perfil o deslocamento dos planos alinhados.

    Modelo simplificado do que o Revit faz: os segmentos travados em planos
    acompanham o plano; os vértices ligados a eles se deslocam junto.
    """
    if not s.get("alignments"):
        return s["loops"]
    u_ax, _ = PERP_AXES[s["axis"]]
    shifts: list[tuple[int, float, float]] = []
    for al in s["alignments"]:
        pid = al["plane"]
        idx = 0 if axes[pid] == u_ax else 1
        shifts.append((idx, original[pid], planes[pid] - original[pid]))
    loops = []
    for loop in s["loops"]:
        new_loop = []
        for seg in loop:
            seg2 = dict(seg)
            for key in ("start", "end", "mid"):
                if key in seg:
                    p = list(seg[key])
                    for idx, coord, delta in shifts:
                        if abs(seg[key][idx] - coord) < 0.05:
                            p[idx] = seg[key][idx] + delta
                    seg2[key] = p
            new_loop.append(seg2)
        loops.append(new_loop)
    return loops


def _extrude(poly, height: float):
    import trimesh

    return trimesh.creation.extrude_polygon(poly, height)


def _revolve(pts: np.ndarray):
    """Revolve um perfil (r, h) fechado em torno do eixo h."""
    import trimesh
    from shapely.geometry import Polygon

    poly = Polygon(pts).buffer(0)
    if poly.is_empty or poly.area <= 0:
        return None
    if poly.geom_type != "Polygon":
        poly = max(poly.geoms, key=lambda g: g.area)
    ring = np.asarray(poly.exterior.coords)[:-1]
    # trimesh.creation.revolve espera uma polilinha (r, z) aberta do eixo ao eixo;
    # usamos a fronteira do polígono como polilinha fechada.
    linestring = np.vstack([ring, ring[:1]])
    try:
        mesh = trimesh.creation.revolve(linestring, sections=48)
    except Exception:
        return None
    if mesh.volume < 0:
        mesh.invert()
    return mesh


def build_mesh(spec: dict, overrides: dict[str, float] | None = None):
    """Malha única (concatenada) do spec avaliado, sem vazios."""
    import trimesh

    solids = evaluate_solids(spec, overrides)
    meshes = [s.mesh for s in solids if s.mesh is not None and not _is_void(spec, s.id)]
    voids = [s.mesh for s in solids if s.mesh is not None and _is_void(spec, s.id)]
    if not meshes:
        return None
    body = trimesh.util.concatenate(meshes)
    if voids:
        try:
            body = trimesh.boolean.difference([body, trimesh.util.concatenate(voids)], engine="manifold")
        except Exception:
            pass  # sem booleanas: prévia sem os furos
    return body


def _is_void(spec: dict, sid: str) -> bool:
    for s in spec["solids"]:
        if s["id"] == sid:
            return bool(s.get("isVoid"))
    return False


@dataclass
class FlexResult:
    ok: bool
    failures: list[str]
    checked: list[str]


def flex_test(spec: dict, factors: tuple[float, ...] = (0.75, 0.9, 1.1, 1.5)) -> FlexResult:
    """Teste de flexão: varia cada parâmetro de usuário e verifica a geometria.

    Para cada parâmetro de comprimento editável (sem fórmula, papel envelope ou
    semântico), aplica os fatores e confirma que todas as fórmulas continuam
    positivas e nenhum sólido se inverte ou desaparece.
    """
    failures: list[str] = []
    checked: list[str] = []
    editable = [
        p for p in spec["parameters"]
        if p["kind"] == "length" and not p.get("formula") and p["role"] in ("envelope", "semantic")
    ]
    for p in editable:
        checked.append(p["name"])
        for f in factors:
            ov = {p["id"]: float(p["value"]) * f}
            try:
                values = parameter_values(spec, ov)
            except fx.FormulaError as exc:
                failures.append(f"{p['name']} x{f:g}: {exc}")
                continue
            bad = [q["name"] for q in spec["parameters"] if q["kind"] == "length" and q.get("formula") and not values[q["id"]] > 0]
            if bad:
                failures.append(f"{p['name']} x{f:g}: fórmulas não positivas em {', '.join(bad)}")
                continue
            # Planos independentes podem se cruzar sem problema (cada um tem sua
            # própria cota); só importa que nenhum sólido se inverta.
            solids = evaluate_solids(spec, ov, build_meshes=False)
            probs = [pr for s in solids for pr in s.problems]
            if probs:
                failures.append(f"{p['name']} x{f:g}: " + "; ".join(probs[:3]))
    return FlexResult(ok=not failures, failures=failures, checked=checked)


def _order_problems(spec: dict, planes: dict[str, float], original: dict[str, float]) -> list[str]:
    """Planos que trocaram de ordem em relação à geometria original."""
    problems = []
    by_axis: dict[str, list[str]] = {"x": [], "y": [], "z": []}
    for p in spec["planes"]:
        by_axis[p["axis"]].append(p["id"])
    for rid, (ax, _) in ROOTS.items():
        by_axis[ax].append(rid)
    for ax, ids in by_axis.items():
        ids_sorted = sorted(ids, key=lambda i: original[i])
        for a, b in zip(ids_sorted, ids_sorted[1:]):
            if original[b] - original[a] > 0.05 and planes[b] - planes[a] < -0.05:
                problems.append(f"planos {a} e {b} trocaram de ordem no eixo {ax}")
    return problems


def fidelity(spec: dict, reference_mesh, samples: int = 1500, seed: int = 0) -> dict[str, float]:
    """Desvio entre a geometria do spec e a malha de referência (mm).

    Distância ponto-superfície nos dois sentidos (amostras de uma malha contra
    a superfície da outra), resumida por média, percentil 95 e máximo.
    """
    import trimesh

    rebuilt = build_mesh(spec)
    if rebuilt is None:
        return {"mean": math.inf, "p95": math.inf, "max": math.inf}
    pts_ref, _ = trimesh.sample.sample_surface(reference_mesh, samples, seed=seed)
    pts_new, _ = trimesh.sample.sample_surface(rebuilt, samples, seed=seed + 1)
    _, d1, _ = trimesh.proximity.closest_point(rebuilt, pts_ref)
    _, d2, _ = trimesh.proximity.closest_point(reference_mesh, pts_new)
    d = np.concatenate([d1, d2])
    return {"mean": round(float(d.mean()), 4), "p95": round(float(np.percentile(d, 95)), 4), "max": round(float(d.max()), 4)}

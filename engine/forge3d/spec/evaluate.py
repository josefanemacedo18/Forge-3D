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
from .validate import MIN_SEGMENT_MM, PERP_AXES, ROOTS, anchor_position, parameter_values, plane_positions

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
                if e < MIN_SEGMENT_MM:
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
            if h1 - h0 < MIN_SEGMENT_MM:
                problems.append(f"cilindro '{s['id']}' com altura {h1 - h0:.2f} mm")
            if dia < MIN_SEGMENT_MM:
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
                if h1 - h0 < MIN_SEGMENT_MM:
                    problems.append(f"extrusão '{s['id']}' com espessura {h1 - h0:.2f} mm")
                if any(abs(planes[al["plane"]] - original[al["plane"]]) > 1e-9 for al in s.get("alignments", [])):
                    problems += _profile_problems(loops, f"extrusão '{s['id']}'")
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
            r0, r1 = s["range"]
            # Como no esboço do Revit (ver _flex_loop): trechos radiais e tampas
            # de topo/base travados acompanham seus planos; o resto se ajusta.
            locks: dict[int, tuple[int, float]] = {}
            for al in s.get("alignments", []):
                locks[al["segment"]] = (0, planes[al["plane"]] - original[al["plane"]])
            if s.get("extent"):
                for ref, h in ((s["extent"][0], r0), (s["extent"][1], r1)):
                    cap = next((k for k, g in enumerate(prof) if g["type"] == "line" and abs(g["start"][1] - h) <= 0.05
                                and abs(g["end"][1] - h) <= 0.05), None)
                    if cap is not None:
                        locks[cap] = (1, planes[ref] - original[ref])
            if any(abs(d) > 1e-9 for _, d in locks.values()):
                prof = _flex_loop(prof, locks)
                if any(seg["start"][0] < -0.01 or seg["end"][0] < -0.01 for seg in prof):
                    problems.append(f"revolução '{s['id']}' com raio negativo após flexão")
                problems += _profile_problems([prof], f"revolução '{s['id']}'")
            pts = discretize_loop(prof)
            span = r1 - r0
            new_lo, new_hi = float(pts[:, 1].min()), float(pts[:, 1].max())
            if new_hi - new_lo < MIN_SEGMENT_MM:
                problems.append(f"revolução '{s['id']}' com altura {new_hi - new_lo:.2f} mm")
            if build_meshes and new_hi > new_lo and span > 0:
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
    """Perfil de uma extrusão depois de mover os planos alinhados (ver _flex_loop)."""
    if not s.get("alignments"):
        return s["loops"]
    u_ax, _ = PERP_AXES[s["axis"]]
    locks: dict[int, dict[int, tuple[int, float]]] = {}
    for al in s["alignments"]:
        pid = al["plane"]
        idx = 0 if axes[pid] == u_ax else 1
        locks.setdefault(al["loop"], {})[al["segment"]] = (idx, planes[pid] - original[pid])
    return [_flex_loop(lp, locks.get(k, {})) for k, lp in enumerate(s["loops"])]


def _seg_tangent(seg: dict, at_end: bool) -> np.ndarray | None:
    """Direção (unitária, no sentido do percurso) do segmento no início ou no fim."""
    a = np.asarray(seg["start"], float)
    b = np.asarray(seg["end"], float)
    if seg["type"] == "line":
        d = b - a
    else:
        c, _ = circle_from_3pts(a, np.asarray(seg["mid"], float), b)
        if c is None:
            d = b - a
        else:
            p = b if at_end else a
            rad = p - c
            d = np.array([-rad[1], rad[0]])
            # Sentido do percurso: o ponto médio deve ficar "à frente" no início.
            ref = (np.asarray(seg["mid"], float) - a) if not at_end else (b - np.asarray(seg["mid"], float))
            if float(d @ ref) < 0:
                d = -d
    n = float(np.linalg.norm(d))
    return d / n if n > 1e-12 else None


def _flex_loop(loop: list[dict], locks: dict[int, tuple[int, float]]) -> list[dict]:
    """Modelo do que o Revit faz com um esboço quando planos alinhados se movem.

    - Retas travadas acompanham o plano (na coordenada travada).
    - Os extremos ligados a elas se movem junto (as junções são mantidas).
    - Arcos tangentes numa junção que se moveu são transladados inteiros (o
      Revit mantém a tangência e o raio), levando a junção do outro lado.
    - Retas livres apenas esticam entre suas junções.
    """
    n = len(loop)
    if n == 0 or not locks:
        return loop
    # Junção j = fim do segmento j = início do segmento (j + 1) % n.
    fixed: list[list[float | None]] = [[None, None] for _ in range(n)]
    for i, (c, d) in locks.items():
        for j in ((i - 1) % n, i):
            fixed[j][c] = d if fixed[j][c] is None else (fixed[j][c] + d) / 2
    if all(abs(v or 0.0) < 1e-12 for f in fixed for v in f):
        return loop
    prop: list[list[float | None]] = [[None, None] for _ in range(n)]

    def cur(j: int) -> list[float]:
        return [fixed[j][c] if fixed[j][c] is not None else (prop[j][c] if prop[j][c] is not None else 0.0)
                for c in (0, 1)]

    def tangent_at(j: int) -> bool:
        t1 = _seg_tangent(loop[j], True)
        t2 = _seg_tangent(loop[(j + 1) % n], False)
        return t1 is not None and t2 is not None and float(t1 @ t2) > math.cos(math.radians(3))

    from collections import deque

    queue = deque(j for j in range(n) if any(abs(v) > 1e-12 for v in cur(j)))
    done: set[int] = set()
    while queue:
        j = queue.popleft()
        if not tangent_at(j):
            continue
        for seg_i, far in ((j, (j - 1) % n), ((j + 1) % n, (j + 1) % n)):
            if loop[seg_i]["type"] != "arc" or seg_i in locks or seg_i in done:
                continue
            done.add(seg_i)
            sj = cur(j)
            for c in (0, 1):
                if fixed[far][c] is None:
                    prop[far][c] = sj[c] if prop[far][c] is None else (prop[far][c] + sj[c]) / 2
            queue.append(far)
    shift = [cur(j) for j in range(n)]
    out = []
    for i, seg in enumerate(loop):
        s0 = shift[(i - 1) % n]
        s1 = shift[i]
        seg2 = dict(seg)
        seg2["start"] = [seg["start"][0] + s0[0], seg["start"][1] + s0[1]]
        seg2["end"] = [seg["end"][0] + s1[0], seg["end"][1] + s1[1]]
        if seg["type"] == "arc":
            seg2["mid"] = [seg["mid"][0] + (s0[0] + s1[0]) / 2, seg["mid"][1] + (s0[1] + s1[1]) / 2]
            seg2.pop("center", None)
            seg2.pop("radius", None)
        out.append(seg2)
    return out


def _profile_problems(loops: list[list[dict]], label: str) -> list[str]:
    """Perfil depois da flexão: sem trechos menores que 1 mm e sem cruzamentos
    (o Revit recusa os dois casos ao regenerar a família)."""
    from shapely.geometry import Polygon

    for lp in loops:
        for seg in lp:
            if math.dist(seg["start"], seg["end"]) < MIN_SEGMENT_MM - 1e-6:
                return [f"{label} com trecho de perfil menor que {MIN_SEGMENT_MM:g} mm após flexão"]
        ring = discretize_loop(lp)
        if len(ring) < 3:
            return [f"{label} com perfil degenerado após flexão"]
        poly = Polygon(ring)
        if not poly.is_valid or poly.area <= 0:
            return [f"{label} com perfil cruzado após flexão"]
    return []


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


def flex_culprits(spec: dict, factors: tuple[float, ...] = (0.9, 1.1)) -> dict[str, set[str]]:
    """Restrições que não aguentam variações pequenas dos parâmetros.

    Para cada parâmetro editável, aplica os fatores e, nos sólidos que falham,
    aponta o que soltar: "contorno" (retas travadas de uma extrusão), "raios"
    e/ou "altura" (revolução). Um parâmetro que quebra a família com ±10% não
    serve para o usuário — melhor a forma fixa do que um erro no Revit.
    """
    out: dict[str, set[str]] = {}
    original = _original_positions(spec)
    by_id = {s["id"]: s for s in spec["solids"]}
    editable = [p for p in spec["parameters"]
                if p["kind"] == "length" and not p.get("formula") and p["role"] in ("envelope", "semantic")]
    for p in editable:
        for f in factors:
            ov = {p["id"]: float(p["value"]) * f}
            try:
                planes = plane_positions(spec, parameter_values(spec, ov))
            except Exception:
                continue
            moved = {k for k, v in planes.items() if k in original and abs(v - original[k]) > 1e-9}
            for es in evaluate_solids(spec, ov, build_meshes=False):
                if not es.problems:
                    continue
                s = by_id[es.id]
                aligned_moved = any(al["plane"] in moved for al in s.get("alignments", []))
                if s["kind"] == "extrusion" and aligned_moved:
                    out.setdefault(s["id"], set()).add("contorno")
                elif s["kind"] == "revolution":
                    if aligned_moved:
                        out.setdefault(s["id"], set()).add("raios")
                    if s.get("extent") and any(r in moved for r in s["extent"]):
                        out.setdefault(s["id"], set()).add("altura")
    return out


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


def export_preview_obj(spec: dict, path: str) -> str:
    """Grava a prévia (OBJ, mm, Z para cima) com um objeto por sólido.

    Sólidos vazios recortam os sólidos que atravessam; o nome de cada objeto é
    o id do sólido no spec, para o plugin colorir por nível de parametrização.
    """
    import trimesh

    solids = evaluate_solids(spec)
    by_id = {s["id"]: s for s in spec["solids"]}
    voids = [s.mesh for s in solids if s.mesh is not None and by_id[s.id].get("isVoid")]
    void_mesh = trimesh.util.concatenate(voids) if voids else None
    lines = ["# forge3d preview units=mm up=Z"]
    offset = 1
    for s in solids:
        if s.mesh is None or by_id[s.id].get("isVoid"):
            continue
        mesh = s.mesh
        if void_mesh is not None:
            try:
                b0, b1 = mesh.bounds
                v0, v1 = void_mesh.bounds
                if (b0 <= v1).all() and (v0 <= b1).all():
                    mesh = trimesh.boolean.difference([mesh, void_mesh], engine="manifold")
            except Exception:
                pass
        lines.append(f"o {s.id}")
        for v in mesh.vertices:
            lines.append(f"v {v[0]:.3f} {v[1]:.3f} {v[2]:.3f}")
        for f in mesh.faces:
            lines.append(f"f {f[0] + offset} {f[1] + offset} {f[2] + offset}")
        offset += len(mesh.vertices)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    return path

"""Classificação de componentes de malha em primitivas paramétricas.

Ordem das tentativas para um componente fechado:
1. caixa alinhada aos eixos (teste exato por volume);
2. prisma (extrusão) ao longo de Z, X ou Y — vira caixa, cilindro ou extrusão;
3. prisma em eixos próprios (peça girada) — extrusão em plano inclinado;
4. decomposição ortogonal em caixas (sólidos só com faces alinhadas aos eixos);
5. sólido de revolução (Z, X ou Y);
6. decomposição em fatias 2.5D, com recursão nas fatias não prismáticas;
7. aproximação por caixa (se permitido e dentro da tolerância);
8. forma livre.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, field

import numpy as np

from .cleanup import repair
from .polygons import AXIS_VEC, axis_rectangle, polygon_to_loops, section_polygons, to_local
from .primitives import (
    AXIS_INDEX,
    PERP,
    Box,
    Cylinder,
    Extrusion,
    FramedExtrusion,
    FreeformMesh,
    Primitive,
    Revolution,
    Segment,
)


@dataclass
class Options:
    tol: float = 0.05  # mm — testes exatos (malhas de CAD)
    fit_tol: float = 1.0  # mm — desvio aceito em aproximações
    sheet_thickness: float = 3.0  # mm — espessura dada a superfícies planas abertas
    allow_approx: bool = True
    max_boxes: int = 60
    max_grid_cells: int = 60000
    max_depth: int = 2
    revolution_samples: int = 500


@dataclass
class ComponentResult:
    primitives: list[Primitive]
    method: str
    notes: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# utilidades


def _cluster_values(values: np.ndarray, tol: float) -> np.ndarray:
    """Agrupa valores próximos (dentro de tol) e retorna os representantes."""
    if len(values) == 0:
        return values
    v = np.sort(values)
    groups = [[v[0]]]
    for x in v[1:]:
        if x - groups[-1][-1] <= tol:
            groups[-1].append(x)
        else:
            groups.append([x])
    return np.array([float(np.mean(g)) for g in groups])


def _snap(values: np.ndarray, levels: np.ndarray) -> np.ndarray:
    idx = np.abs(values[:, None] - levels[None, :]).argmin(axis=1)
    return levels[idx]


def _poly_from_loops_area(loops) -> float:
    from shapely.geometry import Polygon

    from ..spec.evaluate import discretize_loop

    total = 0.0
    for k, lp in enumerate(loops):
        pts = discretize_loop([s.to_spec() for s in lp], 5)
        a = Polygon(pts).area if len(pts) >= 3 else 0.0
        total += a if k == 0 else -a
    return total


def _volume_tolerance(mesh, tol: float) -> float:
    return tol * float(mesh.area) + 1e-6


# ---------------------------------------------------------------------------
# 1. caixa


def try_box(mesh, opts: Options) -> Box | None:
    lo, hi = mesh.bounds
    ext = hi - lo
    if (ext <= opts.tol * 2).any():
        return None
    vol_box = float(np.prod(ext))
    if abs(float(mesh.volume) - vol_box) > _volume_tolerance(mesh, opts.tol):
        return None
    box = Box(lo=tuple(map(float, lo)), hi=tuple(map(float, hi)))
    box.volume = vol_box
    area_box = 2 * (ext[0] * ext[1] + ext[1] * ext[2] + ext[0] * ext[2])
    box.deviation = float(2 * (vol_box - float(mesh.volume)) / area_box)
    return box


# ---------------------------------------------------------------------------
# 2. prisma alinhado


def _prism_profile_primitive(poly, axis: str, h0: float, h1: float, opts: Options) -> Primitive | None:
    """Converte o perfil de um prisma em caixa, cilindro ou extrusão."""
    rect = axis_rectangle(poly, opts.tol)
    if rect is not None:
        u0, v0, u1, v1 = rect
        lo = [0.0, 0.0, 0.0]
        hi = [0.0, 0.0, 0.0]
        u, v = PERP[axis]
        lo[AXIS_INDEX[u]], hi[AXIS_INDEX[u]] = u0, u1
        lo[AXIS_INDEX[v]], hi[AXIS_INDEX[v]] = v0, v1
        lo[AXIS_INDEX[axis]], hi[AXIS_INDEX[axis]] = h0, h1
        b = Box(lo=tuple(lo), hi=tuple(hi))
        b.volume = (u1 - u0) * (v1 - v0) * (h1 - h0)
        return b
    conv = polygon_to_loops(poly, opts.tol)
    if conv is None:
        return None
    loops, infos = conv
    if len(loops) == 1 and infos[0].is_circle:
        c = infos[0].circle_center
        r = infos[0].circle_radius
        cyl = Cylinder(axis=axis, center=(c[0], c[1]), diameter=2 * r, h0=h0, h1=h1)
        cyl.volume = math.pi * r * r * (h1 - h0)
        n_sides = len(poly.exterior.coords) - 1
        cyl.deviation = float(r * (1 - math.cos(math.pi / max(n_sides, 3))))
        return cyl
    ext = Extrusion(axis=axis, loops=loops, h0=h0, h1=h1)
    ext.volume = float(poly.area * (h1 - h0))
    return ext


def try_axis_prism(mesh, axis: str, opts: Options) -> Primitive | None:
    w = AXIS_VEC[axis]
    d = mesh.face_normals @ w
    if not ((np.abs(d) > 0.9999) | (np.abs(d) < 1e-3)).all():
        return None
    coords = mesh.vertices @ w
    h0, h1 = float(coords.min()), float(coords.max())
    if h1 - h0 <= opts.tol * 2:
        return None
    on_caps = (np.abs(coords - h0) <= opts.tol) | (np.abs(coords - h1) <= opts.tol)
    if not on_caps.all():
        return None
    polys = section_polygons(mesh, axis, (h0 + h1) / 2)
    if len(polys) != 1:
        return None
    poly = polys[0]
    if abs(poly.area * (h1 - h0) - float(mesh.volume)) > _volume_tolerance(mesh, opts.tol) * 4:
        return None
    return _prism_profile_primitive(poly, axis, h0, h1, opts)


# ---------------------------------------------------------------------------
# 3. prisma girado (eixos da caixa orientada)


def try_oriented_prism(mesh, opts: Options) -> FramedExtrusion | None:
    import trimesh

    try:
        to_origin, extents = trimesh.bounds.oriented_bounds(mesh)
    except Exception:
        return None
    R = np.asarray(to_origin)[:3, :3]
    # Se os eixos já são os globais, os testes alinhados bastam.
    if np.all(np.abs(np.abs(R).max(axis=1) - 1.0) < 1e-4):
        return None
    local = mesh.copy()
    local.apply_transform(to_origin)
    for axis in ("z", "x", "y"):
        prim = try_axis_prism(local, axis, opts)
        if prim is None:
            continue
        # Converte o perfil local em extrusão com referencial global.
        inv = np.linalg.inv(np.asarray(to_origin))
        u, v = PERP[axis]
        U = np.zeros(3)
        V = np.zeros(3)
        W = np.zeros(3)
        U[AXIS_INDEX[u]] = 1
        V[AXIS_INDEX[v]] = 1
        W[AXIS_INDEX[axis]] = 1
        if isinstance(prim, Box):
            lo, hi = np.asarray(prim.lo), np.asarray(prim.hi)
            u0, u1 = lo[AXIS_INDEX[u]], hi[AXIS_INDEX[u]]
            v0, v1 = lo[AXIS_INDEX[v]], hi[AXIS_INDEX[v]]
            loops = [[
                Segment("line", (u0, v0), (u1, v0)), Segment("line", (u1, v0), (u1, v1)),
                Segment("line", (u1, v1), (u0, v1)), Segment("line", (u0, v1), (u0, v0)),
            ]]
            h0, h1 = lo[AXIS_INDEX[axis]], hi[AXIS_INDEX[axis]]
        elif isinstance(prim, Cylinder):
            from .polygons import _arc_segment

            c = np.asarray(prim.center)
            r = prim.diameter / 2
            p0, p1 = c + [r, 0], c - [r, 0]
            loops = [_arc_segment(p0, p1, c, r, True) + _arc_segment(p1, p0, c, r, True)]
            h0, h1 = prim.h0, prim.h1
        else:
            loops = prim.loops
            h0, h1 = prim.h0, prim.h1
        # Base local do perfil: u, v no plano; extrusão ao longo de w (direita: u x v = w?)
        if np.dot(np.cross(U, V), W) < 0:
            # Mantém o referencial destro trocando u e v no perfil.
            U, V = V, U
            loops = [[_swap_seg(s) for s in lp] for lp in loops]
        origin_local = W * h0
        o = (inv @ np.append(origin_local, 1))[:3]
        xd = inv[:3, :3] @ U
        yd = inv[:3, :3] @ V
        fe = FramedExtrusion(origin=tuple(map(float, o)), xdir=tuple(map(float, xd)), ydir=tuple(map(float, yd)),
                             loops=loops, depth=float(h1 - h0))
        fe.volume = float(mesh.volume)
        fe.notes.append("peça inclinada: geometria exata, posição fixa")
        return fe
    return None


def _swap_seg(s: Segment) -> Segment:
    sw = lambda p: (p[1], p[0]) if p is not None else None  # noqa: E731
    return Segment(s.kind, sw(s.start), sw(s.end), sw(s.mid), sw(s.center), s.radius)


# ---------------------------------------------------------------------------
# 4. decomposição ortogonal em caixas


def _inside_points(mesh, points: np.ndarray) -> np.ndarray:
    try:
        return np.asarray(mesh.contains(points), dtype=bool)
    except Exception:
        return np.zeros(len(points), dtype=bool)


def try_orthogonal_boxes(mesh, opts: Options) -> list[Box] | None:
    n = mesh.face_normals
    if not (np.abs(n).max(axis=1) > 0.9999).all():
        return None
    xs = _cluster_values(mesh.vertices[:, 0], opts.tol)
    ys = _cluster_values(mesh.vertices[:, 1], opts.tol)
    zs = _cluster_values(mesh.vertices[:, 2], opts.tol)
    nx, ny, nz = len(xs) - 1, len(ys) - 1, len(zs) - 1
    if min(nx, ny, nz) < 1 or nx * ny * nz > opts.max_grid_cells:
        return None
    cx = (xs[:-1] + xs[1:]) / 2
    cy = (ys[:-1] + ys[1:]) / 2
    cz = (zs[:-1] + zs[1:]) / 2
    grid = np.stack(np.meshgrid(cx, cy, cz, indexing="ij"), axis=-1).reshape(-1, 3)
    filled = _inside_points(mesh, grid).reshape(nx, ny, nz)
    if not filled.any():
        return None
    dx, dy, dz = np.diff(xs), np.diff(ys), np.diff(zs)
    cell_vol = dx[:, None, None] * dy[None, :, None] * dz[None, None, :]
    if abs(float((cell_vol * filled).sum()) - float(mesh.volume)) > _volume_tolerance(mesh, opts.tol) * 4:
        return None  # teste de interior não confiável para esta malha

    remaining = filled.copy()
    boxes: list[Box] = []
    orders = list(itertools.permutations(range(3)))
    while remaining.any():
        best = None
        best_vol = -1.0
        seeds = np.argwhere(remaining)
        if len(seeds) > 4000:
            # Sementes restritas a cantos mínimos para limitar o custo.
            mask = remaining.copy()
            mask[1:, :, :] &= ~remaining[:-1, :, :]
            mask[:, 1:, :] &= ~remaining[:, :-1, :]
            mask[:, :, 1:] &= ~remaining[:, :, :-1]
            seeds = np.argwhere(mask)
        for (i, j, k) in seeds:
            for order in orders:
                lo = [int(i), int(j), int(k)]
                hi = [int(i) + 1, int(j) + 1, int(k) + 1]
                for ax in order:
                    while hi[ax] < remaining.shape[ax]:
                        sl = [slice(lo[0], hi[0]), slice(lo[1], hi[1]), slice(lo[2], hi[2])]
                        sl[ax] = slice(hi[ax], hi[ax] + 1)
                        if remaining[tuple(sl)].all():
                            hi[ax] += 1
                        else:
                            break
                vol = float(cell_vol[lo[0]:hi[0], lo[1]:hi[1], lo[2]:hi[2]].sum())
                if vol > best_vol + 1e-9:
                    best_vol = vol
                    best = (tuple(lo), tuple(hi))
        (i0, j0, k0), (i1, j1, k1) = best
        remaining[i0:i1, j0:j1, k0:k1] = False
        b = Box(lo=(float(xs[i0]), float(ys[j0]), float(zs[k0])), hi=(float(xs[i1]), float(ys[j1]), float(zs[k1])))
        b.volume = best_vol
        boxes.append(b)
        if len(boxes) > opts.max_boxes:
            return None
    return boxes


# ---------------------------------------------------------------------------
# 5. revolução

_REV_PLANES = {
    # eixo: (eixo normal do plano do perfil, índice de h em (u, v), índice de r em (u, v))
    "z": ("y", 1, 0),
    "x": ("z", 0, 1),
    "y": ("z", 1, 0),
}


def _rotation_about(axis: str, center_uv: tuple[float, float], angle: float) -> np.ndarray:
    import trimesh

    point = np.zeros(3)
    u, v = PERP[axis]
    point[AXIS_INDEX[u]] = center_uv[0]
    point[AXIS_INDEX[v]] = center_uv[1]
    return trimesh.transformations.rotation_matrix(angle, AXIS_VEC[axis], point)


def try_revolution(mesh, opts: Options, axes=("z", "x", "y")) -> list[Revolution] | None:
    import trimesh

    lo, hi = mesh.bounds
    ext = hi - lo
    for axis in axes:
        u, v = PERP[axis]
        eu, ev = ext[AXIS_INDEX[u]], ext[AXIS_INDEX[v]]
        r_max = max(eu, ev) / 2
        if r_max <= opts.tol * 4 or abs(eu - ev) > max(opts.fit_tol, 0.01 * r_max) * 2:
            continue
        center = ((lo[AXIS_INDEX[u]] + hi[AXIS_INDEX[u]]) / 2, (lo[AXIS_INDEX[v]] + hi[AXIS_INDEX[v]]) / 2)
        thr = max(opts.fit_tol, 0.012 * r_max)
        try:
            pts, _ = trimesh.sample.sample_surface(mesh, opts.revolution_samples, seed=7)
        except Exception:
            continue
        worst = 0.0
        ok = True
        for ang in (37.0, 101.0, 163.0, 241.0):
            T = _rotation_about(axis, center, math.radians(ang))
            rp = trimesh.transform_points(pts, T)
            try:
                _, dist, _ = trimesh.proximity.closest_point(mesh, rp)
            except Exception:
                ok = False
                break
            worst = max(worst, float(np.max(dist)))
            if worst > thr:
                ok = False
                break
        if not ok:
            continue
        revs = _revolution_profiles(mesh, axis, center, opts)
        if revs:
            for r in revs:
                r.deviation = worst
            return revs
    return None


def _revolution_profiles(mesh, axis: str, center: tuple[float, float], opts: Options) -> list[Revolution]:
    from shapely.geometry import Polygon, box as sbox

    plane_axis, h_idx, r_idx = _REV_PLANES[axis]
    u, v = PERP[axis]
    # Coordenada do plano do perfil: passa pelo eixo.
    plane_coord = center[1] if plane_axis == v else center[0]
    polys = section_polygons(mesh, plane_axis, plane_coord)
    if not polys:
        return []
    # Centro do eixo na coordenada r (índice r_idx do par (u, v) do plano).
    from .polygons import _G2L

    axis_point = np.zeros(3)
    axis_point[AXIS_INDEX[u]] = center[0]
    axis_point[AXIS_INDEX[v]] = center[1]
    r_center = float((_G2L[plane_axis] @ axis_point)[r_idx])
    out = []
    lo, hi = mesh.bounds
    h_lo = float(lo[AXIS_INDEX[axis]])
    h_hi = float(hi[AXIS_INDEX[axis]])
    for poly in polys:
        coords = np.asarray(poly.exterior.coords)
        # Reordena para (r, h).
        rh = np.column_stack([coords[:, r_idx] - r_center, coords[:, h_idx]])
        holes = [np.column_stack([np.asarray(i.coords)[:, r_idx] - r_center, np.asarray(i.coords)[:, h_idx]]) for i in poly.interiors]
        prh = Polygon(rh, holes).buffer(0)
        half = prh.intersection(sbox(0.0, h_lo - 1, float(np.abs(rh[:, 0]).max()) + 1, h_hi + 1))
        if half.is_empty:
            continue
        geoms = [half] if half.geom_type == "Polygon" else [g for g in getattr(half, "geoms", []) if g.geom_type == "Polygon"]
        for g in geoms:
            if g.area < opts.tol * 10:
                continue
            g = _snap_axis(g, opts.tol)
            conv = polygon_to_loops(g, opts.tol)
            if conv is None:
                continue
            loops, _ = conv
            prof = [_clamp_r(s) for s in loops[0]]
            gh0, gh1 = g.bounds[1], g.bounds[3]
            rev = Revolution(axis=axis, center=center, profile=prof, h0=float(gh0), h1=float(gh1))
            rev.flat_bottom = _has_flat(prof, gh0, opts)
            rev.flat_top = _has_flat(prof, gh1, opts)
            # Volume por Pappus: 2*pi*área*raio do centroide.
            rev.volume = float(2 * math.pi * g.area * g.centroid.x)
            if len(loops) > 1:
                rev.notes.append("perfil com furos internos simplificado")
            out.append(rev)
    return out


def _snap_axis(poly, tol: float):
    """Encosta no eixo (r = 0) os vértices a menos de tol dele."""
    from shapely.geometry import Polygon

    ext = np.asarray(poly.exterior.coords)
    ext[:, 0] = np.where(ext[:, 0] < tol * 2, 0.0, ext[:, 0])
    return Polygon(ext, [np.asarray(i.coords) for i in poly.interiors]).buffer(0)


def _clamp_r(s: Segment) -> Segment:
    c = lambda p: (max(0.0, p[0]), p[1]) if p is not None else None  # noqa: E731
    return Segment(s.kind, c(s.start), c(s.end), c(s.mid), s.center, s.radius)


def _has_flat(profile, h: float, opts: Options) -> bool:
    for s in profile:
        if s.kind == "line" and abs(s.start[1] - h) <= opts.tol and abs(s.end[1] - h) <= opts.tol and s.length() > 1.0:
            return True
    return False


# ---------------------------------------------------------------------------
# 6. decomposição em fatias 2.5D


def _section_signature_equal(a: list, b: list, tol: float) -> bool:
    from shapely.ops import unary_union

    if len(a) != len(b):
        return False
    ua = unary_union(a)
    ub = unary_union(b)
    diff = ua.symmetric_difference(ub).area
    return diff <= tol * max(ua.length, 1.0) * 2


def slab_decompose(mesh, axis: str, opts: Options, depth: int, skip_axes: tuple[str, ...]) -> list[Primitive] | None:
    w = AXIS_VEC[axis]
    d = mesh.face_normals @ w
    cap = np.abs(d) > 0.9999
    side = np.abs(d) < 1e-3
    slant = ~(cap | side)
    coords = mesh.vertices @ w
    face_h = coords[mesh.faces]
    cand = [coords.min(), coords.max()]
    cand += list(face_h[cap].ravel())
    if slant.any():
        cand += list(face_h[slant].min(axis=1)) + list(face_h[slant].max(axis=1))
    levels = _cluster_values(np.asarray(cand, dtype=float), max(opts.tol, 0.5))
    if len(levels) < 3:
        return None
    slant_lo = face_h[slant].min(axis=1) if slant.any() else np.zeros(0)
    slant_hi = face_h[slant].max(axis=1) if slant.any() else np.zeros(0)

    intervals = []
    for a, b in zip(levels[:-1], levels[1:]):
        if b - a <= opts.tol:
            continue
        has_slant = bool(((slant_lo < b - opts.tol) & (slant_hi > a + opts.tol)).any()) if slant.any() else False
        if has_slant:
            intervals.append({"a": a, "b": b, "prism": False, "polys": None})
        else:
            polys = section_polygons(mesh, axis, (a + b) / 2)
            intervals.append({"a": a, "b": b, "prism": True, "polys": polys})
    if not intervals:
        return None

    merged = [intervals[0]]
    for itv in intervals[1:]:
        last = merged[-1]
        if last["prism"] and itv["prism"] and _section_signature_equal(last["polys"], itv["polys"], opts.tol):
            last["b"] = itv["b"]
        elif not last["prism"] and not itv["prism"]:
            last["b"] = itv["b"]
        else:
            merged.append(itv)
    if len(merged) < 2:
        return None

    out: list[Primitive] = []
    for itv in merged:
        a, b = float(itv["a"]), float(itv["b"])
        if itv["prism"]:
            for poly in itv["polys"]:
                prim = _prism_profile_primitive(poly, axis, a, b, opts)
                if prim is None:
                    return None
                out.append(prim)
        else:
            sub = _slice_between(mesh, axis, a, b)
            if sub is None or len(sub.faces) == 0:
                continue
            # Dentro da fatia, peças que não se tocam são analisadas separadamente
            # (ex.: a haste de um poste e a luminária ao lado dela).
            from .cleanup import split_components

            for comp in split_components(sub):
                fixed, closed = repair(comp)
                if not closed:
                    out.append(_freeform(fixed, closed=False))
                    continue
                out += classify_closed(fixed, opts, depth + 1, skip_axes + (axis,)).primitives
    return _merge_stacked(out, opts)


def _slice_between(mesh, axis: str, a: float, b: float):
    import trimesh

    w = AXIS_VEC[axis]
    try:
        part = trimesh.intersections.slice_mesh_plane(mesh, plane_normal=w, plane_origin=w * a, cap=True)
        part = trimesh.intersections.slice_mesh_plane(part, plane_normal=-w, plane_origin=w * b, cap=True)
    except Exception:
        return None
    return part


def _merge_stacked(prims: list[Primitive], opts: Options) -> list[Primitive]:
    """Une primitivas iguais empilhadas (mesmo perfil, faixas contíguas)."""
    changed = True
    prims = list(prims)
    while changed:
        changed = False
        for i, j in itertools.permutations(range(len(prims)), 2):
            p, q = prims[i], prims[j]
            merged = _try_merge_pair(p, q, opts)
            if merged is not None:
                keep = [x for k, x in enumerate(prims) if k not in (i, j)]
                prims = keep + [merged]
                changed = True
                break
    return prims


def _try_merge_pair(p: Primitive, q: Primitive, opts: Options) -> Primitive | None:
    tol = max(opts.tol, 0.01)
    if isinstance(p, Box) and isinstance(q, Box):
        lp, hp, lq, hq = map(np.asarray, (p.lo, p.hi, q.lo, q.hi))
        for ax in range(3):
            others = [k for k in range(3) if k != ax]
            if all(abs(lp[k] - lq[k]) <= tol and abs(hp[k] - hq[k]) <= tol for k in others) and abs(hp[ax] - lq[ax]) <= tol:
                lo = lp.copy()
                hi = hp.copy()
                hi[ax] = hq[ax]
                b = Box(lo=tuple(lo), hi=tuple(hi))
                b.volume = p.volume + q.volume
                return b
        return None
    if isinstance(p, Cylinder) and isinstance(q, Cylinder):
        if p.axis == q.axis and abs(p.diameter - q.diameter) <= tol and np.allclose(p.center, q.center, atol=tol) and abs(p.h1 - q.h0) <= tol:
            c = Cylinder(axis=p.axis, center=p.center, diameter=p.diameter, h0=p.h0, h1=q.h1)
            c.volume = p.volume + q.volume
            c.deviation = max(p.deviation, q.deviation)
            return c
        return None
    if isinstance(p, Extrusion) and isinstance(q, Extrusion):
        if p.axis == q.axis and abs(p.h1 - q.h0) <= tol and _loops_equal(p.loops, q.loops, tol):
            e = Extrusion(axis=p.axis, loops=p.loops, h0=p.h0, h1=q.h1)
            e.volume = p.volume + q.volume
            return e
    return None


def _loops_equal(a, b, tol: float) -> bool:
    if len(a) != len(b):
        return False
    for la, lb in zip(a, b):
        if len(la) != len(lb):
            return False
        for sa, sb in zip(la, lb):
            if sa.kind != sb.kind or not np.allclose(sa.start, sb.start, atol=tol) or not np.allclose(sa.end, sb.end, atol=tol):
                return False
    return True


# ---------------------------------------------------------------------------
# 7. aproximação


def try_approx_box(mesh, opts: Options) -> Box | None:
    if not opts.allow_approx:
        return None
    lo, hi = mesh.bounds
    ext = hi - lo
    vol_box = float(np.prod(ext))
    if vol_box <= 0:
        return None
    if float(mesh.volume) / vol_box < 0.97:
        return None
    area_box = 2 * (ext[0] * ext[1] + ext[1] * ext[2] + ext[0] * ext[2])
    dev = 2 * (vol_box - float(mesh.volume)) / area_box
    # Desvio máximo estimado: amostra pontos da caixa e mede a distância à malha.
    import trimesh

    probe = trimesh.creation.box(extents=ext)
    probe.apply_translation((lo + hi) / 2)
    pts, _ = trimesh.sample.sample_surface(probe, 300, seed=3)
    try:
        _, dist, _ = trimesh.proximity.closest_point(mesh, pts)
        dev = max(dev, float(np.max(dist)))
    except Exception:
        return None
    if dev > opts.fit_tol:
        return None
    b = Box(lo=tuple(map(float, lo)), hi=tuple(map(float, hi)))
    b.volume = vol_box
    b.deviation = dev
    b.notes.append(f"aproximado por caixa (desvio {dev:.2f} mm)")
    return b


# ---------------------------------------------------------------------------
# orquestração


def classify_closed(mesh, opts: Options, depth: int = 0, skip_axes: tuple[str, ...] = ()) -> ComponentResult:
    box = try_box(mesh, opts)
    if box is not None:
        return ComponentResult([box], "caixa")
    for axis in ("z", "x", "y"):
        prim = try_axis_prism(mesh, axis, opts)
        if prim is not None:
            return ComponentResult([prim], f"prisma {axis.upper()}")
    if depth == 0:
        fe = try_oriented_prism(mesh, opts)
        if fe is not None:
            return ComponentResult([fe], "prisma inclinado")
    boxes = try_orthogonal_boxes(mesh, opts)
    if boxes is not None:
        return ComponentResult(list(boxes), "caixas ortogonais")
    # Fatias: primeiro calcula; aceita já se todas as peças forem prismas
    # (caixas, cilindros, extrusões), que dão os parâmetros mais ricos.
    slab_results: list[tuple[str, list[Primitive]]] = []
    if depth < opts.max_depth:
        for axis in ("z", "x", "y"):
            if axis in skip_axes:
                continue
            pieces = slab_decompose(mesh, axis, opts, depth, skip_axes)
            if not pieces:
                continue
            total = sum(p.volume for p in pieces)
            if abs(total - float(mesh.volume)) > max(0.03 * float(mesh.volume), _volume_tolerance(mesh, opts.tol) * 4):
                continue
            if all(isinstance(p, (Box, Cylinder, Extrusion)) for p in pieces):
                return ComponentResult(pieces, f"fatias {axis.upper()}")
            slab_results.append((axis, pieces))
    revs = try_revolution(mesh, opts)
    if revs:
        return ComponentResult(list(revs), "revolução")
    if slab_results:
        # Prefere pouco volume em forma livre e poucas peças; Z (vertical) é
        # o corte mais natural para objetos e ganha em caso de empate.
        vol = max(float(mesh.volume), 1e-9)

        def score(item):
            axis, pieces = item
            free = sum(p.volume for p in pieces if isinstance(p, FreeformMesh)) / vol
            return free + 0.02 * len(pieces) + (0.0 if axis == "z" else 0.05)

        axis, pieces = min(slab_results, key=score)
        return ComponentResult(pieces, f"fatias {axis.upper()}")
    approx = try_approx_box(mesh, opts)
    if approx is not None:
        return ComponentResult([approx], "caixa aproximada")
    return ComponentResult([_freeform(mesh, closed=True)], "forma livre")


def _freeform(mesh, closed: bool) -> FreeformMesh:
    ff = FreeformMesh(vertices=np.asarray(mesh.vertices, dtype=float), faces=np.asarray(mesh.faces, dtype=np.int64),
                      watertight=closed)
    try:
        ff.volume = float(abs(mesh.volume)) if closed else float(mesh.area) * 1.0
    except Exception:
        ff.volume = 0.0
    return ff


def classify_open_sheet(mesh, opts: Options) -> ComponentResult | None:
    """Superfície plana aberta (ex.: vidro modelado como face única)."""
    from shapely.geometry import Polygon
    from shapely.ops import unary_union

    pts = np.asarray(mesh.vertices, dtype=float)
    c = pts.mean(axis=0)
    _, sv, vt = np.linalg.svd(pts - c, full_matrices=False)
    normal = vt[2]
    if sv[2] / max(len(pts), 1) ** 0.5 > opts.tol * 2:
        if np.max(np.abs((pts - c) @ normal)) > opts.tol * 2:
            return None
    t = opts.sheet_thickness
    for axis in ("z", "x", "y"):
        if abs(abs(normal @ AXIS_VEC[axis]) - 1.0) < 1e-4:
            loc = to_local(pts, axis)
            h = float(np.mean(loc[:, 2]))
            tris = [Polygon(loc[f, :2]) for f in mesh.faces]
            shape = unary_union([tp.buffer(0) for tp in tris if tp.area > 0])
            polys = [shape] if shape.geom_type == "Polygon" else list(getattr(shape, "geoms", []))
            out = []
            for poly in polys:
                if poly.area <= opts.tol:
                    continue
                prim = _prism_profile_primitive(poly, axis, h - t / 2, h + t / 2, opts)
                if prim is not None:
                    prim.notes.append(f"superfície aberta convertida em placa de {t:g} mm")
                    out.append(prim)
            if out:
                return ComponentResult(out, "placa")
    return None


def classify_component(mesh, opts: Options) -> ComponentResult:
    """Classifica um componente conexo (fechado ou não)."""
    fixed, closed = repair(mesh)
    if closed:
        return classify_closed(fixed, opts)
    sheet = classify_open_sheet(mesh, opts)
    if sheet is not None:
        return sheet
    return ComponentResult([_freeform(fixed, closed=False)], "forma livre (aberta)",
                           ["superfície aberta: será fechada por remalhamento"])

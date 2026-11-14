"""Perfis 2D: cortes da malha, simplificação e reconhecimento de retas e arcos.

Os laços gerados respeitam as exigências do Revit: segmentos conectados com
coordenadas idênticas, nenhum segmento menor que 1 mm e arcos com no máximo
180° (círculos completos viram dois arcos).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from .primitives import Loop, Segment

MIN_SEGMENT = 1.0  # mm (o Revit rejeita curvas menores que ~0,78 mm)

# Matrizes global -> local (u, v, w) para cada eixo, coerentes com o spec:
#   eixo z: (u, v) = (x, y); eixo x: (u, v) = (y, z); eixo y: (u, v) = (x, z).
_G2L = {
    "z": np.array([[1, 0, 0], [0, 1, 0], [0, 0, 1]], dtype=float),
    "x": np.array([[0, 1, 0], [0, 0, 1], [1, 0, 0]], dtype=float),
    "y": np.array([[1, 0, 0], [0, 0, 1], [0, 1, 0]], dtype=float),
}
AXIS_VEC = {"x": np.array([1.0, 0, 0]), "y": np.array([0, 1.0, 0]), "z": np.array([0, 0, 1.0])}


def to_local(points: np.ndarray, axis: str) -> np.ndarray:
    """Converte pontos 3D para (u, v, w) do eixo."""
    return points @ _G2L[axis].T


def section_polygons(mesh, axis: str, h: float) -> list:
    """Polígonos (shapely, com furos) do corte da malha no plano eixo = h."""
    origin = AXIS_VEC[axis] * h
    try:
        path = mesh.section(plane_origin=origin, plane_normal=AXIS_VEC[axis])
    except Exception:
        return []
    if path is None:
        return []
    M = np.eye(4)
    M[:3, :3] = _G2L[axis]
    M[2, 3] = -h
    try:
        p2d, _ = path.to_2D(to_2D=M, check=False)
        polys = list(p2d.polygons_full)
    except Exception:
        return []
    return [p for p in polys if p is not None and not p.is_empty and p.area > 0]


def cross2(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Produto vetorial 2D (componente z), aceitando vetores ou matrizes (n, 2)."""
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    return a[..., 0] * b[..., 1] - a[..., 1] * b[..., 0]


def _turn_angles(pts: np.ndarray) -> np.ndarray:
    prev = pts - np.roll(pts, 1, axis=0)
    nxt = np.roll(pts, -1, axis=0) - pts
    a1 = np.arctan2(prev[:, 1], prev[:, 0])
    a2 = np.arctan2(nxt[:, 1], nxt[:, 0])
    d = a2 - a1
    return (d + np.pi) % (2 * np.pi) - np.pi


def fit_circle(pts: np.ndarray) -> tuple[np.ndarray, float, float]:
    """Ajuste algébrico (Kåsa). Retorna (centro, raio, resíduo máximo)."""
    x, y = pts[:, 0], pts[:, 1]
    A = np.column_stack([x, y, np.ones_like(x)])
    b = x * x + y * y
    sol, *_ = np.linalg.lstsq(A, b, rcond=None)
    cx, cy = sol[0] / 2, sol[1] / 2
    r2 = sol[2] + cx * cx + cy * cy
    if r2 <= 0:
        return np.array([cx, cy]), 0.0, math.inf
    r = math.sqrt(r2)
    res = np.abs(np.hypot(x - cx, y - cy) - r)
    return np.array([cx, cy]), r, float(res.max())


def _dedupe(pts: np.ndarray, eps: float = 1e-6) -> np.ndarray:
    if len(pts) > 1 and np.linalg.norm(pts[0] - pts[-1]) < eps:
        pts = pts[:-1]
    keep = [0]
    for i in range(1, len(pts)):
        if np.linalg.norm(pts[i] - pts[keep[-1]]) > eps:
            keep.append(i)
    return pts[keep]


def _remove_collinear(pts: np.ndarray, eps: float) -> np.ndarray:
    """Remove vértices colineares de um anel fechado (ex.: pontos no meio de
    cordas criados quando o corte atravessa a diagonal de um quadrilátero)."""
    for _ in range(len(pts)):
        n = len(pts)
        if n <= 3:
            return pts
        prev = np.roll(pts, 1, axis=0)
        nxt = np.roll(pts, -1, axis=0)
        base = nxt - prev
        L = np.linalg.norm(base, axis=1)
        d = np.abs(cross2(base, pts - prev)) / np.where(L > 1e-12, L, 1.0)
        # Só remove se o ponto está entre os vizinhos (não é um "bico" de volta).
        between = np.einsum("ij,ij->i", pts - prev, nxt - pts) > 0
        cand = (d < eps) & between
        if not cand.any():
            return pts
        # Remove no máximo vértices alternados por rodada (vizinhos se protegem).
        idx = np.nonzero(cand)[0]
        keep_remove = []
        last = -10
        for i in idx:
            if i != last + 1:
                keep_remove.append(i)
                last = i
        if idx[-1] == n - 1 and 0 in keep_remove and n - 1 in keep_remove:
            keep_remove.remove(n - 1)
        mask = np.ones(n, dtype=bool)
        mask[keep_remove] = False
        pts = pts[mask]
    return pts


def _simplify_polyline(pts: np.ndarray, tol: float) -> list[int]:
    """Douglas-Peucker em polilinha aberta; retorna índices mantidos."""
    n = len(pts)
    if n <= 2:
        return list(range(n))
    keep = np.zeros(n, dtype=bool)
    keep[0] = keep[-1] = True
    stack = [(0, n - 1)]
    while stack:
        i, j = stack.pop()
        if j <= i + 1:
            continue
        a, b = pts[i], pts[j]
        ab = b - a
        L = np.linalg.norm(ab)
        seg = pts[i + 1:j]
        if L < 1e-12:
            d = np.linalg.norm(seg - a, axis=1)
        else:
            d = np.abs(cross2(ab, seg - a)) / L
        k = int(np.argmax(d))
        if d[k] > tol:
            m = i + 1 + k
            keep[m] = True
            stack.append((i, m))
            stack.append((m, j))
    return list(np.nonzero(keep)[0])


@dataclass
class RingInfo:
    segments: Loop
    is_circle: bool = False
    circle_center: tuple[float, float] | None = None
    circle_radius: float | None = None


def _arc_segment(p0: np.ndarray, p1: np.ndarray, center: np.ndarray, r: float, ccw: bool) -> list[Segment]:
    """Arco de p0 a p1 no círculo dado; divide se passar de 180°."""
    a0 = math.atan2(p0[1] - center[1], p0[0] - center[0])
    a1 = math.atan2(p1[1] - center[1], p1[0] - center[0])
    sweep = (a1 - a0) % (2 * math.pi) if ccw else -((a0 - a1) % (2 * math.pi))
    if abs(sweep) < 1e-9:
        sweep = 2 * math.pi if ccw else -2 * math.pi
    pieces = 1 if abs(sweep) <= math.pi + 1e-9 else 2
    out = []
    start = p0
    for k in range(pieces):
        a_end = a0 + sweep * (k + 1) / pieces
        a_mid = a0 + sweep * (k + 0.5) / pieces
        end = p1 if k == pieces - 1 else center + r * np.array([math.cos(a_end), math.sin(a_end)])
        mid = center + r * np.array([math.cos(a_mid), math.sin(a_mid)])
        out.append(Segment("arc", (float(start[0]), float(start[1])), (float(end[0]), float(end[1])),
                           mid=(float(mid[0]), float(mid[1])), center=(float(center[0]), float(center[1])), radius=float(r)))
        start = end
    return out


def ring_to_segments(coords, tol: float, min_len: float = MIN_SEGMENT) -> RingInfo | None:
    """Converte um anel fechado em segmentos (retas e arcos).

    Arcos são reconhecidos por crescimento guloso: a partir de cada vértice, o
    arco cresce enquanto os pontos couberem num círculo (dentro de ``tol``),
    sem cantos vivos e sem inverter o sentido da curva.
    """
    pts = _remove_collinear(_dedupe(np.asarray(coords, dtype=float)[:, :2]), max(tol * 0.02, 1e-5))
    n = len(pts)
    if n < 3:
        return None
    turns = _turn_angles(pts)
    max_smooth = math.radians(40)

    # Círculo completo.
    if n >= 8 and (np.abs(turns) < max_smooth).all() and (np.sign(turns) == np.sign(turns[0])).all():
        c, r, res = fit_circle(pts)
        chords = np.linalg.norm(np.roll(pts, -1, axis=0) - pts, axis=1)
        if r > 0 and res <= max(tol, min(0.003 * r, 4 * tol)) and chords.max() <= 4.0 * chords.min():
            p0 = c + np.array([r, 0.0])
            p1 = c + np.array([-r, 0.0])
            ccw = bool(turns[0] > 0)
            segs = _arc_segment(p0, p1, c, r, ccw) + _arc_segment(p1, p0, c, r, ccw)
            return RingInfo(segs, True, (float(c[0]), float(c[1])), float(r))

    # Começa num canto vivo ou no início da aresta mais longa (provável reta),
    # para não cortar um arco ao meio.
    big = np.nonzero(np.abs(turns) >= max_smooth)[0]
    if len(big):
        start = int(big[0])
    else:
        edge_len = np.linalg.norm(np.roll(pts, -1, axis=0) - pts, axis=1)
        start = int(np.argmax(edge_len))
    pts = np.roll(pts, -start, axis=0)
    turns = np.roll(turns, -start)
    P = np.vstack([pts, pts[:1]])  # n + 1 pontos; o último repete o primeiro
    T = np.append(turns, turns[0])

    def arc_fit(i: int, j: int):
        inner = T[i + 1:j]
        if len(inner) < 2:
            return None
        if (np.abs(inner) < math.radians(0.2)).any() or (np.abs(inner) >= max_smooth).any():
            return None
        if not (np.sign(inner) == np.sign(inner[0])).all():
            return None
        chords = np.linalg.norm(np.diff(P[i:j + 1], axis=0), axis=1)
        # Tesselação de arco tem cordas de tamanho parecido.
        if chords.min() <= 0 or chords.max() > 4.0 * chords.min():
            return None
        c, r, res = fit_circle(P[i:j + 1])
        if not (0 < r < 1e6) or res > max(tol, min(0.003 * r, 4 * tol)):
            return None
        # Cada corda deve cobrir um ângulo pequeno do círculo ajustado.
        if (chords / (2 * r)).max() > math.sin(max_smooth / 2):
            return None
        return c, r, bool(inner[0] > 0)

    segments: Loop = []
    i = 0
    while i < n:
        fit = arc_fit(i, i + 3) if i + 3 <= n else None
        if fit is None:
            segments.append(Segment("line", (float(P[i][0]), float(P[i][1])), (float(P[i + 1][0]), float(P[i + 1][1]))))
            i += 1
            continue
        j_ok, best = i + 3, fit
        step = 1
        while True:
            j_try = min(n, j_ok + step)
            if j_try == j_ok:
                break
            f = arc_fit(i, j_try)
            if f is not None:
                j_ok, best = j_try, f
                step *= 2
                continue
            lo, hi = j_ok + 1, j_try - 1
            while lo <= hi:
                mid = (lo + hi) // 2
                f = arc_fit(i, mid)
                if f is not None:
                    j_ok, best = mid, f
                    lo = mid + 1
                else:
                    hi = mid - 1
            break
        c, r, ccw = best
        a0 = math.atan2(P[i][1] - c[1], P[i][0] - c[0])
        a1 = math.atan2(P[j_ok][1] - c[1], P[j_ok][0] - c[0])
        sweep = abs((a1 - a0 + math.pi) % (2 * math.pi) - math.pi)
        if sweep < math.radians(8) and j_ok - i < 6:
            segments.append(Segment("line", (float(P[i][0]), float(P[i][1])), (float(P[i + 1][0]), float(P[i + 1][1]))))
            i += 1
            continue
        segments += _arc_segment(P[i], P[j_ok], c, r, ccw)
        i = j_ok
    segments = _merge_collinear(segments, max(tol * 0.5, 1e-4))
    segments = _enforce_min_length(segments, min_len)
    if len(segments) < 2:
        return None
    return RingInfo(segments)


def _merge_collinear(segments: Loop, tol: float) -> Loop:
    """Funde retas consecutivas colineares (dentro de ``tol``)."""
    if len(segments) < 3:
        return segments
    out: Loop = []
    run: list[tuple[float, float]] = []

    def flush():
        if len(run) >= 2:
            arr = np.asarray(run)
            keep = _simplify_polyline(arr, tol)
            for a, b in zip(keep, keep[1:]):
                out.append(Segment("line", tuple(map(float, arr[a])), tuple(map(float, arr[b]))))

    for s in segments:
        if s.kind == "line":
            if not run:
                run.append(s.start)
            run.append(s.end)
        else:
            flush()
            run = []
            out.append(s)
    flush()
    # A primeira e a última reta podem ser colineares através do início do anel.
    if len(out) >= 3 and out[0].kind == "line" and out[-1].kind == "line":
        a, b, c = np.asarray(out[-1].start), np.asarray(out[0].start), np.asarray(out[0].end)
        L = np.linalg.norm(c - a)
        if L > 1e-9 and abs(float(cross2(c - a, b - a))) / L <= tol and np.dot(b - a, c - b) > 0:
            merged = Segment("line", out[-1].start, out[0].end)
            out = [merged] + out[1:-1]
    return out


def _lines_from_run(pts: np.ndarray, run: list[int], tol: float) -> Loop:
    if len(run) < 2:
        return []
    run_pts = pts[run]
    keep = _simplify_polyline(run_pts, max(tol * 0.5, 1e-4))
    out = []
    for a, b in zip(keep, keep[1:]):
        p, q = run_pts[a], run_pts[b]
        out.append(Segment("line", (float(p[0]), float(p[1])), (float(q[0]), float(q[1]))))
    return out


def _enforce_min_length(segments: Loop, min_len: float) -> Loop:
    """Remove segmentos menores que ``min_len`` fundindo seus extremos."""
    segs = list(segments)
    changed = True
    guard = 0
    while changed and len(segs) > 3 and guard < 10000:
        changed = False
        guard += 1
        for i, s in enumerate(segs):
            if s.length() >= min_len:
                continue
            n = len(segs)
            prev = segs[(i - 1) % n]
            nxt = segs[(i + 1) % n]
            m = ((s.start[0] + s.end[0]) / 2, (s.start[1] + s.end[1]) / 2)
            segs[(i - 1) % n] = _with_end(prev, m)
            segs[(i + 1) % n] = _with_start(nxt, m)
            del segs[i]
            changed = True
            break
    # Arcos que ficaram quase retos viram retas.
    out = []
    for s in segs:
        if s.kind == "arc" and _arc_is_flat(s):
            out.append(Segment("line", s.start, s.end))
        else:
            out.append(s)
    return out


def _arc_is_flat(s: Segment) -> bool:
    a = np.asarray(s.start)
    b = np.asarray(s.end)
    m = np.asarray(s.mid)
    ab = b - a
    L = np.linalg.norm(ab)
    if L < 1e-9:
        return False
    return abs(float(cross2(ab, m - a))) / L < 0.02


def _with_end(s: Segment, p: tuple[float, float]) -> Segment:
    return Segment(s.kind, s.start, p, s.mid, s.center, s.radius)


def _with_start(s: Segment, p: tuple[float, float]) -> Segment:
    return Segment(s.kind, p, s.end, s.mid, s.center, s.radius)


def polygon_to_loops(poly, tol: float) -> tuple[list[Loop], list[RingInfo]] | None:
    """Polígono shapely -> laços (externo primeiro, depois furos)."""
    infos = []
    ext = ring_to_segments(np.asarray(poly.exterior.coords), tol)
    if ext is None:
        return None
    infos.append(ext)
    for interior in poly.interiors:
        info = ring_to_segments(np.asarray(interior.coords), tol)
        if info is not None and len(info.segments) >= 2:
            infos.append(info)
    return [i.segments for i in infos], infos


def axis_rectangle(poly, tol: float) -> tuple[float, float, float, float] | None:
    """(u0, v0, u1, v1) se o polígono é um retângulo alinhado aos eixos."""
    if len(poly.interiors) > 0:
        return None
    u0, v0, u1, v1 = poly.bounds
    if u1 - u0 <= tol or v1 - v0 <= tol:
        return None
    if abs(poly.area - (u1 - u0) * (v1 - v0)) > max(tol * (u1 - u0 + v1 - v0), 1e-6 * poly.area):
        return None
    return (u0, v0, u1, v1)


def loop_is_axis_rectangle(loop: Loop, tol: float) -> bool:
    if len(loop) != 4 or any(s.kind != "line" for s in loop):
        return False
    for s in loop:
        du = abs(s.end[0] - s.start[0])
        dv = abs(s.end[1] - s.start[1])
        if min(du, dv) > tol:
            return False
    return True

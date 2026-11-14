"""Unidades, eixo vertical e enquadramento no sistema da família."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

UNIT_TO_MM = {"mm": 1.0, "cm": 10.0, "dm": 100.0, "m": 1000.0, "in": 25.4, "ft": 304.8}
UNIT_LABEL = {"mm": "milímetros", "cm": "centímetros", "dm": "decímetros", "m": "metros", "in": "polegadas", "ft": "pés"}

# Rotações que levam o eixo "para cima" da origem ao +Z do Revit.
UP_ROTATIONS = {
    "Z": np.eye(3),
    "-Z": np.array([[1, 0, 0], [0, -1, 0], [0, 0, -1]], dtype=float),
    # Y para cima -> Z para cima: (x, y, z) -> (x, -z, y). A frente (+Z no glTF)
    # passa a apontar para -Y, que é a frente das famílias do Revit.
    "Y": np.array([[1, 0, 0], [0, 0, -1], [0, 1, 0]], dtype=float),
    "-Y": np.array([[1, 0, 0], [0, 0, 1], [0, -1, 0]], dtype=float),
    "X": np.array([[0, 0, -1], [0, 1, 0], [1, 0, 0]], dtype=float),
    "-X": np.array([[0, 0, 1], [0, 1, 0], [-1, 0, 0]], dtype=float),
}


@dataclass
class UnitGuess:
    unit: str
    confidence: float  # 0..1
    reason: str


def guess_units(max_extent: float) -> UnitGuess:
    """Estima a unidade pelo tamanho do objeto (objetos de 3 cm a 30 m).

    Arquivos sem unidade declarada são ambíguos; o resultado aparece na prévia
    para o usuário confirmar.
    """
    if max_extent <= 0:
        return UnitGuess("mm", 0.0, "objeto sem dimensão")
    if max_extent < 25:
        return UnitGuess("m", 0.6, f"maior dimensão {max_extent:.3g} — provável metros")
    if max_extent < 250:
        return UnitGuess("cm", 0.5, f"maior dimensão {max_extent:.3g} — provável centímetros")
    if max_extent <= 60000:
        return UnitGuess("mm", 0.7, f"maior dimensão {max_extent:.3g} — provável milímetros")
    return UnitGuess("mm", 0.2, f"maior dimensão {max_extent:.3g} — muito grande; verifique a unidade")


def _support_score(vertices: np.ndarray, faces: np.ndarray, up: np.ndarray) -> float:
    """Quanto o objeto 'apoia' de forma estável com este eixo para cima."""
    h = vertices @ up
    lo, hi = h.min(), h.max()
    span = hi - lo
    if span <= 0:
        return 0.0
    tri = vertices[faces]
    normals = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    area2 = np.linalg.norm(normals, axis=1)
    valid = area2 > 0
    if not valid.any():
        return 0.0
    n = normals[valid] / area2[valid, None]
    a = area2[valid] / 2
    tri_h = (tri[valid] @ up).max(axis=1)
    bottom = (tri_h <= lo + 0.02 * span) & (n @ up < -0.9)
    contact_ratio = a[bottom].sum() / a.sum()
    # Estabilidade: o centro de massa (aprox. pelo centro da área) cai dentro
    # do contorno dos pontos de apoio?
    stable = 0.0
    base_pts = vertices[h <= lo + 0.02 * span]
    if len(base_pts) >= 3:
        try:
            from scipy.spatial import ConvexHull, Delaunay

            basis = np.linalg.svd(np.eye(3) - np.outer(up, up))[0][:, :2]
            p2 = base_pts @ basis
            if np.ptp(p2[:, 0]) > 0 and np.ptp(p2[:, 1]) > 0:
                hull = ConvexHull(p2)
                centroid = (tri[valid].mean(axis=1) * a[:, None]).sum(0) / a.sum()
                if Delaunay(p2[hull.vertices]).find_simplex(centroid @ basis) >= 0:
                    stable = 1.0
        except Exception:
            stable = 0.0
    return float(contact_ratio + 0.5 * stable)


def _slender_alignment(parts_vertices: list[np.ndarray], up: np.ndarray) -> float:
    """Fração do volume de peças esbeltas (pernas, hastes) alinhadas ao eixo."""
    total = 0.0
    aligned = 0.0
    for pv in parts_vertices:
        if len(pv) < 4:
            continue
        ext = pv.max(0) - pv.min(0)
        order = np.sort(ext)
        if order[1] <= 0 or order[2] < 3.0 * order[1]:
            continue
        vol = float(np.prod(np.maximum(ext, 1e-9)))
        total += vol
        long_axis = np.zeros(3)
        long_axis[int(np.argmax(ext))] = 1.0
        if abs(float(long_axis @ up)) > 0.9:
            aligned += vol
    return aligned / total if total > 0 else 0.0


def _roundness(vertices: np.ndarray, up: np.ndarray) -> float:
    """1 se o objeto tem seção 'redonda' (extensões iguais) perpendicular ao eixo.

    Vasos, postes, luminárias e colunas giram em torno do eixo vertical.
    """
    ext = vertices.max(0) - vertices.min(0)
    k = int(np.argmax(np.abs(up)))
    others = [ext[i] for i in range(3) if i != k]
    big = max(others)
    if big <= 0:
        return 0.0
    if abs(others[0] - others[1]) > 0.01 * big:
        return 0.0
    # Exige que não seja um cubo (todas as extensões iguais não indicam nada).
    return 0.0 if abs(ext[k] - big) <= 0.01 * big else 1.0


def guess_up_axis(vertices: np.ndarray, faces: np.ndarray, file_format: str,
                  parts_vertices: list[np.ndarray] | None = None) -> tuple[str, float, str]:
    """Escolhe entre Z e Y para cima. Retorna (eixo, confiança, motivo).

    Sinais: apoio estável na base, peças esbeltas na vertical (pernas, hastes)
    e a convenção do formato (OBJ costuma vir com Y para cima; STL e 3MF, Z).
    """
    if len(faces) == 0:
        return "Z", 0.0, "sem faces"
    prior = {"obj": {"Y": 0.3}, "stl": {"Z": 0.3}, "3mf": {"Z": 0.6}, "ply": {"Z": 0.1}}.get(file_format, {})
    scores = {}
    detail = []
    for axis in ("Z", "Y"):
        up = UP_ROTATIONS[axis].T @ np.array([0.0, 0.0, 1.0])  # eixo de origem que vira +Z
        support = _support_score(vertices, faces, up)
        slender = _slender_alignment(parts_vertices or [], up)
        round_ = _roundness(vertices, up)
        scores[axis] = 0.5 * support + 1.0 * slender + 0.4 * round_ + prior.get(axis, 0.0)
        detail.append(f"{axis}: apoio {support:.2f}, esbeltas {slender:.2f}, seção redonda {round_:.0f}")
    best = max(scores, key=scores.get)
    other = "Y" if best == "Z" else "Z"
    margin = scores[best] - scores[other]
    confidence = float(min(1.0, 0.5 + margin))
    return best, confidence, "; ".join(detail)


def read_dae_up_axis(path: str) -> str | None:
    try:
        import collada

        mesh = collada.Collada(path, ignore=[collada.common.DaeUnsupportedError, collada.common.DaeBrokenRefError])
        up = getattr(mesh.assetInfo, "upaxis", None)
        if up:
            up = str(up).upper()
            return {"Y_UP": "Y", "Z_UP": "Z", "X_UP": "X"}.get(up)
    except Exception:
        return None
    return None


def read_dae_unit(path: str) -> float | None:
    """Metros por unidade declarados no Collada (<unit meter=...>)."""
    try:
        import collada

        mesh = collada.Collada(path, ignore=[collada.common.DaeUnsupportedError, collada.common.DaeBrokenRefError])
        meter = getattr(mesh.assetInfo, "unitmeter", None)
        return float(meter) if meter else None
    except Exception:
        return None


def framing_offset(vertices: np.ndarray) -> np.ndarray:
    """Translação que centraliza o envelope em X/Y e apoia a base em Z = 0."""
    lo = vertices.min(axis=0)
    hi = vertices.max(axis=0)
    return np.array([-(lo[0] + hi[0]) / 2, -(lo[1] + hi[1]) / 2, -lo[2]])

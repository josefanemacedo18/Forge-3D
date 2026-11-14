"""Geometria a partir da silhueta (sem IA 3D).

- Revolução: objetos com simetria de rotação (vasos, postes, luminárias,
  colunas, banquetas, mesas redondas). O meio-perfil da silhueta gira em torno
  do eixo vertical.
- Extrusão: objetos planos ou vistos de frente (painéis, placas, recortes,
  grades, letreiros). A silhueta é extrudada na profundidade informada.

As malhas geradas passam pelo mesmo fluxo dos arquivos 3D (decomposição e
planejamento), que separa trechos cilíndricos (parâmetros de diâmetro) dos
trechos curvos.
"""

from __future__ import annotations

import numpy as np

from ..errors import EngineError
from .silhouette import Silhouette


def revolution_profile(sil: Silhouette, mm_per_px: float) -> np.ndarray:
    """Meio-perfil (r, h) em mm, da base ao topo, fechado no eixo."""
    from scipy.ndimage import median_filter

    x0, y0, x1, y1 = sil.bbox
    axis = sil.axis_x
    rows = range(y1 - 1, y0 - 1, -1)  # da base (y maior) ao topo
    radii = []
    hs = []
    for y in rows:
        xs = np.nonzero(sil.mask[y])[0]
        if len(xs) == 0:
            continue
        left = axis - xs.min()
        right = xs.max() + 1 - axis
        r = max(0.0, (left + right) / 2)
        radii.append(r)
        hs.append((y1 - 1 - y) + 0.5)
    if len(radii) < 3:
        raise EngineError("Silhueta muito pequena para gerar uma revolução.")
    radii = median_filter(np.asarray(radii), size=5, mode="nearest")
    hs = np.asarray(hs)
    # Base e topo exatos (alturas de 0 até a altura total).
    hs = (hs - hs.min()) / max(hs.max() - hs.min(), 1e-9) * (y1 - y0)
    r_mm = radii * mm_per_px
    h_mm = hs * mm_per_px
    pts = np.column_stack([r_mm, h_mm])
    # Simplifica mantendo a forma (tolerância de 0,4% da altura).
    from shapely.geometry import LineString

    line = LineString(pts).simplify(max(0.004 * h_mm.max(), 0.3), preserve_topology=False)
    prof = np.asarray(line.coords)
    prof = np.vstack([[0.0, prof[0, 1]], prof, [0.0, prof[-1, 1]]])
    # Remove raios minúsculos (ruído) junto ao eixo.
    prof[:, 0] = np.where(prof[:, 0] < 0.5, 0.0, prof[:, 0])
    return prof


def revolution_mesh(sil: Silhouette, mm_per_px: float, sections: int = 96):
    import trimesh

    prof = revolution_profile(sil, mm_per_px)
    mesh = trimesh.creation.revolve(prof, sections=sections)
    if mesh.volume < 0:
        mesh.invert()
    trimesh.repair.fix_normals(mesh)
    if not mesh.is_watertight:
        raise EngineError("Não foi possível fechar o sólido de revolução gerado pela silhueta.")
    return mesh


def silhouette_polygon(sil: Silhouette, mm_per_px: float):
    """Contorno da silhueta como polígono (mm), centrado no eixo de simetria."""
    from shapely.geometry import Polygon
    from shapely.ops import unary_union
    from skimage import measure

    padded = np.pad(sil.mask.astype(np.uint8), 1)
    contours = measure.find_contours(padded, 0.5)
    if not contours:
        raise EngineError("Não foi possível extrair o contorno da silhueta.")
    x0, y0, x1, y1 = sil.bbox
    polys = []
    for c in contours:
        if len(c) < 8:
            continue
        rows = c[:, 0] - 1
        cols = c[:, 1] - 1
        xy = np.column_stack([(cols - sil.axis_x) * mm_per_px, (y1 - rows) * mm_per_px])
        poly = Polygon(xy).buffer(0)
        if poly.area > 0:
            polys.append(poly)
    if not polys:
        raise EngineError("Contorno da silhueta inválido.")
    # Contornos internos são furos (regra par-ímpar via diferença simétrica).
    polys.sort(key=lambda p: -p.area)
    shape = polys[0]
    for p in polys[1:]:
        shape = shape.symmetric_difference(p)
    shape = unary_union([shape]).buffer(0)
    size = max(shape.bounds[2] - shape.bounds[0], shape.bounds[3] - shape.bounds[1])
    shape = shape.simplify(max(0.003 * size, 0.3), preserve_topology=True)
    if shape.geom_type != "Polygon":
        shape = max(shape.geoms, key=lambda g: g.area)
    from ..geometry.polygons import orthogonalize

    return orthogonalize(shape)


def extrusion_mesh(sil: Silhouette, mm_per_px: float, depth_mm: float):
    import trimesh

    if depth_mm <= 0:
        raise EngineError("A profundidade da extrusão precisa ser positiva.")
    poly = silhouette_polygon(sil, mm_per_px)
    mesh = trimesh.creation.extrude_polygon(poly, depth_mm)
    # Perfil no plano XZ (vista frontal), extrudado ao longo de Y:
    # (x, y, z) local -> (x, -z, y) global (rotação própria, sem espelhar).
    T = np.array([[1, 0, 0, 0], [0, 0, -1, depth_mm / 2], [0, 1, 0, 0], [0, 0, 0, 1]], dtype=float)
    mesh.apply_transform(T)
    if mesh.volume < 0:
        mesh.invert()
    return mesh

"""Silhueta do objeto em uma imagem (máscara do primeiro plano)."""

from __future__ import annotations

import os
from dataclasses import dataclass

import numpy as np

from ..errors import EngineError

MAX_SIDE = 1024


@dataclass
class Silhouette:
    mask: np.ndarray  # bool (H, W)
    bbox: tuple[int, int, int, int]  # x0, y0, x1, y1 (x1/y1 exclusivos)
    method: str
    symmetry: float  # IoU com o espelho em torno do melhor eixo vertical
    axis_x: float  # coluna (pixel) do eixo de simetria
    coverage: float  # fração da imagem ocupada pelo objeto

    @property
    def width_px(self) -> int:
        return self.bbox[2] - self.bbox[0]

    @property
    def height_px(self) -> int:
        return self.bbox[3] - self.bbox[1]


def load_image(path: str):
    from PIL import Image, ImageOps

    if not os.path.exists(path):
        raise EngineError(f"Imagem não encontrada: {path}")
    try:
        img = Image.open(path)
        img = ImageOps.exif_transpose(img)
    except Exception as exc:
        raise EngineError(f"Não foi possível abrir a imagem '{os.path.basename(path)}': {exc}") from exc
    img = img.convert("RGBA")
    scale = MAX_SIDE / max(img.size)
    if scale < 1:
        img = img.resize((max(1, int(img.width * scale)), max(1, int(img.height * scale))), Image.LANCZOS)
    return img


def _mask_from_alpha(rgba: np.ndarray) -> np.ndarray | None:
    alpha = rgba[:, :, 3]
    if (alpha < 128).mean() < 0.01:
        return None
    return alpha >= 128


def _mask_rembg(img) -> np.ndarray | None:
    try:
        from rembg import remove  # type: ignore
    except Exception:
        return None
    try:
        out = remove(img.convert("RGB"))
        arr = np.asarray(out)
        if arr.ndim == 3 and arr.shape[2] == 4:
            return arr[:, :, 3] >= 128
    except Exception:
        return None
    return None


def _mask_background_color(rgba: np.ndarray) -> np.ndarray:
    from scipy import ndimage
    from skimage.filters import threshold_otsu

    rgb = rgba[:, :, :3].astype(np.float32)
    h, w = rgb.shape[:2]
    border = np.concatenate([rgb[0, :], rgb[-1, :], rgb[:, 0], rgb[:, -1]])
    bg = np.median(border, axis=0)
    dist = np.linalg.norm(rgb - bg, axis=2)
    if dist.max() <= 1e-6:
        raise EngineError("A imagem parece não ter objeto (cor única).")
    try:
        thr = threshold_otsu(dist)
    except ValueError:
        thr = dist.max() / 2
    thr = max(thr, 18.0)  # ignora variações leves do fundo (compressão JPEG, sombras suaves)
    mask = dist > thr
    k = max(1, int(round(min(h, w) / 200)))
    mask = ndimage.binary_opening(mask, iterations=k)
    mask = ndimage.binary_closing(mask, iterations=k * 2)
    mask = ndimage.binary_fill_holes(mask)
    return mask


def _largest_component(mask: np.ndarray) -> np.ndarray:
    from scipy import ndimage

    labels, n = ndimage.label(mask)
    if n <= 1:
        return mask
    sizes = ndimage.sum(mask, labels, range(1, n + 1))
    keep = int(np.argmax(sizes)) + 1
    # Mantém também pedaços grandes (>= 15% do maior), como pernas separadas pela sombra.
    big = [i + 1 for i, s in enumerate(sizes) if s >= 0.15 * sizes[keep - 1]]
    return np.isin(labels, big)


def symmetry_score(mask: np.ndarray) -> tuple[float, float]:
    """Melhor eixo vertical de simetria (IoU máximo entre a máscara e seu espelho)."""
    ys, xs = np.nonzero(mask)
    if len(xs) == 0:
        return 0.0, 0.0
    x0, x1 = xs.min(), xs.max() + 1
    center = (x0 + x1) / 2
    best = (0.0, center)
    width = x1 - x0
    for dx in np.linspace(-0.08 * width, 0.08 * width, 17):
        axis = center + dx
        # Espelha em torno de 'axis': coluna c -> 2*axis - c - 1.
        cols = np.arange(mask.shape[1])
        src = np.round(2 * axis - cols - 1).astype(int)
        valid = (src >= 0) & (src < mask.shape[1])
        mirrored = np.zeros_like(mask)
        mirrored[:, valid] = mask[:, src[valid]]
        inter = np.logical_and(mask, mirrored).sum()
        union = np.logical_or(mask, mirrored).sum()
        iou = inter / union if union else 0.0
        if iou > best[0]:
            best = (float(iou), float(axis))
    return best


def border_uniformity(rgba: np.ndarray) -> float:
    """Fração dos pixels da borda com cor próxima da cor típica do fundo."""
    rgb = rgba[:, :, :3].astype(np.float32)
    border = np.concatenate([rgb[0, :], rgb[-1, :], rgb[:, 0], rgb[:, -1]])
    med = np.median(border, axis=0)
    return float((np.linalg.norm(border - med, axis=1) < 25).mean())


def extract(img, use_ai: bool = True) -> Silhouette:
    """Máscara do objeto.

    Ordem: transparência da imagem; fundo liso (cor de fundo — exato e
    determinístico, caso das fotos de catálogo); fundo complexo (IA, se
    instalada). A IA não é usada em fundo liso porque tende a preencher vãos
    (por exemplo, entre as pernas de uma cadeira).
    """
    rgba = np.asarray(img)
    mask = _mask_from_alpha(rgba)
    method = "transparência da imagem"
    if mask is None and border_uniformity(rgba) >= 0.9:
        mask = _mask_background_color(rgba)
        method = "cor de fundo"
        if not (0.002 < mask.mean() < 0.95):
            mask = None
    if mask is None and use_ai:
        mask = _mask_rembg(img)
        method = "remoção de fundo por IA"
    if mask is None:
        mask = _mask_background_color(rgba)
        method = "cor de fundo"
    mask = _largest_component(mask)
    if mask.sum() < 50:
        raise EngineError("Não encontrei um objeto na imagem. Use uma foto com fundo liso ou PNG com transparência.")
    ys, xs = np.nonzero(mask)
    bbox = (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)
    coverage = float(mask.mean())
    if bbox[0] == 0 and bbox[1] == 0 and bbox[2] == mask.shape[1] and bbox[3] == mask.shape[0] and coverage > 0.9:
        raise EngineError("O objeto ocupa a imagem inteira; não foi possível separar do fundo. Use uma foto com margem e fundo liso.")
    sym, axis = symmetry_score(mask)
    return Silhouette(mask=mask, bbox=bbox, method=method, symmetry=sym, axis_x=axis, coverage=coverage)


def save_preview(img, sil: Silhouette, path: str) -> str:
    """Imagem com a silhueta destacada (para a prévia no plugin)."""
    from PIL import Image

    base = np.asarray(img.convert("RGB")).astype(np.float32)
    overlay = base.copy()
    overlay[sil.mask] = overlay[sil.mask] * 0.45 + np.array([255, 140, 0]) * 0.55
    out = Image.fromarray(overlay.clip(0, 255).astype(np.uint8))
    out.save(path)
    return path

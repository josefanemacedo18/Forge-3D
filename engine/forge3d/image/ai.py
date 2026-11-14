"""Reconstrução 3D por IA a partir de uma imagem (TripoSR, offline).

O módulo é opcional: o instalador baixa o código do TripoSR (licença MIT) e
os pesos uma única vez. Depois disso tudo roda sem internet, na GPU NVIDIA
(perfis ``gpu`` e ``gpu-leve``) ou na CPU.

A dependência ``torchmcubes`` do TripoSR exige compilação C++/CUDA; aqui ela é
substituída por uma implementação equivalente com o marching cubes do
scikit-image, evitando a etapa de compilação no Windows.
"""

from __future__ import annotations

import os
import sys
import types
from pathlib import Path
from typing import Callable

import numpy as np

from ..errors import CancelledError, EngineError


def engine_root() -> Path:
    return Path(__file__).resolve().parents[2]


def models_dir() -> Path:
    env = os.environ.get("FORGE3D_MODELS")
    if env:
        return Path(env)
    local = os.environ.get("LOCALAPPDATA")
    if local:
        return Path(local) / "Forge3D" / "models"
    return engine_root() / "models"


def triposr_dirs() -> tuple[Path, Path]:
    """(pasta do código do TripoSR, pasta dos pesos)."""
    code = Path(os.environ.get("FORGE3D_TRIPOSR_CODE", str(models_dir().parent / "vendor" / "TripoSR")))
    weights = Path(os.environ.get("FORGE3D_TRIPOSR_WEIGHTS", str(models_dir() / "TripoSR")))
    return code, weights


def _install_mcubes_shim() -> None:
    try:
        import torchmcubes  # noqa: F401
        return
    except ImportError:
        pass
    import torch
    from skimage.measure import marching_cubes as _mc

    mod = types.ModuleType("torchmcubes")

    def marching_cubes(vol, thresh):  # mesma assinatura do torchmcubes
        v = vol.detach().float().cpu().numpy()
        if not (v.min() <= float(thresh) <= v.max()):
            raise EngineError("A IA não gerou uma superfície para esta imagem.")
        verts, faces, _, _ = _mc(v, level=float(thresh))
        verts = np.ascontiguousarray(verts[:, ::-1])  # torchmcubes devolve (x, y, z) invertido
        return (torch.from_numpy(verts.astype(np.float32)).to(vol.device),
                torch.from_numpy(faces.astype(np.int64)).to(vol.device))

    mod.marching_cubes = marching_cubes
    sys.modules["torchmcubes"] = mod


def status() -> dict:
    """Disponibilidade do módulo de IA (sem carregar o modelo)."""
    code, weights = triposr_dirs()
    out = {"available": False, "reason": "", "code": str(code), "weights": str(weights)}
    try:
        import torch  # noqa: F401
    except ImportError:
        out["reason"] = "PyTorch não instalado (instale o módulo de IA pelo instalador)."
        return out
    if not (code / "tsr").is_dir():
        out["reason"] = f"Código do TripoSR não encontrado em {code}."
        return out
    if not (weights / "model.ckpt").is_file() or not (weights / "config.yaml").is_file():
        out["reason"] = f"Pesos do TripoSR não encontrados em {weights}."
        return out
    out["available"] = True
    return out


_MODEL_CACHE: dict[str, object] = {}


def _load_model(device: str, chunk: int):
    code, weights = triposr_dirs()
    if str(code) not in sys.path:
        sys.path.insert(0, str(code))
    _install_mcubes_shim()
    from tsr.system import TSR  # type: ignore

    key = f"{device}"
    model = _MODEL_CACHE.get(key)
    if model is None:
        model = TSR.from_pretrained(str(weights), config_name="config.yaml", weight_name="model.ckpt")
        model.to(device)
        _MODEL_CACHE.clear()
        _MODEL_CACHE[key] = model
    model.renderer.set_chunk_size(chunk)
    return model


def _prepare_image(img_rgba, mask: np.ndarray):
    from PIL import Image

    code, _ = triposr_dirs()
    if str(code) not in sys.path:
        sys.path.insert(0, str(code))
    from tsr.utils import resize_foreground  # type: ignore

    arr = np.asarray(img_rgba.convert("RGBA")).copy()
    arr[:, :, 3] = np.where(mask, 255, 0).astype(np.uint8)
    image = resize_foreground(Image.fromarray(arr, "RGBA"), 0.85)
    a = np.asarray(image).astype(np.float32) / 255.0
    rgb = a[:, :, :3] * a[:, :, 3:4] + (1 - a[:, :, 3:4]) * 0.5
    return Image.fromarray((rgb * 255.0).astype(np.uint8))


def reconstruct(img_rgba, mask: np.ndarray, profile: str, progress: Callable[[float, str], None] | None = None,
                cancel: Callable[[], bool] | None = None):
    """Malha (trimesh) reconstruída pela IA, em escala e orientação do modelo."""
    st = status()
    if not st["available"]:
        raise EngineError("Módulo de IA indisponível: " + st["reason"])
    import torch

    report = progress or (lambda f, m: None)
    plans = []
    if profile == "gpu" and torch.cuda.is_available():
        plans.append(("cuda", 8192, 256))
    elif profile == "gpu-leve" and torch.cuda.is_available():
        plans.append(("cuda", 1024, 160))
    plans.append(("cpu", 4096, 192))

    image = _prepare_image(img_rgba, mask)
    last_error: Exception | None = None
    for device, chunk, resolution in plans:
        if cancel and cancel():
            raise CancelledError()
        try:
            report(0.1, f"Carregando o modelo de IA ({'GPU' if device == 'cuda' else 'CPU'})")
            model = _load_model(device, chunk)
            report(0.3, "Reconstruindo o objeto em 3D")
            with torch.no_grad():
                scene_codes = model([image], device=device)
                if cancel and cancel():
                    raise CancelledError()
                report(0.7, "Extraindo a superfície")
                meshes = model.extract_mesh(scene_codes, True, resolution=resolution)
            mesh = meshes[0]
            if len(mesh.faces) == 0:
                raise EngineError("A IA não gerou geometria para esta imagem.")
            return mesh
        except CancelledError:
            raise
        except Exception as exc:  # falta de memória na GPU: tenta a próxima opção
            last_error = exc
            if device == "cuda":
                try:
                    torch.cuda.empty_cache()
                except Exception:
                    pass
                _MODEL_CACHE.clear()
                report(0.1, "Memória de vídeo insuficiente; continuando na CPU")
                continue
            break
    raise EngineError(f"Falha na reconstrução por IA: {last_error}")

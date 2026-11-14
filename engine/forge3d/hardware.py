"""Detecção de hardware e escolha do perfil de execução da IA.

Perfis:
- ``gpu``: GPU NVIDIA com 6 GB ou mais de VRAM;
- ``gpu-leve``: GPU NVIDIA com 3,5 a 6 GB (modelos menores, precisão reduzida,
  processamento em blocos menores);
- ``cpu``: sem GPU compatível (mais lento, mas funciona offline).
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
from dataclasses import asdict, dataclass, field


@dataclass
class GpuInfo:
    name: str
    vram_gb: float
    capability: str = ""
    supported_by_torch: bool | None = None
    source: str = ""


@dataclass
class HardwareInfo:
    os: str
    cpu_count: int
    ram_gb: float
    python: str
    torch: str | None = None
    cuda_available: bool = False
    gpus: list[GpuInfo] = field(default_factory=list)
    profile: str = "cpu"
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        return d


def _ram_gb() -> float:
    try:
        if os.name == "nt":
            import ctypes

            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("sullAvailExtendedVirtual", ctypes.c_ulonglong)]

            stat = MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
            return stat.ullTotalPhys / 1024 ** 3
        pages = os.sysconf("SC_PHYS_PAGES")
        size = os.sysconf("SC_PAGE_SIZE")
        return pages * size / 1024 ** 3
    except Exception:
        return 0.0


def _nvidia_smi() -> list[GpuInfo]:
    exe = shutil.which("nvidia-smi")
    if not exe:
        return []
    try:
        out = subprocess.run([exe, "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=10, check=False).stdout
    except Exception:
        return []
    gpus = []
    for line in out.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 2:
            try:
                gpus.append(GpuInfo(name=parts[0], vram_gb=float(parts[1]) / 1024, source="nvidia-smi"))
            except ValueError:
                continue
    return gpus


def detect() -> HardwareInfo:
    info = HardwareInfo(os=f"{platform.system()} {platform.release()}", cpu_count=os.cpu_count() or 1,
                        ram_gb=round(_ram_gb(), 1), python=platform.python_version())
    try:
        import torch  # noqa: F401

        info.torch = torch.__version__
        if torch.cuda.is_available():
            info.cuda_available = True
            arch_list = set(torch.cuda.get_arch_list() or [])
            for i in range(torch.cuda.device_count()):
                props = torch.cuda.get_device_properties(i)
                cap = f"{props.major}.{props.minor}"
                dev = props.major * 10 + props.minor
                supported: bool | None = None
                if arch_list:
                    sms = {int(a[3:]) for a in arch_list if a.startswith("sm_") and a[3:].isdigit()}
                    ptx = {int(a[8:]) for a in arch_list if a.startswith("compute_") and a[8:].isdigit()}
                    # Binário exato da arquitetura ou PTX de capacidade menor/igual (compilado na hora).
                    supported = dev in sms or any(p <= dev for p in ptx)
                info.gpus.append(GpuInfo(name=props.name, vram_gb=round(props.total_memory / 1024 ** 3, 2),
                                         capability=cap, supported_by_torch=supported, source="torch"))
        else:
            info.notes.append("PyTorch instalado, mas sem CUDA disponível.")
    except ImportError:
        info.notes.append("PyTorch não instalado: o módulo de IA usará o modo sem IA ou a CPU.")
    except Exception as exc:  # pragma: no cover - drivers variam
        info.notes.append(f"Falha ao consultar a GPU pelo PyTorch: {exc}")
    if not info.gpus:
        info.gpus = _nvidia_smi()
    usable = [g for g in info.gpus if g.supported_by_torch is not False]
    best = max((g.vram_gb for g in usable), default=0.0)
    if info.cuda_available and best >= 6:
        info.profile = "gpu"
    elif info.cuda_available and best >= 3.5:
        info.profile = "gpu-leve"
    else:
        info.profile = "cpu"
        if any(g.supported_by_torch is False for g in info.gpus):
            info.notes.append("A GPU encontrada não é suportada por esta versão do PyTorch; usando a CPU.")
    return info

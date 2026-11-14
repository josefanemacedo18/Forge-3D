"""Primitivas geométricas reconhecidas pelo motor, no sistema da família (mm).

Sistema da família: X = largura, Y = profundidade, Z = altura; origem no
centro do envelope em planta e na base (Z = 0).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

import numpy as np

Axis = Literal["x", "y", "z"]
PERP: dict[str, tuple[str, str]] = {"z": ("x", "y"), "x": ("y", "z"), "y": ("x", "z")}
AXIS_INDEX = {"x": 0, "y": 1, "z": 2}


@dataclass
class Segment:
    """Segmento de perfil 2D: reta (start, end) ou arco por 3 pontos."""

    kind: Literal["line", "arc"]
    start: tuple[float, float]
    end: tuple[float, float]
    mid: tuple[float, float] | None = None
    center: tuple[float, float] | None = None
    radius: float | None = None

    def to_spec(self) -> dict:
        d = {"type": self.kind, "start": [round(self.start[0], 4), round(self.start[1], 4)],
             "end": [round(self.end[0], 4), round(self.end[1], 4)]}
        if self.kind == "arc":
            d["mid"] = [round(self.mid[0], 4), round(self.mid[1], 4)]
            if self.center is not None:
                d["center"] = [round(self.center[0], 4), round(self.center[1], 4)]
            if self.radius is not None:
                d["radius"] = round(self.radius, 4)
        return d

    def length(self) -> float:
        if self.kind == "line":
            return float(np.hypot(self.end[0] - self.start[0], self.end[1] - self.start[1]))
        # Aproximação pela poligonal start-mid-end (suficiente para limites).
        a = np.hypot(self.mid[0] - self.start[0], self.mid[1] - self.start[1])
        b = np.hypot(self.end[0] - self.mid[0], self.end[1] - self.mid[1])
        return float(a + b)


Loop = list[Segment]


@dataclass
class PrimitiveBase:
    name: str = ""
    source: str = ""  # nome da parte no arquivo de origem
    color: tuple[int, int, int] = (180, 180, 180)
    material_name: str = ""
    volume: float = 0.0
    deviation: float = 0.0  # desvio máximo estimado em relação à malha original (mm)
    detail: bool = False  # peça pequena (mostrada só em médio/fino)
    is_void: bool = False  # sólido vazio (recorta os sólidos que atravessa)
    notes: list[str] = field(default_factory=list)

    kind: str = "base"

    def bounds(self) -> np.ndarray:  # (2, 3)
        raise NotImplementedError


@dataclass
class Box(PrimitiveBase):
    lo: tuple[float, float, float] = (0.0, 0.0, 0.0)
    hi: tuple[float, float, float] = (0.0, 0.0, 0.0)
    kind: str = "box"

    def bounds(self) -> np.ndarray:
        return np.array([self.lo, self.hi], dtype=float)

    @property
    def extents(self) -> np.ndarray:
        return np.asarray(self.hi) - np.asarray(self.lo)


@dataclass
class Cylinder(PrimitiveBase):
    axis: Axis = "z"
    center: tuple[float, float] = (0.0, 0.0)  # coordenadas (u, v) perpendiculares ao eixo
    diameter: float = 1.0
    h0: float = 0.0
    h1: float = 1.0
    kind: str = "cylinder"

    def bounds(self) -> np.ndarray:
        u, v = PERP[self.axis]
        lo = [0.0, 0.0, 0.0]
        hi = [0.0, 0.0, 0.0]
        r = self.diameter / 2
        lo[AXIS_INDEX[u]], hi[AXIS_INDEX[u]] = self.center[0] - r, self.center[0] + r
        lo[AXIS_INDEX[v]], hi[AXIS_INDEX[v]] = self.center[1] - r, self.center[1] + r
        lo[AXIS_INDEX[self.axis]], hi[AXIS_INDEX[self.axis]] = self.h0, self.h1
        return np.array([lo, hi], dtype=float)


@dataclass
class Extrusion(PrimitiveBase):
    """Extrusão alinhada a um eixo: perfil (u, v) entre h0 e h1 ao longo do eixo."""

    axis: Axis = "z"
    loops: list[Loop] = field(default_factory=list)  # o primeiro é o contorno externo
    h0: float = 0.0
    h1: float = 1.0
    kind: str = "extrusion"

    def bounds(self) -> np.ndarray:
        pts = np.array([p for lp in self.loops for s in lp for p in (s.start, s.end)], dtype=float)
        u, v = PERP[self.axis]
        lo = [0.0, 0.0, 0.0]
        hi = [0.0, 0.0, 0.0]
        lo[AXIS_INDEX[u]], hi[AXIS_INDEX[u]] = pts[:, 0].min(), pts[:, 0].max()
        lo[AXIS_INDEX[v]], hi[AXIS_INDEX[v]] = pts[:, 1].min(), pts[:, 1].max()
        lo[AXIS_INDEX[self.axis]], hi[AXIS_INDEX[self.axis]] = self.h0, self.h1
        return np.array([lo, hi], dtype=float)


@dataclass
class FramedExtrusion(PrimitiveBase):
    """Extrusão em plano arbitrário (geometria fixa, exata)."""

    origin: tuple[float, float, float] = (0.0, 0.0, 0.0)
    xdir: tuple[float, float, float] = (1.0, 0.0, 0.0)
    ydir: tuple[float, float, float] = (0.0, 1.0, 0.0)
    loops: list[Loop] = field(default_factory=list)
    depth: float = 1.0
    kind: str = "framed_extrusion"

    def bounds(self) -> np.ndarray:
        o = np.asarray(self.origin)
        x = np.asarray(self.xdir)
        y = np.asarray(self.ydir)
        z = np.cross(x, y)
        pts2 = np.array([p for lp in self.loops for s in lp for p in (s.start, s.end)], dtype=float)
        base = o + pts2[:, :1] * x + pts2[:, 1:2] * y
        allp = np.vstack([base, base + z * self.depth])
        return np.array([allp.min(0), allp.max(0)])


@dataclass
class Revolution(PrimitiveBase):
    """Sólido de revolução: perfil (r, h) girado em torno do eixo."""

    axis: Axis = "z"
    center: tuple[float, float] = (0.0, 0.0)
    profile: Loop = field(default_factory=list)  # coordenadas (r >= 0, h absoluto)
    h0: float = 0.0
    h1: float = 1.0
    flat_bottom: bool = False
    flat_top: bool = False
    kind: str = "revolution"

    def max_radius(self) -> float:
        return max(max(s.start[0], s.end[0], (s.mid or (0, 0))[0]) for s in self.profile)

    def bounds(self) -> np.ndarray:
        u, v = PERP[self.axis]
        r = self.max_radius()
        lo = [0.0, 0.0, 0.0]
        hi = [0.0, 0.0, 0.0]
        lo[AXIS_INDEX[u]], hi[AXIS_INDEX[u]] = self.center[0] - r, self.center[0] + r
        lo[AXIS_INDEX[v]], hi[AXIS_INDEX[v]] = self.center[1] - r, self.center[1] + r
        lo[AXIS_INDEX[self.axis]], hi[AXIS_INDEX[self.axis]] = self.h0, self.h1
        return np.array([lo, hi], dtype=float)


@dataclass
class FreeformMesh(PrimitiveBase):
    vertices: np.ndarray = field(default_factory=lambda: np.zeros((0, 3)))
    faces: np.ndarray = field(default_factory=lambda: np.zeros((0, 3), dtype=int))
    watertight: bool = False
    kind: str = "mesh"

    def bounds(self) -> np.ndarray:
        return np.array([self.vertices.min(0), self.vertices.max(0)])


Primitive = Box | Cylinder | Extrusion | FramedExtrusion | Revolution | FreeformMesh

PARAMETRIC_KINDS = {"box", "cylinder", "extrusion", "revolution"}

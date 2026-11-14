"""Papéis das peças e nomes em português para sólidos, planos e parâmetros.

Os papéis são deduzidos da geometria (posição, proporções e relação com o
envelope). Quando nenhum papel se aplica, usa-se um nome genérico. Todos os
nomes gerados são válidos no Revit e em fórmulas (sem operadores ou símbolos).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

import numpy as np

from ..geometry.primitives import Box, Cylinder, Extrusion, FramedExtrusion, FreeformMesh, Primitive, Revolution


@dataclass(frozen=True)
class Role:
    key: str
    noun: str  # "Tampo"
    of: str  # "do Tampo" (para compor nomes de parâmetros)


ROLES = {
    "tampo": Role("tampo", "Tampo", "do Tampo"),
    "base": Role("base", "Base", "da Base"),
    "assento": Role("assento", "Assento", "do Assento"),
    "prateleira": Role("prateleira", "Prateleira", "da Prateleira"),
    "perna": Role("perna", "Perna", "da Perna"),
    "montante": Role("montante", "Montante", "do Montante"),
    "haste": Role("haste", "Haste", "da Haste"),
    "travessa": Role("travessa", "Travessa", "da Travessa"),
    "lateral": Role("lateral", "Lateral", "da Lateral"),
    "fundo": Role("fundo", "Fundo", "do Fundo"),
    "frente": Role("frente", "Frente", "da Frente"),
    "divisoria": Role("divisoria", "Divisória", "da Divisória"),
    "encosto": Role("encosto", "Encosto", "do Encosto"),
    "torneada": Role("torneada", "Peça Torneada", "da Peça Torneada"),
    "livre": Role("livre", "Forma Livre", "da Forma Livre"),
    "furo": Role("furo", "Furo", "do Furo"),
    "peca": Role("peca", "Peça", "da Peça"),
}


def classify_roles(prims: list[Primitive], envelope: tuple[float, float, float]) -> list[Role]:
    W, D, H = envelope
    footprint = W * D
    roles: list[Role] = []
    bounds = [p.bounds() for p in prims]
    # Primeiro identifica placas horizontais e verticais e peças esbeltas.
    raw: list[str] = []
    for p, b in zip(prims, bounds):
        ext = b[1] - b[0]
        ex, ey, ez = ext
        lo, hi = b
        key = "peca"
        if getattr(p, "is_void", False):
            key = "furo"
        elif isinstance(p, FreeformMesh):
            key = "livre"
        elif isinstance(p, Revolution):
            key = "torneada"
        elif isinstance(p, Cylinder) and p.axis == "z" and ez >= 3 * max(ex, ey):
            key = "haste" if ez >= 0.5 * H and lo[2] > 0.02 * H else ("perna" if lo[2] <= 0.02 * H else "montante")
            if key == "haste" and lo[2] <= 0.02 * H:
                key = "perna" if ez < 0.9 * H else "haste"
        elif ez <= 0.25 * H and ex * ey >= 0.3 * footprint and ez < min(ex, ey):
            if hi[2] >= H - 0.02 * H:
                key = "tampo"
            elif lo[2] <= 0.02 * H:
                key = "base"
            else:
                key = "prateleira"
        elif ez >= 3 * max(ex, ey):
            key = "perna" if lo[2] <= 0.02 * H else "montante"
        elif lo[2] <= 0.02 * H and ez <= 0.25 * H and isinstance(p, (Box, Cylinder, Extrusion)) and ez < max(ex, ey):
            key = "base"
        elif (ex >= 3 * max(ey, ez)) or (ey >= 3 * max(ex, ez)):
            key = "travessa"
        elif ex <= 0.25 * W and ey >= 0.5 * D and ez >= 0.3 * H:
            key = "lateral" if (lo[0] <= -W / 2 + 0.05 * W or hi[0] >= W / 2 - 0.05 * W) else "divisoria"
        elif ey <= 0.25 * D and ex >= 0.5 * W and ez >= 0.3 * H:
            if hi[1] >= D / 2 - 0.05 * D:
                key = "fundo"
            elif lo[1] <= -D / 2 + 0.05 * D:
                key = "frente"
            else:
                key = "divisoria"
        raw.append(key)
    # Assento: prateleira na faixa de 35-65% da altura com um encosto acima.
    has_back = any(k == "fundo" and b[0][2] >= 0.3 * H for k, b in zip(raw, bounds))
    for i, (k, b) in enumerate(zip(raw, bounds)):
        if k == "prateleira" and has_back and 0.35 * H <= b[1][2] <= 0.65 * H:
            raw[i] = "assento"
    if any(k == "assento" for k in raw):
        for i, (k, b) in enumerate(zip(raw, bounds)):
            if k == "fundo" and b[0][2] >= 0.3 * H:
                raw[i] = "encosto"
    for k in raw:
        roles.append(ROLES[k])
    return roles


def solid_names(roles: list[Role]) -> list[str]:
    """Nomes dos sólidos: 'Tampo', 'Perna 1', 'Perna 2'..."""
    counts: dict[str, int] = {}
    for r in roles:
        counts[r.key] = counts.get(r.key, 0) + 1
    seen: dict[str, int] = {}
    out = []
    for r in roles:
        if counts[r.key] == 1:
            out.append(r.noun)
        else:
            seen[r.key] = seen.get(r.key, 0) + 1
            out.append(f"{r.noun} {seen[r.key]}")
    return out


_INVALID = re.compile(r"[^A-Za-zÀ-ÖØ-öø-ÿ0-9 _]")


def sanitize_name(text: str, fallback: str = "Peça", max_len: int = 40) -> str:
    """Nome seguro para o Revit e para fórmulas."""
    text = unicodedata.normalize("NFC", text or "")
    text = _INVALID.sub(" ", text)
    text = re.sub(r"\s+", " ", text).strip()
    text = text.lstrip("0123456789 _")
    if not text:
        return fallback
    return text[:max_len].rstrip(" _") or fallback


class NameRegistry:
    """Garante nomes únicos (sem diferenciar maiúsculas) acrescentando números."""

    def __init__(self, reserved: list[str] | None = None):
        self._used: set[str] = {n.casefold() for n in (reserved or [])}

    def take(self, base: str) -> str:
        base = sanitize_name(base, "Parâmetro", 55)
        if base.casefold() not in self._used:
            self._used.add(base.casefold())
            return base
        k = 2
        while f"{base} {k}".casefold() in self._used:
            k += 1
        name = f"{base} {k}"
        self._used.add(name.casefold())
        return name

    def has(self, name: str) -> bool:
        return name.casefold() in self._used


def material_display_name(material_name: str, color: tuple[int, int, int]) -> str:
    clean = sanitize_name(material_name, "", 40)
    generic = {"", "material", "default", "defaultmaterial", "none", "padrao", "padrão"}
    if clean.casefold().replace(" ", "") in generic or re.fullmatch(r"(material|mat)\s*\d*", clean.casefold()):
        return f"Cor {color[0]:02X}{color[1]:02X}{color[2]:02X}"
    return clean


def envelope_kind_axis_label(axis: str) -> str:
    return {"x": "X", "y": "Y", "z": "Z"}[axis]


def is_slender(p: Primitive) -> bool:
    b = p.bounds()
    ext = np.sort(b[1] - b[0])
    return ext[2] >= 3 * ext[1]


__all__ = [
    "Role", "ROLES", "classify_roles", "solid_names", "sanitize_name", "NameRegistry",
    "material_display_name", "is_slender", "Box", "Cylinder", "Extrusion", "FramedExtrusion",
]

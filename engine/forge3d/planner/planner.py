"""Planejador de restrições: primitivas -> FamilySpec.

Cada eixo (X, Y, Z) recebe uma árvore de planos de referência. Cada plano tem
exatamente uma cota até o plano pai, rotulada por um parâmetro — a mesma
estrutura que o construtor cria no Revit (planos, cotas rotuladas, fórmulas).

Como cada plano é controlado no Nível C:
- envelope: Largura / Profundidade / Altura (ou Diâmetro, se a planta é redonda);
- espessura: peças finas mantêm a espessura constante (Espessura do Tampo...);
- recuo: faces perto de um plano já resolvido mantêm a distância (Recuo X...);
- altura própria: topo de placas horizontais (Altura do Assento...);
- proporcional: o restante acompanha o envelope (aux X01 = Largura * n / d).

No Nível B tudo é proporcional ao envelope. No Nível A a geometria é fixa.
"""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from ..geometry.decompose import Decomposition
from ..geometry.polygons import _arc_segment
from ..geometry.primitives import (
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
from ..spec import formula as fx
from .naming import ROLES, NameRegistry, Role, classify_roles, material_display_name, sanitize_name, solid_names

MERGE_TOL = 1.0  # mm — coordenadas mais próximas viram um único plano
THIN_RATIO = 0.25
INSET_RATIO = 0.15
ROOT_ID = {"x": "@x0", "y": "@y0", "z": "@z0"}
AXIS_LABEL = {"x": "X", "y": "Y", "z": "Z"}

ENGINE_VERSION = "0.1.0"


# ---------------------------------------------------------------------------
# estruturas internas


@dataclass
class Ref:
    axis: str
    value: float
    prim: int
    role: str  # lo, hi, center, seg
    is_plane: bool


@dataclass
class Lvl:
    axis: str
    value: float
    refs: list[Ref] = field(default_factory=list)
    root: bool = False
    env: str | None = None  # "min" | "max"
    parent: "Lvl | None" = None
    driver: str | None = None
    kind: str = ""
    plane_id: str | None = None
    original: float = 0.0

    @property
    def resolved(self) -> bool:
        return self.root or self.parent is not None

    @property
    def is_plane(self) -> bool:
        return self.root or self.env is not None or any(r.is_plane for r in self.refs)


class Params:
    """Tabela de parâmetros com nomes únicos e compartilhamento por chave."""

    def __init__(self):
        self.items: list[dict] = []
        self.by_id: dict[str, dict] = {}
        self.by_key: dict[Any, str] = {}
        self.names = NameRegistry()
        self._ids: set[str] = set()

    def _make_id(self, name: str) -> str:
        base = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
        base = re.sub(r"[^a-z0-9]+", "_", base).strip("_") or "p"
        if not base[0].isalpha():
            base = "p_" + base
        base = base[:56]
        pid = base
        k = 2
        while pid in self._ids:
            pid = f"{base}_{k}"
            k += 1
        self._ids.add(pid)
        return pid

    def add(self, name: str, kind: str, group: str, value: Any, role: str, formula: str | None = None,
            instance: bool = False, description: str = "", key: Any = None) -> str:
        if key is not None and key in self.by_key:
            return self.by_key[key]
        real_name = self.names.take(name)
        pid = self._make_id(real_name)
        item = {"id": pid, "name": real_name, "kind": kind, "group": group, "instance": instance,
                "value": value, "formula": formula, "role": role}
        if description:
            item["description"] = description
        self.items.append(item)
        self.by_id[pid] = item
        if key is not None:
            self.by_key[key] = pid
        return pid

    def name(self, pid: str) -> str:
        return self.by_id[pid]["name"]

    def value(self, pid: str) -> float:
        return float(self.by_id[pid]["value"])


@dataclass
class PlanOptions:
    level: str = "C"  # A | B | C
    family_name: str = "Forge3D Familia"
    category: str = "GenericModel"
    source_name: str = ""


# ---------------------------------------------------------------------------
# utilidades geométricas


def _rect_loop(u0: float, v0: float, u1: float, v1: float) -> list[Segment]:
    return [
        Segment("line", (u0, v0), (u1, v0)), Segment("line", (u1, v0), (u1, v1)),
        Segment("line", (u1, v1), (u0, v1)), Segment("line", (u0, v1), (u0, v0)),
    ]


def _circle_loop(cu: float, cv: float, r: float) -> list[Segment]:
    c = np.array([cu, cv])
    p0 = c + np.array([r, 0.0])
    p1 = c - np.array([r, 0.0])
    return _arc_segment(p0, p1, c, r, True) + _arc_segment(p1, p0, c, r, True)


def _framed_from_axis_profile(axis: str, loops: list[list[Segment]], h0: float, h1: float) -> FramedExtrusion:
    """Extrusão alinhada -> extrusão com referencial (geometria fixa)."""
    if axis == "z":
        origin, xd, yd = (0.0, 0.0, h0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)
        lp = loops
    elif axis == "x":  # (u, v) = (y, z), normal y x z = +x
        origin, xd, yd = (h0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)
        lp = loops
    else:  # eixo y: (u, v) = (x, z); x x z = -y, então parte de h1 rumo a -y
        origin, xd, yd = (0.0, h1, 0.0), (1.0, 0.0, 0.0), (0.0, 0.0, 1.0)
        lp = loops
    fe = FramedExtrusion(origin=origin, xdir=xd, ydir=yd, loops=lp, depth=h1 - h0)
    return fe


def _as_fixed(p: Primitive) -> Primitive:
    """Converte uma primitiva em geometria fixa equivalente."""
    if isinstance(p, Box):
        lo, hi = np.asarray(p.lo), np.asarray(p.hi)
        fe = _framed_from_axis_profile("z", [_rect_loop(lo[0], lo[1], hi[0], hi[1])], lo[2], hi[2])
    elif isinstance(p, Cylinder):
        fe = _framed_from_axis_profile(p.axis, [_circle_loop(p.center[0], p.center[1], p.diameter / 2)], p.h0, p.h1)
    elif isinstance(p, Extrusion):
        fe = _framed_from_axis_profile(p.axis, p.loops, p.h0, p.h1)
    else:
        return p
    for attr in ("name", "source", "color", "material_name", "volume", "deviation", "detail"):
        setattr(fe, attr, getattr(p, attr))
    fe.notes = list(p.notes) + ["peça muito fina para restrições: geometria fixa"]
    return fe


def _loop_circle(loop: list[Segment], tol: float = 1e-3) -> tuple[float, float, float] | None:
    """(cu, cv, r) se o laço é um círculo completo formado por arcos."""
    if not loop or any(s.kind != "arc" or s.center is None or s.radius is None for s in loop):
        return None
    c0, r0 = loop[0].center, loop[0].radius
    for s in loop[1:]:
        if abs(s.center[0] - c0[0]) > tol * 100 or abs(s.center[1] - c0[1]) > tol * 100 or abs(s.radius - r0) > tol * 100:
            return None
    return (float(c0[0]), float(c0[1]), float(r0))


def _aligned_segments(loop: list[Segment], tol: float = 1e-3) -> list[tuple[int, int, float]]:
    """Retas paralelas a um eixo do perfil: (índice, 0 = coordenada u fixa / 1 = v fixa, coordenada)."""
    out = []
    for i, s in enumerate(loop):
        if s.kind != "line":
            continue
        if abs(s.end[0] - s.start[0]) <= tol:
            out.append((i, 0, s.start[0]))
        elif abs(s.end[1] - s.start[1]) <= tol:
            out.append((i, 1, s.start[1]))
    return out


def _alignable(loop: list[Segment], tol: float = 0.05) -> bool:
    """O contorno acompanha os planos se cada extremo (u mín/máx, v mín/máx)
    tiver uma reta travável; arcos entre as retas (cantos arredondados)
    acompanham as retas no Revit."""
    pts = [p for s in loop for p in (s.start, s.end)]
    us = [p[0] for p in pts]
    vs = [p[1] for p in pts]
    al = _aligned_segments(loop)
    need = {(0, min(us)), (0, max(us)), (1, min(vs)), (1, max(vs))}
    have = {(ax, c) for _, ax, c in al}
    return all(any(ax == a and abs(c - val) <= tol for a, c in have) for ax, val in need)


def _orthogonal_profile(loops: list[list[Segment]], tol: float = 1e-3) -> bool:
    for lp in loops:
        for s in lp:
            if s.kind != "line":
                return False
            du = abs(s.end[0] - s.start[0])
            dv = abs(s.end[1] - s.start[1])
            if min(du, dv) > tol:
                return False
    return True


# ---------------------------------------------------------------------------
# revoluções: raios paramétricos

RADIAL_STEP = 2.0  # mm — trecho vertical inserido nos pontos-chave do perfil


def _arc_geom(sg: Segment):
    import math as _m

    from ..spec.evaluate import _arc_sweep, circle_from_3pts

    a = np.asarray(sg.start, float)
    m = np.asarray(sg.mid, float)
    b = np.asarray(sg.end, float)
    c, r = circle_from_3pts(a, m, b)
    if c is None:
        return None
    a0 = _m.atan2(a[1] - c[1], a[0] - c[0])
    am = _m.atan2(m[1] - c[1], m[0] - c[0])
    a1 = _m.atan2(b[1] - c[1], b[0] - c[0])
    return c, r, a0, _arc_sweep(a0, am, a1)


def _arc_point(c, r, ang):
    import math as _m

    return (float(c[0] + r * _m.cos(ang)), float(c[1] + r * _m.sin(ang)))


def _revolution_keys(p: Revolution) -> list[tuple[float, float, str]]:
    """Pontos-chave do perfil (r, h, tipo): raio máximo, boca (topo) e base."""
    import math as _m

    prof = p.profile
    keys: list[tuple[float, float, str]] = []
    # Raio máximo: vértices e interior dos arcos.
    best = (-1.0, 0.0)
    for sg in prof:
        for pt in (sg.start, sg.end):
            if pt[0] > best[0] + 1e-6:
                best = (pt[0], pt[1])
        if sg.kind == "arc":
            g = _arc_geom(sg)
            if g is None:
                continue
            c, r, a0, sweep = g
            # O arco passa pelo ângulo 0 (ponto mais externo do círculo)?
            for k in (-1, 0, 1):
                ang = 2 * _m.pi * k
                t = (ang - a0) / sweep if sweep else -1
                if 0 < t < 1 and c[0] + r > best[0] + 1e-6:
                    best = (float(c[0] + r), float(c[1]))
    if best[0] > MERGE_TOL:
        keys.append((best[0], best[1], "max"))
    # Cantos externos do topo e da base (tampas planas).
    for h, kind, flat in ((p.h1, "topo", p.flat_top), (p.h0, "base", p.flat_bottom)):
        if not flat:
            continue
        rs = [pt[0] for sg in prof if sg.kind == "line" for pt in (sg.start, sg.end)
              if abs(sg.start[1] - h) <= 0.05 and abs(sg.end[1] - h) <= 0.05]
        if rs and max(rs) > MERGE_TOL:
            keys.append((max(rs), h, kind))
    # Remove repetidos (mesmo ponto).
    out: list[tuple[float, float, str]] = []
    for k in keys:
        if all(abs(k[0] - o[0]) > 0.5 or abs(k[1] - o[1]) > 0.5 for o in out):
            out.append(k)
    return out


def _with_points(sg: Segment, start=None, end=None) -> Segment:
    return Segment(sg.kind, start if start is not None else sg.start, end if end is not None else sg.end,
                   sg.mid, sg.center, sg.radius)


def _insert_radial(profile: list[Segment], key: tuple[float, float, str], r_final: float, h_lo: float,
                   h_hi: float) -> tuple[list[Segment], int] | None:
    """Garante um trecho vertical no ponto-chave e devolve (perfil, índice do trecho).

    Se já existe uma reta vertical passando pelo ponto, ela é usada. Senão o
    vértice (ou o ponto interno do arco, que é dividido) dá lugar a um trecho
    vertical de 2 mm — diferença de forma imperceptível.
    """
    r_k, h_k, kind = key
    L = RADIAL_STEP
    # 1) Reta vertical existente.
    for i, sg in enumerate(profile):
        if sg.kind == "line" and abs(sg.start[0] - r_k) <= 0.05 and abs(sg.end[0] - r_k) <= 0.05:
            lo, hi = sorted((sg.start[1], sg.end[1]))
            if lo - 0.05 <= h_k <= hi + 0.05 and hi - lo >= MIN_RADIAL_LINE:
                return [_snap_r(x, r_k, r_final) for x in profile], i
    prof = list(profile)
    # 2) Ponto interno de arco: divide o arco.
    vertex = None
    for i, sg in enumerate(prof):
        if abs(sg.end[0] - r_k) <= 0.05 and abs(sg.end[1] - h_k) <= 0.05:
            vertex = i
            break
    if vertex is None:
        for i, sg in enumerate(prof):
            if sg.kind != "arc":
                continue
            g = _arc_geom(sg)
            if g is None:
                continue
            c, r, a0, sweep = g
            import math as _m

            ang = _m.atan2(h_k - c[1], r_k - c[0])
            for k in (-1, 0, 1):
                t = (ang + 2 * _m.pi * k - a0) / sweep if sweep else -1
                if 0.02 < t < 0.98:
                    p_mid = (r_k, h_k)
                    m1 = _arc_point(c, r, a0 + sweep * t / 2)
                    m2 = _arc_point(c, r, a0 + sweep * (t + (1 - t) / 2))
                    first = Segment("arc", sg.start, p_mid, m1, sg.center, sg.radius)
                    second = Segment("arc", p_mid, sg.end, m2, sg.center, sg.radius)
                    prof[i:i + 1] = [first, second]
                    vertex = i
                    break
            if vertex is not None:
                break
    if vertex is None:
        return None
    n = len(prof)
    a = prof[vertex]
    b = prof[(vertex + 1) % n]
    going_down = a.start[1] > h_k + 1e-6 or (abs(a.start[1] - h_k) <= 1e-6 and b.end[1] < h_k)
    if kind == "topo":
        p_top, p_bot = (r_final, h_k), (r_final, h_k - L)
    elif kind == "base":
        p_top, p_bot = (r_final, h_k + L), (r_final, h_k)
    else:
        p_top, p_bot = (r_final, h_k + L / 2), (r_final, h_k - L / 2)
    first_pt, second_pt = (p_top, p_bot) if going_down else (p_bot, p_top)
    new_a = _with_points(a, end=first_pt)
    new_b = _with_points(b, start=second_pt)
    vertical = Segment("line", first_pt, second_pt)
    if new_a.length() < MIN_SEGMENT_MM or new_b.length() < MIN_SEGMENT_MM:
        return None
    if vertex + 1 < n:
        out = prof[:vertex] + [new_a, vertical, new_b] + prof[vertex + 2:]
        idx = vertex + 1
    else:  # o vértice fecha o laço (fim do último = início do primeiro)
        out = [new_b] + prof[1:vertex] + [new_a, vertical]
        idx = len(out) - 1
    return out, idx


MIN_RADIAL_LINE = 1.0
MIN_SEGMENT_MM = 1.0


def _snap_r(sg: Segment, r_old: float, r_new: float) -> Segment:
    def sp(pt):
        if pt is None:
            return None
        return (r_new, pt[1]) if abs(pt[0] - r_old) <= 0.05 else pt

    return Segment(sg.kind, sp(sg.start), sp(sg.end), sp(sg.mid) if sg.kind == "arc" else sg.mid, sg.center, sg.radius)


# ---------------------------------------------------------------------------
# planejador


class Planner:
    def __init__(self, dec: Decomposition, opts: PlanOptions):
        self.dec = dec
        self.opts = opts
        self.W, self.D, self.H = dec.envelope
        self.params = Params()
        self.warnings: list[str] = list(dec.warnings)
        self.prims: list[Primitive] = []
        self.part_levels: list[str] = []
        self.part_notes: list[list[str]] = []
        self.round_footprint = False

    # -- materiais -----------------------------------------------------------
    def _materials(self) -> tuple[list[dict], dict[int, str]]:
        groups: dict[tuple, list[int]] = {}
        for i, p in enumerate(self.prims):
            key = (p.material_name or "", tuple(p.color))
            groups.setdefault(key, []).append(i)
        materials = []
        prim_param: dict[int, str] = {}
        single = len(groups) == 1
        used_names = NameRegistry()
        for k, ((mname, color), idxs) in enumerate(sorted(groups.items(), key=lambda kv: -len(kv[1]))):
            disp = used_names.take(material_display_name(mname, color))
            mat_id = f"mat_{k + 1}"
            materials.append({"id": mat_id, "name": f"Forge3D {disp}", "color": [int(c) for c in color],
                              "transparency": 0, "shininess": 64, "smoothness": 50})
            pname = "Material" if single else f"Material {disp}"
            pid = self.params.add(pname, "material", "materials", mat_id, "material",
                                  description="Material das peças desta cor/material no modelo original.")
            for i in idxs:
                prim_param[i] = pid
        return materials, prim_param

    # -- referências -------------------------------------------------------------
    def _collect_refs(self) -> dict[str, list[Ref]]:
        refs: dict[str, list[Ref]] = {"x": [], "y": [], "z": []}
        for i, p in enumerate(self.prims):
            if isinstance(p, Box):
                for ax in ("x", "y", "z"):
                    k = AXIS_INDEX[ax]
                    refs[ax].append(Ref(ax, p.lo[k], i, "lo", True))
                    refs[ax].append(Ref(ax, p.hi[k], i, "hi", True))
            elif isinstance(p, Cylinder):
                u, v = PERP[p.axis]
                refs[u].append(Ref(u, p.center[0], i, "center", False))
                refs[v].append(Ref(v, p.center[1], i, "center", False))
                refs[p.axis].append(Ref(p.axis, p.h0, i, "lo", True))
                refs[p.axis].append(Ref(p.axis, p.h1, i, "hi", True))
            elif isinstance(p, Extrusion):
                refs[p.axis].append(Ref(p.axis, p.h0, i, "lo", True))
                refs[p.axis].append(Ref(p.axis, p.h1, i, "hi", True))
                if _alignable(p.loops[0]):
                    u, v = PERP[p.axis]
                    for lp in p.loops:
                        if lp is not p.loops[0] and not _orthogonal_profile([lp]):
                            continue
                        for _, ax, coord in _aligned_segments(lp):
                            if ax == 0:
                                refs[u].append(Ref(u, coord, i, "seg", True))
                            else:
                                refs[v].append(Ref(v, coord, i, "seg", True))
            elif isinstance(p, Revolution):
                u, v = PERP[p.axis]
                refs[u].append(Ref(u, p.center[0], i, "center", False))
                refs[v].append(Ref(v, p.center[1], i, "center", False))
                for r_k, _, _ in self._radial_keys(p):
                    refs[u].append(Ref(u, p.center[0] + r_k, i, "radial", True))
                if p.flat_bottom:
                    refs[p.axis].append(Ref(p.axis, p.h0, i, "lo", True))
                if p.flat_top:
                    refs[p.axis].append(Ref(p.axis, p.h1, i, "hi", True))
        return refs

    def _build_levels(self, axis: str, refs: list[Ref]) -> list[Lvl]:
        ext = {"x": self.W, "y": self.D, "z": self.H}[axis]
        specials: list[tuple[float, bool, str | None]] = [(0.0, True, None)]
        if axis == "z":
            specials.append((ext, False, "max"))
        else:
            specials += [(-ext / 2, False, "min"), (ext / 2, False, "max")]
        levels: list[Lvl] = [Lvl(axis, v, root=r, env=e, original=v) for v, r, e in specials]
        for ref in sorted(refs, key=lambda r: r.value):
            target = None
            for lv in levels:
                if abs(lv.original - ref.value) <= MERGE_TOL:
                    target = lv
                    break
            if target is None:
                target = Lvl(axis, ref.value, original=ref.value)
                levels.append(target)
            target.refs.append(ref)
        # Valor representativo: média das referências (exceto níveis especiais).
        for lv in levels:
            if not lv.root and lv.env is None and lv.refs:
                lv.value = float(np.mean([r.value for r in lv.refs]))
                lv.original = lv.value
        levels.sort(key=lambda lv: lv.value)
        return levels

    # -- resolução -----------------------------------------------------------------
    def _envelope_params(self, has_x: bool, has_y: bool) -> dict[str, str]:
        out = {}
        if self.round_footprint:
            pid = self.params.add("Diâmetro", "length", "dimensions", self.W, "envelope",
                                  description="Diâmetro total do objeto em planta.")
            half = self.params.add("Raio", "length", "constraints", self.W / 2, "auxiliary",
                                   formula=f"{self.params.name(pid)} / 2")
            out.update({"x_env": pid, "y_env": pid, "x_half": half, "y_half": half})
        else:
            pw = self.params.add("Largura", "length", "dimensions", self.W, "envelope",
                                 description="Largura total (eixo X, esquerda-direita).")
            pd = self.params.add("Profundidade", "length", "dimensions", self.D, "envelope",
                                 description="Profundidade total (eixo Y, frente-fundo).")
            out["x_env"], out["y_env"] = pw, pd
            out["x_half"] = self.params.add("Meia Largura", "length", "constraints", self.W / 2, "auxiliary",
                                            formula=f"{self.params.name(pw)} / 2")
            out["y_half"] = self.params.add("Meia Profundidade", "length", "constraints", self.D / 2, "auxiliary",
                                            formula=f"{self.params.name(pd)} / 2")
        out["z_env"] = self.params.add("Altura", "length", "dimensions", self.H, "envelope",
                                       description="Altura total (eixo Z).")
        return out

    def _set(self, lv: Lvl, parent: Lvl, driver: str, kind: str) -> None:
        """Resolve ``lv`` a partir de ``parent`` com o parâmetro ``driver``.

        O valor do nível é ajustado para coincidir exatamente com o parâmetro
        (diferenças de arredondamento, sempre abaixo de 0,1 mm).
        """
        sign = 1.0 if lv.value >= parent.value else -1.0
        lv.parent = parent
        lv.driver = driver
        lv.kind = kind
        lv.value = parent.value + sign * self.params.value(driver)

    def _resolve_axis(self, axis: str, levels: list[Lvl], roles: list[Role], env: dict[str, str], mode: str) -> None:
        ext = {"x": self.W, "y": self.D, "z": self.H}[axis]
        root = next(lv for lv in levels if lv.root)
        for lv in levels:
            if lv.env is not None:
                if axis == "z":
                    self._set(lv, root, env["z_env"], "envelope")
                else:
                    self._set(lv, root, env[f"{axis}_half"], "envelope")
        # Pares de faces de cada primitiva (para espessuras).
        level_of: dict[tuple[int, str], Lvl] = {}
        for lv in levels:
            for r in lv.refs:
                if r.role in ("lo", "hi"):
                    level_of[(r.prim, r.role)] = lv
        pairs = []
        for i in range(len(self.prims)):
            a, b = level_of.get((i, "lo")), level_of.get((i, "hi"))
            if a is not None and b is not None and a is not b:
                pairs.append((i, a, b))

        def thin_pass() -> bool:
            changed = False
            for i, a, b in pairs:
                if a.resolved == b.resolved:
                    continue
                t = abs(b.value - a.value)
                if t > THIN_RATIO * ext or t < MERGE_TOL:
                    continue
                known, unknown = (a, b) if a.resolved else (b, a)
                pid = self._thickness_param(i, axis, t, roles)
                self._set(unknown, known, pid, "thickness")
                changed = True
            return changed

        def inset_pass() -> bool:
            best = None
            planes = [lv for lv in levels if lv.resolved and lv.is_plane]
            for lv in levels:
                if lv.resolved or (lv.refs and all(r.role == "radial" for r in lv.refs)):
                    continue
                for cand in planes:
                    gap = abs(lv.value - cand.value)
                    if MERGE_TOL <= gap <= INSET_RATIO * ext:
                        score = (gap, 0 if (cand.env or cand.root) else 1)
                        if best is None or score < best[0]:
                            best = (score, lv, cand, gap)
            if best is None:
                return False
            _, lv, cand, gap = best
            pid = self._inset_param(axis, lv, cand, gap, roles)
            self._set(lv, cand, pid, "inset")
            return True

        def interior_pass() -> bool:
            pending = [lv for lv in levels if not lv.resolved]
            if not pending:
                return False
            pending.sort(key=lambda lv: (-len(lv.refs), -lv.value))
            # Topo de placas horizontais ganha parâmetro próprio de altura.
            if axis == "z" and mode == "C":
                for lv in pending:
                    role = self._slab_top_role(lv, roles)
                    if role is not None and lv.value > MERGE_TOL:
                        pid = self.params.add(f"Altura {role.of}", "length", "dimensions", lv.value, "semantic",
                                              description=f"Altura do topo {role.of} em relação à base.")
                        self._set(lv, root, pid, "height")
                        return True
            lv = pending[0]
            dist = abs(lv.value)
            if dist < MERGE_TOL:
                lv.parent, lv.driver, lv.kind, lv.value = root, None, "root", 0.0
                return True
            env_name = self.params.name(env[f"{axis}_env"])
            env_val = self.params.value(env[f"{axis}_env"])
            frac = dist / env_val
            formula = fx.fraction_formula(env_name, frac)
            value = fx.evaluate(formula, lambda n: env_val)
            key = ("prop", axis, round(value, 3))
            label = AXIS_LABEL[axis]
            pid = self.params.add(f"aux {label}{len([k for k in self.params.by_key if isinstance(k, tuple) and k[:2] == ('prop', axis)]) + 1:02d}",
                                  "length", "constraints", value, "auxiliary", formula=formula, key=key,
                                  description="Posição proporcional ao envelope (calculada).")
            self._set(lv, root, pid, "proportional")
            return True

        guard = 0
        while any(not lv.resolved for lv in levels) and guard < 10000:
            guard += 1
            if mode == "C" and (thin_pass() or inset_pass()):
                continue
            if not interior_pass():
                break

    def _slab_top_role(self, lv: Lvl, roles: list[Role]) -> Role | None:
        for r in lv.refs:
            if r.role == "hi" and roles[r.prim].key in ("assento", "prateleira", "base", "tampo"):
                return roles[r.prim]
        return None

    def _thickness_param(self, prim: int, axis: str, t: float, roles: list[Role]) -> str:
        role = roles[prim]
        p = self.prims[prim]
        value = round(t, 3)
        if role.key in ("perna", "montante", "travessa", "haste") and isinstance(p, Box):
            ext = p.extents
            u, v = PERP[{"perna": "z", "montante": "z", "haste": "z"}.get(role.key, "z")]
            square = abs(ext[AXIS_INDEX[u]] - ext[AXIS_INDEX[v]]) <= MERGE_TOL
            if role.key == "travessa":
                long_axis = int(np.argmax(ext))
                if AXIS_INDEX[axis] == long_axis:
                    return self._generic_thickness(axis, value, role)
                others = [k for k in range(3) if k != long_axis]
                square = abs(ext[others[0]] - ext[others[1]]) <= MERGE_TOL
                name = f"Seção {role.of}" if square else f"{'Altura' if axis == 'z' else 'Largura'} {role.of}"
                return self.params.add(name, "length", "dimensions", value, "semantic",
                                       description=f"Dimensão da seção {role.of}.", key=("sec", role.key, square, axis if not square else "", value))
            if axis == "z":
                return self._generic_thickness(axis, value, role)
            if square:
                return self.params.add(f"Seção {role.of}", "length", "dimensions", value, "semantic",
                                       description=f"Lado da seção quadrada {role.of}.", key=("sec", role.key, value))
            name = f"{'Largura' if axis == 'x' else 'Profundidade'} {role.of}"
            return self.params.add(name, "length", "dimensions", value, "semantic", key=("sec", role.key, axis, value))
        if role.key in ("tampo", "base", "assento", "prateleira") and axis == "z":
            return self.params.add(f"Espessura {role.of}", "length", "dimensions", value, "semantic",
                                   description=f"Espessura {role.of}.", key=("esp", role.key, value))
        if role.key in ("lateral", "fundo", "frente", "divisoria", "encosto"):
            thin_axis = {"lateral": "x", "divisoria": None, "fundo": "y", "frente": "y", "encosto": "y"}[role.key]
            if thin_axis is None or thin_axis == axis:
                return self.params.add(f"Espessura {role.of}", "length", "dimensions", value, "semantic",
                                       description=f"Espessura {role.of}.", key=("esp", role.key, value))
        return self._generic_thickness(axis, value, role)

    def _generic_thickness(self, axis: str, value: float, role: Role) -> str:
        return self.params.add(f"Espessura {AXIS_LABEL[axis]}", "length", "dimensions", value, "semantic",
                               description=f"Espessura constante no eixo {AXIS_LABEL[axis]}.", key=("espg", axis, value))

    def _inset_param(self, axis: str, lv: Lvl, cand: Lvl, gap: float, roles: list[Role]) -> str:
        value = round(gap, 3)
        if axis == "z":
            prim_roles = {roles[r.prim].key for r in lv.refs}
            if cand.root and "travessa" in prim_roles:
                name = "Altura da Travessa"
            elif cand.root:
                name = "Recuo Inferior"
            elif cand.env == "max":
                name = "Recuo Superior"
            else:
                name = "Distância Z"
        else:
            name = f"Recuo {AXIS_LABEL[axis]}" if (cand.env or cand.root) else f"Distância {AXIS_LABEL[axis]}"
        return self.params.add(name, "length", "dimensions", value, "semantic",
                               description="Distância constante até o plano de referência.",
                               key=("inset", axis, name, value))

    # -- principal -------------------------------------------------------------------
    def plan(self) -> dict:
        dec = self.dec
        level = self.opts.level
        prims = self._split_holes([p for p in dec.primitives]) if level != "A" else [p for p in dec.primitives]
        roles = classify_roles(prims, dec.envelope)
        names = solid_names(roles)
        for p, n in zip(prims, names):
            p.name = n
        self.prims = prims

        materials, mat_param = self._materials()
        info_src = self.params.add("Origem do Modelo", "text", "identity", self.opts.source_name or "", "info",
                                   description="Arquivo de origem convertido pelo Forge 3D.")
        info_lvl = self.params.add("Nível de Parametrização", "text", "identity", level, "info",
                                   description="Nível alcançado pelo Forge 3D (A, B ou C).")

        if level == "A":
            spec = self._emit_fixed(prims, roles, materials, mat_param)
        else:
            spec = self._emit_parametric(prims, roles, materials, mat_param, level)
        self.params.by_id[info_lvl]["value"] = spec["family"]["achievedLevel"]
        del info_src
        return spec

    def _split_holes(self, prims: list[Primitive]) -> list[Primitive]:
        """Furos circulares de extrusões retas viram cilindros vazios paramétricos."""
        out: list[Primitive] = []
        for p in prims:
            if isinstance(p, Extrusion) and len(p.loops) > 1 and _alignable(p.loops[0]):
                keep = [p.loops[0]]
                voids = []
                for lp in p.loops[1:]:
                    circ = _loop_circle(lp)
                    if circ is None:
                        keep.append(lp)
                        continue
                    v = Cylinder(axis=p.axis, center=(circ[0], circ[1]), diameter=2 * circ[2], h0=p.h0, h1=p.h1)
                    v.is_void = True
                    v.source, v.color, v.material_name = p.source, p.color, p.material_name
                    v.volume = 0.0
                    voids.append(v)
                if voids:
                    e = Extrusion(axis=p.axis, loops=keep, h0=p.h0, h1=p.h1)
                    for attr in ("name", "source", "color", "material_name", "volume", "deviation", "detail"):
                        setattr(e, attr, getattr(p, attr))
                    e.notes = list(p.notes)
                    out.append(e)
                    out += voids
                    continue
            out.append(p)
        return out

    def _radial_keys(self, p: Revolution) -> list[tuple[float, float, str]]:
        """Pontos-chave radiais de uma revolução vertical centrada (Nível C)."""
        if getattr(self, "_mode", "C") != "C" or p.axis != "z":
            return []
        if abs(p.center[0]) > MERGE_TOL or abs(p.center[1]) > MERGE_TOL:
            return []
        if p.h1 - p.h0 < 20 * RADIAL_STEP:
            return []
        return _revolution_keys(p)

    def _thin_for_constraints(self, p: Primitive) -> bool:
        b = p.bounds()
        ext = b[1] - b[0]
        if isinstance(p, (Box, Extrusion)):
            return bool((ext < 2 * MERGE_TOL).any())
        if isinstance(p, Cylinder):
            return p.diameter < 2 * MERGE_TOL or (p.h1 - p.h0) < 2 * MERGE_TOL
        return False

    def _emit_parametric(self, prims, roles, materials, mat_param, level: str) -> dict:
        self._mode = level
        # Peças finas demais viram geometria fixa.
        for i, p in enumerate(prims):
            if self._thin_for_constraints(p):
                prims[i] = _as_fixed(p)
        self.prims = prims
        # Planta redonda: um cilindro centrado com o diâmetro do envelope.
        self.round_footprint = abs(self.W - self.D) <= MERGE_TOL and any(
            ((isinstance(p, Cylinder) and abs(p.diameter - self.W) <= MERGE_TOL)
             or (isinstance(p, Revolution) and level == "C" and abs(2 * p.max_radius() - self.W) <= MERGE_TOL))
            and p.axis == "z" and abs(p.center[0]) <= MERGE_TOL and abs(p.center[1]) <= MERGE_TOL for p in prims)
        refs = self._collect_refs()
        env = self._envelope_params(bool(refs["x"]), bool(refs["y"]))
        levels = {ax: self._build_levels(ax, refs[ax]) for ax in ("x", "y", "z")}
        for ax in ("x", "y", "z"):
            self._resolve_axis(ax, levels[ax], roles, env, level)

        level_of_ref: dict[tuple[int, str, str, float], Lvl] = {}
        for ax, lvls in levels.items():
            for lv in lvls:
                for r in lv.refs:
                    level_of_ref[(r.prim, ax, r.role, round(r.value, 6))] = lv

        def lvl(i: int, ax: str, role: str, value: float) -> Lvl:
            return level_of_ref[(i, ax, role, round(value, 6))]

        # Diâmetros.
        diam_driver: dict[int, str | None] = {}
        for i, p in enumerate(prims):
            if not isinstance(p, Cylinder):
                continue
            if self.round_footprint and p.axis == "z" and abs(p.diameter - self.W) <= MERGE_TOL:
                diam_driver[i] = env["x_env"]
                continue
            if level == "C":
                role = roles[i]
                diam_driver[i] = self.params.add(f"Diâmetro {role.of}", "length", "dimensions", round(p.diameter, 3),
                                                 "semantic", description=f"Diâmetro {role.of}.",
                                                 key=("diam", role.key, round(p.diameter, 1)))
            else:
                ref_env = env["x_env"] if p.axis != "x" else env["y_env"]
                env_val = self.params.value(ref_env)
                formula = fx.fraction_formula(self.params.name(ref_env), p.diameter / env_val)
                value = fx.evaluate(formula, lambda n: env_val)
                diam_driver[i] = self.params.add("aux Diâmetro", "length", "constraints", value, "auxiliary",
                                                 formula=formula, key=("pdiam", round(value, 3)))

        # Uso de níveis (poda de planos e parâmetros não usados).
        used: set[int] = set()

        def use(lv: Lvl) -> None:
            while lv is not None and id(lv) not in used:
                used.add(id(lv))
                lv = lv.parent

        solids_out = []
        part_reports = []
        for i, p in enumerate(prims):
            notes = list(p.notes)
            part_level = "B"
            mat = mat_param.get(i)
            if p.is_void:
                mat = None
            base = {"id": f"s{i + 1}", "name": p.name, "material": mat, "isVoid": bool(p.is_void),
                    "visibility": "medium_fine" if (p.detail and not p.is_void) else "all",
                    "part": sanitize_name(p.source, "parte", 60)}
            if isinstance(p, Box):
                faces = {}
                for ax in ("x", "y", "z"):
                    k = AXIS_INDEX[ax]
                    a = lvl(i, ax, "lo", p.lo[k])
                    b = lvl(i, ax, "hi", p.hi[k])
                    use(a)
                    use(b)
                    faces[ax] = (a, b)
                s = dict(base, kind="box")
                for ax, (a, b) in faces.items():
                    s[ax] = [a, b]
                solids_out.append(s)
                part_level = "C"
            elif isinstance(p, Cylinder):
                u, v = PERP[p.axis]
                cu = lvl(i, u, "center", p.center[0])
                cv = lvl(i, v, "center", p.center[1])
                h0 = lvl(i, p.axis, "lo", p.h0)
                h1 = lvl(i, p.axis, "hi", p.h1)
                for x in (h0, h1):
                    use(x)
                for c in (cu, cv):
                    if c.parent is not None:
                        use(c.parent)
                    if c.is_plane:
                        use(c)
                drv = diam_driver.get(i)
                dia_val = self.params.value(drv) if drv else p.diameter
                s = dict(base, kind="cylinder", axis=p.axis, center={"u": cu, "v": cv},
                         diameter={"value": round(dia_val, 4), "driver": drv}, extent=[h0, h1])
                solids_out.append(s)
                part_level = "C"
            elif isinstance(p, Extrusion):
                h0 = lvl(i, p.axis, "lo", p.h0)
                h1 = lvl(i, p.axis, "hi", p.h1)
                use(h0)
                use(h1)
                aligned = []
                loops = p.loops
                if _alignable(p.loops[0]):
                    u, v = PERP[p.axis]
                    loops = []
                    for li, lp in enumerate(p.loops):
                        if li > 0 and not _orthogonal_profile([lp]):
                            loops.append(list(lp))
                            notes.append("furo irregular com posição fixa")
                            continue
                        for si, ax, coord in _aligned_segments(lp):
                            lv = lvl(i, u if ax == 0 else v, "seg", coord)
                            use(lv)
                            aligned.append((li, si, lv))
                        loops.append(list(lp))
                    if any(sg.kind == "arc" for sg in p.loops[0]):
                        notes.append("cantos curvos acompanham as retas travadas (o raio pode variar ao flexionar)")
                    part_level = "C"
                else:
                    notes.append("contorno curvo sem retas nos extremos: forma fixa, espessura/altura paramétricas")
                s = dict(base, kind="extrusion", axis=p.axis, loops=loops, extent=[h0, h1], _aligned=aligned)
                solids_out.append(s)
            elif isinstance(p, Revolution):
                u, v = PERP[p.axis]
                cu = lvl(i, u, "center", p.center[0])
                cv = lvl(i, v, "center", p.center[1])
                for c in (cu, cv):
                    if c.parent is not None:
                        use(c.parent)
                    if c.is_plane:
                        use(c)
                ext_lv = None
                if p.flat_bottom and p.flat_top:
                    a = lvl(i, p.axis, "lo", p.h0)
                    b = lvl(i, p.axis, "hi", p.h1)
                    use(a)
                    use(b)
                    ext_lv = [a, b]
                profile = list(p.profile)
                radial: list[tuple[int, Lvl]] = []
                for key in self._radial_keys(p):
                    lv_r = lvl(i, u, "radial", p.center[0] + key[0])
                    res = _insert_radial(profile, key, lv_r.value - p.center[0], p.h0, p.h1)
                    if res is None:
                        continue
                    new_profile, idx = res
                    # Índices de trechos já travados mudam se a inserção veio antes.
                    shift = len(new_profile) - len(profile)
                    radial = [(j + shift if j >= idx and shift else j, lvv) for j, lvv in radial]
                    profile = new_profile
                    use(lv_r)
                    radial.append((idx, lv_r))
                if radial and ext_lv:
                    part_level = "C"
                    notes.append("revolução: altura e diâmetros paramétricos (pontos-chave do perfil travados)")
                else:
                    notes.append("revolução: altura paramétrica, perfil (raios) fixo" if ext_lv else "revolução: forma fixa")
                s = dict(base, kind="revolution", axis=p.axis, center={"u": cu, "v": cv}, profile=profile,
                         range=[p.h0, p.h1], extent=ext_lv, _radial=radial)
                solids_out.append(s)
            elif isinstance(p, FramedExtrusion):
                s = dict(base, kind="extrusion", frame={"origin": list(p.origin), "xdir": list(p.xdir), "ydir": list(p.ydir)},
                         loops=p.loops, depth=p.depth)
                solids_out.append(s)
            elif isinstance(p, FreeformMesh):
                s = dict(base, kind="mesh", vertices=p.vertices, faces=p.faces)
                solids_out.append(s)
                notes.append("forma livre: geometria fixa")
            self.part_levels.append(part_level)
            part_reports.append({"id": base["id"], "name": p.name, "source": p.source, "kind": p.kind, "level": part_level,
                                 "volume": round(float(p.volume), 2), "deviation": round(float(p.deviation), 3),
                                 "detail": bool(p.detail), "notes": notes})

        # Planos usados e seus ids.
        planes_out = []
        counters = {"x": 0, "y": 0, "z": 0}
        env_names = {("x", "min"): ("Esquerda", "left"), ("x", "max"): ("Direita", "right"),
                     ("y", "min"): ("Frente", "front"), ("y", "max"): ("Fundo", "back"), ("z", "max"): ("Topo", "top")}
        for ax in ("x", "y", "z"):
            for lv in levels[ax]:
                if lv.root:
                    lv.plane_id = ROOT_ID[ax]
                    continue
                if id(lv) not in used or not (lv.is_plane or any(o.parent is lv and id(o) in used for o in levels[ax])):
                    continue
                if lv.parent is None or lv.driver is None:
                    # Nível que coincide com a raiz (sem cota).
                    lv.plane_id = ROOT_ID[ax]
                    continue
                counters[ax] += 1
                lv.plane_id = f"{ax}_{counters[ax]:02d}"
        for ax in ("x", "y", "z"):
            for lv in levels[ax]:
                if lv.plane_id is None or lv.root or lv.plane_id == ROOT_ID[ax]:
                    continue
                nm, ref = env_names.get((ax, lv.env), (None, "none")) if lv.env else (None, "none")
                planes_out.append({"id": lv.plane_id, "axis": ax, "position": round(lv.value, 4),
                                   "parent": lv.parent.plane_id or ROOT_ID[ax], "driver": lv.driver,
                                   "name": nm or f"Forge {AXIS_LABEL[ax]}{lv.plane_id[2:]}", "reference": ref})

        def anchor(c: Lvl) -> dict:
            if c.root or c.parent is None or c.driver is None:
                return {"position": round(c.value, 4), "parent": ROOT_ID[c.axis], "driver": None}
            return {"position": round(c.value, 4), "parent": c.parent.plane_id or ROOT_ID[c.axis], "driver": c.driver}

        def pid(lv: Lvl) -> str:
            return lv.plane_id or ROOT_ID[lv.axis]

        final_solids = []
        for s in solids_out:
            s = dict(s)
            if s["kind"] == "box":
                for ax in ("x", "y", "z"):
                    a, b = s[ax]
                    s[ax] = [pid(a), pid(b)]
            elif s["kind"] == "cylinder":
                s["center"] = {"u": anchor(s["center"]["u"]), "v": anchor(s["center"]["v"])}
                s["extent"] = [pid(s["extent"][0]), pid(s["extent"][1])]
            elif s["kind"] == "extrusion" and "extent" in s:
                a, b = s["extent"]
                s["extent"] = [pid(a), pid(b)]
                # Coordenadas do perfil ajustadas aos níveis (posição final dos planos).
                aligned = s.pop("_aligned", [])
                loops = [[Segment(x.kind, x.start, x.end, x.mid, x.center, x.radius) for x in lp] for lp in s["loops"]]
                s["alignments"] = []
                if aligned:
                    u, v = PERP[s["axis"]]
                    snap_u: dict[float, float] = {}
                    snap_v: dict[float, float] = {}
                    for li, si, lv in aligned:
                        sgm = loops[li][si]
                        if lv.axis == u:
                            snap_u[round(sgm.start[0], 6)] = lv.value
                        else:
                            snap_v[round(sgm.start[1], 6)] = lv.value
                    loops = [[_snap_segment(x, snap_u, snap_v) for x in lp] for lp in loops]
                    for li, si, lv in aligned:
                        s["alignments"].append({"loop": li, "segment": si, "plane": pid(lv)})
                s["loops"] = [[x.to_spec() for x in lp] for lp in loops]
            elif s["kind"] == "extrusion":
                s["loops"] = [[x.to_spec() for x in lp] for lp in s["loops"]]
                s["depth"] = round(float(s["depth"]), 4)
                s["frame"] = {k: [round(float(c), 6) for c in val] for k, val in s["frame"].items()}
            elif s["kind"] == "revolution":
                s["center"] = {"u": anchor(s["center"]["u"]), "v": anchor(s["center"]["v"])}
                radial = s.pop("_radial", [])
                if radial:
                    s["alignments"] = [{"loop": 0, "segment": j, "plane": pid(lv)} for j, lv in radial]
                prof = s["profile"]
                if s.get("extent"):
                    a, b = s["extent"]
                    lo_old, hi_old = s["range"]
                    prof = [_snap_h(x, lo_old, a.value, hi_old, b.value) for x in prof]
                    s["range"] = [round(a.value, 4), round(b.value, 4)]
                    s["extent"] = [pid(a), pid(b)]
                else:
                    s.pop("extent", None)
                    s["range"] = [round(float(s["range"][0]), 4), round(float(s["range"][1]), 4)]
                s["profile"] = [x.to_spec() for x in prof]
            elif s["kind"] == "mesh":
                s["vertices"] = [[round(float(c), 3) for c in vtx] for vtx in s["vertices"]]
                s["faces"] = [[int(c) for c in f] for f in s["faces"]]
            final_solids.append(s)

        # Snap das caixas: faces nos valores finais dos níveis (já resolvidos).
        params_used = self._used_params(planes_out, final_solids)
        parameters = [self._finalize_param(p) for p in self.params.items if p["id"] in params_used]
        materials_used = {p["value"] for p in parameters if p["kind"] == "material"}
        materials = [m for m in materials if m["id"] in materials_used]

        achieved = self._achieved_level(level)
        family_types = [{"name": self._type_name(parameters), "values": {
            p["id"]: p["value"] for p in parameters
            if p["kind"] in ("length", "number", "integer", "angle") and not p.get("formula")
        }}]
        coverage = self._coverage()
        spec = {
            "schema": "forge3d.familyspec",
            "version": 1,
            "generator": {"name": "forge3d-engine", "version": ENGINE_VERSION},
            "family": {"name": self.opts.family_name, "category": self.opts.category,
                       "description": "Família gerada pelo Forge 3D.", "requestedLevel": level, "achievedLevel": achieved},
            "units": "mm",
            "envelope": {"width": round(self.W, 4), "depth": round(self.D, 4), "height": round(self.H, 4)},
            "parameters": parameters,
            "planes": planes_out,
            "materials": materials,
            "solids": final_solids,
            "types": family_types,
            "report": {
                "requestedLevel": level, "achievedLevel": achieved, "parametricCoverage": round(coverage, 4),
                "parts": part_reports, "warnings": list(self.warnings),
                "levelCBlockers": self._blockers(part_reports) if level == "C" else [],
            },
        }
        return spec

    def _emit_fixed(self, prims, roles, materials, mat_param) -> dict:
        solids_out = []
        part_reports = []
        for i, p in enumerate(prims):
            q = _as_fixed(p)
            mat = mat_param.get(i)
            base = {"id": f"s{i + 1}", "name": p.name, "material": mat, "isVoid": False,
                    "visibility": "medium_fine" if p.detail else "all", "part": sanitize_name(p.source, "parte", 60)}
            if isinstance(q, FramedExtrusion):
                s = dict(base, kind="extrusion", loops=[[x.to_spec() for x in lp] for lp in q.loops],
                         frame={"origin": [round(c, 6) for c in q.origin], "xdir": list(q.xdir), "ydir": list(q.ydir)},
                         depth=round(q.depth, 4))
            elif isinstance(q, Revolution):
                s = dict(base, kind="revolution", axis=q.axis,
                         center={"u": {"position": q.center[0], "parent": ROOT_ID[PERP[q.axis][0]], "driver": None},
                                 "v": {"position": q.center[1], "parent": ROOT_ID[PERP[q.axis][1]], "driver": None}},
                         profile=[x.to_spec() for x in q.profile], range=[q.h0, q.h1])
            else:
                s = dict(base, kind="mesh", vertices=[[round(float(c), 3) for c in v] for v in q.vertices],
                         faces=[[int(c) for c in f] for f in q.faces])
            solids_out.append(s)
            self.part_levels.append("A")
            part_reports.append({"id": base["id"], "name": p.name, "source": p.source, "kind": p.kind, "level": "A",
                                 "volume": round(float(p.volume), 2), "deviation": round(float(p.deviation), 3),
                                 "detail": bool(p.detail), "notes": list(q.notes)})
        params_used = self._used_params([], solids_out)
        parameters = [self._finalize_param(p) for p in self.params.items if p["id"] in params_used]
        materials_used = {p["value"] for p in parameters if p["kind"] == "material"}
        return {
            "schema": "forge3d.familyspec", "version": 1,
            "generator": {"name": "forge3d-engine", "version": ENGINE_VERSION},
            "family": {"name": self.opts.family_name, "category": self.opts.category,
                       "description": "Família gerada pelo Forge 3D (geometria fixa).", "requestedLevel": "A", "achievedLevel": "A"},
            "units": "mm",
            "envelope": {"width": round(self.W, 4), "depth": round(self.D, 4), "height": round(self.H, 4)},
            "parameters": parameters, "planes": [],
            "materials": [m for m in materials if m["id"] in materials_used],
            "solids": solids_out,
            "types": [{"name": "Padrão", "values": {}}],
            "report": {"requestedLevel": "A", "achievedLevel": "A", "parametricCoverage": 0.0, "parts": part_reports,
                       "warnings": list(self.warnings), "levelCBlockers": []},
        }

    # -- finalização ------------------------------------------------------------------
    def _used_params(self, planes: list[dict], solids: list[dict]) -> set[str]:
        used: set[str] = set()
        for pl in planes:
            used.add(pl["driver"])
        for s in solids:
            if s.get("material"):
                used.add(s["material"])
            if s["kind"] in ("cylinder", "revolution"):
                for k in ("u", "v"):
                    d = s["center"][k]["driver"]
                    if d:
                        used.add(d)
            if s["kind"] == "cylinder" and s["diameter"]["driver"]:
                used.add(s["diameter"]["driver"])
        for p in self.params.items:
            if p["role"] == "info":
                used.add(p["id"])
        # Fecho pelas fórmulas.
        by_name = {p["name"]: p["id"] for p in self.params.items}
        stack = list(used)
        while stack:
            pid_ = stack.pop()
            f = self.params.by_id[pid_].get("formula")
            if f:
                for n in fx.referenced_names(f):
                    ref = by_name.get(n)
                    if ref and ref not in used:
                        used.add(ref)
                        stack.append(ref)
        return used

    def _finalize_param(self, p: dict) -> dict:
        q = dict(p)
        if q["kind"] == "length" and isinstance(q["value"], (int, float)):
            q["value"] = round(float(q["value"]), 4)
        return q

    def _achieved_level(self, requested: str) -> str:
        if requested == "B":
            return "B"
        significant = [lv for lv, p in zip(self.part_levels, self.prims) if not p.detail]
        if significant and all(lv == "C" for lv in significant):
            return "C"
        return "B"

    def _coverage(self) -> float:
        total = sum(p.volume for p in self.prims) or 1.0
        c = sum(p.volume for p, lv in zip(self.prims, self.part_levels) if lv == "C")
        return float(min(1.0, c / total))

    def _blockers(self, parts: list[dict]) -> list[str]:
        total = sum(p["volume"] for p in parts) or 1.0
        out = []
        for p in parts:
            if p["level"] != "C" and not p["detail"]:
                pct = 100.0 * p["volume"] / total
                motivo = "; ".join(p["notes"]) or "geometria não reconhecida como primitiva paramétrica"
                out.append(f"{p['name']} ({pct:.0f}% do volume): {motivo}")
        return out

    def _type_name(self, parameters: list[dict]) -> str:
        vals = {p["name"]: p["value"] for p in parameters if p["role"] == "envelope"}
        if "Diâmetro" in vals:
            return f"Ø{vals['Diâmetro']:.0f} x {vals.get('Altura', self.H):.0f}"
        parts = [f"{vals[k]:.0f}" for k in ("Largura", "Profundidade", "Altura") if k in vals]
        return " x ".join(parts) if parts else "Padrão"


def _snap_segment(s: Segment, snap_u: dict[float, float], snap_v: dict[float, float]) -> Segment:
    def sp(p):
        if p is None:
            return None
        u = snap_u.get(round(p[0], 6), p[0])
        v = snap_v.get(round(p[1], 6), p[1])
        return (u, v)

    return Segment(s.kind, sp(s.start), sp(s.end), sp(s.mid), s.center, s.radius)


def _snap_h(s: Segment, lo_old: float, lo_new: float, hi_old: float, hi_new: float) -> Segment:
    def sp(p):
        if p is None:
            return None
        h = p[1]
        if abs(h - lo_old) <= MERGE_TOL:
            h = lo_new
        elif abs(h - hi_old) <= MERGE_TOL:
            h = hi_new
        return (p[0], h)

    return Segment(s.kind, sp(s.start), sp(s.end), sp(s.mid), s.center, s.radius)


def plan(dec: Decomposition, opts: PlanOptions) -> dict:
    return Planner(dec, opts).plan()

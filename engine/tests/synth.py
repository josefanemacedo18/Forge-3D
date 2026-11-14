"""Objetos sintéticos para testes (dimensões em mm, Z para cima)."""

from __future__ import annotations

import math

import numpy as np
import trimesh


def box(lo, hi):
    lo = np.asarray(lo, float)
    hi = np.asarray(hi, float)
    m = trimesh.creation.box(extents=hi - lo)
    m.apply_translation((lo + hi) / 2)
    return m


def cylinder(center_xy, r, z0, z1, sections=48):
    m = trimesh.creation.cylinder(radius=r, height=z1 - z0, sections=sections)
    m.apply_translation([center_xy[0], center_xy[1], (z0 + z1) / 2])
    return m


def union(meshes):
    """União booleana (malha única, soldada) via manifold3d."""
    import manifold3d as mf

    acc = None
    for m in meshes:
        mm = mf.Manifold(mf.Mesh(vert_properties=np.asarray(m.vertices, np.float32), tri_verts=np.asarray(m.faces, np.uint32)))
        acc = mm if acc is None else acc + mm
    out = acc.to_mesh()
    return trimesh.Trimesh(np.asarray(out.vert_properties)[:, :3].astype(float), np.asarray(out.tri_verts), process=True)


def table(width=1200, depth=800, height=750, top=30, leg=50, inset=40):
    parts = [box([-width / 2, -depth / 2, height - top], [width / 2, depth / 2, height])]
    for sx in (-1, 1):
        for sy in (-1, 1):
            x0 = sx * (width / 2 - inset) - (leg if sx > 0 else 0)
            y0 = sy * (depth / 2 - inset) - (leg if sy > 0 else 0)
            parts.append(box([x0, y0, 0], [x0 + leg, y0 + leg, height - top]))
    return parts


def round_table(d=900, height=740, top=25, pole_r=40, base_r=250, base_t=20):
    return [
        cylinder((0, 0), d / 2, height - top, height, 96),
        cylinder((0, 0), pole_r, base_t, height - top, 32),
        cylinder((0, 0), base_r, 0, base_t, 64),
    ]


def chair(w=450, d=480, seat_h=450, back_h=900, t=30, leg=40):
    parts = [box([-w / 2, -d / 2, seat_h - t], [w / 2, d / 2, seat_h])]
    for sx in (-1, 1):
        for sy in (-1, 1):
            x0 = -w / 2 if sx < 0 else w / 2 - leg
            y0 = -d / 2 if sy < 0 else d / 2 - leg
            top = back_h if sy > 0 else seat_h - t
            parts.append(box([x0, y0, 0], [x0 + leg, y0 + leg, top if sy < 0 else seat_h - t]))
    parts.append(box([-w / 2, d / 2 - t, seat_h], [w / 2, d / 2, back_h]))
    return parts


def vase(height=400, sections=64):
    zs = np.linspace(0, height, 25)
    rs = 60 + 40 * np.sin(zs / height * math.pi) + 10 * (zs / height)
    prof = np.column_stack([rs, zs])
    prof = np.vstack([[0, 0], prof, [0, height]])
    return trimesh.creation.revolve(prof, sections=sections)


def lamp_post(h=4000):
    base = cylinder((0, 0), 150, 0, 300, 48)
    pole = cylinder((0, 0), 60, 300, h, 48)
    arm = box([0, -30, h - 300], [800, 30, h - 240])
    head = trimesh.creation.icosphere(subdivisions=2, radius=180)
    head.apply_translation([800, 0, h - 450])
    return [base, pole, arm, head]


def cabinet(w=800, d=450, h=900, t=18):
    parts = [
        box([-w / 2, -d / 2, 0], [-w / 2 + t, d / 2, h]),
        box([w / 2 - t, -d / 2, 0], [w / 2, d / 2, h]),
        box([-w / 2 + t, -d / 2, 0], [w / 2 - t, d / 2, t]),
        box([-w / 2 + t, -d / 2, h - t], [w / 2 - t, d / 2, h]),
        box([-w / 2 + t, d / 2 - t, t], [w / 2 - t, d / 2, h - t]),
        box([-w / 2 + t, -d / 2, h / 2 - t / 2], [w / 2 - t, d / 2 - t, h / 2 + t / 2]),
    ]
    return parts


def shelf_with_holes():
    from shapely.geometry import Point, box as sbox

    poly = sbox(-300, -150, 300, 150).difference(Point(-150, 0).buffer(50, quad_segs=16)).difference(Point(150, 0).buffer(50, quad_segs=16))
    return trimesh.creation.extrude_polygon(poly, 25)


def tilted_plank():
    m = box([-400, -50, -15], [400, 50, 15])
    m.apply_transform(trimesh.transformations.rotation_matrix(math.radians(30), [0, 0, 1]))
    m.apply_transform(trimesh.transformations.rotation_matrix(math.radians(10), [1, 0, 0]))
    return m


def blob(radius=200, seed=1):
    m = trimesh.creation.icosphere(subdivisions=4, radius=radius)
    rng = np.random.default_rng(seed)
    n = m.vertex_normals
    disp = 0.15 * radius * np.sin(m.vertices[:, 0] / 40.0) * np.cos(m.vertices[:, 1] / 55.0)
    m.vertices = m.vertices + n * disp[:, None] + rng.normal(0, 0.5, m.vertices.shape)
    return m


def rounded_table(width=1400, depth=800, height=750, top=30, radius=80, leg=50, inset=60):
    """Mesa com tampo de cantos arredondados (extrusão com retas e arcos)."""
    from shapely.geometry import box as sbox

    poly = sbox(-width / 2 + radius, -depth / 2 + radius, width / 2 - radius, depth / 2 - radius).buffer(radius, quad_segs=8)
    tampo = trimesh.creation.extrude_polygon(poly, top)
    tampo.apply_translation([0, 0, height - top])
    parts = [tampo]
    for sx in (-1, 1):
        for sy in (-1, 1):
            x0 = sx * (width / 2 - inset) - (leg if sx > 0 else 0)
            y0 = sy * (depth / 2 - inset) - (leg if sy > 0 else 0)
            parts.append(box([x0, y0, 0], [x0 + leg, y0 + leg, height - top]))
    return parts

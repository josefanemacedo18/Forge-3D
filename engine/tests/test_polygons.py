import math

import numpy as np
from shapely.geometry import Point, Polygon, box

from forge3d.geometry.polygons import MIN_SEGMENT, axis_rectangle, polygon_to_loops
from forge3d.spec.evaluate import discretize_loop


def _kinds(poly, tol=0.05):
    loops, infos = polygon_to_loops(poly, tol)
    return ["".join("L" if s.kind == "line" else "A" for s in lp) for lp in loops], loops, infos


def _area_error(poly, loops):
    rebuilt = Polygon(discretize_loop([s.to_spec() for s in loops[0]], 2))
    for lp in loops[1:]:
        rebuilt = rebuilt.difference(Polygon(discretize_loop([s.to_spec() for s in lp], 2)))
    return abs(rebuilt.area - poly.area) / poly.area


def _connected(loops):
    return all(np.allclose(lp[i].end, lp[(i + 1) % len(lp)].start) for lp in loops for i in range(len(lp)))


def test_rectangle():
    k, loops, _ = _kinds(box(0, 0, 100, 50))
    assert k == ["LLLL"] and _connected(loops)
    assert axis_rectangle(box(0, 0, 100, 50), 0.05) == (0.0, 0.0, 100.0, 50.0)


def test_circle_becomes_two_arcs():
    k, loops, infos = _kinds(Point(10, 5).buffer(25, quad_segs=24))
    assert k == ["AA"] and infos[0].is_circle
    assert abs(infos[0].circle_radius - 25) < 0.1


def test_rounded_rectangle_lines_and_arcs():
    poly = box(0, 0, 200, 100).buffer(10, quad_segs=8)
    k, loops, _ = _kinds(poly)
    assert k == ["LALALALA"]
    assert _area_error(poly, loops) < 0.002 and _connected(loops)


def test_ring_with_hole():
    poly = Point(0, 0).buffer(50, quad_segs=24).difference(Point(0, 0).buffer(30, quad_segs=24))
    k, loops, infos = _kinds(poly)
    assert k == ["AA", "AA"] and all(i.is_circle for i in infos)


def test_s_curve_keeps_arcs_and_closes():
    t1 = np.linspace(math.pi, 0, 20)
    t2 = np.linspace(math.pi, 2 * math.pi, 20)
    top = np.column_stack([50 + 50 * np.cos(t1), 50 * np.sin(t1)])
    s2 = np.column_stack([150 + 50 * np.cos(t2), 50 * np.sin(t2)])
    poly = Polygon(np.vstack([top, s2[1:], [[200, -100], [0, -100]]]))
    k, loops, _ = _kinds(poly)
    assert "A" in k[0] and _connected(loops) and _area_error(poly, loops) < 0.01


def test_no_segment_shorter_than_revit_limit():
    rng = np.random.default_rng(3)
    ang = np.sort(rng.uniform(0, 2 * math.pi, 400))
    r = 300 + rng.normal(0, 0.2, ang.size)
    poly = Polygon(np.column_stack([r * np.cos(ang), r * np.sin(ang)]))
    loops, _ = polygon_to_loops(poly, 0.05)
    assert min(s.length() for lp in loops for s in lp) >= MIN_SEGMENT - 1e-9

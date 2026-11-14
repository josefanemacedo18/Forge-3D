import numpy as np
import pytest

import synth
from forge3d.geometry.classify import Options, classify_component
from forge3d.geometry.cleanup import split_components, to_trimesh, weld


def classify(mesh):
    v, f = weld(np.asarray(mesh.vertices), np.asarray(mesh.faces), 1e-3)
    comps = split_components(to_trimesh(v, f))
    assert len(comps) == 1
    return classify_component(comps[0], Options())


def kinds(res):
    return sorted(p.kind for p in res.primitives)


def test_box():
    res = classify(synth.box([0, 0, 0], [100, 200, 300]))
    assert kinds(res) == ["box"]


def test_cylinder_from_prism():
    res = classify(synth.cylinder((10, 20), 25, 0, 400))
    assert kinds(res) == ["cylinder"]
    c = res.primitives[0]
    assert abs(c.diameter - 50) < 0.5 and c.axis == "z"


def test_welded_table_becomes_five_boxes():
    res = classify(synth.union(synth.table()))
    assert kinds(res) == ["box"] * 5
    vols = sorted(p.volume for p in res.primitives)
    assert vols[-1] == pytest.approx(1200 * 800 * 30, rel=1e-6)


def test_welded_round_table_becomes_cylinders():
    res = classify(synth.union(synth.round_table()))
    assert kinds(res) == ["cylinder"] * 3


def test_vase_is_revolution():
    res = classify(synth.vase())
    assert kinds(res) == ["revolution"]
    rev = res.primitives[0]
    assert rev.flat_bottom and rev.flat_top


def test_shelf_with_holes_is_extrusion_with_loops():
    res = classify(synth.shelf_with_holes())
    assert kinds(res) == ["extrusion"]
    assert len(res.primitives[0].loops) == 3


def test_tilted_plank_is_framed_extrusion():
    res = classify(synth.tilted_plank())
    assert kinds(res) == ["framed_extrusion"]


def test_blob_is_freeform():
    res = classify(synth.blob())
    assert kinds(res) == ["mesh"]

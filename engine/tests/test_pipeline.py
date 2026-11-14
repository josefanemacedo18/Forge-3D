import copy

import numpy as np
import pytest

import synth
from forge3d.geometry.mesh_io import Part, save_obj
from forge3d.pipeline import ConvertOptions, convert_mesh_file
from forge3d.spec import evaluate, validate

CASES = {
    "mesa": (synth.table, "C"),
    "mesa_soldada": (lambda: [synth.union(synth.table())], "C"),
    "mesa_redonda": (synth.round_table, "C"),
    "cadeira": (synth.chair, "C"),
    "armario": (synth.cabinet, "C"),
    "prateleira": (lambda: [synth.shelf_with_holes()], "C"),
    "vaso": (lambda: [synth.vase()], "B"),
    "poste": (synth.lamp_post, "B"),
    "blob": (lambda: [synth.blob()], "B"),
}


def write(tmp_path, name, meshes, header="# forge3d units=mm up=Z"):
    parts = [Part(f"peca{i}", np.asarray(m.vertices), np.asarray(m.faces)) for i, m in enumerate(meshes)]
    path = tmp_path / f"{name}.obj"
    save_obj(path, parts, header=header)
    return str(path)


@pytest.mark.parametrize("name", list(CASES))
def test_convert_level_c_valid_flexible_and_faithful(tmp_path, name):
    factory, expected_level = CASES[name]
    path = write(tmp_path, name, factory())
    spec = convert_mesh_file(path, ConvertOptions(level="C")).spec
    validate.validate(spec)
    assert spec["family"]["achievedLevel"] == expected_level
    assert spec["report"]["flex"]["ok"], spec["report"]["flex"]["failures"]
    fid = spec["report"]["fidelity"]
    assert fid["p95"] < 1.5, fid
    if expected_level == "B":
        assert spec["report"]["levelCBlockers"], "nível B precisa explicar o motivo"


def test_level_b_is_purely_proportional(tmp_path):
    path = write(tmp_path, "mesa", synth.table())
    spec = convert_mesh_file(path, ConvertOptions(level="B")).spec
    validate.validate(spec)
    roles = {p["role"] for p in spec["parameters"]}
    assert "semantic" not in roles
    names = {p["name"] for p in spec["parameters"]}
    assert {"Largura", "Profundidade", "Altura"} <= names


def test_level_a_has_fixed_geometry(tmp_path):
    path = write(tmp_path, "cadeira", synth.chair())
    spec = convert_mesh_file(path, ConvertOptions(level="A")).spec
    validate.validate(spec)
    assert spec["planes"] == []
    assert all(s["kind"] in ("extrusion", "revolution", "mesh") for s in spec["solids"])


def test_table_semantics(tmp_path):
    path = write(tmp_path, "mesa", synth.table())
    spec = convert_mesh_file(path, ConvertOptions(level="C")).spec
    p = {q["name"]: q for q in spec["parameters"]}
    assert p["Espessura do Tampo"]["value"] == pytest.approx(30)
    assert p["Seção da Perna"]["value"] == pytest.approx(50)
    assert p["Recuo X"]["value"] == pytest.approx(40)
    # Mudar a largura mantém pernas e recuos constantes.
    big = evaluate.build_mesh(spec, {p["Largura"]["id"]: 2000.0})
    assert big.bounds[1][0] - big.bounds[0][0] == pytest.approx(2000, abs=0.01)
    assert p["Largura"]["min"] == pytest.approx(180, abs=1)


def test_units_and_up_axis_detection(tmp_path):
    meshes = []
    for m in synth.chair():
        m = m.copy()
        v = np.asarray(m.vertices) / 1000.0
        m.vertices = v[:, [0, 2, 1]] * np.array([1, 1, -1])
        meshes.append(m)
    path = write(tmp_path, "cadeira_m_yup", meshes, header="# outro programa")
    spec = convert_mesh_file(path, ConvertOptions(level="C")).spec
    assert spec["report"]["source"]["units"] == "m"
    assert spec["report"]["source"]["upAxis"] == "Y"
    assert spec["envelope"]["height"] == pytest.approx(900, abs=0.01)


def test_validator_catches_broken_specs(tmp_path):
    path = write(tmp_path, "mesa", synth.table())
    spec = convert_mesh_file(path, ConvertOptions(level="C", compute_fidelity=False)).spec
    bad = copy.deepcopy(spec)
    bad["planes"][0]["position"] += 7.0
    with pytest.raises(validate.SpecValidationError):
        validate.validate(bad)
    bad = copy.deepcopy(spec)
    bad["planes"][0]["parent"] = bad["planes"][0]["id"]
    with pytest.raises(validate.SpecValidationError):
        validate.validate(bad)
    bad = copy.deepcopy(spec)
    bad["parameters"][-1]["name"] = bad["parameters"][0]["name"]
    with pytest.raises(validate.SpecValidationError):
        validate.validate(bad)


def test_level_a_off_center_revolution_is_valid(tmp_path):
    import trimesh

    v = synth.vase()
    v.apply_translation([700, 0, 0])
    stand = synth.box([-100, -100, 0], [100, 100, 50])
    path = write(tmp_path, "vaso_deslocado", [v, stand])
    spec = convert_mesh_file(path, ConvertOptions(level="A")).spec
    validate.validate(spec)
    revs = [s for s in spec["solids"] if s["kind"] == "revolution"]
    assert revs and abs(revs[0]["center"]["u"]["position"]) > 100
    assert spec["report"]["fidelity"]["p95"] < 1.5


def test_rounded_tabletop_follows_width(tmp_path):
    path = write(tmp_path, "mesa_arredondada", synth.rounded_table())
    spec = convert_mesh_file(path, ConvertOptions(level="C")).spec
    validate.validate(spec)
    assert spec["family"]["achievedLevel"] == "C"
    top = next(s for s in spec["solids"] if s["kind"] == "extrusion")
    assert len(top["alignments"]) == 4  # as quatro retas do contorno travadas
    p = {q["name"]: q for q in spec["parameters"]}
    wide = evaluate.build_mesh(spec, {p["Largura"]["id"]: 2000.0})
    assert wide.bounds[1][0] - wide.bounds[0][0] == pytest.approx(2000, abs=0.5)
    assert spec["report"]["flex"]["ok"]

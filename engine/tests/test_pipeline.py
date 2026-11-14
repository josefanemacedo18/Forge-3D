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
    "vaso": (lambda: [synth.vase()], "C"),
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
    # Faixa segura = o que o Revit aceita (pernas podem se sobrepor; nada se inverte).
    assert 1.0 <= p["Largura"]["min"] < 180
    assert p["Largura"]["max"] is None  # sem limite para crescer
    for q in spec["parameters"]:
        if q.get("min") is not None:
            assert q["min"] >= 1.0, q  # nenhuma cota rotulada abaixo de 1 mm
            assert q["min"] <= q["value"] <= (q["max"] if q.get("max") is not None else q["value"])


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


def test_vase_diameter_is_parametric(tmp_path):
    path = write(tmp_path, "vaso", [synth.vase()])
    spec = convert_mesh_file(path, ConvertOptions(level="C")).spec
    validate.validate(spec)
    rev = next(s for s in spec["solids"] if s["kind"] == "revolution")
    assert len(rev["alignments"]) == 3  # barriga, boca e base travadas
    p = {q["name"]: q for q in spec["parameters"]}
    assert "Diâmetro" in p and "Altura" in p
    big = evaluate.build_mesh(spec, {p["Diâmetro"]["id"]: 300.0, p["Altura"]["id"]: 500.0})
    ext = big.extents
    assert ext[0] == pytest.approx(300, abs=1) and ext[2] == pytest.approx(500, abs=1)


def test_thin_sheets_in_any_direction_are_buildable(tmp_path):
    """Chapas de 0,6 mm (abaixo do mínimo do Revit) em pé e deitadas: viram
    geometria fixa de 1,2 mm, extrudada na direção da espessura."""
    meshes = [
        synth.box([-300, -200, 0], [300, 200, 20]),
        synth.box([-300, -200, 20], [-299.4, 200, 400]),  # fina em X
        synth.box([-300, 199.4, 20], [300, 200, 400]),  # fina em Y
        synth.box([-300, -200, 400], [300, 200, 400.6]),  # fina em Z
        synth.cylinder((0, 0), 0.3, 20, 300, 16),  # arame de 0,6 mm
    ]
    for level in ("C", "A"):
        path = write(tmp_path, f"chapas_{level}", meshes)
        spec = convert_mesh_file(path, ConvertOptions(level=level)).spec
        validate.validate(spec)  # nenhuma curva, espessura ou diâmetro menor que 1 mm
        assert spec["report"]["flex"]["ok"], spec["report"]["flex"]["failures"]
        assert spec["report"]["fidelity"]["p95"] < 1.5


def test_many_small_freeform_parts_are_grouped(tmp_path):
    """Centenas de peças livres (folhas, parafusos) viram conjuntos de detalhes
    por material: a família continua leve no Revit."""
    rng = np.random.default_rng(3)
    meshes = [synth.box([-500, -500, 0], [500, 500, 40])]
    for k in range(90):
        pts = rng.normal(0, 12, (40, 3))
        import trimesh

        m = trimesh.convex.convex_hull(pts)
        m.apply_translation([(k % 10) * 90 - 400, (k // 10) * 90 - 400, 100])
        meshes.append(m)
    path = write(tmp_path, "folhas", meshes)
    spec = convert_mesh_file(path, ConvertOptions(level="C")).spec
    validate.validate(spec)
    meshes_out = [s for s in spec["solids"] if s["kind"] == "mesh"]
    assert len(meshes_out) <= 40
    assert any("agrupadas" in w for w in spec["report"]["warnings"])


def test_pointed_and_tilted_caps_keep_height_parametric(tmp_path):
    """Revolução com ponta (cone) e aro levemente inclinado: a altura continua
    paramétrica (tampa de 2 mm criada/nivelada) e a flexão é válida."""
    import trimesh

    # Perfil (r, h): base plana, parede, ombro e ponta no eixo.
    prof = [(0, 0), (150, 0), (150, 300), (60, 520), (0, 600)]
    cone = trimesh.creation.revolve(np.array(prof, float), sections=64)
    path = write(tmp_path, "pinaculo", [cone])
    spec = convert_mesh_file(path, ConvertOptions(level="C")).spec
    validate.validate(spec)
    rev = next(s for s in spec["solids"] if s["kind"] == "revolution")
    assert rev.get("extent"), "altura deveria ser paramétrica"
    p = {q["name"]: q for q in spec["parameters"]}
    tall = evaluate.build_mesh(spec, {p["Altura"]["id"]: 800.0})
    assert tall.extents[2] == pytest.approx(800, abs=1)
    assert spec["report"]["flex"]["ok"], spec["report"]["flex"]["failures"]


@pytest.mark.parametrize("axis,size,idx", [("z", 1500.0, 2), ("x", 2400.0, 0), ("max", 2400.0, 0)])
def test_real_size_rescales_the_model(tmp_path, axis, size, idx):
    """Medida real informada (modelo baixado em escala errada): a família sai
    com a medida pedida e o aviso de unidade estimada some."""
    path = write(tmp_path, "mesa_escala", synth.table(), header="")  # sem cabeçalho: unidade estimada
    spec = convert_mesh_file(path, ConvertOptions(level="C", target_size=size, target_axis=axis)).spec
    validate.validate(spec)
    env = [spec["envelope"]["width"], spec["envelope"]["depth"], spec["envelope"]["height"]]
    assert env[idx] == pytest.approx(size, abs=0.01)
    assert "escala ajustada" in spec["report"]["source"]["unitsReason"]
    assert not any("Unidade do arquivo estimada" in w for w in spec["report"]["warnings"])
    assert spec["family"]["achievedLevel"] == "C"


def test_implausible_size_for_category_warns(tmp_path):
    """Uma mesa de 12 m em Mobiliário quase sempre é escala errada: o usuário
    é avisado e orientado a informar o tamanho real."""
    big = [m.copy() for m in synth.table()]
    for m in big:
        m.apply_scale(10)
    path = write(tmp_path, "mesa_gigante", big)
    spec = convert_mesh_file(path, ConvertOptions(level="C", category="Furniture")).spec
    assert any("Tamanho incomum para Mobiliário" in w for w in spec["report"]["warnings"])
    spec = convert_mesh_file(path, ConvertOptions(level="C", category="Furniture", target_size=750)).spec
    assert not any("Tamanho incomum" in w for w in spec["report"]["warnings"])
    assert spec["envelope"]["height"] == pytest.approx(750, abs=0.01)

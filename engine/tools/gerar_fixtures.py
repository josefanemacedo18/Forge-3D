"""Gera os FamilySpecs de referência usados nos testes do C# e no comando
Forge 3D › Diagnóstico (que monta cada um deles como família no Revit).

Uso (na pasta engine, com o ambiente .venv ativo):

    python tools/gerar_fixtures.py

Os arquivos vão para src/Forge3D.Core.Tests/Fixtures. Rode de novo sempre que o
motor mudar o formato ou a estratégia de parametrização.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import numpy as np

ENGINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ENGINE))
sys.path.insert(0, str(ENGINE / "tests"))

import synth  # noqa: E402

from forge3d.geometry.mesh_io import Part, save_obj  # noqa: E402
from forge3d.pipeline import ConvertOptions, convert_mesh_file  # noqa: E402
from forge3d.spec import validate  # noqa: E402

OUT = ENGINE.parent / "src" / "Forge3D.Core.Tests" / "Fixtures"


def _thin_sheets():
    return [
        synth.box([-300, -200, 0], [300, 200, 20]),
        synth.box([-300, -200, 20], [-299.4, 200, 400]),
        synth.box([-300, 199.4, 20], [300, 200, 400]),
        synth.box([-300, -200, 400], [300, 200, 400.6]),
    ]


def _pinnacle():
    import trimesh

    prof = [(0, 0), (150, 0), (150, 300), (60, 520), (0, 600)]
    return [trimesh.creation.revolve(np.array(prof, float), sections=64)]


# nome -> (fábrica de malhas, nível pedido)
CASES = {
    "mesa": (synth.table, "C"),
    "cadeira": (synth.chair, "C"),
    "mesa_redonda": (synth.round_table, "C"),
    "prateleira_furos": (lambda: [synth.shelf_with_holes()], "C"),
    "vaso": (lambda: [synth.vase()], "C"),
    "poste": (synth.lamp_post, "C"),
    "tabua_inclinada": (lambda: [synth.tilted_plank()], "C"),
    "forma_livre": (lambda: [synth.blob()], "C"),
    "cadeira_nivel_a": (synth.chair, "A"),
    "mesa_nivel_b": (synth.table, "B"),
    "armario": (synth.cabinet, "C"),
    "mesa_arredondada": (synth.rounded_table, "C"),
    "chapas_finas": (_thin_sheets, "C"),
    "pinaculo": (_pinnacle, "C"),
}


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        for name, (factory, level) in CASES.items():
            meshes = factory()
            parts = [Part(f"peca{i}", np.asarray(m.vertices), np.asarray(m.faces)) for i, m in enumerate(meshes)]
            obj = Path(tmp) / f"{name}.obj"
            save_obj(obj, parts)
            spec = convert_mesh_file(str(obj), ConvertOptions(level=level)).spec
            # Arquivos estáveis: sem a pasta temporária nem os tempos de execução,
            # para que só mudem quando o conteúdo da família mudar.
            spec["report"]["source"]["path"] = obj.name
            spec["report"]["timings"] = {}
            validate.validate(spec)
            if not spec["report"]["flex"]["ok"]:
                print(f"AVISO {name}: flexão com falhas: {spec['report']['flex']['failures'][:2]}")
            (OUT / f"{name}.json").write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
            print(f"{name:18s} pedido {level} -> obtido {spec['family']['achievedLevel']}, "
                  f"{len(spec['solids'])} sólidos, {len(spec['parameters'])} parâmetros")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

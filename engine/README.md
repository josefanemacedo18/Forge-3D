# Motor Forge 3D

Motor Python do Forge 3D. Converte modelos 3D (e imagens) em um **FamilySpec** — o contrato JSON que o plugin do Revit transforma em família paramétrica.

## Desenvolvimento

```bash
uv venv .venv --python 3.12
uv pip install -e ".[dev]"
python -m pytest
```

## Uso pela linha de comando

```bash
forge3d converter modelo.obj --nivel C -o modelo.familyspec.json
```

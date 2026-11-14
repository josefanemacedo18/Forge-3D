# Forge 3D

Plugin para **Revit 2027** que converte modelos 3D (SketchUp, 3ds Max, FBX, OBJ, STL, GLB, STEP, Rhino…) e **fotos de objetos** em **famílias paramétricas do Revit**, com planos de referência, cotas rotuladas, fórmulas e parâmetros em português. Funciona offline.

## Como funciona

```
Arquivo 3D / imagem ──► Motor Forge 3D (Python, local) ──► FamilySpec (JSON) ──► Plugin Revit (C#) ──► Família .rfa
```

1. **Leitura**: SketchUp, STEP, Rhino e SAT pelo próprio Revit; `.max`, `.fbx` e `.3ds` pelo 3ds Max em modo lote; OBJ, STL, PLY, GLB, DAE e 3MF direto no motor.
2. **Decomposição**: cada peça vira caixa, cilindro, extrusão (retas e arcos), revolução ou forma livre. Malhas soldadas são separadas em caixas e fatias.
3. **Planejamento paramétrico**: planos em árvore e parâmetros com significado (Largura, Altura, Espessura do Tampo, Seção da Perna, Recuo X, Diâmetro do Furo…).
4. **Construção no Revit**: planos, cotas, rótulos, fórmulas, faces travadas, materiais e tipos, com **teste de flexão** antes de salvar.

| Nível | Resultado |
|---|---|
| **C** | Parâmetros com significado: espessuras e recuos constantes, alturas próprias, diâmetros |
| **B** | Tudo acompanha o envelope proporcionalmente |
| **A** | Geometria fixa e fiel |

Quando o Nível C não é possível, o plugin **pergunta** se pode gerar em Nível B e mostra o motivo, peça por peça.

**Imagem → família:** revolução pela silhueta (vasos, postes, luminárias), extrusão da silhueta (painéis, recortes) e reconstrução 3D por IA (TripoSR, opcional, roda na GPU NVIDIA ou na CPU).

## Instalação

Baixe o pacote **Forge3D-Revit2027** (artefato da última execução do GitHub Actions), feche o Revit e rode **Instalar.cmd**. Detalhes em [docs/GUIA-DO-USUARIO.md](docs/GUIA-DO-USUARIO.md).

Depois de instalar, rode **Forge 3D › Diagnóstico** no Revit: ele constrói famílias de teste e gera um relatório completo.

## Desenvolvimento

| Pasta | Conteúdo |
|---|---|
| `engine/` | Motor Python (leitura, decomposição, planejamento, imagem, servidor local) |
| `src/` | Plugin do Revit 2027 (C#/.NET 10, WPF) e testes |
| `exporters/` | Exportadores do 3ds Max (MAXScript) e do SketchUp (Ruby) |
| `installer/` | Instalação, desinstalação e montagem do pacote |
| `docs/` | [Guia do usuário](docs/GUIA-DO-USUARIO.md), [Arquitetura](docs/ARQUITETURA.md), [FamilySpec](docs/FAMILYSPEC.md) |

```bash
# Motor
cd engine && uv venv .venv --python 3.12 && uv pip install -r requirements.txt pytest && .venv/bin/python -m pytest
```

```bash
# Plugin e pacote (requer .NET 10 SDK)
pwsh installer/package.ps1
```

## Estado

- **Motor:** 53 testes (inclui a reconstrução real por IA quando `FORGE3D_TEST_AI` está definido). Validado também com 24 modelos reais baixados (cadeiras, sofás, luminárias, vasos, estantes…): todos geram famílias válidas.
- **Núcleo C#:** 61 testes, incluindo integração com o motor real e os 14 FamilySpecs de referência (os mesmos que o **Diagnóstico** monta no Revit; gerados por `engine/tools/gerar_fixtures.py`).
- **Plugin:** compila contra a API do Revit 2027 sem erros nem avisos. A validação dentro do Revit é feita pelo comando **Diagnóstico**.
- **Limites desta versão:** formas livres ficam fixas; contornos sem retas nos extremos (ovais) e revoluções fora do centro mantêm a forma (altura e espessura paramétricas). Detalhes em [docs/ARQUITETURA.md](docs/ARQUITETURA.md).

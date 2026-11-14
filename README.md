# Forge 3D

Plugin para **Revit 2027** que converte modelos 3D (SketchUp, 3ds Max, OBJ, FBX, STL, GLB…) e **imagens** em **famílias paramétricas do Revit**.

## Como funciona

```
Arquivo 3D / imagem ──► Motor Forge 3D (Python, local) ──► FamilySpec (JSON) ──► Plugin Revit (C#) ──► Família .rfa
```

1. **Leitura** — SketchUp e STEP/Rhino/OBJ/STL pelo próprio Revit; `.max`, `.fbx` e `.3ds` pelo 3ds Max em modo lote; GLB, PLY, DAE e 3MF direto pelo motor.
2. **Decomposição** — cada peça é reconhecida como caixa, cilindro, extrusão, revolução ou forma livre (malhas soldadas são decompostas em caixas e fatias).
3. **Planejamento paramétrico** — planos de referência em árvore, cotas rotuladas e fórmulas, com parâmetros em português (Largura, Altura, Espessura do Tampo, Seção da Perna, Recuo X…).
4. **Construção no Revit** — o plugin cria a família com planos, cotas, rótulos, fórmulas, sólidos travados nos planos e materiais, e roda um **teste de flexão** antes de salvar.

## Níveis de parametrização

| Nível | O que significa |
|---|---|
| **C** | Peças reconhecidas com parâmetros com significado: espessuras e recuos constantes, alturas próprias, diâmetros. |
| **B** | Tudo acompanha o envelope (Largura/Profundidade/Altura) proporcionalmente; formas livres ficam fixas. |
| **A** | Geometria fixa, fiel ao original. |

Quando o Nível C não é possível para todo o objeto, o plugin **pergunta** se pode gerar em Nível B, mostrando quais peças impediram o Nível C e por quê.

## Estrutura do repositório

| Pasta | Conteúdo |
|---|---|
| `engine/` | Motor Python: leitura, decomposição, planejamento, imagem → 3D, servidor local |
| `src/` | Plugin do Revit 2027 (C#/.NET 10) |
| `exporters/` | Scripts de exportação do 3ds Max (MAXScript) e do SketchUp (Ruby) |
| `installer/` | Instalação no Windows |
| `docs/` | Arquitetura, formato FamilySpec e guia do usuário |

## Estado

Em desenvolvimento. Veja `docs/` para a arquitetura e o roteiro.

# Forge 3D — Arquitetura

## Visão geral

```
                     ┌──────────────────────── Revit 2027 ────────────────────────┐
 arquivo / imagem ─► │ Plugin (C#, .NET 10, WPF)                                   │
                     │  ├─ Importação: SKP/STEP/3DM/SAT pelo Revit · MAX/FBX/3DS     │
                     │  │   pelo 3ds Max em lote · OBJ/STL/GLB/DAE… direto           │
                     │  ├─ Janelas (prévia 3D, parâmetros, pergunta C → B)           │
                     │  └─ FamilyBuilder: planos, cotas, fórmulas, sólidos,         │
                     │       alinhamentos, materiais, tipos, teste de flexão         │
                     └───────────────▲──────────────────────────┬──────────────────┘
                                     │ JSON-RPC (stdin/stdout)   │ .rfa
                     ┌───────────────┴──────────────┐           ▼
                     │ Motor (Python 3.12, local)     │   Biblioteca local
                     │  ├─ leitura e orientação        │   (Documentos\Forge3D)
                     │  ├─ decomposição em primitivas  │
                     │  ├─ planejador de restrições     │
                     │  ├─ imagem → 3D (silhueta / IA)  │
                     │  └─ validação, avaliação, flexão │
                     └────────────────────────────────┘
                                     │
                                     ▼
                           FamilySpec (JSON, mm)
```

O **FamilySpec** é o contrato entre as duas partes. O motor decide *o que* construir; o plugin só traduz o spec em chamadas da API do Revit. Os dois validam o spec com as mesmas regras, e o motor simula a flexão antes de o Revit ser tocado.

## Motor (`engine/forge3d`)

| Módulo | Função |
|---|---|
| `geometry/mesh_io.py` | Lê OBJ, STL, PLY, OFF, GLB/GLTF, DAE e 3MF preservando partes e cores |
| `geometry/orientation.py` | Unidade (declarada ou estimada pelo tamanho) e eixo vertical (declarado; ou estimado por apoio, peças esbeltas, simetria redonda e convenção do formato) |
| `geometry/cleanup.py` | Solda de vértices, faces degeneradas/duplicadas, componentes conexos, reparo |
| `geometry/classify.py` | Classificação de cada componente (ordem abaixo) |
| `geometry/polygons.py` | Cortes, retas e arcos (crescimento guloso com ajuste de círculo), retângulos e círculos, ortogonalização, limite de 1 mm do Revit |
| `geometry/freeform.py` | Formas livres fechadas (remalhamento volumétrico), simplificação, solda de arestas menores que 1 mm |
| `geometry/decompose.py` | Orquestra: unidades, eixo, enquadramento, componentes, classificação, detalhes |
| `planner/naming.py` | Papéis das peças (tampo, perna, lateral, encosto…) e nomes em português |
| `planner/planner.py` | Planos em árvore, parâmetros semânticos, fórmulas, Níveis A/B/C, relatório |
| `spec/` | JSON Schema, validação semântica, fórmulas, avaliação, flexão, fidelidade, prévia |
| `image/` | Silhueta, revolução/extrusão pela silhueta, IA (TripoSR) com perfis de hardware |
| `server.py` / `cli.py` | Servidor JSON-RPC e linha de comando em português |

### Classificação de um componente fechado

1. Caixa alinhada (o volume é igual ao da caixa envolvente).
2. Prisma ao longo de Z, X ou Y, que vira caixa, cilindro ou extrusão (retas e arcos). Perfis retilíneos não retangulares viram **caixas separadas** (pernas, assento…).
3. Prisma inclinado (eixos da caixa orientada): extrusão em plano inclinado, exata e fixa.
4. Decomposição ortogonal em caixas (sólidos só com faces alinhadas, mesmo soldados).
5. Fatias 2.5D puramente prismáticas (mesas redondas soldadas viram cilindros).
6. Sólido de revolução (teste de simetria de rotação contra a própria malha).
7. Fatias com recursão: dentro de cada fatia, peças separadas são analisadas uma a uma.
8. Caixa aproximada (dentro da tolerância configurada).
9. Forma livre.

### Planejamento (Nível C)

Cada eixo recebe uma árvore de planos de referência; cada plano tem **uma** cota até o pai, rotulada por um parâmetro:

- **Envelope:** `Meia Largura = Largura / 2` (esquerda/direita), `Altura` (topo).
- **Espessura:** peça fina mantém a espessura (`Espessura do Tampo`, `Seção da Perna`).
- **Recuo:** face perto de um plano resolvido mantém a distância (`Recuo X`).
- **Altura própria:** topo de placas horizontais (`Altura do Assento`).
- **Proporcional:** o resto acompanha o envelope (`aux X01 = Largura * n / d`; sem ponto decimal, seguro em qualquer idioma do Revit).

Coordenadas a menos de 1 mm viram um único plano (o Revit não aceita cotas nulas). O motor calcula o **valor mínimo seguro** de cada parâmetro.

## Plugin (`src/`)

| Projeto | Conteúdo |
|---|---|
| `Forge3D.Core` | Modelo do spec, validador, fórmulas, cliente do motor, configurações, biblioteca. Sem dependência do Revit; testado no Linux e no Windows. |
| `Forge3D.Core.Tests` | 38 testes xUnit, incluindo integração com o motor real. |
| `Forge3D.Revit` | Add-in: aplicação, comandos, construtor, importação e janelas WPF. |

### Construção da família (FamilyBuilder)

1. Documento novo a partir do modelo *Modelo genérico* (encontrado em qualquer idioma); categoria aplicada depois.
2. Materiais e parâmetros (grupos: Dimensões, Restrições, Materiais, Dados de identidade).
3. Planos de referência (ordem topológica), com nomes e "É referência" (Esquerda, Direita, Frente, Fundo, Topo).
4. Cotas lineares rotuladas entre cada plano e seu pai (planta para X/Y, elevação frontal para Z).
5. Fórmulas.
6. Sólidos: extrusões (caixas, cilindros, perfis), revoluções, extrusões em plano livre, formas livres (sólido de forma livre; alternativa: forma direta).
7. Restrições: faces travadas nos planos; diâmetro por cota rotulada (no esboço; alternativa: na aresta); centro por cota até a marca de centro.
8. Recortes de vazios, materiais associados, visibilidade de detalhes, tipos.
9. Conferência do envelope e teste de flexão no Revit (valores menores e maiores; o Revit regenera e processa as falhas; a geometria é medida).

**Cada operação arriscada roda em transação própria** com coletor de falhas: se o Revit recusar, só aquela operação é desfeita e o relatório registra o motivo. A família nunca fica pela metade e o nível efetivo é informado com honestidade.

## Testes

| Onde | O quê |
|---|---|
| `engine/tests` (44) | Fórmulas, perfis, classificação, planejamento, Níveis A/B/C, unidades e eixo, validador, servidor (protocolo, cancelamento), imagem, IA real (opcional) |
| `src/Forge3D.Core.Tests` (38) | Fórmulas, specs reais do motor, árvore de planos, validação, cliente do motor com o Python real |
| Revit (comando **Diagnóstico**) | Constrói 11 famílias de teste no Revit do usuário, roda a flexão e gera relatório |
| GitHub Actions | Motor e núcleo C# no Windows, compilação e pacote de instalação |

## Decisões e limites conhecidos

- **Revoluções:** a altura é paramétrica (faces de topo e base travadas); os raios do perfil ficam fixos nesta versão.
- **Formas livres:** geometria fiel e fixa. Não acompanham o envelope.
- **Extrusões com curvas:** espessura/altura paramétricas, contorno fixo (contornos retos são totalmente paramétricos).
- **Malhas soldadas complexas:** podem gerar fatias em vez de peças inteiras; arquivos com objetos separados dão os melhores resultados.
- **IA 3D:** resultados aproximados (forma livre). Para objetos redondos, a revolução pela silhueta é melhor e é a escolha automática.

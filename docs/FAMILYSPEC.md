# FamilySpec v1

Contrato JSON entre o motor e o plugin. Esquema completo: [`engine/forge3d/spec/familyspec.schema.json`](../engine/forge3d/spec/familyspec.schema.json).

**Convenções:** comprimentos em milímetros. X = largura (esquerda → direita), Y = profundidade (frente → fundo), Z = altura. A origem fica no centro do envelope em planta, com a base em Z = 0. Planos raiz implícitos: `@x0` (centro esquerda/direita), `@y0` (centro frente/fundo) e `@z0` (nível de referência).

## Estrutura

```jsonc
{
  "schema": "forge3d.familyspec",
  "version": 1,
  "family": { "name": "Mesa", "category": "Furniture", "requestedLevel": "C", "achievedLevel": "C" },
  "units": "mm",
  "envelope": { "width": 1200, "depth": 800, "height": 750 },
  "parameters": [ /* ver abaixo */ ],
  "planes":     [ /* árvore de planos */ ],
  "materials":  [ { "id": "mat_1", "name": "Forge3D Madeira", "color": [150, 111, 51] } ],
  "solids":     [ /* box, cylinder, extrusion, revolution, mesh */ ],
  "types":      [ { "name": "1200 x 800 x 750", "values": { "largura": 1200 } } ],
  "report":     { /* nível, cobertura, peças, avisos, motivos do Nível C, flexão, fidelidade */ }
}
```

## Parâmetros

```json
{ "id": "espessura_do_tampo", "name": "Espessura do Tampo", "kind": "length", "group": "dimensions",
  "instance": false, "value": 30, "formula": null, "role": "semantic", "min": 0.5 }
```

- `kind`: `length`, `angle`, `number`, `integer`, `yesno`, `material`, `text`.
- `role`: `envelope` e `semantic` são editáveis pelo usuário; `auxiliary` é calculado por fórmula; `material` e `info` completam a família.
- `formula`: sintaxe do Revit usando os **nomes** dos parâmetros, com `+ - * /`, parênteses e números sem unidade. O motor não usa ponto decimal: frações viram `n / d`.

## Planos

```json
{ "id": "x_02", "axis": "x", "position": -560, "parent": "x_01", "driver": "recuo_x", "name": "Forge X02", "reference": "none" }
```

Regra: `|position − posição do pai| = valor do driver`. O lado em relação ao pai é o da geometria original. Cada plano tem exatamente um pai, e a árvore não tem ciclos.

## Sólidos

| `kind` | Campos |
|---|---|
| `box` | `x`, `y`, `z`: pares de planos (faces travadas) |
| `cylinder` | `axis`, `center.u/v` (âncoras), `diameter {value, driver}`, `extent` (planos das tampas) |
| `extrusion` (alinhada) | `axis`, `loops` (perfil u,v), `extent`, `alignments` (segmento reto ↔ plano) |
| `extrusion` (livre) | `frame {origin, xdir, ydir}`, `loops`, `depth` (geometria fixa) |
| `revolution` | `axis`, `center`, `profile` (r, h), `range`, `extent` opcional |
| `mesh` | `vertices`, `faces` (forma livre fechada) |

Campos comuns: `id`, `name`, `material` (id do parâmetro de material), `isVoid` (vazio que recorta), `visibility` (`all`, `medium_fine`, `fine`), `part` (peça de origem).

**Âncora** (centro de cilindro/revolução): `{position, parent, driver}`. Com `driver`, acompanha o plano pai; sem `driver`, fica fixa na posição absoluta.

**Segmentos** de perfil: `{"type": "line", "start", "end"}` ou `{"type": "arc", "start", "end", "mid"}` (arco por 3 pontos, no máximo 180°). Nenhum segmento menor que 1 mm, que é o limite do Revit.

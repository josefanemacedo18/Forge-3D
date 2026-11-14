# Forge 3D — Guia do usuário

O Forge 3D é um plugin para o **Revit 2027** que transforma modelos 3D e fotos de objetos em **famílias paramétricas do Revit**, com planos de referência, cotas, fórmulas e parâmetros em português.

## 1. Requisitos

| Item | Necessário |
|---|---|
| Sistema | Windows 10 ou 11 (64 bits) |
| Revit | Revit 2027 |
| Internet | Não é necessária. Só o módulo de IA (opcional) baixa arquivos, uma única vez. |
| GPU NVIDIA | Opcional. Acelera a reconstrução 3D por IA; sem GPU, a IA roda na CPU. |
| 3ds Max | Opcional. Necessário só para abrir `.max`, `.fbx` e `.3ds`. |
| SketchUp | Opcional. O Revit já lê `.skp`; o SketchUp só é usado se o arquivo for de uma versão que o Revit não lê. |

## 2. Instalação (rápida, sem downloads)

1. Baixe o pacote **Forge3D-Revit2027** (artefato da compilação do GitHub Actions) e **extraia** a pasta.
2. **Feche o Revit.**
3. Dê dois cliques em **Instalar.cmd**. Em poucos segundos aparece "Forge 3D instalado" e a janela fecha sozinha.
4. Abra o Revit 2027: a aba **Forge 3D** já está pronta para uso.

O pacote já traz o motor completo (um Python próprio e isolado, com todas as bibliotecas instaladas), então nada é baixado e não é preciso ser administrador. O plugin vai para `%APPDATA%\Autodesk\Revit\Addins\2027`, o motor para `%LOCALAPPDATA%\Forge3D` e a configuração para `%APPDATA%\Forge3D\config.json`.

Ao abrir o Revit, o motor é iniciado em segundo plano. A primeira conversão já encontra tudo carregado. Para desligar, use **Configurações › Deixar o motor pronto ao abrir o Revit**.

Para **atualizar**, rode o Instalar.cmd da versão nova. As configurações, as famílias geradas e o módulo de IA são mantidos.

### Módulo de IA 3D (opcional)

A reconstrução 3D por IA, para fotos de objetos sem simetria, e a remoção de fundo por IA ficam num módulo à parte. Para instalar, feche o Revit e dê dois cliques em **InstalarIA.cmd**. Ele baixa cerca de 4 GB uma única vez e depois funciona offline. Sem esse módulo, a geração por imagem continua funcionando para objetos simétricos (revolução) e para silhuetas (extrusão).

### Primeiro uso: Diagnóstico

Abra o Revit 2027, crie ou abra qualquer projeto e clique em **Forge 3D › Diagnóstico**. O Forge 3D vai:

- verificar o motor, a GPU e o módulo de IA;
- construir famílias de teste que usam todas as técnicas (planos, cotas rotuladas, fórmulas, faces travadas, diâmetros, vazios, revoluções, formas livres);
- rodar o teste de flexão em cada uma.

O relatório fica em `Documentos\Forge3D\Diagnostico_<data>.txt`. Se algo falhar, esse arquivo mostra exatamente o quê.

## 3. Converter um modelo 3D

**Forge 3D › Modelo 3D**

1. Arraste o arquivo para a janela ou clique em **Procurar…**
2. Escolha o **nível** (padrão: C), a **categoria** (Mobiliário, Luminárias, Vegetação…) e o **nome da família**.
3. **Unidade** e **eixo vertical** ficam em "Automático". O Forge 3D lê o que o arquivo informa ou deduz pelo tamanho e pela forma do objeto. Se a prévia mostrar o objeto deitado ou com tamanho errado, ajuste aqui e analise de novo.
   - **Tamanho real (mm)** — opcional. Modelos baixados da internet muitas vezes vêm em escala arbitrária (uma cadeira de 40 m, um vaso de 9 mm). Informe uma medida que você conhece (por exemplo, *Altura* = 750 para uma mesa) e o modelo é escalado por igual para bater com ela. Deixe vazio para usar a unidade do arquivo. Se a maior medida não combinar com a categoria escolhida (por exemplo, um móvel com mais de 6 m), a prévia avisa.
4. Clique em **Analisar**. Aparecem a prévia 3D e a lista de peças e parâmetros.
5. Clique em **Criar família**.

### Formatos aceitos

| Formato | Como é lido |
|---|---|
| `.skp` (SketchUp) | Pelo conversor do próprio Revit; se a versão for muito nova, pelo SketchUp instalado |
| `.max`, `.fbx`, `.3ds` | Pelo 3ds Max em modo lote (sem abrir a interface) |
| `.stp`/`.step`, `.3dm` (Rhino), `.sat` | Pelo conversor do Revit |
| `.obj`, `.stl`, `.ply`, `.off`, `.glb`/`.gltf`, `.dae`, `.3mf` | Direto pelo motor |

### Cores da prévia

- **Verde** — peça em Nível C (parâmetros com significado).
- **Âmbar** — peça em Nível B (forma fixa ou proporcional).
- **Cinza** — forma livre (geometria fiel, fixa).

Na prévia, arraste para girar, use a roda do mouse para zoom e dê um duplo clique para reenquadrar.

## 4. Níveis de parametrização

| Nível | O que você recebe | Exemplo (mesa) |
|---|---|---|
| **C** | Parâmetros com significado: espessuras e recuos constantes, alturas próprias, diâmetros. Mudar a largura não deforma as pernas. | Largura, Profundidade, Altura, Espessura do Tampo, Seção da Perna, Recuo X, Recuo Y |
| **B** | Tudo acompanha o envelope proporcionalmente. | Largura, Profundidade, Altura |
| **A** | Geometria fixa e fiel ao original. | — |

### Quando o Nível C não é possível

Se alguma peça não puder ser parametrizada em Nível C (por exemplo, uma almofada orgânica ou uma escultura), o Forge 3D **avisa e pergunta** se pode gerar em Nível B, listando quais peças impediram e por quê. As peças que alcançaram o Nível C continuam com seus parâmetros.

Você pode desligar essa pergunta em **Configurações**.

### O que vira parâmetro

O Forge 3D reconhece a função de cada peça pela geometria e dá nomes em português:

- **Placas horizontais:** Tampo, Base, Prateleira, Assento → *Espessura do Tampo*, *Altura do Assento*…
- **Pernas e hastes:** *Seção da Perna* (quadrada) ou *Diâmetro da Perna* (redonda), *Diâmetro da Haste*.
- **Painéis:** Lateral, Fundo, Frente, Divisória, Encosto → *Espessura da Lateral*…
- **Recuos:** distância constante até a borda → *Recuo X*, *Recuo Y*, *Recuo Superior*.
- **Furos redondos:** viram vazios paramétricos → *Diâmetro do Furo*.
- **Peças torneadas (vasos, luminárias, colunas):** *Diâmetro* e *Altura*; a boca e a base acompanham o diâmetro proporcionalmente.
- **Tampos com cantos arredondados:** acompanham Largura e Profundidade (os cantos seguem as bordas retas).
- **Planta redonda:** *Diâmetro* no lugar de Largura e Profundidade.

Cada parâmetro tem uma **faixa segura** (mínimo e, quando existe, máximo), mostrada na prévia. Fora dela, o Revit recusaria a geometria: alguma peça se inverteria, teria o perfil cruzado ou ficaria com aresta ou cota menor que 1 mm.

Se travar uma peça impedir até uma variação pequena (±10%) de um parâmetro, o Forge 3D deixa o contorno, os raios ou a altura dessa peça fixos, em vez de criar um parâmetro que quebraria a família. A observação da peça explica o motivo.

### Ajustes automáticos para o Revit

O Revit não aceita arestas menores que ~0,8 mm. Para que a família sempre possa ser criada, o Forge 3D:

- **Engrossa chapas e arames finos** (menos de 1,2 mm) para 1,2 mm. Essas peças ficam com geometria fixa.
- **Agrupa detalhes**: quando o modelo tem mais de 40 peças de forma livre pequenas (parafusos, folhas, frisos), elas viram um conjunto de *Detalhes* por material. A família continua leve.
- **Nivela o aro levemente inclinado** de vasos e peças torneadas, e cria uma tampa plana de 2 mm em **pontas** (cones, pináculos). Assim a altura pode ser paramétrica. A diferença de forma é imperceptível.

Cada ajuste aparece nas observações da peça, na prévia e no relatório.

## 5. Gerar família a partir de uma imagem

**Forge 3D › Imagem**

1. Escolha a foto do objeto. O ideal é **fundo liso** (branco ou de cor uniforme) ou **PNG com transparência**, com o objeto inteiro visível e margem nas bordas.
2. O Forge 3D separa o objeto do fundo e sugere uma estratégia.
3. Informe uma **medida real** (altura ou largura do objeto, em mm). Sem ela, a escala é um palpite.
4. Clique em **Gerar**, confira a prévia e clique em **Criar família**.

### Estratégias

| Estratégia | Quando usar | Precisa de GPU? |
|---|---|---|
| **Revolução** | Objetos redondos: vasos, postes, luminárias, colunas, banquetas, mesas de pé central | Não |
| **Extrusão** | Objetos planos ou vistos de frente: painéis, placas, recortes, grades, letreiros (informe a profundidade) | Não |
| **Reconstrução 3D por IA** | Qualquer objeto, quando a forma completa importa | Não (GPU acelera) |
| **Automática** | Escolhe pela forma da silhueta: revolução para silhuetas simétricas e cheias; IA (se instalada) ou extrusão nas demais | — |

Silhuetas de móveis retos (cadeira de frente, estante) viram caixas separadas em Nível C.

A IA roda no seu computador, sem internet. Numa CPU de notebook leva cerca de 30 segundos a alguns minutos por imagem. Com GPU NVIDIA o Forge 3D escolhe sozinho o perfil adequado à memória da placa e, se faltar memória de vídeo, continua na CPU automaticamente.

## 6. Depois de criar a família

A janela de resultado mostra:

- o **nível efetivo** no Revit. Se alguma restrição não pôde ser criada, ele pode ficar abaixo do previsto, e o relatório explica;
- a **conferência do envelope**, que diz se as dimensões batem com o modelo;
- o **teste de flexão**: cada parâmetro foi variado para mais e para menos, e o Revit regenerou sem erros.

Botões: **Inserir no projeto** (carrega e inicia a inserção), **Abrir pasta**, **Fechar**.

Cada família fica numa pasta própria em `Documentos\Forge3D\Familias` com o `.rfa`, o relatório e o spec usado.

## 7. Biblioteca

**Forge 3D › Biblioteca** lista as famílias geradas, com busca por nome, categoria ou arquivo de origem. Dê um duplo clique para inserir no projeto aberto.

## 8. Configurações

**Forge 3D › Configurações**

- Nível e categoria padrão, pergunta antes do Nível B, teste de flexão e inserção ao final.
- Tolerância de aproximação. Exemplo: 1 mm aceita uma caixa com chanfro de até 1 mm como caixa paramétrica.
- Máximo de triângulos em formas livres (padrão 15.000, para manter o Revit leve).
- Pastas: famílias geradas, modelo de família (`.rft`), motor, Python, modelos de IA, 3ds Max e SketchUp. Deixe vazio para detecção automática.
- **Testar motor**: mostra Python, GPU, memória e se a IA está pronta.

## 9. Solução de problemas

| Sintoma | O que fazer |
|---|---|
| "Python do motor não encontrado" | Rode o **Instalar.cmd** de novo. |
| "Modelo de família não encontrado" | Indique o arquivo *Modelo genérico métrico.rft* (ou *Metric Generic Model.rft*) da sua instalação do Revit. A escolha fica salva. |
| SketchUp não abre | Salve o arquivo numa versão anterior do SketchUp ou exporte em OBJ/DAE. |
| `.max` não abre | Confira o caminho do `3dsmaxbatch.exe` em Configurações. |
| Objeto aparece deitado | Troque o **Eixo vertical** (Z ou Y) e analise de novo. |
| Tamanho errado | Troque a **Unidade** (mm, cm, m…) ou informe o **Tamanho real** de uma medida conhecida e analise de novo. |
| "O motor já está processando outra conversão" | Aguarde a análise atual terminar (ou clique em **Cancelar análise**). |
| Nível efetivo menor que o previsto | Veja o relatório: ele lista cada cota, fórmula e alinhamento que o Revit recusou. |
| Algo inesperado | Envie o relatório do **Diagnóstico** e os registros em `%LOCALAPPDATA%\Forge3D\logs`. |

## 10. Desinstalação

Feche o Revit e dê dois cliques em **Desinstalar.cmd**. As famílias geradas em `Documentos\Forge3D` nunca são apagadas.

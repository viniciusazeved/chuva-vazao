# chuva_vazao

Pipeline chuva→vazão para projetos de drenagem urbana e hidrologia de pequenas bacias: parte de coeficientes IDF regionais pré-ajustados e conduz o cálculo até o hidrograma de projeto, passando por desagregação temporal e geração de hietograma.

App irmã do [IDF-generator](https://idf-generator.streamlit.app) (ajuste estatístico de IDF a partir de dados ANA). O IDF-generator continua dono da etapa de estimar K/a/b/c a partir de séries diárias; o chuva_vazao assume daí em diante.

## Fonte dos dados

O catálogo de postos, coeficientes IDF regionais e betas de desagregação vêm do banco de dados distribuído com o **HidroFlu v2.0** (UFRJ/COPPE, 2007), software freeware acadêmico hoje descontinuado. Os coeficientes originais foram compilados de publicações brasileiras clássicas — entre elas DENARDIN & FREITAS (1982), PFAFSTETTER (1957) e SILVA et al. (2002) — e consolidados no `.mdb` distribuído com o instalador.

Créditos originais pertencem aos autores das publicações fonte e à equipe do HidroFlu. O banco é redistribuído aqui apenas como referência técnica para reprodução do pipeline em Python.

## Status

**Pipeline hidrológico-hidráulico completo. 6 páginas, 65 testes passando.**

Cobertura atual (refletindo o `HIDROAPP_BRIEFING.md`):

| Página | Módulo backend | Função |
|---|---|---|
| 0. Bacia | `basin.py` | Delineamento automático via WhiteboxTools + DEM (Copernicus via OpenTopography ou upload local) |
| 1. Posto e IDF | `db.py`, `idf.py` | HidroFlu catalog (8 IDF + 98 Pfafstetter) + upload TXT/CSV do IDF-generator + entrada manual |
| 2. Hietograma | `hietograma.py` | Blocos alternados (Chicago) + Huff 1º-4º quartil |
| 3. Chuva-Vazão | `hidrograma.py`, `tempo_concentracao.py` | Racional (A≤2 km²) ou SCS-HU (A>2) com auto-seleção; tc via Kirpich/Chow/California, pré-preenchido pela bacia |
| 4. Hidráulica | `hidraulica.py` | Manning circular/retangular, dimensionamento com diâmetros comerciais, validação de velocidade |
| 5. Detenção | `detencao.py` | Reservatório prismático + orifício + vertedor, Puls modificado |
| 6. Exportar | `report.py`, `plots.py` | PDF técnico + CSVs |

Cenário de referência validado (Bangu RJ, TR=10, D=60min, A=10 km², CN=75):
- Hietograma: 61.2 mm total (conservação OK)
- Q_pico SCS-HU: 42.3 m³/s; volume 151 k m³ (conservação <1%)
- Dimensionamento: manilha típica ~1.5 m para S=1% (material concreto liso)
- Detenção amplia atenuação >30% com reservatório de 5000 m²

Pendências (evoluções):
- **Validar Pfafstetter contra HidroFlu.exe** — forma exata da equação com a/b/c ainda não usada nos cálculos (tabela disponível no catálogo, aplicação não).
- `scripts/validate_vs_hidroflu.py` para regressão numérica.
- Geocodificação dos postos HidroFlu (para exibir no mapa).
- Deploy Streamlit Cloud.
- **Diagnóstico opcional com série histórica ANA (v2)**: IDF continua sendo o input canônico do SCS-HU (dimensionamento exige evento com TR explícito). Em bacias com monitoramento fluviométrico, permitir carregar série pluviométrica do posto ANA mais próximo, rodar SCS-HU sobre um evento histórico conhecido e comparar com a vazão medida — sanity check do CN escolhido, não substitui IDF.

### Pré-requisitos da Página 0 (Bacia)

- `whitebox` baixa binário ~60 MB na primeira execução.
- **Conflito PROJ Windows**: se você tem PostgreSQL/PostGIS instalado, o `PROJ_LIB` do sistema pode colidir com o rasterio. O `basin.py` já redireciona `PROJ_DATA`/`PROJ_LIB`/`GDAL_DATA` para o bundle do rasterio automaticamente.
- DEM: upload local (qualquer CRS) OU download via OpenTopography (precisa API key gratuita em portal.opentopography.org; defina `OPENTOPO_API_KEY` no `.env`).

## Esquema real extraído

O banco do HidroFlu é mais enxuto do que presumíamos no plano macro (que esperava tabelas `Beta5min`/`Beta15min`/etc. separadas). Na prática, são 3 tabelas:

### `estados_brasil` (26 linhas)

Apenas a sigla de cada UF brasileira (sem DF). Serve como catálogo de filtro.

| Coluna | Tipo |
|---|---|
| `estado` | `TEXT` (sigla UF) |

### `postos_idf_coeficientes` (8 linhas, **todos RJ**)

Coeficientes IDF com formulação clássica `i = K · Tr^a / (t + c)^b` — 8 postos da Região Metropolitana do RJ (Bangu, Benfica, Campo Grande, Capela Mayrink, Jardim Botânico, Mendanha, Saboia Lima, Santa Cruz).

| Coluna | Tipo |
|---|---|
| `descricao` | `TEXT` |
| `estado` | `TEXT` |
| `k` | `REAL` (intensidade base, mm/h) |
| `a` | `REAL` (expoente TR) |
| `b` | `REAL` (expoente duração) |
| `c` | `REAL` (constante duração, min) |

### `postos_pfafstetter_coeficientes` (98 linhas, 23 estados)

Coeficientes IDF regionais estilo Pfafstetter (1957) + betas de desagregação embutidos como colunas. Sem coluna `K` — a formulação Pfafstetter usa a, b, c de forma distinta e derivacao via betas regionais.

| Coluna | Tipo |
|---|---|
| `descricao` | `TEXT` |
| `estado` | `TEXT` |
| `a` | `REAL` |
| `b` | `REAL` |
| `c` | `REAL` |
| `beta5min` | `REAL` (fator desagregação 24h → 5min) |
| `beta15min` | `REAL` |
| `beta30min` | `REAL` |
| `beta1h_6dias` | `REAL` |

Distribuição por estado (total 98):

| UF | Postos | UF | Postos | UF | Postos |
|---|---|---|---|---|---|
| RJ | 21 | PR | 4 | MT | 1 |
| RS | 14 | MA | 3 | PI | 1 |
| SP | 14 | CE | 3 | RN | 1 |
| MG | 9 | GO | 3 | RO | 1 |
| AM | 4 | SC | 3 | BA | 1 |
| PA | 4 | PE | 3 | ES | 1 |
| AC | 2 | AL | 1 | SE | 1 |
| PB | 2 | MS | 1 |  |  |

Estados sem cobertura no HidroFlu: AP, DF, RR, TO.

## Desvios em relação ao plano macro

O plano em `C:\Users\vinic\.claude\plans\claude-agora-que-j-serialized-papert.md` presumia tabelas `Beta5min`/`Beta15min`/`Beta30min`/`Beta1h_6dias` separadas e maior volume. Na prática:

- Não há tabelas de betas separadas. Os betas são **colunas** de `postos_pfafstetter_coeficientes`.
- Não existe tabela `Postos` ou `Coeficientes` genérica no MDB: o catálogo de postos é implícito (união do `DISTINCT descricao` das duas tabelas de coeficientes).
- As duas tabelas de coeficientes usam **formulações IDF distintas** — clássica com K em uma, Pfafstetter com betas na outra. A Fase 2 vai precisar implementar os dois caminhos de cálculo (e o manual do HidroFlu `.hlp` deve confirmar a forma exata das equações).

Esses achados simplificam o modelo de dados e já estão refletidos no `schema_inspection.md`.

## Como reproduzir a extração

Pré-requisitos:
- Windows com driver **Microsoft Access Driver (\*.mdb, \*.accdb)** instalado (vem com Office/Access ou com o Access Database Engine Redistributable).
- [uv](https://docs.astral.sh/uv/) ≥ 0.9 e Python 3.12+.

Passos:

```bash
cd D:\Projetos\chuva_vazao
uv sync
uv run python scripts/extract_mdb.py --force
```

O script gera:
- `data/chuvavazao.db` — SQLite com as 3 tabelas, nomes normalizados para snake_case ASCII.
- `schema_inspection.md` — relatório com colunas, tipos, contagem de linhas e amostra dos 3 primeiros registros de cada tabela.

**Observação sobre acentos**: o script configura `pyodbc.setdecoding(..., encoding="cp1252")` para preservar os acentos dos nomes de postos brasileiros (Petrópolis, Caxambú, Jacarepaguá etc.). O SQLite armazena em UTF-8; se o seu terminal Windows mostrar `�` no lugar dos acentos, é apenas issue de display — os dados em disco estão corretos. Teste com `uv run python -c "import sqlite3; [print(r[0]) for r in sqlite3.connect('data/chuvavazao.db').execute('SELECT descricao FROM postos_pfafstetter_coeficientes WHERE estado=\"RJ\"')]"` redirecionando para UTF-8, ou abra o `.db` no DB Browser for SQLite.

## Como rodar o app

```bash
cd D:\Projetos\chuva_vazao
uv sync
uv run streamlit run chuva_vazao/app.py
```

Fluxo: **1. Posto e IDF** (escolhe UF→posto ou sobe CSV do IDF-generator) → **2. Hietograma** (TR, duração, dt, método blocos/Huff) → **3. Hidrograma** (área, tc, CN) → **4. Exportar** (PDF técnico + CSVs).

## Rodar testes e demo

```bash
uv run pytest tests/ -v             # 35 testes
uv run python scripts/demo_pipeline.py   # gera out/*.html + out/*.pdf para Santa Cruz RJ
```

## Estrutura do projeto

```
chuva_vazao/
├── data/                            # MDB original (gitignored) + SQLite extraído
├── chuva_vazao/                     # pacote Python
│   ├── db.py                        # acesso SQLite
│   ├── idf.py                       # i = K * TR^a / (t + c)^b + parser TXT/CSV IDF-generator
│   ├── desagregacao.py              # Pfafstetter (regional) + fallback DNAEE
│   ├── hietograma.py                # blocos alternados (Chicago) + Huff 1-4 quartis
│   ├── hidrograma.py                # Racional + SCS-CN + UH triangular + select_method
│   ├── tempo_concentracao.py        # Kirpich, Ven Te Chow, California
│   ├── hidraulica.py                # Manning circular/retangular + dimensionamento
│   ├── detencao.py                  # Reservatório + Puls modificado
│   ├── basin.py                     # WhiteboxTools + OpenTopography (delineamento)
│   ├── plots.py                     # Plotly
│   ├── report.py                    # PDF técnico FPDF2
│   ├── app.py                       # entrypoint Streamlit
│   └── app_pages/                   # 7 páginas do app (0-Bacia a 6-Exportar)
├── scripts/
│   ├── extract_mdb.py               # MDB -> SQLite
│   └── demo_pipeline.py             # end-to-end CLI
├── tests/                           # 65 testes pytest
│   ├── test_db.py
│   ├── test_idf.py
│   ├── test_desagregacao.py
│   ├── test_hietograma.py
│   ├── test_hidrograma.py
│   ├── test_racional.py
│   ├── test_tempo_concentracao.py
│   ├── test_hidraulica.py
│   └── test_detencao.py
├── schema_inspection.md
├── HIDROAPP_BRIEFING.md             # briefing original do escopo expandido
└── README.md
```

## Licença e atribuição

- Este projeto: MIT (TBD).
- Banco HidroFlu v2.0: freeware acadêmico UFRJ/COPPE. Os coeficientes carregam as atribuições originais das publicações fonte (Pfafstetter 1957, Denardin & Freitas 1982, Silva et al. 2002, entre outras).

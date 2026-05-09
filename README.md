# Chuva - Vazão

Pipeline chuva → vazão para projetos de drenagem urbana, fluvial e infraestrutura hidráulica em pequenas bacias. Recebe coeficientes IDF (K, a, b, c) e conduz o cálculo até o hidrograma de projeto, dimensionamento hidráulico de condutos e roteamento de reservatório de detenção.

App publicado em **[chuva-vazao.streamlit.app](https://chuva-vazao.streamlit.app)**.

App irmã do [IDF-generator](https://idf-generator.streamlit.app) (ajuste estatístico de IDF a partir de dados ANA). O IDF-generator continua dono da etapa de estimar K/a/b/c a partir de séries diárias; o Chuva - Vazão consome esses coeficientes e segue daí.

## Páginas

| Página | Módulo backend | Função |
|---|---|---|
| 0. Bacia | `basin.py` | Delineamento automático via WhiteboxTools + DEM (Copernicus via OpenTopography ou upload local) |
| 1. Posto e IDF | `idf.py` | Upload TXT/CSV do IDF-generator + entrada manual |
| 2. Hietograma | `hietograma.py` | Blocos alternados (Chicago) + Huff 1º-4º quartil |
| 3. Chuva-Vazão | `hidrograma.py`, `tempo_concentracao.py` | Racional (A≤2 km²) ou SCS-HU (A>2) com auto-seleção; tc via Kirpich/Chow/California, pré-preenchido pela bacia |
| 4. Verificação de Seção | `secao_natural.py`, `hidraulica.py` | Manning em seções naturais — regime uniforme em três seções |
| 5. Hidráulica | `hidraulica.py` | Manning circular/retangular, dimensionamento com diâmetros comerciais |
| 6. Detenção | `detencao.py`, `reservatorio_dem.py` | Reservatório prismático / curva manual / mancha de inundação via DEM (GEE), Puls modificado |
| 7. Exportar | `report.py` | Relatório PDF técnico modular (só os módulos rodados) + CSVs |

## Convenção IDF

```
i = K · TR^a / (t + b)^c
```

Com `i` em mm/h, `TR` em anos, `t` em min. Mesma convenção do IDF-generator. A página 1 aceita o TXT exportado pelo IDF-generator (`K = ...`, `a = ...`, ...) ou um CSV com colunas K, a, b, c.

## Como rodar

```bash
cd D:\Projetos\chuva_vazao
uv sync
uv run streamlit run chuva_vazao/app.py
```

## Testes

```bash
uv run pytest tests/ -v
```

## Pré-requisitos da Página 0 (Bacia)

- `whitebox` baixa binário ~60 MB na primeira execução.
- **Conflito PROJ Windows**: se você tem PostgreSQL/PostGIS instalado, o `PROJ_LIB` do sistema pode colidir com o rasterio. O `basin.py` já redireciona `PROJ_DATA`/`PROJ_LIB`/`GDAL_DATA` para o bundle do rasterio automaticamente.
- DEM: upload local (qualquer CRS) OU download via OpenTopography (precisa API key gratuita em portal.opentopography.org; defina `OPENTOPO_API_KEY` no `.env`).

## Estrutura

```
chuva_vazao/
├── chuva_vazao/                       # pacote Python
│   ├── idf.py                         # IDF + parser TXT/CSV IDF-generator
│   ├── desagregacao.py                # tabela DNAEE (CETESB/Tucci)
│   ├── hietograma.py                  # blocos alternados + Huff
│   ├── hidrograma.py                  # Racional + SCS-CN + UH triangular
│   ├── tempo_concentracao.py          # Kirpich, Ven Te Chow, California
│   ├── hidraulica.py                  # Manning circular/retangular
│   ├── secao_natural.py               # seções naturais (regime uniforme)
│   ├── detencao.py                    # reservatório + Puls modificado
│   ├── reservatorio_dem.py            # mancha de inundação via DEM/GEE
│   ├── basin.py                       # WhiteboxTools + OpenTopography
│   ├── landuse.py, gee_client.py      # uso/ocupação via GEE (CN sugerido)
│   ├── plots.py                       # Plotly
│   ├── report.py                      # PDF técnico modular (FPDF2)
│   ├── assets/                        # logo LAPLA
│   ├── app.py                         # entrypoint Streamlit
│   └── app_pages/                     # 8 páginas (0-Bacia a 7-Exportar)
├── tests/                             # pytest
└── README.md
```

## Créditos

Desenvolvido no **LAPLA — Laboratório de Planejamento Ambiental**, FECFAU/Unicamp.

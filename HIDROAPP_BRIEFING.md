# HidroApp — Pipeline Hidrológico-Hidráulico Ponto-a-Ponto

> **Briefing para Claude Code** — Especificação técnica completa para desenvolvimento de aplicativo web de dimensionamento de drenagem urbana e rural integrando delineamento automático de bacia, chuva de projeto via IDF regional e dimensionamento de dispositivos hidráulicos.

## 1. Visão geral

Aplicativo **Streamlit** que executa o pipeline completo de projeto hidrológico-hidráulico em **cinco etapas sequenciais**, acionadas a partir de um **clique no mapa**:

```
[Clique no mapa]
      ↓
[1] Delineamento automático da bacia de contribuição
      ↓
[2] Chuva de projeto (via idf-generator já existente OU entrada manual)
      ↓
[3] Transformação chuva-vazão (Racional ou SCS-HU conforme área)
      ↓
[4] Dimensionamento do dispositivo hidráulico (manilha/galeria)
      ↓
[5] Opcional: reservatório de detenção com amortecimento (Puls)
```

**Objetivo**: entregar ao projetista, em poucos cliques, uma estimativa preliminar consistente para projetos de drenagem pluvial, bueiros rodoviários, galerias urbanas e bacias de detenção.

**Público**: engenheiros ambientais, hidrólogos e projetistas de infraestrutura — uso em projetos básicos e anteprojetos, sempre com validação profissional posterior.

## 2. Stack tecnológico

| Camada | Tecnologia | Motivo |
|--------|-----------|--------|
| Interface | Streamlit ≥ 1.32 | Prototipagem rápida, suporte nativo a mapas e session_state |
| Mapa interativo | folium + streamlit-folium | Clique para exutório, overlay de bacia delineada |
| Geoprocessamento | WhiteboxTools (via pacote `whitebox`) | Delineamento robusto, sem dependência de GRASS/ArcGIS |
| DEM | Copernicus GLO-30 via OpenTopography API | Melhor qualidade para relevo acidentado (Serra da Bocaina) |
| Dados raster/vetor | rasterio, geopandas, shapely, pyproj | Padrão da comunidade geoespacial Python |
| Cálculo numérico | numpy, scipy | Convolução de hidrograma, root-finding em Manning |
| Visualização | matplotlib | Hietogramas, hidrogramas |
| IDF | **Integração com idf-generator existente** | Reaproveita o app já desenvolvido |

## 3. Integração com o idf-generator existente

**Este é o ponto crítico da arquitetura.** O projeto `Projetos/IDF/idf-generator` (disponível em `https://idf-generator.streamlit.app/`) já implementa o pipeline completo de geração de curvas IDF a partir de séries pluviométricas da ANA HidroWeb. O HidroApp deve **reutilizar** essa lógica ao invés de reimplementar.

### 3.1 O que o idf-generator faz

```
Código estação ANA HidroWeb
        ↓
Série pluviométrica diária (↓ download automático)
        ↓
Máximos anuais por duração
        ↓
Ajuste Gumbel (padrão) ou GEV
        ↓
Teste Kolmogorov-Smirnov
        ↓
Fator 1,14 (chuva máxima pontual de 1 dia → 24h)
        ↓
Desagregação DAEE/CETESB (coeficientes 24h→1h, 1h→5min, etc.)
        ↓
Ajuste K, a, b, c por regressão não-linear (mínimos quadrados)
        ↓
Equação IDF: i = K · Tr^a / (t + b)^c
```

### 3.2 Estratégia de integração — TRÊS caminhos possíveis

O HidroApp deve oferecer os três, com prioridade visual para o (A):

**(A) Refatoração modular — PREFERIDO**
- Extrair a lógica de geração de IDF do `idf-generator` para um pacote Python compartilhado.
- Estrutura proposta:
  ```
  Projetos/
  ├── IDF/
  │   ├── idf-generator/          # app Streamlit original (mantém funcional)
  │   │   └── app.py
  │   └── idf-core/               # NOVO: biblioteca compartilhada
  │       ├── setup.py
  │       └── idf_core/
  │           ├── ana_client.py   # download HidroWeb
  │           ├── maxima.py       # extração de máximos anuais
  │           ├── distributions.py # Gumbel, GEV, KS
  │           ├── disaggregation.py # DAEE/CETESB
  │           └── fitting.py      # ajuste K,a,b,c
  └── HidroApp/
      └── app.py                  # importa `from idf_core import ...`
  ```
- Benefício: ambos os apps consomem a mesma lógica. Bug fixes e melhorias se propagam.

**(B) Call API entre apps Streamlit**
- O `idf-generator` expõe um endpoint simples (pode ser via FastAPI rodando em paralelo, ou via arquivo JSON salvo em `~/.idf-cache/`).
- O HidroApp faz `fetch_idf(station_code)` e recebe `{K, a, b, c, station_name, Tr_range, validity}`.
- Benefício: apps completamente desacoplados.
- Risco: latência e dependência de segundo serviço rodando.

**(C) Entrada manual com pré-preenchimento**
- Fallback sempre disponível: usuário digita K, a, b, c.
- Botão "Buscar no idf-generator" abre o app em nova aba com código da estação pré-selecionado.
- Benefício: zero acoplamento técnico, máxima simplicidade.

### 3.3 Decisão final de arquitetura

**Implementar (A) + (C) combinados**:
1. Refatorar `idf-generator` para extrair `idf-core` como biblioteca instalável via `pip install -e ../idf-core`.
2. HidroApp importa diretamente: `from idf_core.fitting import fit_idf_parameters`.
3. Na UI do HidroApp, oferecer três opções:
   - **"Gerar IDF pela estação"** → formulário com código ANA, chama `idf_core` diretamente
   - **"Importar do idf-generator"** → carrega JSON salvo pelo app original
   - **"Entrada manual"** → campos K, a, b, c

### 3.4 Contrato de dados (interface entre idf-core e HidroApp)

Ao consultar uma IDF, o HidroApp espera receber um objeto com o seguinte contrato:

```python
@dataclass
class IDFParameters:
    K: float                    # mm/h (com Tr em anos, t em min)
    a: float                    # expoente de Tr
    b: float                    # deslocamento em min
    c: float                    # expoente de (t+b)
    station_code: str           # código ANA (ex: "02244079")
    station_name: str           # nome descritivo
    record_years: int           # anos de registro usados
    distribution: str           # "Gumbel" ou "GEV"
    ks_pvalue: float            # p-valor do teste KS
    Tr_range: Tuple[int, int]   # (Tr_min, Tr_max) do ajuste
    duration_range: Tuple[int, int]  # (dur_min, dur_max) em minutos
    rmse_fit: float             # erro do ajuste K,a,b,c
    source: str                 # "idf-core v0.1"
    
    def intensity(self, Tr: float, duration_min: float) -> float:
        """i = K · Tr^a / (t + b)^c"""
        return self.K * (Tr ** self.a) / ((duration_min + self.b) ** self.c)
```

## 4. Estrutura de arquivos

```
HidroApp/
├── app.py                          # Streamlit main com as 5 abas
├── modules/
│   ├── __init__.py
│   ├── basin.py                    # Delineamento WBT + tc
│   ├── rainfall.py                 # Hietograma + integração idf-core
│   ├── hydrology.py                # Racional + SCS-HU + convolução
│   ├── hydraulics.py               # Manning + dimensionamento galerias
│   ├── routing.py                  # Puls modificado (detenção)
│   └── report.py                   # Exportação de memorial PDF (v0.2)
├── data/
│   └── dems/                       # Cache de DEMs baixados
├── tests/
│   ├── test_basin.py
│   ├── test_hydrology.py
│   └── test_routing.py
├── .env.example                    # OPENTOPO_API_KEY=...
├── requirements.txt
├── README.md
└── CLAUDE.md                       # Este briefing
```

## 5. Especificação por módulo

### 5.1 `basin.py` — Delineamento de bacia

**Pipeline WhiteboxTools:**

```python
def delineate_basin(lat: float, lon: float, dem_path: str) -> BasinResult:
    """
    1. Reprojeta DEM para UTM apropriado (cálculos métricos corretos)
    2. BreachDepressions (remove poços artificiais)
    3. D8Pointer (direção de fluxo)
    4. D8FlowAccumulation (acumulação em células)
    5. ExtractStreams (threshold 100 células ≈ 0,09 km²)
    6. JensonSnapPourPoints (snap do exutório ao canal, dist máx 200m)
    7. Watershed (delineamento)
    8. RasterToVectorPolygons (conversão)
    9. RasterStreamsToVector (rede de drenagem)
    """
```

**Saídas necessárias:**
- `basin_gdf`: polígono da bacia (GeoDataFrame EPSG:4326)
- `stream_gdf`: rede de drenagem recortada (linha, EPSG:4326)
- `outlet_snapped`: ponto ajustado ao canal
- `area_km2`, `perimeter_km`, `flowlength_km`
- `slope_mean_pct`, `elev_max`, `elev_min`, `delta_h_m`

**Tempo de concentração — três métodos simultâneos:**

```python
def time_of_concentration(L_km, S_mm, S_pct) -> dict:
    # Kirpich (1940): bacias rurais pequenas
    tc_kirpich = 0.0195 * (L_m ** 0.77) * (S_mm ** -0.385)  # min
    
    # Ven Te Chow
    tc_chow = 0.1602 * (L_km / sqrt(S_mm)) ** 0.64 * 60  # min
    
    # California Culverts Practice
    tc_california = 57 * ((L_km**3) / H) ** 0.385  # min
    
    return {"Kirpich": ..., "Ven Te Chow": ..., "California": ..., "Média": ...}
```

Usuário escolhe qual usar via radio button; Média é o default.

### 5.2 `rainfall.py` — Chuva de projeto

**Geração de hietograma pelos blocos alternados (Chow, 1988):**

```python
def alternating_block_hyetograph(Tr, duration_min, dt_min, idf: IDFParameters) -> dict:
    """
    1. Calcula i(t) para t = dt, 2dt, ..., duration
    2. P(t) = i(t) · t/60 (altura acumulada)
    3. Incrementos ΔP_k = P(k·dt) − P((k−1)·dt)
    4. Rearranja: maior incremento no centro, alternando lados
    """
    return {"t_min": ..., "depth_mm": ..., "intensity_mmh": ..., "total_mm": ...}
```

**Integração com idf-core (prioridade A da seção 3):**

```python
from idf_core.fitting import fit_idf_from_ana_station

def get_idf_from_station(station_code: str) -> IDFParameters:
    """Chama diretamente a biblioteca compartilhada."""
    return fit_idf_from_ana_station(station_code)
```

### 5.3 `hydrology.py` — Transformação chuva-vazão

**Método Racional (A ≤ 2 km²):**

```python
def rational_method(C: float, i_mmh: float, A_km2: float) -> float:
    return (C * i_mmh * A_km2) / 3.6  # m³/s
```

Tabela de C por uso do solo (ABRH/DAEE) integrada ao módulo.

**Método SCS-HU (2 < A ≤ 250 km²):**

```python
def scs_effective_rainfall(P_mm, CN, Ia_ratio=0.2) -> np.ndarray:
    S = 25400/CN - 254
    Ia = Ia_ratio * S
    Q = (P - Ia)**2 / (P - Ia + S)  # apenas para P > Ia
    return Q

def scs_triangular_uh(A_km2, tc_min, dt_min) -> dict:
    """
    Hidrograma Unitário Triangular SCS — ATENÇÃO:
    
    ⚠️  Constante em SI: qp = 0.208 · A / Tp_h  [m³/s/mm]
    ⚠️  NÃO usar 2.08 (erro comum vindo do manual americano CSM/in)
    
    Derivação: volume sob HU triangular = ½·Tb·qp = A[km²]·1[mm] = 1000·A [m³]
    → qp = 2000·A/Tb[s] = 0.208·A/Tp[h]
    
    Validar sempre com conservação de massa:
      V_hidrograma ≈ P_efetiva[mm] × A[km²] × 1000
    """
    lag = 0.6 * tc_min
    Tp = dt_min/2 + lag
    Tb = 2.67 * Tp
    qp = 0.208 * A_km2 / (Tp/60)  # m³/s/mm
    # ... gera HU triangular ...
```

**Convolução:**

```python
def convolve_hydrograph(effective_rain_mm, uh, dt_min) -> dict:
    Q = np.convolve(effective_rain_mm, uh, mode="full")
    return {"t_min": ..., "Q_m3s": Q, "Qp_m3s": ..., "V_total_m3": ...}
```

**Seleção automática de método:**

```python
def select_method(area_km2: float) -> str:
    if area_km2 <= 2.0: return "Racional"
    elif area_km2 <= 250.0: return "SCS-HU"
    else: return "Modelo distribuído (fora do escopo deste MVP)"
```

### 5.4 `hydraulics.py` — Dimensionamento hidráulico

**Equação de Manning para galeria circular:**

```python
# Capacidade a seção plena
def manning_circular_full(D, S, n):
    A = π·D²/4
    P = π·D
    R = A/P = D/4
    v = (1/n) · R^(2/3) · S^(1/2)
    Q = v · A

# Escoamento parcialmente cheio (via ângulo θ)
def manning_circular_partial(D, h, S, n):
    θ = 2·arccos(1 − 2h/D)
    A = D²/8 · (θ − sin(θ))
    P = D·θ/2
    # ... resto análogo
```

**Dimensionamento com diâmetros comerciais:**

```python
COMMERCIAL_DIAMETERS_M = [0.30, 0.40, 0.50, 0.60, 0.80, 1.00, 1.20, 1.50, 1.80, 2.00]

def size_circular_culvert(Q_design, S, n, safety_factor=1.10, fill_ratio_max=0.80):
    """
    Critério: lâmina máxima 80% do diâmetro (norma DAEE/ABNT).
    Aplica fator de segurança sobre a vazão antes de selecionar diâmetro.
    Retorna o menor diâmetro comercial que atende.
    Calcula lâmina e velocidade de operação reais (Q sem fator).
    """
```

**Validações obrigatórias na UI:**
- v < 0,6 m/s → warning de sedimentação
- v > 5,0 m/s → warning de abrasão

**Materiais e coeficientes n:**

```python
MANNING_N = {
    "Concreto liso (manilha)": 0.013,
    "Concreto rugoso": 0.015,
    "PEAD corrugado": 0.022,
    "PVC corrugado": 0.020,
    "Metálico corrugado": 0.024,
    "Alvenaria de pedra": 0.025,
    "Canal de terra": 0.030,
    "Canal em concreto": 0.015,
}
```

**Galeria retangular (celular):**

```python
def size_box_culvert(Q_design, S, n, b_over_h_ratio=1.5, ...):
    # Iterativo: fixa b/h e busca h via brentq
```

### 5.5 `routing.py` — Reservatório de detenção

**Método de Puls modificado:**

```
(I1 + I2)/2 − (O1 + O2)/2 = (S2 − S1)/Δt

Reorganizando para resolver:
(2S2/Δt + O2) = (2S1/Δt − O1) + (I1 + I2)
```

**Estrutura das curvas:**

```python
def build_storage_discharge_curves(
    Aw_m2: float,       # área superficial (prismático)
    h_max: float,       
    z_orifice: float,   # cota do orifício de fundo
    d_orifice: float,
    z_weir: float,      # cota do vertedor de emergência  
    b_weir: float,
) -> dict:
    """
    Dispositivos combinados:
      - Orifício inferior: Q = Cd·A·√(2g·h)
      - Vertedor retangular: Q = Cw·b·h^(3/2)  (Cw=1.85 borda delgada)
    """
```

**Roteamento:**

```python
def puls_routing(inflow_m3s, dt_min, curves) -> dict:
    """
    Para cada passo:
      1. RHS = (2S_k/Δt − O_k) + (I_k + I_{k+1})
      2. φ(h) = 2S(h)/Δt + O(h)  (pré-computada)
      3. Interpola h tal que φ(h) = RHS
      4. S_{k+1} = S(h), O_{k+1} = O(h)
    
    Retorna hidrograma efluente amortecido.
    """
```

## 6. Interface do usuário — 5 abas

### Aba 1: 🗺️ Exutório
- Mapa folium centralizado em Bananal/SP por padrão
- Camadas: OpenStreetMap, OpenTopoMap, Esri Satellite
- Clique marca ponto; entrada manual de lat/lon como alternativa
- Botão "Limpar" reseta pipeline

### Aba 2: 🏞️ Bacia
- Botão "Delinear bacia" aciona o pipeline WBT
- Status em tempo real com `st.status()` durante processamento
- Métricas: A, P, L, S, ΔH, método sugerido
- Tabela de tc por método + radio para escolha

### Aba 3: 🌧️ Chuva
- **Três opções em radio button:**
  1. 🌩️ "Gerar IDF pela estação ANA" (idf-core integrado)
  2. 📥 "Importar do idf-generator" (carrega JSON/cache)
  3. ✏️ "Entrada manual" (K, a, b, c)
- Seleção de Tr (2, 5, 10, 25, 50, 100, 200, 500)
- Duração (default = 2·tc), Δt (default 5 min)
- Hietograma gerado e plotado (bar chart)

### Aba 4: 📈 Vazão
- Método auto-selecionado pela área (com override manual)
- **Racional:** selectbox de uso do solo → C sugerido (editável)
- **SCS-HU:** selectbox de cobertura + grupo de solo → CN sugerido
- Plot do hidrograma completo (SCS) ou metadado da Qp (Racional)

### Aba 5: 🔧 Dimensionamento
- **Sub-aba A: Galeria/Manilha**
  - Circular ou retangular
  - Material → n de Manning
  - S, fator de segurança, lâmina máxima
  - Output: diâmetro, lâmina operação, velocidade
  - Alertas de velocidade (sedimentação/abrasão)
- **Sub-aba B: Reservatório de detenção**
  - Aw, h_max, orifício (cota+diâmetro), vertedor (cota+largura)
  - Roteia hidrograma da aba 4
  - Plot comparativo afluente vs efluente
  - Métricas: Qp_in, Qp_out, atenuação %, V_max, h_max

## 7. Armadilhas técnicas conhecidas

### 7.1 ⚠️ Constante do HU triangular SCS

**Problema**: Literatura brasileira frequentemente copia a constante `2.08` do manual americano (onde qp está em CSM/in). Para SI (A em km², qp em m³/s/mm), a constante correta é **0.208**.

**Teste de validação obrigatório**: após gerar qualquer hidrograma SCS, verificar:
```python
V_calculado = Q_m3s.sum() * dt_min * 60
V_esperado = P_efetiva_mm * A_km2 * 1000
assert abs(V_calculado - V_esperado) / V_esperado < 0.01  # 1% de tolerância
```

### 7.2 Download do binário WhiteboxTools

Na primeira execução, o pacote `whitebox` baixa o binário nativo (~60 MB). Em redes restritivas isso falha. Mitigações:
- Documentar no README que primeira execução requer internet
- Permitir override via variável de ambiente `WBT_BINARY_PATH`
- Mostrar mensagem clara no app se o binário não carregar

### 7.3 Reprojeção DEM → UTM

O DEM do OpenTopography vem em EPSG:4326. Todos os cálculos métricos (área, perímetro, declividade, Manning) exigem projeção métrica. **Sempre** reprojetar para UTM apropriado baseado na longitude central antes de qualquer operação:

```python
zone = int((lon_center + 180) / 6) + 1
epsg = 32700 + zone if lat_center < 0 else 32600 + zone  # S ou N
```

### 7.4 Snap do exutório

Usuários clicam próximo a canais, mas raramente exatamente sobre a célula de maior acumulação. O `JensonSnapPourPoints` com `snap_dist=200m` resolve a maioria dos casos, mas:
- Expor slider de 50-1000m para ajuste
- Mostrar ponto original e ponto snapped no mapa (cores diferentes)

### 7.5 Aplicabilidade do Racional

Método Racional assume **duração = tc** (chuva crítica). Se o hietograma for mais longo que tc, a vazão de pico é superestimada. No app:
- Para Racional: usa `i(tc)` direto da IDF (não convolve)
- Gera hidrograma triangular sintético apenas para o módulo de detenção
- Deixa claro na UI que "para dimensionar detenção, prefira SCS-HU"

## 8. Fluxo de desenvolvimento sugerido

### Sprint 1 — Esqueleto funcional (MVP interno)
1. Criar estrutura de pastas e `requirements.txt`
2. Implementar `rainfall.py` + `hydrology.py` + `hydraulics.py` + `routing.py`
3. Escrever testes unitários com cenários sintéticos
4. **Validar conservação de massa do HU SCS** antes de tudo
5. `app.py` simplificado com entrada manual em todos os passos

### Sprint 2 — Geoprocessamento
1. Implementar `basin.py` com WhiteboxTools
2. Testar com DEM local (sem OpenTopography)
3. Integrar download do Copernicus via OpenTopography
4. Validar com uma bacia conhecida (ex: ponto na região de Bananal/SP)

### Sprint 3 — Integração idf-core
1. Refatorar `idf-generator` extraindo `idf-core` como biblioteca
2. Instalar `idf-core` no HidroApp via `pip install -e`
3. Implementar os três caminhos (ANA, cache, manual)
4. Testar com estação real

### Sprint 4 — UX e refinamentos
1. Mapa com overlay de resultados (bacia + rede + exutório)
2. Gráficos bonitos (matplotlib com tema consistente)
3. Mensagens de erro claras
4. Validações e warnings (velocidade, lâmina, extravasamento)

### Sprint 5 — Relatório e v0.2
1. Exportação de memorial de cálculo em PDF (usar skill `pdf`)
2. Série histórica HidroWeb como alternativa à IDF (para bacias com registro local)
3. Múltiplas linhas de galerias quando Qp excede manilha de 2m
4. Curva S(h) não prismática (input via CSV ou DEM do reservatório)

## 9. Dependências

```txt
streamlit>=1.32
folium>=0.15
streamlit-folium>=0.20
whitebox>=2.3
rasterio>=1.3
geopandas>=0.14
shapely>=2.0
pyproj>=3.6
requests>=2.31
numpy>=1.26
pandas>=2.1
matplotlib>=3.8
scipy>=1.11

# idf-core (instalação local durante dev)
-e ../IDF/idf-core
```

**Variáveis de ambiente (`.env`):**
```
OPENTOPO_API_KEY=your_key_here
WBT_BINARY_PATH=/optional/path/to/whitebox_tools  # opcional
```

## 10. Testes

**Cenários-chave a cobrir:**

```python
# test_hydrology.py
def test_scs_mass_conservation():
    """HU SCS deve conservar massa: V_hidrograma ≈ P_ef · A · 1000"""
    uh = scs_triangular_uh(area_km2=10, tc_min=60, dt_min=5)
    V_uh = np.trapezoid(uh['uh'], uh['t_min']*60)
    assert abs(V_uh - 10*1000) / 10000 < 0.01  # < 1% erro

def test_rational_magnitude():
    """Racional com C=0.5, i=100mm/h, A=1km² deve dar ~14 m³/s"""
    Q = rational_method(C=0.5, i_mmh=100, A_km2=1.0)
    assert abs(Q - 13.89) < 0.1

def test_method_selection():
    assert select_method(0.5) == "Racional"
    assert select_method(50) == "SCS-HU"
    assert select_method(500).startswith("Modelo distribuído")

# test_routing.py
def test_puls_attenuation():
    """Reservatório bem dimensionado deve atenuar > 50%"""
    # ... cenário de hidrograma triangular + reservatório amplo ...
    assert result["attenuation_pct"] > 50
```

## 11. Referências técnicas

- **Delineamento**: WhiteboxTools Manual (Lindsay, 2014)
- **Método Racional**: DAEE-SP, Drenagem Urbana (Tucci, 1995)
- **SCS-CN**: USDA NRCS TR-55 (1986), NEH-4
- **HU Triangular**: Mockus (1957), SCS National Engineering Handbook
- **Puls**: Chow, Maidment & Mays — *Applied Hydrology* (1988)
- **Blocos alternados**: Chow, 1988 — Capítulo 14
- **Manning**: Chaudhry — *Open-Channel Flow* (2008)
- **IDF no Brasil**: Martinez & Magni (1999), Silveira (2014), Plúvio 2.1 (UFV)
- **Desagregação DAEE/CETESB**: Equação relação 24h/1h, CETESB/DAEE

## 12. Contexto pessoal e projeto

- **Autor**: Vinicius / Azevedo Consultoria Ambiental e Energética
- **Localização de testes**: Bananal/SP (Serra da Bocaina, 22,68°S 44,32°W)
- **Aplicações práticas previstas**:
  - Projetos de drenagem para licenciamento ambiental (Resolução INEA 72/2013)
  - Dimensionamento de travessias e bueiros para obras viárias (Sextante + Motiva/RioSP)
  - Verificação de dispositivos de drenagem em empreendimentos imobiliários
  - Apoio ao trabalho de doutorado (FECFAU/UNICAMP) em bacias do médio Paraíba do Sul
- **Integração com ecossistema existente**:
  - `idf-generator`: já em produção em `https://idf-generator.streamlit.app/`
  - Task Panel React: o HidroApp pode ser um dos módulos listados no painel
  - HEC-RAS workflow: saídas do HidroApp (Qp) alimentam estudos hidráulicos detalhados

---

**Última atualização**: 2026-04-16  
**Versão deste briefing**: 1.0

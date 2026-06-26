"""Página 6: reservatório de detenção via Puls modificado."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from chuva_vazao import detencao as dt
from chuva_vazao import plots


st.title("6. Reservatório de Detenção")
st.caption(
    "Roteia o hidrograma afluente por Puls modificado em reservatório com "
    "orifício de fundo + vertedor retangular de emergência. Geometria "
    "prismática ou via curva cota-volume tabulada."
)

hg = st.session_state.get("hidrograma")
if hg is None:
    st.error("Gere o hidrograma na Página 3 antes.")
    st.stop()


# ---------------------------------------------------------------------------
# Datum vertical
# ---------------------------------------------------------------------------

st.subheader("Referencial geodésico")
col_datum1, col_datum2 = st.columns([2, 1])
with col_datum1:
    datum_vertical = st.text_input(
        "Datum vertical (rótulo de projeto)",
        value="Imbituba (SGB) — IBGE",
        help=(
            "Apenas rótulo para o relatório; não entra no cálculo. SIRGAS "
            "2000 é datum horizontal — para cotas verticais use Imbituba "
            "(SGB), a realização vertical mais recente do IBGE, ou EGM2008 "
            "quando as cotas vierem de DEM como o Copernicus GLO-30 (que já "
            "é referido ao EGM2008)."
        ),
    )
with col_datum2:
    z_fundo = st.number_input(
        "Cota do fundo z₀ (m)",
        min_value=-100.0, max_value=5000.0,
        value=100.0, step=0.10,
        help=(
            "Cota do ponto mais baixo do reservatório (leito), em metros no "
            "datum vertical do projeto. Não altera a atenuação nem o "
            "roteamento: o cálculo usa só a altura relativa h. Este valor "
            "apenas posiciona as cotas absolutas (orifício, vertedor, NA "
            "máx) nos gráficos e no relatório. No modo DEM in-line ele pode "
            "ser sobrescrito pela cota que o DEM lê no ponto da barragem."
        ),
    )


# ---------------------------------------------------------------------------
# Geometria — prismatico ou curva cota-volume
# ---------------------------------------------------------------------------

st.subheader("Geometria do reservatório")
modo = st.radio(
    "Modo de definição",
    [
        "Prismático (área constante)",
        "Curva cota × volume (manual / drone)",
        "Gerar do DEM (in-line, GEE)",
    ],
    horizontal=False,
    help=(
        "Escolhe como descrever a geometria. Prismático: área do espelho "
        "constante com a altura (S = Aw·h), bom para pré-dimensionamento. "
        "Curva cota×volume: você fornece pares (cota, volume) de topografia "
        "ou drone. DEM in-line: o app gera a curva e a mancha por flood-fill "
        "no DEM da Página 0."
    ),
)


cota_vol_h: tuple[float, ...] = ()
cota_vol_v: tuple[float, ...] = ()
Aw = 0.0
h_max = 0.0
mancha_geojson_4326: str | None = None
ponto_barragem: tuple[float, float] | None = None

if modo == "Prismático (área constante)":
    col1, col2 = st.columns(2)
    with col1:
        Aw = st.number_input(
            "Área superficial Aw (m²)",
            min_value=10.0, max_value=1_000_000.0,
            value=5000.0, step=100.0,
            help=(
                "Área do espelho d'água, suposta constante com a altura no "
                "modo prismático (volume S = Aw·h). Aw maior armazena mais "
                "volume por metro de lâmina e atenua mais o pico, atingindo "
                "menor altura para o mesmo hidrograma afluente."
            ),
        )
        st.caption(
            "Sai do volume de detenção exigido dividido pela altura útil "
            "(Aw ≈ V_req / h_máx). Ordens de grandeza típicas, a verificar "
            "no caso local: ~200-2.000 m² (lote/microdrenagem); ~2.000-20.000 "
            "m² (loteamento/quadra); acima de ~20.000 m² (sub-bacia)."
        )
    with col2:
        h_max = st.number_input(
            "Altura máxima h_max (m)", min_value=0.5, max_value=20.0,
            value=4.0, step=0.1,
            help=(
                "Profundidade máxima de água acima do fundo (volume útil mais "
                "borda livre). Define até onde o roteamento pode subir antes "
                "de saturar e marcar extravasamento. h_máx maior aumenta o "
                "amortecimento, mas exige barramento/talude mais alto e mais "
                "borda livre."
            ),
        )
        st.caption(
            "Ordens de grandeza de manuais de drenagem urbana, a verificar no "
            "caso local: ~1-3 m em bacias secas, por segurança; ~3-6 m em "
            "reservatórios maiores; acima de ~6 m já entra no domínio de "
            "barragens, com projeto e licenciamento próprios."
        )
    z_max_abs = z_fundo + h_max

elif modo == "Curva cota × volume (manual / drone)":
    st.caption(
        "Edite a tabela abaixo. As cotas (z) são absolutas no datum; o "
        "volume é acumulado da cota do fundo até z. A primeira linha deve "
        "ser z = z_fundo com V = 0. Útil pra colar saída de drone/topografia."
    )
    default_curva = pd.DataFrame({
        "Cota z (m)": [z_fundo, z_fundo + 1.0, z_fundo + 2.0, z_fundo + 3.0, z_fundo + 4.0],
        "Volume V (m³)": [0.0, 1500.0, 4000.0, 7500.0, 12000.0],
    })
    curva_df = st.data_editor(
        default_curva,
        num_rows="dynamic",
        use_container_width=True,
        column_config={
            "Cota z (m)": st.column_config.NumberColumn(format="%.2f", step=0.1),
            "Volume V (m³)": st.column_config.NumberColumn(format="%.1f", step=100.0),
        },
        key="curva_cota_volume_editor",
    )
    curva_df = curva_df.dropna().sort_values("Cota z (m)").reset_index(drop=True)

    if len(curva_df) < 2:
        st.error("A curva precisa de pelo menos 2 pontos.")
        st.stop()

    z_abs = curva_df["Cota z (m)"].to_numpy(dtype=float)
    V = curva_df["Volume V (m³)"].to_numpy(dtype=float)

    if z_abs[0] < z_fundo - 1e-6:
        st.error(f"A menor cota da curva ({z_abs[0]:.2f} m) está abaixo do fundo (z₀={z_fundo:.2f} m).")
        st.stop()
    if not np.all(np.diff(z_abs) > 0):
        st.error("As cotas precisam ser estritamente crescentes.")
        st.stop()
    if not np.all(np.diff(V) >= 0):
        st.error("Volumes precisam ser monotonicamente não-decrescentes.")
        st.stop()
    if V[0] > 1e-3:
        st.warning(
            f"Primeira linha tem V = {V[0]:.0f} m³ ≠ 0. O modelo usa V[0] como volume "
            "morto na cota inicial; geralmente queremos V = 0 em z = z_fundo."
        )

    h_rel = z_abs - z_fundo
    cota_vol_h = tuple(h_rel.tolist())
    cota_vol_v = tuple(V.tolist())
    h_max = float(h_rel[-1])
    z_max_abs = float(z_abs[-1])
    Aw = float(V[-1] / h_max) if h_max > 0 else 1.0

    fig_curva = plots.plot_curva_cota_volume(
        z_abs_m=z_abs, V_m3=V,
        z_fundo_m=z_fundo, datum=datum_vertical,
    )
    st.plotly_chart(fig_curva, use_container_width=True)

else:  # "Gerar do DEM (in-line, GEE)"
    st.caption(
        "Reaproveita a bacia delineada na **Página 0** + DEM Copernicus GLO-30 "
        "para flood-fill por cota. Útil pra prospecção de CGH e detenção em "
        "lote vazio. Eixo do barramento é assumido vertical no ponto."
    )

    bres = st.session_state.get("basin_result")
    dem_path_session = st.session_state.get("_dem_path")

    if bres is None or dem_path_session is None:
        st.error(
            "Vá à **Página 0** primeiro: baixe um DEM e delineie a bacia de "
            "contribuição. Esta opção depende dos dois."
        )
        st.stop()

    import folium
    from streamlit_folium import st_folium

    from chuva_vazao import reservatorio_dem as rd

    bacia_fc = json.loads(bres["basin_geojson"])
    # Polygon da bacia em EPSG:4326 (geopandas .to_json gera FeatureCollection)
    bacia_geom = bacia_fc["features"][0]["geometry"]
    outlet_lon, outlet_lat = bres["outlet_snapped"]

    # Estado: ponto da barragem (default = exutorio)
    if "_barragem_ponto" not in st.session_state:
        st.session_state["_barragem_ponto"] = (outlet_lat, outlet_lon)
    bar_lat, bar_lon = st.session_state["_barragem_ponto"]

    col_map, col_ctrl = st.columns([4, 1])
    with col_ctrl:
        bar_lat = st.number_input(
            "Lat barragem", -90.0, 90.0, float(bar_lat), 0.0001, format="%.6f",
            help=(
                "Latitude do ponto onde a barragem cruza o talvegue (eixo "
                "in-line, assumido vertical). Em geral você define clicando "
                "no mapa; o flood-fill por cota parte deste pixel para "
                "montante. Mover o ponto muda a bacia represada e, com ela, "
                "a curva cota-volume e a mancha."
            ),
        )
        bar_lon = st.number_input(
            "Lon barragem", -180.0, 180.0, float(bar_lon), 0.0001, format="%.6f",
            help=(
                "Longitude do ponto da barragem (eixo in-line). Normalmente "
                "definida pelo clique no mapa; o flood-fill por cota arranca "
                "deste pixel. Reposicionar altera a bacia represada, a curva "
                "cota-volume e a mancha gerada."
            ),
        )
        delta_h_max = st.number_input(
            "Altura máx do barramento Δh (m)",
            0.5, 100.0, 10.0, 0.5,
            help=(
                "Altura máxima da barragem acima do leito no ponto; define a "
                "cota até onde o app inunda o DEM (z_máx = cota do pixel da "
                "barragem + Δh). Δh maior gera reservatório e mancha maiores "
                "e estende a curva cota-volume para cima."
            ),
        )
        st.caption(
            "Ordem de grandeza por tipo de obra, a verificar no caso local: "
            "detenção in-line em lote/canal ~2-5 m; pequena barragem/CGH "
            "~5-15 m. Acima de ~15 m costuma cair no enquadramento de "
            "barragens (Lei 12.334/PNSB), com projeto e licenciamento "
            "específicos."
        )
        n_pontos = st.number_input(
            "Nº de pontos da curva", 5, 60, 20, 1,
            help=(
                "Quantidade de cotas igualmente espaçadas entre o fundo e "
                "z_máx em que o flood-fill calcula área e volume. Mais pontos "
                "suavizam a curva cota-volume, mas pesam o processamento (cada "
                "cota refaz a busca de pixels conectados). Não mudam a física, "
                "só a resolução da curva."
            ),
        )
        usar_clip = st.checkbox(
            "Lago existente — definir piso do leito", value=False,
            help=(
                "Marque se já existe lâmina d'água no ponto: o DEM enxerga a "
                "superfície da água, não o leito, o que falsearia o fundo e o "
                "volume. Ativando, você informa o piso do leito e o app usa "
                "esse valor como fundo do flood-fill."
            ),
        )
        z_min_clip = (
            st.number_input(
                "Piso do leito z_min (m)", -100.0, 5000.0, float(z_fundo), 0.1,
                help=(
                    "Cota presumida do leito sob a lâmina d'água existente. O "
                    "app adota esse valor como fundo do reservatório; pixels do "
                    "DEM abaixo dele são elevados a esse piso (não descartados), "
                    "de modo que o volume passa a ser medido acima do leito "
                    "presumido. Use a cota de fundo de projeto ou de batimetria; "
                    "um piso mais alto reduz o volume calculado."
                ),
            )
            if usar_clip else None
        )
        if usar_clip:
            st.caption(
                "Entre a cota da lâmina d'água que o DEM enxerga (limite "
                "superior) e a cota de fundo de projeto/batimetria (limite "
                "inferior real). Definido pelo dado de campo, sem faixa fixa."
            )
        gerar_btn = st.button("Gerar curva", type="primary")

    with col_map:
        m = folium.Map(location=[outlet_lat, outlet_lon], zoom_start=13)
        folium.TileLayer(
            tiles="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
            attr="Esri", name="Satélite",
        ).add_to(m)
        folium.GeoJson(
            bacia_fc, name="Bacia",
            style_function=lambda _: {"color": "#ff7f0e", "weight": 2, "fillOpacity": 0.15},
        ).add_to(m)
        folium.Marker(
            [outlet_lat, outlet_lon], tooltip="Exutório (snapped)",
            icon=folium.Icon(color="red", icon="water", prefix="fa"),
        ).add_to(m)
        folium.Marker(
            [bar_lat, bar_lon], tooltip="Barragem (in-line)",
            icon=folium.Icon(color="darkblue", icon="dam", prefix="fa"),
        ).add_to(m)

        cv = st.session_state.get("_curva_dem")
        if cv is not None:
            folium.GeoJson(
                cv["mancha_geojson"], name="Mancha (z_máx)",
                style_function=lambda _: {"color": "#1f77b4", "weight": 1, "fillOpacity": 0.45, "fillColor": "#1f77b4"},
            ).add_to(m)
        folium.LayerControl(collapsed=True).add_to(m)

        click = st_folium(
            m, height=600, width=None,
            returned_objects=["last_clicked"], key="mapa_barragem",
            use_container_width=True,
        )
        if click and click.get("last_clicked"):
            st.session_state["_barragem_ponto"] = (
                click["last_clicked"]["lat"], click["last_clicked"]["lng"],
            )
            st.rerun()

    if gerar_btn:
        with st.spinner("Flood-fill por cota no DEM..."):
            try:
                cv = rd.gerar_curva_cota_volume_dem(
                    lat=bar_lat, lon=bar_lon,
                    dem_path=Path(dem_path_session),
                    bacia_polygon_4326=bacia_geom,
                    delta_h_max_m=float(delta_h_max),
                    n_pontos=int(n_pontos),
                    z_min_clip_m=z_min_clip,
                )
            except Exception as exc:
                st.error(f"Falhou: {exc}")
                st.stop()
        st.session_state["_curva_dem"] = {
            "cotas_m": cv.cotas_m.tolist(),
            "volumes_m3": cv.volumes_m3.tolist(),
            "areas_m2": cv.areas_m2.tolist(),
            "z_fundo_m": cv.z_fundo_m,
            "mancha_geojson": cv.mancha_geojson,
            "pixel_size_m": cv.pixel_size_m,
            "n_pixels_z_max": cv.n_pixels_z_max,
        }
        st.rerun()

    cv = st.session_state.get("_curva_dem")
    if cv is None:
        st.info("Clique no mapa para reposicionar a barragem (default = exutório) e clique **Gerar curva**.")
        st.stop()

    z_abs = np.asarray(cv["cotas_m"])
    V = np.asarray(cv["volumes_m3"])
    A = np.asarray(cv["areas_m2"])
    z_fundo_dem = float(cv["z_fundo_m"])

    # Aviso técnico
    area_max = float(A[-1])
    pixel = float(cv["pixel_size_m"])
    n_pix = int(cv["n_pixels_z_max"])
    if n_pix < 50:
        st.warning(
            f"Espelho na cota máx: **{area_max:,.0f} m²** ({n_pix} pixels de "
            f"{pixel:.1f} m). Resolução do DEM é grosseira para este reservatório — "
            "considere drone."
        )
    else:
        st.success(
            f"Curva gerada: {len(z_abs)} pontos. Espelho z_máx = "
            f"{area_max / 1e4:.2f} ha ({n_pix} pixels)."
        )

    # Override do z_fundo: se o DEM achou um fundo diferente do que o usuario
    # tinha posto, sincroniza e avisa
    if abs(z_fundo_dem - z_fundo) > 0.5:
        st.info(
            f"DEM informa fundo no ponto = **{z_fundo_dem:.2f} m**. "
            f"Sobrescrevendo o valor manual ({z_fundo:.2f} m) para coerência."
        )
        z_fundo = z_fundo_dem

    # Popula a curva
    h_rel = z_abs - z_fundo
    cota_vol_h = tuple(h_rel.tolist())
    cota_vol_v = tuple(V.tolist())
    h_max = float(h_rel[-1])
    z_max_abs = float(z_abs[-1])
    Aw = float(V[-1] / h_max) if h_max > 0 else 1.0
    mancha_geojson_4326 = cv["mancha_geojson"]
    ponto_barragem = (bar_lat, bar_lon)

    # Plots: curva V x Z e A x Z
    col_a, col_b = st.columns(2)
    with col_a:
        fig_vz = plots.plot_curva_cota_volume(
            z_abs_m=z_abs, V_m3=V,
            z_fundo_m=z_fundo, datum=datum_vertical,
        )
        st.plotly_chart(fig_vz, use_container_width=True)
    with col_b:
        fig_az = go.Figure()
        fig_az.add_trace(go.Scatter(
            x=A, y=z_abs,
            mode="lines+markers",
            line=dict(color="#1f77b4", width=2),
            marker=dict(size=8),
            hovertemplate="A=%{x:,.0f} m²<br>z=%{y:.2f} m<extra></extra>",
        ))
        fig_az.update_layout(
            title="Área do espelho × Cota",
            xaxis_title="Área inundada (m²)",
            yaxis_title=f"Cota z (m) — {datum_vertical or 'datum'}",
            template="plotly_white",
            height=400,
        )
        st.plotly_chart(fig_az, use_container_width=True)

    with st.expander("Tabela cota / área / volume"):
        st.dataframe(
            pd.DataFrame({
                "Cota z (m)": z_abs.round(2),
                "Área (m²)": A.round(0),
                "Volume (m³)": V.round(0),
            }),
            use_container_width=True,
        )


st.info(f"NA máximo (z_máx) = **{z_max_abs:.2f} m** no datum.")


# ---------------------------------------------------------------------------
# Dispositivos de saida
# ---------------------------------------------------------------------------

st.subheader("Dispositivos de saída")
st.caption("Cotas absolutas no datum vertical do projeto.")

col1, col2 = st.columns(2)
with col1:
    st.markdown("**Orifício de fundo**")
    z_orif_abs = st.number_input(
        "Cota do orifício (m)",
        min_value=float(z_fundo),
        max_value=float(z_max_abs),
        value=float(z_fundo),
        step=0.05,
        help=(
            "Cota a partir da qual o orifício de fundo começa a descarregar "
            "(carga efetiva h_ef = NA − cota do orifício). Em geral fica no "
            "fundo (z₀) para esvaziar o reservatório por completo. Elevá-la "
            "cria volume morto abaixo do orifício e atrasa o início da vazão "
            "efluente. A UI limita o valor ao intervalo [z₀, z_máx]."
        ),
    )
    st.caption(
        "Padrão: igual à cota do fundo (descarga de fundo, esvazia tudo). "
        "Acima do fundo só para reter volume permanente (bacia úmida) ou "
        "tratamento de qualidade da água."
    )
    d_orif = st.number_input(
        "Diâmetro do orifício (m)", 0.05, 3.0, 0.30, 0.05,
        help=(
            "Diâmetro do orifício de fundo, principal dispositivo que limita "
            "a vazão efluente à de pré-desenvolvimento. A descarga cresce com "
            "o quadrado do diâmetro (Q ∝ d²): orifício maior libera mais "
            "vazão, amortece menos o pico e exige menos volume; menor amortece "
            "mais, mas eleva o NA e pode acionar o vertedor. Não há campo "
            "separado de vazão-alvo: ela é imposta pelo par (diâmetro, Cd) e "
            "pela cota do orifício."
        ),
    )
    st.caption(
        "Resultado do dimensionamento à vazão de pré-desenvolvimento, não um "
        "valor tabelado. Invertendo a equação do orifício (Cd=0,61, carga "
        "h≈1,5 m): Q≈0,2 m³/s → d≈0,28 m; Q≈0,5 → d≈0,44 m; Q≈1,0 → d≈0,62 m; "
        "Q≈2,0 → d≈0,88 m. d cresce com a raiz de Q e depende da carga: menos "
        "carga exige diâmetro maior."
    )
    Cd = st.number_input(
        "Cd orifício", 0.4, 0.9, 0.61, 0.01,
        help=(
            "Coeficiente de descarga do orifício: fração da vazão teórica que "
            "efetivamente escoa, função da geometria da borda. Q é "
            "proporcional a Cd. Para orifício de borda viva em parede fina, "
            "0,61 é o valor consagrado."
        ),
    )
    st.caption(
        "Borda viva / parede fina: 0,60-0,62 (padrão 0,61); bocal ou tubo "
        "curto: ~0,80; entrada bem arredondada (bell-mouth): 0,90-0,98. A "
        "faixa do campo (0,40-0,90) cobre do orifício à boca arredondada."
    )
with col2:
    st.markdown("**Vertedor de emergência**")
    z_vert_abs = st.number_input(
        "Cota do vertedor (m)",
        min_value=float(z_fundo),
        max_value=float(z_max_abs),
        value=float(z_fundo + 0.75 * h_max),
        step=0.05,
        help=(
            "Cota da soleira do vertedor de emergência: abaixo dela só o "
            "orifício descarrega; acima, o vertedor entra para evitar "
            "galgamento. Soleira mais alta deixa mais volume útil para "
            "amortecer antes do extravasamento, mas reduz a borda livre. O "
            "padrão fica a 75% de h_máx acima do fundo. A UI limita o valor "
            "ao intervalo [z₀, z_máx]."
        ),
    )
    st.caption(
        "Tipicamente 60-85% de h_máx acima do fundo (padrão 75%), deixando "
        "borda livre de ~0,3-0,5 m até o NA máximo."
    )
    b_vert = st.number_input(
        "Largura do vertedor (m)", 0.1, 50.0, 3.0, 0.1,
        help=(
            "Largura da soleira do vertedor de emergência. A descarga é "
            "proporcional à largura (Q = Cw·b·h^1,5): vertedor mais largo "
            "escoa a cheia excedente com menor carga sobre a soleira, baixando "
            "o NA máximo; mais estreito eleva a lâmina e o risco de "
            "galgamento."
        ),
    )
    st.caption(
        "Dimensione para passar a cheia excedente dentro da borda livre. "
        "Ordem de grandeza, a verificar no caso local: ~1-5 m em bacias "
        "urbanas; ~5-20 m em reservatórios maiores."
    )
    Cw = st.number_input(
        "Cw vertedor", 1.5, 2.2, 1.85, 0.05,
        help=(
            "Coeficiente de vazão do vertedor na forma Q = Cw·b·h^1,5; embute "
            "o tipo de soleira. Cw maior significa mais vazão para a mesma "
            "carga. O padrão 1,85 corresponde a vertedor retangular de soleira "
            "delgada (chapa)."
        ),
    )
    st.caption(
        "Soleira delgada (chapa, borda viva): ~1,84-1,86 (padrão 1,85); "
        "soleira espessa / canal de terra: ~1,55-1,70; perfil ogee/Creager: "
        "~2,0-2,2. A equação Q=(2/3)·Cd·√(2g)·b·h^1,5 com Cd≈0,62 dá Cw≈1,83, "
        "próximo do padrão. A faixa do campo (1,5-2,2) cobre os três tipos."
    )


# Converte cotas absolutas em alturas relativas (a base do reservatorio na engine
# continua sendo h relativa ao fundo).
z_orif_rel = z_orif_abs - z_fundo
z_vert_rel = z_vert_abs - z_fundo


try:
    res = dt.Reservatorio(
        Aw_m2=Aw, h_max_m=h_max,
        z_orificio_m=z_orif_rel, d_orificio_m=d_orif,
        z_vertedor_m=z_vert_rel, b_vertedor_m=b_vert,
        Cd_orificio=Cd, Cw_vertedor=Cw,
        cota_volume_h_m=cota_vol_h,
        cota_volume_v_m3=cota_vol_v,
        z_fundo_m=z_fundo,
        datum_vertical=datum_vertical,
    )
except ValueError as exc:
    st.error(f"Erro na configuração do reservatório: {exc}")
    st.stop()


# ---------------------------------------------------------------------------
# Roteamento
# ---------------------------------------------------------------------------

inflow = hg["Q_m3s"].to_numpy()
dt_min = float(hg.index[1] - hg.index[0]) if len(hg) > 1 else 1.0

resultado = dt.puls_routing(inflow, dt_min=dt_min, reservatorio=res)
st.session_state.detencao = resultado
st.session_state.detencao_reservatorio = res


st.subheader("Resultado")
col1, col2, col3, col4 = st.columns(4)
col1.metric("Qp afluente", f"{resultado.Qp_in_m3_s:.2f} m³/s")
col2.metric("Qp efluente", f"{resultado.Qp_out_m3_s:.2f} m³/s")
col3.metric("Atenuação", f"{resultado.atenuacao_pct:.1f} %")
col4.metric("NA máx atingido", f"{z_fundo + resultado.h_max_m:.2f} m")

col1, col2, col3 = st.columns(3)
col1.metric("Volume armazenado máx", f"{resultado.volume_armazenado_max_m3:,.0f} m³")
col2.metric("Extravasou?", "Sim ⚠️" if resultado.h_max_m >= h_max * 0.99 else "Não")
borda_livre_m = max(h_max - resultado.h_max_m, 0.0)
borda_livre_pct = 100.0 * borda_livre_m / h_max if h_max > 0 else 0.0
col3.metric(
    "Borda livre disponível",
    f"{borda_livre_m:.2f} m",
    delta=f"{borda_livre_pct:.0f} % de h_máx",
    delta_color="off",
)


# ---------------------------------------------------------------------------
# Plot comparativo
# ---------------------------------------------------------------------------

fig = go.Figure()
fig.add_trace(go.Scatter(
    x=resultado.tempo_min, y=resultado.inflow_m3_s,
    name="Afluente", mode="lines",
    line=dict(color="#1f77b4", width=2),
    fill="tozeroy", fillcolor="rgba(31,119,180,0.15)",
))
fig.add_trace(go.Scatter(
    x=resultado.tempo_min, y=resultado.outflow_m3_s,
    name="Efluente", mode="lines",
    line=dict(color="#d62728", width=2),
    fill="tozeroy", fillcolor="rgba(214,39,40,0.15)",
))
fig.update_layout(
    title="Roteamento pelo Reservatório (Puls)",
    xaxis_title="Tempo (min)",
    yaxis_title="Vazão (m³/s)",
    template="plotly_white",
    height=450,
    hovermode="x unified",
)
st.plotly_chart(fig, use_container_width=True)


# Lamina (NA absoluto) no tempo
fig2 = go.Figure()
fig2.add_trace(go.Scatter(
    x=resultado.tempo_min, y=resultado.h_m + z_fundo,
    mode="lines", line=dict(color="#2ca02c", width=2),
    fill="tozeroy", fillcolor="rgba(44,160,44,0.15)",
    name="Nível d'água",
    hovertemplate="t=%{x:.0f} min<br>z=%{y:.2f} m<extra></extra>",
))
fig2.add_hline(y=z_vert_abs, line_dash="dash", line_color="#d62728",
               annotation_text=f"vertedor @ {z_vert_abs:.2f} m")
fig2.add_hline(y=z_max_abs, line_dash="dot", line_color="black",
               annotation_text=f"NA máx = {z_max_abs:.2f} m")
fig2.add_hline(y=z_fundo, line_dash="dot", line_color="#6b4226",
               annotation_text=f"fundo @ {z_fundo:.2f} m")
fig2.update_layout(
    title=f"Nível d'água (datum: {datum_vertical or 'não informado'})",
    xaxis_title="Tempo (min)",
    yaxis_title="Cota absoluta z (m)",
    template="plotly_white",
    height=350,
)
fig2.update_yaxes(range=[z_fundo - 0.1, z_max_abs + 0.2])
st.plotly_chart(fig2, use_container_width=True)


# Decomposicao da descarga no tempo
fig3 = plots.plot_descarga_decomposta(resultado, res)
st.plotly_chart(fig3, use_container_width=True)


# Caracteristicas do reservatorio (curva cota x volume x descarga)
st.subheader("Características do reservatório")
fig4 = plots.plot_cota_volume_descarga(res)
st.plotly_chart(fig4, use_container_width=True)


with st.expander("Tabela do roteamento"):
    df_show = resultado.to_dataframe().copy()
    df_show["z_abs_m"] = df_show["h_m"] + z_fundo
    st.dataframe(df_show.round(3), use_container_width=True)

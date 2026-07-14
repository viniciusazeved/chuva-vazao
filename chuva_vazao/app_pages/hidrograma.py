"""Página 3: transformação chuva-vazão (Racional OU SCS-HU) + tempo de concentração."""
from __future__ import annotations

import io

import geopandas as gpd
import pandas as pd
import streamlit as st

from chuva_vazao import hidrograma as hg_mod
from chuva_vazao import plots
from chuva_vazao import tempo_concentracao as tc_mod


def _bacia_polygon_from_session():
    """Reconstroi shapely Polygon da bacia salva no session_state, ou None."""
    bres = st.session_state.get("basin_result")
    if bres is None:
        return None
    try:
        gdf = gpd.read_file(io.StringIO(bres["basin_geojson"]))
        if gdf.crs is None:
            gdf.set_crs(epsg=4326, inplace=True)
        elif gdf.crs.to_epsg() != 4326:
            gdf = gdf.to_crs(epsg=4326)
        return gdf.union_all()
    except Exception:
        return None


def _render_calc_gee_auto(metodo: str):
    """Bloco 'Calcular C/CN do GEE' — aparece em ambos os métodos."""
    from chuva_vazao import landuse

    bacia_poly = _bacia_polygon_from_session()
    if bacia_poly is None:
        st.info(
            "Para calcular C/CN automaticamente via GEE, delineie uma bacia "
            "primeiro na **Página 0 (Bacia)**."
        )
        return None

    with st.expander(
        "🛰️ Calcular C/CN automaticamente do GEE (MapBiomas + SoilGrids)",
        expanded=False,
    ):
        col1, col2, col3 = st.columns(3)
        fonte = col1.selectbox(
            "Fonte LULC",
            ["mapbiomas (30 m, Brasil)", "dynamic_world (10 m, global)"],
            index=0,
            help="Mapa de uso e cobertura do solo usado para estimar C e CN. "
            "MapBiomas é 30 m e calibrado para o Brasil (recomendado); Dynamic "
            "World é 10 m e global, útil para detalhe fino em área urbana, mas "
            "menos aderente às classes brasileiras.",
        )
        ano = col2.number_input(
            "Ano LULC", 2017, 2024, 2023, 1,
            help="Ano do mapa de uso do solo. Use o ano mais recente disponível "
            "ou o do cenário que quer representar; anos antigos refletem menos "
            "urbanização e tendem a dar C e CN menores.",
        )
        cond_label = col3.selectbox(
            "Condição da floresta",
            ["Boa / média (padrão)", "Densa / primária", "Secundária / degradada"],
            index=0,
            help="Condição hidrológica da vegetação nativa arbórea (parâmetro do "
            "TR-55). O CN de tabela é 'boa' (floresta protegida, ~55 em grupo B). "
            "'Densa/primária' (Mata Atlântica preservada, infiltra mais) baixa ~5 "
            "pontos (~50); 'secundária/degradada' sobe ~6. Afeta só floresta/savana/"
            "restinga arbórea, não pasto/agricultura/urbano.",
        )
        _COND_MAP = {
            "Boa / média (padrão)": "boa",
            "Densa / primária": "densa",
            "Secundária / degradada": "degradada",
        }
        condicao_floresta = _COND_MAP.get(cond_label, "boa")
        calc_btn = st.button("Calcular do GEE", type="primary")

        fonte_key = "mapbiomas" if fonte.startswith("mapbiomas") else "dynamic_world"

        if calc_btn:
            with st.spinner("Baixando LULC e solo do GEE e calculando..."):
                try:
                    lu = landuse.compute_c_and_cn(
                        bacia_poly, fonte_lulc=fonte_key, ano_lulc=int(ano),
                        condicao_floresta=condicao_floresta,
                    )
                    st.session_state["landuse_result"] = lu
                    st.success("C/CN calculados. Valores aplicados nos campos abaixo.")
                except Exception as exc:
                    st.error(f"Falhou: {exc}")

        lu = st.session_state.get("landuse_result")
        if lu is not None:
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("C (Racional)", f"{lu.C_racional:.2f}")
            c2.metric("CN (SCS)", f"{lu.CN_scs:.1f}")
            c3.metric("GH dominante", lu.gh_dominante)
            c4.metric("Área analisada", f"{lu.area_km2:.2f} km²")

            st.caption("**Composição de uso do solo:**")
            st.dataframe(
                lu.composicao_lulc[["frac", "area_km2", "C"]]
                .rename(columns={"frac": "fração", "area_km2": "área (km²)"})
                .round(3),
                use_container_width=True,
            )
            st.caption(
                f"Fontes: LULC = `{lu.fonte_lulc}`, solo = `{lu.fonte_solo}`. "
                "Os valores C/CN são médias ponderadas por pixel."
            )
            if getattr(lu, "declividade_considerada", False):
                st.caption(
                    "Grupo hidrológico **agravado por declividade** (DEM Copernicus) "
                    "em encostas íngremes — solo raso de encosta responde mais "
                    "rápido que a textura sozinha indicaria."
                )
            _cond = getattr(lu, "condicao_floresta", "boa")
            if _cond != "boa":
                _txt = {
                    "densa": "densa / primária (CN −5 na vegetação nativa)",
                    "degradada": "secundária / degradada (CN +6 na vegetação nativa)",
                }.get(_cond, _cond)
                st.caption(f"Condição da floresta aplicada: **{_txt}**.")
            return lu
    return st.session_state.get("landuse_result")


def _render_distribuido(area_km2: float):
    """
    Modelo semi-distribuido por sub-bacias (A > 250 km2): discretiza a bacia,
    gera o hidrograma SCS de cada sub-bacia (CN da ANA + tc proprio) e roteia
    por Muskingum-Cunge ate o exutorio.
    """
    from pathlib import Path

    from chuva_vazao import distribuido as dist

    st.subheader("Modelo semi-distribuído por sub-bacias")
    st.caption(
        "Discretiza a bacia em sub-bacias (WhiteboxTools), gera o hidrograma SCS "
        "de cada uma (CN da ANA + tc próprio) e roteia por Muskingum-Cunge até o "
        "exutório — o caminho recomendado acima de 250 km²."
    )

    dem_path = st.session_state.get("_dem_path")
    lat = st.session_state.get("exutorio_lat")
    lon = st.session_state.get("exutorio_lon")
    idf = st.session_state.get("idf_params")
    TR = st.session_state.get("TR", 25)

    faltam = []
    if not dem_path or not Path(dem_path).exists():
        faltam.append("DEM + bacia delineada (**Página 0**)")
    if lat is None or lon is None:
        faltam.append("exutório (**Página 0**)")
    if idf is None:
        faltam.append("equação IDF (**Página 1**)")
    if faltam:
        st.warning("Para rodar o modelo distribuído, falta: " + "; ".join(faltam) + ".")
        return

    c1, c2, c3 = st.columns(3)
    TR_in = c1.number_input(
        "TR (anos)", 2, 1000, int(TR), 1,
        help="Período de retorno da chuva de projeto: de quantos em quantos anos, "
        "em média, o evento é igualado ou superado. Maior TR significa chuva mais "
        "intensa pela IDF e vazão de pico maior. Escolha conforme o porte e o risco "
        "da obra.",
    )
    c1.caption(
        "Prática de drenagem: microdrenagem (sarjetas, galerias) 2 a 10 anos; "
        "macrodrenagem e canais 25 a 100; travessias e bueiros rodoviários 50 a "
        "100; grandes estruturas (vertedouros, barragens) 100 a 1000 (o campo "
        "aceita até 1000)."
    )
    D_h = c2.number_input(
        "Duração da chuva (h)", 1.0, 72.0, 12.0, 1.0,
        help="Duração total da chuva de projeto. Para captar o pico, use duração "
        "pelo menos igual ao tempo de concentração da bacia; em bacias grandes (tc "
        "de várias horas) use durações longas. A duração também entra no ARF: "
        "durações maiores reduzem menos a chuva pontual.",
    )
    c2.caption(
        "Regra prática: D ≈ tc da bacia, ou um pouco maior. Ordem de grandeza: "
        "bacias de dezenas de km² 3 a 6 h; centenas de km² 6 a 24 h; milhares de "
        "km² 24 a 48 h."
    )
    dt_min = c3.number_input(
        "Passo dt (min)", 5, 120, 30, 5,
        help="Passo de tempo da simulação (hietograma, hidrógrafa unitária e "
        "roteamento). Menor dt resolve melhor o pico, ao custo de mais "
        "processamento; não altera o volume escoado, só a resolução da curva.",
    )
    c3.caption(
        "Tipicamente 5 a 30 min em bacias urbanas e médias, 15 a 60 min em bacias "
        "grandes. Como referência numérica, evite dt maior que ~1/5 do tc da menor "
        "sub-bacia, senão o pico tende a ser subestimado."
    )
    c4, c5, c6 = st.columns(3)
    thr = c4.number_input(
        "Discretização (células de canal)", 2000, 60000, 12000, 1000,
        help="Número de células acumuladas a montante para iniciar um canal. "
        "Controla quão fina é a divisão em sub-bacias: valor menor cria mais canais "
        "e mais sub-bacias (modelo mais detalhado e mais lento); valor maior agrupa "
        "em poucas sub-bacias grandes. Não muda a bacia total, só o nível de "
        "detalhe interno.",
    )
    c4.caption(
        "A área-limiar para iniciar um canal é células × área do pixel. Com DEM de "
        "~30 m (pixel ≈ 0,0009 km²): 12.000 ≈ 10,8 km²; 2.000 ≈ 1,8 km²; 60.000 ≈ "
        "54 km². Com DEM mais grosso (ex.: 90 m) multiplique por ~9. Mire numa "
        "discretização de poucas dezenas de sub-bacias."
    )
    metodo_h = c5.selectbox(
        "Hietograma", ["blocos", "huff"],
        help="Como a chuva total é distribuída ao longo da duração. 'blocos' "
        "(blocos alternados, tipo Chicago) concentra a intensidade no centro e "
        "tende a dar pico mais alto; 'huff' usa as curvas estatísticas de Huff (2º "
        "quartil) e em geral suaviza o pico. O volume total é o mesmo nos dois.",
    )
    X_musk = c6.number_input(
        "X (Muskingum)", 0.0, 0.5, 0.25, 0.05,
        help="Fator de ponderação entre vazão de entrada e de saída no "
        "armazenamento do trecho (Muskingum). Controla quanto o trecho atenua o "
        "pico: 0 = máxima atenuação (atua como reservatório linear); 0,5 = "
        "translação pura, a onda passa sem amortecer.",
    )
    c6.caption(
        "Rios naturais com planície de inundação: 0,2 a 0,3. Canais regulares ou "
        "retificados, próximos da translação pura: 0,4 a 0,5. Use 0 só para "
        "reservatório ou lago. Faixa física válida 0 a 0,5."
    )
    c7, c8 = st.columns(2)
    arf_auto = c7.checkbox(
        "ARF automático (Leclerc-Schaake)", value=True,
        help="Liga o cálculo automático do fator de redução de área (ARF), que "
        "converte a chuva pontual da IDF na chuva média sobre a bacia inteira. "
        "Marcado: usa a fórmula de Leclerc-Schaake, função da área e da duração. "
        "Desmarcado: você informa o ARF à mão no campo ao lado.",
    )
    arf_manual = None
    if not arf_auto:
        arf_manual = float(c8.number_input(
            "ARF manual", 0.1, 1.0, 0.85, 0.01,
            help="Fator de redução de área informado à mão: multiplica a chuva "
            "pontual para obter a chuva média na bacia. 1,0 = sem redução (bacias "
            "pequenas); valores menores reduzem a chuva e o pico em bacias grandes, "
            "onde a tempestade não cobre tudo com a mesma intensidade.",
        ))
        c8.caption(
            "Tipicamente 0,80 a 0,95 para bacias de dezenas a centenas de km² (pela "
            "fórmula do app, A=300 km² dá ~0,88 em 12 h e ~0,91 em 24 h). Cai abaixo "
            "de 0,70 só em bacias muito grandes com chuvas curtas. 1,0 = sem "
            "abatimento. Confira contra o ARF automático antes de fixar."
        )
    fonte_cn = st.selectbox(
        "Fonte do CN por sub-bacia",
        ["GEE (MapBiomas + SoilGrids)", "ANA (BHAE local)", "Manual (calibração)"],
        help="Define de onde vem o CN de cada sub-bacia. GEE: calcula via "
        "MapBiomas + solo (espacializado, roda no Cloud, coerente com o Racional/"
        "SCS concentrado). ANA: usa o CN das ottobacias do BHAE local (arquivo de "
        "~5,6 GB, não roda no Cloud). Manual: força um único CN em todas, útil para "
        "calibrar.",
    )
    c9, c10 = st.columns(2)
    cn_manual_in, ano_lulc, bhae = 0.0, 2023, "data/BHAE_CN2022.gpkg"
    if fonte_cn.startswith("Manual"):
        cn_manual_in = c9.number_input(
            "CN manual", 30.0, 100.0, 72.0, 1.0,
            help="CN do SCS único aplicado a todas as sub-bacias. Resume solo e uso "
            "do solo num índice: maior CN significa solo mais impermeável ou "
            "saturado, mais escoamento (S = 25400/CN − 254) e pico maior. 72 foi o "
            "valor calibrado para o Bananal.",
        )
        c9.caption(
            "CN típico em umidade média (AMC II): floresta em solo bem drenado 30 a "
            "60; pasto e campo 50 a 80; agricultura 65 a 90; urbano 60 a 90; "
            "superfícies quase impermeáveis ~98. Abaixo de 30 não tem sentido "
            "físico, por isso o mínimo aqui é 30 (como no SCS-HU concentrado)."
        )
    elif fonte_cn.startswith("GEE"):
        ano_lulc = c9.number_input(
            "Ano do MapBiomas", 2017, 2024, 2023, 1,
            help="Ano do mapa MapBiomas usado para calcular o CN de cada sub-bacia. "
            "Use o ano mais recente disponível ou o do cenário desejado; anos "
            "antigos têm menos área urbana e tendem a CN menor.",
        )
        c10.caption("Calcula o CN de cada sub-bacia via GEE (1 consulta por sub-bacia "
                    "— mais lento, mas espacializado e Cloud-ready).")
    else:  # ANA
        bhae = c10.text_input(
            "Arquivo de CN da ANA", value="data/BHAE_CN2022.gpkg",
            help="Caminho do GeoPackage de ottobacias da ANA (BHAE) com o campo de "
            "CN (CN2.ANA_med). O modelo cruza cada sub-bacia com essas ottobacias e "
            "pondera o CN pela área de interseção. Arquivo local de ~5,6 GB, não "
            "disponível no Cloud. Se uma sub-bacia não for coberta pelas ottobacias, "
            "recebe CN 70 nos trechos sem dado. Se o arquivo não for encontrado, a "
            "rodada por esta fonte falha (use GEE ou Manual).",
        )
    c11, c12 = st.columns(2)
    celeridade = c11.number_input(
        "Celeridade do canal (m/s)", 0.2, 5.0, 1.0, 0.1,
        help="Velocidade de propagação da onda de cheia no canal (não é a "
        "velocidade da água: é cerca de 5/3 dela). Define o atraso do roteamento "
        "Muskingum (K = L/c): celeridade menor deixa a onda mais lenta, espalha o "
        "hidrograma e reduz o pico. É o principal parâmetro de calibração do "
        "distribuído.",
    )
    c11.caption(
        "Rios naturais 1 a 1,5 m/s; com planície ou várzea que amortecem 0,4 a 0,8; "
        "canais retificados ou encaixados 2 a 3. O Bananal calibrou ~0,4. Faixa do "
        "app 0,2 a 5."
    )
    prf = c12.number_input(
        "Peak factor (PRF)", 100, 600, 484, 4,
        help="Fator de pico da hidrógrafa unitária do SCS. 484 é o padrão (bacias "
        "mistas); valores menores alargam a base e achatam o pico, próprios de "
        "bacias planas com várzea e armazenamento; maiores afinam e elevam o pico, "
        "próprios de bacias íngremes e rápidas. Conserva o volume.",
    )
    c12.caption(
        "Padrão 484. Bacias planas com muito armazenamento (planície, pântano): 256 "
        "a 300 ou menos. Bacias muito íngremes e rápidas: até ~600. Faixa usual 100 "
        "a 600."
    )

    if not st.button("Rodar modelo distribuído", type="primary"):
        return

    bhae_path = None
    if fonte_cn.startswith("ANA"):
        bhae_path = Path(bhae) if bhae and Path(bhae).exists() else None
        if bhae_path is None:
            st.info(f"Arquivo de CN não encontrado em `{bhae}` — usando CN default (70).")

    try:
        snap_m = (st.session_state.get("basin_result") or {}).get("snap_method", "accumulation")
        with st.spinner("Discretizando a bacia em sub-bacias..."):
            disc = dist.discretizar_bacia(float(lat), float(lon), Path(dem_path),
                                          stream_threshold=int(thr), snap_method=snap_m)
        cn_ext = None
        if fonte_cn.startswith("GEE"):
            with st.spinner("Calculando o CN de cada sub-bacia via GEE (MapBiomas + solo)..."):
                cn_ext = dist.calcular_cn_gee(disc, fonte_lulc="mapbiomas", ano_lulc=int(ano_lulc))
        with st.spinner("Calculando tc e CN por sub-bacia..."):
            params = dist.calcular_parametros(
                disc, bhae_path=bhae_path,
                cn_manual=(cn_manual_in if cn_manual_in > 0 else None),
                cn_por_sub_externo=cn_ext,
            )
        with st.spinner("Gerando hidrogramas e roteando pela rede..."):
            chuva = dist.chuva_projeto_distribuida(
                disc.area_total_km2, idf, TR=float(TR_in),
                duracao_total_min=float(D_h) * 60.0, dt_min=float(dt_min),
                metodo=metodo_h, arf_manual=arf_manual,
            )
            hid = dist.hidrogramas_locais(params, chuva, prf=float(prf))
            res = dist.rotear_rede(disc, params, hid, X_musk=float(X_musk),
                                   celeridade_ms=float(celeridade))
    except Exception as exc:
        st.error(f"Falhou: {exc}")
        return

    for aviso in disc.avisos:
        st.warning(aviso)

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Q pico", f"{res.Qpico_m3s:,.0f} m³/s")
    m2.metric("Tempo ao pico", f"{res.t_pico_min / 60:.1f} h")
    m3.metric("Volume", f"{res.volume_hm3:.1f} hm³")
    m4.metric("Sub-bacias", disc.n_subbacias)
    st.caption(
        f"ARF = {chuva.arf:.2f} (chuva pontual {chuva.altura_pontual_mm:.0f} mm → "
        f"areal {chuva.altura_areal_mm:.0f} mm) · vazão específica de pico = "
        f"{res.Qpico_m3s / disc.area_total_km2:.2f} m³/s/km²"
    )

    import pandas as pd
    import plotly.graph_objects as go

    gdf = disc.to_geodataframe().to_crs(disc.crs_utm)
    ids = gdf["id"].tolist()
    fig_topo = plots.fig_topologia_subbacias(gdf, ids, disc.grafo(), disc.exutorio_id)
    fig_cn = plots.fig_subbacias_choropleth(gdf, ids, [params[i].cn for i in ids], label="CN2")
    fig_tc = plots.fig_subbacias_choropleth(gdf, ids, [params[i].tc_min for i in ids], label="tc (min)")
    fig_hidro = go.Figure()
    fig_hidro.add_trace(go.Scatter(
        x=res.tempo_min / 60.0, y=res.exutorio["Q_m3s"].to_numpy(),
        mode="lines", line=dict(color="#08519c", width=2.5),
        fill="tozeroy", fillcolor="rgba(8,81,156,0.15)",
    ))
    fig_hidro.update_layout(xaxis_title="tempo (h)", yaxis_title="Q (m³/s)", height=430,
                            margin=dict(l=10, r=10, t=30, b=10), showlegend=False)

    cA, cB = st.columns(2)
    with cA:
        st.markdown(f"**Discretização e rede** · {disc.n_subbacias} sub-bacias (★ exutório)")
        st.plotly_chart(fig_topo, use_container_width=True)
    with cB:
        st.markdown("**Hidrograma no exutório**")
        st.plotly_chart(fig_hidro, use_container_width=True)

    cC, cD = st.columns(2)
    with cC:
        st.markdown(f"**CN2 por sub-bacia ({fonte_cn.split()[0]})**")
        st.plotly_chart(fig_cn, use_container_width=True)
    with cD:
        st.markdown("**Tempo de concentração por sub-bacia**")
        st.plotly_chart(fig_tc, use_container_width=True)

    df = pd.DataFrame([
        {"id": p.id, "A (km²)": round(p.area_km2, 1), "L (km)": round(p.L_km, 2),
         "ΔH (m)": round(p.delta_h_m, 0), "S (%)": round(p.declividade_pct, 1),
         "tc (min)": round(p.tc_min, 0), "CN2": round(p.cn, 1) if p.cn else None,
         "deflui em": p_down if (p_down := disc.grafo().get(p.id)) is not None else "exutório"}
        for p in params.values()
    ]).sort_values("id")
    st.markdown("**Parâmetros por sub-bacia**")
    st.dataframe(df, use_container_width=True, hide_index=True)

    st.session_state["distribuido_result"] = {
        "Qpico_m3s": res.Qpico_m3s, "t_pico_min": res.t_pico_min,
        "volume_hm3": res.volume_hm3, "arf": chuva.arf, "TR": TR_in,
        "duracao_h": D_h, "n_subbacias": disc.n_subbacias,
        "area_km2": disc.area_total_km2, "celeridade_ms": float(celeridade),
        "fonte_cn": fonte_cn.split()[0], "prf": float(prf),
        "hidrograma": res.exutorio.reset_index(), "tabela": df,
        "fig_hidrograma": fig_hidro, "fig_mapa_cn": fig_cn,
        "fig_tc": fig_tc, "fig_topologia": fig_topo,
    }
    # Disponibiliza a vazao de projeto para as paginas 4-7 (mesmas chaves do
    # concentrado): hidrograma do exutorio com a coluna Q_m3s.
    st.session_state.hidrograma = res.exutorio
    st.session_state.scs_params = None
    st.session_state.metodo_chuva_vazao = "Distribuído"
    st.success(
        f"Modelo distribuído calculado ✓ — vazão de projeto (Qp = {res.Qpico_m3s:,.0f} "
        "m³/s) disponível para as páginas 4–7 e para o Relatório (página 8)."
    )


st.title("3. Transformação Chuva-Vazão")
st.caption(
    "Escolhe automaticamente Racional (A ≤ 2 km²) ou SCS-HU (2 < A ≤ 250). "
    "Calcula tempo de concentração por Kirpich/Chow/California."
)

hieto = st.session_state.get("hietograma")
if hieto is None:
    st.error("Gere o hietograma na Página 2 antes.")
    st.stop()


# ---------------------------------------------------------------------------
# Bacia
# ---------------------------------------------------------------------------

st.subheader("Parâmetros da bacia")
col1, col2 = st.columns(2)
with col1:
    area = st.number_input(
        "Área (km²)", min_value=0.01, max_value=10_000.0,
        value=float(st.session_state.area_km2), step=0.5,
        help="Área de drenagem da bacia até o exutório. Define o método "
        "recomendado (≤2 km² Racional, 2 a 250 SCS-HU, >250 distribuído) e entra "
        "direto na vazão de pico (no Racional Q = C·i·A/3,6; no SCS o pico cresce "
        "proporcional à área). Se você delineou a bacia na Página 0, já vem "
        "preenchida.",
    )
    st.caption(
        "Microbacias: 0,01 a 2 km² (Racional). Bacias médias: 2 a 250 km² "
        "(SCS-HU). Acima de 250 km² o concentrado perde precisão (chuva não "
        "uniforme, tempo de viagem variável); use o distribuído."
    )
    st.session_state.area_km2 = area
    metodo_default = hg_mod.select_method(area)
    st.info(f"Método recomendado para A={area:g} km²: **{metodo_default}**")

with col2:
    st.markdown("**Tempo de concentração**")
    # Pre-preencher L e H da bacia delineada se disponivel
    L_default = float(st.session_state.get("bacia_L_km", 1.0))
    H_default = float(st.session_state.get("bacia_H_m", 20.0))
    if "bacia_L_km" in st.session_state:
        st.caption(
            f"L e ΔH pré-preenchidos pela bacia delineada (Página 0): "
            f"L = {L_default:.3f} km, ΔH = {H_default:.1f} m."
        )
    with st.expander("Calcular tc pelas fórmulas de Kirpich/Chow/California", expanded=True):
        c1, c2 = st.columns(2)
        with c1:
            L_km = st.number_input(
                "L (canal principal, km)", 0.01, 500.0, L_default, 0.1, format="%.3f",
                help="Comprimento do canal principal (talvegue), do ponto mais "
                "distante até o exutório. Entra nas três fórmulas de tc (Kirpich, "
                "Chow, California): L maior aumenta o tc, atrasando e achatando o "
                "pico. Se delineou a bacia na Página 0, vem pré-preenchido.",
            )
            st.caption(
                "Ordem de grandeza pelo tamanho da bacia (lei de Hack, L[km] ≈ "
                "1,4·A[km²]^0,6): ~1,4 km para 1 km²; ~6 a 15 km para bacias de 10 a "
                "50 km²; dezenas de km acima de 100 km². Ajuste ao valor real da sua "
                "bacia."
            )
        with c2:
            H_m = st.number_input(
                "H (desnível, m)", 0.5, 3000.0, H_default, 1.0, format="%.1f",
                help="Desnível altimétrico ao longo do canal principal (cota da "
                "cabeceira menos cota do exutório). Define a declividade S = H/L "
                "usada no tc: maior desnível deixa o canal mais íngreme, reduz o tc "
                "e adianta o pico.",
            )
            st.caption(
                "Depende do relevo: poucos metros em planície, dezenas a centenas de "
                "metros em relevo ondulado a montanhoso. A declividade resultante "
                "S = H/L costuma cair, em ordem de grandeza, entre 0,001 e 0,1 m/m "
                "(verifique para o seu caso)."
            )
        if st.button("Calcular tc"):
            r = tc_mod.tempo_concentracao_completo(L_km=L_km, H_m=H_m)
            d = r.to_dict()
            st.dataframe(
                pd.DataFrame([d]).T.rename(columns={0: "tc (min)"}).round(2),
                use_container_width=True,
            )
            st.session_state.tc_h = d["Media"] / 60.0
            st.session_state.tc_breakdown = d
            st.success(
                f"tc médio = {d['Media']:.1f} min ({d['Media']/60:.2f} h). "
                f"Aplicado. Kirpich = {d['Kirpich']:.1f} min, "
                f"Chow = {d['Ven Te Chow']:.1f} min, California = {d['California']:.1f} min."
            )
    tc_h = st.number_input(
        "tc adotado (h)", min_value=0.05, max_value=48.0,
        value=float(st.session_state.tc_h), step=0.1, format="%.2f",
        help="Tempo de concentração adotado: o tempo que a água da parte mais "
        "distante leva para chegar ao exutório. No Racional fixa a duração da chuva "
        "(i = i(tc)); no SCS controla o atraso da hidrógrafa (t_lag = 0,6·tc). tc "
        "maior dá pico mais baixo e mais tardio. Use o botão acima para estimar "
        "pelas fórmulas.",
    )
    st.caption(
        "Ordem de grandeza por porte: microbacias urbanas 0,1 a 0,5 h (6 a 30 min); "
        "bacias de dezenas de km² 1 a 4 h; bacias grandes 6 h ou mais. São valores "
        "indicativos; prefira a estimativa pelas fórmulas."
    )
    st.session_state.tc_h = tc_h


# ---------------------------------------------------------------------------
# Metodo manual (override)
# ---------------------------------------------------------------------------

metodo_escolhido = st.radio(
    "Método (permite override do default):",
    ["Automático", "Racional (forçar)", "SCS-HU (forçar)", "Distribuído (forçar)"],
    index=0, horizontal=True,
    help="Escolhe a transformação chuva-vazão. 'Automático' segue a regra por área "
    "(≤2 Racional, 2 a 250 SCS-HU, >250 distribuído). As opções 'forçar' fazem "
    "override, úteis para comparar métodos ou rodar o distribuído numa bacia menor.",
)

if metodo_escolhido == "Automático":
    metodo = metodo_default
elif metodo_escolhido.startswith("Racional"):
    metodo = "Racional"
elif metodo_escolhido.startswith("SCS"):
    metodo = "SCS-HU"
else:
    metodo = "Distribuído"

if metodo == "Distribuído":
    _render_distribuido(area)
    st.stop()

if area > 250.0:
    st.warning(
        f"Área = {area:g} km² (> 250) e você está no **{metodo} concentrado**. "
        "Acima de ~250 km² o hidrograma unitário único perde precisão (chuva "
        "não-uniforme, tempo de viagem variável). Serve como estimativa "
        "preliminar — o método **Distribuído** (sub-bacias + roteamento) é o "
        "recomendado: selecione no rádio acima. Você pode seguir mesmo assim.",
        icon="⚠️",
    )


# ---------------------------------------------------------------------------
# Metodo Racional
# ---------------------------------------------------------------------------

lu_result = _render_calc_gee_auto(metodo)

if metodo == "Racional":
    st.subheader("Método Racional")
    st.caption("Q = C · i(tc) · A / 3.6. Usa a intensidade IDF na duração = tc.")

    col1, col2 = st.columns([1, 2])
    with col1:
        uso_solo = st.selectbox(
            "Uso do solo sugerido",
            list(hg_mod.C_USO_SOLO.keys()),
            index=list(hg_mod.C_USO_SOLO.keys()).index("Residencial densa (>40% impermeabilizado)"),
            help="Categoria de uso e ocupação do solo que sugere o coeficiente C "
            "do Racional. Quanto mais impermeável a categoria, maior o C e maior a "
            "vazão. É apenas uma sugestão de tabela; você pode ajustar o C no campo "
            "ao lado ou usar o valor do GEE.",
        )
        st.caption(
            "Pela tabela do app, C vai de 0,10 (solo arenoso plano, parques) a 0,90 "
            "(concreto): área central densa 0,85, residencial 0,35 a 0,65, "
            "industrial 0,60 a 0,75."
        )
        C_sugerido = hg_mod.C_USO_SOLO[uso_solo]
        if lu_result is not None:
            C_sugerido = lu_result.C_racional
            st.caption(f"C sugerido pelo GEE: {lu_result.C_racional:.3f} (sobrepõe a tabela acima).")
        C = st.number_input(
            "Coeficiente C", min_value=0.05, max_value=0.99,
            value=float(C_sugerido), step=0.05,
            help="Coeficiente de escoamento do Racional: a fração da chuva que vira "
            "escoamento superficial. Entra direto na vazão (Q = C·i·A/3,6), então "
            "dobrar C dobra a vazão. Use a tabela ao lado ou o valor do GEE como "
            "ponto de partida.",
        )
        st.caption(
            "Por uso do solo (TR 5 a 10 anos): áreas verdes e permeáveis 0,10 a "
            "0,25; residencial 0,35 a 0,65; comercial e industrial 0,60 a 0,85; "
            "asfalto, concreto e telhado 0,80 a 0,95. Eleve um pouco para TR altos."
        )
    with col2:
        st.metric("Duração adotada = tc", f"{tc_h * 60:.1f} min")
        i_tc = st.session_state.idf_params.intensidade(
            TR=st.session_state.TR, duracao_min=tc_h * 60,
        )
        st.metric(f"Intensidade i(TR={st.session_state.TR}, t=tc)", f"{i_tc:.2f} mm/h")

    Q_racional = hg_mod.rational_method(C=C, i_mmh=i_tc, A_km2=area)

    col1, col2, col3 = st.columns(3)
    col1.metric("Q_pico (Racional)", f"{Q_racional:.2f} m³/s")
    col2.metric("C adotado", f"{C:.2f}")
    col3.metric("Área", f"{area:g} km²")

    hg_sint = hg_mod.hidrograma_triangular_sintetico(Q_racional, tc_min=tc_h * 60)
    hg_sint["hietograma_mm"] = 0.0
    hg_sint["excedente_mm"] = 0.0
    hg_sint = hg_sint[["hietograma_mm", "excedente_mm", "Q_m3s"]]
    st.session_state.hidrograma = hg_sint
    st.session_state.scs_params = None
    st.session_state.metodo_chuva_vazao = "Racional"
    st.session_state.Q_pico_racional = Q_racional
    st.session_state.C_racional = C
    st.session_state.uso_solo_racional = uso_solo

    st.plotly_chart(
        plots.plot_hidrograma(hg_sint, titulo="Hidrograma triangular sintético (Racional)"),
        use_container_width=True,
    )

    st.info(
        "Observação: Racional entrega apenas Q_pico. Para usar no módulo de "
        "detenção (Puls), um hidrograma triangular sintético foi gerado com "
        "t_pico = tc e t_base = 2.67·tc (SCS)."
    )


# ---------------------------------------------------------------------------
# Metodo SCS-HU
# ---------------------------------------------------------------------------

else:  # SCS-HU
    st.subheader("Método SCS-HU")
    col1, col2 = st.columns(2)
    with col1:
        CN_default = float(st.session_state.CN)
        if lu_result is not None:
            CN_default = float(lu_result.CN_scs)
            st.caption(
                f"CN sugerido pelo GEE: {lu_result.CN_scs:.1f} "
                f"(GH dominante: {lu_result.gh_dominante})."
            )
        CN = st.number_input(
            "CN (Curve Number)", min_value=30.0, max_value=100.0,
            value=CN_default, step=1.0, format="%.1f",
            help="Curve Number do SCS: resume solo e uso do solo num índice de 30 a "
            "100. Maior CN significa solo mais impermeável ou saturado, mais "
            "escoamento (S = 25400/CN − 254). É o parâmetro que mais altera a vazão "
            "no SCS-HU. O cálculo do GEE pode preencher automaticamente.",
        )
        st.caption(
            "AMC II (umidade média): floresta em solo bem drenado 30 a 60; pasto e "
            "campo 50 a 80; agricultura 65 a 90; urbano 60 a 90; superfícies "
            "impermeáveis ~98. Solo muito úmido (AMC III) sobe ~10 a 15 pontos."
        )
        st.session_state.CN = CN

    scs = hg_mod.SCSParams(area_km2=area, tempo_concentracao_h=tc_h, CN=CN)
    st.session_state.scs_params = scs

    col1, col2 = st.columns(2)
    col1.metric("S (retenção)", f"{scs.S_mm:.2f} mm")
    col2.metric("Ia (abstração inicial)", f"{scs.Ia_mm:.2f} mm")

    hg_df = hg_mod.hidrograma_projeto(hieto, scs)
    st.session_state.hidrograma = hg_df
    st.session_state.metodo_chuva_vazao = "SCS-HU"

    st.subheader("Resultado")
    col1, col2, col3 = st.columns(3)
    col1.metric("Q pico", f"{hg_mod.Q_pico_m3s(hg_df):.2f} m³/s")
    col2.metric("Tempo ao pico", f"{hg_mod.tempo_ao_pico_min(hg_df):.1f} min")
    col3.metric("Volume escoado", f"{hg_mod.volume_escoado_m3(hg_df):,.0f} m³")

    st.plotly_chart(
        plots.plot_hietograma_hidrograma(hg_df, titulo="Hietograma + Hidrograma"),
        use_container_width=True,
    )

    with st.expander("Tabela do hidrograma"):
        st.dataframe(hg_df.round(3), use_container_width=True)

    # -----------------------------------------------------------------------
    # Varredura de duracao critica: a duracao adotada (Pagina 2) nem sempre e a
    # que maximiza o pico. Com CN baixo (Ia alto), chuvas mais longas geram mais
    # excedente e o pico cresce ate uma duracao critica > tc.
    # -----------------------------------------------------------------------
    with st.expander("🔎 Duração crítica (varredura)", expanded=False):
        idf_params = st.session_state.get("idf_params")
        if idf_params is None:
            st.info("Carregue a IDF na Página 1 para varrer durações.")
        else:
            from chuva_vazao import hietograma as _hie

            TR_atual = st.session_state.get("TR", 25)
            dt_atual = float(st.session_state.get("dt_min", 5) or 5)
            D_atual = float(st.session_state.get("duracao_min", 60) or 60)
            metodo_h = st.session_state.get(
                "metodo_hietograma", "Blocos Alternados (Chicago)"
            )
            quartil = int(st.session_state.get("huff_quartil", 2) or 2)
            st.caption(
                f"Testa várias durações com a mesma IDF (TR={TR_atual}), CN={CN:.0f} "
                f"e tc={tc_h * 60:.0f} min, método '{metodo_h}'. A duração de projeto "
                f"atual (Página 2) é {D_atual:.0f} min."
            )
            duracoes_teste = [
                d for d in [15, 30, 45, 60, 90, 120, 180, 240, 360, 480, 720, 1080, 1440]
                if d >= dt_atual
            ]
            linhas = []
            for D in duracoes_teste:
                dt_use = dt_atual if D <= 360 else max(dt_atual, 15.0)
                try:
                    if metodo_h.startswith("Huff"):
                        h = _hie.huff(idf_params, TR_atual, D, dt_use, quartil=quartil)
                    else:
                        h = _hie.blocos_alternados(idf_params, TR_atual, D, dt_use)
                    hgd = hg_mod.hidrograma_projeto(h, scs)
                    linhas.append({
                        "D (min)": D,
                        "P total (mm)": round(_hie.altura_total(h), 1),
                        "Q pico (m³/s)": round(hg_mod.Q_pico_m3s(hgd), 1),
                    })
                except Exception:
                    continue
            if linhas:
                df_var = pd.DataFrame(linhas)
                Q_atual = float(hg_mod.Q_pico_m3s(hg_df))
                tc_min = tc_h * 60.0
                st.line_chart(df_var.set_index("D (min)")["Q pico (m³/s)"])
                st.dataframe(df_var, use_container_width=True, hide_index=True)

                # Duracao de projeto para SCS-HU concentrado: D ~ tc a 2*tc
                # (ABNT/DAEE). Em blocos alternados o pico cresce SEM LIMITE com D
                # (empilha a intensidade de pico central + soma volume), entao o
                # maximo da varredura (sempre a maior D, ex. 24 h) NAO e a duracao
                # de projeto — e artefato do metodo. Reportamos o Q na faixa util.
                faixa = df_var[
                    (df_var["D (min)"] >= 0.9 * tc_min)
                    & (df_var["D (min)"] <= 2.2 * tc_min)
                ]
                Q_faixa = float(faixa["Q pico (m³/s)"].max()) if len(faixa) else Q_atual
                st.caption(
                    f"Duração de projeto usual do SCS-HU: **{tc_min:.0f}–{2 * tc_min:.0f} min** "
                    f"(tc a 2·tc). Nessa faixa, Q ≈ **{Q_faixa:.0f} m³/s** — é o valor "
                    f"a adotar. O pico cresce sem limite com D porque blocos "
                    f"alternados empilha a intensidade de pico e soma volume: **o "
                    f"valor de 24 h é artefato, não a duração de projeto**. Para "
                    f"chuvas longas, use Huff em vez de blocos alternados."
                )
                if D_atual < 0.9 * tc_min:
                    st.warning(
                        f"A duração atual ({D_atual:.0f} min) é **menor que o tc "
                        f"({tc_min:.0f} min)** — tende a subestimar o pico. Suba a "
                        f"duração na Página 2 para a faixa {tc_min:.0f}–{2 * tc_min:.0f} min."
                    )
                elif D_atual > 3.0 * tc_min:
                    st.warning(
                        f"A duração atual ({D_atual:.0f} min) é bem maior que 2·tc "
                        f"({2 * tc_min:.0f} min): em blocos alternados isso **infla o "
                        f"pico artificialmente**. Volte para ~tc a 2·tc "
                        f"({tc_min:.0f}–{2 * tc_min:.0f} min)."
                    )
                else:
                    st.success(
                        f"A duração atual ({D_atual:.0f} min) está na faixa de projeto "
                        f"(tc a 2·tc). Q de projeto consistente."
                    )


st.success(
    "Chuva-vazão pronto. Prossiga para **4. Verificação de Seção**, "
    "**5. Hidráulica**, **6. Detenção** ou **7. Inundação 1D**."
)

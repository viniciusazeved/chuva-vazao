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
        )
        ano = col2.number_input("Ano LULC", 2017, 2024, 2023, 1)
        calc_btn = col3.button("Calcular do GEE", type="primary")

        fonte_key = "mapbiomas" if fonte.startswith("mapbiomas") else "dynamic_world"

        if calc_btn:
            with st.spinner("Baixando LULC e solo do GEE e calculando..."):
                try:
                    lu = landuse.compute_c_and_cn(
                        bacia_poly, fonte_lulc=fonte_key, ano_lulc=int(ano),
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
    TR_in = c1.number_input("TR (anos)", 2, 1000, int(TR), 1)
    D_h = c2.number_input("Duração da chuva (h)", 1.0, 72.0, 12.0, 1.0)
    dt_min = c3.number_input("Passo dt (min)", 5, 120, 30, 5)
    c4, c5, c6 = st.columns(3)
    thr = c4.number_input("Discretização (células de canal)", 2000, 60000, 12000, 1000,
                          help="Menor = mais sub-bacias.")
    metodo_h = c5.selectbox("Hietograma", ["blocos", "huff"])
    X_musk = c6.number_input("X (Muskingum)", 0.0, 0.5, 0.25, 0.05,
                             help="0.25 típico de rio natural; 0.5 = translação pura (sem atenuação).")
    c7, c8 = st.columns(2)
    arf_auto = c7.checkbox("ARF automático (Leclerc-Schaake)", value=True)
    arf_manual = None if arf_auto else float(c8.number_input("ARF manual", 0.1, 1.0, 0.85, 0.01))
    fonte_cn = st.selectbox(
        "Fonte do CN por sub-bacia",
        ["GEE (MapBiomas + SoilGrids)", "ANA (BHAE local)", "Manual (calibração)"],
        help="GEE: CN próprio de cada sub-bacia via MapBiomas + solo — consistente "
             "com o Racional/SCS concentrado e roda no Cloud. ANA: ottobacias do "
             "BHAE (arquivo local de 5,6 GB). Manual: um CN único pra calibrar.",
    )
    c9, c10 = st.columns(2)
    cn_manual_in, ano_lulc, bhae = 0.0, 2023, "data/BHAE_CN2022.gpkg"
    if fonte_cn.startswith("Manual"):
        cn_manual_in = c9.number_input(
            "CN manual", 1.0, 100.0, 72.0, 1.0,
            help="Força um CN único em todas as sub-bacias (ex.: 72 calibrado p/ o Bananal).",
        )
    elif fonte_cn.startswith("GEE"):
        ano_lulc = c9.number_input("Ano do MapBiomas", 2017, 2024, 2023, 1)
        c10.caption("Calcula o CN de cada sub-bacia via GEE (1 consulta por sub-bacia "
                    "— mais lento, mas espacializado e Cloud-ready).")
    else:  # ANA
        bhae = c10.text_input("Arquivo de CN da ANA", value="data/BHAE_CN2022.gpkg",
                              help="GeoPackage de ottobacias com CN2.")
    c11, c12 = st.columns(2)
    celeridade = c11.number_input(
        "Celeridade do canal (m/s)", 0.2, 5.0, 1.0, 0.1,
        help="Calibração do roteamento. Rios naturais 1–1,5 m/s; com planície/várzea "
             "mais lentos (0,4–0,8) → mais espalhamento e pico menor. (Bananal calibrou ~0,4.)",
    )
    prf = c12.number_input(
        "Peak factor (PRF)", 100, 600, 484, 4,
        help="Forma da UH do SCS. 484 = padrão; menor achata o pico (bacias com "
             "armazenamento). Conserva o volume.",
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
            )
        with c2:
            H_m = st.number_input(
                "H (desnível, m)", 0.5, 3000.0, H_default, 1.0, format="%.1f",
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
    )
    st.session_state.tc_h = tc_h


# ---------------------------------------------------------------------------
# Metodo manual (override)
# ---------------------------------------------------------------------------

metodo_escolhido = st.radio(
    "Método (permite override do default):",
    ["Automático", "Racional (forçar)", "SCS-HU (forçar)", "Distribuído (forçar)"],
    index=0, horizontal=True,
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
        )
        C_sugerido = hg_mod.C_USO_SOLO[uso_solo]
        if lu_result is not None:
            C_sugerido = lu_result.C_racional
            st.caption(f"C sugerido pelo GEE: {lu_result.C_racional:.3f} (sobrepõe a tabela acima).")
        C = st.number_input(
            "Coeficiente C", min_value=0.05, max_value=0.99,
            value=float(C_sugerido), step=0.05,
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


st.success(
    "Chuva-vazão pronto. Prossiga para **4. Verificação de Seção**, "
    "**5. Hidráulica**, **6. Detenção** ou **7. Inundação 1D**."
)

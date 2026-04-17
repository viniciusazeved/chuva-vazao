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
    ["Automático", "Racional (forçar)", "SCS-HU (forçar)"],
    index=0, horizontal=True,
)

if metodo_escolhido == "Automático":
    metodo = metodo_default
elif metodo_escolhido.startswith("Racional"):
    metodo = "Racional"
else:
    metodo = "SCS-HU"

if metodo.startswith("Modelo distribuido"):
    st.warning(
        "Área > 250 km² — fora do escopo deste app. Use modelagem distribuída "
        "(SWMM, HEC-HMS) com discretização de sub-bacias."
    )
    st.stop()


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


st.success("Chuva-vazão pronto. Prossiga para **4. Hidráulica** ou **5. Detenção**.")

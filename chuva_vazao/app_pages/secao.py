"""Pagina 4: verificacao hidraulica de secao natural ou canalizada."""
from __future__ import annotations

import io
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from chuva_vazao import hidraulica as hd
from chuva_vazao import plots
from chuva_vazao import secao_natural as sn


# ---------------------------------------------------------------------------
# Modo MDT: desenhar 3 linhas sobre o mapa e amostrar os perfis do raster.
# Definido antes do corpo da pagina porque e chamado logo apos a escolha do modo.
# ---------------------------------------------------------------------------

def _perfil_para_df(perfil) -> pd.DataFrame:
    """Converte um PerfilMDT em DataFrame de pontos para o data_editor."""
    return pd.DataFrame({
        "N (m)": [p.N for p in perfil.pontos],
        "E (m)": [p.E for p in perfil.pontos],
        "Z (m)": [p.Z for p in perfil.pontos],
    })


def _centro_lonlat_do_raster(dem_path) -> tuple[float, float]:
    """(lat, lon) do centro do raster, reprojetando para 4326 se necessario."""
    import rasterio  # noqa: PLC0415
    from rasterio.warp import transform as warp_transform  # noqa: PLC0415

    with rasterio.open(dem_path) as src:
        b = src.bounds
        xc = (b.left + b.right) / 2.0
        yc = (b.top + b.bottom) / 2.0
        crs = src.crs
    if crs is not None and crs.to_epsg() != 4326:
        lon, lat = warp_transform(crs, "EPSG:4326", [xc], [yc])
        return float(lat[0]), float(lon[0])
    return float(yc), float(xc)


def _bloco_extrair_mdt() -> None:
    """Fonte do MDT + mapa com desenho de 3 linhas + amostragem dos perfis."""
    from chuva_vazao import secao_mdt as smdt  # noqa: PLC0415

    st.markdown("##### Fonte do MDT")
    fonte = st.radio(
        "De onde vem o modelo digital de terreno?",
        ["Reaproveitar DEM da Página 0", "Upload de GeoTIFF (drone/topografia)"],
        horizontal=True,
        key="mdt_fonte",
    )

    dem_path = None
    if fonte.startswith("Reaproveitar"):
        dp = st.session_state.get("_dem_path")
        if dp is None:
            st.warning(
                "Nenhum DEM na sessão. Baixe/carregue um na **Página 0** ou "
                "use a opção de upload aqui."
            )
        else:
            dem_path = Path(dp)
            st.caption(f"DEM em sessão: `{dem_path.name}`")
    else:
        up = st.file_uploader(
            "MDT (.tif/.tiff, qualquer CRS georreferenciado)",
            type=["tif", "tiff"], key="mdt_upload",
        )
        if up is not None:
            cache_dir = Path(st.session_state.get("_dem_cache_dir", "data/dems"))
            cache_dir.mkdir(parents=True, exist_ok=True)
            dem_path = cache_dir / up.name
            dem_path.write_bytes(up.read())
            st.caption(f"MDT salvo: `{dem_path.name}`")

    st.info(
        "Resolução importa: para canais de drenagem ou CGH (seção de poucos "
        "metros), use **MDT fino de levantamento** (drone/LiDAR, ~0,5–2 m). O "
        "Copernicus GLO-30 (30 m) só serve para rios/planícies largas — numa "
        "seção estreita ele vira 1 pixel e o perfil fica inútil.",
        icon="📐",
    )

    if dem_path is None:
        return

    # Centro do mapa: exutorio da bacia (Pagina 0), se houver; senao o raster.
    bres = st.session_state.get("basin_result")
    if bres is not None:
        lon_c, lat_c = bres["outlet_snapped"]
        center = (lat_c, lon_c)
    else:
        try:
            center = _centro_lonlat_do_raster(dem_path)
        except Exception as exc:
            st.error(f"Não consegui ler a extensão do MDT: {exc}")
            return

    import folium  # noqa: PLC0415
    from folium.plugins import Draw  # noqa: PLC0415
    from streamlit_folium import st_folium  # noqa: PLC0415

    col_map, col_ctrl = st.columns([4, 1])
    with col_ctrl:
        n_amostras = st.number_input(
            "Amostras por seção", min_value=5, max_value=500, value=40, step=5,
            help="Pontos equiespaçados ao longo de cada linha. ~40 dá uma "
                 "seção editável sem poluir a tabela.",
        )
        st.caption(
            "Desenhe **3 linhas** com a ferramenta de polilinha (ícone no mapa), "
            "cruzando o canal de margem a margem. A ordem do traçado vira "
            "esquerda → direita."
        )
        amostrar_btn = st.button(
            "📐 Amostrar perfis", type="primary", use_container_width=True,
        )

    with col_map:
        m = folium.Map(location=center, zoom_start=16)
        folium.TileLayer(
            tiles="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
            attr="Esri", name="Satélite",
        ).add_to(m)
        if bres is not None:
            folium.GeoJson(
                bres["basin_geojson"], name="Bacia",
                style_function=lambda _: {
                    "color": "#ff7f0e", "weight": 2, "fillOpacity": 0.08,
                },
            ).add_to(m)
        Draw(
            draw_options={
                "polyline": {"shapeOptions": {"color": "#e45756", "weight": 3}},
                "polygon": False, "rectangle": False, "circle": False,
                "marker": False, "circlemarker": False,
            },
            edit_options={"edit": True, "remove": True},
            export=False,
        ).add_to(m)
        folium.LayerControl(collapsed=True).add_to(m)

        saida = st_folium(
            m, height=520, width=None,
            returned_objects=["all_drawings"], key="mapa_mdt_secao",
            use_container_width=True,
        )
    if saida and saida.get("all_drawings") is not None:
        st.session_state["_mdt_drawings"] = saida["all_drawings"]

    if amostrar_btn:
        drawings = st.session_state.get("_mdt_drawings") or []
        linhas = [
            d["geometry"]["coordinates"]
            for d in drawings
            if d.get("geometry", {}).get("type") == "LineString"
            and len(d["geometry"]["coordinates"]) >= 2
        ]
        if len(linhas) != 3:
            st.error(
                f"Encontrei {len(linhas)} linha(s) válida(s). Desenhe "
                "exatamente **3 polilinhas** (montante, central e jusante)."
            )
        else:
            try:
                perfis = [
                    smdt.amostrar_perfil(
                        [(c[0], c[1]) for c in linha], dem_path,
                        n_pontos=int(n_amostras),
                    )
                    for linha in linhas
                ]
            except Exception as exc:
                st.error(f"Falha na amostragem: {exc}")
                perfis = None
            if perfis:
                st.session_state["_mdt_perfis"] = perfis
                st.rerun()

    perfis = st.session_state.get("_mdt_perfis")
    if not perfis:
        st.caption("Desenhe as 3 linhas e clique em **Amostrar perfis**.")
        return

    # Auto-ordenacao: maior cota de thalweg = montante; menor = jusante.
    ordem = sorted(
        range(len(perfis)), key=lambda i: perfis[i].z_min_m, reverse=True,
    )
    labels = [
        f"Linha {i + 1} — fundo={p.z_min_m:.2f} m, z̄={p.z_media_m:.2f} m, "
        f"L={p.comprimento_m:.0f} m"
        + (f", {p.n_nodata} nodata" if p.n_nodata else "")
        for i, p in enumerate(perfis)
    ]

    st.markdown("##### Atribuição das linhas")
    st.caption(
        "Auto-ordenado pela cota do fundo (montante = thalweg mais alto). "
        "Ajuste se o traçado não bateu."
    )
    c1, c2, c3 = st.columns(3)
    sel_M = c1.selectbox("Montante", labels, index=ordem[0], key="mdt_sel_M")
    sel_C = c2.selectbox("Central", labels, index=ordem[1], key="mdt_sel_C")
    sel_J = c3.selectbox("Jusante", labels, index=ordem[2], key="mdt_sel_J")
    idx_M, idx_C, idx_J = labels.index(sel_M), labels.index(sel_C), labels.index(sel_J)

    distintas = len({idx_M, idx_C, idx_J}) == 3
    if not distintas:
        st.warning("As três seções precisam apontar para linhas distintas.")

    # Preview: os 3 perfis (estaca local x cota) reaproveitando secao_natural.
    fig = go.Figure()
    for nome, idx, cor in [
        ("Montante", idx_M, "#1f77b4"),
        ("Central", idx_C, "#2ca02c"),
        ("Jusante", idx_J, "#d62728"),
    ]:
        try:
            sec = sn.secao_from_pontos(perfis[idx].pontos)
            fig.add_trace(go.Scatter(
                x=list(sec.estacas), y=list(sec.cotas),
                mode="lines+markers", name=nome,
                line=dict(color=cor, width=2), marker=dict(size=4),
                hovertemplate="estaca=%{x:.1f} m<br>cota=%{y:.2f} m<extra></extra>",
            ))
        except ValueError:
            pass
    fig.update_layout(
        title="Perfis amostrados do MDT",
        xaxis_title="Estaca local (m)", yaxis_title="Cota (m)",
        template="plotly_white", height=360, hovermode="x unified",
    )
    st.plotly_chart(fig, use_container_width=True)

    total_nodata = sum(p.n_nodata for p in perfis)
    if total_nodata:
        st.warning(
            f"{total_nodata} amostra(s) caíram fora do MDT/nodata e foram "
            "interpoladas. Confira essas cotas na tabela antes de verificar."
        )

    if st.button(
        "✅ Enviar pontos para as tabelas M/C/J",
        type="primary", disabled=not distintas,
    ):
        # Limpa o estado interno dos data_editors para que recarreguem os
        # pontos amostrados (senao mantêm o conteudo editado anterior).
        for k in ["M", "C", "J"]:
            st.session_state.pop(f"editor_{k}", None)
        st.session_state["secao_pontos_M"] = _perfil_para_df(perfis[idx_M])
        st.session_state["secao_pontos_C"] = _perfil_para_df(perfis[idx_C])
        st.session_state["secao_pontos_J"] = _perfil_para_df(perfis[idx_J])
        st.rerun()


st.title("4. Verificação Hidráulica de Seção")
st.caption(
    "Lance pontos topográficos (N, E, Z) das seções montante, central e jusante "
    "do trecho. Calcula declividade pelos thalwegs e a linha d'água em regime "
    "uniforme (Manning) na vazão de projeto. Use coordenadas em metros (UTM/SIRGAS)."
)


hg = st.session_state.get("hidrograma")
if hg is None:
    st.error("Gere o hidrograma na Página 3 antes.")
    st.stop()


# ---------------------------------------------------------------------------
# Q_projeto e material (n de Manning)
# ---------------------------------------------------------------------------

Q_pico_cenario = float(hg["Q_m3s"].max()) if "Q_m3s" in hg.columns else 0.0

col1, col2 = st.columns([1, 2])
with col1:
    Q_projeto = st.number_input(
        "Q de projeto (m³/s)",
        min_value=0.001, max_value=10_000.0,
        value=float(Q_pico_cenario),
        step=0.1,
        format="%.3f",
        help=f"Padrão = pico do hidrograma ({Q_pico_cenario:.3f} m³/s).",
    )

with col2:
    # Mescla naturais (Chow) + canalizados (do modulo hidraulica) num so selectbox
    materiais = {**sn.MANNING_N_NATURAL, **hd.MANNING_N}
    material = st.selectbox(
        "Material / tipo de leito",
        list(materiais.keys()),
        index=1,  # "Rio em planicie - limpo, sinuoso..."
        help="Naturais conforme Chow (1959), Tabela 5-6. Canalizados conforme DAEE/ABNT.",
    )
    n_manning = materiais[material]
    st.caption(f"n de Manning = {n_manning}")


# ---------------------------------------------------------------------------
# Modo de entrada das secoes
# ---------------------------------------------------------------------------

st.divider()
modo_entrada = st.radio(
    "Modo de entrada das seções",
    ["Lançar pontos (N, E, Z)", "Extrair de MDT (desenhar linhas)"],
    horizontal=True,
    key="modo_entrada_secao",
    help=(
        "Lance os pontos à mão (ou via CSV) ou extraia os perfis de um MDT "
        "desenhando as três linhas sobre o mapa — os pontos amostrados caem "
        "nas tabelas abaixo para você revisar antes de verificar."
    ),
)
if modo_entrada.startswith("Extrair"):
    _bloco_extrair_mdt()


# ---------------------------------------------------------------------------
# Entrada das secoes via data_editor + upload CSV
# ---------------------------------------------------------------------------

LABELS = {"M": "Montante", "C": "Central", "J": "Jusante"}

# Defaults sinteticos: trapezio 20 m de topo, 8 m de fundo, 3 m de altura.
# Tres secoes alinhadas em N com declividade total ~0.5 % (queda 1 m em 200 m).
_DEFAULTS_SECAO = {
    "M": pd.DataFrame({
        "N (m)": [0.0, 0.0, 0.0, 0.0],
        "E (m)": [0.0, 6.0, 14.0, 20.0],
        "Z (m)": [10.0, 7.0, 7.0, 10.0],
    }),
    "C": pd.DataFrame({
        "N (m)": [100.0, 100.0, 100.0, 100.0],
        "E (m)": [0.0, 6.0, 14.0, 20.0],
        "Z (m)": [9.5, 6.5, 6.5, 9.5],
    }),
    "J": pd.DataFrame({
        "N (m)": [200.0, 200.0, 200.0, 200.0],
        "E (m)": [0.0, 6.0, 14.0, 20.0],
        "Z (m)": [9.0, 6.0, 6.0, 9.0],
    }),
}


def _df_vazio_secao(k: str = "M") -> pd.DataFrame:
    return _DEFAULTS_SECAO[k].copy()


def _df_para_pontos(df: pd.DataFrame) -> list[sn.PontoSecao]:
    """Converte o DataFrame da UI em lista de PontoSecao, validando."""
    df = df.dropna(subset=["N (m)", "E (m)", "Z (m)"])
    if len(df) < 3:
        raise ValueError("Cada seção precisa de pelo menos 3 pontos.")
    return [
        sn.PontoSecao(N=float(r["N (m)"]), E=float(r["E (m)"]), Z=float(r["Z (m)"]))
        for _, r in df.iterrows()
    ]


# Bootstrap session_state pras 3 secoes
for k in ["M", "C", "J"]:
    sk = f"secao_pontos_{k}"
    if sk not in st.session_state:
        st.session_state[sk] = _df_vazio_secao(k)


st.subheader("Pontos das seções")
st.caption(
    "Lance os pontos da margem **esquerda** para a margem **direita** em cada seção. "
    "O ponto de menor Z vira o thalweg automaticamente."
)


# Upload CSV unificado (colunas: secao, N, E, Z)
with st.expander("📤 Carregar CSV unificado (formato: secao, N, E, Z)"):
    st.code(
        "secao,N,E,Z\n"
        "M,7480000,580000,12.5\n"
        "M,7480000,580005,8.0\n"
        "M,7480000,580015,8.0\n"
        "M,7480000,580020,12.5\n"
        "C,7480100,580000,11.5\n"
        "C,7480100,580005,7.0\n"
        "...",
        language="csv",
    )
    arq = st.file_uploader(
        "CSV com `secao` ∈ {M, C, J}", type=["csv"], key="upload_secao",
    )
    if arq is not None:
        try:
            df = pd.read_csv(arq)
            df.columns = [c.strip().lower() for c in df.columns]
            if not {"secao", "n", "e", "z"}.issubset(df.columns):
                st.error("CSV precisa ter colunas: secao, N, E, Z (case-insensitive).")
            else:
                df["secao"] = df["secao"].astype(str).str.upper().str.strip()
                for k in ["M", "C", "J"]:
                    sub = df[df["secao"] == k][["n", "e", "z"]]
                    if not sub.empty:
                        sub = sub.rename(
                            columns={"n": "N (m)", "e": "E (m)", "z": "Z (m)"}
                        ).reset_index(drop=True)
                        st.session_state[f"secao_pontos_{k}"] = sub
                st.success(
                    f"Carregado: M={sum(df['secao']=='M')} pts, "
                    f"C={sum(df['secao']=='C')} pts, "
                    f"J={sum(df['secao']=='J')} pts."
                )
                st.rerun()
        except Exception as exc:
            st.error(f"Falha ao ler CSV: {exc}")


tabs = st.tabs([LABELS[k] for k in ["M", "C", "J"]])
for tab, k in zip(tabs, ["M", "C", "J"]):
    with tab:
        sk = f"secao_pontos_{k}"
        edited = st.data_editor(
            st.session_state[sk],
            num_rows="dynamic",
            use_container_width=True,
            key=f"editor_{k}",
            column_config={
                "N (m)": st.column_config.NumberColumn(format="%.3f"),
                "E (m)": st.column_config.NumberColumn(format="%.3f"),
                "Z (m)": st.column_config.NumberColumn(format="%.3f"),
            },
        )
        st.session_state[sk] = edited

        # Preview minimalista
        try:
            pts = _df_para_pontos(edited)
            sec_preview = sn.secao_from_pontos(pts)
            col_a, col_b, col_c = st.columns(3)
            col_a.metric("Pontos", len(pts))
            col_b.metric("Largura topo", f"{sec_preview.largura_topo:.2f} m")
            col_c.metric("Z thalweg / topo",
                         f"{sec_preview.z_thalweg:.2f} / {sec_preview.z_max:.2f} m")
        except ValueError as exc:
            st.warning(f"Pontos inválidos: {exc}")


st.divider()


# ---------------------------------------------------------------------------
# Verificacao
# ---------------------------------------------------------------------------

if st.button("🔍 Verificar trecho", type="primary", use_container_width=True):
    try:
        pts_M = _df_para_pontos(st.session_state["secao_pontos_M"])
        pts_C = _df_para_pontos(st.session_state["secao_pontos_C"])
        pts_J = _df_para_pontos(st.session_state["secao_pontos_J"])
        sec_M = sn.secao_from_pontos(pts_M)
        sec_C = sn.secao_from_pontos(pts_C)
        sec_J = sn.secao_from_pontos(pts_J)
        verif = sn.verificar_trecho(
            sec_M, sec_C, sec_J, Q_projeto=Q_projeto, n=n_manning,
        )
        st.session_state.verificacao_secao = verif
    except ValueError as exc:
        st.error(f"Erro na verificação: {exc}")
        st.stop()


verif = st.session_state.get("verificacao_secao")
if verif is None:
    st.info("Defina os pontos das três seções e clique em **Verificar trecho**.")
    st.stop()


# ---------------------------------------------------------------------------
# Resultado
# ---------------------------------------------------------------------------

st.subheader("Resultado")

col1, col2, col3, col4 = st.columns(4)
col1.metric("L total", f"{verif.L_total_m:.0f} m")
col2.metric("Declividade", f"{verif.S_trecho_m_per_m * 100:.3f} %")
col3.metric("Q projeto", f"{verif.Q_projeto_m3_s:.2f} m³/s")
col4.metric("n Manning", f"{verif.n:.3f}")


# Tabela comparativa das 3 secoes
def _linha(nome: str, esc: sn.EscoamentoSecao, esc_crit: sn.EscoamentoSecao,
           sec: sn.SecaoTransversal) -> dict:
    return {
        "Seção": nome,
        "y_n (m)": round(esc.y_lamina_m, 3),
        "y_c (m)": round(esc_crit.y_lamina_m, 3),
        "Cota d'água (m)": round(esc.y_w_m, 3),
        "Borda livre (m)": round(sec.z_max - esc.y_w_m, 3),
        "A (m²)": round(esc.A_m2, 2),
        "P (m)": round(esc.P_m, 2),
        "R (m)": round(esc.R_m, 3),
        "v (m/s)": round(esc.v_m_s, 2),
        "Fr": round(esc.Fr, 3),
        "Regime": esc.regime,
    }

tabela = pd.DataFrame([
    _linha("Montante", verif.escoamento_M, verif.critica_M, verif.secao_montante),
    _linha("Central",  verif.escoamento_C, verif.critica_C, verif.secao_central),
    _linha("Jusante",  verif.escoamento_J, verif.critica_J, verif.secao_jusante),
])
st.dataframe(tabela, use_container_width=True, hide_index=True)

for w in verif.warnings:
    st.warning(w)


# Perfil longitudinal
st.subheader("Perfil longitudinal do trecho")
fig_perfil = plots.plot_perfil_longitudinal(verif)
st.plotly_chart(fig_perfil, use_container_width=True)


# Tres secoes lado a lado
st.subheader("Seções transversais com lâmina")
col_M, col_C, col_J = st.columns(3)
for col, nome, sec, esc, crit in [
    (col_M, "Montante", verif.secao_montante, verif.escoamento_M, verif.critica_M),
    (col_C, "Central",  verif.secao_central,  verif.escoamento_C, verif.critica_C),
    (col_J, "Jusante",  verif.secao_jusante,  verif.escoamento_J, verif.critica_J),
]:
    with col:
        fig = plots.plot_secao_transversal(
            sec, esc, esc_critico=crit, titulo=f"Seção {nome}",
        )
        st.plotly_chart(fig, use_container_width=True)


# ---------------------------------------------------------------------------
# Export CSV das tres secoes
# ---------------------------------------------------------------------------

with st.expander("⬇ Exportar pontos das seções (CSV)"):
    rows = []
    for k, sec in [("M", verif.secao_montante),
                   ("C", verif.secao_central),
                   ("J", verif.secao_jusante)]:
        for p in sec.pontos_3d:
            rows.append({"secao": k, "N": p.N, "E": p.E, "Z": p.Z})
    df_export = pd.DataFrame(rows)
    buf = io.StringIO()
    df_export.to_csv(buf, index=False)
    st.download_button(
        "⬇ pontos_secoes.csv",
        data=buf.getvalue().encode("utf-8"),
        file_name="pontos_secoes.csv",
        mime="text/csv",
    )

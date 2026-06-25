"""Página 8: exportar PDF técnico modular e CSVs dos resultados."""
from __future__ import annotations

from io import StringIO

import plotly.graph_objects as go
import streamlit as st

from chuva_vazao import plots
from chuva_vazao.report import RelatorioInputs, gerar_relatorio_pdf


st.title("8. Exportar Resultados")
st.caption(
    "PDF modular: cada página que você rodou no app vira uma seção do relatório. "
    "Não precisa rodar tudo — gere PDF só do dimensionamento, só da verificação "
    "de seção, só da detenção, ou da combinação que fizer sentido."
)


# ---------------------------------------------------------------------------
# Detecta o que o usuario rodou nas paginas anteriores
# ---------------------------------------------------------------------------

modulos_disponiveis = {
    "bacia": (
        "Bacia de contribuição (Página 0)",
        st.session_state.get("basin_metrics") is not None,
    ),
    "hidrologia": (
        "Hidrologia: IDF + Hietograma + Hidrograma (Páginas 1-3)",
        all(
            st.session_state.get(k) is not None
            for k in ("idf_params", "idf_table", "hietograma", "hidrograma")
        ),
    ),
    "verificacao_secao": (
        "Verificação hidráulica de seção (Página 4)",
        st.session_state.get("verificacao_secao") is not None,
    ),
    "dimensionamento": (
        "Dimensionamento hidráulico de conduto (Página 5)",
        st.session_state.get("dimensionamento") is not None,
    ),
    "detencao": (
        "Reservatório de detenção (Página 6)",
        st.session_state.get("detencao") is not None,
    ),
    "inundacao": (
        "Inundação fluvial 1D (Página 7)",
        st.session_state.get("inu_resultado") is not None,
    ),
}

algum_modulo = any(disp for _, disp in modulos_disponiveis.values())
if not algum_modulo:
    st.warning(
        "Nenhuma página foi rodada ainda. Use o app livremente — depois "
        "volte aqui pra exportar o relatório só dos módulos que você usou."
    )
    st.stop()


posto = st.session_state.get("posto_descricao") or ""
uf = st.session_state.get("posto_estado") or ""
fonte = st.session_state.get("posto_fonte") or ""

if posto:
    st.subheader(f"Posto: {posto} ({uf}) — fonte: {fonte}")


# ---------------------------------------------------------------------------
# CSVs rapidos (so para os modulos que o usuario rodou)
# ---------------------------------------------------------------------------

st.divider()
st.subheader("CSVs individuais")

cols_csv = st.columns(3)
csv_idx = 0

if st.session_state.get("idf_table") is not None:
    buf = StringIO()
    st.session_state.idf_table.to_csv(buf)
    cols_csv[csv_idx % 3].download_button(
        "⬇ IDF (mm/h)",
        data=buf.getvalue().encode("utf-8"),
        file_name=f"{(posto or 'idf').replace(' ', '_')}_idf.csv",
        mime="text/csv",
    )
    csv_idx += 1

if st.session_state.get("hietograma") is not None:
    buf = StringIO()
    st.session_state.hietograma.reset_index().to_csv(buf, index=False)
    cols_csv[csv_idx % 3].download_button(
        "⬇ Hietograma",
        data=buf.getvalue().encode("utf-8"),
        file_name=f"{(posto or 'evento').replace(' ', '_')}_hietograma.csv",
        mime="text/csv",
    )
    csv_idx += 1

if st.session_state.get("hidrograma") is not None:
    buf = StringIO()
    st.session_state.hidrograma.reset_index().to_csv(buf, index=False)
    cols_csv[csv_idx % 3].download_button(
        "⬇ Hidrograma",
        data=buf.getvalue().encode("utf-8"),
        file_name=f"{(posto or 'evento').replace(' ', '_')}_hidrograma.csv",
        mime="text/csv",
    )
    csv_idx += 1

if csv_idx == 0:
    st.caption("Nenhuma série pluvio/hidrológica para exportar — gere as Páginas 1-3 se quiser CSVs.")


# ---------------------------------------------------------------------------
# Selecao de modulos do PDF
# ---------------------------------------------------------------------------

st.divider()
st.subheader("Relatório técnico PDF")
st.caption("Selecione os módulos a incluir. Por padrão, marca tudo o que está disponível.")

cols_sel = st.columns(2)
modulos_selecionados: dict[str, bool] = {}
for i, (key, (label, disp)) in enumerate(modulos_disponiveis.items()):
    with cols_sel[i % 2]:
        modulos_selecionados[key] = st.checkbox(
            label, value=disp, disabled=not disp,
            help=None if disp else "Rode a página correspondente para ativar este módulo.",
        )

if not any(modulos_selecionados.values()):
    st.info("Selecione pelo menos um módulo para gerar o PDF.")
    st.stop()


# ---------------------------------------------------------------------------
# Gerar PDF
# ---------------------------------------------------------------------------

if st.button("Gerar PDF", type="primary"):
    with st.spinner("Renderizando figuras e montando PDF..."):
        kwargs: dict = {
            "posto_descricao": posto,
            "posto_estado": uf,
            "posto_fonte": fonte,
        }

        # Hidrologia
        if modulos_selecionados.get("hidrologia"):
            kwargs.update(
                idf_params=st.session_state.idf_params,
                idf_table=st.session_state.idf_table,
                TR_anos=st.session_state.get("TR"),
                duracao_min=st.session_state.get("duracao_min"),
                dt_min=st.session_state.get("dt_min"),
                metodo_hietograma=st.session_state.get("metodo_hietograma"),
                hietograma=st.session_state.hietograma,
                hidrograma=st.session_state.hidrograma,
                metodo_chuva_vazao=st.session_state.get("metodo_chuva_vazao"),
                C_racional=st.session_state.get("C_racional"),
                uso_solo_racional=st.session_state.get("uso_solo_racional"),
                fig_idf=plots.plot_idf_curves(
                    st.session_state.idf_table,
                    titulo=f"IDF — {posto}" if posto else "IDF",
                ),
                fig_hietograma=plots.plot_hietograma(
                    st.session_state.hietograma,
                    titulo=f"Hietograma — {st.session_state.get('metodo_hietograma', '')}",
                ),
                fig_hidrograma=plots.plot_hietograma_hidrograma(
                    st.session_state.hidrograma,
                    titulo=f"Hietograma + Hidrograma" + (f" — {posto}" if posto else ""),
                ),
            )
            scs = st.session_state.get("scs_params")
            if scs is None:
                from chuva_vazao.hidrograma import SCSParams
                scs = SCSParams(
                    area_km2=float(st.session_state.get("area_km2") or 1.0),
                    tempo_concentracao_h=float(st.session_state.get("tc_h") or 1.0),
                    CN=75.0,
                )
            kwargs["scs_params"] = scs

        # Bacia
        if modulos_selecionados.get("bacia"):
            kwargs["basin_metrics"] = st.session_state.get("basin_metrics")
            kwargs["tc_breakdown"] = st.session_state.get("tc_breakdown")
            bres = st.session_state.get("basin_result")
            if bres is not None:
                orig = bres.get("outlet_original")
                snap = bres.get("outlet_snapped")
                if orig:
                    kwargs["basin_outlet_original"] = (orig[1], orig[0])
                if snap:
                    kwargs["basin_outlet_snapped"] = (snap[1], snap[0])

        # Verificacao de secao
        if modulos_selecionados.get("verificacao_secao"):
            verif = st.session_state.get("verificacao_secao")
            kwargs["verificacao_secao"] = verif
            if verif is not None:
                kwargs["fig_verif_perfil"] = plots.plot_perfil_longitudinal(verif)
                kwargs["figs_verif_secoes"] = [
                    plots.plot_secao_transversal(
                        verif.secao_montante, verif.escoamento_M,
                        esc_critico=verif.critica_M, titulo="Seção Montante",
                    ),
                    plots.plot_secao_transversal(
                        verif.secao_central, verif.escoamento_C,
                        esc_critico=verif.critica_C, titulo="Seção Central",
                    ),
                    plots.plot_secao_transversal(
                        verif.secao_jusante, verif.escoamento_J,
                        esc_critico=verif.critica_J, titulo="Seção Jusante",
                    ),
                ]

        # Dimensionamento
        if modulos_selecionados.get("dimensionamento"):
            kwargs["dimensionamento"] = st.session_state.get("dimensionamento")

        # Detencao
        if modulos_selecionados.get("detencao"):
            det = st.session_state.get("detencao")
            kwargs["detencao"] = det
            kwargs["detencao_reservatorio"] = st.session_state.get("detencao_reservatorio")
            kwargs["detencao_curva_dem"] = st.session_state.get("_curva_dem")

            # afluente vs efluente
            fig_det = go.Figure()
            fig_det.add_trace(go.Scatter(
                x=det.tempo_min, y=det.inflow_m3_s,
                name="Afluente", line=dict(color="#1f77b4", width=2),
                fill="tozeroy", fillcolor="rgba(31,119,180,0.15)",
            ))
            fig_det.add_trace(go.Scatter(
                x=det.tempo_min, y=det.outflow_m3_s,
                name="Efluente", line=dict(color="#d62728", width=2),
                fill="tozeroy", fillcolor="rgba(214,39,40,0.15)",
            ))
            fig_det.update_layout(
                title="Roteamento Puls — Afluente vs Efluente",
                xaxis_title="Tempo (min)", yaxis_title="Vazão (m³/s)",
                template="plotly_white", height=400,
            )
            kwargs["fig_detencao"] = fig_det

            # Curvas características (V/A x z + descarga decomposta)
            res = st.session_state.get("detencao_reservatorio")
            if res is not None:
                kwargs["fig_detencao_caracteristicas"] = plots.plot_cota_volume_descarga(res)

        # Inundacao 1D
        if modulos_selecionados.get("inundacao"):
            inu_res = st.session_state.get("inu_resultado")
            if inu_res is not None:
                ger = inu_res["ger"]
                perfil = inu_res["perfil"]
                mancha = inu_res["mancha"]
                warns = list(perfil.warnings) + list(ger.avisos) + list(mancha.avisos)
                kwargs["inundacao"] = {
                    "Q_m3_s": perfil.Q_m3_s,
                    "n": perfil.n_manning,
                    "cc_jusante": perfil.cc_jusante,
                    "n_secoes": len(ger.secoes),
                    "L_eixo_m": ger.comprimento_eixo_m,
                    "area_ha": mancha.area_alagada_m2 / 1e4,
                    "prof_max_m": mancha.prof_max_m,
                    "prof_media_m": mancha.prof_media_m,
                    "warnings": warns,
                }
                kwargs["inundacao_tabela"] = perfil.to_dataframe()
                kwargs["fig_inundacao_mancha"] = plots.fig_mancha_hillshade(mancha)
                kwargs["fig_inundacao_perfil"] = plots.plot_perfil_inundacao(perfil)

        inputs = RelatorioInputs(**kwargs)
        pdf_bytes = gerar_relatorio_pdf(inputs)

    st.success(f"PDF gerado ({len(pdf_bytes):,} bytes).")
    nome_base = (posto or "chuva_vazao").replace(" ", "_")
    selected_keys = [k for k, v in modulos_selecionados.items() if v]
    sufixo = "_".join(selected_keys[:3]) if selected_keys else "vazio"
    st.download_button(
        "⬇ Download PDF",
        data=pdf_bytes,
        file_name=f"{nome_base}_{sufixo}.pdf",
        mime="application/pdf",
    )

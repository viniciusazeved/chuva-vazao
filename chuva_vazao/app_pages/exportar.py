"""Página 4: exportar PDF técnico e CSVs dos resultados."""
from __future__ import annotations

from io import StringIO

import streamlit as st

from chuva_vazao import plots
from chuva_vazao.report import RelatorioInputs, gerar_relatorio_pdf


st.title("6. Exportar Resultados")
st.caption("Gere PDF técnico consolidado e baixe CSVs das tabelas.")


required = {
    "idf_params": "coeficientes IDF (Página 1)",
    "idf_table": "tabela IDF (Página 1)",
    "hietograma": "hietograma (Página 2)",
    "hidrograma": "hidrograma (Página 3)",
}
missing = [label for key, label in required.items() if st.session_state.get(key) is None]
if missing:
    st.error("Precisa completar antes: " + ", ".join(missing) + ".")
    st.stop()

opcional = {
    "dimensionamento": "hidráulica (Página 4)",
    "detencao": "detenção (Página 5)",
}
opcional_feitos = [label for key, label in opcional.items() if st.session_state.get(key) is not None]
if opcional_feitos:
    st.info("Extras incluídos: " + ", ".join(opcional_feitos) + ".")


posto = st.session_state.posto_descricao or "posto"
uf = st.session_state.posto_estado or "--"
fonte = st.session_state.posto_fonte or "?"

st.subheader(f"Posto: {posto} ({uf}) — fonte: {fonte}")


# ---------------------------------------------------------------------------
# CSVs rapidos
# ---------------------------------------------------------------------------

st.divider()
st.subheader("CSVs individuais")

col1, col2, col3 = st.columns(3)

buf = StringIO()
st.session_state.idf_table.to_csv(buf)
col1.download_button(
    "⬇ IDF (mm/h)",
    data=buf.getvalue().encode("utf-8"),
    file_name=f"{posto.replace(' ', '_')}_idf.csv",
    mime="text/csv",
)

buf = StringIO()
st.session_state.hietograma.reset_index().to_csv(buf, index=False)
col2.download_button(
    "⬇ Hietograma",
    data=buf.getvalue().encode("utf-8"),
    file_name=f"{posto.replace(' ', '_')}_hietograma.csv",
    mime="text/csv",
)

buf = StringIO()
st.session_state.hidrograma.reset_index().to_csv(buf, index=False)
col3.download_button(
    "⬇ Hidrograma",
    data=buf.getvalue().encode("utf-8"),
    file_name=f"{posto.replace(' ', '_')}_hidrograma.csv",
    mime="text/csv",
)


# ---------------------------------------------------------------------------
# PDF tecnico
# ---------------------------------------------------------------------------

st.divider()
st.subheader("Relatório técnico PDF")
st.caption(
    "O PDF agrega: posto, equação IDF, curvas, hietograma, parâmetros da bacia, "
    "hidrograma resultante e metodologia/referências."
)

if st.button("Gerar PDF", type="primary"):
    with st.spinner("Renderizando figuras e montando PDF..."):
        fig_idf = plots.plot_idf_curves(
            st.session_state.idf_table,
            titulo=f"IDF — {posto}",
        )
        fig_hieto = plots.plot_hietograma(
            st.session_state.hietograma,
            titulo=f"Hietograma — {st.session_state.metodo_hietograma}",
        )
        fig_hg = plots.plot_hietograma_hidrograma(
            st.session_state.hidrograma,
            titulo=f"Hietograma + Hidrograma — {posto}",
        )
        # SCS params pode ser None se metodo foi Racional — usa stub
        scs_params = st.session_state.scs_params
        if scs_params is None:
            from chuva_vazao.hidrograma import SCSParams
            scs_params = SCSParams(
                area_km2=float(st.session_state.area_km2),
                tempo_concentracao_h=float(st.session_state.tc_h),
                CN=75.0,
            )

        # Figura da detencao (opcional)
        fig_detencao = None
        det = st.session_state.get("detencao")
        if det is not None:
            import plotly.graph_objects as go
            fig_detencao = go.Figure()
            fig_detencao.add_trace(go.Scatter(
                x=det.tempo_min, y=det.inflow_m3_s,
                name="Afluente", line=dict(color="#1f77b4", width=2),
                fill="tozeroy", fillcolor="rgba(31,119,180,0.15)",
            ))
            fig_detencao.add_trace(go.Scatter(
                x=det.tempo_min, y=det.outflow_m3_s,
                name="Efluente", line=dict(color="#d62728", width=2),
                fill="tozeroy", fillcolor="rgba(214,39,40,0.15)",
            ))
            fig_detencao.update_layout(
                title="Roteamento Puls — Afluente vs Efluente",
                xaxis_title="Tempo (min)", yaxis_title="Vazao (m3/s)",
                template="plotly_white", height=450,
            )

        # Basin metrics + outlets (opcional)
        basin_metrics = st.session_state.get("basin_metrics")
        basin_outlet_original = None
        basin_outlet_snapped = None
        bres = st.session_state.get("basin_result")
        if bres is not None:
            orig = bres.get("outlet_original")
            snap = bres.get("outlet_snapped")
            if orig:
                basin_outlet_original = (orig[1], orig[0])  # (lat, lon)
            if snap:
                basin_outlet_snapped = (snap[1], snap[0])

        # tc breakdown se existir
        tc_breakdown = st.session_state.get("tc_breakdown")

        inputs = RelatorioInputs(
            posto_descricao=posto,
            posto_estado=uf,
            posto_fonte=fonte,
            idf_params=st.session_state.idf_params,
            idf_table=st.session_state.idf_table,
            TR_anos=st.session_state.TR,
            duracao_min=st.session_state.duracao_min,
            dt_min=st.session_state.dt_min,
            metodo_hietograma=st.session_state.metodo_hietograma,
            hietograma=st.session_state.hietograma,
            scs_params=scs_params,
            hidrograma=st.session_state.hidrograma,
            fig_idf=fig_idf,
            fig_hietograma=fig_hieto,
            fig_hidrograma=fig_hg,
            basin_metrics=basin_metrics,
            basin_outlet_original=basin_outlet_original,
            basin_outlet_snapped=basin_outlet_snapped,
            tc_breakdown=tc_breakdown,
            metodo_chuva_vazao=st.session_state.get("metodo_chuva_vazao"),
            C_racional=st.session_state.get("C_racional"),
            uso_solo_racional=st.session_state.get("uso_solo_racional"),
            dimensionamento=st.session_state.get("dimensionamento"),
            detencao=det,
            fig_detencao=fig_detencao,
        )
        pdf_bytes = gerar_relatorio_pdf(inputs)

    st.success(f"PDF gerado ({len(pdf_bytes):,} bytes).")
    st.download_button(
        "⬇ Download PDF",
        data=pdf_bytes,
        file_name=f"{posto.replace(' ', '_')}_relatorio.pdf",
        mime="application/pdf",
    )

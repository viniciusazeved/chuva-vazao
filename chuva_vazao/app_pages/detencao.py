"""Página 5: reservatório de detenção via Puls modificado."""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from chuva_vazao import detencao as dt
from chuva_vazao import plots


st.title("5. Reservatório de Detenção")
st.caption(
    "Roteia o hidrograma afluente por Puls modificado em reservatório "
    "prismático com orifício de fundo + vertedor retangular de emergência."
)

hg = st.session_state.get("hidrograma")
if hg is None:
    st.error("Gere o hidrograma na Página 3 antes.")
    st.stop()


# ---------------------------------------------------------------------------
# Parametros do reservatorio
# ---------------------------------------------------------------------------

st.subheader("Geometria do reservatório")
col1, col2 = st.columns(2)
with col1:
    Aw = st.number_input(
        "Área superficial Aw (m²)",
        min_value=10.0, max_value=1_000_000.0,
        value=5000.0, step=100.0,
        help="Prismático — área constante com a altura.",
    )
with col2:
    h_max = st.number_input(
        "Altura máxima h_max (m)", min_value=0.5, max_value=20.0,
        value=4.0, step=0.1,
    )


st.subheader("Dispositivos de saída")
col1, col2 = st.columns(2)
with col1:
    st.markdown("**Orifício de fundo**")
    z_orif = st.number_input("Cota do orifício (m)", 0.0, float(h_max), 0.0, 0.1)
    d_orif = st.number_input("Diâmetro do orifício (m)", 0.05, 3.0, 0.30, 0.05)
    Cd = st.number_input("Cd orifício", 0.4, 0.9, 0.61, 0.01)
with col2:
    st.markdown("**Vertedor de emergência**")
    z_vert = st.number_input("Cota do vertedor (m)", 0.0, float(h_max), float(h_max) * 0.75, 0.1)
    b_vert = st.number_input("Largura do vertedor (m)", 0.1, 50.0, 3.0, 0.1)
    Cw = st.number_input("Cw vertedor", 1.5, 2.2, 1.85, 0.05)


res = dt.Reservatorio(
    Aw_m2=Aw, h_max_m=h_max,
    z_orificio_m=z_orif, d_orificio_m=d_orif,
    z_vertedor_m=z_vert, b_vertedor_m=b_vert,
    Cd_orificio=Cd, Cw_vertedor=Cw,
)


# ---------------------------------------------------------------------------
# Roteamento
# ---------------------------------------------------------------------------

inflow = hg["Q_m3s"].to_numpy()
dt_min = float(hg.index[1] - hg.index[0]) if len(hg) > 1 else 1.0

resultado = dt.puls_routing(inflow, dt_min=dt_min, reservatorio=res)
st.session_state.detencao = resultado


st.subheader("Resultado")
col1, col2, col3, col4 = st.columns(4)
col1.metric("Qp afluente", f"{resultado.Qp_in_m3_s:.2f} m³/s")
col2.metric("Qp efluente", f"{resultado.Qp_out_m3_s:.2f} m³/s")
col3.metric("Atenuação", f"{resultado.atenuacao_pct:.1f} %")
col4.metric("h máx atingida", f"{resultado.h_max_m:.2f} m")

col1, col2 = st.columns(2)
col1.metric("Volume armazenado máx", f"{resultado.volume_armazenado_max_m3:,.0f} m³")
col2.metric("Extravasou?", "Sim ⚠️" if resultado.h_max_m >= h_max * 0.99 else "Não")


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


# Lamina no tempo
fig2 = go.Figure()
fig2.add_trace(go.Scatter(
    x=resultado.tempo_min, y=resultado.h_m,
    mode="lines", line=dict(color="#2ca02c", width=2),
    fill="tozeroy", fillcolor="rgba(44,160,44,0.15)",
))
fig2.add_hline(y=z_vert, line_dash="dash", line_color="#d62728",
               annotation_text=f"vertedor @ {z_vert:.2f} m")
fig2.add_hline(y=h_max, line_dash="dot", line_color="black",
               annotation_text=f"h_max = {h_max:.2f} m")
fig2.update_layout(
    title="Lâmina do reservatório",
    xaxis_title="Tempo (min)",
    yaxis_title="h (m)",
    template="plotly_white",
    height=350,
)
st.plotly_chart(fig2, use_container_width=True)


with st.expander("Tabela do roteamento"):
    st.dataframe(resultado.to_dataframe().round(3), use_container_width=True)

"""Página 2: geração de hietograma de projeto."""
from __future__ import annotations

import streamlit as st

from chuva_vazao import hietograma as hieto_mod
from chuva_vazao import plots


st.title("2. Hietograma de Projeto")
st.caption(
    "Distribuição temporal da chuva para uma dada duração total. "
    "Blocos alternados (Chicago) concentra o pico no centro; "
    "Huff usa curvas adimensionais por quartil."
)

params = st.session_state.get("idf_params")
if params is None:
    st.error("Carregue os coeficientes IDF na Página 1 antes.")
    st.stop()


st.subheader("Parâmetros do evento")
col1, col2, col3, col4 = st.columns(4)
with col1:
    TR = st.number_input("TR (anos)", min_value=1, max_value=1000, value=int(st.session_state.TR), step=1)
with col2:
    duracao = st.number_input(
        "Duração total (min)", min_value=5, max_value=1440,
        value=int(st.session_state.duracao_min), step=5,
    )
with col3:
    dt = st.number_input(
        "Passo dt (min)", min_value=1, max_value=60,
        value=int(st.session_state.dt_min), step=1,
    )
with col4:
    metodo = st.selectbox(
        "Método",
        ["Blocos Alternados (Chicago)", "Huff 1º quartil", "Huff 2º quartil", "Huff 3º quartil", "Huff 4º quartil"],
        index=["Blocos Alternados (Chicago)", "Huff 1º quartil", "Huff 2º quartil", "Huff 3º quartil", "Huff 4º quartil"].index(st.session_state.metodo_hietograma)
        if st.session_state.metodo_hietograma in ["Blocos Alternados (Chicago)", "Huff 1º quartil", "Huff 2º quartil", "Huff 3º quartil", "Huff 4º quartil"]
        else 0,
    )

if duracao % dt != 0:
    st.warning(f"Duração ({duracao}) não é múltiplo de dt ({dt}). Será ajustada para {(duracao // dt) * dt}.")

st.session_state.TR = TR
st.session_state.duracao_min = duracao
st.session_state.dt_min = dt
st.session_state.metodo_hietograma = metodo


# ---------------------------------------------------------------------------
# Geracao do hietograma
# ---------------------------------------------------------------------------

if metodo.startswith("Blocos"):
    hieto = hieto_mod.blocos_alternados(params, TR=TR, duracao_total_min=duracao, dt_min=dt)
    metodo_display = "Blocos Alternados (Chicago)"
else:
    quartil = int(metodo[5])  # "Huff 2º quartil" -> 2
    st.session_state.huff_quartil = quartil
    hieto = hieto_mod.huff(params, TR=TR, duracao_total_min=duracao, dt_min=dt, quartil=quartil)
    metodo_display = f"Huff {quartil}º quartil"

st.session_state.hietograma = hieto


col1, col2, col3 = st.columns(3)
altura_total = float(hieto.sum())
intensidade_media = hieto_mod.intensidade_media(hieto)
col1.metric("Altura total", f"{altura_total:.2f} mm")
col2.metric("Intensidade média", f"{intensidade_media:.2f} mm/h")
col3.metric("Pico do bloco", f"{float(hieto.max()):.2f} mm / dt")


st.plotly_chart(
    plots.plot_hietograma(hieto, titulo=f"{metodo_display} — TR={TR} anos, D={duracao} min"),
    use_container_width=True,
)


with st.expander("Tabela do hietograma"):
    df_hieto = hieto.reset_index()
    df_hieto.columns = ["tempo_min", "altura_mm"]
    st.dataframe(df_hieto, use_container_width=True)


st.success("Hietograma pronto. Prossiga para **3. Hidrograma**.")

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
    TR = st.number_input(
        "TR (anos)", min_value=1, max_value=1000, value=int(st.session_state.TR), step=1,
        help=(
            "Período de retorno (TR): intervalo médio, em anos, entre eventos de chuva iguais ou "
            "superiores ao de projeto — equivale a uma probabilidade de cerca de 1/TR de ser igualado "
            "ou superado num ano qualquer. Entra na IDF como i = K·TR^a/(t+b)^c (com a>0), então TR "
            "maior gera chuva mais intensa e pico de cheia mais alto."
        ),
    )
    st.caption(
        "Valores de referência em drenagem (BR), que variam conforme o manual/órgão: microdrenagem "
        "(sarjetas, bocas de lobo, galerias de loteamento) TR 2-10 anos; galerias e canais de bairro "
        "TR 10-25 anos; macrodrenagem e travessias importantes TR 25-100 anos; verificação de cheia "
        "rara / extravasamento TR ≥ 100 anos."
    )
with col2:
    duracao = st.number_input(
        "Duração total (min)", min_value=5, max_value=1440,
        value=int(st.session_state.duracao_min), step=5,
        help=(
            "Duração total do evento de chuva, em minutos. Em geral adota-se uma duração da ordem do "
            "tempo de concentração da bacia (no Método Racional, a duração crítica é igual ao tc; em "
            "métodos de hidrograma unitário/convolução o pico costuma ocorrer numa duração um pouco "
            "maior), buscando a duração que maximiza a vazão de pico. Duração maior dilui a chuva num "
            "intervalo mais longo (intensidade média menor pela IDF); duração curta concentra a chuva."
        ),
    )
    st.caption(
        "Comece pela duração próxima ao tempo de concentração da bacia. Ordens de grandeza (verificar "
        "para o caso local): microbacias urbanas (<1 km²) ~10-60 min; bacias urbanas médias ~1-3 h; "
        "bacias rurais/grandes ~3-24 h. Faixa do app: 5 a 1440 min (24 h)."
    )
with col3:
    dt = st.number_input(
        "Passo dt (min)", min_value=1, max_value=60,
        value=int(st.session_state.dt_min), step=1,
        help=(
            "Passo de tempo (resolução) do hietograma: a duração é dividida em n = duração/dt blocos. "
            "Passo menor aumenta a resolução e a intensidade de pico instantânea (mm/h), porém cada "
            "bloco acumula menos chuva em mm; passo maior suaviza a curva e eleva a altura (mm) do "
            "bloco de pico. Use um valor que divida a duração de forma exata — o app avisa quando não "
            "divide."
        ),
    )
    st.caption(
        "Regra prática: dt entre 1/10 e 1/20 da duração total para não perder o pico (tipicamente "
        "5 min; 1-10 min em microdrenagem). Ex.: D=60 min com dt=5 min gera 12 blocos. Mantenha "
        "dt ≤ duração."
    )
with col4:
    metodo = st.selectbox(
        "Método",
        ["Blocos Alternados (Chicago)", "Huff 1º quartil", "Huff 2º quartil", "Huff 3º quartil", "Huff 4º quartil"],
        index=["Blocos Alternados (Chicago)", "Huff 1º quartil", "Huff 2º quartil", "Huff 3º quartil", "Huff 4º quartil"].index(st.session_state.metodo_hietograma)
        if st.session_state.metodo_hietograma in ["Blocos Alternados (Chicago)", "Huff 1º quartil", "Huff 2º quartil", "Huff 3º quartil", "Huff 4º quartil"]
        else 0,
        help=(
            "Define como a chuva total é distribuída ao longo do tempo. Blocos Alternados (Chicago) põe "
            "o pico no centro e faz com que qualquer janela centrada no pico reproduza a intensidade da "
            "IDF — gera o pico mais alto, resultado mais conservador. Huff usa curvas empíricas "
            "adimensionais (Huff, 1967); o quartil indica em qual quarto da duração cai o pico de "
            "chuva: 1º = início, 2º = segundo quarto, 3º = terceiro quarto, 4º = fim. Os dois métodos "
            "partem da mesma altura total, i(TR,D)·D/60; muda só a forma da distribuição."
        ),
    )
    st.caption(
        "Blocos Alternados é o método usual para projeto de drenagem urbana (envelope da IDF, mais "
        "conservador). Em Huff, 1º/2º quartil são típicos de chuvas curtas e intensas; 3º/4º de "
        "eventos mais longos. O 2º quartil é uma escolha comum para bacias urbanas (convenção adotada "
        "no próprio módulo)."
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

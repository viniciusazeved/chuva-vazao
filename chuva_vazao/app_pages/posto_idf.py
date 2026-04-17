"""Página 1: seleção de posto e visualização da curva IDF."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from chuva_vazao import db, idf, plots


st.title("1. Posto Pluviométrico e Curva IDF")
st.caption(
    "Escolha um posto do catálogo HidroFlu ou suba um CSV exportado pelo IDF-generator. "
    "Os coeficientes K/a/b/c alimentam todas as páginas seguintes."
)

modo = st.radio(
    "Fonte dos coeficientes IDF:",
    options=[
        "HidroFlu: IDF clássico (K/a/b/c)",
        "HidroFlu: Pfafstetter regional (a/b/c + betas)",
        "Upload CSV IDF-generator",
        "Entrada manual (K/a/b/c)",
    ],
    index=0,
    horizontal=False,
)


coef_selecionado = None

if modo.startswith("HidroFlu: IDF clássico"):
    col1, col2 = st.columns([1, 3])
    with col1:
        estados = db.list_estados_com_postos()
        # Filtrar apenas UFs com posto IDF clássico
        idf_postos = db.list_postos(fonte="idf")
        estados_idf = sorted(idf_postos["estado"].unique())
        estado_sel = st.selectbox("UF", estados_idf, index=0)
    with col2:
        postos_df = db.list_postos(estado=estado_sel, fonte="idf")
        posto_sel = st.selectbox("Posto", postos_df["descricao"].tolist())

    coef = db.get_idf_coef(posto_sel, estado=estado_sel)
    if coef is not None:
        st.session_state.posto_descricao = coef.descricao
        st.session_state.posto_estado = coef.estado
        st.session_state.posto_fonte = coef.fonte
        st.session_state.idf_K = coef.K
        st.session_state.idf_a = coef.a
        st.session_state.idf_b = coef.b
        st.session_state.idf_c = coef.c
        st.session_state.idf_params = idf.params_from_convention(
            coef.K, coef.a, coef.b, coef.c, convention="hidroflu",
        )
        coef_selecionado = coef


elif modo.startswith("HidroFlu: Pfafstetter"):
    st.warning(
        "Modo Pfafstetter em validação: a formulação exata da equação IDF "
        "nos coeficientes a/b/c do HidroFlu ainda precisa ser validada contra "
        "o executável original. Os postos ficam listados para consulta e betas "
        "regionais continuam disponíveis na Página 2."
    )
    col1, col2 = st.columns([1, 3])
    with col1:
        pfaf_postos = db.list_postos(fonte="pfafstetter")
        estados_pfaf = sorted(pfaf_postos["estado"].unique())
        estado_sel = st.selectbox("UF", estados_pfaf, index=estados_pfaf.index("RJ") if "RJ" in estados_pfaf else 0)
    with col2:
        postos_df = db.list_postos(estado=estado_sel, fonte="pfafstetter")
        posto_sel = st.selectbox("Posto", postos_df["descricao"].tolist())

    coef = db.get_pfafstetter_coef(posto_sel, estado=estado_sel)
    if coef is not None:
        st.session_state.posto_descricao = coef.descricao
        st.session_state.posto_estado = coef.estado
        st.session_state.posto_fonte = coef.fonte
        st.caption(
            f"a = {coef.a:.3f}, b = {coef.b:.3f}, c = {coef.c:.3f} | "
            f"betas = (5min={coef.beta5min:.3f}, 15min={coef.beta15min:.3f}, "
            f"30min={coef.beta30min:.3f}, 1h-6d={coef.beta1h_6dias:.3f})"
        )


elif modo.startswith("Upload"):
    st.caption(
        "Aceita o **TXT** exportado pelo [IDF-generator](https://idf-generator.streamlit.app) "
        "(formato `K = ...`, `a = ...`) ou um CSV com colunas K, a, b, c. "
        "Convenção do IDF-generator: `i = K · TR^a / (t + b)^c`."
    )
    up = st.file_uploader(
        "Arquivo exportado pelo IDF-generator",
        type=["csv", "txt"],
    )
    if up is not None:
        raw = up.read()
        try:
            params = idf.params_from_idf_generator_auto(up.name, raw)
        except (ValueError, KeyError) as exc:
            st.error(f"Falha ao interpretar: {exc}")
        else:
            st.success(
                f"K={params.K:.3f}, a={params.expoente_tr:.4f}, "
                f"b={params.expoente_duracao:.4f}, c={params.constante_duracao:.2f} (convenção idf_generator)"
            )
            nome = st.text_input("Nome do posto", value=up.name.rsplit(".", 1)[0])
            uf = st.text_input("UF", value="--", max_chars=2)
            if st.button("Aplicar coeficientes"):
                st.session_state.posto_descricao = nome
                st.session_state.posto_estado = uf
                st.session_state.posto_fonte = "idf_generator"
                st.session_state.idf_K = params.K
                st.session_state.idf_a = params.expoente_tr
                st.session_state.idf_b = params.expoente_duracao
                st.session_state.idf_c = params.constante_duracao
                st.session_state.idf_params = params
                st.rerun()


else:  # Entrada manual
    with st.form("manual_idf"):
        nome = st.text_input("Nome do posto", value="Posto manual")
        uf = st.text_input("UF", value="--", max_chars=2)
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            K = st.number_input("K", value=800.0, step=10.0, format="%.3f")
        with col2:
            a = st.number_input("a (expoente TR)", value=0.18, step=0.01, format="%.4f")
        with col3:
            b = st.number_input("b (expoente duração)", value=0.75, step=0.01, format="%.4f")
        with col4:
            c = st.number_input("c (constante duração, min)", value=10.0, step=1.0, format="%.2f")
        convencao = st.radio("Convenção", ["hidroflu", "idf_generator"], horizontal=True)
        submit = st.form_submit_button("Aplicar")
        if submit:
            st.session_state.posto_descricao = nome
            st.session_state.posto_estado = uf
            st.session_state.posto_fonte = "manual"
            st.session_state.idf_K = K
            st.session_state.idf_a = a
            st.session_state.idf_b = b
            st.session_state.idf_c = c
            st.session_state.idf_params = idf.params_from_convention(
                K, a, b, c, convention=convencao,
            )


# ---------------------------------------------------------------------------
# Visualizacao da IDF atual
# ---------------------------------------------------------------------------

params = st.session_state.get("idf_params")
if params is None:
    st.info("Selecione ou importe coeficientes para visualizar a curva IDF.")
else:
    st.divider()
    st.subheader(f"Curva IDF — {st.session_state.posto_descricao} ({st.session_state.posto_estado})")

    col1, col2 = st.columns([1, 2])
    with col1:
        st.plotly_chart(plots.plot_idf_params(params), use_container_width=True)
        st.metric("K", f"{params.K:.3f}")
        c1, c2 = st.columns(2)
        c1.metric("a (expoente TR)", f"{params.expoente_tr:.4f}")
        c2.metric("b (expoente dur)", f"{params.expoente_duracao:.4f}")
        st.metric("c (constante, min)", f"{params.constante_duracao:.2f}")

    with col2:
        duracoes = [5, 10, 15, 30, 60, 120, 360, 720, 1440]
        TRs = [2, 5, 10, 25, 50, 100]
        tabela = idf.calcular_idf(params, duracoes_min=duracoes, TRs=TRs)
        st.session_state.idf_table = tabela
        st.plotly_chart(
            plots.plot_idf_curves(
                tabela,
                titulo=f"IDF — {st.session_state.posto_descricao}",
            ),
            use_container_width=True,
        )

    st.subheader("Tabela IDF (mm/h)")
    st.dataframe(tabela.round(2), use_container_width=True)

    st.success("Coeficientes carregados. Prossiga para **2. Hietograma**.")

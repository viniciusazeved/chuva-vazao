"""Página 1: seleção de posto e visualização da curva IDF."""
from __future__ import annotations

import streamlit as st

from chuva_vazao import idf, plots


st.title("1. Posto Pluviométrico e Curva IDF")
st.caption(
    "Suba o **TXT/CSV** exportado pelo "
    "[IDF-generator](https://idf-generator.streamlit.app) ou informe os "
    "coeficientes manualmente. Os valores K/a/b/c alimentam todas as "
    "páginas seguintes."
)

modo = st.radio(
    "Fonte dos coeficientes IDF:",
    options=[
        "Upload IDF-generator (TXT/CSV)",
        "Entrada manual (K/a/b/c)",
    ],
    index=0,
    horizontal=True,
    help="Escolhe a origem dos coeficientes K, a, b e c. 'Upload' lê o arquivo exportado pelo IDF-generator; 'Entrada manual' deixa você digitar os quatro coeficientes. A escolha só muda a forma de entrada, não o cálculo.",
)


if modo.startswith("Upload"):
    st.caption(
        "Aceita o **TXT** exportado pelo [IDF-generator](https://idf-generator.streamlit.app) "
        "(formato `K = ...`, `a = ...`) ou um CSV com colunas K, a, b, c. "
        "Convenção: `i = K · TR^a / (t + b)^c`."
    )
    up = st.file_uploader(
        "Arquivo exportado pelo IDF-generator",
        type=["csv", "txt"],
        help="Arquivo .txt ou .csv com a curva IDF do IDF-generator. Aceita o TXT com linhas 'K = ...', 'a = ...' ou um CSV com colunas K, a, b, c; se for um CSV de tabela (duração x TR), o app reajusta os coeficientes por regressão.",
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
                f"b={params.constante_duracao:.2f}, c={params.expoente_duracao:.4f}"
            )
            nome = st.text_input(
                "Nome do posto",
                value=up.name.rsplit(".", 1)[0],
                help="Apenas um rótulo para identificar a curva nos gráficos e no relatório. Não entra em nenhum cálculo.",
            )
            uf = st.text_input(
                "UF",
                value="--",
                max_chars=2,
                help="Sigla de 2 letras do estado do posto (ex.: RJ, SP, MG). Só aparece no relatório; não afeta o cálculo.",
            )
            if st.button("Aplicar coeficientes"):
                st.session_state.posto_descricao = nome
                st.session_state.posto_estado = uf
                st.session_state.posto_fonte = "idf_generator"
                st.session_state.idf_K = params.K
                st.session_state.idf_a = params.expoente_tr
                st.session_state.idf_b = params.constante_duracao
                st.session_state.idf_c = params.expoente_duracao
                st.session_state.idf_params = params
                st.rerun()


else:  # Entrada manual
    st.caption(
        "Convenção adotada: `i = K · TR^a / (t + b)^c` "
        "(mesma do IDF-generator)."
    )
    with st.form("manual_idf"):
        nome = st.text_input(
            "Nome do posto",
            value="Posto manual",
            help="Apenas um rótulo para identificar a curva nos gráficos e no relatório. Não entra em nenhum cálculo. Mesmo campo do ramo Upload.",
        )
        uf = st.text_input(
            "UF",
            value="--",
            max_chars=2,
            help="Sigla de 2 letras do estado do posto (ex.: RJ, SP, MG). Só aparece no relatório; não afeta o cálculo. Mesmo campo do ramo Upload.",
        )
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            K = st.number_input(
                "K",
                value=800.0,
                min_value=1.0,
                max_value=100000.0,
                step=10.0,
                format="%.3f",
                help="Fator de escala da equação i = K*TR^a/(t+b)^c. A intensidade é diretamente proporcional a K: dobrar K dobra a chuva em toda a curva, para qualquer período de retorno e qualquer duração. K carrega as unidades para a intensidade sair em mm/h.",
            )
            st.caption("Fator de escala: varia muito entre postos (só faz sentido comparar K para a mesma forma de equação). Ordem de grandeza típica no Brasil ~500 a 3.000 (postos do RJ perto de 1.000); o ajuste automático do app aceita de 1 a 100.000.")
        with col2:
            a = st.number_input(
                "a (expoente TR)",
                value=0.18,
                min_value=0.05,
                max_value=1.0,
                step=0.01,
                format="%.4f",
                help="Expoente que controla como a intensidade cresce com o período de retorno: i é proporcional a TR^a. Com a=0,18, ir de TR=10 para TR=100 anos multiplica a intensidade por 10^0,18 (cerca de 1,5 vez). Quanto maior a, mais os eventos raros se destacam.",
            )
            st.caption("Expoente do TR: na maioria dos postos brasileiros fica entre ~0,10 e 0,30 (RJ ~0,17); o ajuste do app aceita de 0,05 a 1,0.")
        with col3:
            b = st.number_input(
                "b (constante duração, min)",
                value=10.0,
                min_value=0.0,
                max_value=120.0,
                step=1.0,
                format="%.2f",
                help="Constante somada à duração no denominador (em minutos): i é proporcional a 1/(t+b)^c. Evita intensidade infinita em durações muito curtas e suaviza o topo da curva. Quanto maior b, menores as intensidades nas durações curtas (poucos minutos); em durações longas quase não influi.",
            )
            st.caption("Constante de duração (min): tipicamente ~5 a 30 min (RJ ~10); o ajuste do app aceita de 0 a 120 min.")
        with col4:
            c = st.number_input(
                "c (expoente duração)",
                value=0.75,
                min_value=0.3,
                max_value=1.5,
                step=0.01,
                format="%.4f",
                help="Expoente que controla a queda da intensidade conforme a duração aumenta: i é proporcional a 1/(t+b)^c. Quanto maior c, mais rápido a intensidade cai entre chuvas curtas e longas, deixando a curva IDF mais íngreme.",
            )
            st.caption("Expoente da duração: na maioria dos postos brasileiros fica entre ~0,65 e 0,90 (RJ ~0,75); o ajuste do app aceita de 0,3 a 1,5.")
        submit = st.form_submit_button("Aplicar")
        if submit:
            st.session_state.posto_descricao = nome
            st.session_state.posto_estado = uf
            st.session_state.posto_fonte = "manual"
            st.session_state.idf_K = K
            st.session_state.idf_a = a
            st.session_state.idf_b = b
            st.session_state.idf_c = c
            st.session_state.idf_params = idf.params_from_kabc(K, a, b, c)
            st.rerun()


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
        c2.metric("b (constante, min)", f"{params.constante_duracao:.2f}")
        st.metric("c (expoente dur)", f"{params.expoente_duracao:.4f}")

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

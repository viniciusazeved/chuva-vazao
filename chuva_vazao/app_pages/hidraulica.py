"""Página 5: dimensionamento hidráulico via Manning."""
from __future__ import annotations

import streamlit as st

from chuva_vazao import hidraulica as hd
from chuva_vazao import plots


st.title("5. Dimensionamento Hidráulico")
st.caption(
    "Manning para galeria circular (manilha) ou retangular (celular). "
    "Suporta múltiplas linhas em paralelo, modo automático (menor seção comercial "
    "que atende) ou manual (você escolhe a seção da lista comercial)."
)

hg = st.session_state.get("hidrograma")
if hg is None:
    st.error("Gere o hidrograma na Página 3 antes.")

    st.stop()


# ---------------------------------------------------------------------------
# Q_projeto
# ---------------------------------------------------------------------------

Q_pico_cenario = float(hg["Q_m3s"].max()) if "Q_m3s" in hg.columns else 0.0
Q_projeto = st.number_input(
    "Q de projeto (m³/s)",
    min_value=0.001, max_value=10_000.0,
    value=float(Q_pico_cenario),
    step=0.1,
    format="%.3f",
    help=(
        "Vazão de pico que o conduto precisa transportar (m³/s). Vem do pico do "
        f"hidrograma da Página 3 ({Q_pico_cenario:.3f} m³/s), mas você pode sobrescrever. Quanto maior a vazão, "
        "maior a seção necessária (diâmetro ou box)."
    ),
)


# ---------------------------------------------------------------------------
# Parametros hidraulicos comuns
# ---------------------------------------------------------------------------

col1, col2, col3 = st.columns(3)
with col1:
    material = st.selectbox(
        "Material", list(hd.MANNING_N.keys()), index=0,
        help=(
            "Material da parede do conduto, que define o coeficiente de rugosidade n "
            "de Manning (atrito interno). Material mais liso (n menor) dá maior "
            "velocidade e vazão para a mesma seção e declividade; revestimentos "
            "rugosos exigem seção maior. O valor de n adotado aparece logo abaixo do "
            "campo."
        ),
    )
    st.caption(
        "n desta lista: 0,010 PVC liso; 0,013 concreto liso/manilha (padrão); "
        "0,015 concreto rugoso, moldado in loco ou canal revestido; 0,020 a 0,024 "
        "corrugados (PVC 0,020; PEAD 0,022; metálico 0,024); 0,025 pedra "
        "argamassada; 0,027 gabião; 0,030 canal de terra."
    )
    n = hd.MANNING_N[material]
    st.caption(f"n de Manning = {n}")
with col2:
    S = st.number_input(
        "Declividade S (m/m)", min_value=0.0005, max_value=0.2,
        value=0.01, step=0.001, format="%.4f",
        help=(
            "Declividade longitudinal do fundo do conduto, em metro por metro "
            "(0,01 = 1%). Na fórmula de Manning a vazão cresce com a raiz da "
            "declividade: dobrar S aumenta a capacidade em cerca de 41%. Declividade "
            "baixa reduz a velocidade e favorece sedimentação (o app alerta se a "
            "velocidade ficar abaixo de 0,60 m/s)."
        ),
    )
    st.caption(
        "Faixas usuais em drenagem urbana: 0,3% a 5% (0,003 a 0,05 m/m). Para "
        "garantir autolimpeza (velocidade ~0,60 m/s) costuma-se precisar de pelo "
        "menos ~0,5% (0,005), dependendo da seção e do n. Acima de 5% (0,05) é "
        "terreno íngreme, com risco de abrasão (o app alerta acima de 5 m/s). O "
        "mínimo do campo, 0,0005 (0,05%), é válido mas raramente atende autolimpeza."
    )
with col3:
    fator = st.number_input(
        "Fator de segurança", min_value=1.0, max_value=2.0,
        value=1.10, step=0.05,
        help=(
            "Multiplica a vazão de projeto antes de escolher a seção, criando folga "
            "de capacidade. 1,10 dimensiona para 10% acima do pico. Valores maiores "
            "levam a seções mais conservadoras. A lâmina e a velocidade de operação "
            "mostradas são calculadas com a vazão SEM o fator. No modo Manual o fator "
            "não altera o resultado, porque não há seleção automática de seção."
        ),
    )
    st.caption(
        "Faixas usuais: 1,10 a 1,20 em projetos correntes; 1,20 a 1,50 quando há "
        "incerteza alta na vazão, assoreamento previsível ou obra crítica. Acima de "
        "1,5 raramente se justifica. É critério de projeto, não norma fechada."
    )


secao = st.radio(
    "Seção", ["Circular (manilha)", "Retangular (celular)"], horizontal=True,
    help=(
        "Geometria do conduto. Circular (manilha/tubo) é o padrão para galerias, com "
        "diâmetros comerciais até 3,0 m. Retangular (bueiro celular/box) atende "
        "vazões maiores ou travessias largas e baixas. A escolha troca a lista de "
        "seções comerciais disponíveis abaixo."
    ),
)


# ---------------------------------------------------------------------------
# Circular
# ---------------------------------------------------------------------------

if secao.startswith("Circular"):
    col1, col2, col3 = st.columns(3)
    with col1:
        n_linhas = st.number_input(
            "Nº de linhas em paralelo", min_value=1, max_value=10,
            value=1, step=1,
            help=(
                "Quantidade de tubos idênticos em paralelo; a vazão de projeto é "
                "dividida igualmente entre eles (Q por linha = Q/N). Mais linhas "
                "permitem diâmetros menores, mas multiplicam a obra (mais valas e "
                "mais tubos)."
            ),
        )
    with col2:
        modo = st.radio(
            "Modo", ["Auto", "Manual"], horizontal=True,
            help=(
                "Auto: o app busca o menor diâmetro comercial cuja lâmina de operação "
                "fique dentro do limite definido. Manual: você fixa o diâmetro e o app "
                "só verifica se atende, avisando se a lâmina ultrapassa o limite."
            ),
        )
    with col3:
        lamina_max = st.slider(
            "Lâmina máxima (% do diâmetro)", 50, 100, 80,
            help=(
                "Limite de enchimento do tubo: fração do diâmetro que a água pode "
                "ocupar na vazão de dimensionamento. 80% mantém superfície livre e "
                "folga de ventilação. Quanto mais perto de 100%, mais o conduto se "
                "aproxima da seção plena, sem margem."
            ),
        ) / 100.0
        st.caption(
            "Faixa usual em galerias circulares com superfície livre: 75% a 85% do "
            "diâmetro (DAEE/ABNT), padrão 80%. Subir além disso rende pouco: a vazão "
            "máxima do tubo ocorre por volta de 94% de enchimento (e a velocidade "
            "máxima por volta de 81%), e perde-se ventilação."
        )

    if modo == "Manual":
        diametros_mm = [int(round(d * 1000)) for d in hd.COMMERCIAL_DIAMETERS_M]
        D_mm = st.selectbox(
            "Diâmetro comercial (mm)", diametros_mm,
            index=diametros_mm.index(1000) if 1000 in diametros_mm else 0,
            help=(
                "Diâmetro nominal do tubo, escolhido da lista comercial. O app calcula "
                "a lâmina e a velocidade de operação para esse diâmetro e avisa se a "
                "lâmina ultrapassa o limite. Diâmetro maior reduz a lâmina e a "
                "velocidade."
            ),
        )
        D_m = D_mm / 1000.0
        try:
            dim = hd.avaliar_circular_manual(
                Q_projeto_m3_s=Q_projeto,
                D_m=D_m,
                S_m_per_m=S, n=n,
                fator_seguranca=fator,
                lamina_max_ratio=lamina_max,
                n_linhas=int(n_linhas),
            )
        except ValueError as exc:
            st.error(str(exc))
            st.stop()
    else:
        try:
            dim = hd.size_circular_culvert(
                Q_projeto_m3_s=Q_projeto,
                S_m_per_m=S, n=n,
                fator_seguranca=fator,
                lamina_max_ratio=lamina_max,
                n_linhas=int(n_linhas),
            )
        except ValueError as exc:
            st.error(str(exc))
            st.stop()

    Q_por_linha = Q_projeto / n_linhas
    Q_total_capacidade = dim.operacao.Q_m3_s * n_linhas

    st.subheader("Resultado")
    if n_linhas > 1:
        st.markdown(
            f"**{int(n_linhas)} manilhas Ø {dim.D_adotado_m * 1000:.0f} mm** em paralelo"
        )
    else:
        st.markdown(f"**1 manilha Ø {dim.D_adotado_m * 1000:.0f} mm**")

    col1, col2, col3 = st.columns(3)
    col1.metric("Diâmetro", f"{dim.D_adotado_m * 1000:.0f} mm")
    col2.metric("Q por linha", f"{Q_por_linha:.3f} m³/s")
    col3.metric("Q total (cap.)", f"{Q_total_capacidade:.3f} m³/s")

    col1, col2, col3 = st.columns(3)
    col1.metric("Lâmina de operação", f"{dim.operacao.h_m * 100:.1f} cm")
    col2.metric("Fill ratio", f"{dim.operacao.fill_ratio * 100:.1f} %")
    col3.metric("Velocidade", f"{dim.operacao.v_m_s:.2f} m/s")

    for w in dim.warnings:
        st.warning(w)

    col_a, col_b = st.columns(2)
    with col_a:
        st.plotly_chart(
            plots.plot_conduto_circular(
                D_m=dim.D_adotado_m,
                h_op_m=dim.operacao.h_m,
                h_max_m=dim.lamina_max_permitida,
                v_m_s=dim.operacao.v_m_s,
            ),
            use_container_width=True,
        )
    with col_b:
        st.plotly_chart(
            plots.plot_curva_caracteristica_circular(
                D_m=dim.D_adotado_m,
                S_m_per_m=S, n=n,
                h_op_m=dim.operacao.h_m,
                h_max_m=dim.lamina_max_permitida,
                Q_op_m3_s=Q_por_linha,
            ),
            use_container_width=True,
        )

    st.session_state.dimensionamento = {
        "tipo": "circular",
        "modo": modo.lower(),
        "n_linhas": int(n_linhas),
        "material": material,
        "n": n,
        "S": S,
        "fator_seguranca": fator,
        "lamina_max_ratio": lamina_max,
        "D_adotado_m": dim.D_adotado_m,
        "h_op_m": dim.operacao.h_m,
        "v_op_m_s": dim.operacao.v_m_s,
        "Q_projeto_m3_s": dim.Q_projeto_m3_s,
        "Q_por_linha_m3_s": Q_por_linha,
        "Q_total_capacidade_m3_s": Q_total_capacidade,
        "warnings": dim.warnings,
    }

    with st.expander("Detalhes da operação (por linha)"):
        st.json({
            "A_m2": round(dim.operacao.A_m2, 4),
            "P_m": round(dim.operacao.P_m, 4),
            "R_m": round(dim.operacao.R_m, 4),
            "v_m_s": round(dim.operacao.v_m_s, 3),
            "Q_por_linha_m3_s": round(dim.operacao.Q_m3_s, 3),
        })


# ---------------------------------------------------------------------------
# Retangular
# ---------------------------------------------------------------------------

else:
    col1, col2, col3 = st.columns(3)
    with col1:
        n_linhas = st.number_input(
            "Nº de células em paralelo", min_value=1, max_value=10,
            value=1, step=1,
            help=(
                "Quantidade de células (boxes) idênticas em paralelo; a vazão de "
                "projeto é dividida igualmente entre elas (Q por célula = Q/N). Mais "
                "células permitem seções individuais menores, mas aumentam a largura "
                "total da obra."
            ),
        )
    with col2:
        modo = st.radio(
            "Modo", ["Auto", "Manual"], horizontal=True,
            help=(
                "Auto: o app busca a menor seção comercial (por área) cuja lâmina de "
                "operação fique dentro do limite. Manual: você fixa a seção B×H e o app "
                "só verifica se atende, avisando se a lâmina ultrapassa o limite."
            ),
        )
    with col3:
        lamina_max = st.slider(
            "Lâmina máxima (% da altura)", 50, 100, 85,
            help=(
                "Limite de enchimento do box: fração da altura que a água pode ocupar "
                "na vazão de dimensionamento; o resto é borda livre. 85% deixa cerca "
                "de 15% de folga no topo. Quanto maior o valor, menor a borda livre."
            ),
        ) / 100.0
        st.caption(
            "Faixa usual em bueiros celulares: 80% a 90% da altura, deixando 10% a "
            "20% de borda livre, padrão 85%. Travessias sob rodovia tendem a adotar "
            "folga maior (limite inferior). É critério de projeto."
        )

    if modo == "Manual":
        secoes_lbl = [f"{b:.1f} × {h:.1f} m" for b, h in hd.COMMERCIAL_BOX_SECTIONS_M]
        idx = st.selectbox(
            "Seção comercial (B × H)", range(len(secoes_lbl)),
            format_func=lambda i: secoes_lbl[i],
            index=secoes_lbl.index("2.0 × 2.0 m") if "2.0 × 2.0 m" in secoes_lbl else 0,
            help=(
                "Seção retangular (largura × altura) escolhida da lista de boxes "
                "comerciais. O app calcula a lâmina e a velocidade de operação e avisa "
                "se a lâmina excede o limite. Seção maior reduz a lâmina e a "
                "velocidade. No modo Auto, as seções são percorridas em ordem de área "
                "crescente."
            ),
        )
        b_sel, h_sel = hd.COMMERCIAL_BOX_SECTIONS_M[idx]
        try:
            dim = hd.avaliar_box_manual(
                Q_projeto_m3_s=Q_projeto,
                b_m=b_sel, h_total_m=h_sel,
                S_m_per_m=S, n=n,
                fator_seguranca=fator,
                lamina_max_ratio=lamina_max,
                n_linhas=int(n_linhas),
            )
        except ValueError as exc:
            st.error(str(exc))
            st.stop()
    else:
        try:
            dim = hd.size_box_culvert_commercial(
                Q_projeto_m3_s=Q_projeto,
                S_m_per_m=S, n=n,
                fator_seguranca=fator,
                lamina_max_ratio=lamina_max,
                n_linhas=int(n_linhas),
            )
        except ValueError as exc:
            st.error(str(exc))
            st.stop()

    Q_por_linha = Q_projeto / n_linhas
    Q_total_capacidade = dim.operacao.Q_m3_s * n_linhas

    st.subheader("Resultado")
    if n_linhas > 1:
        st.markdown(
            f"**{int(n_linhas)} células {dim.b_m:.1f} × {dim.h_total_m:.1f} m** em paralelo"
        )
    else:
        st.markdown(f"**1 célula {dim.b_m:.1f} × {dim.h_total_m:.1f} m**")

    col1, col2, col3 = st.columns(3)
    col1.metric("B × H", f"{dim.b_m:.2f} × {dim.h_total_m:.2f} m")
    col2.metric("Q por linha", f"{Q_por_linha:.3f} m³/s")
    col3.metric("Q total (cap.)", f"{Q_total_capacidade:.3f} m³/s")

    col1, col2, col3 = st.columns(3)
    col1.metric("Lâmina de operação", f"{dim.operacao.h_m * 100:.1f} cm")
    col2.metric("Lâmina máx permitida", f"{dim.lamina_max_permitida * 100:.1f} cm")
    col3.metric("Velocidade", f"{dim.operacao.v_m_s:.2f} m/s")

    for w in dim.warnings:
        st.warning(w)

    col_a, col_b = st.columns(2)
    with col_a:
        st.plotly_chart(
            plots.plot_conduto_retangular(
                b_m=dim.b_m,
                h_total_m=dim.h_total_m,
                h_op_m=dim.operacao.h_m,
                h_max_m=dim.lamina_max_permitida,
                v_m_s=dim.operacao.v_m_s,
            ),
            use_container_width=True,
        )
    with col_b:
        st.plotly_chart(
            plots.plot_curva_caracteristica_retangular(
                b_m=dim.b_m, h_total_m=dim.h_total_m,
                S_m_per_m=S, n=n,
                h_op_m=dim.operacao.h_m,
                h_max_m=dim.lamina_max_permitida,
                Q_op_m3_s=Q_por_linha,
            ),
            use_container_width=True,
        )

    st.session_state.dimensionamento = {
        "tipo": "retangular",
        "modo": modo.lower(),
        "n_linhas": int(n_linhas),
        "material": material,
        "n": n,
        "S": S,
        "fator_seguranca": fator,
        "lamina_max_ratio": lamina_max,
        "b_m": dim.b_m,
        "h_total_m": dim.h_total_m,
        "h_op_m": dim.operacao.h_m,
        "v_op_m_s": dim.operacao.v_m_s,
        "Q_projeto_m3_s": dim.Q_projeto_m3_s,
        "Q_por_linha_m3_s": Q_por_linha,
        "Q_total_capacidade_m3_s": Q_total_capacidade,
        "warnings": dim.warnings,
    }

    with st.expander("Detalhes da operação (por célula)"):
        st.json({
            "A_m2": round(dim.operacao.A_m2, 4),
            "P_m": round(dim.operacao.P_m, 4),
            "R_m": round(dim.operacao.R_m, 4),
            "v_m_s": round(dim.operacao.v_m_s, 3),
            "Q_por_linha_m3_s": round(dim.operacao.Q_m3_s, 3),
        })

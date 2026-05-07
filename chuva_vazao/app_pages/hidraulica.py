"""Página 4: dimensionamento hidráulico via Manning."""
from __future__ import annotations

import streamlit as st

from chuva_vazao import hidraulica as hd


st.title("4. Dimensionamento Hidráulico")
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
    help=f"Padrão = Q_pico do hidrograma ({Q_pico_cenario:.3f} m³/s).",
)


# ---------------------------------------------------------------------------
# Parametros hidraulicos comuns
# ---------------------------------------------------------------------------

col1, col2, col3 = st.columns(3)
with col1:
    material = st.selectbox("Material", list(hd.MANNING_N.keys()), index=0)
    n = hd.MANNING_N[material]
    st.caption(f"n de Manning = {n}")
with col2:
    S = st.number_input(
        "Declividade S (m/m)", min_value=0.0005, max_value=0.2,
        value=0.01, step=0.001, format="%.4f",
    )
with col3:
    fator = st.number_input(
        "Fator de segurança", min_value=1.0, max_value=2.0,
        value=1.10, step=0.05,
    )


secao = st.radio("Seção", ["Circular (manilha)", "Retangular (celular)"], horizontal=True)


# ---------------------------------------------------------------------------
# Circular
# ---------------------------------------------------------------------------

if secao.startswith("Circular"):
    col1, col2, col3 = st.columns(3)
    with col1:
        n_linhas = st.number_input(
            "Nº de linhas em paralelo", min_value=1, max_value=10,
            value=1, step=1,
            help="Ex: 3 manilhas Ø1000 mm. Q é dividida igualmente entre elas.",
        )
    with col2:
        modo = st.radio(
            "Modo", ["Auto", "Manual"], horizontal=True,
            help="Auto: escolhe o menor diâmetro comercial. Manual: você escolhe.",
        )
    with col3:
        lamina_max = st.slider("Lâmina máxima (% do diâmetro)", 50, 100, 80) / 100.0

    if modo == "Manual":
        diametros_mm = [int(round(d * 1000)) for d in hd.COMMERCIAL_DIAMETERS_M]
        D_mm = st.selectbox(
            "Diâmetro comercial (mm)", diametros_mm,
            index=diametros_mm.index(1000) if 1000 in diametros_mm else 0,
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
            help="Ex: 2 boxes 2.0×2.0 m. Q é dividida igualmente.",
        )
    with col2:
        modo = st.radio(
            "Modo", ["Auto", "Manual"], horizontal=True,
            help="Auto: menor box comercial. Manual: você escolhe da lista.",
        )
    with col3:
        lamina_max = st.slider("Lâmina máxima (% da altura)", 50, 100, 85) / 100.0

    if modo == "Manual":
        secoes_lbl = [f"{b:.1f} × {h:.1f} m" for b, h in hd.COMMERCIAL_BOX_SECTIONS_M]
        idx = st.selectbox(
            "Seção comercial (B × H)", range(len(secoes_lbl)),
            format_func=lambda i: secoes_lbl[i],
            index=secoes_lbl.index("2.0 × 2.0 m") if "2.0 × 2.0 m" in secoes_lbl else 0,
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

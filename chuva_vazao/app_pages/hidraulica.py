"""Página 4: dimensionamento hidráulico via Manning."""
from __future__ import annotations

import streamlit as st

from chuva_vazao import hidraulica as hd
from chuva_vazao import hidrograma as hg_mod


st.title("4. Dimensionamento Hidráulico")
st.caption(
    "Manning para galeria circular (manilha) ou retangular (celular). "
    "Escolhe o menor diâmetro comercial que atenda a vazão de projeto com "
    "lâmina máxima 80% e fator de segurança configurável."
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
    lamina_max = st.slider("Lâmina máxima (% do diâmetro)", 50, 100, 80) / 100.0

    try:
        dim = hd.size_circular_culvert(
            Q_projeto_m3_s=Q_projeto,
            S_m_per_m=S,
            n=n,
            fator_seguranca=fator,
            lamina_max_ratio=lamina_max,
        )
    except ValueError as exc:
        st.error(str(exc))
        st.stop()

    st.subheader("Resultado")
    col1, col2, col3 = st.columns(3)
    col1.metric("Diâmetro adotado", f"{dim.D_adotado_m * 100:.0f} cm")
    col2.metric("Q projeto", f"{dim.Q_projeto_m3_s:.3f} m³/s")
    col3.metric("Q com fator", f"{dim.Q_fator_seguranca_m3_s:.3f} m³/s")

    col1, col2, col3 = st.columns(3)
    col1.metric("Lâmina de operação", f"{dim.operacao.h_m * 100:.1f} cm")
    col2.metric("Fill ratio", f"{dim.operacao.fill_ratio * 100:.1f} %")
    col3.metric("Velocidade", f"{dim.operacao.v_m_s:.2f} m/s")

    for w in dim.warnings:
        st.warning(w)

    st.session_state.dimensionamento = {
        "tipo": "circular",
        "material": material,
        "n": n,
        "S": S,
        "fator_seguranca": fator,
        "lamina_max_ratio": lamina_max,
        "D_adotado_m": dim.D_adotado_m,
        "h_op_m": dim.operacao.h_m,
        "v_op_m_s": dim.operacao.v_m_s,
        "Q_projeto_m3_s": dim.Q_projeto_m3_s,
        "warnings": dim.warnings,
    }

    with st.expander("Detalhes da operação"):
        st.json({
            "A_m2": round(dim.operacao.A_m2, 4),
            "P_m": round(dim.operacao.P_m, 4),
            "R_m": round(dim.operacao.R_m, 4),
            "v_m_s": round(dim.operacao.v_m_s, 3),
            "Q_m3_s": round(dim.operacao.Q_m3_s, 3),
        })


# ---------------------------------------------------------------------------
# Retangular
# ---------------------------------------------------------------------------

else:
    col1, col2 = st.columns(2)
    with col1:
        razao = st.number_input(
            "Razão b/h", min_value=0.3, max_value=5.0,
            value=1.5, step=0.1,
        )
    with col2:
        lamina_max = st.slider("Lâmina máxima (% da altura)", 50, 100, 85) / 100.0

    try:
        result = hd.size_box_culvert(
            Q_projeto_m3_s=Q_projeto,
            S_m_per_m=S,
            n=n,
            razao_b_h=razao,
            fator_seguranca=fator,
            lamina_max_ratio=lamina_max,
        )
    except ValueError as exc:
        st.error(str(exc))
        st.stop()

    st.subheader("Resultado")
    col1, col2, col3 = st.columns(3)
    col1.metric("Largura b", f"{result['b_m']:.2f} m")
    col2.metric("Altura total h", f"{result['h_total_m']:.2f} m")
    col3.metric("Lâmina máx permitida", f"{result['h_lamina_max_m']:.2f} m")

    op = result["operacao"]
    col1, col2 = st.columns(2)
    col1.metric("Lâmina de operação", f"{op.h_m:.2f} m")
    col2.metric("Velocidade", f"{op.v_m_s:.2f} m/s")

    for w in result["warnings"]:
        st.warning(w)

    st.session_state.dimensionamento = {
        "tipo": "retangular",
        "material": material,
        "n": n,
        "S": S,
        "fator_seguranca": fator,
        "b_m": result["b_m"],
        "h_total_m": result["h_total_m"],
        "h_op_m": op.h_m,
        "v_op_m_s": op.v_m_s,
        "Q_projeto_m3_s": Q_projeto,
        "warnings": result["warnings"],
    }

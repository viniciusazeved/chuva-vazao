"""
Entrypoint do app Streamlit chuva_vazao.

Uso:
    uv run streamlit run chuva_vazao/app.py
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import os

import streamlit as st


# Em producao (Streamlit Cloud) injeta service account GEE como env vars,
# para gee_client.init() detectar sem precisar de 'earthengine authenticate'.
try:
    if "gee" in st.secrets:
        os.environ.setdefault(
            "GEE_SERVICE_ACCOUNT_EMAIL",
            st.secrets["gee"]["service_account_email"],
        )
        os.environ.setdefault(
            "GEE_SERVICE_ACCOUNT_KEY_JSON",
            st.secrets["gee"]["service_account_key_json"],
        )
except Exception:
    pass  # sem secrets -> usa credenciais locais (dev)


st.set_page_config(
    page_title="chuva_vazao",
    page_icon=":cloud_with_rain:",
    layout="wide",
)


# ---------------------------------------------------------------------------
# Bootstrap session_state (valores padrao)
# ---------------------------------------------------------------------------

def _default_state() -> None:
    defaults = {
        "posto_descricao": None,
        "posto_estado": None,
        "posto_fonte": None,
        "idf_params": None,
        "idf_K": None,
        "idf_a": None,
        "idf_b": None,
        "idf_c": None,
        "idf_table": None,
        "TR": 10,
        "duracao_min": 60,
        "dt_min": 5,
        "metodo_hietograma": "Blocos Alternados (Chicago)",
        "huff_quartil": 2,
        "hietograma": None,
        "area_km2": 10.0,
        "tc_h": 1.0,
        "CN": 75.0,
        "scs_params": None,
        "hidrograma": None,
        "metodo_chuva_vazao": None,
        "Q_pico_racional": None,
        "C_racional": None,
        "uso_solo_racional": None,
        "dimensionamento": None,
        "detencao": None,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


_default_state()


# ---------------------------------------------------------------------------
# Navegacao
# ---------------------------------------------------------------------------

pages_dir = Path(__file__).parent / "app_pages"

pg = st.navigation([
    st.Page(str(pages_dir / "bacia.py"), title="0. Bacia", icon=":material/terrain:"),
    st.Page(str(pages_dir / "posto_idf.py"), title="1. Posto e IDF", icon=":material/location_on:"),
    st.Page(str(pages_dir / "hietograma.py"), title="2. Hietograma", icon=":material/rainy:"),
    st.Page(str(pages_dir / "hidrograma.py"), title="3. Chuva-Vazão", icon=":material/water_drop:"),
    st.Page(str(pages_dir / "hidraulica.py"), title="4. Hidráulica", icon=":material/plumbing:"),
    st.Page(str(pages_dir / "detencao.py"), title="5. Detenção", icon=":material/waves:"),
    st.Page(str(pages_dir / "exportar.py"), title="6. Exportar", icon=":material/download:"),
])

with st.sidebar:
    st.markdown("### chuva_vazao")
    st.caption(
        "Pipeline chuva → vazão para drenagem urbana, "
        "a partir do banco HidroFlu v2.0 (UFRJ/COPPE)."
    )
    st.divider()

    bres = st.session_state.get("basin_result")
    if bres is not None:
        m = bres["metrics"]
        st.success(f"Bacia: **{m['A (km2)']} km²**, L = {m['L canal (km)']} km")

    posto = st.session_state.get("posto_descricao")
    if posto:
        st.success(f"Posto: **{posto}** ({st.session_state.posto_estado})")
    else:
        st.info("Selecione um posto na Página 1.")

    if st.session_state.get("idf_params"):
        st.caption("IDF carregada ✓")
    if st.session_state.get("hietograma") is not None:
        st.caption("Hietograma calculado ✓")
    if st.session_state.get("hidrograma") is not None:
        metodo = st.session_state.get("metodo_chuva_vazao", "?")
        st.caption(f"Hidrograma calculado ({metodo}) ✓")
    if st.session_state.get("dimensionamento") is not None:
        st.caption("Hidráulica dimensionada ✓")
    if st.session_state.get("detencao") is not None:
        st.caption("Detenção roteada ✓")

    st.divider()
    st.caption(
        "[IDF-generator](https://idf-generator.streamlit.app) — app irmã para "
        "ajuste estatístico de IDF a partir de dados ANA."
    )

pg.run()

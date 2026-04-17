"""
Figuras Plotly para o chuva_vazao.

Funcoes puras — retornam `go.Figure`. Sem dependencia de Streamlit.
Padrao visual herdado do IDF-generator (paleta, template, hover).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go

from chuva_vazao.idf import IDFParams


_COLORS = [
    "#1f77b4", "#ff7f0e", "#2ca02c", "#d62728",
    "#9467bd", "#8c564b", "#e377c2", "#7f7f7f",
]


# ---------------------------------------------------------------------------
# IDF
# ---------------------------------------------------------------------------

def plot_idf_curves(idf_table: pd.DataFrame, titulo: str = "Curvas IDF") -> go.Figure:
    """Curvas IDF: intensidade vs duracao para cada TR."""
    fig = go.Figure()
    for i, tr in enumerate(idf_table.columns):
        fig.add_trace(go.Scatter(
            x=idf_table.index,
            y=idf_table[tr],
            mode="lines+markers",
            name=f"TR = {tr} anos",
            line=dict(color=_COLORS[i % len(_COLORS)], shape="spline"),
            marker=dict(size=6),
            hovertemplate="t=%{x} min<br>i=%{y:.1f} mm/h<extra></extra>",
        ))

    fig.update_layout(
        title=titulo,
        xaxis_title="Duracao (min)",
        yaxis_title="Intensidade (mm/h)",
        template="plotly_white",
        height=500,
        legend=dict(title="Tempo de Retorno"),
    )
    fig.update_xaxes(type="log")
    fig.update_yaxes(type="log")
    return fig


def plot_idf_params(params: IDFParams) -> go.Figure:
    """Renderiza a equacao IDF como 'card' informativo."""
    fig = go.Figure()
    fig.add_annotation(
        text=(
            f"<b>i = K · TR<sup>a</sup> / (t + c)<sup>b</sup></b><br><br>"
            f"K = {params.K:.3f}<br>"
            f"a = {params.expoente_tr:.4f}<br>"
            f"b = {params.expoente_duracao:.4f}<br>"
            f"c = {params.constante_duracao:.2f} min"
        ),
        xref="paper", yref="paper",
        x=0.5, y=0.5,
        showarrow=False,
        font=dict(size=16, family="monospace"),
        bgcolor="#f0f4fa",
        bordercolor="#1f77b4",
        borderwidth=1,
    )
    fig.update_layout(
        template="plotly_white",
        height=250,
        xaxis=dict(visible=False),
        yaxis=dict(visible=False),
        margin=dict(l=20, r=20, t=30, b=20),
    )
    return fig


# ---------------------------------------------------------------------------
# Hietograma
# ---------------------------------------------------------------------------

def plot_hietograma(
    hietograma: pd.Series,
    titulo: str = "Hietograma de Projeto",
) -> go.Figure:
    """Barras de altura (mm) por intervalo de tempo."""
    dt_min = float(hietograma.index[1] - hietograma.index[0]) if len(hietograma) > 1 else 1.0

    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=hietograma.index - dt_min / 2.0,
        y=hietograma.values,
        width=dt_min * 0.9,
        marker_color=_COLORS[0],
        hovertemplate="t=%{x:.0f} min<br>h=%{y:.2f} mm<extra></extra>",
        name="Altura",
    ))

    fig.update_layout(
        title=f"{titulo} (total = {hietograma.sum():.1f} mm)",
        xaxis_title="Tempo (min)",
        yaxis_title=f"Altura de chuva no intervalo (mm / {dt_min:.0f} min)",
        template="plotly_white",
        height=450,
        showlegend=False,
    )
    return fig


def plot_hietograma_comparacao(
    hietogramas: dict[str, pd.Series],
    titulo: str = "Comparacao de Hietogramas",
) -> go.Figure:
    """Compara varios metodos de hietograma sobre o mesmo eixo."""
    fig = go.Figure()
    for i, (nome, serie) in enumerate(hietogramas.items()):
        fig.add_trace(go.Scatter(
            x=serie.index,
            y=serie.values,
            mode="lines+markers",
            name=nome,
            line=dict(color=_COLORS[i % len(_COLORS)], shape="spline"),
        ))

    fig.update_layout(
        title=titulo,
        xaxis_title="Tempo (min)",
        yaxis_title="Altura (mm)",
        template="plotly_white",
        height=450,
    )
    return fig


# ---------------------------------------------------------------------------
# Hidrograma
# ---------------------------------------------------------------------------

def plot_hidrograma(
    hidrograma_df: pd.DataFrame,
    titulo: str = "Hidrograma de Projeto",
) -> go.Figure:
    """
    Plot duplo: hietograma (barras superiores invertidas) + hidrograma (linha).

    Assume df com colunas: hietograma_mm, excedente_mm, Q_m3s.
    """
    fig = go.Figure()

    # Hidrograma Q(t)
    fig.add_trace(go.Scatter(
        x=hidrograma_df.index,
        y=hidrograma_df["Q_m3s"],
        mode="lines",
        name="Q (m³/s)",
        line=dict(color=_COLORS[0], width=2),
        hovertemplate="t=%{x:.0f} min<br>Q=%{y:.2f} m³/s<extra></extra>",
        fill="tozeroy",
        fillcolor="rgba(31, 119, 180, 0.15)",
    ))

    q_max = float(hidrograma_df["Q_m3s"].max())
    t_max = float(hidrograma_df["Q_m3s"].idxmax())
    fig.add_trace(go.Scatter(
        x=[t_max],
        y=[q_max],
        mode="markers+text",
        marker=dict(color=_COLORS[3], size=10),
        text=[f"Q_pico = {q_max:.1f} m³/s"],
        textposition="top center",
        showlegend=False,
    ))

    fig.update_layout(
        title=titulo,
        xaxis_title="Tempo (min)",
        yaxis_title="Vazao Q (m³/s)",
        template="plotly_white",
        height=450,
    )
    return fig


def plot_hietograma_hidrograma(
    hidrograma_df: pd.DataFrame,
    titulo: str = "Hietograma + Hidrograma",
) -> go.Figure:
    """Figura dual: hietograma no topo (invertido), hidrograma embaixo."""
    from plotly.subplots import make_subplots

    dt_min = float(hidrograma_df.index[1] - hidrograma_df.index[0]) if len(hidrograma_df) > 1 else 1.0

    fig = make_subplots(
        rows=2, cols=1,
        shared_xaxes=True,
        vertical_spacing=0.03,
        row_heights=[0.3, 0.7],
    )

    fig.add_trace(
        go.Bar(
            x=hidrograma_df.index - dt_min / 2,
            y=hidrograma_df["hietograma_mm"],
            width=dt_min * 0.9,
            marker_color=_COLORS[0],
            name="Chuva (mm)",
            hovertemplate="t=%{x:.0f} min<br>h=%{y:.2f} mm<extra></extra>",
        ),
        row=1, col=1,
    )
    fig.add_trace(
        go.Bar(
            x=hidrograma_df.index - dt_min / 2,
            y=hidrograma_df["excedente_mm"],
            width=dt_min * 0.9,
            marker_color=_COLORS[1],
            name="Excedente (mm)",
            opacity=0.7,
            hovertemplate="t=%{x:.0f} min<br>h=%{y:.2f} mm<extra></extra>",
        ),
        row=1, col=1,
    )
    fig.add_trace(
        go.Scatter(
            x=hidrograma_df.index,
            y=hidrograma_df["Q_m3s"],
            mode="lines",
            name="Q (m³/s)",
            line=dict(color=_COLORS[3], width=2),
            fill="tozeroy",
            fillcolor="rgba(214, 39, 40, 0.15)",
            hovertemplate="t=%{x:.0f} min<br>Q=%{y:.2f} m³/s<extra></extra>",
        ),
        row=2, col=1,
    )

    fig.update_yaxes(title_text="Chuva (mm)", row=1, col=1, autorange="reversed")
    fig.update_yaxes(title_text="Vazao Q (m³/s)", row=2, col=1)
    fig.update_xaxes(title_text="Tempo (min)", row=2, col=1)
    fig.update_layout(
        title=titulo,
        template="plotly_white",
        height=600,
        barmode="overlay",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )
    return fig


# ---------------------------------------------------------------------------
# Cobertura geografica
# ---------------------------------------------------------------------------

def plot_cobertura_estados(contagem: pd.DataFrame) -> go.Figure:
    """Barras horizontal de numero de postos por UF."""
    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=contagem["n"],
        y=contagem["estado"],
        orientation="h",
        marker_color=_COLORS[2],
    ))
    fig.update_layout(
        title="Cobertura HidroFlu por Estado",
        xaxis_title="Numero de postos",
        yaxis_title="UF",
        template="plotly_white",
        height=max(300, 20 * len(contagem)),
        yaxis=dict(categoryorder="total ascending"),
    )
    return fig

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

# ---------------------------------------------------------------------------
# Conduto fechado: secao transversal e curva caracteristica
# ---------------------------------------------------------------------------

def plot_conduto_circular(
    D_m: float,
    h_op_m: float,
    h_max_m: float,
    v_m_s: float,
    Fr: float | None = None,
    titulo: str = "Seção circular",
) -> go.Figure:
    """Desenho em escala da seção transversal circular com lâmina hachurada."""
    R = D_m / 2.0
    fig = go.Figure()

    # Contorno do conduto
    theta = np.linspace(0, 2 * np.pi, 200)
    x_cont = R * np.sin(theta)
    y_cont = R - R * np.cos(theta)
    fig.add_trace(go.Scatter(
        x=x_cont, y=y_cont,
        mode="lines",
        line=dict(color="#444", width=2),
        name="Conduto",
        hoverinfo="skip",
    ))

    # Poligono da agua (arco inferior fechado pela horizontal em h_op)
    if 0 < h_op_m < D_m:
        t1 = np.arccos((R - h_op_m) / R)  # angulo da intersecao com h_op (lado direito)
        t = np.linspace(-t1, t1, 80)
        x_water = R * np.sin(t)
        y_water = R - R * np.cos(t)
        x_poly = np.concatenate([x_water, [x_water[-1], x_water[0], x_water[0]]])
        y_poly = np.concatenate([y_water, [h_op_m, h_op_m, y_water[0]]])
        fig.add_trace(go.Scatter(
            x=x_poly, y=y_poly,
            fill="toself",
            fillcolor="rgba(31, 119, 180, 0.35)",
            line=dict(color="#1f77b4", width=1),
            mode="lines",
            name=f"Lâmina ({h_op_m * 100:.0f} cm)",
            hoverinfo="skip",
        ))
    elif h_op_m >= D_m:
        # Tubo cheio
        x_poly = np.concatenate([x_cont, [x_cont[0]]])
        y_poly = np.concatenate([y_cont, [y_cont[0]]])
        fig.add_trace(go.Scatter(
            x=x_poly, y=y_poly,
            fill="toself",
            fillcolor="rgba(31, 119, 180, 0.35)",
            line=dict(color="#1f77b4", width=1),
            mode="lines",
            name="Lâmina (cheio)",
            hoverinfo="skip",
        ))

    # Linha tracejada na lamina max permitida
    fig.add_hline(
        y=h_max_m,
        line_dash="dash",
        line_color="#d62728",
        annotation_text=f"h_máx = {h_max_m * 100:.0f} cm",
        annotation_position="top right",
    )

    # Card com info
    info = (
        f"<b>D = {D_m * 1000:.0f} mm</b><br>"
        f"h_op = {h_op_m * 100:.1f} cm ({h_op_m / D_m * 100:.0f} % de D)<br>"
        f"v = {v_m_s:.2f} m/s"
    )
    if Fr is not None:
        info += f"<br>Fr = {Fr:.2f}"
    fig.add_annotation(
        text=info,
        xref="paper", yref="paper",
        x=0.02, y=0.98,
        showarrow=False, align="left",
        font=dict(size=11, family="monospace"),
        bgcolor="rgba(255,255,255,0.85)",
        bordercolor="#999", borderwidth=1,
    )

    fig.update_layout(
        title=titulo,
        xaxis_title="x (m)",
        yaxis_title="y (m)",
        template="plotly_white",
        height=400,
        showlegend=True,
    )
    fig.update_yaxes(scaleanchor="x", scaleratio=1)
    return fig


def plot_conduto_retangular(
    b_m: float,
    h_total_m: float,
    h_op_m: float,
    h_max_m: float,
    v_m_s: float,
    Fr: float | None = None,
    titulo: str = "Seção retangular",
) -> go.Figure:
    """Seção transversal retangular (box culvert) em escala com lâmina."""
    fig = go.Figure()

    # Contorno
    fig.add_trace(go.Scatter(
        x=[0, b_m, b_m, 0, 0],
        y=[0, 0, h_total_m, h_total_m, 0],
        mode="lines",
        line=dict(color="#444", width=2),
        name="Conduto",
        hoverinfo="skip",
    ))

    # Lamina
    if h_op_m > 0:
        h_plot = min(h_op_m, h_total_m)
        fig.add_trace(go.Scatter(
            x=[0, b_m, b_m, 0, 0],
            y=[0, 0, h_plot, h_plot, 0],
            fill="toself",
            fillcolor="rgba(31, 119, 180, 0.35)",
            line=dict(color="#1f77b4", width=1),
            mode="lines",
            name=f"Lâmina ({h_op_m * 100:.0f} cm)",
            hoverinfo="skip",
        ))

    fig.add_hline(
        y=h_max_m,
        line_dash="dash",
        line_color="#d62728",
        annotation_text=f"h_máx = {h_max_m * 100:.0f} cm",
        annotation_position="top right",
    )

    info = (
        f"<b>{b_m:.2f} × {h_total_m:.2f} m</b><br>"
        f"h_op = {h_op_m * 100:.1f} cm ({h_op_m / h_total_m * 100:.0f} % de H)<br>"
        f"v = {v_m_s:.2f} m/s"
    )
    if Fr is not None:
        info += f"<br>Fr = {Fr:.2f}"
    fig.add_annotation(
        text=info,
        xref="paper", yref="paper",
        x=0.02, y=0.98,
        showarrow=False, align="left",
        font=dict(size=11, family="monospace"),
        bgcolor="rgba(255,255,255,0.85)",
        bordercolor="#999", borderwidth=1,
    )

    fig.update_layout(
        title=titulo,
        xaxis_title="x (m)",
        yaxis_title="y (m)",
        template="plotly_white",
        height=400,
    )
    fig.update_yaxes(scaleanchor="x", scaleratio=1)
    return fig


def plot_curva_caracteristica_circular(
    D_m: float, S_m_per_m: float, n: float,
    h_op_m: float, h_max_m: float, Q_op_m3_s: float,
) -> go.Figure:
    """
    Curva caracteristica Q × h do conduto circular (Manning), com ponto de
    operação destacado e faixa h > h_max sombreada.
    """
    from chuva_vazao.hidraulica import manning_circular_partial

    h = np.linspace(0.01 * D_m, 0.99 * D_m, 80)
    Q = np.array([
        manning_circular_partial(D_m, float(hi), S_m_per_m, n).Q_m3_s for hi in h
    ])

    fig = go.Figure()

    fig.add_trace(go.Scatter(
        x=Q, y=h,
        mode="lines",
        line=dict(color="#1f77b4", width=2),
        name="Q × h (Manning)",
        hovertemplate="Q=%{x:.3f} m³/s<br>h=%{y:.2f} m<extra></extra>",
    ))

    # Faixa de operacao acima da lamina permitida
    Q_max = max(Q.max(), Q_op_m3_s) * 1.05
    fig.add_shape(
        type="rect",
        x0=0, x1=Q_max,
        y0=h_max_m, y1=D_m,
        fillcolor="rgba(214, 39, 40, 0.10)",
        line_width=0,
        layer="below",
    )

    # Ponto de operacao
    fig.add_trace(go.Scatter(
        x=[Q_op_m3_s], y=[h_op_m],
        mode="markers+text",
        marker=dict(color="#d62728", size=12, symbol="x"),
        text=[f" Q_op = {Q_op_m3_s:.2f} m³/s"],
        textposition="middle right",
        name="Operação",
        hovertemplate="Q_op=%{x:.3f} m³/s<br>h_op=%{y:.2f} m<extra></extra>",
    ))

    fig.add_hline(
        y=h_max_m, line_dash="dash", line_color="#d62728",
        annotation_text=f"h_máx = {h_max_m * 100:.0f} cm",
        annotation_position="top right",
    )

    fig.update_layout(
        title=f"Curva característica — D = {D_m * 1000:.0f} mm, S = {S_m_per_m * 100:.3f} %",
        xaxis_title="Q (m³/s)",
        yaxis_title="h (m)",
        template="plotly_white",
        height=400,
    )
    return fig


def plot_curva_caracteristica_retangular(
    b_m: float, h_total_m: float, S_m_per_m: float, n: float,
    h_op_m: float, h_max_m: float, Q_op_m3_s: float,
) -> go.Figure:
    """Curva caracteristica Q × h da secao retangular."""
    from chuva_vazao.hidraulica import manning_rectangular

    h = np.linspace(0.01 * h_total_m, 0.99 * h_total_m, 80)
    Q = np.array([
        manning_rectangular(b_m, float(hi), S_m_per_m, n).Q_m3_s for hi in h
    ])

    fig = go.Figure()

    fig.add_trace(go.Scatter(
        x=Q, y=h,
        mode="lines",
        line=dict(color="#1f77b4", width=2),
        name="Q × h (Manning)",
        hovertemplate="Q=%{x:.3f} m³/s<br>h=%{y:.2f} m<extra></extra>",
    ))

    Q_max = max(Q.max(), Q_op_m3_s) * 1.05
    fig.add_shape(
        type="rect",
        x0=0, x1=Q_max,
        y0=h_max_m, y1=h_total_m,
        fillcolor="rgba(214, 39, 40, 0.10)",
        line_width=0,
        layer="below",
    )

    fig.add_trace(go.Scatter(
        x=[Q_op_m3_s], y=[h_op_m],
        mode="markers+text",
        marker=dict(color="#d62728", size=12, symbol="x"),
        text=[f" Q_op = {Q_op_m3_s:.2f} m³/s"],
        textposition="middle right",
        name="Operação",
    ))

    fig.add_hline(
        y=h_max_m, line_dash="dash", line_color="#d62728",
        annotation_text=f"h_máx = {h_max_m * 100:.0f} cm",
        annotation_position="top right",
    )

    fig.update_layout(
        title=f"Curva característica — {b_m:.2f}×{h_total_m:.2f} m, S = {S_m_per_m * 100:.3f} %",
        xaxis_title="Q (m³/s)",
        yaxis_title="h (m)",
        template="plotly_white",
        height=400,
    )
    return fig


# ---------------------------------------------------------------------------
# Reservatorio de detencao: graficos adicionais
# ---------------------------------------------------------------------------

def plot_cota_volume_descarga(reservatorio) -> go.Figure:
    """
    Caracteristicas do reservatorio: volume e vazao de saida em funcao de h.

    Eixo y esquerdo: V(h) (m³). Eixo y direito: Q_saida(h) (m³/s) decomposta
    em orificio, vertedor e total.
    """
    from chuva_vazao.detencao import (
        build_storage_discharge_table,
        orificio,
        vertedor_retangular,
    )
    from plotly.subplots import make_subplots

    tabela = build_storage_discharge_table(reservatorio, n_pontos=200)
    h = tabela["h_m"].to_numpy()
    S = tabela["S_m3"].to_numpy()

    Q_orif = np.array([
        orificio(reservatorio.Cd_orificio, reservatorio.A_orificio_m2,
                 max(hi - reservatorio.z_orificio_m, 0.0)) for hi in h
    ])
    Q_vert = np.array([
        vertedor_retangular(reservatorio.Cw_vertedor, reservatorio.b_vertedor_m,
                            max(hi - reservatorio.z_vertedor_m, 0.0)) for hi in h
    ])
    Q_total = Q_orif + Q_vert

    fig = make_subplots(specs=[[{"secondary_y": True}]])

    fig.add_trace(
        go.Scatter(
            x=h, y=S,
            mode="lines",
            line=dict(color="#2ca02c", width=2),
            name="Volume V(h)",
            hovertemplate="h=%{x:.2f} m<br>V=%{y:,.0f} m³<extra></extra>",
        ),
        secondary_y=False,
    )

    fig.add_trace(
        go.Scatter(
            x=h, y=Q_orif,
            mode="lines",
            line=dict(color="#1f77b4", width=2, dash="dot"),
            name="Q_orif",
            hovertemplate="h=%{x:.2f} m<br>Q_orif=%{y:.2f} m³/s<extra></extra>",
        ),
        secondary_y=True,
    )
    fig.add_trace(
        go.Scatter(
            x=h, y=Q_vert,
            mode="lines",
            line=dict(color="#d62728", width=2, dash="dot"),
            name="Q_vert",
            hovertemplate="h=%{x:.2f} m<br>Q_vert=%{y:.2f} m³/s<extra></extra>",
        ),
        secondary_y=True,
    )
    fig.add_trace(
        go.Scatter(
            x=h, y=Q_total,
            mode="lines",
            line=dict(color="#ff7f0e", width=2.5),
            name="Q_total",
            hovertemplate="h=%{x:.2f} m<br>Q_total=%{y:.2f} m³/s<extra></extra>",
        ),
        secondary_y=True,
    )

    # Marcacoes nas cotas dos dispositivos
    fig.add_vline(
        x=reservatorio.z_vertedor_m,
        line_dash="dash", line_color="#d62728",
        annotation_text=f"vertedor @ {reservatorio.z_vertedor_m:.2f} m",
        annotation_position="top left",
    )
    if reservatorio.z_orificio_m > 0:
        fig.add_vline(
            x=reservatorio.z_orificio_m,
            line_dash="dash", line_color="#1f77b4",
            annotation_text=f"orifício @ {reservatorio.z_orificio_m:.2f} m",
            annotation_position="bottom left",
        )

    fig.update_xaxes(title_text="Cota h (m)")
    fig.update_yaxes(title_text="Volume (m³)", secondary_y=False)
    fig.update_yaxes(title_text="Vazão de saída (m³/s)", secondary_y=True)
    fig.update_layout(
        title="Curvas características do reservatório",
        template="plotly_white",
        height=450,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )
    return fig


def plot_descarga_decomposta(resultado, reservatorio) -> go.Figure:
    """
    Decomposicao temporal da vazao de saida em orificio + vertedor (empilhado).

    Mostra exatamente quando o vertedor entrou em acao.
    """
    from chuva_vazao.detencao import orificio, vertedor_retangular

    h = resultado.h_m
    Q_orif = np.array([
        orificio(reservatorio.Cd_orificio, reservatorio.A_orificio_m2,
                 max(hi - reservatorio.z_orificio_m, 0.0)) for hi in h
    ])
    Q_vert = np.array([
        vertedor_retangular(reservatorio.Cw_vertedor, reservatorio.b_vertedor_m,
                            max(hi - reservatorio.z_vertedor_m, 0.0)) for hi in h
    ])

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=resultado.tempo_min, y=Q_orif,
        mode="lines",
        name="Orifício",
        line=dict(width=0.5, color="#1f77b4"),
        stackgroup="saida",
        fillcolor="rgba(31, 119, 180, 0.45)",
        hovertemplate="t=%{x:.0f} min<br>Q_orif=%{y:.2f} m³/s<extra></extra>",
    ))
    fig.add_trace(go.Scatter(
        x=resultado.tempo_min, y=Q_vert,
        mode="lines",
        name="Vertedor",
        line=dict(width=0.5, color="#d62728"),
        stackgroup="saida",
        fillcolor="rgba(214, 39, 40, 0.45)",
        hovertemplate="t=%{x:.0f} min<br>Q_vert=%{y:.2f} m³/s<extra></extra>",
    ))
    fig.add_trace(go.Scatter(
        x=resultado.tempo_min, y=resultado.inflow_m3_s,
        mode="lines",
        name="Afluente",
        line=dict(color="#888", width=1.5, dash="dot"),
        hovertemplate="t=%{x:.0f} min<br>I=%{y:.2f} m³/s<extra></extra>",
    ))

    fig.update_layout(
        title="Decomposição temporal da descarga",
        xaxis_title="Tempo (min)",
        yaxis_title="Vazão (m³/s)",
        template="plotly_white",
        height=400,
        hovermode="x unified",
    )
    return fig


# ---------------------------------------------------------------------------
# Verificacao hidraulica de secao (canal/curso d'agua)
# ---------------------------------------------------------------------------

def plot_secao_transversal(
    secao,                    # SecaoTransversal — evita import ciclico
    esc_normal,               # EscoamentoSecao da lamina normal
    esc_critico=None,         # EscoamentoSecao da lamina critica (opcional)
    titulo: str = "Secao transversal",
) -> go.Figure:
    """
    Secao 2D (estaca x cota) com terreno, lamina d'agua hachurada, linha
    critica tracejada e marcadores de thalweg e margens.

    A agua e desenhada como poligono fechado — assume secao sem ilha.
    """
    estacas = list(secao.estacas)
    cotas = list(secao.cotas)
    y_w = esc_normal.y_w_m

    fig = go.Figure()

    # Terreno (linha contínua marrom)
    fig.add_trace(go.Scatter(
        x=estacas, y=cotas,
        mode="lines+markers",
        name="Terreno",
        line=dict(color="#6b4226", width=2),
        marker=dict(size=6, color="#6b4226"),
        hovertemplate="estaca=%{x:.2f} m<br>cota=%{y:.3f} m<extra></extra>",
    ))

    # Poligono da agua — caminha pelo terreno colocando vertices,
    # interpola entradas/saidas em z = y_w
    poly_x: list[float] = []
    poly_y: list[float] = []
    n = len(estacas)
    for i in range(n - 1):
        s0, z0 = estacas[i], cotas[i]
        s1, z1 = estacas[i + 1], cotas[i + 1]
        h0 = y_w - z0
        h1 = y_w - z1

        # Inicio do segmento: incluir ponto i se submerso, ou interpolar se sai/entra
        if h0 > 0:
            if not poly_x or poly_x[-1] != s0 or poly_y[-1] != z0:
                poly_x.append(s0)
                poly_y.append(z0)

        # Cruzou y_w no segmento? interpola
        if (h0 > 0) != (h1 > 0):
            if z1 != z0:
                t_star = (y_w - z0) / (z1 - z0)
                t_star = max(0.0, min(1.0, t_star))
                s_star = s0 + t_star * (s1 - s0)
                poly_x.append(s_star)
                poly_y.append(y_w)

        # Fim do segmento: ponto i+1 se submerso
        if h1 > 0:
            poly_x.append(s1)
            poly_y.append(z1)

    if poly_x:
        # Fecha o polígono pelo topo (horizontal em y_w, da direita pra esquerda)
        poly_x_fechado = poly_x + [poly_x[-1], poly_x[0], poly_x[0]]
        poly_y_fechado = poly_y + [y_w, y_w, poly_y[0]]
        fig.add_trace(go.Scatter(
            x=poly_x_fechado, y=poly_y_fechado,
            fill="toself",
            fillcolor="rgba(31, 119, 180, 0.30)",
            line=dict(color="#1f77b4", width=1),
            mode="lines",
            name=f"Lâmina (y_n = {esc_normal.y_lamina_m:.2f} m)",
            hoverinfo="skip",
        ))

    # Linha critica (opcional)
    if esc_critico is not None:
        fig.add_hline(
            y=esc_critico.y_w_m,
            line_dash="dash",
            line_color="#d62728",
            annotation_text=f"y_c = {esc_critico.y_lamina_m:.2f} m",
            annotation_position="top right",
        )

    # Marcador no thalweg
    fig.add_trace(go.Scatter(
        x=[secao.estacas[secao.cotas.index(secao.z_thalweg)]],
        y=[secao.z_thalweg],
        mode="markers",
        marker=dict(symbol="triangle-up", size=12, color="#2ca02c"),
        name=f"Thalweg ({secao.z_thalweg:.2f} m)",
        hoverinfo="skip",
    ))

    # Anotacao de regime e velocidade
    info = (
        f"<b>Q = {esc_normal.Q_m3_s:.2f} m³/s</b><br>"
        f"v = {esc_normal.v_m_s:.2f} m/s<br>"
        f"Fr = {esc_normal.Fr:.2f} ({esc_normal.regime})"
    )
    fig.add_annotation(
        text=info,
        xref="paper", yref="paper",
        x=0.02, y=0.98,
        showarrow=False,
        align="left",
        font=dict(size=12, family="monospace"),
        bgcolor="rgba(255,255,255,0.85)",
        bordercolor="#999",
        borderwidth=1,
    )

    fig.update_layout(
        title=titulo,
        xaxis_title="Estaca local (m)",
        yaxis_title="Cota (m)",
        template="plotly_white",
        height=420,
        hovermode="closest",
        showlegend=True,
    )
    return fig


def plot_perfil_longitudinal(verificacao) -> go.Figure:
    """
    Perfil longitudinal do trecho M -> C -> J: cota_thalweg do leito e
    cota da linha d'agua nas tres secoes. Estaca acumulada no trecho.
    """
    L_MC = verificacao.L_MC_m
    L_CJ = verificacao.L_CJ_m
    estacas_trecho = [0.0, L_MC, L_MC + L_CJ]

    z_thalweg = [
        verificacao.secao_montante.z_thalweg,
        verificacao.secao_central.z_thalweg,
        verificacao.secao_jusante.z_thalweg,
    ]
    z_agua = [
        verificacao.escoamento_M.y_w_m,
        verificacao.escoamento_C.y_w_m,
        verificacao.escoamento_J.y_w_m,
    ]
    z_critico = [
        verificacao.critica_M.y_w_m,
        verificacao.critica_C.y_w_m,
        verificacao.critica_J.y_w_m,
    ]
    z_topo = [
        verificacao.secao_montante.z_max,
        verificacao.secao_central.z_max,
        verificacao.secao_jusante.z_max,
    ]

    fig = go.Figure()

    # Margem (cota maxima — capacidade ate extravasar)
    fig.add_trace(go.Scatter(
        x=estacas_trecho, y=z_topo,
        mode="lines+markers",
        name="Margem (z_max)",
        line=dict(color="#999", width=1, dash="dot"),
        marker=dict(size=6),
        hovertemplate="L=%{x:.0f} m<br>z_topo=%{y:.2f} m<extra></extra>",
    ))

    # Linha d'agua — preenche entre thalweg e linha d'agua
    fig.add_trace(go.Scatter(
        x=estacas_trecho, y=z_agua,
        mode="lines+markers",
        name="Linha d'água",
        line=dict(color="#1f77b4", width=2),
        marker=dict(size=8),
        hovertemplate="L=%{x:.0f} m<br>y_w=%{y:.2f} m<extra></extra>",
        fill="tonexty",
        fillcolor="rgba(31, 119, 180, 0.20)",
    ))

    # Thalweg (leito) — preenchimento ate aqui pra dar a sensacao de leito
    fig.add_trace(go.Scatter(
        x=estacas_trecho, y=z_thalweg,
        mode="lines+markers",
        name="Thalweg (leito)",
        line=dict(color="#6b4226", width=2),
        marker=dict(size=8, symbol="triangle-up"),
        hovertemplate="L=%{x:.0f} m<br>z_thalweg=%{y:.2f} m<extra></extra>",
    ))

    # Linha critica
    fig.add_trace(go.Scatter(
        x=estacas_trecho, y=z_critico,
        mode="lines+markers",
        name="Linha crítica (Fr=1)",
        line=dict(color="#d62728", width=1, dash="dash"),
        marker=dict(size=6),
        hovertemplate="L=%{x:.0f} m<br>y_c=%{y:.2f} m<extra></extra>",
    ))

    # Anotacoes nas tres secoes
    for x, label in zip(estacas_trecho, ["Montante", "Central", "Jusante"]):
        fig.add_vline(x=x, line_color="#bbb", line_width=1, line_dash="dot")
        fig.add_annotation(
            x=x, y=max(z_topo) + 0.05 * (max(z_topo) - min(z_thalweg)),
            text=label,
            showarrow=False,
            font=dict(size=11, color="#555"),
        )

    fig.update_layout(
        title=(
            f"Perfil longitudinal — S = {verificacao.S_trecho_m_per_m * 100:.3f} %, "
            f"L = {verificacao.L_total_m:.0f} m, Q = {verificacao.Q_projeto_m3_s:.2f} m³/s"
        ),
        xaxis_title="Estaca acumulada do trecho (m)",
        yaxis_title="Cota (m)",
        template="plotly_white",
        height=450,
        hovermode="x unified",
    )
    return fig


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

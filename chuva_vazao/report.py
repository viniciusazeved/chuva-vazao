"""
Gerador de relatorio PDF tecnico para projeto de drenagem via chuva_vazao.

Um PDF auto-explicativo consolida:
    1. Dados do posto (descricao, UF, fonte)
    2. Equacao IDF + curva
    3. Hietograma de projeto (metodo, TR, duracao, dt)
    4. Parametros da bacia (area, t_c, CN)
    5. Hidrograma resultante (Q_pico, volume, t_pico)
    6. Metodologia + referencias

Usa fpdf2 + kaleido (via plotly.to_image). Herda o estilo do IDF-generator.
"""
from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
from fpdf import FPDF

from chuva_vazao.hidrograma import (
    Q_pico_m3s,
    SCSParams,
    tempo_ao_pico_min,
    volume_escoado_m3,
)
from chuva_vazao.idf import IDFParams


@dataclass
class RelatorioInputs:
    """Consolida tudo que o relatorio precisa (evita argumentos gigantes)."""
    posto_descricao: str
    posto_estado: str
    posto_fonte: str
    idf_params: IDFParams
    idf_table: pd.DataFrame
    TR_anos: float
    duracao_min: float
    dt_min: float
    metodo_hietograma: str
    hietograma: pd.Series
    scs_params: SCSParams
    hidrograma: pd.DataFrame
    fig_idf: go.Figure
    fig_hietograma: go.Figure
    fig_hidrograma: go.Figure

    # Secoes opcionais (viram paginas condicionais no PDF se preenchidas)
    basin_metrics: object | None = None          # BasinMetrics
    basin_outlet_original: tuple[float, float] | None = None  # (lat, lon)
    basin_outlet_snapped: tuple[float, float] | None = None
    tc_breakdown: dict[str, float] | None = None  # Kirpich/Chow/California/Media
    metodo_chuva_vazao: str | None = None         # "Racional" ou "SCS-HU"
    C_racional: float | None = None
    uso_solo_racional: str | None = None
    dimensionamento: dict | None = None           # output da pagina 4
    detencao: object | None = None                # RoteamentoResult
    fig_detencao: go.Figure | None = None


class RelatorioPDF(FPDF):
    def __init__(self, titulo: str):
        super().__init__(orientation="P", unit="mm", format="A4")
        self.titulo = titulo
        self.set_auto_page_break(auto=True, margin=20)

    def header(self):
        self.set_font("Helvetica", "B", 9)
        self.set_text_color(120, 120, 120)
        self.cell(0, 5, "chuva_vazao - Relatorio de Drenagem", align="L")
        self.cell(
            0, 5, f"Pagina {self.page_no()}/{{nb}}",
            align="R", new_x="LMARGIN", new_y="NEXT",
        )
        self.set_draw_color(200, 200, 200)
        self.line(10, self.get_y(), 200, self.get_y())
        self.ln(4)
        self.set_text_color(0, 0, 0)

    def footer(self):
        self.set_y(-12)
        self.set_font("Helvetica", "I", 7)
        self.set_text_color(150, 150, 150)
        self.cell(0, 5, "Gerado por chuva_vazao (base: HidroFlu UFRJ/COPPE)", align="C")
        self.set_text_color(0, 0, 0)

    def add_title(self, text: str):
        self.set_font("Helvetica", "B", 16)
        self.cell(0, 12, text, new_x="LMARGIN", new_y="NEXT")
        self.ln(2)

    def add_section(self, text: str):
        self.set_font("Helvetica", "B", 12)
        self.set_text_color(30, 70, 130)
        self.cell(0, 9, text, new_x="LMARGIN", new_y="NEXT")
        self.set_text_color(0, 0, 0)
        self.ln(1)

    def add_subsection(self, text: str):
        self.set_font("Helvetica", "B", 10)
        self.set_text_color(60, 60, 60)
        self.cell(0, 7, text, new_x="LMARGIN", new_y="NEXT")
        self.set_text_color(0, 0, 0)
        self.ln(1)

    def add_text(self, text: str, size: int = 9):
        self.set_font("Helvetica", "", size)
        self.multi_cell(0, 5, text)
        self.ln(2)

    def add_param(self, name: str, value: str, description: str = ""):
        self.set_font("Helvetica", "B", 9)
        self.cell(55, 5, name)
        self.set_font("Helvetica", "", 9)
        self.cell(40, 5, value)
        if description:
            self.set_font("Helvetica", "I", 8)
            self.set_text_color(100, 100, 100)
            self.cell(0, 5, description)
            self.set_text_color(0, 0, 0)
        self.ln(5)

    def add_figure(self, fig: go.Figure, width_mm: int = 180, height_mm: int = 100):
        """Exporta Plotly -> PNG -> embed no PDF."""
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp_path = Path(tmp.name)
        try:
            fig.write_image(str(tmp_path), width=900, height=500, scale=2)
            x = (210 - width_mm) / 2
            self.image(str(tmp_path), x=x, w=width_mm, h=height_mm)
            self.ln(4)
        finally:
            try:
                tmp_path.unlink()
            except OSError:
                pass

    def add_dataframe(self, df: pd.DataFrame, col_widths: list[int] | None = None):
        cols = list(df.columns)
        n_cols = len(cols)
        if col_widths is None:
            col_widths = [190 // n_cols] * n_cols

        self.set_font("Helvetica", "B", 8)
        self.set_fill_color(230, 235, 245)
        for i, col in enumerate(cols):
            self.cell(col_widths[i], 6, str(col), border=1, fill=True, align="C")
        self.ln()

        self.set_font("Helvetica", "", 7)
        for _, row in df.iterrows():
            for i, col in enumerate(cols):
                val = row[col]
                text = f"{val:.2f}" if isinstance(val, float) else str(val)
                self.cell(col_widths[i], 5, text, border=1, align="C")
            self.ln()
        self.ln(3)


def gerar_relatorio_pdf(inputs: RelatorioInputs) -> bytes:
    """Monta o PDF tecnico e retorna bytes prontos pra download."""
    pdf = RelatorioPDF(titulo=inputs.posto_descricao)
    pdf.alias_nb_pages()

    # ===== CAPA =====
    pdf.add_page()
    pdf.ln(15)
    pdf.set_font("Helvetica", "B", 22)
    pdf.cell(0, 12, "Relatorio Tecnico", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(
        0, 9, "Pipeline Chuva-Vazao para Drenagem",
        align="C", new_x="LMARGIN", new_y="NEXT",
    )
    pdf.ln(10)
    pdf.set_font("Helvetica", "", 12)
    pdf.cell(
        0, 8, f"Posto: {inputs.posto_descricao} - {inputs.posto_estado}",
        align="C", new_x="LMARGIN", new_y="NEXT",
    )
    pdf.cell(
        0, 7, f"Fonte: {inputs.posto_fonte}",
        align="C", new_x="LMARGIN", new_y="NEXT",
    )
    pdf.ln(8)
    pdf.set_draw_color(30, 70, 130)
    pdf.set_line_width(0.5)
    pdf.line(60, pdf.get_y(), 150, pdf.get_y())
    pdf.ln(10)
    pdf.set_font("Helvetica", "", 10)
    pdf.cell(0, 6, f"TR adotado: {inputs.TR_anos:g} anos", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(
        0, 6, f"Duracao do evento: {inputs.duracao_min:g} min (passo {inputs.dt_min:g} min)",
        align="C", new_x="LMARGIN", new_y="NEXT",
    )
    pdf.cell(
        0, 6,
        f"Bacia: {inputs.scs_params.area_km2:g} km^2, tc = {inputs.scs_params.tempo_concentracao_h:g} h, CN = {inputs.scs_params.CN:g}",
        align="C", new_x="LMARGIN", new_y="NEXT",
    )

    # ===== 1. POSTO =====
    pdf.add_page()
    pdf.add_section("1. Posto Pluviometrico")
    pdf.add_text(
        "Coeficientes de chuva intensa extraidos do banco do HidroFlu v2.0 "
        "(UFRJ/COPPE, 2007), que consolida ajustes regionais das publicacoes "
        "classicas brasileiras de desagregacao de chuvas (Pfafstetter 1957, "
        "Denardin & Freitas 1982, Silva et al. 2002, entre outras)."
    )
    pdf.add_param("Descricao:", inputs.posto_descricao)
    pdf.add_param("Estado:", inputs.posto_estado)
    pdf.add_param("Fonte:", inputs.posto_fonte)

    # ===== 2. IDF =====
    pdf.add_section("2. Equacao IDF")
    pdf.add_text(
        "Equacao Intensidade-Duracao-Frequencia adotada (convencao HidroFlu):\n\n"
        "    i = K * TR^a / (t + c)^b\n\n"
        "onde:\n"
        "  i = intensidade (mm/h), TR = tempo de retorno (anos),\n"
        "  t = duracao (minutos), K/a/b/c = coeficientes do posto."
    )
    pdf.add_param("K:", f"{inputs.idf_params.K:.3f}")
    pdf.add_param("a (expoente TR):", f"{inputs.idf_params.expoente_tr:.4f}")
    pdf.add_param("b (expoente duracao):", f"{inputs.idf_params.expoente_duracao:.4f}")
    pdf.add_param("c (constante, min):", f"{inputs.idf_params.constante_duracao:.2f}")

    pdf.add_subsection("Curva IDF")
    pdf.add_figure(inputs.fig_idf, height_mm=95)

    pdf.add_subsection("Tabela IDF (mm/h)")
    idf_display = inputs.idf_table.round(2).reset_index()
    idf_display.columns = ["Duracao (min)"] + [f"TR={tr}" for tr in inputs.idf_table.columns]
    n = len(idf_display.columns)
    w = [25] + [int(165 / (n - 1))] * (n - 1)
    pdf.add_dataframe(idf_display, col_widths=w)

    # ===== 3. HIETOGRAMA =====
    pdf.add_page()
    pdf.add_section("3. Hietograma de Projeto")
    pdf.add_text(
        f"Metodo: {inputs.metodo_hietograma}.\n"
        f"TR = {inputs.TR_anos:g} anos, duracao total = {inputs.duracao_min:g} min, "
        f"passo dt = {inputs.dt_min:g} min, altura total = {inputs.hietograma.sum():.2f} mm."
    )
    pdf.add_figure(inputs.fig_hietograma, height_mm=90)

    # ===== 4. BACIA =====
    pdf.add_section("4. Parametros da Bacia e SCS")
    pdf.add_param("Area:", f"{inputs.scs_params.area_km2:.3f} km^2")
    pdf.add_param("Tempo de concentracao:", f"{inputs.scs_params.tempo_concentracao_h:.3f} h")
    pdf.add_param("CN:", f"{inputs.scs_params.CN:.1f}")
    pdf.add_param("S (retencao):", f"{inputs.scs_params.S_mm:.2f} mm")
    pdf.add_param("Ia (abstracao inicial):", f"{inputs.scs_params.Ia_mm:.2f} mm")

    # ===== 5. HIDROGRAMA =====
    pdf.add_section("5. Hidrograma de Projeto")
    pdf.add_text(
        "Hidrograma obtido por convolucao do hietograma excedente (SCS-CN) "
        "com hidrograma unitario triangular SCS (t_pico = D/2 + 0.6*t_c, "
        "t_base = 2.67*t_pico, Q_pico = 0.208 * A / t_pico)."
    )
    pdf.add_param("Q_pico:", f"{Q_pico_m3s(inputs.hidrograma):.2f} m^3/s")
    pdf.add_param("Tempo ao pico:", f"{tempo_ao_pico_min(inputs.hidrograma):.1f} min")
    pdf.add_param("Volume escoado:", f"{volume_escoado_m3(inputs.hidrograma):,.0f} m^3")
    pdf.add_figure(inputs.fig_hidrograma, height_mm=110)

    # ===== 6. METODOLOGIA =====
    pdf.add_page()
    pdf.add_section("6. Metodologia")
    pdf.add_subsection("6.1 IDF")
    pdf.add_text(
        "Os coeficientes K, a, b, c vem do banco HidroFlu. A convencao usada e "
        "b = expoente da duracao, c = constante temporal em minutos. Intensidades "
        "calculadas pela equacao acima e tabuladas para duracoes de 5min a 24h."
    )
    pdf.add_subsection("6.2 Hietograma")
    pdf.add_text(
        "Blocos alternados (Chicago): redistribui alturas incrementais "
        "derivadas da IDF em torno do bloco central (pico). Huff: distribui a "
        "altura total segundo curva adimensional do quartil escolhido."
    )
    pdf.add_subsection("6.3 Escoamento Superficial (SCS-CN)")
    pdf.add_text(
        "Q = (P - Ia)^2 / (P - Ia + S), Ia = 0.2 * S, S = 25400/CN - 254 (mm). "
        "Aplicado cumulativamente sobre o hietograma para gerar o hietograma excedente."
    )
    pdf.add_subsection("6.4 Hidrograma Unitario Triangular SCS")
    pdf.add_text(
        "t_lag = 0.6 * t_c; t_pico = D/2 + t_lag; t_base = 2.67 * t_pico; "
        "Q_pico = 0.208 * A / t_pico [m^3/s/mm]. Convolucao numerica do "
        "hietograma excedente (mm) com a UH (m^3/s/mm) produz Q(t)."
    )

    # ===== 7. BACIA (opcional) =====
    if inputs.basin_metrics is not None:
        pdf.add_page()
        pdf.add_section("7. Bacia de Contribuicao (delineamento automatico)")
        pdf.add_text(
            "Delineamento feito por WhiteboxTools a partir de DEM (Copernicus "
            "GLO-30 ou DEM local). Pipeline: breach depressions -> D8 pointer "
            "-> flow accumulation -> extract streams -> snap pour point -> "
            "watershed -> vetorizacao."
        )
        d = inputs.basin_metrics.summary_dict() if hasattr(inputs.basin_metrics, "summary_dict") else {}
        for label, value in d.items():
            pdf.add_param(f"{label}:", f"{value}")

        if inputs.basin_outlet_original and inputs.basin_outlet_snapped:
            pdf.add_subsection("Exutorio")
            o_lat, o_lon = inputs.basin_outlet_original
            s_lat, s_lon = inputs.basin_outlet_snapped
            pdf.add_param("Original (clicado):", f"({o_lat:.5f}, {o_lon:.5f})")
            pdf.add_param("Snapped (ajustado):", f"({s_lat:.5f}, {s_lon:.5f})")

    # ===== 7B. TEMPO DE CONCENTRACAO (opcional) =====
    if inputs.tc_breakdown is not None:
        pdf.add_subsection("Tempo de Concentracao")
        pdf.add_text(
            "Calculado por tres formulas classicas (aplicaveis a bacias rurais "
            "de pequeno a medio porte). A media aritmetica das tres e o valor "
            "adotado por default."
        )
        for metodo, valor in inputs.tc_breakdown.items():
            pdf.add_param(f"  {metodo}:", f"{valor:.2f} min")

    # ===== 8. METODO CHUVA-VAZAO RACIONAL (opcional) =====
    if inputs.metodo_chuva_vazao == "Racional":
        pdf.add_section("8. Metodo Racional")
        pdf.add_text(
            "Q = C . i(tc) . A / 3.6. Valido para bacias pequenas (A <= 2 km2). "
            "Pressupoe duracao critica = tc e chuva uniforme sobre a bacia."
        )
        if inputs.C_racional is not None:
            pdf.add_param("Coeficiente C:", f"{inputs.C_racional:.2f}")
        if inputs.uso_solo_racional:
            pdf.add_param("Uso do solo:", inputs.uso_solo_racional)

    # ===== 9. DIMENSIONAMENTO HIDRAULICO (opcional) =====
    if inputs.dimensionamento is not None:
        pdf.add_page()
        pdf.add_section("9. Dimensionamento Hidraulico")
        dim = inputs.dimensionamento
        pdf.add_text(
            "Dimensionamento por equacao de Manning "
            "(Q = 1/n . A . R^(2/3) . S^(1/2)). Para secao circular, escolhe "
            "o menor diametro comercial que atenda a vazao de projeto com "
            "lamina maxima configurada."
        )
        pdf.add_param("Tipo:", str(dim.get("tipo", "?")).title())
        pdf.add_param("Material:", str(dim.get("material", "?")))
        pdf.add_param("n de Manning:", f"{dim.get('n', 0):.3f}")
        pdf.add_param("Declividade S:", f"{dim.get('S', 0):.4f} m/m")
        pdf.add_param("Fator de seguranca:", f"{dim.get('fator_seguranca', 1):.2f}")
        pdf.add_param("Q de projeto:", f"{dim.get('Q_projeto_m3_s', 0):.3f} m3/s")

        pdf.add_subsection("Secao adotada")
        if dim.get("tipo") == "circular":
            pdf.add_param("  Diametro:", f"{dim.get('D_adotado_m', 0) * 100:.0f} cm")
        else:
            pdf.add_param("  Largura b:", f"{dim.get('b_m', 0):.2f} m")
            pdf.add_param("  Altura total h:", f"{dim.get('h_total_m', 0):.2f} m")

        pdf.add_subsection("Operacao real (Q projeto, sem fator de seguranca)")
        pdf.add_param("  Lamina de operacao:", f"{dim.get('h_op_m', 0):.3f} m")
        pdf.add_param("  Velocidade:", f"{dim.get('v_op_m_s', 0):.2f} m/s")

        warnings_list = dim.get("warnings") or []
        if warnings_list:
            pdf.add_subsection("Alertas")
            for w in warnings_list:
                pdf.add_text(f"- {w}", size=9)

    # ===== 10. DETENCAO (opcional) =====
    if inputs.detencao is not None:
        pdf.add_page()
        pdf.add_section("10. Reservatorio de Detencao (roteamento Puls)")
        pdf.add_text(
            "Roteamento pelo metodo de Puls modificado em reservatorio "
            "prismatico com orificio de fundo + vertedor retangular de "
            "emergencia. Atenuacao = 1 - Qp_out / Qp_in."
        )
        det = inputs.detencao
        pdf.add_param("Qp afluente:", f"{det.Qp_in_m3_s:.2f} m3/s")
        pdf.add_param("Qp efluente:", f"{det.Qp_out_m3_s:.2f} m3/s")
        pdf.add_param("Atenuacao:", f"{det.atenuacao_pct:.1f} %")
        pdf.add_param("Lamina maxima:", f"{det.h_max_m:.2f} m")
        pdf.add_param("Volume armazenado max:", f"{det.volume_armazenado_max_m3:,.0f} m3")

        if inputs.fig_detencao is not None:
            pdf.add_figure(inputs.fig_detencao, height_mm=100)

    # ===== 11. REFERENCIAS =====
    pdf.add_section("11. Referencias")
    refs = [
        "Pfafstetter, O. (1957). Chuvas Intensas no Brasil. Rio de Janeiro: DNOS.",
        "Denardin, J. E., & Freitas, P. L. (1982). Caracteristicas fundamentais "
        "da chuva no Brasil. Pesquisa Agropecuaria Brasileira, 17(10), 1409-1416.",
        "CETESB. (1980). Drenagem Urbana - Manual de Projeto. Sao Paulo: CETESB.",
        "Weiss, L. (1964). Ratio of true to fixed-interval maximum rainfall. "
        "Journal of the Hydraulic Division, 90(1), 77-82.",
        "NRCS (1972). National Engineering Handbook, Section 4: Hydrology. "
        "U.S. Department of Agriculture.",
        "Chow, V. T., Maidment, D. R., & Mays, L. W. (1988). Applied Hydrology. "
        "New York: McGraw-Hill.",
        "Tucci, C. E. M. (2009). Hidrologia: Ciencia e Aplicacao (4 ed.). "
        "Porto Alegre: UFRGS/ABRH.",
        "UFRJ/COPPE. (2007). HidroFlu v2.0 - software de hidrologia para drenagem. "
        "Banco de dados distribuido com o instalador.",
    ]
    for ref in refs:
        pdf.set_font("Helvetica", "", 8)
        pdf.multi_cell(0, 4, f"- {ref}")
        pdf.ln(1)

    return bytes(pdf.output())

"""
Gerador de relatorio PDF tecnico modular para o pipeline chuva_vazao.

Cada modulo (Bacia, IDF/Hietograma/Hidrograma, Verificacao de Secao,
Dimensionamento Hidraulico, Reservatorio de Detencao) e renderizado
condicionalmente conforme os inputs disponiveis em `RelatorioInputs` —
o app e multi-uso, e o PDF acompanha: se o usuario so dimensionou uma
manilha, sai um PDF de dimensionamento; se so fez verificacao de secao,
sai um PDF de verificacao; e assim por diante.

Stack: fpdf2 + kaleido (via plotly.to_image).
"""
from __future__ import annotations

import tempfile
from dataclasses import dataclass, field
from datetime import date
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


# ---------------------------------------------------------------------------
# Paleta e constantes de estilo
# ---------------------------------------------------------------------------

COLOR_PRIMARY = (22, 66, 91)         # #16425B  azul-petroleo Azevedo (titulos, header de tabela)
COLOR_PRIMARY_SOFT = (219, 231, 237) # #DBE7ED  tinta clara do primario (fundos de header)
COLOR_ACCENT = (46, 125, 50)         # #2E7D32  verde-energia Azevedo (destaques)
COLOR_TEXT = (52, 58, 64)            # #343A40  cinza-grafite do corpo de texto
COLOR_MUTED = (108, 117, 125)        # #6C757D  legendas, header/footer
COLOR_RULE = (198, 210, 216)         # linhas divisorias sutis
COLOR_ROW_ALT = (243, 246, 248)      # zebra de tabelas

MARGIN_LR = 18      # margem esquerda/direita em mm
MARGIN_TOP = 18
MARGIN_BOT = 18
PAGE_W = 210        # A4
USABLE_W = PAGE_W - 2 * MARGIN_LR  # 174 mm


# Helvetica builtin do fpdf2 e Latin-1 only. Mapeia chars unicode comuns que
# aparecem em saidas tecnicas pra equivalentes Latin-1 antes de imprimir.
_LATIN1_FALLBACK = {
    "—": "-",   # em-dash
    "–": "-",   # en-dash
    "−": "-",   # minus sign
    "•": "-",   # bullet
    "·": ".",   # middle dot
    "²": "2",   # superscript 2  (m²)
    "³": "3",   # superscript 3  (m³)
    "×": "x",   # multiplication
    "≤": "<=",
    "≥": ">=",
    "→": "->",
    "…": "...",
    "“": '"', "”": '"',
    "‘": "'", "’": "'",
}


def _latin1_safe(text: str) -> str:
    """Substitui chars unicode comuns pra Latin-1; outros viram '?'."""
    if not text:
        return text
    for k, v in _LATIN1_FALLBACK.items():
        text = text.replace(k, v)
    return text.encode("latin-1", errors="replace").decode("latin-1")


# ---------------------------------------------------------------------------
# RelatorioInputs — todos os modulos sao opcionais
# ---------------------------------------------------------------------------

@dataclass
class RelatorioInputs:
    """Inputs do relatorio. Cada campo opcional ativa um modulo no PDF."""

    # ----- Identificacao (sempre util, pode ficar generico) -----
    posto_descricao: str = ""
    posto_estado: str = ""
    posto_fonte: str = ""

    # ----- Modulo IDF / Hietograma / Hidrograma (paginas 1-3) -----
    idf_params: IDFParams | None = None
    idf_table: pd.DataFrame | None = None
    TR_anos: float | None = None
    duracao_min: float | None = None
    dt_min: float | None = None
    metodo_hietograma: str | None = None
    hietograma: pd.Series | None = None
    scs_params: SCSParams | None = None
    hidrograma: pd.DataFrame | None = None
    fig_idf: go.Figure | None = None
    fig_hietograma: go.Figure | None = None
    fig_hidrograma: go.Figure | None = None

    # ----- Modulo Bacia (pagina 0) -----
    basin_metrics: object | None = None
    basin_outlet_original: tuple[float, float] | None = None
    basin_outlet_snapped: tuple[float, float] | None = None
    tc_breakdown: dict[str, float] | None = None

    # ----- Modulo Chuva-Vazao Racional (sub-modulo da hidrologia) -----
    metodo_chuva_vazao: str | None = None
    C_racional: float | None = None
    uso_solo_racional: str | None = None

    # ----- Modulo Verificacao de Secao (pagina 4) -----
    verificacao_secao: object | None = None              # VerificacaoTrecho
    fig_verif_perfil: go.Figure | None = None
    figs_verif_secoes: list[go.Figure] | None = None     # [M, C, J]

    # ----- Modulo Dimensionamento Hidraulico (pagina 5) -----
    dimensionamento: dict | None = None

    # ----- Modulo Reservatorio de Detencao (pagina 6) -----
    detencao: object | None = None                        # RoteamentoResult
    detencao_reservatorio: object | None = None           # Reservatorio
    detencao_curva_dem: dict | None = None                # {cotas_m, volumes_m3, areas_m2, mancha_geojson}
    fig_detencao: go.Figure | None = None                 # afluente vs efluente
    fig_detencao_caracteristicas: go.Figure | None = None # V/A x z + descarga
    fig_mancha: go.Figure | None = None                   # mancha sobre bacia (opcional)

    # ----- Modulo Inundacao Fluvial 1D (pagina 7) -----
    inundacao: dict | None = None                         # resumo (Q, n, cc, area_ha, prof, n_secoes, L, warnings)
    inundacao_tabela: pd.DataFrame | None = None          # perfil por secao
    fig_inundacao_mancha: object | None = None            # matplotlib Figure (hillshade + mancha)
    fig_inundacao_perfil: go.Figure | None = None         # perfil longitudinal (Plotly)

    # ----- Modulo Chuva-Vazao Distribuido (sub-bacias, pagina 3) -----
    distribuido: dict | None = None                       # Qp, t_pico, volume, ARF, TR, n, area, fonte_cn, celeridade
    distribuido_tabela: pd.DataFrame | None = None        # parametros por sub-bacia (id, A, tc, CN)
    fig_distribuido_hidrograma: go.Figure | None = None   # hidrograma no exutorio (Plotly)
    fig_distribuido_mapa: go.Figure | None = None         # choropleth do CN por sub-bacia (Plotly)
    fig_distribuido_tc: go.Figure | None = None           # choropleth do tc por sub-bacia (Plotly)
    fig_distribuido_topologia: go.Figure | None = None    # rede / topologia das sub-bacias (Plotly)


# ---------------------------------------------------------------------------
# RelatorioPDF — fpdf2 estilizado
# ---------------------------------------------------------------------------

class RelatorioPDF(FPDF):
    def __init__(self, titulo_capa: str, subtitulo: str = "",
                 identidade: dict | None = None):
        super().__init__(orientation="P", unit="mm", format="A4")
        self.titulo_capa = titulo_capa
        self.subtitulo = subtitulo
        # Marca ativa (default Azevedo); override parcial e mesclado por cima.
        from chuva_vazao.identidade import identidade_ativa  # noqa: PLC0415
        self.identidade = identidade_ativa(identidade)
        self.set_margins(MARGIN_LR, MARGIN_TOP, MARGIN_LR)
        self.set_auto_page_break(auto=True, margin=MARGIN_BOT)
        self._section_counter = 0

    # ---- Header / footer -------------------------------------------------
    # A capa e sempre a pag. 1 e renderiza seu proprio rodape (marca + creditos);
    # header/footer padrao so entram a partir da pag. 2.
    def header(self):
        if self.page_no() == 1:
            return
        self.set_font("Helvetica", "", 8)
        self.set_text_color(*COLOR_MUTED)
        left = self.titulo_capa[:60] + (" - " + self.subtitulo if self.subtitulo else "")
        self.cell(USABLE_W * 0.7, 5, _latin1_safe(left), align="L")
        self.cell(
            USABLE_W * 0.3, 5,
            f"{date.today().isoformat()}  -  pag. {self.page_no()}/{{nb}}",
            align="R", new_x="LMARGIN", new_y="NEXT",
        )
        self.set_draw_color(*COLOR_RULE)
        self.set_line_width(0.2)
        self.line(MARGIN_LR, self.get_y(), PAGE_W - MARGIN_LR, self.get_y())
        self.ln(4)
        self.set_text_color(*COLOR_TEXT)

    def footer(self):
        if self.page_no() == 1:
            return
        self.set_y(-12)
        self.set_font("Helvetica", "I", 7)
        self.set_text_color(*COLOR_MUTED)
        self.cell(
            0, 5,
            _latin1_safe(self.identidade["rodape_paginas"]),
            align="C",
        )
        self.set_text_color(*COLOR_TEXT)

    # ---- Headings --------------------------------------------------------
    def add_section(self, text: str, *, count: bool = True):
        """Secao numerada automaticamente (1., 2., 3., ...)."""
        if count:
            self._section_counter += 1
            text = f"{self._section_counter}.  {text}"
        self.ln(2)
        self.set_text_color(*COLOR_PRIMARY)
        self.set_font("Helvetica", "B", 14)
        self.cell(0, 8, _latin1_safe(text), new_x="LMARGIN", new_y="NEXT")
        # Linha sob o titulo, na cor primary
        self.set_draw_color(*COLOR_PRIMARY)
        self.set_line_width(0.4)
        y = self.get_y() + 0.5
        self.line(MARGIN_LR, y, MARGIN_LR + 60, y)
        self.set_text_color(*COLOR_TEXT)
        self.ln(4)

    def add_subsection(self, text: str):
        self.ln(1)
        self.set_font("Helvetica", "B", 10)
        self.set_text_color(*COLOR_ACCENT)
        self.cell(0, 6, _latin1_safe(text), new_x="LMARGIN", new_y="NEXT")
        self.set_text_color(*COLOR_TEXT)
        self.ln(1)

    def add_text(self, text: str, *, size: int = 9):
        self.set_font("Helvetica", "", size)
        self.set_text_color(*COLOR_TEXT)
        self.multi_cell(0, 4.5, _latin1_safe(text))
        self.ln(2)

    # ---- Param table (label: value) -------------------------------------
    def add_param(self, label: str, value: str, *, hint: str = ""):
        self.set_text_color(*COLOR_TEXT)
        self.set_font("Helvetica", "B", 9)
        self.cell(56, 5.5, _latin1_safe(label))
        self.set_font("Helvetica", "", 9)
        self.cell(45, 5.5, _latin1_safe(value))
        if hint:
            self.set_font("Helvetica", "I", 8)
            self.set_text_color(*COLOR_MUTED)
            self.cell(0, 5.5, _latin1_safe(hint))
            self.set_text_color(*COLOR_TEXT)
        self.ln(5.5)

    def add_kv_row(self, items: list[tuple[str, str]]):
        """Linha 2x2 ou Nx2 de label:value (mais denso que add_param)."""
        col_w = USABLE_W / len(items)
        self.set_font("Helvetica", "", 9)
        self.set_text_color(*COLOR_TEXT)
        for label, value in items:
            self.set_font("Helvetica", "B", 9)
            self.cell(col_w * 0.55, 5.5, _latin1_safe(label))
            self.set_font("Helvetica", "", 9)
            self.cell(col_w * 0.45, 5.5, _latin1_safe(value))
        self.ln(6)

    # ---- Tables ---------------------------------------------------------
    def add_dataframe(self, df: pd.DataFrame, col_widths: list[float] | None = None):
        cols = list(df.columns)
        n_cols = len(cols)
        if col_widths is None:
            col_widths = [USABLE_W / n_cols] * n_cols
        else:
            # normaliza pra somar exatamente USABLE_W
            tot = sum(col_widths)
            col_widths = [w * USABLE_W / tot for w in col_widths]

        # Header
        self.set_font("Helvetica", "B", 8)
        self.set_fill_color(*COLOR_PRIMARY)
        self.set_text_color(255, 255, 255)
        self.set_draw_color(*COLOR_PRIMARY)
        self.set_line_width(0.2)
        for i, col in enumerate(cols):
            self.cell(col_widths[i], 6.5, _latin1_safe(str(col)), border=1, fill=True, align="C")
        self.ln()

        # Rows com zebra
        self.set_font("Helvetica", "", 8)
        self.set_text_color(*COLOR_TEXT)
        self.set_draw_color(*COLOR_RULE)
        for idx, (_, row) in enumerate(df.iterrows()):
            fill = idx % 2 == 1
            if fill:
                self.set_fill_color(*COLOR_ROW_ALT)
            for i, col in enumerate(cols):
                val = row[col]
                if isinstance(val, float):
                    text = f"{val:.2f}"
                else:
                    text = str(val)
                self.cell(col_widths[i], 5.5, _latin1_safe(text), border=1, align="C", fill=fill)
            self.ln()
        self.ln(3)

    # ---- Figures --------------------------------------------------------
    def add_figure(
        self,
        fig: go.Figure,
        *,
        width_mm: float | None = None,
        height_mm: float = 90,
    ):
        if width_mm is None:
            width_mm = USABLE_W
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp_path = Path(tmp.name)
        try:
            try:
                # render alto pra ficar nitido na impressao
                fig.write_image(str(tmp_path), width=1200, height=int(height_mm * 12), scale=2)
            except Exception:  # noqa: BLE001
                # kaleido ausente/descasado (comum no ambiente local) — degrada sem
                # derrubar o PDF; as figuras saem normalmente no Streamlit Cloud.
                self.set_font("Helvetica", "I", 8)
                self.set_text_color(150, 150, 150)
                self.multi_cell(0, 5, _latin1_safe(
                    "[Grafico indisponivel neste ambiente local — gere o PDF no "
                    "Streamlit Cloud para incluir as figuras.]"))
                self.set_text_color(*COLOR_TEXT)
                self.ln(2)
                return
            x = MARGIN_LR + (USABLE_W - width_mm) / 2
            self.image(str(tmp_path), x=x, w=width_mm, h=height_mm)
            self.ln(4)
        finally:
            try:
                tmp_path.unlink()
            except OSError:
                pass

    def add_mpl_figure(self, fig, *, width_mm: float | None = None):
        """Insere uma figura matplotlib (savefig PNG); altura automatica p/ manter aspecto."""
        import matplotlib.pyplot as plt  # noqa: PLC0415
        if width_mm is None:
            width_mm = USABLE_W
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp_path = Path(tmp.name)
        try:
            fig.savefig(str(tmp_path), dpi=150, bbox_inches="tight")
            plt.close(fig)
            x = MARGIN_LR + (USABLE_W - width_mm) / 2
            self.image(str(tmp_path), x=x, w=width_mm)
            self.ln(4)
        finally:
            try:
                tmp_path.unlink()
            except OSError:
                pass

    # ---- Cover -----------------------------------------------------------
    def add_cover(self, modulos_label: str):
        self.add_page()
        self.set_text_color(*COLOR_TEXT)

        # Logo da marca ativa centralizado no topo (identidade visual).
        # Largura por orientacao: logo horizontal (aspecto baixo) ganha mais
        # largura; quadrado/vertical fica compacto.
        logo_path = Path(self.identidade.get("logo_path") or "")
        if logo_path.is_file():
            try:
                from PIL import Image  # noqa: PLC0415 — dependencia do fpdf2

                with Image.open(logo_path) as im:
                    aspecto = im.height / im.width if im.width else 1.0
            except Exception:  # noqa: BLE001 — proporcao default se PIL falhar
                aspecto = 1.0
            logo_w = 56.0 if aspecto < 0.6 else 32.0
            logo_h = logo_w * aspecto
            x = (PAGE_W - logo_w) / 2
            y_logo = 24
            self.image(str(logo_path), x=x, y=y_logo, w=logo_w)
            self.set_y(y_logo + logo_h + 8)
        else:
            self.ln(40)

        self.set_font("Helvetica", "B", 10)
        self.set_text_color(*COLOR_ACCENT)
        self.cell(0, 6, "RELATORIO TECNICO", align="C", new_x="LMARGIN", new_y="NEXT")
        self.set_text_color(*COLOR_TEXT)

        self.ln(2)
        self.set_font("Helvetica", "B", 24)
        self.set_text_color(*COLOR_PRIMARY)
        self.cell(0, 12, _latin1_safe(self.titulo_capa), align="C", new_x="LMARGIN", new_y="NEXT")

        if self.subtitulo:
            self.set_font("Helvetica", "", 12)
            self.set_text_color(*COLOR_TEXT)
            self.cell(0, 7, _latin1_safe(self.subtitulo), align="C", new_x="LMARGIN", new_y="NEXT")

        self.ln(8)
        self.set_draw_color(*COLOR_PRIMARY)
        self.set_line_width(0.6)
        self.line(70, self.get_y(), 140, self.get_y())
        self.ln(10)

        # Bloco de info-chave em "card" leve
        self._cover_card(modulos_label)

        # Rodape da capa (sem auto_page_break pra nao quebrar pra pag.2)
        self.set_auto_page_break(auto=False)
        rodape_sub = self.identidade.get("rodape_capa_sub", "")
        self.set_y(-34 if rodape_sub else -30)
        self.set_font("Helvetica", "B", 9)
        self.set_text_color(*COLOR_PRIMARY)
        self.cell(
            0, 5,
            _latin1_safe(self.identidade["rodape_capa_titulo"]),
            align="C", new_x="LMARGIN", new_y="NEXT",
        )
        if rodape_sub:
            self.set_font("Helvetica", "", 8)
            self.set_text_color(*COLOR_MUTED)
            self.cell(
                0, 5, _latin1_safe(rodape_sub),
                align="C", new_x="LMARGIN", new_y="NEXT",
            )
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(*COLOR_MUTED)
        self.cell(
            0, 5,
            _latin1_safe(
                f"Gerado por Chuva - Vazão  ·  emitido em {date.today().strftime('%d/%m/%Y')}"
            ),
            align="C",
        )
        self.set_auto_page_break(auto=True, margin=MARGIN_BOT)

    def _cover_card(self, modulos_label: str):
        x0 = 35
        y0 = self.get_y()
        w = PAGE_W - 2 * x0
        h = 60
        self.set_draw_color(*COLOR_RULE)
        self.set_line_width(0.3)
        self.set_fill_color(252, 253, 255)
        self.rect(x0, y0, w, h, style="DF")

        self.set_xy(x0 + 8, y0 + 6)
        self.set_font("Helvetica", "B", 9)
        self.set_text_color(*COLOR_ACCENT)
        self.cell(0, 5, "MODULOS DESTE RELATORIO", new_x="LMARGIN", new_y="NEXT")
        self.set_x(x0 + 8)
        self.set_font("Helvetica", "", 10)
        self.set_text_color(*COLOR_TEXT)
        self.multi_cell(w - 16, 5.5, _latin1_safe(modulos_label))

        if self.titulo_capa or self.subtitulo:
            self.ln(2)


# ---------------------------------------------------------------------------
# Helpers de orquestracao
# ---------------------------------------------------------------------------

def _detectar_modulos(inputs: RelatorioInputs) -> list[str]:
    modulos: list[str] = []
    if inputs.basin_metrics is not None:
        modulos.append("bacia")
    if inputs.idf_params is not None and inputs.hietograma is not None and inputs.hidrograma is not None:
        modulos.append("hidrologia")
    if inputs.verificacao_secao is not None:
        modulos.append("verificacao_secao")
    if inputs.dimensionamento is not None:
        modulos.append("dimensionamento")
    if inputs.detencao is not None:
        modulos.append("detencao")
    if inputs.inundacao is not None:
        modulos.append("inundacao")
    if inputs.distribuido is not None:
        modulos.append("distribuido")
    return modulos


_MODULO_NOMES = {
    "bacia": "Bacia de Contribuicao",
    "hidrologia": "Hidrologia de Projeto (IDF, Hietograma, Hidrograma)",
    "verificacao_secao": "Verificacao Hidraulica de Secao",
    "dimensionamento": "Dimensionamento Hidraulico de Conduto",
    "detencao": "Reservatorio de Detencao",
    "inundacao": "Inundacao Fluvial 1D",
    "distribuido": "Chuva-Vazao Distribuido (sub-bacias)",
}


def _titulo_capa(modulos: list[str]) -> tuple[str, str]:
    """(titulo, subtitulo) adaptado aos modulos selecionados."""
    if not modulos:
        return ("Relatório Chuva - Vazão", "")
    if len(modulos) == 1:
        return (_MODULO_NOMES[modulos[0]], "")
    if set(modulos) == {"hidrologia"}:
        return ("Hidrologia de Projeto", "Pipeline Chuva - Vazão para drenagem")
    return ("Pipeline Chuva - Vazão", "Drenagem urbana, fluvial e infraestrutura hidráulica")


# ---------------------------------------------------------------------------
# Renderers por modulo
# ---------------------------------------------------------------------------

def _render_identificacao(pdf: RelatorioPDF, inp: RelatorioInputs):
    """Bloco rapido de identificacao do projeto/posto, se preenchido."""
    if not (inp.posto_descricao or inp.posto_estado or inp.posto_fonte):
        return
    pdf.add_page()
    pdf.add_section("Identificacao do projeto")
    if inp.posto_descricao:
        pdf.add_param("Posto / local:", inp.posto_descricao)
    if inp.posto_estado:
        pdf.add_param("Estado / UF:", inp.posto_estado)
    if inp.posto_fonte:
        pdf.add_param("Fonte da IDF:", inp.posto_fonte)


def _render_bacia(pdf: RelatorioPDF, inp: RelatorioInputs):
    pdf.add_page()
    pdf.add_section("Bacia de Contribuicao")
    pdf.add_text(
        "Delineamento via WhiteboxTools sobre DEM (Copernicus GLO-30 ou DEM "
        "local). Pipeline: breach depressions, D8 pointer, flow accumulation, "
        "extract streams, snap pour point, watershed, vetorizacao."
    )
    if hasattr(inp.basin_metrics, "summary_dict"):
        d = inp.basin_metrics.summary_dict()
        for k, v in d.items():
            pdf.add_param(f"{k}:", f"{v}")

    if inp.basin_outlet_original and inp.basin_outlet_snapped:
        pdf.add_subsection("Exutorio")
        o_lat, o_lon = inp.basin_outlet_original
        s_lat, s_lon = inp.basin_outlet_snapped
        pdf.add_param("Original (clicado):", f"({o_lat:.5f}, {o_lon:.5f})")
        pdf.add_param("Snapped (ajustado):", f"({s_lat:.5f}, {s_lon:.5f})")

    if inp.tc_breakdown:
        pdf.add_subsection("Tempo de Concentracao")
        pdf.add_text(
            "Tres formulas classicas para bacias rurais de pequeno/medio porte; "
            "media aritmetica e o valor adotado por default."
        )
        for metodo, valor in inp.tc_breakdown.items():
            pdf.add_param(f"  {metodo}:", f"{valor:.2f} min")


def _render_hidrologia(pdf: RelatorioPDF, inp: RelatorioInputs):
    # IDF
    pdf.add_page()
    pdf.add_section("Equacao IDF")
    pdf.add_text(
        "Equacao Intensidade-Duracao-Frequencia (convencao do catalogo embutido):\n\n"
        "    i = K * TR^a / (t + c)^b\n\n"
        "i (mm/h), TR (anos), t (min), K/a/b/c — coeficientes do posto."
    )
    p = inp.idf_params
    pdf.add_param("K:", f"{p.K:.3f}")
    pdf.add_param("a (expoente TR):", f"{p.expoente_tr:.4f}")
    pdf.add_param("b (expoente duracao):", f"{p.expoente_duracao:.4f}")
    pdf.add_param("c (constante, min):", f"{p.constante_duracao:.2f}")

    if inp.fig_idf is not None:
        pdf.add_subsection("Curva IDF")
        pdf.add_figure(inp.fig_idf, height_mm=85)

    if inp.idf_table is not None:
        pdf.add_subsection("Tabela IDF (mm/h)")
        idf_display = inp.idf_table.round(2).reset_index()
        idf_display.columns = ["Duracao (min)"] + [f"TR={tr}" for tr in inp.idf_table.columns]
        n = len(idf_display.columns)
        widths = [1.4] + [1.0] * (n - 1)
        pdf.add_dataframe(idf_display, col_widths=widths)

    # Hietograma
    pdf.add_page()
    pdf.add_section("Hietograma de Projeto")
    if inp.metodo_hietograma:
        pdf.add_text(
            f"Metodo: {inp.metodo_hietograma}.\n"
            f"TR = {inp.TR_anos:g} anos, duracao = {inp.duracao_min:g} min, "
            f"passo dt = {inp.dt_min:g} min, altura total = {inp.hietograma.sum():.2f} mm."
        )
    if inp.fig_hietograma is not None:
        pdf.add_figure(inp.fig_hietograma, height_mm=85)

    # Bacia / SCS
    if inp.scs_params is not None:
        pdf.add_section("Parametros da Bacia e SCS")
        s = inp.scs_params
        pdf.add_param("Area:", f"{s.area_km2:.3f} km^2")
        pdf.add_param("Tempo de concentracao:", f"{s.tempo_concentracao_h:.3f} h")
        pdf.add_param("CN:", f"{s.CN:.1f}")
        pdf.add_param("S (retencao):", f"{s.S_mm:.2f} mm")
        pdf.add_param("Ia (abstracao inicial):", f"{s.Ia_mm:.2f} mm")

    # Metodo Racional (sub-bloco)
    if inp.metodo_chuva_vazao == "Racional":
        pdf.add_subsection("Metodo Racional")
        pdf.add_text(
            "Q = C . i(tc) . A / 3.6. Valido para bacias pequenas (A <= 2 km2). "
            "Pressupoe duracao critica = tc e chuva uniforme sobre a bacia."
        )
        if inp.C_racional is not None:
            pdf.add_param("Coeficiente C:", f"{inp.C_racional:.2f}")
        if inp.uso_solo_racional:
            pdf.add_param("Uso do solo:", inp.uso_solo_racional)

    # Hidrograma
    pdf.add_section("Hidrograma de Projeto")
    pdf.add_text(
        "Convolucao do hietograma excedente (SCS-CN) com hidrograma unitario "
        "triangular SCS. t_pico = D/2 + 0.6*tc; t_base = 2.67*t_pico; "
        "Q_pico = 0.208 * A / t_pico [m^3/s/mm]."
    )
    pdf.add_kv_row([
        ("Q_pico:", f"{Q_pico_m3s(inp.hidrograma):.2f} m³/s"),
        ("Tempo ao pico:", f"{tempo_ao_pico_min(inp.hidrograma):.1f} min"),
    ])
    pdf.add_kv_row([
        ("Volume escoado:", f"{volume_escoado_m3(inp.hidrograma):,.0f} m³"),
        ("dt simulado:", f"{inp.dt_min:g} min"),
    ])
    if inp.fig_hidrograma is not None:
        pdf.add_figure(inp.fig_hidrograma, height_mm=110)

    # Metodologia compacta
    pdf.add_page()
    pdf.add_section("Metodologia")
    pdf.add_subsection("IDF")
    pdf.add_text(
        "Coeficientes K, a, b, c oriundos do catalogo embutido de postos "
        "pluviometricos. Convencao: b = expoente da duracao, c = constante "
        "temporal em minutos."
    )
    pdf.add_subsection("Hietograma")
    pdf.add_text(
        "Blocos Alternados (Chicago): redistribui alturas incrementais "
        "derivadas da IDF em torno do bloco central. Huff: distribui a altura "
        "total pela curva adimensional do quartil escolhido."
    )
    pdf.add_subsection("Escoamento Superficial (SCS-CN)")
    pdf.add_text(
        "Q = (P − Ia)² / (P − Ia + S);  Ia = 0,2 · S;  S = 25 400 / CN − 254 (mm). "
        "Aplicado cumulativamente sobre o hietograma para gerar o excedente."
    )
    pdf.add_subsection("Hidrograma Unitario Triangular SCS")
    pdf.add_text(
        "t_lag = 0,6 · tc; t_pico = D/2 + t_lag; t_base = 2,67 · t_pico; "
        "Q_pico = 0,208 · A / t_pico [m³/s/mm]. Convolucao numerica do "
        "hietograma excedente (mm) com a UH (m³/s/mm) produz Q(t)."
    )


def _render_verificacao_secao(pdf: RelatorioPDF, inp: RelatorioInputs):
    v = inp.verificacao_secao
    pdf.add_page()
    pdf.add_section("Verificacao Hidraulica de Secao")
    pdf.add_text(
        "Verificacao em regime uniforme (Manning) nas tres secoes do trecho "
        "(montante / central / jusante), com a mesma declividade calculada "
        "via thalwegs M→J. A linha critica (Fr = 1) e mostrada para "
        "diagnostico de regime."
    )
    pdf.add_kv_row([
        ("Q de projeto:", f"{v.Q_projeto_m3_s:.3f} m³/s"),
        ("n de Manning:", f"{v.n:.3f}"),
    ])
    pdf.add_kv_row([
        ("Comprimento do trecho:", f"{v.L_total_m:.1f} m"),
        ("Declividade S:", f"{v.S_trecho_m_per_m * 100:.3f} %"),
    ])

    pdf.add_subsection("Resumo das tres secoes (regime uniforme)")
    df = pd.DataFrame([
        {
            "Secao": "Montante",
            "y_w (m)": round(v.escoamento_M.y_w_m, 3),
            "y_lam (m)": round(v.escoamento_M.y_lamina_m, 2),
            "v (m/s)": round(v.escoamento_M.v_m_s, 2),
            "Fr": round(v.escoamento_M.Fr, 3),
            "Regime": v.escoamento_M.regime,
            "y_c (m)": round(v.critica_M.y_lamina_m, 2),
        },
        {
            "Secao": "Central",
            "y_w (m)": round(v.escoamento_C.y_w_m, 3),
            "y_lam (m)": round(v.escoamento_C.y_lamina_m, 2),
            "v (m/s)": round(v.escoamento_C.v_m_s, 2),
            "Fr": round(v.escoamento_C.Fr, 3),
            "Regime": v.escoamento_C.regime,
            "y_c (m)": round(v.critica_C.y_lamina_m, 2),
        },
        {
            "Secao": "Jusante",
            "y_w (m)": round(v.escoamento_J.y_w_m, 3),
            "y_lam (m)": round(v.escoamento_J.y_lamina_m, 2),
            "v (m/s)": round(v.escoamento_J.v_m_s, 2),
            "Fr": round(v.escoamento_J.Fr, 3),
            "Regime": v.escoamento_J.regime,
            "y_c (m)": round(v.critica_J.y_lamina_m, 2),
        },
    ])
    pdf.add_dataframe(df, col_widths=[1.0, 1.0, 1.0, 0.9, 0.7, 1.1, 0.9])

    if v.warnings:
        pdf.add_subsection("Alertas")
        for w in v.warnings:
            pdf.add_text(f"• {w}", size=9)

    if inp.fig_verif_perfil is not None:
        pdf.add_subsection("Perfil longitudinal")
        pdf.add_figure(inp.fig_verif_perfil, height_mm=85)

    if inp.figs_verif_secoes:
        pdf.add_subsection("Secoes transversais")
        for fig in inp.figs_verif_secoes:
            pdf.add_figure(fig, height_mm=70)


def _render_dimensionamento(pdf: RelatorioPDF, inp: RelatorioInputs):
    dim = inp.dimensionamento
    pdf.add_page()
    pdf.add_section("Dimensionamento Hidraulico")
    pdf.add_text(
        "Manning (Q = (1/n) · A · R^(2/3) · S^(1/2)). Para conduto circular, "
        "escolhe o menor diametro comercial que atenda a vazao de projeto com "
        "lamina maxima configurada; retangular usa as dimensoes informadas."
    )

    pdf.add_kv_row([
        ("Tipo:", str(dim.get("tipo", "?")).title()),
        ("Material:", str(dim.get("material", "?"))),
    ])
    pdf.add_kv_row([
        ("n de Manning:", f"{dim.get('n', 0):.3f}"),
        ("Declividade S:", f"{dim.get('S', 0) * 100:.3f} %"),
    ])
    pdf.add_kv_row([
        ("Q de projeto:", f"{dim.get('Q_projeto_m3_s', 0):.3f} m³/s"),
        ("Fator de seguranca:", f"{dim.get('fator_seguranca', 1):.2f}"),
    ])

    n_linhas = int(dim.get("n_linhas", 1) or 1)
    pdf.add_kv_row([
        ("Modo de calculo:", str(dim.get("modo", "auto")).title()),
        ("Linhas em paralelo:", f"{n_linhas}"),
    ])

    pdf.add_subsection("Secao adotada")
    if dim.get("tipo") == "circular":
        D_mm = dim.get("D_adotado_m", 0) * 1000
        arranjo = (
            f"{n_linhas} manilhas Phi {D_mm:.0f} mm" if n_linhas > 1
            else f"1 manilha Phi {D_mm:.0f} mm"
        )
        pdf.add_param("Arranjo:", arranjo)
        pdf.add_param("Diametro adotado:", f"{D_mm:.0f} mm")
    else:
        b = dim.get("b_m", 0)
        h = dim.get("h_total_m", 0)
        arranjo = (
            f"{n_linhas} celulas {b:.1f} × {h:.1f} m" if n_linhas > 1
            else f"1 celula {b:.1f} × {h:.1f} m"
        )
        pdf.add_param("Arranjo:", arranjo)
        pdf.add_param("Largura b:", f"{b:.2f} m")
        pdf.add_param("Altura total h:", f"{h:.2f} m")

    pdf.add_subsection("Operacao real (Q de projeto, sem fator de seguranca)")
    pdf.add_param("Q por linha:", f"{dim.get('Q_por_linha_m3_s', 0):.3f} m³/s")
    pdf.add_param(
        "Q total (capacidade):",
        f"{dim.get('Q_total_capacidade_m3_s', 0):.3f} m³/s",
    )
    pdf.add_param("Lamina de operacao:", f"{dim.get('h_op_m', 0):.3f} m")
    pdf.add_param("Velocidade:", f"{dim.get('v_op_m_s', 0):.2f} m/s")

    warnings_list = dim.get("warnings") or []
    if warnings_list:
        pdf.add_subsection("Alertas")
        for w in warnings_list:
            pdf.add_text(f"• {w}", size=9)


def _render_detencao(pdf: RelatorioPDF, inp: RelatorioInputs):
    det = inp.detencao
    res = inp.detencao_reservatorio
    curva_dem = inp.detencao_curva_dem
    pdf.add_page()
    pdf.add_section("Reservatorio de Detencao")
    pdf.add_text(
        "Roteamento por Puls modificado em reservatorio com orificio de fundo "
        "+ vertedor retangular de emergencia. Geometria prismatica, curva "
        "cota-volume tabulada (drone/topografia) ou gerada do DEM via "
        "flood-fill na bacia. Atenuacao = 1 - Qp_out / Qp_in."
    )

    if res is not None:
        modo = "Curva cota-volume tabulada" if getattr(res, "usa_curva_tabulada", False) else "Prismatica"
        z_fundo = getattr(res, "z_fundo_m", 0.0)
        z_max_abs = getattr(res, "z_max_abs_m", z_fundo + getattr(res, "h_max_m", 0.0))
        datum = getattr(res, "datum_vertical", "") or "nao informado"
        pdf.add_subsection("Geometria")
        pdf.add_kv_row([
            ("Modo:", modo),
            ("Datum vertical:", datum),
        ])
        pdf.add_kv_row([
            ("Cota do fundo z₀:", f"{z_fundo:.2f} m"),
            ("NA maximo:", f"{z_max_abs:.2f} m"),
        ])
        pdf.add_kv_row([
            ("Altura maxima h_max:", f"{getattr(res, 'h_max_m', 0):.2f} m"),
            ("Cota orificio:", f"{z_fundo + getattr(res, 'z_orificio_m', 0):.2f} m"),
        ])
        pdf.add_kv_row([
            ("Diametro orificio:", f"{getattr(res, 'd_orificio_m', 0) * 1000:.0f} mm"),
            ("Cota vertedor:", f"{z_fundo + getattr(res, 'z_vertedor_m', 0):.2f} m"),
        ])
        pdf.add_kv_row([
            ("Largura vertedor:", f"{getattr(res, 'b_vertedor_m', 0):.2f} m"),
            ("Cd / Cw:", f"{getattr(res, 'Cd_orificio', 0):.2f} / {getattr(res, 'Cw_vertedor', 0):.2f}"),
        ])

    pdf.add_subsection("Resultado do roteamento")
    pdf.add_kv_row([
        ("Qp afluente:", f"{det.Qp_in_m3_s:.2f} m³/s"),
        ("Qp efluente:", f"{det.Qp_out_m3_s:.2f} m³/s"),
    ])
    pdf.add_kv_row([
        ("Atenuacao:", f"{det.atenuacao_pct:.1f} %"),
        ("h_max atingida:", f"{det.h_max_m:.2f} m"),
    ])
    pdf.add_kv_row([
        ("Volume armazenado max:", f"{det.volume_armazenado_max_m3:,.0f} m³"),
        ("NA max atingido:",
         f"{(getattr(res, 'z_fundo_m', 0) + det.h_max_m):.2f} m" if res else "—"),
    ])

    if inp.fig_detencao is not None:
        pdf.add_subsection("Afluente vs Efluente")
        pdf.add_figure(inp.fig_detencao, height_mm=80)

    if inp.fig_detencao_caracteristicas is not None:
        pdf.add_subsection("Curvas caracteristicas do reservatorio")
        pdf.add_figure(inp.fig_detencao_caracteristicas, height_mm=80)

    if curva_dem is not None:
        pdf.add_page()
        pdf.add_section("Curva Cota-Volume do DEM (in-line)")
        pdf.add_text(
            "Curva gerada a partir do DEM Copernicus GLO-30 por flood-fill 4-conexo "
            "restrito ao poligono da bacia. Piso automatico = z do pixel da barragem. "
            "Datum vertical do Copernicus: EGM2008."
        )
        cotas = curva_dem.get("cotas_m", [])
        vols = curva_dem.get("volumes_m3", [])
        areas = curva_dem.get("areas_m2", [])
        if cotas:
            df = pd.DataFrame({
                "Cota z (m)": [round(z, 2) for z in cotas],
                "Area (m²)": [round(a, 0) for a in areas],
                "Volume (m³)": [round(v, 0) for v in vols],
            })
            pdf.add_dataframe(df, col_widths=[1.0, 1.0, 1.0])
        if inp.fig_mancha is not None:
            pdf.add_subsection("Mancha de inundacao")
            pdf.add_figure(inp.fig_mancha, height_mm=110)


def _render_inundacao(pdf: RelatorioPDF, inp: RelatorioInputs):
    inu = inp.inundacao or {}
    pdf.add_page()
    pdf.add_section("Inundacao Fluvial 1D")
    pdf.add_text(
        "Mancha de inundacao por modelo 1D permanente (standard step / escoamento "
        "gradualmente variado). Secoes transversais perpendiculares ao eixo do rio "
        "sao amostradas do MDT; a linha d'agua e resolvida secao a secao por balanco "
        "de energia (Manning + perdas de contracao/expansao), marchando de jusante "
        "para montante a partir de uma condicao de contorno, e projetada no terreno "
        "(profundidade = cota d'agua - cota do terreno) para gerar a mancha."
    )
    pdf.add_kv_row([
        ("Q de projeto:", f"{inu.get('Q_m3_s', 0):.3f} m³/s"),
        ("n de Manning:", f"{inu.get('n', 0):.3f}"),
    ])
    pdf.add_kv_row([
        ("Contorno de jusante:", str(inu.get("cc_jusante", "?"))),
        ("Numero de secoes:", f"{inu.get('n_secoes', 0)}"),
    ])
    pdf.add_kv_row([
        ("Comprimento do eixo:", f"{inu.get('L_eixo_m', 0):.0f} m"),
        ("Area alagada:", f"{inu.get('area_ha', 0):.2f} ha"),
    ])
    pdf.add_kv_row([
        ("Profundidade maxima:", f"{inu.get('prof_max_m', 0):.2f} m"),
        ("Profundidade media:", f"{inu.get('prof_media_m', 0):.2f} m"),
    ])

    if inp.fig_inundacao_mancha is not None:
        pdf.add_subsection("Mancha de inundacao sobre o MDT")
        pdf.add_figure(inp.fig_inundacao_mancha, height_mm=85)

    if inp.fig_inundacao_perfil is not None:
        pdf.add_subsection("Perfil longitudinal da linha d'agua")
        pdf.add_figure(inp.fig_inundacao_perfil, height_mm=80)

    warns = inu.get("warnings") or []
    if warns:
        pdf.add_subsection("Alertas")
        for w in warns:
            pdf.add_text(f"- {w}", size=9)

    if inp.inundacao_tabela is not None and len(inp.inundacao_tabela):
        pdf.add_subsection("Resultados por secao (amostra)")
        df = inp.inundacao_tabela.copy()
        cols = [c for c in
                ["estaca_rio_m", "z_thalweg_m", "WSE_m", "y_m", "V_m_s", "Fr", "regime"]
                if c in df.columns]
        df = df[cols]
        if len(df) > 20:   # amostra equiespacada p/ nao explodir o PDF
            df = df.iloc[:: max(1, len(df) // 20)].reset_index(drop=True)
        df = df.rename(columns={
            "estaca_rio_m": "Estaca (m)", "z_thalweg_m": "z leito (m)",
            "WSE_m": "WSE (m)", "y_m": "y (m)", "V_m_s": "v (m/s)",
        })
        pdf.add_dataframe(df, col_widths=[1.1, 1.0, 1.0, 0.8, 0.9, 0.7, 1.2][:len(df.columns)])

    pdf.add_text(
        "Modelo 1D permanente: a mancha e a cota d'agua projetada, nao capta "
        "propagacao transiente, backwater de estruturas (pontes/bueiros) nem fluxo "
        "2D em confluencias. Adequado a triagem/regularizacao; para cota de projeto "
        "de obra, validar contra modelagem hidrodinamica.",
        size=8,
    )


def _render_distribuido(pdf: RelatorioPDF, inp: RelatorioInputs):
    d = inp.distribuido or {}
    area = d.get("area_km2", 1) or 1
    pdf.add_page()
    pdf.add_section("Memorial - Chuva-Vazao Distribuido por Sub-bacias")

    pdf.add_subsection("1. Justificativa e visao geral")
    pdf.add_text(
        "Acima de ~250 km2 o hidrograma unitario unico (modelo concentrado) perde "
        "representatividade: a precipitacao deixa de ser uniforme sobre a bacia e o "
        "tempo de viagem da agua varia muito entre as cabeceiras e o exutorio. "
        "Adota-se entao um modelo SEMI-DISTRIBUIDO: a bacia e dividida em "
        "sub-bacias, cada uma com seus proprios parametros (area, tempo de "
        "concentracao e numero de curva); cada sub-bacia gera um hidrograma, que e "
        "propagado pela rede de drenagem e somado aos demais nas confluencias, ate "
        "o exutorio. As secoes seguintes documentam a cadeia completa de calculo."
    )

    pdf.add_subsection("2. Discretizacao da bacia e topologia de drenagem")
    pdf.add_text(
        "O modelo digital de elevacao e processado pelo WhiteboxTools: abertura de "
        "depressoes (breach), direcao de fluxo D8, area de drenagem acumulada e "
        "extracao da rede de canais por limiar de acumulacao. A rotina 'subbasins' "
        "divide a bacia em uma sub-bacia por trecho de canal entre confluencias. A "
        "topologia (qual sub-bacia deflui em qual) e reconstruida seguindo a "
        "direcao D8 do pixel de saida de cada sub-bacia, gerando a arvore de "
        "drenagem montante->jusante usada no roteamento. Resultado da discretizacao: "
        f"{d.get('n_subbacias', 0)} sub-bacias, area total {area:.0f} km2."
    )
    if inp.fig_distribuido_topologia is not None:
        pdf.add_subsection("Rede de drenagem e topologia")
        pdf.add_figure(inp.fig_distribuido_topologia, height_mm=82)

    pdf.add_subsection("3. Parametros por sub-bacia: tempo de concentracao e CN")
    pdf.add_text(
        "Tempo de concentracao (tc): media de tres formulas classicas - Kirpich "
        "[tc = 0.0195 L^0.77 S^-0.385], Ven Te Chow e California Culverts "
        "[tc = 57 (L^3/H)^0.385] - com L = comprimento do canal principal da "
        "sub-bacia (ou estimativa de Hack, L = 1.4 A^0.6, quando maior) e H = "
        "desnivel obtido do MDT por percentis (robusto a ruido). "
        "Numero de curva (CN, metodo SCS): o uso e a ocupacao do solo vem do "
        "MapBiomas e a textura do solo do SoilGrids; o grupo hidrologico segue a "
        "adaptacao de Sartori et al. (2005) aos solos tropicais brasileiros - "
        "Latossolos e Argissolos, argilosos mas bem estruturados e drenados, nao "
        "sao penalizados como na classificacao por textura pura. Cada sub-bacia "
        "recebe o CN ponderado pela sua area."
    )
    if inp.fig_distribuido_tc is not None:
        pdf.add_subsection("Tempo de concentracao por sub-bacia")
        pdf.add_figure(inp.fig_distribuido_tc, height_mm=80)
    if inp.fig_distribuido_mapa is not None:
        pdf.add_subsection("Numero de curva (CN) por sub-bacia")
        pdf.add_figure(inp.fig_distribuido_mapa, height_mm=80)

    pdf.add_subsection("4. Chuva de projeto e reducao de area (ARF)")
    pdf.add_text(
        "O hietograma de projeto e gerado a partir da equacao IDF do posto "
        "[i = K TR^a / (t+b)^c] pelo metodo dos blocos alternados (Chicago). A "
        "chuva pontual da IDF e convertida em chuva MEDIA sobre a bacia pelo fator "
        "de reducao de area de Leclerc & Schaake (1972): "
        "ARF = 1 - exp(-1.1 D^0.25) + exp(-1.1 D^0.25 - 0.01 A), com D em horas e A "
        f"em km2. Para esta bacia: TR = {d.get('TR', 0):.0f} anos, duracao "
        f"D = {d.get('duracao_h', 0):.1f} h, ARF = {d.get('arf', 1):.2f}. O mesmo "
        "hietograma (ja reduzido) alimenta todas as sub-bacias - o ganho do modelo "
        "distribuido vem do roteamento e do ARF, nao da variacao espacial da chuva."
    )

    pdf.add_subsection("5. Transformacao chuva-vazao (SCS)")
    pdf.add_text(
        "Em cada sub-bacia a chuva excedente e obtida pelo metodo SCS-CN: "
        "S = 25400/CN - 254 (mm); abstracao inicial Ia = 0.2 S; escoamento "
        "Pe = (P - Ia)^2 / (P - Ia + S) para P > Ia. A chuva excedente e convoluida "
        "com o hidrograma unitario triangular do SCS - tempo de pico "
        "tp = D/2 + 0.6 tc, tempo de base tb = 2.67 tp e vazao de pico "
        "Qp = 0.208 A / tp - produzindo o hidrograma de cada sub-bacia. O fator de "
        "pico (PRF, padrao 484) e ajustavel para bacias com maior armazenamento em "
        "planicie de inundacao."
    )

    pdf.add_subsection("6. Propagacao na rede (Muskingum-Cunge)")
    pdf.add_text(
        "Os hidrogramas sao propagados de montante para jusante pela arvore de "
        "drenagem. Em cada trecho aplica-se o metodo de Muskingum - "
        "Q_out(t) = C0 I(t) + C1 I(t-1) + C2 Q_out(t-1) - com os coeficientes "
        "derivados de K (tempo de viagem = comprimento do trecho / celeridade da "
        "onda) e X (fator de ponderacao, 0.2-0.3 para rios naturais). A celeridade "
        "e o principal parametro de calibracao do amortecimento: rios com planicie "
        "de inundacao sao mais lentos e espalham mais o hidrograma. Nas confluencias "
        "os hidrogramas sao somados; o resultado no exutorio e o hidrograma final."
    )

    pdf.add_subsection("7. Resultados")
    pdf.add_kv_row([
        ("Q de pico:", f"{d.get('Qpico_m3s', 0):.0f} m3/s"),
        ("Tempo ao pico:", f"{d.get('t_pico_min', 0) / 60:.1f} h"),
    ])
    pdf.add_kv_row([
        ("Volume escoado:", f"{d.get('volume_hm3', 0):.1f} hm3"),
        ("Vazao especifica:", f"{d.get('Qpico_m3s', 0) / area:.2f} m3/s/km2"),
    ])
    pdf.add_kv_row([
        ("Fonte do CN:", str(d.get("fonte_cn", "-"))),
        ("Celeridade adotada:", f"{d.get('celeridade_ms', 0):.2f} m/s"),
    ])
    if inp.fig_distribuido_hidrograma is not None:
        pdf.add_subsection("Hidrograma no exutorio")
        pdf.add_figure(inp.fig_distribuido_hidrograma, height_mm=80)
    if inp.distribuido_tabela is not None and len(inp.distribuido_tabela):
        pdf.add_subsection("Parametros por sub-bacia")
        df = inp.distribuido_tabela.copy()
        if len(df) > 25:
            df = df.iloc[:: max(1, len(df) // 25)].reset_index(drop=True)
        pdf.add_dataframe(df)

    pdf.add_subsection("8. Limitacoes e recomendacoes")
    pdf.add_text(
        "Modelo de evento, com chuva uniforme no espaco (a variabilidade espacial "
        "da precipitacao real exigiria dados distribuidos de satelite ou radar). "
        "Nao ha escoamento de base nem propagacao hidrodinamica 2D em planicie; o CN "
        "e a celeridade concentram as incertezas e devem ser calibrados contra dados "
        "observados (postos fluviometricos) quando disponiveis. Para cota de projeto "
        "de obra, recomenda-se validacao contra modelagem hidrodinamica.",
        size=8,
    )


# ---------------------------------------------------------------------------
# Referencias por modulo
# ---------------------------------------------------------------------------

_REFS_GERAIS = [
    ("Chow, V. T., Maidment, D. R., & Mays, L. W. (1988). Applied Hydrology. "
     "New York: McGraw-Hill."),
    ("Tucci, C. E. M. (2009). Hidrologia: Ciencia e Aplicacao (4 ed.). "
     "Porto Alegre: UFRGS/ABRH."),
]
_REFS_HIDROLOGIA = [
    ("Pfafstetter, O. (1957). Chuvas Intensas no Brasil. Rio de Janeiro: DNOS."),
    ("CETESB. (1980). Drenagem Urbana - Manual de Projeto. Sao Paulo: CETESB."),
    ("NRCS (1972). National Engineering Handbook, Section 4: Hydrology. USDA."),
]
_REFS_DETENCAO = [
    ("DAEE-SP. Manual de Calculo para Reservatorios de Detencao."),
    ("Puls, L. G. (1928). Construction of flood routing curves. U.S. House Doc. 185."),
]
_REFS_HIDRAULICA = [
    ("Manning, R. (1891). On the flow of water in open channels and pipes. "
     "Trans. Inst. Civil Eng. of Ireland."),
    ("Henderson, F. M. (1966). Open Channel Flow. New York: Macmillan."),
]
_REFS_BACIA = [
    ("Lindsay, J. (2014). WhiteboxTools User Manual."),
]
_REFS_INUNDACAO = [
    ("USACE (2016). HEC-RAS River Analysis System — Hydraulic Reference Manual. "
     "US Army Corps of Engineers."),
    ("Chow, V. T. (1959). Open-Channel Hydraulics. McGraw-Hill (cap. 10: standard "
     "step method)."),
]
_REFS_DISTRIBUIDO = [
    ("Sartori, A., Lombardi Neto, F. & Genovez, A. M. (2005). Classificacao "
     "Hidrologica de Solos Brasileiros para a estimativa da chuva excedente com o "
     "metodo do SCS. RBRH, 10(4)."),
    ("Cunge, J. A. (1969). On the subject of a flood propagation computation method "
     "(Muskingum method). Journal of Hydraulic Research, 7(2)."),
    ("Leclerc, G. & Schaake, J. C. (1972). Derivation of hydrologic frequency "
     "curves. Report 142, MIT, Cambridge."),
    ("Lindsay, J. (2014). WhiteboxTools User Manual."),
]


def _render_referencias(pdf: RelatorioPDF, modulos: list[str]):
    pdf.add_section("Referencias")
    refs = list(_REFS_GERAIS)
    if "hidrologia" in modulos:
        refs += _REFS_HIDROLOGIA
    if "detencao" in modulos:
        refs += _REFS_DETENCAO
    if "verificacao_secao" in modulos or "dimensionamento" in modulos:
        refs += _REFS_HIDRAULICA
    if "bacia" in modulos:
        refs += _REFS_BACIA
    if "inundacao" in modulos:
        refs += _REFS_INUNDACAO
    if "distribuido" in modulos:
        refs += _REFS_DISTRIBUIDO

    pdf.set_font("Helvetica", "", 8)
    pdf.set_text_color(*COLOR_TEXT)
    for r in refs:
        pdf.multi_cell(0, 4.2, _latin1_safe(f"- {r}"))
        pdf.ln(0.8)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def gerar_relatorio_pdf(inputs: RelatorioInputs) -> bytes:
    modulos = _detectar_modulos(inputs)
    titulo, subtitulo = _titulo_capa(modulos)
    if inputs.posto_descricao and not subtitulo:
        subtitulo = inputs.posto_descricao

    pdf = RelatorioPDF(titulo_capa=titulo, subtitulo=subtitulo)
    pdf.alias_nb_pages()

    modulos_label = (
        "  •  ".join(_MODULO_NOMES[m] for m in modulos)
        if modulos else "Sem modulos selecionados"
    )
    pdf.add_cover(modulos_label)

    _render_identificacao(pdf, inputs)

    if "bacia" in modulos:
        _render_bacia(pdf, inputs)
    if "hidrologia" in modulos:
        _render_hidrologia(pdf, inputs)
    if "verificacao_secao" in modulos:
        _render_verificacao_secao(pdf, inputs)
    if "dimensionamento" in modulos:
        _render_dimensionamento(pdf, inputs)
    if "detencao" in modulos:
        _render_detencao(pdf, inputs)
    if "inundacao" in modulos:
        _render_inundacao(pdf, inputs)
    if "distribuido" in modulos:
        _render_distribuido(pdf, inputs)

    pdf.add_page()
    _render_referencias(pdf, modulos)

    return bytes(pdf.output())

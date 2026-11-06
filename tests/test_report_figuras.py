"""Figuras do PDF: exportacao real, registro de falha e compatibilidade plotly x kaleido."""
from __future__ import annotations

import importlib.metadata as md
import tomllib
from pathlib import Path

import plotly.graph_objects as go
import pytest
from packaging.requirements import Requirement

from chuva_vazao import hietograma as hieto_mod
from chuva_vazao import hidrograma as hidro_mod
from chuva_vazao import idf, plots
from chuva_vazao.report import RelatorioInputs, gerar_relatorio_pdf

RAIZ = Path(__file__).resolve().parent.parent


def _inputs_hidrologia(com_figuras: bool = True) -> RelatorioInputs:
    """Modulo de hidrologia, por padrao com as tres figuras (IDF, hietograma, hidrograma)."""
    params = idf.IDFParams(K=1091.236, expoente_tr=0.17, expoente_duracao=0.7795, constante_duracao=11.67)
    tabela = idf.calcular_idf(params, duracoes_min=[5, 15, 60, 720], TRs=[2, 10, 100])
    hieto = hieto_mod.blocos_alternados(params, TR=100, duracao_total_min=30, dt_min=5)
    scs = hidro_mod.SCSParams(area_km2=0.46, tempo_concentracao_h=0.17, CN=75.0)
    hidro = hidro_mod.hidrograma_projeto(hieto, scs)
    inputs = RelatorioInputs(
        idf_params=params,
        idf_table=tabela,
        TR_anos=100,
        duracao_min=30,
        dt_min=5,
        metodo_hietograma="Blocos Alternados",
        hietograma=hieto,
        scs_params=scs,
        hidrograma=hidro,
    )
    if com_figuras:
        inputs.fig_idf = plots.plot_idf_curves(tabela)
        inputs.fig_hietograma = plots.plot_hietograma(hieto)
        inputs.fig_hidrograma = plots.plot_hietograma_hidrograma(hidro)
    return inputs


def _write_image_falho(self, *args, **kwargs):
    raise RuntimeError("\nImage export requires the Kaleido package, v1.0.0 or greater,\n")


def _kaleido_instalado() -> bool:
    """Falso so com o pacote ausente ou sem metadados (caso do venv local no Windows)."""
    try:
        return md.version("kaleido") is not None
    except (md.PackageNotFoundError, KeyError):
        return False


def _especificadores(manifesto: str) -> dict:
    texto = (RAIZ / manifesto).read_text(encoding="utf-8")
    if manifesto == "pyproject.toml":
        linhas = tomllib.loads(texto)["project"]["dependencies"]
    else:
        linhas = [linha.split("#")[0].strip() for linha in texto.splitlines()]
    return {r.name: r.specifier for r in map(Requirement, filter(None, linhas))}


def test_falha_na_exportacao_fica_registrada_sem_derrubar_o_pdf(monkeypatch):
    monkeypatch.setattr(go.Figure, "write_image", _write_image_falho)
    inputs = _inputs_hidrologia()

    pdf = gerar_relatorio_pdf(inputs)

    assert pdf.startswith(b"%PDF")
    assert len(inputs.figuras_com_falha) == 3
    assert inputs.figuras_com_falha[0].startswith("RuntimeError: Image export requires")


@pytest.mark.skipif(not _kaleido_instalado(), reason="kaleido nao instalado neste ambiente")
def test_figuras_entram_no_pdf():
    """Exportacao real: falha se plotly e kaleido instalados forem incompativeis."""
    inputs = _inputs_hidrologia()

    pdf = gerar_relatorio_pdf(inputs)
    sem_figuras = gerar_relatorio_pdf(_inputs_hidrologia(com_figuras=False))

    assert inputs.figuras_com_falha == []
    assert pdf.count(b"/Subtype /Image") - sem_figuras.count(b"/Subtype /Image") == 3


@pytest.mark.parametrize("manifesto", ["requirements.txt", "pyproject.toml"])
def test_manifesto_nao_admite_plotly_7_com_kaleido_antigo(manifesto):
    """Plotly 7 nao exporta imagem com kaleido<1; sem teto, o rebuild do Cloud tira as figuras."""
    deps = _especificadores(manifesto)
    if deps["kaleido"].contains("0.2.1"):
        assert not deps["plotly"].contains("7.0.0")


@pytest.mark.parametrize("metodo", ["Racional", "SCS-HU", "Distribuído"])
def test_pagina_exportar_com_cada_metodo(monkeypatch, metodo):
    """O botao Gerar PDF nao quebra em nenhum metodo; se a imagem falhar, a pagina avisa."""
    from streamlit.testing.v1 import AppTest  # noqa: PLC0415

    monkeypatch.setattr(go.Figure, "write_image", _write_image_falho)
    base = _inputs_hidrologia(com_figuras=False)
    hidro = base.hidrograma
    if metodo == "Racional":
        hidro = hidro.assign(hietograma_mm=0.0, excedente_mm=0.0)
    elif metodo == "Distribuído":
        hidro = hidro[["Q_m3s"]]

    at = AppTest.from_file(str(RAIZ / "chuva_vazao" / "app_pages" / "exportar.py"), default_timeout=60)
    at.session_state.idf_params = base.idf_params
    at.session_state.idf_table = base.idf_table
    at.session_state.hietograma = base.hietograma
    at.session_state.hidrograma = hidro
    at.session_state.metodo_chuva_vazao = metodo
    at.session_state.metodo_hietograma = base.metodo_hietograma
    at.session_state.TR = base.TR_anos
    at.session_state.duracao_min = base.duracao_min
    at.session_state.dt_min = base.dt_min
    at.session_state.scs_params = base.scs_params if metodo == "SCS-HU" else None
    at.run()
    next(b for b in at.button if b.label == "Gerar PDF").click().run()

    assert not at.exception
    assert "3 figura(s) ficaram fora do PDF" in at.error[0].value

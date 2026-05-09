"""Testes da equacao IDF (convencao i = K * TR^a / (t + b)^c)."""
from __future__ import annotations

import pytest

from chuva_vazao import idf


def test_intensidade_typical_brazil_tr10_60min():
    """Coeficientes tipicos do RJ -> i para 60min/TR10 fica em 50-70 mm/h."""
    # K=711.3, a=0.186, b=7 (cte min), c=0.687 (exp duracao) -> Bangu RJ
    params = idf.params_from_kabc(K=711.3, a=0.186, b=7.0, c=0.687)
    i = params.intensidade(TR=10, duracao_min=60)
    assert 50 <= i <= 70


def test_tabela_monotona():
    """Intensidade deve decrescer com a duracao e crescer com o TR."""
    params = idf.params_from_kabc(K=711.3, a=0.186, b=7.0, c=0.687)
    tabela = idf.calcular_idf(params, duracoes_min=[5, 15, 60, 720], TRs=[2, 10, 100])

    # Decrescente em duracao (para cada TR)
    for tr in tabela.columns:
        assert (tabela[tr].diff().dropna() < 0).all()
    # Crescente em TR (para cada duracao)
    for t in tabela.index:
        assert (tabela.loc[t].diff().dropna() > 0).all()


def test_altura_igual_intensidade_vezes_tempo():
    """h (mm) = i (mm/h) * t (h). Sanity check."""
    params = idf.IDFParams(K=100.0, expoente_tr=0.2, expoente_duracao=0.7, constante_duracao=10.0)
    i = params.intensidade(TR=10, duracao_min=60)
    h = idf.altura_mm(params, TR=10, duracao_min=60)
    assert h == pytest.approx(i * 60 / 60, rel=1e-9)


def test_tabela_idf_dimensoes():
    params = idf.IDFParams(K=100.0, expoente_tr=0.2, expoente_duracao=0.7, constante_duracao=10.0)
    tabela = idf.calcular_idf(params, duracoes_min=[5, 15, 60], TRs=[2, 10, 100])
    assert tabela.shape == (3, 3)
    assert list(tabela.columns) == [2, 10, 100]


def test_params_from_kabc_mapping():
    """params_from_kabc(K,a,b,c): b vai pra constante e c vai pra expoente."""
    p = idf.params_from_kabc(K=1000.0, a=0.18, b=10.0, c=0.75)
    assert p.K == 1000.0
    assert p.expoente_tr == 0.18
    assert p.constante_duracao == 10.0
    assert p.expoente_duracao == 0.75

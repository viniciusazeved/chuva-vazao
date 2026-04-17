"""Testes da equacao IDF e suas duas convencoes."""
from __future__ import annotations

import pytest

from chuva_vazao import db, idf


def test_intensidade_santa_cruz_tr10_60min_plausivel():
    """
    Santa Cruz RJ, TR=10, duracao=60min -> intensidade tipica brasileira
    de 50-70 mm/h para chuva de 1h TR=10.
    """
    coef = db.get_idf_coef("Santa Cruz")
    params = idf.params_from_convention(coef.K, coef.a, coef.b, coef.c)
    i = params.intensidade(TR=10, duracao_min=60)
    assert 50 <= i <= 70


def test_tabela_monotona():
    """Intensidade deve decrescer com a duracao e crescer com o TR."""
    coef = db.get_idf_coef("Santa Cruz")
    params = idf.params_from_convention(coef.K, coef.a, coef.b, coef.c)
    tabela = idf.calcular_idf(params, duracoes_min=[5, 15, 60, 720], TRs=[2, 10, 100])

    # Decrescente em duracao (para cada TR)
    for tr in tabela.columns:
        assert (tabela[tr].diff().dropna() < 0).all()
    # Crescente em TR (para cada duracao)
    for t in tabela.index:
        assert (tabela.loc[t].diff().dropna() > 0).all()


def test_convencao_hidroflu_vs_idf_generator_dao_resultados_distintos():
    """Mesmos (K,a,b,c) com convencoes distintas -> valores diferentes."""
    K, a, b, c = 711.3, 0.186, 0.687, 7.0
    p_hf = idf.params_from_convention(K, a, b, c, convention="hidroflu")
    p_idf = idf.params_from_convention(K, a, b, c, convention="idf_generator")
    i_hf = p_hf.intensidade(TR=10, duracao_min=60)
    i_idf = p_idf.intensidade(TR=10, duracao_min=60)
    assert i_hf != pytest.approx(i_idf, rel=1e-6)


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

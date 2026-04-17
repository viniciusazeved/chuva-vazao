"""Testes de conservacao e formato dos hietogramas."""
from __future__ import annotations

import pytest

from chuva_vazao import hietograma, idf


@pytest.fixture
def params_teste():
    return idf.IDFParams(K=711.3, expoente_tr=0.186, expoente_duracao=0.687, constante_duracao=7.0)


def test_blocos_alternados_conservacao(params_teste):
    """Soma das alturas do hietograma == i(TR, D) * D / 60 (conservacao)."""
    TR, D, dt = 10, 60, 5
    hieto = hietograma.blocos_alternados(params_teste, TR=TR, duracao_total_min=D, dt_min=dt)
    altura_esperada = params_teste.intensidade(TR, D) * D / 60.0
    assert hieto.sum() == pytest.approx(altura_esperada, rel=1e-6)


def test_blocos_alternados_pico_central(params_teste):
    """No metodo Chicago, o maximo deve cair proximo do centro do evento."""
    hieto = hietograma.blocos_alternados(params_teste, TR=10, duracao_total_min=120, dt_min=10)
    idx_max = hieto.values.argmax()
    n = len(hieto)
    # Pico deve estar no quartil central
    assert n // 4 < idx_max < 3 * n // 4


def test_huff_conservacao(params_teste):
    """Huff tambem deve conservar a altura total = i(TR,D) * D / 60."""
    TR, D, dt = 10, 60, 5
    hieto = hietograma.huff(params_teste, TR=TR, duracao_total_min=D, dt_min=dt, quartil=2)
    altura_esperada = params_teste.intensidade(TR, D) * D / 60.0
    assert hieto.sum() == pytest.approx(altura_esperada, rel=1e-6)


@pytest.mark.parametrize("quartil", [1, 2, 3, 4])
def test_huff_todos_quartis_positivos(params_teste, quartil):
    hieto = hietograma.huff(params_teste, TR=10, duracao_total_min=60, dt_min=5, quartil=quartil)
    assert (hieto >= 0).all()
    assert hieto.sum() > 0


def test_huff_quartil_invalido_lanca(params_teste):
    with pytest.raises(ValueError):
        hietograma.huff(params_teste, TR=10, duracao_total_min=60, dt_min=5, quartil=5)


def test_blocos_alternados_dt_invalido(params_teste):
    with pytest.raises(ValueError):
        hietograma.blocos_alternados(params_teste, TR=10, duracao_total_min=60, dt_min=0)
    with pytest.raises(ValueError):
        hietograma.blocos_alternados(params_teste, TR=10, duracao_total_min=5, dt_min=10)

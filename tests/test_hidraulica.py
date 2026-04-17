"""Testes Manning circular/retangular + dimensionamento."""
from __future__ import annotations

import math

import pytest

from chuva_vazao import hidraulica as h


def test_manning_full_formula_classica():
    """Q_cheia = (1/n) * (pi*D^2/4) * (D/4)^(2/3) * S^(1/2)."""
    r = h.manning_circular_full(D_m=1.0, S_m_per_m=0.01, n=0.013)
    # Calculo manual
    A = math.pi / 4
    R = 0.25
    v = (1 / 0.013) * R ** (2 / 3) * math.sqrt(0.01)
    Q = v * A
    assert r.v_m_s == pytest.approx(v, rel=1e-6)
    assert r.Q_m3_s == pytest.approx(Q, rel=1e-6)


def test_manning_partial_Q_menor_que_full():
    """Tubo parcialmente cheio deve ter Q < Q_full."""
    full = h.manning_circular_full(D_m=1.0, S_m_per_m=0.01, n=0.013)
    partial = h.manning_circular_partial(D_m=1.0, h_m=0.5, S_m_per_m=0.01, n=0.013)
    assert partial.Q_m3_s < full.Q_m3_s


def test_lamina_para_vazao_inverso_partial():
    """Se calculo h a partir de Q, e volto a calcular Q, bate."""
    Q_alvo = 0.5
    h_ach = h.lamina_para_vazao_circular(Q_alvo, D_m=1.0, S_m_per_m=0.01, n=0.013)
    Q_back = h.manning_circular_partial(1.0, h_ach, 0.01, 0.013).Q_m3_s
    assert Q_back == pytest.approx(Q_alvo, rel=1e-4)


def test_size_culvert_escolhe_menor_diametro_que_atende():
    """Q bem pequeno (0.02 m^3/s) deve caber na menor manilha comercial."""
    dim = h.size_circular_culvert(
        Q_projeto_m3_s=0.02, S_m_per_m=0.01, n=0.013,
    )
    assert dim.D_adotado_m == 0.30


def test_size_culvert_salta_diametro_quando_preciso():
    """Q=0.1 m^3/s em S=0.01 nao cabe em 0.30m (Q_full ~0.096); deve ir pra 0.40m."""
    dim = h.size_circular_culvert(
        Q_projeto_m3_s=0.1, S_m_per_m=0.01, n=0.013,
    )
    assert dim.D_adotado_m >= 0.40


def test_size_culvert_Q_grande_escolhe_maior():
    """Q maior -> diametro maior."""
    dim = h.size_circular_culvert(
        Q_projeto_m3_s=5.0, S_m_per_m=0.005, n=0.013,
    )
    assert dim.D_adotado_m >= 1.0


def test_size_culvert_Q_absurdo_lanca():
    with pytest.raises(ValueError):
        h.size_circular_culvert(
            Q_projeto_m3_s=10_000.0, S_m_per_m=0.005, n=0.013,
        )


def test_validar_velocidade_warnings():
    assert h.validar_velocidade(0.3) == [
        m for m in h.validar_velocidade(0.3) if "sedimenta" in m.lower()
    ]
    assert len(h.validar_velocidade(6.0)) >= 1
    assert h.validar_velocidade(2.0) == []


def test_manning_rectangular_fluxo_positivo():
    r = h.manning_rectangular(b_m=1.0, h_m=0.5, S_m_per_m=0.01, n=0.015)
    assert r.Q_m3_s > 0
    assert r.A_m2 == pytest.approx(0.5)
    assert r.P_m == pytest.approx(2.0)


def test_manning_n_tabela_tem_materiais_chave():
    assert "Concreto liso (manilha)" in h.MANNING_N
    assert h.MANNING_N["Concreto liso (manilha)"] == pytest.approx(0.013)

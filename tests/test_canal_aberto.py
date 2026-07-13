"""Testes do dimensionamento de canal aberto (canal_aberto.py)."""
import math

import pytest

from chuva_vazao import canal_aberto as ca


def test_geometria_trapezoidal():
    g = ca.geometria_trapezoidal(b_m=8.0, z=1.0, y_m=2.0)
    assert g.A_m2 == pytest.approx((8.0 + 1.0 * 2.0) * 2.0)      # 20
    assert g.P_m == pytest.approx(8.0 + 2 * 2.0 * math.sqrt(2))  # 8+5.657
    assert g.T_m == pytest.approx(8.0 + 2 * 1.0 * 2.0)           # 12
    assert g.R_m == pytest.approx(g.A_m2 / g.P_m)


def test_retangular_e_caso_limite_z0():
    g = ca.geometria_trapezoidal(b_m=5.0, z=0.0, y_m=2.0)
    assert g.A_m2 == pytest.approx(10.0)
    assert g.P_m == pytest.approx(5.0 + 2 * 2.0)  # b + 2y
    assert g.T_m == pytest.approx(5.0)            # topo = base


def test_manning_e_lamina_normal_sao_inversos():
    # Para um y conhecido, calcula Q; lamina_normal(Q) deve recuperar o y.
    b, z, S, n, y = 8.0, 1.5, 0.01, 0.027, 2.4
    g = ca.geometria_trapezoidal(b, z, y)
    Q = ca.manning_Q(g.A_m2, g.R_m, S, n)
    y_rec = ca.lamina_normal_trapezoidal(Q, b, z, S, n)
    assert y_rec == pytest.approx(y, rel=1e-4)


def test_froude_e_regime():
    assert ca.regime(0.5) == "subcrítico"
    assert ca.regime(1.0) == "crítico"
    assert ca.regime(1.8) == "supercrítico"


def test_lamina_normal_erra_se_secao_nao_atende():
    # Vazao enorme numa secao minuscula e declividade baixa -> nao atende.
    with pytest.raises(ValueError):
        ca.lamina_normal_trapezoidal(5000.0, b_m=1.0, z=0.0, S=0.0005, n=0.03, y_max_m=5.0)


def test_tensao_trativa_lane():
    tf, tt = ca.tensoes_trativas(y_m=2.0, S=0.02)
    assert tf == pytest.approx(9810.0 * 2.0 * 0.02)      # gama.y.S
    assert tt == pytest.approx(0.76 * 9810.0 * 2.0 * 0.02)
    assert tt < tf


def test_fator_talude_ingreme_demais_para_enrocamento():
    # z=1 -> talude 45deg > repouso 41deg -> None (instavel p/ enrocamento solto)
    assert ca.fator_talude(1.0) is None
    # z=2 -> talude ~26.6deg < 41deg -> fator valido em (0,1)
    k = ca.fator_talude(2.0)
    assert k is not None and 0.0 < k < 1.0
    # parede vertical (retangular) nao tem componente de talude granular
    assert ca.fator_talude(0.0) == 1.0


def test_d50_shields_cresce_com_tensao():
    d1 = ca.d50_enrocamento_m(200.0)
    d2 = ca.d50_enrocamento_m(400.0)
    assert d2 == pytest.approx(2 * d1, rel=1e-6)
    # ordem de grandeza: 457 N/m2 -> ~0,6 m (caso Gratau S=2%)
    assert ca.d50_enrocamento_m(457.0) == pytest.approx(0.60, abs=0.05)


def test_dimensionar_gabiao_alerta_velocidade_no_caso_gratau():
    r = ca.dimensionar_canal_aberto(
        175.75, b_m=10.0, z=1.5, S=0.02, revestimento="Gabião caixa",
    )
    assert r.v_m_s > r.v_admissivel_m_s          # 6.85 > 5.5
    assert r.regime == "supercrítico"
    assert any("Velocidade" in w for w in r.warnings)
    assert any("supercrit" in w.lower() for w in r.warnings)


def test_dimensionar_gabiao_estavel_com_declividade_menor():
    r = ca.dimensionar_canal_aberto(
        175.75, b_m=10.0, z=1.5, S=0.005, revestimento="Gabião caixa",
    )
    assert r.v_m_s < r.v_admissivel_m_s          # ~4.2 < 5.5
    assert r.regime == "subcrítico"
    assert not any("Velocidade" in w for w in r.warnings)


def test_dimensionar_enrocamento_dimensiona_d50():
    r = ca.dimensionar_canal_aberto(
        175.75, b_m=12.0, z=2.0, S=0.005, revestimento="Enrocamento (rip-rap)",
    )
    assert r.d50_enrocamento_m is not None and r.d50_enrocamento_m > 0
    assert r.talude_estavel_enroc is True        # z=2 ok p/ enrocamento
    assert r.v_admissivel_m_s is None            # enrocamento nao usa v_adm


def test_enrocamento_talude_ingreme_avisa_instabilidade():
    r = ca.dimensionar_canal_aberto(
        175.75, b_m=10.0, z=1.0, S=0.01, revestimento="Enrocamento (rip-rap)",
    )
    assert r.talude_estavel_enroc is False
    assert any("angulo de repouso" in w for w in r.warnings)


def test_borda_livre_default_respeita_minimo():
    r = ca.dimensionar_canal_aberto(
        175.75, b_m=12.0, z=2.0, S=0.005, revestimento="Gabião caixa",
        borda_livre_min_m=0.5,
    )
    assert r.borda_livre_m >= 0.5 - 1e-9
    assert r.altura_total_m > r.y_op_m

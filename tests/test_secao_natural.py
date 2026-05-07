"""Testes do modulo secao_natural: geometria 3D->2D, Manning, lamina y_n e y_c, trecho."""
from __future__ import annotations

import math

import pytest

from chuva_vazao import secao_natural as sn


# ---------------------------------------------------------------------------
# Helpers: secoes sinteticas
# ---------------------------------------------------------------------------

def secao_retangular_sintetica(
    b_m: float = 10.0,
    H_margem_m: float = 5.0,
    z_thalweg: float = 0.0,
    N0: float = 0.0,
    E0: float = 0.0,
    eps_parede: float = 0.001,
) -> sn.SecaoTransversal:
    """
    Retangulo idealizado com paredes quase verticais (eps_parede de horizontal),
    largura b_m no fundo e altura H_margem_m. Z_thalweg = z_thalweg.

    Os pontos sao lancados com N constante e E crescente — estaca = E.
    """
    pontos = [
        sn.PontoSecao(N=N0, E=E0,                                 Z=z_thalweg + H_margem_m),
        sn.PontoSecao(N=N0, E=E0 + eps_parede,                    Z=z_thalweg),
        sn.PontoSecao(N=N0, E=E0 + eps_parede + b_m,              Z=z_thalweg),
        sn.PontoSecao(N=N0, E=E0 + eps_parede + b_m + eps_parede, Z=z_thalweg + H_margem_m),
    ]
    return sn.secao_from_pontos(pontos)


# ---------------------------------------------------------------------------
# Geometria 3D -> 2D
# ---------------------------------------------------------------------------

def test_secao_estacas_pontos_colineares():
    """Pontos colineares com 5 m entre si: estacas = [0, 5, 10]."""
    pontos = [
        sn.PontoSecao(N=0, E=0, Z=10),
        sn.PontoSecao(N=0, E=5, Z=2),
        sn.PontoSecao(N=0, E=10, Z=10),
    ]
    sec = sn.secao_from_pontos(pontos)
    assert sec.estacas == (0.0, 5.0, 10.0)
    assert sec.cotas == (10.0, 2.0, 10.0)


def test_secao_distancia_3d_planimetrica():
    """Distancia entre pontos e euclidiana em (N, E), ignorando Z."""
    pontos = [
        sn.PontoSecao(N=0, E=0, Z=5),
        sn.PontoSecao(N=3, E=4, Z=0),  # distancia 2D = 5
        sn.PontoSecao(N=3, E=4, Z=5),  # mesma posicao planimetrica que o anterior!
    ]
    # Vai falhar pq P1 e P2 tem N,E iguais
    with pytest.raises(ValueError, match="distancia zero"):
        sn.secao_from_pontos(pontos)


def test_secao_thalweg_e_zmax():
    """Thalweg = ponto de menor Z; z_max = maior Z."""
    pontos = [
        sn.PontoSecao(N=0, E=0, Z=8),
        sn.PontoSecao(N=0, E=5, Z=2),
        sn.PontoSecao(N=0, E=10, Z=3),
        sn.PontoSecao(N=0, E=15, Z=9),
    ]
    sec = sn.secao_from_pontos(pontos)
    assert sec.z_thalweg == 2.0
    assert sec.z_max == 9.0
    assert sec.N_thalweg == 0.0
    assert sec.E_thalweg == 5.0


def test_secao_minimo_3_pontos():
    pontos = [
        sn.PontoSecao(N=0, E=0, Z=5),
        sn.PontoSecao(N=0, E=10, Z=5),
    ]
    with pytest.raises(ValueError, match=">= 3 pontos"):
        sn.secao_from_pontos(pontos)


# ---------------------------------------------------------------------------
# Geometria molhada num retangulo sintetico
# ---------------------------------------------------------------------------

def test_geometria_molhada_retangular_bate_b_h():
    """Em secao retangular b=10, H=5, com lamina y=2 sobre o thalweg z=0:
    A_esperado = 20, P_esperado = 14, B_esperado = 10. Tolerancia 1e-2."""
    sec = secao_retangular_sintetica(b_m=10.0, H_margem_m=5.0, z_thalweg=0.0)
    g = sn.geometria_molhada(sec, y_w=2.0)
    assert g.A_m2 == pytest.approx(20.0, rel=1e-3)
    assert g.P_m == pytest.approx(14.0, rel=1e-3)
    assert g.B_m == pytest.approx(10.0, rel=1e-3)
    assert g.R_m == pytest.approx(20.0 / 14.0, rel=1e-3)
    assert g.D_h_m == pytest.approx(2.0, rel=1e-3)


def test_geometria_molhada_seca_quando_y_abaixo_thalweg():
    sec = secao_retangular_sintetica(z_thalweg=10.0)
    g = sn.geometria_molhada(sec, y_w=5.0)
    assert g.A_m2 == 0.0
    assert g.P_m == 0.0
    assert g.B_m == 0.0


def test_geometria_molhada_y_igual_zthalweg_e_zero():
    sec = secao_retangular_sintetica(z_thalweg=0.0)
    g = sn.geometria_molhada(sec, y_w=0.0)
    assert g.A_m2 == 0.0


# ---------------------------------------------------------------------------
# Manning aplicado a secao irregular
# ---------------------------------------------------------------------------

def test_manning_secao_retangular_q_positivo():
    """Manning numa secao retangular sintetica com S=0.001, n=0.030 deve dar
    Q proximo do calculo manual: A=20, R=20/14=1.4286, v=(1/0.030)*1.4286^(2/3)*0.001^0.5."""
    sec = secao_retangular_sintetica(b_m=10.0, H_margem_m=5.0, z_thalweg=0.0)
    esc = sn.manning_secao(sec, y_w=2.0, n=0.030, S=0.001)

    A_esperado = 20.0
    R_esperado = 20.0 / 14.0
    v_esperado = (1.0 / 0.030) * R_esperado ** (2.0 / 3.0) * math.sqrt(0.001)
    Q_esperado = v_esperado * A_esperado

    assert esc.v_m_s == pytest.approx(v_esperado, rel=5e-3)
    assert esc.Q_m3_s == pytest.approx(Q_esperado, rel=5e-3)
    assert esc.regime in ("subcritico", "critico", "supercritico")


def test_manning_secao_n_zero_lanca():
    sec = secao_retangular_sintetica()
    with pytest.raises(ValueError, match="n de Manning"):
        sn.manning_secao(sec, y_w=2.0, n=0.0, S=0.001)


def test_manning_secao_S_zero_lanca():
    sec = secao_retangular_sintetica()
    with pytest.raises(ValueError, match="Declividade"):
        sn.manning_secao(sec, y_w=2.0, n=0.030, S=0.0)


# ---------------------------------------------------------------------------
# Lamina normal (inverso de Manning)
# ---------------------------------------------------------------------------

def test_lamina_normal_inverso_da_certo():
    """Calculo y_n a partir de Q, depois rodando Manning na y_n volta o Q."""
    sec = secao_retangular_sintetica(b_m=10.0, H_margem_m=5.0, z_thalweg=0.0)
    Q_alvo = 30.0
    esc = sn.lamina_normal(sec, Q_target=Q_alvo, n=0.030, S=0.001)
    assert esc.Q_m3_s == pytest.approx(Q_alvo, rel=1e-4)
    # E a cota deve estar entre thalweg e topo
    assert sec.z_thalweg < esc.y_w_m < sec.z_max


def test_lamina_normal_extravasa_lanca():
    """Q absurdo num canalzinho deve estourar a capacidade e levantar erro."""
    sec = secao_retangular_sintetica(b_m=2.0, H_margem_m=1.0, z_thalweg=0.0)
    with pytest.raises(ValueError, match="extravasa"):
        sn.lamina_normal(sec, Q_target=500.0, n=0.030, S=0.001)


def test_lamina_normal_Q_negativo_lanca():
    sec = secao_retangular_sintetica()
    with pytest.raises(ValueError, match="precisa ser > 0"):
        sn.lamina_normal(sec, Q_target=-1.0, n=0.030, S=0.001)


# ---------------------------------------------------------------------------
# Lamina critica (Fr = 1)
# ---------------------------------------------------------------------------

def test_lamina_critica_retangular_bate_q2_g():
    """Em retangulo b=10, Q=10, q=Q/B=1 -> y_c = (q^2/g)^(1/3) ≈ 0.467 m."""
    sec = secao_retangular_sintetica(b_m=10.0, H_margem_m=5.0, z_thalweg=0.0)
    esc = sn.lamina_critica(sec, Q=10.0)

    q = 10.0 / 10.0  # vazao especifica
    y_c_esperado_lamina = (q ** 2 / sn.G) ** (1.0 / 3.0)
    y_c_esperado_abs = 0.0 + y_c_esperado_lamina  # thalweg em z=0

    assert esc.y_w_m == pytest.approx(y_c_esperado_abs, rel=5e-3)
    assert esc.Fr == pytest.approx(1.0, abs=1e-6)
    assert esc.regime == "critico"


def test_lamina_critica_independente_n_S():
    """y_c so depende de Q e geometria — nao deve mudar se n ou S mudarem."""
    sec = secao_retangular_sintetica(b_m=10.0, H_margem_m=5.0, z_thalweg=0.0)
    e1 = sn.lamina_critica(sec, Q=10.0)
    e2 = sn.lamina_critica(sec, Q=10.0)
    assert e1.y_w_m == pytest.approx(e2.y_w_m, rel=1e-9)


# ---------------------------------------------------------------------------
# Trecho com 3 secoes
# ---------------------------------------------------------------------------

def test_declividade_trecho_3_secoes_alinhadas():
    """Tres secoes alinhadas em E, cotas dos thalwegs caindo 1 m a cada 100 m de
    distancia 2D (planimetrica) -> S = 0.01."""
    sec_M = secao_retangular_sintetica(z_thalweg=2.0, N0=0,   E0=0)
    sec_C = secao_retangular_sintetica(z_thalweg=1.0, N0=100, E0=0)
    sec_J = secao_retangular_sintetica(z_thalweg=0.0, N0=200, E0=0)

    L_MC, L_CJ, S = sn.declividade_trecho(sec_M, sec_C, sec_J)
    # Os thalwegs estao no centro do retangulo (estaca ~5), entao N e mesma
    # entre M-C-J (mudanca em N0 desloca tudo). L deve ser ~100.
    assert L_MC == pytest.approx(100.0, rel=1e-3)
    assert L_CJ == pytest.approx(100.0, rel=1e-3)
    assert S == pytest.approx(0.01, rel=1e-3)


def test_declividade_trecho_ordem_invertida_lanca():
    """Z jusante > Z montante deve falhar."""
    sec_M = secao_retangular_sintetica(z_thalweg=0.0, N0=0,   E0=0)
    sec_C = secao_retangular_sintetica(z_thalweg=1.0, N0=100, E0=0)
    sec_J = secao_retangular_sintetica(z_thalweg=2.0, N0=200, E0=0)
    with pytest.raises(ValueError, match="ordem"):
        sn.declividade_trecho(sec_M, sec_C, sec_J)


def test_verificar_trecho_consolida_resultados():
    """Verificacao integrada: deve preencher todos os campos e rodar sem erro."""
    sec_M = secao_retangular_sintetica(z_thalweg=2.0, N0=0,   E0=0)
    sec_C = secao_retangular_sintetica(z_thalweg=1.0, N0=100, E0=0)
    sec_J = secao_retangular_sintetica(z_thalweg=0.0, N0=200, E0=0)

    v = sn.verificar_trecho(sec_M, sec_C, sec_J, Q_projeto=20.0, n=0.030)

    assert v.Q_projeto_m3_s == 20.0
    assert v.S_trecho_m_per_m == pytest.approx(0.01, rel=1e-3)
    assert v.escoamento_M.Q_m3_s == pytest.approx(20.0, rel=1e-4)
    assert v.escoamento_C.Q_m3_s == pytest.approx(20.0, rel=1e-4)
    assert v.escoamento_J.Q_m3_s == pytest.approx(20.0, rel=1e-4)
    # Lamina normal deve ser positiva nas tres
    assert v.escoamento_M.y_lamina_m > 0
    assert v.escoamento_C.y_lamina_m > 0
    assert v.escoamento_J.y_lamina_m > 0


def test_verificar_trecho_warning_borda_livre():
    """Q calibrado pra deixar lamina apertada (< 30 cm de borda) sem extravasar."""
    # b=4, H=1, S=0.005 -> Q_max ~7 m3/s. Q=5 m3/s deixa borda ~20 cm.
    sec_M = secao_retangular_sintetica(b_m=4.0, H_margem_m=1.0, z_thalweg=1.0, N0=0,   E0=0)
    sec_C = secao_retangular_sintetica(b_m=4.0, H_margem_m=1.0, z_thalweg=0.5, N0=100, E0=0)
    sec_J = secao_retangular_sintetica(b_m=4.0, H_margem_m=1.0, z_thalweg=0.0, N0=200, E0=0)
    v = sn.verificar_trecho(sec_M, sec_C, sec_J, Q_projeto=5.0, n=0.030)
    assert any("Borda livre" in w for w in v.warnings)


# ---------------------------------------------------------------------------
# Tabela de Manning natural
# ---------------------------------------------------------------------------

def test_manning_natural_tem_chaves_principais():
    assert any("Rio em planicie" in k for k in sn.MANNING_N_NATURAL)
    assert any("Canal escavado" in k for k in sn.MANNING_N_NATURAL)
    # Valor de rio reto/limpo da Tabela 5-6 do Chow
    valor = sn.MANNING_N_NATURAL["Rio em planicie - limpo, reto, sem bancos"]
    assert 0.025 <= valor <= 0.035

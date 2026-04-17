"""Testes de conservacao e formato do hidrograma."""
from __future__ import annotations

import numpy as np
import pytest

from chuva_vazao import hidrograma, hietograma, idf


@pytest.fixture
def params_teste():
    return idf.IDFParams(K=711.3, expoente_tr=0.186, expoente_duracao=0.687, constante_duracao=7.0)


@pytest.fixture
def scs_teste():
    return hidrograma.SCSParams(area_km2=10.0, tempo_concentracao_h=1.0, CN=75.0)


def test_S_e_Ia(scs_teste):
    """S = 25400/CN - 254; Ia = 0.2 * S."""
    assert scs_teste.S_mm == pytest.approx(25400 / 75 - 254, rel=1e-6)
    assert scs_teste.Ia_mm == pytest.approx(0.2 * scs_teste.S_mm, rel=1e-6)


def test_escoamento_direto_zero_quando_menor_que_Ia(scs_teste):
    """P <= Ia -> Q = 0."""
    assert hidrograma.escoamento_direto_scs(5.0, scs_teste) == 0.0


def test_escoamento_direto_positivo_quando_maior_que_Ia(scs_teste):
    assert hidrograma.escoamento_direto_scs(100.0, scs_teste) > 0


def test_uh_triangular_area_igual_volume_unitario():
    """
    Integral da UH sob 1 mm de chuva = area * 1 mm (em m^3).
    Para A = 10 km^2 e 1 mm -> 10_000 m^3.
    """
    uh = hidrograma.uh_triangular_scs(
        area_km2=10.0, tempo_concentracao_h=1.0, duracao_chuva_min=5,
    )
    # Integral analitica da triangular
    area_uh = 0.5 * uh.t_base_h * 3600.0 * uh.Q_pico_m3s_por_mm  # m^3/mm
    assert area_uh == pytest.approx(10_000.0, rel=0.01)


def test_volume_conservacao(params_teste, scs_teste):
    """Volume integrado do hidrograma == Q_excedente_total * A * 1000."""
    hieto = hietograma.blocos_alternados(params_teste, TR=10, duracao_total_min=60, dt_min=5)
    excedente = hidrograma.chuva_excedente(hieto, scs_teste)
    V_esperado = float(excedente.sum()) * scs_teste.area_km2 * 1000.0  # mm * km^2 * 1000 = m^3

    hg = hidrograma.hidrograma_projeto(hieto, scs_teste)
    V_integrado = hidrograma.volume_escoado_m3(hg)
    assert V_integrado == pytest.approx(V_esperado, rel=0.01)


def test_hidrograma_qpico_positivo(params_teste, scs_teste):
    hieto = hietograma.blocos_alternados(params_teste, TR=10, duracao_total_min=60, dt_min=5)
    hg = hidrograma.hidrograma_projeto(hieto, scs_teste)
    assert hidrograma.Q_pico_m3s(hg) > 0
    assert hidrograma.tempo_ao_pico_min(hg) > 0


def test_CN_fora_do_intervalo_lanca():
    with pytest.raises(ValueError):
        hidrograma.SCSParams(area_km2=1.0, tempo_concentracao_h=1.0, CN=0).S_mm
    with pytest.raises(ValueError):
        hidrograma.SCSParams(area_km2=1.0, tempo_concentracao_h=1.0, CN=101).S_mm


def test_cn_100_escoamento_quase_total(scs_teste):
    """CN alto (100) -> S ~ 0 -> quase todo P vira runoff."""
    scs_alto = hidrograma.SCSParams(area_km2=1.0, tempo_concentracao_h=0.5, CN=99.0)
    P = 100.0
    Q = hidrograma.escoamento_direto_scs(P, scs_alto)
    assert Q > 90.0

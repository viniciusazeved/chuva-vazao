"""Testes do roteamento Puls em reservatorio de detencao."""
from __future__ import annotations

import math

import numpy as np
import pytest

from chuva_vazao import detencao


def test_orificio_lei_potencia():
    """Q cresce com sqrt(h)."""
    Q1 = detencao.orificio(Cd=0.61, A_m2=0.1, h_eff_m=1.0)
    Q4 = detencao.orificio(Cd=0.61, A_m2=0.1, h_eff_m=4.0)
    assert Q4 == pytest.approx(2 * Q1, rel=1e-4)


def test_orificio_h_zero_ou_negativo_zero_vazao():
    assert detencao.orificio(0.61, 0.1, 0) == 0
    assert detencao.orificio(0.61, 0.1, -0.5) == 0


def test_vertedor_formula_classica():
    """Q = Cw * b * h^(3/2)."""
    Q = detencao.vertedor_retangular(Cw=1.85, b_m=2.0, h_over_weir_m=0.5)
    esperado = 1.85 * 2.0 * (0.5 ** 1.5)
    assert Q == pytest.approx(esperado, rel=1e-6)


def test_reservatorio_combina_orificio_e_vertedor():
    res = detencao.Reservatorio(
        Aw_m2=1000.0, h_max_m=3.0,
        z_orificio_m=0.0, d_orificio_m=0.3,
        z_vertedor_m=2.0, b_vertedor_m=3.0,
    )
    # Na cota 1.0m, so orificio ativo
    Q1 = res.vazao_saida(h_m=1.0)
    # Na cota 2.5m, orificio + vertedor com h=0.5 sobre crista
    Q25 = res.vazao_saida(h_m=2.5)
    assert Q25 > Q1


def test_puls_atenua_hidrograma_triangular():
    """
    Reservatorio amplo deve atenuar pico > 30% para hidrograma triangular.
    """
    res = detencao.Reservatorio(
        Aw_m2=5000.0, h_max_m=4.0,
        z_orificio_m=0.0, d_orificio_m=0.20,
        z_vertedor_m=3.0, b_vertedor_m=4.0,
    )
    # Hidrograma triangular: 0 -> 10 -> 0 em 60min
    t = np.arange(0, 61, 1)
    inflow = np.where(t <= 30, t / 30 * 10, (60 - t) / 30 * 10)
    inflow = np.maximum(inflow, 0)

    r = detencao.puls_routing(inflow, dt_min=1, reservatorio=res)
    assert r.atenuacao_pct > 30
    assert r.Qp_out_m3_s < r.Qp_in_m3_s


def test_puls_conservacao_de_volume_no_tempo_longo():
    """
    Simulacao longa com descarga estavel: V_in ~= V_out + V_armazenado_final.
    """
    res = detencao.Reservatorio(
        Aw_m2=500.0, h_max_m=4.0,
        z_orificio_m=0.0, d_orificio_m=0.3,
        z_vertedor_m=3.0, b_vertedor_m=2.0,
    )
    # Hidrograma 30min triangular
    dt = 1
    t = np.arange(0, 120, dt)
    inflow = np.where(t <= 15, t / 15 * 5,
                     np.where(t <= 30, (30 - t) / 15 * 5, 0.0))
    inflow = np.maximum(inflow, 0)

    r = detencao.puls_routing(inflow, dt_min=dt, reservatorio=res)
    dt_s = dt * 60
    V_in = float(np.trapezoid(r.inflow_m3_s, dx=dt_s))
    V_out = float(np.trapezoid(r.outflow_m3_s, dx=dt_s))
    V_arm_final = float(r.S_m3[-1])
    # Balanco: V_in = V_out + V_armazenado_final (dentro de tolerancia do metodo)
    assert V_in == pytest.approx(V_out + V_arm_final, rel=0.05)


def test_build_storage_discharge_table_monotonica():
    res = detencao.Reservatorio(
        Aw_m2=1000.0, h_max_m=3.0,
        z_orificio_m=0.0, d_orificio_m=0.3,
        z_vertedor_m=2.5, b_vertedor_m=2.0,
    )
    tabela = detencao.build_storage_discharge_table(res)
    # S cresce monotonicamente com h
    assert (tabela["S_m3"].diff().dropna() >= 0).all()
    # O cresce monotonicamente com h
    assert (tabela["O_m3_s"].diff().dropna() >= 0).all()

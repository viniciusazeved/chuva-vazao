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


# ---------------------------------------------------------------------------
# Curva cota-volume tabulada
# ---------------------------------------------------------------------------

def test_curva_cota_volume_interpola_volume():
    """V(h) na curva tabulada deve casar nos pontos e interpolar linear."""
    res = detencao.Reservatorio(
        Aw_m2=0.0, h_max_m=4.0,
        z_orificio_m=0.0, d_orificio_m=0.3,
        z_vertedor_m=3.0, b_vertedor_m=2.0,
        cota_volume_h_m=(0.0, 1.0, 2.0, 3.0, 4.0),
        cota_volume_v_m3=(0.0, 1500.0, 4000.0, 7500.0, 12000.0),
        z_fundo_m=100.0,
        datum_vertical="Imbituba (SGB)",
    )
    assert res.usa_curva_tabulada is True
    # Pontos exatos
    assert res.volume(0.0) == pytest.approx(0.0)
    assert res.volume(2.0) == pytest.approx(4000.0)
    assert res.volume(4.0) == pytest.approx(12000.0)
    # Interpolacao linear entre h=2 e h=3 (V vai de 4000 a 7500)
    assert res.volume(2.5) == pytest.approx((4000.0 + 7500.0) / 2.0)
    # z_max_abs = z_fundo + h_max
    assert res.z_max_abs_m == pytest.approx(104.0)


def test_curva_cota_volume_validacoes():
    base = dict(
        Aw_m2=0.0, h_max_m=4.0,
        z_orificio_m=0.0, d_orificio_m=0.3,
        z_vertedor_m=3.0, b_vertedor_m=2.0,
    )
    # cotas decrescentes
    with pytest.raises(ValueError, match="estritamente crescentes"):
        detencao.Reservatorio(
            **base,
            cota_volume_h_m=(0.0, 2.0, 1.0),
            cota_volume_v_m3=(0.0, 1000.0, 500.0),
        )
    # volumes nao-monotonos
    with pytest.raises(ValueError, match="nao-decrescentes"):
        detencao.Reservatorio(
            **base,
            cota_volume_h_m=(0.0, 1.0, 2.0),
            cota_volume_v_m3=(0.0, 1000.0, 500.0),
        )
    # par incompleto
    with pytest.raises(ValueError, match="ambos"):
        detencao.Reservatorio(
            **base,
            cota_volume_h_m=(0.0, 1.0),
            cota_volume_v_m3=(),
        )
    # poucos pontos
    with pytest.raises(ValueError, match="2 pontos"):
        detencao.Reservatorio(
            **base,
            cota_volume_h_m=(0.0,),
            cota_volume_v_m3=(0.0,),
        )


def test_curva_cota_volume_atenua_diferente_de_prismatico():
    """
    Curva cota-volume com forma de cone (V cresce com h^2) deve produzir
    laminas diferentes de um prismatico de mesmo volume total — porque a
    relacao V(h) e que diferencia o roteamento.
    """
    h_pts = (0.0, 1.0, 2.0, 3.0, 4.0)
    # Cone: V = k * h^2 -> reservatorio "raso" no inicio
    V_pts = tuple(500.0 * h ** 2 for h in h_pts)  # 0, 500, 2000, 4500, 8000
    res_curva = detencao.Reservatorio(
        Aw_m2=0.0, h_max_m=4.0,
        z_orificio_m=0.0, d_orificio_m=0.20,
        z_vertedor_m=3.5, b_vertedor_m=2.0,
        cota_volume_h_m=h_pts,
        cota_volume_v_m3=V_pts,
    )
    # Prismatico com mesmo volume total na lamina maxima (Aw = V_max / h_max = 2000)
    res_prism = detencao.Reservatorio(
        Aw_m2=2000.0, h_max_m=4.0,
        z_orificio_m=0.0, d_orificio_m=0.20,
        z_vertedor_m=3.5, b_vertedor_m=2.0,
    )

    # Hidrograma triangular pequeno o suficiente pra nao extravasar
    t = np.arange(0, 61, 1)
    inflow = np.where(t <= 30, t / 30 * 5, (60 - t) / 30 * 5)
    inflow = np.maximum(inflow, 0)

    r_curva = detencao.puls_routing(inflow, dt_min=1, reservatorio=res_curva)
    r_prism = detencao.puls_routing(inflow, dt_min=1, reservatorio=res_prism)

    # Ambos devem atenuar
    assert r_curva.atenuacao_pct > 0
    assert r_prism.atenuacao_pct > 0
    # Cone e prismatico devem dar laminas distintas (V(h) diferente)
    assert not np.allclose(r_curva.h_m, r_prism.h_m, atol=0.05)


def test_curva_cota_volume_z_fundo_nao_afeta_resultado():
    """z_fundo_m e datum_vertical sao anotacoes — nao alteram o roteamento."""
    h_pts = (0.0, 1.0, 2.0, 3.0)
    V_pts = (0.0, 1000.0, 3000.0, 6000.0)
    kwargs = dict(
        Aw_m2=0.0, h_max_m=3.0,
        z_orificio_m=0.0, d_orificio_m=0.25,
        z_vertedor_m=2.5, b_vertedor_m=2.0,
        cota_volume_h_m=h_pts,
        cota_volume_v_m3=V_pts,
    )
    r0 = detencao.Reservatorio(**kwargs, z_fundo_m=0.0)
    r1 = detencao.Reservatorio(**kwargs, z_fundo_m=523.42, datum_vertical="Imbituba")

    t = np.arange(0, 61, 1)
    inflow = np.where(t <= 30, t / 30 * 8, (60 - t) / 30 * 8)
    inflow = np.maximum(inflow, 0)

    out0 = detencao.puls_routing(inflow, dt_min=1, reservatorio=r0)
    out1 = detencao.puls_routing(inflow, dt_min=1, reservatorio=r1)
    np.testing.assert_allclose(out0.outflow_m3_s, out1.outflow_m3_s)
    np.testing.assert_allclose(out0.h_m, out1.h_m)

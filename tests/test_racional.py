"""Metodo Racional + select_method + hidrograma triangular sintetico."""
from __future__ import annotations

import pytest

from chuva_vazao import hidrograma


def test_racional_magnitude_tipica():
    """C=0.5, i=100 mm/h, A=1 km^2 -> Q ~ 13.89 m^3/s."""
    Q = hidrograma.rational_method(C=0.5, i_mmh=100, A_km2=1.0)
    assert Q == pytest.approx(13.889, rel=1e-3)


def test_racional_escala_linear_com_C_i_A():
    """Dobrar C, i, ou A, dobra Q."""
    base = hidrograma.rational_method(0.3, 80, 0.5)
    assert hidrograma.rational_method(0.6, 80, 0.5) == pytest.approx(2 * base, rel=1e-6)
    assert hidrograma.rational_method(0.3, 160, 0.5) == pytest.approx(2 * base, rel=1e-6)
    assert hidrograma.rational_method(0.3, 80, 1.0) == pytest.approx(2 * base, rel=1e-6)


def test_racional_C_invalido_lanca():
    with pytest.raises(ValueError):
        hidrograma.rational_method(C=1.5, i_mmh=100, A_km2=1.0)
    with pytest.raises(ValueError):
        hidrograma.rational_method(C=-0.1, i_mmh=100, A_km2=1.0)


def test_select_method_por_area():
    assert hidrograma.select_method(0.5) == "Racional"
    assert hidrograma.select_method(2.0) == "Racional"
    assert hidrograma.select_method(2.5) == "SCS-HU"
    assert hidrograma.select_method(250.0) == "SCS-HU"
    assert hidrograma.select_method(500.0).startswith("Modelo distribuido")


def test_C_uso_solo_tem_chaves():
    assert "Asfalto" in hidrograma.C_USO_SOLO
    assert 0 < hidrograma.C_USO_SOLO["Asfalto"] <= 1
    assert all(0 <= c <= 1 for c in hidrograma.C_USO_SOLO.values())


def test_hidrograma_triangular_conserva_volume():
    """Volume = 0.5 * t_base * Qp (em segundos)."""
    tc_min = 30
    Qp = 10.0
    hg = hidrograma.hidrograma_triangular_sintetico(Qp_m3_s=Qp, tc_min=tc_min, dt_min=1)
    # Volume numerico
    import numpy as np
    dt_s = 60
    V_num = float(np.trapezoid(hg["Q_m3s"].values, dx=dt_s))
    V_ana = 0.5 * (2.67 * tc_min * 60) * Qp
    assert V_num == pytest.approx(V_ana, rel=0.01)

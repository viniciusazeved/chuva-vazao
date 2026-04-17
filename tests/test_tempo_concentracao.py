"""Testes das formulas de tempo de concentracao."""
from __future__ import annotations

import pytest

from chuva_vazao import tempo_concentracao as tc


def test_kirpich_valores_razoaveis():
    """Bacia de L=1km, S=0.02 -> tc ~15-30 min (range tipico)."""
    v = tc.kirpich(L_km=1.0, S_m_per_m=0.02)
    assert 10 < v < 40


def test_ven_te_chow_valores_razoaveis():
    v = tc.ven_te_chow(L_km=1.0, S_m_per_m=0.02)
    assert 10 < v < 60


def test_california_valores_razoaveis():
    v = tc.california(L_km=1.0, H_m=20.0)  # S=0.02
    assert 10 < v < 60


def test_tc_completo_tem_tres_metodos_e_media():
    result = tc.tempo_concentracao_completo(L_km=2.0, H_m=50.0)
    d = result.to_dict()
    assert set(d.keys()) == {"Kirpich", "Ven Te Chow", "California", "Media"}
    assert d["Media"] == pytest.approx(
        (d["Kirpich"] + d["Ven Te Chow"] + d["California"]) / 3, rel=1e-6,
    )


def test_tc_cresce_com_L():
    """Bacia mais longa -> tc maior (com mesma declividade)."""
    v1 = tc.kirpich(L_km=1.0, S_m_per_m=0.02)
    v2 = tc.kirpich(L_km=5.0, S_m_per_m=0.02)
    assert v2 > v1


def test_tc_diminui_com_S():
    """Terreno mais ingreme -> tc menor."""
    v1 = tc.kirpich(L_km=1.0, S_m_per_m=0.02)
    v2 = tc.kirpich(L_km=1.0, S_m_per_m=0.10)
    assert v2 < v1


def test_tc_invalido_lanca():
    with pytest.raises(ValueError):
        tc.kirpich(L_km=0, S_m_per_m=0.02)
    with pytest.raises(ValueError):
        tc.kirpich(L_km=1.0, S_m_per_m=0)
    with pytest.raises(ValueError):
        tc.california(L_km=1.0, H_m=0)

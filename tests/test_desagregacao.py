"""Testes da desagregacao DNAEE e Pfafstetter."""
from __future__ import annotations

import pytest

from chuva_vazao import db, desagregacao


def test_dnaee_dia_para_1440_fator_weiss():
    """P_1440 = 1.14 * P_1dia (Weiss 1964)."""
    depths = desagregacao.desagregar_dnaee(100.0)
    assert depths[1440] == pytest.approx(114.0, rel=1e-6)


def test_dnaee_cobre_todas_duracoes():
    depths = desagregacao.desagregar_dnaee(100.0)
    assert set(depths.keys()) == set(desagregacao.DURATIONS_MIN)


def test_dnaee_monotonico():
    """Alturas crescem com a duracao."""
    depths = desagregacao.desagregar_dnaee(100.0)
    duracoes_ordenadas = sorted(depths.keys())
    alturas = [depths[d] for d in duracoes_ordenadas]
    assert alturas == sorted(alturas)


def test_altura_para_intensidade():
    depths = {60: 30.0, 120: 50.0}
    intensidades = desagregacao.altura_para_intensidade(depths)
    assert intensidades[60] == pytest.approx(30.0, rel=1e-6)
    assert intensidades[120] == pytest.approx(25.0, rel=1e-6)


def test_desagregar_usa_pfafstetter_quando_coef_fornecido():
    coef = db.get_pfafstetter_coef("Rio de Janeiro - Pça.XV")
    assert coef is not None
    depths, metodo = desagregacao.desagregar(100.0, coef_pfafstetter=coef)
    assert metodo == "pfafstetter"
    assert depths[1440] == pytest.approx(114.0, rel=1e-6)  # ainda aplica 1.14


def test_desagregar_fallback_dnaee():
    depths, metodo = desagregacao.desagregar(100.0, coef_pfafstetter=None)
    assert metodo == "dnaee"
    assert 5 in depths and 1440 in depths

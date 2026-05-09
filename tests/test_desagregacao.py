"""Testes da desagregacao DNAEE (tabela CETESB/Tucci + fator Weiss 1.14)."""
from __future__ import annotations

import pytest

from chuva_vazao import desagregacao


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

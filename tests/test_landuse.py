"""Testes para landuse.py — tabelas, classificador GH e compute_c_and_cn."""
from __future__ import annotations

from unittest.mock import patch

import numpy as np
import pytest
from shapely.geometry import Polygon, box

from chuva_vazao import landuse


# ---------------------------------------------------------------------------
# Tabelas
# ---------------------------------------------------------------------------

def test_mapbiomas_lookup_cobre_categorias_principais():
    """Classes mais comuns no Brasil devem estar mapeadas."""
    for classe in [3, 4, 9, 11, 12, 15, 18, 21, 24, 33]:
        assert classe in landuse.MAPBIOMAS_TO_CATEGORIA


def test_dw_lookup_tem_9_classes():
    """Dynamic World tem 9 classes (0..8), todas devem estar mapeadas."""
    for classe in range(9):
        assert classe in landuse.DW_TO_CATEGORIA


def test_c_tem_entrada_para_toda_categoria_mapeada():
    """Toda categoria usada pelo MapBiomas/DW deve ter entrada em C."""
    categorias = set(landuse.MAPBIOMAS_TO_CATEGORIA.values()) | set(
        landuse.DW_TO_CATEGORIA.values()
    )
    for cat in categorias:
        assert cat in landuse.C_POR_CATEGORIA, f"Falta C para '{cat}'"


def test_cn_tem_entrada_para_toda_categoria_mapeada():
    """Toda categoria usada pelo MapBiomas/DW deve ter entrada em CN."""
    categorias = set(landuse.MAPBIOMAS_TO_CATEGORIA.values()) | set(
        landuse.DW_TO_CATEGORIA.values()
    )
    for cat in categorias:
        assert cat in landuse.CN_POR_CATEGORIA_E_GH, f"Falta CN para '{cat}'"


def test_cn_tem_4_grupos_para_cada_categoria():
    """Cada categoria em CN_POR_CATEGORIA_E_GH deve ter A/B/C/D."""
    for cat, row in landuse.CN_POR_CATEGORIA_E_GH.items():
        assert set(row.keys()) == {"A", "B", "C", "D"}, (
            f"Categoria '{cat}' sem todos grupos: {row.keys()}"
        )


def test_c_em_intervalo_valido():
    """C em [0, 1]."""
    for cat, c in landuse.C_POR_CATEGORIA.items():
        assert 0.0 <= c <= 1.0, f"C fora do intervalo para '{cat}': {c}"


def test_cn_em_intervalo_valido():
    """CN em [30, 100]."""
    for cat, row in landuse.CN_POR_CATEGORIA_E_GH.items():
        for gh, cn in row.items():
            assert 30.0 <= cn <= 100.0, (
                f"CN fora do intervalo para '{cat}/{gh}': {cn}"
            )


def test_cn_urbano_maior_que_floresta():
    """Sanidade fisica: urbano tem CN maior que floresta para mesmo GH."""
    for gh in ["A", "B", "C", "D"]:
        assert (
            landuse.CN_POR_CATEGORIA_E_GH["urbano"][gh]
            > landuse.CN_POR_CATEGORIA_E_GH["floresta"][gh]
        )


def test_cn_cresce_com_grupo_mais_impermeavel():
    """CN deve crescer de A (mais permeavel) para D (mais impermeavel)."""
    # Em categorias nao-saturadas (agua e alagados ja estao em 98-100)
    for cat in ["floresta", "pastagem", "agricultura", "urbano", "solo_exposto"]:
        row = landuse.CN_POR_CATEGORIA_E_GH[cat]
        assert row["A"] <= row["B"] <= row["C"] <= row["D"], (
            f"Monotonicidade quebrada para '{cat}': {row}"
        )


def test_c_urbano_maior_que_floresta():
    """Urbano >> floresta em C."""
    assert landuse.C_POR_CATEGORIA["urbano"] > landuse.C_POR_CATEGORIA["floresta"]


# ---------------------------------------------------------------------------
# Classificador grupo hidrologico
# ---------------------------------------------------------------------------

def test_classify_gh_areia_pura_vira_A():
    """Textura muito arenosa -> A."""
    sand = np.array([80.0, 85.0, 90.0])
    clay = np.array([5.0, 5.0, 5.0])
    gh = landuse.classify_hydrological_group(sand, clay)
    assert all(g == "A" for g in gh)


def test_classify_gh_argila_muito_pesada_vira_D():
    """Sartori: só argila MUITO pesada e pouco arenosa (sand<15, clay>=55) -> D.
    Argila moderada (ex. Latossolo, clay~45%) fica em C/B por ser bem drenada,
    não em D como a classificação por textura pura (Rawls) faria."""
    sand = np.array([10.0, 8.0])
    clay = np.array([60.0, 70.0])
    gh = landuse.classify_hydrological_group(sand, clay)
    assert all(g == "D" for g in gh)
    # Argila moderada e pouco arenosa cai em C (não D), pelo princípio de Sartori.
    gh_moderada = landuse.classify_hydrological_group(
        np.array([20.0, 15.0]), np.array([45.0, 50.0]),
    )
    assert all(g == "C" for g in gh_moderada)


def test_classify_gh_intermediario_nao_quebra():
    """Textura intermediaria deve cair em B ou C, nunca falhar."""
    sand = np.array([55.0, 40.0, 30.0])
    clay = np.array([15.0, 25.0, 30.0])
    gh = landuse.classify_hydrological_group(sand, clay)
    assert all(g in {"A", "B", "C", "D"} for g in gh)


def test_classify_gh_agravado_por_declividade():
    """Encosta ingreme agrava o grupo (plano B, ingreme C, muito ingreme D)."""
    sand = np.array([40.0, 40.0, 40.0])   # textura -> B
    clay = np.array([20.0, 20.0, 20.0])
    assert all(g == "B" for g in landuse.classify_hydrological_group(sand, clay))
    slope = np.array([5.0, 30.0, 60.0])   # plano / ingreme / muito ingreme
    gh = landuse.classify_hydrological_group(sand, clay, slope_pct=slope)
    assert list(gh) == ["B", "C", "D"]


def test_agravar_gh_limita_em_D():
    """Agravamento por declividade nao passa de D."""
    gh = np.array(["C", "D"])
    slope = np.array([60.0, 60.0])
    out = landuse._agravar_gh_por_declividade(gh, slope)
    assert list(out) == ["D", "D"]


def test_classify_gh_shape_preservado():
    """Output tem mesmo shape do input."""
    sand = np.random.uniform(20, 80, size=(10, 10))
    clay = np.random.uniform(5, 40, size=(10, 10))
    gh = landuse.classify_hydrological_group(sand, clay)
    assert gh.shape == sand.shape


# ---------------------------------------------------------------------------
# compute_c_and_cn (com mock dos downloads GEE)
# ---------------------------------------------------------------------------

@pytest.fixture
def bacia_simples() -> Polygon:
    """Polygon de 0.01 x 0.01 graus ~= 1.1 x 1.1 km."""
    return box(-44.32, -22.68, -44.31, -22.67)


def test_compute_c_and_cn_rejeita_fonte_invalida(bacia_simples):
    """fonte_lulc fora do catalogo deve levantar ValueError antes de chamar GEE."""
    with pytest.raises(ValueError, match="fonte_lulc invalida"):
        landuse.compute_c_and_cn(bacia_simples, fonte_lulc="invalid_source")

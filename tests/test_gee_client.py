"""Testes para gee_client.py — utilidades sem hit na API real do GEE."""
from __future__ import annotations

from pathlib import Path

import pytest
from shapely.geometry import Polygon, box

from chuva_vazao import gee_client


# ---------------------------------------------------------------------------
# bbox_from_point
# ---------------------------------------------------------------------------

def test_bbox_from_point_retangulo_correto():
    """bbox 0.1 graus deve ter arestas de ~0.2 graus."""
    poly = gee_client.bbox_from_point(lat=-22.68, lon=-44.32, buffer_deg=0.1)
    minx, miny, maxx, maxy = poly.bounds
    assert abs(maxx - minx - 0.2) < 1e-9
    assert abs(maxy - miny - 0.2) < 1e-9
    # Centro confere
    cx, cy = poly.centroid.x, poly.centroid.y
    assert abs(cx - (-44.32)) < 1e-9
    assert abs(cy - (-22.68)) < 1e-9


def test_bbox_from_point_default_buffer():
    """Buffer default = 0.1."""
    poly = gee_client.bbox_from_point(lat=0.0, lon=0.0)
    minx, miny, maxx, maxy = poly.bounds
    assert abs(maxx - minx - 0.2) < 1e-9


# ---------------------------------------------------------------------------
# _cache_path
# ---------------------------------------------------------------------------

def test_cache_path_deterministico():
    """Mesma geometria + kind + extra -> mesmo path."""
    geom = box(-44.32, -22.68, -44.31, -22.67)
    p1 = gee_client._cache_path("dem_cop30", geom, extra="s30")
    p2 = gee_client._cache_path("dem_cop30", geom, extra="s30")
    assert p1 == p2


def test_cache_path_muda_com_extra():
    """Kind/extra diferente -> path diferente."""
    geom = box(-44.32, -22.68, -44.31, -22.67)
    p1 = gee_client._cache_path("dem_cop30", geom, extra="s30")
    p2 = gee_client._cache_path("dem_cop30", geom, extra="s90")
    assert p1 != p2


def test_cache_path_muda_com_geom():
    """Geometrias diferentes -> paths diferentes."""
    g1 = box(-44.32, -22.68, -44.31, -22.67)
    g2 = box(-43.00, -22.00, -42.99, -21.99)
    p1 = gee_client._cache_path("dem_cop30", g1, extra="s30")
    p2 = gee_client._cache_path("dem_cop30", g2, extra="s30")
    assert p1 != p2


def test_cache_path_usa_diretorio_correto():
    """Path cai em data/gee_cache/."""
    geom = box(0, 0, 0.1, 0.1)
    p = gee_client._cache_path("test", geom)
    assert "gee_cache" in str(p)
    assert p.suffix == ".tif"


# ---------------------------------------------------------------------------
# _to_ee_geometry (valida tipos — sem init GEE)
# ---------------------------------------------------------------------------

def test_to_ee_geometry_rejeita_tipo_errado():
    with pytest.raises(TypeError):
        gee_client._to_ee_geometry([1, 2, 3, 4])  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Integration smoke (skip se GEE nao autenticado) — tag slow
# ---------------------------------------------------------------------------

@pytest.mark.slow
def test_check_connection_gee():
    """
    Teste de integracao: so passa se o host tiver `earthengine authenticate`
    rodado e projeto 'ggeantigravity' acessivel. Marca como 'slow' pra rodar
    apenas sob pedido (pytest -m slow).
    """
    r = gee_client.check_connection()
    if not r["ok"]:
        pytest.skip(f"GEE nao disponivel neste host: {r.get('error')}")
    assert r["project"] == "ggeantigravity"

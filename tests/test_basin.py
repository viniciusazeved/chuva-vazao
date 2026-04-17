"""Testes do modulo basin (delineamento WhiteboxTools + helpers)."""
from __future__ import annotations

import pytest

from chuva_vazao import basin


# ---------------------------------------------------------------------------
# utm_epsg_for
# ---------------------------------------------------------------------------

def test_utm_epsg_for_rj():
    """RJ (-22.9, -43.2) -> UTM 23S = EPSG 32723."""
    assert basin.utm_epsg_for(-22.9, -43.2) == 32723


def test_utm_epsg_for_porto_alegre():
    """POA (-30.0, -51.2) -> UTM 22S = EPSG 32722."""
    assert basin.utm_epsg_for(-30.0, -51.2) == 32722


def test_utm_epsg_for_manaus():
    """Manaus em (-3.1, -59.9) fica na zona 21S = EPSG 32721.
    Longitude -60.0 cai na fronteira 20/21; por convenção pertence a 21."""
    assert basin.utm_epsg_for(-3.1, -59.9) == 32721


def test_utm_epsg_for_hemisferio_norte():
    """NYC (40.7, -74.0) -> UTM 18N = EPSG 32618."""
    assert basin.utm_epsg_for(40.7, -74.0) == 32618


# ---------------------------------------------------------------------------
# download_dem_opentopography
# ---------------------------------------------------------------------------

def test_download_sem_api_key_lanca(monkeypatch):
    """Sem OPENTOPO_API_KEY no env, deve lancar ValueError."""
    monkeypatch.delenv("OPENTOPO_API_KEY", raising=False)
    with pytest.raises(ValueError, match="API key"):
        basin.download_dem_opentopography(lat=-22.68, lon=-44.32, api_key=None)


# ---------------------------------------------------------------------------
# reproject_dem_to_utm
# ---------------------------------------------------------------------------

def test_reproject_dem_gera_arquivo(synthetic_dem, tmp_path):
    """Reprojetar o DEM sintetico para UTM e verificar que o arquivo saiu."""
    out = tmp_path / "reproj.tif"
    result = basin.reproject_dem_to_utm(
        dem_path=synthetic_dem, lat=-22.68, lon=-44.32, out_path=out,
    )
    assert result.exists()
    assert result.stat().st_size > 1000


def test_reproject_dem_crs_utm(synthetic_dem, tmp_path):
    """O DEM reprojetado deve ter CRS UTM."""
    import rasterio
    out = basin.reproject_dem_to_utm(synthetic_dem, -22.68, -44.32, tmp_path / "r.tif")
    with rasterio.open(out) as src:
        assert src.crs.to_epsg() == 32723  # UTM 23S


# ---------------------------------------------------------------------------
# delineate_basin (smoke)
# ---------------------------------------------------------------------------

@pytest.mark.timeout(300)
def test_delineate_basin_retorna_resultado_valido(synthetic_dem, synthetic_outlet):
    """Smoke test: delinear bacia no DEM sintetico e conferir metricas sensatas."""
    lat, lon = synthetic_outlet
    result = basin.delineate_basin(
        lat=lat, lon=lon, dem_path=synthetic_dem,
        snap_dist_m=500, stream_threshold=20,
    )

    # Estrutura
    assert result.basin_gdf is not None
    assert result.stream_gdf is not None
    assert result.outlet_snapped is not None
    assert result.work_dir.exists()

    # Metricas sensatas
    m = result.metrics
    assert m.area_km2 > 0
    assert m.perimeter_km > 0
    assert m.elev_max_m > m.elev_min_m
    assert m.delta_h_m == pytest.approx(m.elev_max_m - m.elev_min_m, rel=1e-6)

    # CRS do resultado em EPSG:4326
    assert result.basin_gdf.crs.to_epsg() == 4326

    # Outlet snapped perto do outlet original (< 1 km ~ 0.01 grau)
    dist_deg = (
        (result.outlet_snapped.x - lon) ** 2
        + (result.outlet_snapped.y - lat) ** 2
    ) ** 0.5
    assert dist_deg < 0.01


def test_basin_metrics_summary_dict(synthetic_dem, synthetic_outlet):
    """summary_dict deve retornar todas as chaves esperadas."""
    lat, lon = synthetic_outlet
    result = basin.delineate_basin(
        lat=lat, lon=lon, dem_path=synthetic_dem,
        snap_dist_m=500, stream_threshold=20,
    )
    d = result.metrics.summary_dict()
    assert set(d.keys()) == {
        "A (km2)", "P (km)", "L canal (km)", "S media (%)",
        "Z max (m)", "Z min (m)", "dH (m)",
    }

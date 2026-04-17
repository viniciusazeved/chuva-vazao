"""
Fixtures compartilhadas entre testes.

synthetic_dem: DEM sintetico em forma de cone invertido (vale central), 100x100
celulas, resolucao ~100m/pixel, EPSG:4326, centrado em Bananal/SP (-22.68, -44.32).
Exutorio no centro do raster — a bacia deve cobrir boa parte da area.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest


@pytest.fixture(scope="session")
def synthetic_dem(tmp_path_factory) -> Path:
    """
    DEM sintetico em forma de cone invertido (vale central, elevacao cresce
    com a distancia do centro). Garante drenagem convergente para o centro.

    Ao rodar pelo basin.delineate_basin com lat/lon no centro, o delineamento
    deve retornar uma bacia com area > 0 e canal principal > 0.
    """
    # Import tardio — rasterio requer PROJ config que basin.py faz.
    from chuva_vazao import basin  # noqa: F401 — for side-effects (PROJ)
    import rasterio
    from rasterio.transform import from_origin

    size = 100
    # Centro do raster no lat/lon de Bananal
    center_lat, center_lon = -22.68, -44.32
    pixel_deg = 0.001  # ~111 m

    # Coords do canto NW
    half = size / 2 * pixel_deg
    north = center_lat + half
    west = center_lon - half
    transform = from_origin(west=west, north=north, xsize=pixel_deg, ysize=pixel_deg)

    # Superficie: cone invertido centrado + inclinacao leve pra leste
    xx, yy = np.meshgrid(np.arange(size), np.arange(size))
    cx, cy = size / 2, size / 2
    dist = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2)
    # Vale central cresce com distancia quadratica; elevacao base 500m
    z = 500.0 + 0.5 * dist + 0.02 * (xx - cx)
    z = z.astype("float32")

    out_dir = tmp_path_factory.mktemp("dems")
    dem_path = out_dir / "synthetic_dem.tif"
    with rasterio.open(
        dem_path, "w",
        driver="GTiff",
        height=size, width=size,
        count=1, dtype="float32",
        crs="EPSG:4326",
        transform=transform,
        nodata=-9999,
    ) as dst:
        dst.write(z, 1)

    return dem_path


@pytest.fixture(scope="session")
def synthetic_outlet() -> tuple[float, float]:
    """(lat, lon) do exutório para a fixture synthetic_dem."""
    return (-22.68, -44.32)

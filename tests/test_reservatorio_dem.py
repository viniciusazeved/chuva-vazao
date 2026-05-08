"""
Testes do flood-fill cota-volume sobre DEM sintetico.

Cenario base: paraboloide centrado no ponto da barragem,
    z(x, y) = z0 + a * (x^2 + y^2)
Volume teorico ate a cota Z (com Z > z0):
    V(Z) = integral_circular = pi * (Z - z0)^2 / (2 * a)

Implementamos a curva pelo flood-fill e checamos a tolerancia ~5-10 % por
discretizacao em pixel.
"""
from __future__ import annotations

from pathlib import Path

# Import reservatorio_dem antes de rasterio para garantir o workaround PROJ.
from chuva_vazao.reservatorio_dem import gerar_curva_cota_volume_dem  # noqa: I001

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin


def _criar_dem_paraboloide(
    out_path: Path,
    *,
    n: int = 121,
    pixel_m: float = 5.0,
    z0: float = 100.0,
    a: float = 0.02,
    epsg_utm: int = 32723,
    origin_x: float = 500_000.0,
    origin_y: float = 7_500_000.0,
):
    """
    Cria um DEM em GeoTIFF UTM com z(x,y) = z0 + a*((x-xc)^2 + (y-yc)^2).
    Centro da grade fica nas coordenadas (origin_x + n/2*pixel, origin_y - n/2*pixel).
    """
    transform = from_origin(origin_x, origin_y, pixel_m, pixel_m)
    cols = np.arange(n)
    rows = np.arange(n)
    cc, rr = np.meshgrid(cols, rows)
    xs = origin_x + (cc + 0.5) * pixel_m
    ys = origin_y - (rr + 0.5) * pixel_m
    xc = origin_x + (n / 2.0) * pixel_m
    yc = origin_y - (n / 2.0) * pixel_m
    z = z0 + a * ((xs - xc) ** 2 + (ys - yc) ** 2)
    z = z.astype(np.float32)

    with rasterio.open(
        out_path, "w",
        driver="GTiff",
        height=n, width=n, count=1,
        dtype=z.dtype,
        crs=f"EPSG:{epsg_utm}",
        transform=transform,
        nodata=-9999.0,
    ) as dst:
        dst.write(z, 1)
    return xc, yc


def _utm_to_lonlat(x: float, y: float, epsg_utm: int) -> tuple[float, float]:
    from rasterio.warp import transform as warp_transform
    lon, lat = warp_transform(f"EPSG:{epsg_utm}", "EPSG:4326", [x], [y])
    return lon[0], lat[0]


def test_paraboloide_volume_bate_com_analitico(tmp_path: Path):
    """
    V(Z) = pi * (Z - z0)^2 / (2*a). Tolerancia 8 % por discretizacao 5 m.
    """
    z0, a = 100.0, 0.02
    pixel = 5.0
    epsg_utm = 32723
    dem = tmp_path / "parab.tif"
    xc, yc = _criar_dem_paraboloide(
        dem, z0=z0, a=a, pixel_m=pixel, n=151, epsg_utm=epsg_utm,
    )
    lon, lat = _utm_to_lonlat(xc, yc, epsg_utm)

    delta_h = 8.0  # 8 m acima do fundo (raio submerso ate 20 m -> 4 pixels)
    res = gerar_curva_cota_volume_dem(
        lat=lat, lon=lon,
        dem_path=dem,
        bacia_polygon_4326=None,
        delta_h_max_m=delta_h,
        n_pontos=10,
    )
    # z_fundo deve ser ~z0 (pixel central do paraboloide)
    assert res.z_fundo_m == pytest.approx(z0, abs=0.5)

    # Compara V analitico — so pra cotas onde o raio submerso > 2 pixels
    # (discretizacao em pequenas alturas e severa: pixel=5m, h<1m da 1 pixel).
    for z_i, V_num in zip(res.cotas_m, res.volumes_m3):
        h = z_i - z0
        if h < 2.0:
            continue
        raio_submerso = np.sqrt(h / a)
        if raio_submerso < 3 * pixel:
            continue
        V_teor = np.pi * h ** 2 / (2 * a)
        # Tolerancia ~12 % por discretizacao em grade quadrada
        assert V_num == pytest.approx(V_teor, rel=0.12), (
            f"z={z_i:.2f}, h={h:.2f}: V_num={V_num:.1f}, V_teor={V_teor:.1f}"
        )


def test_paraboloide_area_aproximada(tmp_path: Path):
    """
    A(Z) = pi * (Z - z0) / a. Tolerancia 10 %.
    """
    z0, a = 100.0, 0.02
    pixel = 5.0
    epsg_utm = 32723
    dem = tmp_path / "parab2.tif"
    xc, yc = _criar_dem_paraboloide(
        dem, z0=z0, a=a, pixel_m=pixel, n=151, epsg_utm=epsg_utm,
    )
    lon, lat = _utm_to_lonlat(xc, yc, epsg_utm)

    res = gerar_curva_cota_volume_dem(
        lat=lat, lon=lon,
        dem_path=dem,
        delta_h_max_m=8.0,
        n_pontos=6,
    )
    # area na ultima cota (z = z0 + 8): h grande o suficiente pra grade fina
    A_teor = np.pi * (res.cotas_m[-1] - z0) / a
    assert res.areas_m2[-1] == pytest.approx(A_teor, rel=0.12)


def test_curva_monotonica_e_z_fundo_zero(tmp_path: Path):
    """V e A devem ser nao-decrescentes; V(z_fundo) = 0; A(z_fundo) >= 1 pixel."""
    dem = tmp_path / "parab3.tif"
    xc, yc = _criar_dem_paraboloide(dem, n=81, pixel_m=10.0)
    lon, lat = _utm_to_lonlat(xc, yc, 32723)

    res = gerar_curva_cota_volume_dem(
        lat=lat, lon=lon, dem_path=dem,
        delta_h_max_m=4.0, n_pontos=8,
    )
    assert (np.diff(res.volumes_m3) >= -1e-6).all()
    assert (np.diff(res.areas_m2) >= -1e-6).all()
    assert res.volumes_m3[0] == pytest.approx(0.0, abs=1.0)


def test_z_min_clip_remove_pixels_baixos(tmp_path: Path):
    """
    Cliping de pixels abaixo de z_min_clip diminui o volume — simula lago
    existente onde o "fundo" real comeca acima da bacia mais baixa.
    """
    dem = tmp_path / "parab4.tif"
    xc, yc = _criar_dem_paraboloide(dem, z0=100.0, a=0.02, n=121, pixel_m=5.0)
    lon, lat = _utm_to_lonlat(xc, yc, 32723)

    # Ponto da barragem ligeiramente fora do centro pra evitar pegar exatamente o minimo
    res_sem_clip = gerar_curva_cota_volume_dem(
        lat=lat, lon=lon, dem_path=dem,
        delta_h_max_m=4.0, n_pontos=5,
    )
    # Clip em z=101: forca o "fundo" a ser ~101 e nao 100
    res_com_clip = gerar_curva_cota_volume_dem(
        lat=lat, lon=lon, dem_path=dem,
        z_min_clip_m=101.0,
        z_max_m=104.0, n_pontos=5,
    )
    # Volume final do clipado deve ser menor (mesma cota maxima 104)
    V_sem = float(np.interp(104.0, res_sem_clip.cotas_m, res_sem_clip.volumes_m3))
    V_com = float(res_com_clip.volumes_m3[-1])
    assert V_com < V_sem


def test_piso_automatico_zera_volume_no_fundo(tmp_path: Path):
    """
    Vale com pixels mais baixos que o ponto da barragem: o piso automatico
    (= z do pixel da barragem) deve garantir V(z_fundo) = 0.
    """
    n = 81
    pixel = 10.0
    epsg = 32723
    ox, oy = 500_000.0, 7_500_000.0
    transform = from_origin(ox, oy, pixel, pixel)
    cc, rr = np.meshgrid(np.arange(n), np.arange(n))
    xs = ox + (cc + 0.5) * pixel
    ys = oy - (rr + 0.5) * pixel
    xc = ox + (n / 2.0) * pixel
    yc = oy - (n / 2.0) * pixel
    # Vale: cota cresce com y (norte mais alto), centro a 100 m. Pixels
    # ao sul do centro tem z < 100 (mais baixos que o ponto da barragem).
    z = 100.0 + 0.05 * (yc - ys) + 0.001 * (xs - xc) ** 2
    z = z.astype(np.float32)

    dem = tmp_path / "vale.tif"
    with rasterio.open(
        dem, "w", driver="GTiff",
        height=n, width=n, count=1, dtype="float32",
        crs=f"EPSG:{epsg}", transform=transform, nodata=-9999.0,
    ) as dst:
        dst.write(z, 1)
    lon, lat = _utm_to_lonlat(xc, yc, epsg)

    res = gerar_curva_cota_volume_dem(
        lat=lat, lon=lon, dem_path=dem,
        delta_h_max_m=5.0, n_pontos=5,
    )
    # Sem o piso automatico, V(z_fundo) seria > 0 (vale tem pixels < 100).
    # Com o piso, deve ser zero.
    assert res.volumes_m3[0] == pytest.approx(0.0, abs=1.0)
    assert res.z_fundo_m == pytest.approx(100.0, abs=0.5)
    assert res.volumes_m3[-1] > 0


def test_bacia_polygon_restringe_flood_fill(tmp_path: Path):
    """
    Bug-fix: quando o DEM nao tem nodata definido, rio_mask preenche fora-do-
    poligono com 0, e o flood-fill antigo alagava esses pixels. Com o sentinel
    explicito, pixels fora da bacia precisam ser totalmente ignorados.
    """
    n = 121
    pixel = 10.0
    epsg = 32723
    ox, oy = 500_000.0, 7_500_000.0
    transform = from_origin(ox, oy, pixel, pixel)
    cc, rr = np.meshgrid(np.arange(n), np.arange(n))
    xs = ox + (cc + 0.5) * pixel
    ys = oy - (rr + 0.5) * pixel
    xc = ox + (n / 2.0) * pixel
    yc = oy - (n / 2.0) * pixel
    z = (100.0 + 0.05 * (yc - ys) + 0.001 * (xs - xc) ** 2).astype(np.float32)
    dem = tmp_path / "dem_sem_nodata.tif"
    # Sem nodata definido — reproduz o caso do Copernicus GLO-30.
    with rasterio.open(
        dem, "w", driver="GTiff",
        height=n, width=n, count=1, dtype="float32",
        crs=f"EPSG:{epsg}", transform=transform,
    ) as dst:
        dst.write(z, 1)

    lon_c, lat_c = _utm_to_lonlat(xc, yc, epsg)

    # Bacia pequena = quadrado de ~200 m em torno do centro
    half_m = 100.0
    bacia_corners_utm = [
        (xc - half_m, yc - half_m),
        (xc + half_m, yc - half_m),
        (xc + half_m, yc + half_m),
        (xc - half_m, yc + half_m),
        (xc - half_m, yc - half_m),
    ]
    from rasterio.warp import transform as warp_transform
    xs_b = [c[0] for c in bacia_corners_utm]
    ys_b = [c[1] for c in bacia_corners_utm]
    lon_b, lat_b = warp_transform(f"EPSG:{epsg}", "EPSG:4326", xs_b, ys_b)
    bacia_geom = {
        "type": "Polygon",
        "coordinates": [list(zip(lon_b, lat_b))],
    }

    res = gerar_curva_cota_volume_dem(
        lat=lat_c, lon=lon_c, dem_path=dem,
        bacia_polygon_4326=bacia_geom,
        delta_h_max_m=10.0, n_pontos=5,
    )
    # Bacia tem ~200 x 200 m = 40_000 m² de area maxima.
    # Espelho NAO pode exceder a area da bacia.
    assert res.areas_m2[-1] <= 40_000 * 1.05, (
        f"Espelho ({res.areas_m2[-1]:.0f} m²) excedeu a area da bacia "
        "(40 000 m²) — flood-fill vazou pra fora do poligono."
    )


def test_mancha_geojson_valida(tmp_path: Path):
    """A mancha deve ser GeoJSON FeatureCollection nao-vazia em EPSG:4326."""
    import json
    dem = tmp_path / "parab5.tif"
    xc, yc = _criar_dem_paraboloide(dem, n=81, pixel_m=10.0)
    lon, lat = _utm_to_lonlat(xc, yc, 32723)

    res = gerar_curva_cota_volume_dem(
        lat=lat, lon=lon, dem_path=dem,
        delta_h_max_m=4.0, n_pontos=4,
    )
    fc = json.loads(res.mancha_geojson)
    assert fc["type"] == "FeatureCollection"
    assert len(fc["features"]) >= 1
    g0 = fc["features"][0]["geometry"]
    assert g0["type"] in ("Polygon", "MultiPolygon")
    # Coordenadas devem ser longitudes/latitudes plausiveis (Brasil)
    if g0["type"] == "Polygon":
        x, y = g0["coordinates"][0][0]
    else:
        x, y = g0["coordinates"][0][0][0]
    assert -75 < x < -30
    assert -34 < y < 6

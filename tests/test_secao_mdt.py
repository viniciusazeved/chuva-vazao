"""
Testes da amostragem de secao transversal a partir de MDT (secao_mdt).

DEMs sinteticos em GeoTIFF UTM. Verifica:
- bilinear exata em superficie linear,
- captura do thalweg (menor Z perto do centro do canal numa calha em V),
- ordenacao montante -> jusante por cota (thalweg cresce a montante),
- preenchimento de nodata isolado,
- erros (linha fora do raster, raster sem CRS).
"""
from __future__ import annotations

from pathlib import Path

# Import antes de rasterio para garantir o workaround PROJ no Windows.
from chuva_vazao.secao_mdt import amostrar_perfil, _utm_epsg_from_lonlat  # noqa: I001

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin
from rasterio.warp import transform as warp_transform


EPSG_UTM = 32723
OX, OY = 500_000.0, 7_500_000.0  # canto NW (UTM 23S)
PIXEL = 1.0
N = 400


def _utm_to_lonlat(x: float, y: float, epsg: int = EPSG_UTM) -> tuple[float, float]:
    lon, lat = warp_transform(f"EPSG:{epsg}", "EPSG:4326", [x], [y])
    return lon[0], lat[0]


def _escrever_tif(out_path: Path, z: np.ndarray, *, crs: str | None = f"EPSG:{EPSG_UTM}"):
    transform = from_origin(OX, OY, PIXEL, PIXEL)
    with rasterio.open(
        out_path, "w", driver="GTiff",
        height=z.shape[0], width=z.shape[1], count=1,
        dtype="float32",
        crs=crs, transform=transform, nodata=-9999.0,
    ) as dst:
        dst.write(z.astype(np.float32), 1)


def _grade_xy():
    cc, rr = np.meshgrid(np.arange(N), np.arange(N))
    xs = OX + (cc + 0.5) * PIXEL
    ys = OY - (rr + 0.5) * PIXEL
    return xs, ys


def test_utm_epsg_hemisferio_sul():
    # Bananal/SP fica na zona 23S
    assert _utm_epsg_from_lonlat(-22.68, -44.32) == "EPSG:32723"
    # Ponto no hemisferio norte usa 326xx
    assert _utm_epsg_from_lonlat(10.0, -44.32).startswith("EPSG:326")


def test_bilinear_exata_em_plano(tmp_path: Path):
    """Bilinear recupera exatamente uma superficie linear z = a*E + b*N + c'."""
    a, b, c = 0.01, 0.02, 100.0
    xs, ys = _grade_xy()
    z = a * (xs - OX) + b * (OY - ys) + c
    dem = tmp_path / "plano.tif"
    _escrever_tif(dem, z)

    y0 = OY - (N / 2.0) * PIXEL
    l1 = _utm_to_lonlat(OX + 50 * PIXEL, y0)
    l2 = _utm_to_lonlat(OX + 350 * PIXEL, y0)
    perfil = amostrar_perfil([l1, l2], dem, n_pontos=50, crs_trabalho=f"EPSG:{EPSG_UTM}")

    assert perfil.n_nodata == 0
    assert perfil.n_amostras == 50
    for p in perfil.pontos:
        z_teor = a * (p.E - OX) + b * (OY - p.N) + c
        assert p.Z == pytest.approx(z_teor, abs=0.05)


def test_captura_thalweg_no_centro(tmp_path: Path):
    """Calha em V: o menor Z fica perto do eixo do canal e os extremos sobem."""
    s_long, s_trans, z_base = 0.005, 0.10, 100.0
    xs, ys = _grade_xy()
    x_canal = OX + (N / 2.0) * PIXEL
    y_min = OY - N * PIXEL
    z = z_base + s_long * (ys - y_min) + s_trans * np.abs(xs - x_canal)
    dem = tmp_path / "calha.tif"
    _escrever_tif(dem, z)

    y0 = OY - (N / 2.0) * PIXEL
    l1 = _utm_to_lonlat(OX + 50 * PIXEL, y0)
    l2 = _utm_to_lonlat(OX + 350 * PIXEL, y0)
    perfil = amostrar_perfil([l1, l2], dem, n_pontos=101, crs_trabalho=f"EPSG:{EPSG_UTM}")

    zs = np.array([p.Z for p in perfil.pontos])
    es = np.array([p.E for p in perfil.pontos])
    i_min = int(np.argmin(zs))
    assert abs(es[i_min] - x_canal) < 5.0          # thalweg perto do eixo
    assert zs[0] > zs[i_min] and zs[-1] > zs[i_min]  # forma de V
    assert perfil.z_min_m == pytest.approx(zs.min(), abs=1e-6)


def test_cota_cresce_para_montante(tmp_path: Path):
    """Tres linhas em y decrescente: thalweg e media caem de M -> C -> J."""
    s_long, s_trans, z_base = 0.01, 0.10, 100.0
    xs, ys = _grade_xy()
    x_canal = OX + (N / 2.0) * PIXEL
    y_min = OY - N * PIXEL
    z = z_base + s_long * (ys - y_min) + s_trans * np.abs(xs - x_canal)
    dem = tmp_path / "calha2.tif"
    _escrever_tif(dem, z)

    def perfil_em(y: float):
        l1 = _utm_to_lonlat(OX + 50 * PIXEL, y)
        l2 = _utm_to_lonlat(OX + 350 * PIXEL, y)
        return amostrar_perfil([l1, l2], dem, n_pontos=40, crs_trabalho=f"EPSG:{EPSG_UTM}")

    p_m = perfil_em(OY - 80 * PIXEL)    # mais ao norte (y maior) = montante
    p_c = perfil_em(OY - 200 * PIXEL)
    p_j = perfil_em(OY - 320 * PIXEL)   # mais ao sul = jusante

    assert p_m.z_min_m > p_c.z_min_m > p_j.z_min_m
    assert p_m.z_media_m > p_c.z_media_m > p_j.z_media_m


def test_nodata_interpolado(tmp_path: Path):
    """Faixa de nodata no meio: amostras viram interpoladas, sem NaN final."""
    z = np.full((N, N), 100.0, dtype=np.float32)
    z[:, N // 2 - 5:N // 2 + 5] = -9999.0  # buraco no centro
    dem = tmp_path / "plano_buraco.tif"
    _escrever_tif(dem, z)

    y0 = OY - (N / 2.0) * PIXEL
    l1 = _utm_to_lonlat(OX + 20 * PIXEL, y0)
    l2 = _utm_to_lonlat(OX + 380 * PIXEL, y0)
    perfil = amostrar_perfil([l1, l2], dem, n_pontos=100, crs_trabalho=f"EPSG:{EPSG_UTM}")

    zs = np.array([p.Z for p in perfil.pontos])
    assert perfil.n_nodata > 0
    assert np.isfinite(zs).all()                 # buracos preenchidos
    assert zs == pytest.approx(100.0, abs=0.01)  # plano: interp tambem da 100


def test_raster_em_4326_reprojeta(tmp_path: Path):
    """MDT em graus (4326) e amostrado corretamente (reprojeta pontos)."""
    # Cone invertido em torno de Bananal/SP, grade em graus
    size = 200
    clat, clon = -22.68, -44.32
    pixel_deg = 0.0005  # ~55 m
    half = size / 2 * pixel_deg
    transform = from_origin(clon - half, clat + half, pixel_deg, pixel_deg)
    cc, rr = np.meshgrid(np.arange(size), np.arange(size))
    cx = cy = size / 2
    dist = np.sqrt((cc - cx) ** 2 + (rr - cy) ** 2)
    z = (300.0 + 0.5 * dist).astype(np.float32)
    dem = tmp_path / "cone_4326.tif"
    with rasterio.open(
        dem, "w", driver="GTiff", height=size, width=size, count=1,
        dtype="float32", crs="EPSG:4326", transform=transform, nodata=-9999.0,
    ) as dst:
        dst.write(z, 1)

    # Linha cruzando o centro (vale) — perfil deve ter minimo no meio
    l1 = (clon - 0.03, clat)
    l2 = (clon + 0.03, clat)
    perfil = amostrar_perfil([l1, l2], dem, n_pontos=61)

    zs = np.array([p.Z for p in perfil.pontos])
    i_min = int(np.argmin(zs))
    assert 20 < i_min < 40              # minimo perto do centro da linha
    assert zs[0] > zs[i_min] < zs[-1]
    assert perfil.crs_trabalho.startswith("EPSG:327")  # UTM sul


def test_linha_fora_do_raster_erro(tmp_path: Path):
    z = np.full((100, 100), 100.0, dtype=np.float32)
    dem = tmp_path / "plano2.tif"
    _escrever_tif(dem, z)
    # Pontos no Golfo da Guine (lon~0) — longe do raster em UTM 23S
    with pytest.raises(ValueError, match="fora"):
        amostrar_perfil([(0.0, 0.0), (0.0005, 0.0)], dem, n_pontos=10,
                        crs_trabalho=f"EPSG:{EPSG_UTM}")


def test_raster_sem_crs_erro(tmp_path: Path):
    z = np.full((100, 100), 100.0, dtype=np.float32)
    dem = tmp_path / "sem_crs.tif"
    _escrever_tif(dem, z, crs=None)
    l1 = _utm_to_lonlat(OX + 10 * PIXEL, OY - 10 * PIXEL)
    l2 = _utm_to_lonlat(OX + 40 * PIXEL, OY - 10 * PIXEL)
    with pytest.raises(ValueError, match="CRS"):
        amostrar_perfil([l1, l2], dem, n_pontos=10, crs_trabalho=f"EPSG:{EPSG_UTM}")


def test_poucos_vertices_erro(tmp_path: Path):
    z = np.full((100, 100), 100.0, dtype=np.float32)
    dem = tmp_path / "plano3.tif"
    _escrever_tif(dem, z)
    with pytest.raises(ValueError, match=">= 2 vertices"):
        amostrar_perfil([(-44.32, -22.68)], dem, crs_trabalho=f"EPSG:{EPSG_UTM}")

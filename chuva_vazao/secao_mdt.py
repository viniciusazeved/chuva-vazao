"""
Extracao de secoes transversais a partir de um MDT (raster) + linhas tracadas.

Recebe um GeoTIFF (DEM/MDT em qualquer CRS georreferenciado) e uma polilinha
desenhada no mapa (coordenadas lon/lat WGS84, EPSG:4326), amostra o perfil
topografico ao longo da linha e devolve pontos (N, E, Z) em UTM metrico —
exatamente o formato que `secao_natural.PontoSecao` consome. A verificacao
hidraulica a jusante (Manning, lamina normal/critica) reaproveita o backend de
secao_natural sem nenhuma alteracao.

Estrategia:
1. CRS de trabalho metrico = UTM local do centro da linha (estacas e distancias
   em metros, nao em graus).
2. Vertices da linha (4326) -> UTM de trabalho.
3. Pontos equiespacados ao longo da polilinha, no UTM de trabalho.
4. Esses pontos -> CRS nativo do raster; amostragem bilinear de Z.
5. PontoSecao(N=y_utm, E=x_utm, Z=z).

Reprojetamos os *pontos* (e nao o raster inteiro) para amostrar: preserva a
resolucao nativa do MDT e evita uma reamostragem global cara. Mesmo workaround
de PROJ_DATA/GDAL_DATA do basin.py / reservatorio_dem.py para o rasterio achar
os dados do PROJ no Windows.

Referencias:
- Interpolacao bilinear via scipy.ndimage.map_coordinates (ordem 1).
"""
from __future__ import annotations

import importlib.util
import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

# Antes de importar rasterio, apontar PROJ_DATA/GDAL_DATA para os bundles do
# proprio rasterio. Evita conflito com PROJ do PostgreSQL/PostGIS quando ambos
# estao instalados no mesmo host Windows. Mesmo workaround do basin.py.
_spec = importlib.util.find_spec("rasterio")
if _spec and _spec.origin:
    _rio_dir = Path(_spec.origin).parent
    _proj_data = _rio_dir / "proj_data"
    _gdal_data = _rio_dir / "gdal_data"
    if _proj_data.exists():
        os.environ["PROJ_DATA"] = str(_proj_data)
        os.environ["PROJ_LIB"] = str(_proj_data)
    if _gdal_data.exists():
        os.environ["GDAL_DATA"] = str(_gdal_data)

import numpy as np
import rasterio
from rasterio.warp import transform as warp_transform
from rasterio.windows import Window
from scipy.ndimage import map_coordinates
from shapely.geometry import LineString

from chuva_vazao.secao_natural import PontoSecao


# Teto de seguranca para o numero de amostras por linha (evita estourar memoria
# em MDT fino com linha longa).
MAX_AMOSTRAS = 2000


@dataclass(frozen=True)
class PerfilMDT:
    """Perfil topografico amostrado de um MDT ao longo de uma linha."""
    pontos: tuple[PontoSecao, ...]   # (N, E, Z) em UTM metrico, ordem do tracado
    crs_trabalho: str                # EPSG UTM usado (ex: "EPSG:32723")
    comprimento_m: float             # comprimento da linha no plano UTM
    z_media_m: float                 # cota media das amostras
    z_min_m: float                   # menor cota (thalweg do perfil)
    z_max_m: float                   # maior cota (margem mais alta)
    n_amostras: int                  # numero de pontos amostrados
    n_nodata: int                    # quantas amostras cairam fora do MDT/nodata


def _utm_epsg_from_lonlat(lat: float, lon: float) -> str:
    """EPSG do UTM para o ponto (hemisferio sul: 327xx; norte: 326xx)."""
    zone = int((lon + 180) / 6) + 1
    epsg = 32700 + zone if lat < 0 else 32600 + zone
    return f"EPSG:{epsg}"


def amostrar_perfil(
    linha_lonlat: Sequence[tuple[float, float]],
    dem_path: Path | str,
    *,
    passo_m: float = 1.0,
    n_pontos: int | None = None,
    crs_trabalho: str | None = None,
) -> PerfilMDT:
    """
    Amostra o perfil de um MDT ao longo de uma polilinha (lon, lat).

    Parameters
    ----------
    linha_lonlat : vertices da linha em EPSG:4326 (lon, lat), na ordem do
                   tracado — vira margem esquerda -> direita da secao.
    dem_path : GeoTIFF do MDT. Qualquer CRS com georreferencia; precisa ter CRS
               definido.
    passo_m : espacamento alvo entre amostras (m). Ignorado se n_pontos for dado.
    n_pontos : se informado, usa exatamente esse numero de amostras equiespacadas.
    crs_trabalho : EPSG metrico para estacas/distancias. Default = UTM local do
                   centro da linha.

    Returns
    -------
    PerfilMDT com pontos (N, E, Z) em UTM, pronto para secao_natural.
    """
    coords = [(float(lon), float(lat)) for lon, lat in linha_lonlat]
    if len(coords) < 2:
        raise ValueError(
            f"A linha precisa de >= 2 vertices; recebi {len(coords)}."
        )

    lons = [c[0] for c in coords]
    lats = [c[1] for c in coords]
    lon_c = sum(lons) / len(lons)
    lat_c = sum(lats) / len(lats)
    crs_work = crs_trabalho or _utm_epsg_from_lonlat(lat_c, lon_c)

    # Vertices 4326 -> UTM de trabalho
    xs_v, ys_v = warp_transform("EPSG:4326", crs_work, lons, lats)
    linha_utm = LineString(list(zip(xs_v, ys_v)))
    L = float(linha_utm.length)
    if L <= 0:
        raise ValueError(
            "Linha degenerada (comprimento zero) — os vertices coincidem?"
        )

    if n_pontos is not None:
        n = max(2, int(n_pontos))
    else:
        if passo_m <= 0:
            raise ValueError(f"passo_m precisa ser > 0, recebi {passo_m}.")
        n = max(2, int(math.ceil(L / passo_m)) + 1)
    n = min(n, MAX_AMOSTRAS)

    dists = np.linspace(0.0, L, n)
    pts_utm = [linha_utm.interpolate(float(d)) for d in dists]
    xs_p = np.array([p.x for p in pts_utm])
    ys_p = np.array([p.y for p in pts_utm])

    with rasterio.open(dem_path) as src:
        if src.crs is None:
            raise ValueError(
                f"O MDT '{Path(dem_path).name}' nao tem CRS definido. Atribua/"
                "reprojete para um CRS (idealmente UTM/SIRGAS) antes de usar."
            )
        src_crs = src.crs.to_string()
        transform = src.transform
        nodata = src.nodata
        width, height = src.width, src.height

        # Pontos do UTM de trabalho -> CRS nativo do raster
        if src_crs == crs_work:
            xr = xs_p
            yr = ys_p
        else:
            xr_l, yr_l = warp_transform(
                crs_work, src_crs, list(xs_p), list(ys_p),
            )
            xr = np.asarray(xr_l)
            yr = np.asarray(yr_l)

        # Coords de mundo -> pixel fracionario (col, row) via inversa do affine
        inv = ~transform
        cols = inv.a * xr + inv.b * yr + inv.c
        rows = inv.d * xr + inv.e * yr + inv.f

        # Janela cobrindo as amostras, com margem; clip aos limites do raster
        margem = 2
        col_off = max(0, int(math.floor(float(np.nanmin(cols)))) - margem)
        row_off = max(0, int(math.floor(float(np.nanmin(rows)))) - margem)
        col_end = min(width, int(math.ceil(float(np.nanmax(cols)))) + margem)
        row_end = min(height, int(math.ceil(float(np.nanmax(rows)))) + margem)
        if col_end <= col_off or row_end <= row_off:
            raise ValueError(
                "A linha cai inteiramente fora da extensao do MDT. Desenhe "
                "sobre a area coberta pelo raster."
            )
        window = Window(col_off, row_off, col_end - col_off, row_end - row_off)
        arr = src.read(1, window=window).astype(np.float64)

    if nodata is not None:
        arr = np.where(arr == nodata, np.nan, arr)

    # map_coordinates indexa por (dim0=row, dim1=col), relativo a janela lida.
    rows_rel = rows - row_off
    cols_rel = cols - col_off
    z = map_coordinates(
        arr, [rows_rel, cols_rel], order=1, mode="constant", cval=np.nan,
        prefilter=False,
    )

    valid = np.isfinite(z)
    n_nodata = int((~valid).sum())
    if not valid.any():
        raise ValueError(
            "Nenhuma amostra valida ao longo da linha (toda em nodata?). "
            "Verifique se a linha esta sobre a area util do MDT."
        )

    # Preenche buracos isolados (nodata) por interpolacao linear na estaca, para
    # nao quebrar a geometria molhada. O usuario revisa/edita na tabela depois.
    if n_nodata:
        z = np.interp(dists, dists[valid], z[valid])

    pontos = tuple(
        PontoSecao(N=float(ys_p[i]), E=float(xs_p[i]), Z=float(z[i]))
        for i in range(n)
    )
    return PerfilMDT(
        pontos=pontos,
        crs_trabalho=crs_work,
        comprimento_m=L,
        z_media_m=float(np.mean(z)),
        z_min_m=float(np.min(z)),
        z_max_m=float(np.max(z)),
        n_amostras=n,
        n_nodata=n_nodata,
    )

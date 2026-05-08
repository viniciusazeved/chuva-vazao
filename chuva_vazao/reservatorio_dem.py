"""
Geracao de curva cota-volume e mancha de inundacao a partir de DEM.

Modelo: barragem in-line vertical no ponto clicado. Para cada cota z_i numa
grade [z_fundo, z_max]:
    1. Identifica pixels da bacia com z_pixel <= z_i.
    2. Aplica connected-component 4-conexo a partir do pixel da barragem
       (so pixels conectados ao ponto sao considerados como o lago).
    3. Volume(z_i) = sum((z_i - z_pixel) * area_pixel) em pixels submersos.
    4. Area(z_i)   = n_pixels_submersos * area_pixel.

Mancha de inundacao = poligonalizacao da mascara de submersao na cota maxima.

Limitacoes que valem deixar claras na UI:
- DEM 30 m (Copernicus GLO-30) e grosseiro abaixo de ~5 ha de espelho.
- Lago existente falseia o "fundo": pixel ja submerso conta como cota d'agua,
  nao do leito. Mitigado pelo parametro `z_min_clip_m`.
- Eixo do barramento e assumido vertical no ponto — nao e a linha real do
  paramento. Para CGH preliminar e suficiente; refinamento exige eixo poligonal.
"""
from __future__ import annotations

import importlib.util
import os
from dataclasses import dataclass
from pathlib import Path

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

import json
import numpy as np
import rasterio
from rasterio.features import shapes
from rasterio.mask import mask as rio_mask
from rasterio.warp import Resampling, calculate_default_transform, reproject
from scipy.ndimage import label
from shapely.geometry import Polygon, mapping, shape


@dataclass(frozen=True)
class CurvaCotaVolumeDEM:
    """Resultado do flood-fill por cota a partir do DEM."""
    cotas_m: np.ndarray              # cotas absolutas (z) no datum do DEM, shape (N,)
    volumes_m3: np.ndarray           # V(z) acumulado, shape (N,)
    areas_m2: np.ndarray             # area do espelho a cada z, shape (N,)
    z_fundo_m: float                 # cota do pixel da barragem (= cotas_m[0])
    mancha_geojson: str              # GeoJSON FeatureCollection da mancha em z_max
    dem_crs: str                     # CRS UTM em que os calculos foram feitos
    pixel_size_m: float              # tamanho do pixel reprojetado (m)
    n_pixels_z_max: int              # nº de pixels submersos na cota maxima

    def to_dataframe(self):
        import pandas as pd  # noqa: PLC0415
        return pd.DataFrame({
            "z_m": self.cotas_m,
            "V_m3": self.volumes_m3,
            "A_m2": self.areas_m2,
        })


# ---------------------------------------------------------------------------
# Reprojection helper
# ---------------------------------------------------------------------------

def _utm_zone_from_lonlat(lat: float, lon: float) -> str:
    """EPSG do UTM para o ponto (hemisferio sul brasileiro: 327xx; norte: 326xx)."""
    zone = int((lon + 180) / 6) + 1
    epsg = 32700 + zone if lat < 0 else 32600 + zone
    return f"EPSG:{epsg}"


def _reproject_to_utm(src_path: Path, dst_path: Path, dst_crs: str) -> Path:
    """Reprojeta o DEM (geralmente EPSG:4326) para UTM com resampling bilinear."""
    with rasterio.open(src_path) as src:
        transform, width, height = calculate_default_transform(
            src.crs, dst_crs, src.width, src.height, *src.bounds,
        )
        kwargs = src.meta.copy()
        kwargs.update({
            "crs": dst_crs,
            "transform": transform,
            "width": width,
            "height": height,
        })
        with rasterio.open(dst_path, "w", **kwargs) as dst:
            for i in range(1, src.count + 1):
                reproject(
                    source=rasterio.band(src, i),
                    destination=rasterio.band(dst, i),
                    src_transform=src.transform,
                    src_crs=src.crs,
                    dst_transform=transform,
                    dst_crs=dst_crs,
                    resampling=Resampling.bilinear,
                )
    return dst_path


# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------

def gerar_curva_cota_volume_dem(
    *,
    lat: float,
    lon: float,
    dem_path: Path,
    bacia_polygon_4326: Polygon | dict | None = None,
    z_max_m: float | None = None,
    delta_h_max_m: float = 10.0,
    n_pontos: int = 20,
    z_min_clip_m: float | None = None,
) -> CurvaCotaVolumeDEM:
    """
    Gera curva cota-volume + mancha de inundacao para reservatorio in-line.

    Parameters
    ----------
    lat, lon : ponto da barragem em EPSG:4326.
    dem_path : path do GeoTIFF (qualquer CRS — sera reprojetado para UTM local).
    bacia_polygon_4326 : opcional. Polygon em EPSG:4326 (ou GeoJSON dict). Se
                         dado, restringe o flood-fill aa bacia. Sem ele, o
                         lago pode "vazar" pra fora a montante.
    z_max_m : cota absoluta maxima. Se None, usa z_fundo + delta_h_max_m.
    delta_h_max_m : altura maxima do barramento em m (usada quando z_max_m=None).
    n_pontos : pontos da curva (cotas igualmente espacadas em z).
    z_min_clip_m : se dado, ignora pixels com z < z_min_clip_m. Util para
                   reservatorios existentes — define o fundo presumido.

    Returns
    -------
    CurvaCotaVolumeDEM com cotas absolutas no datum vertical do DEM (Copernicus
    GLO-30 vem em EGM2008).
    """
    dst_crs = _utm_zone_from_lonlat(lat, lon)
    # Se o DEM ja esta em UTM (qualquer EPSG metrico), pula reprojeto — economia
    # de tempo e evita interpolacao a mais. Caso contrario, reprojeta para o UTM
    # local do ponto da barragem.
    with rasterio.open(dem_path) as _probe:
        src_crs = _probe.crs
    if src_crs is not None and src_crs.is_projected and src_crs.linear_units in ("metre", "meter"):
        utm_path = dem_path
    else:
        utm_path = dem_path.with_suffix(".utm.tif")
        if not utm_path.exists():
            _reproject_to_utm(dem_path, utm_path, dst_crs)

    with rasterio.open(utm_path) as src:
        if bacia_polygon_4326 is not None:
            geom_dict = (
                mapping(bacia_polygon_4326)
                if isinstance(bacia_polygon_4326, Polygon)
                else bacia_polygon_4326
            )
            geom_utm = _reproject_geojson(geom_dict, "EPSG:4326", dst_crs)
            arr, transform = rio_mask(src, [geom_utm], crop=True, filled=True, nodata=src.nodata)
            arr = arr[0]
        else:
            arr = src.read(1)
            transform = src.transform

        nodata = src.nodata
        crs_out = src.crs.to_string()

    px_w = abs(transform.a)
    px_h = abs(transform.e)
    pixel_area = px_w * px_h
    pixel_size = (px_w + px_h) / 2.0

    # Pixel da barragem (em UTM)
    x_utm, y_utm = _project_point_4326_to(dst_crs, lat, lon)
    col = int((x_utm - transform.c) / transform.a)
    row = int((y_utm - transform.f) / transform.e)
    if not (0 <= row < arr.shape[0] and 0 <= col < arr.shape[1]):
        raise ValueError(
            f"Ponto da barragem ({lat}, {lon}) fora do recorte do DEM/bacia. "
            f"row={row}, col={col}, shape={arr.shape}."
        )

    # Mascara de validos (so nodata).
    arr_f = arr.astype(np.float64, copy=False)
    if nodata is not None:
        valid = arr_f != nodata
    else:
        valid = np.isfinite(arr_f)

    if not valid[row, col]:
        raise ValueError(
            f"Pixel da barragem (row={row}, col={col}) e nodata. "
            "Reescolha o ponto."
        )

    # Piso do reservatorio = cota do pixel da barragem (modelo "leito plano"
    # do paramento ate a montante). Pixels naturalmente mais baixos que isso
    # sao elevados ao piso — fisicamente: fluxo da bacia para o reservatorio
    # converge ao piso da barragem, abaixo nao ha volume util.
    #
    # `z_min_clip_m` overscreve esse piso quando dado (caso lago existente:
    # piso real do leito mais alto que a cota da lamina d'agua atual).
    z_pixel_barragem = float(arr_f[row, col])
    piso_m = z_min_clip_m if z_min_clip_m is not None else z_pixel_barragem
    arr_f = np.where(valid, np.maximum(arr_f, piso_m), arr_f)
    z_fundo = piso_m
    if z_max_m is None:
        z_max_m = z_fundo + delta_h_max_m
    if z_max_m <= z_fundo:
        raise ValueError(f"z_max ({z_max_m}) <= z_fundo ({z_fundo}).")

    cotas = np.linspace(z_fundo, z_max_m, n_pontos)
    volumes = np.zeros(n_pontos)
    areas = np.zeros(n_pontos)

    last_mask = None
    for i, z_i in enumerate(cotas):
        # candidatos: validos e abaixo (ou iguais) da cota
        cand = valid & (arr_f <= z_i)
        if not cand[row, col]:
            # Em z=z_fundo o proprio pixel ja e <= z_i por construcao;
            # se cair aqui, esta tudo zero
            volumes[i] = 0.0
            areas[i] = 0.0
            continue
        # connected component 4-conexo contendo (row, col)
        labels, _ = label(cand, structure=np.array([
            [0, 1, 0],
            [1, 1, 1],
            [0, 1, 0],
        ]))
        comp = labels[row, col]
        mask = labels == comp
        n_pix = int(mask.sum())
        depths = z_i - arr_f[mask]
        volumes[i] = float(depths.sum() * pixel_area)
        areas[i] = float(n_pix * pixel_area)
        if i == n_pontos - 1:
            last_mask = mask

    # Poligonaliza a mascara da cota maxima
    if last_mask is None or not last_mask.any():
        mancha_geojson = json.dumps({"type": "FeatureCollection", "features": []})
    else:
        mask_uint8 = last_mask.astype(np.uint8)
        feats = [
            {
                "type": "Feature",
                "geometry": geom,
                "properties": {"value": int(val)},
            }
            for geom, val in shapes(mask_uint8, mask=mask_uint8 == 1, transform=transform)
        ]
        # Reprojeta para EPSG:4326 para folium
        feats_4326 = [
            {
                "type": "Feature",
                "geometry": _reproject_geojson(f["geometry"], dst_crs, "EPSG:4326"),
                "properties": f["properties"],
            }
            for f in feats
        ]
        mancha_geojson = json.dumps({"type": "FeatureCollection", "features": feats_4326})

    return CurvaCotaVolumeDEM(
        cotas_m=cotas,
        volumes_m3=volumes,
        areas_m2=areas,
        z_fundo_m=z_fundo,
        mancha_geojson=mancha_geojson,
        dem_crs=crs_out,
        pixel_size_m=pixel_size,
        n_pixels_z_max=int(last_mask.sum()) if last_mask is not None else 0,
    )


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------

def _project_point_4326_to(dst_crs: str, lat: float, lon: float) -> tuple[float, float]:
    """Projeta lat/lon (EPSG:4326) -> (x, y) no dst_crs via rasterio.warp."""
    from rasterio.warp import transform as warp_transform
    xs, ys = warp_transform("EPSG:4326", dst_crs, [lon], [lat])
    return float(xs[0]), float(ys[0])


def _reproject_geojson(geom_dict: dict, src_crs: str, dst_crs: str) -> dict:
    """Reprojeta um GeoJSON dict (qualquer geometria) entre CRSs."""
    from rasterio.warp import transform as warp_transform
    g = shape(geom_dict)
    if g.geom_type == "Polygon":
        coords = [list(g.exterior.coords)] + [list(r.coords) for r in g.interiors]
    elif g.geom_type == "MultiPolygon":
        coords = []
        for p in g.geoms:
            coords.append([list(p.exterior.coords)] + [list(r.coords) for r in p.interiors])
    else:
        # generic: roundtrip via mapping
        return mapping(g)

    def _xform_ring(ring):
        xs = [c[0] for c in ring]
        ys = [c[1] for c in ring]
        nx, ny = warp_transform(src_crs, dst_crs, xs, ys)
        return list(zip(nx, ny))

    if g.geom_type == "Polygon":
        new_rings = [_xform_ring(r) for r in coords]
        return {"type": "Polygon", "coordinates": new_rings}
    else:
        new_polys = []
        for poly_rings in coords:
            new_polys.append([_xform_ring(r) for r in poly_rings])
        return {"type": "MultiPolygon", "coordinates": new_polys}

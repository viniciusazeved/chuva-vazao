"""
Delineamento automatico de bacia via WhiteboxTools.

Pipeline:
    1. Reprojeta DEM para UTM apropriado (calculos metricos corretos).
    2. BreachDepressions (remove pocos artificiais).
    3. D8Pointer (direcao de fluxo).
    4. D8FlowAccumulation (acumulacao em celulas).
    5. ExtractStreams (threshold configuravel, default 100 celulas).
    6. JensonSnapPourPoints (snap do exutorio ao canal, dist max configuravel).
    7. Watershed (delineamento).
    8. RasterToVectorPolygons (conversao bacia para shapefile).
    9. RasterStreamsToVector (rede de drenagem).

Referencias:
- Lindsay, J. (2014). WhiteboxTools User Manual.
- NRCS (1972). National Engineering Handbook, Section 4.
"""
from __future__ import annotations

import importlib.util
import os
import platform
import shutil
import tempfile
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

# Antes de importar rasterio/geopandas, apontar PROJ_DATA/GDAL_DATA para os
# bundles do proprio rasterio. Evita conflito com PROJ do PostgreSQL/PostGIS
# quando ambos estao instalados no mesmo host Windows (bug classico: o env
# aponta pra um proj.db com layout incompativel).
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

import geopandas as gpd
import numpy as np
import rasterio
import requests
import whitebox
from rasterio.mask import mask as rio_mask
from rasterio.warp import (
    Resampling,
    calculate_default_transform,
    reproject,
    transform as warp_transform,
)
from shapely.geometry import Point


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DEM_CACHE = PROJECT_ROOT / "data" / "dems"
OPENTOPO_URL = "https://portal.opentopography.org/API/globaldem"


_WBT_LINKS = {
    "Windows": "https://www.whiteboxgeo.com/WBT_Windows/WhiteboxTools_win_amd64.zip",
    "Darwin": "https://www.whiteboxgeo.com/WBT_Darwin/WhiteboxTools_darwin_amd64.zip",
    "Darwin-arm": "https://www.whiteboxgeo.com/WBT_Darwin/WhiteboxTools_darwin_m_series.zip",
    "Linux": "https://www.whiteboxgeo.com/WBT_Linux/WhiteboxTools_linux_amd64.zip",
}


def _ensure_whitebox_binary() -> Path:
    """
    Garante o binario WhiteboxTools num diretorio gravavel e devolve o path
    do dir que contem `whitebox_tools` (ou .exe). Default site-packages
    nao e gravavel em Streamlit Cloud — daqui em diante usamos ~/.cache.
    """
    cache_root = Path(os.environ.get("WBT_CACHE_DIR", Path.home() / ".cache" / "whitebox"))
    wbt_dir = cache_root / "WBT"
    exe_name = "whitebox_tools.exe" if platform.system() == "Windows" else "whitebox_tools"
    if (wbt_dir / exe_name).exists():
        return wbt_dir

    sysname = platform.system()
    if sysname == "Darwin" and platform.processor() == "arm":
        url = _WBT_LINKS["Darwin-arm"]
    else:
        url = _WBT_LINKS.get(sysname)
    if not url:
        raise RuntimeError(f"WhiteboxTools nao tem binario para {sysname}.")

    cache_root.mkdir(parents=True, exist_ok=True)
    zip_path = cache_root / Path(url).name
    urllib.request.urlretrieve(url, zip_path)
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(cache_root)
    zip_path.unlink(missing_ok=True)

    # ZIP extrai em <cache_root>/WhiteboxTools_<plat>/WBT/. Move WBT pra cache_root/WBT.
    extracted = next(
        (p for p in cache_root.iterdir() if p.is_dir() and (p / "WBT").exists()),
        None,
    )
    if extracted:
        if wbt_dir.exists():
            shutil.rmtree(wbt_dir)
        shutil.move(str(extracted / "WBT"), str(wbt_dir))
        shutil.rmtree(extracted, ignore_errors=True)

    if sysname != "Windows":
        os.chmod(wbt_dir / exe_name, 0o755)
        plugins_dir = wbt_dir / "plugins"
        if plugins_dir.exists():
            for p in plugins_dir.iterdir():
                if p.is_file() and p.suffix != ".json":
                    os.chmod(p, 0o755)
    return wbt_dir


def _new_whitebox_tools():
    """Instancia WhiteboxTools com binario num path gravavel."""
    wbt_dir = _ensure_whitebox_binary()
    # Sinaliza pro download_wbt() do pacote pular o download em site-packages
    os.environ["WBT_PATH"] = str(wbt_dir)
    wbt = whitebox.WhiteboxTools()
    wbt.set_whitebox_dir(str(wbt_dir))
    return wbt


def download_dem_gee(
    lat: float,
    lon: float,
    buffer_deg: float = 0.1,
    out_path: Path | None = None,
    scale_m: int = 30,
) -> Path:
    """
    Baixa DEM Copernicus GLO-30 via Google Earth Engine.

    Alternativa a `download_dem_opentopography`. Vantagem: nao precisa
    de API key OpenTopography — usa o projeto GEE ja autenticado.

    Parameters
    ----------
    lat, lon : coordenadas do centro (EPSG:4326).
    buffer_deg : buffer ao redor do centro (graus). 0.1 ~= 11 km.
    out_path : path destino. Default: cache deterministico do gee_client.
    scale_m : resolucao do raster em metros. GLO-30 nativo = 30 m.
    """
    from chuva_vazao import gee_client

    geom = gee_client.bbox_from_point(lat, lon, buffer_deg=buffer_deg)
    if out_path is not None:
        return gee_client.fetch_dem_copernicus(
            geom, out_path=out_path, scale_m=scale_m,
        )
    return gee_client.fetch_dem_copernicus(geom, scale_m=scale_m)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class BasinMetrics:
    area_km2: float
    perimeter_km: float
    flowlength_km: float
    slope_mean_pct: float          # declividade do TALVEGUE (dH_talvegue / L)
    elev_max_m: float
    elev_min_m: float
    delta_h_m: float               # amplitude altimetrica da bacia (Zmax - Zmin)
    delta_h_talvegue_m: float = 0.0  # queda ao longo do talvegue (nascente -> exutorio)

    def summary_dict(self) -> dict:
        """
        Chaves ASCII-safe para uso em PDF (FPDF default Helvetica). Para exibir
        com simbolos unicode bonitinhos (²/Δ) na UI Streamlit, prefira pegar os
        campos diretamente do dataclass.
        """
        return {
            "A (km2)": round(self.area_km2, 3),
            "P (km)": round(self.perimeter_km, 3),
            "L canal (km)": round(self.flowlength_km, 3),
            "S media (%)": round(self.slope_mean_pct, 2),
            "Z max (m)": round(self.elev_max_m, 1),
            "Z min (m)": round(self.elev_min_m, 1),
            "dH bacia (m)": round(self.delta_h_m, 1),
            "dH talvegue (m)": round(self.delta_h_talvegue_m, 1),
        }


@dataclass
class BasinResult:
    basin_gdf: gpd.GeoDataFrame     # poligono da bacia em EPSG:4326
    stream_gdf: gpd.GeoDataFrame    # rede de drenagem em EPSG:4326
    outlet_original: Point          # ponto clicado (EPSG:4326)
    outlet_snapped: Point           # ponto ajustado ao canal (EPSG:4326)
    metrics: BasinMetrics
    dem_utm_path: Path              # DEM reprojetado (fica no disco)
    work_dir: Path                  # diretorio de trabalho WBT
    clipped_by_dem: bool = False    # True se a bacia encosta na borda do DEM (truncada)
    n_fragmentos_removidos: int = 0  # poligonos espurios descartados na vetorizacao
    area_fragmentos_frac: float = 0.0  # fracao de area que os fragmentos representavam


# ---------------------------------------------------------------------------
# UTM helper
# ---------------------------------------------------------------------------

def utm_epsg_for(lat: float, lon: float) -> int:
    """
    Codigo EPSG do UTM apropriado para a coordenada.
        Norte: 326XX, Sul: 327XX, onde XX e a zona 1..60.
    """
    zone = int((lon + 180) / 6) + 1
    return (32700 if lat < 0 else 32600) + zone


# ---------------------------------------------------------------------------
# Download de DEM via OpenTopography
# ---------------------------------------------------------------------------

def download_dem_opentopography(
    lat: float,
    lon: float,
    buffer_deg: float = 0.1,
    dem_type: str = "COP30",
    api_key: str | None = None,
    out_path: Path | None = None,
    timeout_s: int = 120,
) -> Path:
    """
    Baixa DEM via OpenTopography API.

    Parameters
    ----------
    lat, lon : coordenadas do centro (EPSG:4326).
    buffer_deg : buffer ao redor do centro (graus). 0.1 ~= 11km.
    dem_type : "COP30" (Copernicus 30m), "SRTMGL1" (SRTM 30m),
               "SRTMGL3" (SRTM 90m), "COP90", "NASADEM".
    api_key : chave OpenTopography. Se None, le OPENTOPO_API_KEY do env.
    out_path : path destino. Default: data/dems/dem_<TYPE>_<lat>_<lon>.tif.

    Nota: requer internet + API key gratuita em portal.opentopography.org.
    """
    if api_key is None:
        from dotenv import load_dotenv
        load_dotenv()
        api_key = os.environ.get("OPENTOPO_API_KEY")
    if not api_key:
        raise ValueError(
            "OpenTopography API key ausente. Crie em https://portal.opentopography.org/"
            " e defina OPENTOPO_API_KEY no .env ou passe via argumento. "
            "Alternativa: use um DEM local (parametro dem_path em delineate_basin)."
        )

    south = lat - buffer_deg
    north = lat + buffer_deg
    west = lon - buffer_deg
    east = lon + buffer_deg

    params = {
        "demtype": dem_type,
        "south": south,
        "north": north,
        "west": west,
        "east": east,
        "outputFormat": "GTiff",
        "API_Key": api_key,
    }

    if out_path is None:
        DEFAULT_DEM_CACHE.mkdir(parents=True, exist_ok=True)
        out_path = DEFAULT_DEM_CACHE / f"dem_{dem_type}_{lat:.4f}_{lon:.4f}.tif"

    if out_path.exists():
        return out_path

    r = requests.get(OPENTOPO_URL, params=params, timeout=timeout_s)
    r.raise_for_status()
    if not r.content or len(r.content) < 1000:
        raise RuntimeError(
            f"Resposta vazia do OpenTopography (len={len(r.content)}). "
            f"Verifique API key e coordenadas."
        )
    out_path.write_bytes(r.content)
    return out_path


# ---------------------------------------------------------------------------
# Reprojecao DEM -> UTM
# ---------------------------------------------------------------------------

def reproject_dem_to_utm(
    dem_path: Path,
    lat: float,
    lon: float,
    out_path: Path | None = None,
) -> Path:
    """Reprojeta DEM para UTM apropriado via rasterio."""
    epsg = utm_epsg_for(lat, lon)

    if out_path is None:
        out_path = dem_path.with_suffix(f".utm{epsg}.tif")

    with rasterio.open(dem_path) as src:
        transform, width, height = calculate_default_transform(
            src.crs, f"EPSG:{epsg}", src.width, src.height, *src.bounds,
        )
        kwargs = src.meta.copy()
        kwargs.update({
            "crs": f"EPSG:{epsg}",
            "transform": transform,
            "width": width,
            "height": height,
        })
        with rasterio.open(out_path, "w", **kwargs) as dst:
            for i in range(1, src.count + 1):
                reproject(
                    source=rasterio.band(src, i),
                    destination=rasterio.band(dst, i),
                    src_transform=src.transform,
                    src_crs=src.crs,
                    dst_transform=transform,
                    dst_crs=f"EPSG:{epsg}",
                    resampling=Resampling.bilinear,
                )
    return out_path


# ---------------------------------------------------------------------------
# Delineamento
# ---------------------------------------------------------------------------

def delineate_basin(
    lat: float,
    lon: float,
    dem_path: Path,
    snap_dist_m: float = 200.0,
    stream_threshold: int = 100,
    work_dir: Path | None = None,
    snap_method: str = "accumulation",
) -> BasinResult:
    """
    Delineia a bacia de contribuicao para um exutorio.

    Parameters
    ----------
    lat, lon : coordenadas do exutorio (EPSG:4326).
    dem_path : DEM de entrada (qualquer CRS; sera reprojetado para UTM).
    snap_dist_m : distancia maxima de snap ao canal (m).
    stream_threshold : numero minimo de celulas de acumulacao para canal.
    work_dir : diretorio temporario para artefatos WBT. Default = temp dir.
    snap_method : como ajustar o exutorio ao canal.
        "accumulation" (default) -> move pro pixel de MAIOR acumulacao no raio
            (o rio principal). Robusto: nao depende do threshold de canal.
        "jenson" -> move pro canal (stream) mais proximo. Respeita melhor a
            intencao do clique, mas com threshold baixo gruda em corrego lateral.
    """
    if work_dir is None:
        work_dir = Path(tempfile.mkdtemp(prefix="basin_"))
    work_dir = Path(work_dir).resolve()
    work_dir.mkdir(parents=True, exist_ok=True)

    epsg_utm = utm_epsg_for(lat, lon)

    # 0. Validar que o exutorio cai dentro do DEM. Erro classico: baixar o DEM
    # (recortado no exutorio anterior) e so depois mover/clicar o exutorio para
    # fora dessa area — o snap nao acha canal e a bacia sai vazia.
    with rasterio.open(dem_path) as _src:
        _xs, _ys = warp_transform("EPSG:4326", _src.crs, [lon], [lat])
        _px, _py = _xs[0], _ys[0]
        _b = _src.bounds
        if not (_b.left <= _px <= _b.right and _b.bottom <= _py <= _b.top):
            raise RuntimeError(
                "Exutorio fora da area coberta pelo DEM. Marque o exutorio no "
                "mapa PRIMEIRO e baixe o DEM depois — o DEM e recortado ao "
                "redor do exutorio. Alternativa: aumente o buffer do DEM."
            )

    # 1. Reprojetar DEM
    dem_utm = reproject_dem_to_utm(
        Path(dem_path), lat, lon, out_path=work_dir / "dem_utm.tif",
    )

    # 2. WBT setup
    wbt = _new_whitebox_tools()
    # Verbose ligado para o callback capturar mensagens de erro do binario;
    # filtramos as linhas de progresso (%) no proprio callback.
    wbt.set_verbose_mode(True)
    wbt.set_working_dir(str(work_dir))

    _wbt_log: list[str] = []

    def _cb(msg: object) -> None:
        s = str(msg).strip()
        # Descarta linhas de progresso ("Progress: 42%") e vazias.
        if s and "%" not in s:
            _wbt_log.append(s)

    def _run(label: str, fn, *, out: str | Path | None = None, **kwargs):
        """
        Executa uma ferramenta WBT, captura a saida e falha cedo com mensagem
        util. Sem isso, uma etapa que quebra no meio do pipeline so estoura la
        no `gpd.read_file` final com um generico "No such file or directory".
        """
        _wbt_log.clear()
        try:
            ret = fn(callback=_cb, **kwargs)
        except Exception as exc:
            tail = "\n".join(_wbt_log[-15:])
            raise RuntimeError(
                f"WhiteboxTools falhou na etapa '{label}': {exc}"
                + (f"\nSaida WBT:\n{tail}" if tail else "")
            ) from exc
        if ret is not None and ret != 0:
            tail = "\n".join(_wbt_log[-15:])
            raise RuntimeError(
                f"WhiteboxTools retornou codigo {ret} na etapa '{label}'."
                + (f"\nSaida WBT:\n{tail}" if tail else "")
            )
        if out is not None:
            out_abs = out if Path(out).is_absolute() else work_dir / out
            if not Path(out_abs).exists():
                tail = "\n".join(_wbt_log[-15:])
                raise RuntimeError(
                    f"WhiteboxTools nao gerou '{Path(out).name}' na etapa "
                    f"'{label}'. Etapa anterior pode ter produzido raster vazio."
                    + (f"\nSaida WBT:\n{tail}" if tail else "")
                )
        return ret

    # 3. Breach depressions
    breached = "dem_breached.tif"
    _run("breach_depressions", wbt.breach_depressions,
         out=breached, dem=str(dem_utm), output=breached)

    # 4. D8 pointer
    d8_ptr = "d8_ptr.tif"
    _run("d8_pointer", wbt.d8_pointer, out=d8_ptr, dem=breached, output=d8_ptr)

    # 5. D8 flow accumulation
    d8_acc = "d8_acc.tif"
    _run("d8_flow_accumulation", wbt.d8_flow_accumulation,
         out=d8_acc, i=d8_ptr, output=d8_acc, pntr=True)

    # 6. Extract streams
    streams = "streams.tif"
    _run("extract_streams", wbt.extract_streams,
         out=streams, flow_accum=d8_acc, output=streams,
         threshold=stream_threshold)

    # 7. Outlet shapefile em UTM
    outlet_4326 = gpd.GeoDataFrame(
        geometry=[Point(lon, lat)], crs="EPSG:4326",
    )
    outlet_utm = outlet_4326.to_crs(epsg=epsg_utm)
    outlet_shp = work_dir / "outlet.shp"
    outlet_utm.to_file(outlet_shp)

    # 8. Snap do exutorio ao canal
    outlet_snapped_shp = work_dir / "outlet_snapped.shp"
    if snap_method == "jenson":
        _run("jenson_snap_pour_points", wbt.jenson_snap_pour_points,
             out=outlet_snapped_shp,
             pour_pts=str(outlet_shp), streams=streams,
             output=str(outlet_snapped_shp), snap_dist=snap_dist_m)
    else:
        # "accumulation": move pro pixel de MAIOR acumulacao no raio (rio
        # principal) — robusto ao threshold e ao tamanho do DEM, evita grudar
        # num corrego lateral perto do exutorio.
        _run("snap_pour_points", wbt.snap_pour_points,
             out=outlet_snapped_shp,
             pour_pts=str(outlet_shp), flow_accum=d8_acc,
             output=str(outlet_snapped_shp), snap_dist=snap_dist_m)

    # 9. Watershed
    basin_rst = "basin.tif"
    _run("watershed", wbt.watershed,
         out=basin_rst, d8_pntr=d8_ptr,
         pour_pts=str(outlet_snapped_shp), output=basin_rst)

    # 10. Vetorizar bacia
    basin_shp_rel = "basin.shp"
    _run("raster_to_vector_polygons", wbt.raster_to_vector_polygons,
         out=basin_shp_rel, i=basin_rst, output=basin_shp_rel)

    # 11. Vetorizar rede de drenagem
    streams_shp_rel = "streams.shp"
    _run("raster_streams_to_vector", wbt.raster_streams_to_vector,
         out=streams_shp_rel, streams=streams, d8_pntr=d8_ptr,
         output=streams_shp_rel)

    # 12. Caminho mais longo (canal principal, do topo da bacia ate o exutorio)
    longest_shp_rel = "longest_flowpath.shp"
    longest_path_m: float | None = None
    dh_talvegue_m: float | None = None
    try:
        wbt.longest_flowpath(
            dem=breached, basins=basin_rst, output=longest_shp_rel,
        )
        longest_gdf = gpd.read_file(work_dir / longest_shp_rel)
        if longest_gdf.crs is None:
            longest_gdf.set_crs(epsg=epsg_utm, inplace=True)
        if len(longest_gdf) > 0:
            # Atributo LENGTH do WBT vem em metros; fallback = geom.length
            if "LENGTH" in longest_gdf.columns:
                longest_path_m = float(longest_gdf["LENGTH"].max())
            else:
                longest_path_m = float(longest_gdf.geometry.length.max())
            # Queda ao longo do TALVEGUE: cotas amostradas do DEM nas duas pontas
            # do caminho mais longo (nascente e exutorio). E a queda correta para
            # a declividade do tc — NAO o Zmax-Zmin da bacia inteira (que inclui
            # o topo do divisor e superestima S, subestimando o tc).
            try:
                ep = _endpoints_linha(longest_gdf.geometry.union_all())
                if ep is not None:
                    with rasterio.open(dem_utm) as _dsrc:
                        zs = [v[0] for v in _dsrc.sample([ep[0], ep[1]])]
                    if len(zs) == 2 and all(z is not None for z in zs):
                        dh_talvegue_m = abs(float(zs[0]) - float(zs[1]))
            except Exception:
                dh_talvegue_m = None
    except Exception:
        longest_path_m = None

    # Carregar
    basin_gdf_utm = gpd.read_file(work_dir / basin_shp_rel)
    if basin_gdf_utm.crs is None:
        basin_gdf_utm.set_crs(epsg=epsg_utm, inplace=True)
    if len(basin_gdf_utm) == 0 or basin_gdf_utm.geometry.is_empty.all():
        raise RuntimeError(
            "Bacia delineada ficou vazia. O exutorio provavelmente nao foi "
            "ajustado a nenhum canal — aumente o 'Snap (m)' ou reduza o "
            "'Threshold canal (celulas)' para uma rede de drenagem mais densa."
        )
    # Descarta fragmentos espurios da vetorizacao (mantem so a bacia conexa).
    # Feito ANTES de metricas, clip de streams, mapa e recorte de CN, para que
    # todos usem a geometria limpa.
    basin_gdf_utm, n_fragmentos, area_frag_frac = _filtrar_fragmentos(basin_gdf_utm)
    stream_gdf_utm = gpd.read_file(work_dir / streams_shp_rel)
    if stream_gdf_utm.crs is None:
        stream_gdf_utm.set_crs(epsg=epsg_utm, inplace=True)
    outlet_snapped_gdf_utm = gpd.read_file(outlet_snapped_shp)
    if outlet_snapped_gdf_utm.crs is None:
        outlet_snapped_gdf_utm.set_crs(epsg=epsg_utm, inplace=True)

    # Cortar streams pela bacia
    try:
        streams_in_basin_utm = gpd.overlay(
            stream_gdf_utm, basin_gdf_utm, how="intersection",
        )
    except Exception:
        streams_in_basin_utm = stream_gdf_utm

    # Metricas (usa longest_path_m se disponivel; senao cai no max segmento)
    metrics = _compute_metrics(
        basin_gdf_utm, streams_in_basin_utm, dem_utm,
        longest_path_m=longest_path_m,
        dh_talvegue_m=dh_talvegue_m,
    )

    # Reprojetar para WGS84 (mapa)
    basin_4326 = basin_gdf_utm.to_crs(epsg=4326)
    streams_4326 = streams_in_basin_utm.to_crs(epsg=4326)
    outlet_snapped_4326 = outlet_snapped_gdf_utm.to_crs(epsg=4326)

    # Deteccao de corte: se a bacia encosta na borda do raster, ela foi
    # provavelmente TRUNCADA pela extensao do DEM baixado (buffer pequeno) — o
    # divisor de aguas real fica fora do DEM e a bacia sai com um lado reto.
    clipped = False
    try:
        with rasterio.open(work_dir / basin_rst) as bsrc:
            barr = bsrc.read(1)
            bnod = bsrc.nodata
        bmask = (barr > 0) if bnod is None else ((barr > 0) & (barr != bnod))
        if bmask.any():
            clipped = bool(
                bmask[0, :].any() or bmask[-1, :].any()
                or bmask[:, 0].any() or bmask[:, -1].any()
            )
    except Exception:
        clipped = False

    return BasinResult(
        basin_gdf=basin_4326,
        stream_gdf=streams_4326,
        outlet_original=Point(lon, lat),
        outlet_snapped=outlet_snapped_4326.geometry.iloc[0],
        metrics=metrics,
        dem_utm_path=dem_utm,
        work_dir=work_dir,
        clipped_by_dem=clipped,
        n_fragmentos_removidos=n_fragmentos,
        area_fragmentos_frac=area_frag_frac,
    )


def _filtrar_fragmentos(
    gdf: gpd.GeoDataFrame,
) -> tuple[gpd.GeoDataFrame, int, float]:
    """
    Mantem apenas o maior poligono conexo (a bacia real).

    A conversao raster->vetor (RasterToVectorPolygons) gera fragmentos espurios
    — pixels isolados na borda do watershed — que NAO fazem parte da bacia e
    inflam area, perimetro e o recorte de LULC/solo (logo o CN). O watershed de
    um exutorio unico e sempre conexo, entao o maior poligono E a bacia; o resto
    e ruido da vetorizacao.

    Retorna (gdf_so_o_maior, n_fragmentos_removidos, fracao_de_area_removida).
    """
    partes = gdf.geometry.explode(index_parts=False).reset_index(drop=True)
    partes = partes[(~partes.is_empty) & (partes.geom_type == "Polygon")]
    if len(partes) <= 1:
        return gdf, 0, 0.0
    areas = partes.area
    area_total = float(areas.sum())
    idx_maior = areas.idxmax()
    maior = partes.loc[idx_maior]
    n_removidos = int(len(partes) - 1)
    frac_removida = (
        (area_total - float(maior.area)) / area_total if area_total > 0 else 0.0
    )
    limpo = gpd.GeoDataFrame(geometry=[maior], crs=gdf.crs)
    return limpo, n_removidos, frac_removida


def _endpoints_linha(geom):
    """Pontos extremos (inicio, fim) de uma LineString/MultiLineString (x, y)."""
    try:
        if geom.geom_type == "LineString":
            cs = list(geom.coords)
            return (cs[0][:2], cs[-1][:2])
        if geom.geom_type == "MultiLineString":
            parts = list(geom.geoms)
            return (list(parts[0].coords)[0][:2], list(parts[-1].coords)[-1][:2])
    except Exception:
        return None
    return None


def _compute_metrics(
    basin_gdf_utm: gpd.GeoDataFrame,
    streams_gdf_utm: gpd.GeoDataFrame,
    dem_utm_path: Path,
    longest_path_m: float | None = None,
    dh_talvegue_m: float | None = None,
) -> BasinMetrics:
    """
    Calcula A, P, L, S, DH a partir dos GDFs em UTM e do DEM reprojetado.

    L (flow_length_m) prioriza `longest_path_m` (caminho unico topo->exutorio
    calculado pelo WhiteboxTools `longest_flowpath`). Fallback = maior segmento
    individual da rede vetorizada (so valido em bacias com canal unico).
    """
    basin_union = basin_gdf_utm.union_all()
    area_m2 = float(basin_union.area)
    perim_m = float(basin_union.length)

    if longest_path_m is not None and longest_path_m > 0:
        flow_length_m = float(longest_path_m)
    elif len(streams_gdf_utm) > 0:
        try:
            # Soma dos segmentos da rede — aproximacao grosseira se longest_flowpath falhar
            flow_length_m = float(streams_gdf_utm.geometry.length.sum())
        except Exception:
            flow_length_m = 0.0
    else:
        flow_length_m = 0.0

    # Elevacoes dentro da bacia
    try:
        with rasterio.open(dem_utm_path) as src:
            nodata = src.nodata if src.nodata is not None else -9999
            masked, _ = rio_mask(src, [basin_union], crop=True, nodata=nodata)
            elev = masked[0]
            mask_valid = (elev != nodata) & ~np.isnan(elev)
            elev_valid = elev[mask_valid]
            if elev_valid.size == 0:
                raise ValueError("DEM vazio dentro da bacia.")
            elev_max = float(elev_valid.max())
            elev_min = float(elev_valid.min())
    except Exception as exc:
        raise RuntimeError(f"Falha ao extrair elevacoes: {exc}") from exc

    delta_h = elev_max - elev_min
    # Declividade do tc usa a queda do TALVEGUE (nascente->exutorio). Fallback
    # para a amplitude da bacia quando o talvegue nao pode ser amostrado.
    dh_talvegue = (
        dh_talvegue_m if (dh_talvegue_m is not None and dh_talvegue_m > 0)
        else delta_h
    )
    slope_pct = (dh_talvegue / flow_length_m * 100.0) if flow_length_m > 0 else 0.0

    return BasinMetrics(
        area_km2=area_m2 / 1e6,
        perimeter_km=perim_m / 1000.0,
        flowlength_km=flow_length_m / 1000.0,
        slope_mean_pct=slope_pct,
        elev_max_m=elev_max,
        elev_min_m=elev_min,
        delta_h_m=delta_h,
        delta_h_talvegue_m=dh_talvegue,
    )

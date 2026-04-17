"""
Cliente Google Earth Engine para extracao de DEM, uso do solo e solo.

Fornece funcoes de alto nivel que recebem uma geometria (bbox ou polygon em
EPSG:4326) e devolvem arquivos GeoTIFF salvos em disco, prontos para rasterio.

Produtos suportados:
- DEM Copernicus GLO-30 (`COPERNICUS/DEM/GLO30`, 30 m global).
- MapBiomas Col 9 Brasil (`projects/mapbiomas-public/...`, 30 m, 1985-2023).
- Dynamic World v1 (`GOOGLE/DYNAMICWORLD/V1`, 10 m global, Sentinel-2).
- SoilGrids textura 0-5 cm (sand/clay `projects/soilgrids-isric/*`, 250 m).

Projeto GEE padrao: `ggeantigravity` (conforme CLAUDE.md global do usuario).

Autenticacao: `ee.Authenticate()` precisa ter sido rodado uma unica vez no
host. O `init()` aqui e idempotente e lida com fallback de projeto via env.
"""
from __future__ import annotations

import hashlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

import ee  # type: ignore
import requests
from shapely.geometry import Polygon, box, mapping, shape


DEFAULT_PROJECT = os.environ.get("GEE_PROJECT", "ggeantigravity")
DEFAULT_DEM_CACHE = Path(__file__).resolve().parent.parent / "data" / "gee_cache"


# ---------------------------------------------------------------------------
# Init
# ---------------------------------------------------------------------------

_INITIALIZED: bool = False


def init(project: str | None = None) -> str:
    """
    Inicializa o cliente Earth Engine de forma idempotente.

    Tenta na ordem:
        1. Service account via env vars `GEE_SERVICE_ACCOUNT_EMAIL` +
           `GEE_SERVICE_ACCOUNT_KEY_JSON` (modo producao Streamlit Cloud).
        2. `ee.Initialize(project=...)` com credenciais locais (modo dev local
           apos `earthengine authenticate`).

    Returns
    -------
    O project id efetivamente usado.
    """
    global _INITIALIZED
    proj = project or DEFAULT_PROJECT
    if _INITIALIZED:
        return proj

    sa_email = os.environ.get("GEE_SERVICE_ACCOUNT_EMAIL")
    sa_key_json = os.environ.get("GEE_SERVICE_ACCOUNT_KEY_JSON")
    if sa_email and sa_key_json:
        try:
            credentials = ee.ServiceAccountCredentials(sa_email, key_data=sa_key_json)
            ee.Initialize(credentials=credentials, project=proj)
            _INITIALIZED = True
            return proj
        except Exception as exc:
            raise RuntimeError(
                f"Falha ao inicializar GEE via service account '{sa_email}'. "
                "Confira o JSON da key e se a SA esta registrada em "
                "https://signup.earthengine.google.com/#!/service_accounts "
                f"com acesso ao projeto '{proj}'. Erro: {exc}"
            ) from exc

    try:
        ee.Initialize(project=proj)
    except Exception as exc:
        msg = str(exc).lower()
        if "credential" in msg or "authenticate" in msg or "default" in msg:
            raise RuntimeError(
                "GEE nao autenticado neste host. Opcoes:\n"
                "  (dev local) uv run earthengine authenticate\n"
                "  (producao) defina GEE_SERVICE_ACCOUNT_EMAIL e "
                "GEE_SERVICE_ACCOUNT_KEY_JSON como env vars ou secrets Streamlit.\n"
                f"Projeto alvo: '{proj}'. Override via GEE_PROJECT."
            ) from exc
        raise
    _INITIALIZED = True
    return proj


# ---------------------------------------------------------------------------
# Utilidades de geometria
# ---------------------------------------------------------------------------

def bbox_from_point(lat: float, lon: float, buffer_deg: float = 0.1) -> Polygon:
    """Retangulo lat/lon centrado no ponto, em EPSG:4326 (shapely Polygon)."""
    return box(lon - buffer_deg, lat - buffer_deg, lon + buffer_deg, lat + buffer_deg)


def _to_ee_geometry(geom: Polygon | dict) -> ee.Geometry:
    """Aceita shapely Polygon ou GeoJSON dict. Devolve ee.Geometry em EPSG:4326."""
    if isinstance(geom, Polygon):
        return ee.Geometry(mapping(geom))
    if isinstance(geom, dict):
        return ee.Geometry(geom)
    raise TypeError(f"Tipo nao suportado: {type(geom)}")


def _is_cache_valid(path: Path, min_pixels: int = 4) -> bool:
    """Verifica se o .tif em cache tem conteudo utilizavel (>= min_pixels)."""
    if not path.exists():
        return False
    try:
        import rasterio  # noqa: PLC0415
        with rasterio.open(path) as src:
            return src.height * src.width >= min_pixels
    except Exception:
        return False


def _cache_path(kind: str, geom: Polygon | dict, extra: str = "") -> Path:
    """Path deterministico para cachear resultados por geometria + produto."""
    geom_dict = mapping(geom) if isinstance(geom, Polygon) else geom
    centroid = shape(geom_dict).centroid
    h = hashlib.md5(
        f"{kind}_{extra}_{geom_dict}".encode(),
    ).hexdigest()[:10]
    DEFAULT_DEM_CACHE.mkdir(parents=True, exist_ok=True)
    return DEFAULT_DEM_CACHE / (
        f"{kind}_{centroid.y:.3f}_{centroid.x:.3f}_{extra}_{h}.tif"
    )


# ---------------------------------------------------------------------------
# Download helper (ee.Image -> disco)
# ---------------------------------------------------------------------------

def _download_ee_image(
    image: ee.Image,
    out_path: Path,
    region: ee.Geometry,
    scale: int,
    crs: str = "EPSG:4326",
    timeout_s: int = 300,
) -> Path:
    """
    Exporta ee.Image para GeoTIFF via `getDownloadURL` (sincrono, sem Drive).

    Limite: ~32 MB por request. Para bacias <= 250 km2 a 30 m essa via e mais
    que suficiente. Acima disso use Export to Drive.

    Pos-download valida com rasterio (header TIFF sozinho nao basta — GEE as
    vezes devolve TIFF com 1x1 pixel pra regiao sem dado).
    """
    import rasterio  # noqa: PLC0415

    url = image.getDownloadURL({
        "scale": scale,
        "region": region,
        "crs": crs,
        "format": "GEO_TIFF",
    })
    r = requests.get(url, timeout=timeout_s, stream=True)
    r.raise_for_status()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "wb") as f:
        for chunk in r.iter_content(chunk_size=1024 * 1024):
            if chunk:
                f.write(chunk)

    # Valida que o raster tem conteudo razoavel (mais robusto que checar bytes)
    try:
        with rasterio.open(out_path) as src:
            h, w = src.height, src.width
            if h * w < 4:
                raise RuntimeError(
                    f"Raster GEE tem {w}x{h} pixel(s), muito pequeno para ser usavel. "
                    "Provavel region muito pequena para o scale pedido "
                    f"(scale={scale} m). URL: {url[:200]}..."
                )
    except rasterio.RasterioIOError as exc:
        # Se rasterio nao consegue abrir, o conteudo nao e TIFF -> provavel erro
        raise RuntimeError(
            f"Download GEE nao e TIFF valido (rasterio: {exc}). "
            f"Tamanho do arquivo: {out_path.stat().st_size} B. URL: {url[:200]}..."
        ) from exc
    return out_path


# ---------------------------------------------------------------------------
# DEM Copernicus GLO-30
# ---------------------------------------------------------------------------

def fetch_dem_copernicus(
    geom: Polygon | dict,
    out_path: Path | None = None,
    scale_m: int = 30,
    use_cache: bool = True,
) -> Path:
    """
    Baixa DEM Copernicus GLO-30 recortado na geometria.

    Parameters
    ----------
    geom : shapely Polygon ou GeoJSON dict em EPSG:4326.
    out_path : path destino. Default = cache determinstico em data/gee_cache/.
    scale_m : resolucao do raster em metros. GLO-30 nativo e 30 m; downscale
              oficial para 10 m nao existe.

    Retorna
    -------
    Path do .tif em EPSG:4326.
    """
    init()
    region = _to_ee_geometry(geom)
    if out_path is None:
        out_path = _cache_path("dem_cop30", geom, extra=f"s{scale_m}")
    if use_cache and _is_cache_valid(out_path):
        return out_path

    # COPERNICUS/DEM/GLO30 e ImageCollection com uma banda 'DEM'. Mosaic devolve
    # uma ee.Image unica.
    dem = (
        ee.ImageCollection("COPERNICUS/DEM/GLO30")
        .select("DEM")
        .mosaic()
        .clip(region)
    )
    return _download_ee_image(dem, out_path, region, scale=scale_m)


# ---------------------------------------------------------------------------
# MapBiomas Col 9 (Brasil, 30 m)
# ---------------------------------------------------------------------------

MAPBIOMAS_C9_ASSET = (
    "projects/mapbiomas-public/assets/brazil/lulc/"
    "collection9/mapbiomas_collection90_integration_v1"
)


def fetch_mapbiomas(
    geom: Polygon | dict,
    ano: int = 2023,
    out_path: Path | None = None,
    scale_m: int = 30,
    use_cache: bool = True,
) -> Path:
    """
    Baixa MapBiomas Col 9 recortado na geometria para o ano selecionado.

    O asset e uma ee.Image com bandas `classification_<ano>` para 1985-2023.
    """
    init()
    region = _to_ee_geometry(geom)
    if out_path is None:
        out_path = _cache_path("mapbiomas_c9", geom, extra=f"a{ano}s{scale_m}")
    if use_cache and _is_cache_valid(out_path):
        return out_path

    band = f"classification_{ano}"
    image = ee.Image(MAPBIOMAS_C9_ASSET).select(band).rename("lulc").clip(region)
    return _download_ee_image(image, out_path, region, scale=scale_m)


# ---------------------------------------------------------------------------
# Dynamic World v1 (global, 10 m, Sentinel-2)
# ---------------------------------------------------------------------------

DW_ASSET = "GOOGLE/DYNAMICWORLD/V1"


def fetch_dynamic_world(
    geom: Polygon | dict,
    ano: int = 2024,
    out_path: Path | None = None,
    scale_m: int = 10,
    use_cache: bool = True,
) -> Path:
    """
    Baixa Dynamic World v1 recortado — banda `label` (moda anual).

    Classes:
        0 water, 1 trees, 2 grass, 3 flooded_vegetation, 4 crops,
        5 shrub_and_scrub, 6 built, 7 bare, 8 snow_and_ice.
    """
    init()
    region = _to_ee_geometry(geom)
    if out_path is None:
        out_path = _cache_path("dw", geom, extra=f"a{ano}s{scale_m}")
    if use_cache and _is_cache_valid(out_path):
        return out_path

    start = f"{ano}-01-01"
    end = f"{ano + 1}-01-01"
    coll = (
        ee.ImageCollection(DW_ASSET)
        .filterDate(start, end)
        .filterBounds(region)
        .select("label")
    )
    # Moda = classe mais frequente no ano (proxy do uso "dominante").
    image = coll.mode().rename("lulc").clip(region)
    return _download_ee_image(image, out_path, region, scale=scale_m)


# ---------------------------------------------------------------------------
# SoilGrids textura (areia + argila, 0-5 cm)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SoilTexturePaths:
    sand_tif: Path  # % areia 0-5 cm
    clay_tif: Path  # % argila 0-5 cm


def fetch_soilgrids_texture(
    geom: Polygon | dict,
    out_dir: Path | None = None,
    scale_m: int = 30,
    use_cache: bool = True,
) -> SoilTexturePaths:
    """
    Baixa SoilGrids v2 — fracao de areia e argila (g/kg) a 0-5 cm.

    Os assets oficiais SoilGrids no GEE estao em
    `projects/soilgrids-isric/<variavel>_mean` como ee.Image com bandas por
    profundidade (ex: `sand_0-5cm_mean`).

    Default scale_m=30 (upsampling nearest do nativo 250 m) para alinhar com
    grids LULC de MapBiomas 30 m ou Dynamic World 10 m — evita TIFF de 1 pixel
    quando a bacia e pequena.
    """
    init()
    region = _to_ee_geometry(geom)
    if out_dir is None:
        out_dir = DEFAULT_DEM_CACHE
    out_dir.mkdir(parents=True, exist_ok=True)

    sand_path = _cache_path("soilgrids_sand", geom, extra=f"s{scale_m}")
    clay_path = _cache_path("soilgrids_clay", geom, extra=f"s{scale_m}")

    if not (use_cache and _is_cache_valid(sand_path)):
        sand_img = (
            ee.Image("projects/soilgrids-isric/sand_mean")
            .select("sand_0-5cm_mean")
            .clip(region)
        )
        _download_ee_image(sand_img, sand_path, region, scale=scale_m)

    if not (use_cache and _is_cache_valid(clay_path)):
        clay_img = (
            ee.Image("projects/soilgrids-isric/clay_mean")
            .select("clay_0-5cm_mean")
            .clip(region)
        )
        _download_ee_image(clay_img, clay_path, region, scale=scale_m)

    return SoilTexturePaths(sand_tif=sand_path, clay_tif=clay_path)


# ---------------------------------------------------------------------------
# Saude do setup
# ---------------------------------------------------------------------------

def check_connection() -> dict:
    """Sanity check rapido — inicializa e tenta uma query simples."""
    try:
        proj = init()
        # Image.pixelArea().reduceRegion() eh super leve, so pra validar comm.
        pt = ee.Geometry.Point([-47.0, -22.0]).buffer(100)
        val = (
            ee.Image.pixelArea()
            .reduceRegion(reducer=ee.Reducer.mean(), geometry=pt, scale=30)
            .getInfo()
        )
        return {"ok": True, "project": proj, "test_value": val}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}

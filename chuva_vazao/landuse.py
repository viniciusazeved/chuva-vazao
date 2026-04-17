"""
Calculo automatico de C (Racional) e CN (SCS) a partir de LULC + solo GEE.

Fluxo:
    1. Recebe geometria da bacia (EPSG:4326).
    2. Baixa raster LULC (MapBiomas ou Dynamic World).
    3. Baixa raster SoilGrids textura (sand + clay, 0-5 cm).
    4. Recorta os dois pela bacia.
    5. Converte textura em grupo hidrologico NRCS (A/B/C/D) por pixel.
    6. Calcula histograma LULC ponderado por area.
    7. Aplica tabelas de lookup -> C_racional (LULC only) e CN_scs (LULC x GH).
    8. Devolve composicao + C e CN ponderados.

Tabelas de referencia:
    - C por uso do solo: ABRH/DAEE/Tucci (adaptado para classes MapBiomas).
    - CN por uso x GH: USDA NRCS TR-55 (1986), adaptado.
    - Classificacao GH por textura: Rawls & Brakensiek (1985) simplificada.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
import rasterio
from rasterio.mask import mask as rio_mask
from rasterio.warp import Resampling, reproject
from shapely.geometry import Polygon, mapping, shape

from chuva_vazao import gee_client


# ---------------------------------------------------------------------------
# Mapeamento MapBiomas Col 9 -> categoria hidrologica (categoria padrao do app)
# ---------------------------------------------------------------------------
# Fonte das classes: https://mapbiomas.org/codigos-de-legenda
# Cada classe mapeada numa das categorias hidrologicas abaixo, que sao as
# chaves das tabelas de C e CN.

MAPBIOMAS_TO_CATEGORIA: dict[int, str] = {
    # Floresta / vegetacao nativa densa
    1: "floresta", 3: "floresta", 4: "savana", 5: "mangue", 6: "floresta_alagada",
    49: "vegetacao_arborea_restinga",
    # Silvicultura
    9: "silvicultura",
    # Campo / pastagem
    10: "campo", 11: "campo_alagado", 12: "campo", 13: "campo",
    15: "pastagem", 50: "vegetacao_herbacea_restinga",
    # Agricultura
    14: "agricultura", 18: "agricultura", 19: "agricultura",
    20: "agricultura", 39: "agricultura", 40: "agricultura",
    41: "agricultura", 46: "agricultura", 47: "agricultura",
    48: "agricultura", 62: "agricultura",
    # Mosaico de usos
    21: "mosaico_agropecuario",
    # Nao vegetada
    22: "solo_exposto", 23: "praia_duna", 25: "nao_vegetada_outra",
    29: "afloramento_rochoso", 30: "mineracao", 32: "salina",
    # Urbano
    24: "urbano",
    # Agua
    26: "agua", 31: "aquicultura", 33: "agua",
}

CATEGORIAS_PADRAO = sorted(set(MAPBIOMAS_TO_CATEGORIA.values()))


# ---------------------------------------------------------------------------
# Mapeamento Dynamic World -> mesma categoria
# ---------------------------------------------------------------------------

DW_TO_CATEGORIA: dict[int, str] = {
    0: "agua",
    1: "floresta",
    2: "campo",
    3: "campo_alagado",
    4: "agricultura",
    5: "campo",  # shrub_and_scrub -> campo/savana
    6: "urbano",
    7: "solo_exposto",
    8: "nao_vegetada_outra",  # snow_and_ice (irrelevante no Brasil)
}


# ---------------------------------------------------------------------------
# Tabela C (coeficiente de escoamento, Racional)
# ---------------------------------------------------------------------------
# Valores tipicos para TR ~5-10 anos. ABRH/Tucci "Drenagem Urbana" e DAEE-SP.

C_POR_CATEGORIA: dict[str, float] = {
    "floresta": 0.15,
    "floresta_alagada": 0.10,
    "mangue": 0.10,
    "savana": 0.20,
    "silvicultura": 0.20,
    "campo": 0.25,
    "campo_alagado": 0.30,
    "pastagem": 0.30,
    "agricultura": 0.40,
    "mosaico_agropecuario": 0.35,
    "vegetacao_arborea_restinga": 0.18,
    "vegetacao_herbacea_restinga": 0.25,
    "praia_duna": 0.15,
    "solo_exposto": 0.55,
    "afloramento_rochoso": 0.80,
    "mineracao": 0.75,
    "salina": 0.90,
    "nao_vegetada_outra": 0.55,
    "urbano": 0.75,
    "agua": 1.00,
    "aquicultura": 1.00,
}


# ---------------------------------------------------------------------------
# Tabela CN (SCS) -> NRCS TR-55 adaptado
# ---------------------------------------------------------------------------
# CN por (categoria, grupo_hidrologico). CN=100 em superficies de agua.

CN_POR_CATEGORIA_E_GH: dict[str, dict[str, float]] = {
    # Floresta (boa cobertura, TR-55 woods/forest "Good")
    "floresta":                 {"A": 30, "B": 55, "C": 70, "D": 77},
    "floresta_alagada":         {"A": 98, "B": 98, "C": 98, "D": 98},
    "mangue":                   {"A": 98, "B": 98, "C": 98, "D": 98},
    "savana":                   {"A": 39, "B": 61, "C": 74, "D": 80},
    "silvicultura":             {"A": 36, "B": 60, "C": 73, "D": 79},
    # Campo / pastagem
    "campo":                    {"A": 49, "B": 69, "C": 79, "D": 84},
    "campo_alagado":            {"A": 98, "B": 98, "C": 98, "D": 98},
    "pastagem":                 {"A": 49, "B": 69, "C": 79, "D": 84},
    # Agricultura
    "agricultura":              {"A": 67, "B": 78, "C": 85, "D": 89},
    "mosaico_agropecuario":     {"A": 61, "B": 74, "C": 82, "D": 86},
    # Restinga
    "vegetacao_arborea_restinga":  {"A": 35, "B": 58, "C": 72, "D": 79},
    "vegetacao_herbacea_restinga": {"A": 49, "B": 69, "C": 79, "D": 84},
    # Nao vegetada
    "praia_duna":               {"A": 63, "B": 77, "C": 85, "D": 88},
    "solo_exposto":             {"A": 77, "B": 86, "C": 91, "D": 94},
    "afloramento_rochoso":      {"A": 98, "B": 98, "C": 98, "D": 98},
    "mineracao":                {"A": 77, "B": 86, "C": 91, "D": 94},
    "salina":                   {"A": 98, "B": 98, "C": 98, "D": 98},
    "nao_vegetada_outra":       {"A": 77, "B": 86, "C": 91, "D": 94},
    # Urbano (residencial medio, ~38% impermeavel -> TR-55 Table 2-2a)
    "urbano":                   {"A": 61, "B": 75, "C": 83, "D": 87},
    # Agua
    "agua":                     {"A": 100, "B": 100, "C": 100, "D": 100},
    "aquicultura":              {"A": 100, "B": 100, "C": 100, "D": 100},
}


# ---------------------------------------------------------------------------
# Classificacao grupo hidrologico a partir de textura (sand, clay)
# ---------------------------------------------------------------------------
# SoilGrids devolve valores em g/kg (i.e. multiplicar por 0.1 -> %).
# Regras simplificadas baseadas em Rawls & Brakensiek e adaptadas por Nachtergaele
# para grupo hidrologico NRCS:
#     A: texturas muito arenosas, alta infiltracao
#     B: arenoso-argiloso, media infiltracao
#     C: argila media
#     D: argila pesada, baixa infiltracao

def classify_hydrological_group(sand_pct: np.ndarray, clay_pct: np.ndarray) -> np.ndarray:
    """
    Retorna array de strings com GH (`'A'`, `'B'`, `'C'` ou `'D'`).

    Parameters
    ----------
    sand_pct, clay_pct : arrays % (0-100).
    """
    gh = np.full(sand_pct.shape, "C", dtype="<U1")
    # D: argila muito pesada ou areia muito baixa
    mask_d = (clay_pct >= 40) | (sand_pct < 20)
    gh[mask_d] = "D"
    # C: argila 20-40, areia 20-50 (nao D, nao B)
    mask_c = (~mask_d) & ((clay_pct >= 20) | (sand_pct < 50))
    gh[mask_c] = "C"
    # B: areno-argiloso (clay 10-20, sand 50-70) ou sand 50-70 com clay<20
    mask_b = (~mask_d) & (~mask_c) & ((sand_pct >= 50) & (clay_pct < 20))
    gh[mask_b] = "B"
    # A: muito arenoso, pouca argila
    mask_a = (sand_pct >= 70) & (clay_pct < 10)
    gh[mask_a] = "A"
    return gh


# ---------------------------------------------------------------------------
# Dataclasses de saida
# ---------------------------------------------------------------------------

@dataclass
class LanduseResult:
    """Resultado do calculo de C e CN ponderados."""
    C_racional: float
    CN_scs: float
    gh_dominante: str  # 'A'|'B'|'C'|'D'
    area_km2: float
    fonte_lulc: str    # 'mapbiomas_c9' ou 'dynamic_world'
    fonte_solo: str    # 'soilgrids_v2'
    composicao_lulc: pd.DataFrame = field(default_factory=pd.DataFrame)
    composicao_gh: pd.DataFrame = field(default_factory=pd.DataFrame)

    def resumo_texto(self) -> str:
        lines = [
            f"Area analisada: {self.area_km2:.3f} km2",
            f"C (Racional) = {self.C_racional:.3f}",
            f"CN (SCS)     = {self.CN_scs:.1f}",
            f"GH dominante = {self.gh_dominante}",
            f"Fonte LULC   = {self.fonte_lulc}",
            f"Fonte solo   = {self.fonte_solo}",
        ]
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Leitura de raster mascarado pela bacia
# ---------------------------------------------------------------------------

def _read_raster_masked(raster_path: Path, geom: Polygon) -> tuple[np.ndarray, float]:
    """
    Le raster recortado pela geometria.

    Returns
    -------
    (values, pixel_area_m2) : valores validos (nodata removido) e area de 1 pixel
    projetada aproximadamente em m2 (via pyproj, para uso em estatisticas por
    fracao de area na bacia).
    """
    with rasterio.open(raster_path) as src:
        geom_mapped = [mapping(geom)]
        masked, transform = rio_mask(src, geom_mapped, crop=True, nodata=src.nodata)
        arr = masked[0]
        nodata = src.nodata
        if nodata is not None:
            valid = arr[arr != nodata]
        else:
            valid = arr[~np.isnan(arr)]
        # Area aproximada de um pixel em m2 — o raster esta em graus (4326), entao
        # aproximacao: cos(lat) * 111320^2 * dx_deg * dy_deg.
        lat_c = (src.bounds.top + src.bounds.bottom) / 2
        px_w_deg = transform.a
        px_h_deg = abs(transform.e)
        m_per_deg_lat = 111_320.0
        m_per_deg_lon = 111_320.0 * float(np.cos(np.deg2rad(lat_c)))
        pixel_area_m2 = px_w_deg * m_per_deg_lon * px_h_deg * m_per_deg_lat
        return valid, pixel_area_m2


def _read_raster_aligned_to(
    raster_path: Path,
    reference_path: Path,
    resampling: Resampling = Resampling.nearest,
) -> np.ndarray:
    """
    Le raster reamostrado para o grid do `reference_path`.

    Usado para alinhar SoilGrids (250 m) com LULC (30 m ou 10 m) antes de
    fazer overlay.
    """
    with rasterio.open(reference_path) as ref:
        ref_transform = ref.transform
        ref_crs = ref.crs
        ref_shape = (ref.height, ref.width)

    with rasterio.open(raster_path) as src:
        dst = np.zeros(ref_shape, dtype=np.float32)
        reproject(
            source=rasterio.band(src, 1),
            destination=dst,
            src_transform=src.transform,
            src_crs=src.crs,
            dst_transform=ref_transform,
            dst_crs=ref_crs,
            resampling=resampling,
        )
        return dst


# ---------------------------------------------------------------------------
# Calculo C/CN ponderado
# ---------------------------------------------------------------------------

def compute_c_and_cn(
    geom: Polygon,
    fonte_lulc: str = "mapbiomas",
    ano_lulc: int = 2023,
) -> LanduseResult:
    """
    Calcula C_racional e CN_scs automaticamente para a bacia.

    Parameters
    ----------
    geom : shapely Polygon em EPSG:4326 (bacia delineada).
    fonte_lulc : 'mapbiomas' (30 m, Brasil) ou 'dynamic_world' (10 m, global).
    ano_lulc : ano do produto LULC.
    """
    # 1. Baixa LULC
    if fonte_lulc == "mapbiomas":
        lulc_path = gee_client.fetch_mapbiomas(geom, ano=ano_lulc)
        lookup = MAPBIOMAS_TO_CATEGORIA
        fonte_label = "mapbiomas_c9"
    elif fonte_lulc == "dynamic_world":
        lulc_path = gee_client.fetch_dynamic_world(geom, ano=ano_lulc)
        lookup = DW_TO_CATEGORIA
        fonte_label = "dynamic_world"
    else:
        raise ValueError(f"fonte_lulc invalida: {fonte_lulc}")

    # 2. Baixa SoilGrids textura
    soil = gee_client.fetch_soilgrids_texture(geom)

    # 3. Crop LULC pela bacia (ja retorna grid apenas dos pixels dentro)
    geom_mapped = [mapping(geom)]
    with rasterio.open(lulc_path) as src_lulc:
        lulc_crop, lulc_transform = rio_mask(
            src_lulc, geom_mapped, crop=True, filled=False,
        )
        lulc_nodata = src_lulc.nodata
        lulc_crs = src_lulc.crs
        # Area aproximada de 1 pixel em m2 (raster em graus WGS84)
        lat_c = (src_lulc.bounds.top + src_lulc.bounds.bottom) / 2
        px_w_deg = lulc_transform.a
        px_h_deg = abs(lulc_transform.e)
        m_per_deg_lat = 111_320.0
        m_per_deg_lon = 111_320.0 * float(np.cos(np.deg2rad(lat_c)))
        pixel_area_m2 = px_w_deg * m_per_deg_lon * px_h_deg * m_per_deg_lat

    lulc_arr = lulc_crop[0]
    # Mascara valida = dentro da bacia (filled=False gera MaskedArray)
    if np.ma.is_masked(lulc_arr):
        mask_inside = ~np.ma.getmaskarray(lulc_arr)
    else:
        mask_inside = np.ones(lulc_arr.shape, dtype=bool)
    if lulc_nodata is not None:
        mask_inside = mask_inside & (np.asarray(lulc_arr) != lulc_nodata)

    if not mask_inside.any():
        raise RuntimeError("Raster LULC vazio dentro da bacia (bacia pode ser menor que 1 pixel).")

    # 4. Reamostra sand/clay pro MESMO grid do LULC cropado
    target_shape = lulc_arr.shape
    sand_aligned = np.zeros(target_shape, dtype=np.float32)
    clay_aligned = np.zeros(target_shape, dtype=np.float32)
    with rasterio.open(soil.sand_tif) as src_sand:
        reproject(
            source=rasterio.band(src_sand, 1),
            destination=sand_aligned,
            src_transform=src_sand.transform,
            src_crs=src_sand.crs,
            dst_transform=lulc_transform,
            dst_crs=lulc_crs,
            resampling=Resampling.nearest,
        )
    with rasterio.open(soil.clay_tif) as src_clay:
        reproject(
            source=rasterio.band(src_clay, 1),
            destination=clay_aligned,
            src_transform=src_clay.transform,
            src_crs=src_clay.crs,
            dst_transform=lulc_transform,
            dst_crs=lulc_crs,
            resampling=Resampling.nearest,
        )
    # SoilGrids vem em g/kg -> % = g/kg / 10
    sand_pct = sand_aligned / 10.0
    clay_pct = clay_aligned / 10.0

    # 5. Vetoriza pixels validos nos tres rasters alinhados
    sand_valid = sand_pct[mask_inside]
    clay_valid = clay_pct[mask_inside]
    lulc_valid = np.asarray(lulc_arr)[mask_inside]

    gh_valid = classify_hydrological_group(sand_valid, clay_valid)

    # 5. Converte lulc em categoria
    cat_valid = np.array(
        [lookup.get(int(v), "nao_vegetada_outra") for v in lulc_valid],
        dtype=object,
    )

    # 6. Histograma por categoria
    df = pd.DataFrame({
        "categoria": cat_valid,
        "gh": gh_valid,
        "lulc_code": lulc_valid,
    })
    total_n = len(df)
    total_area_km2 = total_n * pixel_area_m2 / 1e6

    comp_lulc = (
        df.groupby("categoria")
        .size()
        .rename("n_pixels")
        .to_frame()
        .assign(
            frac=lambda d: d["n_pixels"] / total_n,
            area_km2=lambda d: d["n_pixels"] * pixel_area_m2 / 1e6,
            C=lambda d: d.index.map(lambda c: C_POR_CATEGORIA.get(c, 0.4)),
        )
        .sort_values("frac", ascending=False)
    )

    comp_gh = (
        df.groupby("gh")
        .size()
        .rename("n_pixels")
        .to_frame()
        .assign(frac=lambda d: d["n_pixels"] / total_n)
        .sort_values("frac", ascending=False)
    )

    # 7. C ponderado (so LULC)
    C_weighted = float((comp_lulc["C"] * comp_lulc["frac"]).sum())

    # 8. CN ponderado (por pixel: cruzamento categoria x GH)
    def _cn_pixel(cat: str, gh: str) -> float:
        row = CN_POR_CATEGORIA_E_GH.get(cat)
        if row is None:
            return 75.0  # fallback conservador
        return float(row.get(gh, row["C"]))

    cn_vec = np.array([_cn_pixel(c, g) for c, g in zip(cat_valid, gh_valid)])
    CN_weighted = float(cn_vec.mean())

    gh_dominante = str(comp_gh.index[0]) if len(comp_gh) else "C"

    return LanduseResult(
        C_racional=C_weighted,
        CN_scs=CN_weighted,
        gh_dominante=gh_dominante,
        area_km2=total_area_km2,
        fonte_lulc=fonte_label,
        fonte_solo="soilgrids_v2_0-5cm",
        composicao_lulc=comp_lulc,
        composicao_gh=comp_gh,
    )

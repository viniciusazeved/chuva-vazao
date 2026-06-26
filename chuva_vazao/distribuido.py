"""
Modelo chuva-vazao SEMI-DISTRIBUIDO por sub-bacias (ottobacias).

Fase 1 (este arquivo, por enquanto): discretizacao da bacia em sub-bacias +
topologia de roteamento (quem deflui em quem). As fases seguintes (parametros
por sub-bacia, hietograma+ARF, SCS-HU local, roteamento Muskingum-Cunge) entram
depois, reaproveitando este esqueleto.

Discretizacao:
    1. Reaproveita o pipeline de `basin.delineate_basin` (breach, D8 pointer,
       D8 accumulation, extract streams, watershed) — o `work_dir` resultante ja
       tem d8_ptr.tif, d8_acc.tif, streams.tif e basin.tif.
    2. `subbasins` (WhiteboxTools): uma sub-bacia por trecho de canal entre
       confluencias. O nº de sub-bacias e controlado pelo threshold de canal.
    3. Recorta as sub-bacias pela bacia (mascara raster, evita o winding-order
       ruim do RasterToVectorPolygons) e poligonaliza com rasterio.features.

Topologia (grafo de roteamento):
    Para cada sub-bacia, segue a direcao de fluxo D8 do seu pixel de saida (o de
    maior acumulacao que deflui para OUTRA sub-bacia) -> a sub-bacia de destino e
    a de jusante. A sub-bacia cujo fluxo sai da bacia e o exutorio (downstream =
    None). Ordenacao topologica (Kahn) da a ordem de processamento montante ->
    jusante para o roteamento.

Convencao D8 do WhiteboxTools D8Pointer (confirmada empiricamente, horaria a
partir de NE): 1=NE, 2=E, 4=SE, 8=S, 16=SW, 32=W, 64=NW, 128=N.
"""
from __future__ import annotations

import importlib.util
import os
from collections import deque
from dataclasses import dataclass, field
from math import exp
from pathlib import Path

# PROJ_DATA/GDAL_DATA do bundle do rasterio (mesmo workaround dos outros modulos).
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
import pandas as pd
import rasterio
from rasterio.features import shapes as rio_shapes
from shapely.geometry import shape as shp_shape
from shapely.ops import unary_union

from chuva_vazao import basin


# Offsets (drow, dcol) do D8Pointer do WhiteboxTools. Convencao CONFIRMADA
# empiricamente (rampa p/ E->2, S->8, W->32, N->128): horaria a partir de NE.
# drow > 0 = Sul (a linha cresce para baixo).
_D8_OFFSETS: dict[int, tuple[int, int]] = {
    1: (-1, 1),    # NE
    2: (0, 1),     # E
    4: (1, 1),     # SE
    8: (1, 0),     # S
    16: (1, -1),   # SW
    32: (0, -1),   # W
    64: (-1, -1),  # NW
    128: (-1, 0),  # N
}


# ---------------------------------------------------------------------------
# Estruturas
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SubBacia:
    """Uma sub-bacia da discretizacao."""
    id: int                       # ID (= ID do stream link / sub-bacia no WBT)
    geometry_4326: "object"       # shapely Polygon/MultiPolygon em EPSG:4326
    geometry_utm: "object"        # shapely no UTM de trabalho (p/ area/comprimento)
    area_km2: float
    downstream_id: int | None     # sub-bacia de jusante (None = exutorio)


@dataclass
class DiscretizacaoResult:
    """Sub-bacias + topologia de roteamento."""
    subbacias: list[SubBacia]
    ordem_roteamento: list[int]   # IDs montante -> jusante (ordem de processamento)
    exutorio_id: int              # sub-bacia do exutorio (downstream None)
    crs_utm: str
    n_subbacias: int
    area_total_km2: float
    avisos: list[str] = field(default_factory=list)
    work_dir: Path | None = None  # work_dir do delineamento (rasters intermediarios)

    def grafo(self) -> dict[int, int | None]:
        return {s.id: s.downstream_id for s in self.subbacias}

    def to_geodataframe(self) -> gpd.GeoDataFrame:
        """GeoDataFrame (EPSG:4326) com id, area_km2, downstream_id."""
        return gpd.GeoDataFrame(
            {
                "id": [s.id for s in self.subbacias],
                "area_km2": [s.area_km2 for s in self.subbacias],
                "downstream_id": [s.downstream_id for s in self.subbacias],
            },
            geometry=[s.geometry_4326 for s in self.subbacias],
            crs="EPSG:4326",
        )


# ---------------------------------------------------------------------------
# Topologia
# ---------------------------------------------------------------------------

def _construir_topologia(
    sub_arr: np.ndarray,
    d8_arr: np.ndarray,
    acc_arr: np.ndarray,
    ids_validos: set[int],
    in_basin: np.ndarray,
) -> dict[int, int | None]:
    """
    Grafo sub-bacia -> sub-bacia de jusante a partir do D8, restrito a DENTRO da
    bacia.

    Para cada sub-bacia, o pixel de SAIDA e o de maior acumulacao (entre os
    pixels DA BACIA) cujo D8 aponta para outra sub-bacia ou para fora da bacia.
    Se o destino e outra sub-bacia valida -> downstream; se o D8 sai da bacia
    (so o exutorio faz isso) -> None. Restringir a bacia evita que sub-bacias de
    borda "vazem" para o rio externo de acumulacao alta.
    """
    nrow, ncol = sub_arr.shape
    rows, cols = np.indices(sub_arr.shape)

    drow = np.zeros(sub_arr.shape, dtype=np.int32)
    dcol = np.zeros(sub_arr.shape, dtype=np.int32)
    tem_dir = np.zeros(sub_arr.shape, dtype=bool)
    for code, (orow, ocol) in _D8_OFFSETS.items():
        m = d8_arr == code
        drow[m] = orow
        dcol[m] = ocol
        tem_dir |= m

    rr = rows + drow
    cc = cols + dcol
    dentro_raster = (rr >= 0) & (rr < nrow) & (cc >= 0) & (cc < ncol) & tem_dir

    # Destino so "conta" como sub-bacia se o pixel-destino estiver DENTRO da bacia.
    dest_in_basin = np.zeros(sub_arr.shape, dtype=bool)
    dest_in_basin[dentro_raster] = in_basin[rr[dentro_raster], cc[dentro_raster]]
    s_dest = np.full(sub_arr.shape, -1, dtype=np.int64)
    ok = dentro_raster & dest_in_basin
    s_dest[ok] = sub_arr[rr[ok], cc[ok]]

    origem_ok = in_basin & np.isin(sub_arr, list(ids_validos))
    # Transicao: pixel da bacia/sub valida cujo destino e outra sub (ou fora=-1).
    trans = origem_ok & (sub_arr.astype(np.int64) != s_dest)
    if not trans.any():
        return {int(s): None for s in ids_validos}

    df = pd.DataFrame({
        "origem": sub_arr[trans].astype(np.int64),
        "destino": s_dest[trans],
        "acc": acc_arr[trans],
    })
    # Para cada sub-bacia origem, o destino do pixel de MAIOR acumulacao = saida.
    idx = df.groupby("origem")["acc"].idxmax()
    saida = df.loc[idx]

    downstream: dict[int, int | None] = {}
    for _, row in saida.iterrows():
        o = int(row["origem"])
        d = int(row["destino"])
        downstream[o] = d if d in ids_validos else None  # d<=0 ou externo -> None
    for s in ids_validos:
        downstream.setdefault(s, None)
    return downstream


def _ordem_topologica(downstream: dict[int, int | None]) -> list[int]:
    """Ordem montante -> jusante (Kahn). Quebra ciclos eventuais sem travar."""
    subs = set(downstream.keys())
    n_montante = {s: 0 for s in subs}
    for s, d in downstream.items():
        if d is not None and d in n_montante:
            n_montante[d] += 1
    fila = deque(sorted(s for s in subs if n_montante[s] == 0))
    ordem: list[int] = []
    while fila:
        s = fila.popleft()
        ordem.append(s)
        d = downstream.get(s)
        if d is not None and d in n_montante:
            n_montante[d] -= 1
            if n_montante[d] == 0:
                fila.append(d)
    # Sobrou alguem (ciclo)? Anexa pra nao perder sub-bacia.
    for s in subs:
        if s not in ordem:
            ordem.append(s)
    return ordem


# ---------------------------------------------------------------------------
# Discretizacao
# ---------------------------------------------------------------------------

def discretizar_bacia(
    lat: float,
    lon: float,
    dem_path: Path,
    *,
    stream_threshold: int = 12000,
    snap_dist_m: float = 500.0,
    snap_method: str = "accumulation",
    area_min_km2: float = 0.25,
) -> DiscretizacaoResult:
    """
    Discretiza a bacia do exutorio em sub-bacias + topologia de roteamento.

    Parameters
    ----------
    lat, lon : exutorio (EPSG:4326).
    dem_path : DEM (qualquer CRS; reprojetado para UTM no pipeline).
    stream_threshold : células de acumulacao p/ canal. Controla a GRANULARIDADE
                       da discretizacao (maior = menos sub-bacias).
    snap_dist_m, snap_method : repassados ao delineamento (default accumulation).
    area_min_km2 : descarta sub-bacias menores (slivers da vetorizacao).
    """
    # 1. Pipeline de delineamento (gera o work_dir com d8/streams/basin).
    br = basin.delineate_basin(
        lat, lon, Path(dem_path), snap_dist_m=snap_dist_m,
        stream_threshold=stream_threshold, snap_method=snap_method,
    )
    wd = br.work_dir
    crs_utm = f"EPSG:{basin.utm_epsg_for(lat, lon)}"
    avisos: list[str] = []

    # 2. subbasins (WhiteboxTools) no work_dir existente.
    wbt = basin._new_whitebox_tools()
    wbt.set_verbose_mode(False)
    wbt.set_working_dir(str(wd))
    wbt.subbasins(d8_pntr="d8_ptr.tif", streams="streams.tif", output="subbasins.tif")

    # 3. Le os rasters alinhados (mesma grade do work_dir).
    with rasterio.open(wd / "subbasins.tif") as s:
        sub_arr = s.read(1)
        transform = s.transform
        sub_nodata = s.nodata
    with rasterio.open(wd / "d8_ptr.tif") as d:
        d8_arr = d.read(1)
    with rasterio.open(wd / "d8_acc.tif") as a:
        acc_arr = a.read(1)
    with rasterio.open(wd / "basin.tif") as b:
        bas_arr = b.read(1)
        bas_nodata = b.nodata

    in_basin = bas_arr > 0
    if bas_nodata is not None:
        in_basin &= bas_arr != bas_nodata

    # 4. IDs de sub-bacia majoritariamente dentro da bacia (>50% dos pixels).
    flat = pd.DataFrame({"sub": sub_arr.ravel(), "inb": in_basin.ravel()})
    flat = flat[flat["sub"] > 0]
    if sub_nodata is not None:
        flat = flat[flat["sub"] != sub_nodata]
    frac = flat.groupby("sub")["inb"].mean()
    ids_validos = set(int(i) for i in frac[frac > 0.5].index)
    if len(ids_validos) < 1:
        raise ValueError("Nenhuma sub-bacia gerada dentro da bacia — confira o threshold.")

    # 5. Poligonaliza as sub-bacias RECORTADAS pela bacia (mascara raster).
    sub_masked = np.where(in_basin, sub_arr, 0).astype(np.int32)
    geom_por_id: dict[int, list] = {}
    for geom, val in rio_shapes(sub_masked, mask=sub_masked > 0, transform=transform):
        v = int(val)
        if v in ids_validos:
            geom_por_id.setdefault(v, []).append(shp_shape(geom))

    # 6. Topologia restrita a DENTRO da bacia (evita vazamento de borda).
    downstream = _construir_topologia(sub_arr, d8_arr, acc_arr, ids_validos, in_basin)

    # 7. Monta as sub-bacias (une fragmentos, filtra area minima, reprojeta).
    from rasterio.warp import transform_geom  # noqa: PLC0415

    px_area = abs(transform.a * transform.e)
    subbacias: list[SubBacia] = []
    for sid, geoms in geom_por_id.items():
        poly_utm = unary_union(geoms).buffer(0)
        area_km2 = poly_utm.area / 1e6
        if area_km2 < area_min_km2:
            continue
        geom_4326 = shp_shape(transform_geom(crs_utm, "EPSG:4326", poly_utm.__geo_interface__))
        subbacias.append(SubBacia(
            id=sid, geometry_4326=geom_4326, geometry_utm=poly_utm,
            area_km2=area_km2, downstream_id=downstream.get(sid),
        ))

    if len(subbacias) < 1:
        raise ValueError("Todas as sub-bacias ficaram abaixo da area minima.")

    # Reconcilia downstream com as sub-bacias que sobreviveram ao filtro de area.
    ids_finais = {s.id for s in subbacias}
    sub_objs = []
    for s in subbacias:
        d = s.downstream_id
        # se o downstream foi filtrado por area, sobe pra None (vira exutorio local)
        d_ok = d if (d is not None and d in ids_finais) else None
        sub_objs.append(SubBacia(
            id=s.id, geometry_4326=s.geometry_4326, geometry_utm=s.geometry_utm,
            area_km2=s.area_km2, downstream_id=d_ok,
        ))
    subbacias = sub_objs

    downstream_final = {s.id: s.downstream_id for s in subbacias}
    ordem = _ordem_topologica(downstream_final)

    exutorios = [s.id for s in subbacias if s.downstream_id is None]
    if len(exutorios) > 1:
        avisos.append(
            f"{len(exutorios)} sub-bacias sem jusante (esperado 1 exutorio) — "
            "pode haver fragmento desconectado ou ruido na direcao de fluxo."
        )
    exutorio_id = ordem[-1] if ordem else subbacias[0].id

    area_total = sum(s.area_km2 for s in subbacias)
    return DiscretizacaoResult(
        subbacias=subbacias, ordem_roteamento=ordem, exutorio_id=exutorio_id,
        crs_utm=crs_utm, n_subbacias=len(subbacias), area_total_km2=area_total,
        avisos=avisos, work_dir=wd,
    )


# ---------------------------------------------------------------------------
# Fase 2 — parametros por sub-bacia (area, tc; CN entra logo a seguir)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ParametrosSubBacia:
    """Parametros hidrologicos de uma sub-bacia."""
    id: int
    area_km2: float
    L_km: float            # comprimento do caminho de fluxo (canal principal)
    delta_h_m: float       # desnivel (z_max - z_min) na sub-bacia
    declividade_pct: float # 100 * delta_h / L
    tc_min: float          # tempo de concentracao (media Kirpich/Chow/California)
    cn: float | None = None  # CN-SCS (None enquanto a Fase de CN nao roda)
    metodo_L: str = "canal"  # 'canal' (streams na sub-bacia) ou 'hack' (fallback)


def _z_min_max(dem_path: Path, geom_utm) -> tuple[float, float] | None:
    """(z_min, z_max) do DEM dentro da geometria (UTM). None se vazio."""
    from rasterio.mask import mask as rio_mask  # noqa: PLC0415

    with rasterio.open(dem_path) as src:
        arr, _ = rio_mask(src, [geom_utm.__geo_interface__], crop=True, filled=False)
        band = arr[0]
        nod = src.nodata
        vals = np.asarray(band, dtype="float64")
        valido = ~np.ma.getmaskarray(band) if np.ma.is_masked(band) else np.ones(band.shape, bool)
        if nod is not None:
            valido &= vals != nod
        valido &= np.isfinite(vals)
        if not valido.any():
            return None
        v = vals[valido]
        # Percentis 1-99 no lugar de min/max: robusto a pixels espurios (nodata=0
        # nao mascarado na reprojecao, picos isolados) que inflariam o desnivel.
        return float(np.percentile(v, 1)), float(np.percentile(v, 99))


# Campo padrao de CN no GeoPackage de ottobacias da ANA (BHAE_CN2022):
# CN2 = condicao de umidade antecedente II (projeto); ANA = metodologia que usa
# as classes hidrologicas de Sartori et al. (2005). Vinicius escolheu a ANA.
CAMPO_CN_PADRAO = "CN2.ANA_med"


def _cn_por_interseccao(
    disc: DiscretizacaoResult, bhae_path: Path, campo: str,
) -> dict[int, float | None]:
    """
    CN por sub-bacia via interseccao area-ponderada com as ottobacias da ANA.

    Le o GeoPackage filtrado pelo bbox da bacia (rapido — ignora o resto do BR) e,
    para cada sub-bacia, pondera o CN das ottobacias que a cobrem pela area de
    interseccao. As ottobacias da ANA NAO precisam coincidir com a discretizacao;
    entram apenas como camada-fonte de CN.
    """
    gsub = disc.to_geodataframe()
    minx, miny, maxx, maxy = gsub.total_bounds
    bbox = (minx - 0.05, miny - 0.05, maxx + 0.05, maxy + 0.05)
    otto = gpd.read_file(bhae_path, bbox=bbox)
    if campo not in otto.columns:
        raise ValueError(f"Campo '{campo}' ausente em {Path(bhae_path).name}.")
    otto = otto[[campo, "geometry"]].to_crs(disc.crs_utm)
    otto = otto[otto[campo].notna()]

    cn: dict[int, float | None] = {}
    for s in disc.subbacias:
        geom = s.geometry_utm.buffer(0)
        sel = otto[otto.intersects(geom)]
        if len(sel) == 0:
            cn[s.id] = None
            continue
        area_int = sel.geometry.intersection(geom).area
        total = float(area_int.sum())
        if total <= 0:
            cn[s.id] = None
            continue
        cn[s.id] = float((sel[campo].to_numpy() * (area_int.to_numpy() / total)).sum())
    return cn


def calcular_cn_gee(
    disc: DiscretizacaoResult,
    *,
    fonte_lulc: str = "mapbiomas",
    ano_lulc: int = 2023,
) -> dict[int, float | None]:
    """
    CN-SCS por sub-bacia via GEE (MapBiomas/Dynamic World + SoilGrids), reusando
    `landuse.compute_c_and_cn` em cada polígono. Funciona no Cloud (≠ o BHAE de
    5,6 GB) e e consistente com o Racional/SCS concentrado, que ja usam MapBiomas.
    Uma chamada GEE por sub-bacia (mais lento, mas espacializado). Sub-bacia que
    falhar fica None (cai no cn_default em calcular_parametros).
    """
    from chuva_vazao import landuse  # noqa: PLC0415

    cn: dict[int, float | None] = {}
    for s in disc.subbacias:
        try:
            lu = landuse.compute_c_and_cn(
                s.geometry_4326, fonte_lulc=fonte_lulc, ano_lulc=ano_lulc,
            )
            cn[s.id] = float(lu.CN_scs)
        except Exception:  # noqa: BLE001
            cn[s.id] = None
    return cn


def calcular_parametros(
    disc: DiscretizacaoResult,
    *,
    bhae_path: Path | None = None,
    campo_cn: str = CAMPO_CN_PADRAO,
    cn_default: float = 70.0,
    cn_manual: float | None = None,
    cn_por_sub_externo: dict[int, float | None] | None = None,
    tc_min_floor: float = 10.0,
    L_canal_min_m: float = 100.0,
) -> dict[int, ParametrosSubBacia]:
    """
    Fase 2: area, L, delta_h, declividade, tc e CN por sub-bacia.

    L = comprimento dos canais (streams) dentro da sub-bacia ou Hack
    L[m]=1400*A_km2^0.6 (o maior, p/ nao explodir a declividade em cabeceira).
    ΔH = percentis 1-99 do DEM na sub-bacia (robusto a pixel espurio). tc = media
    de Kirpich/Chow/California. CN = interseccao area-ponderada com as ottobacias
    da ANA se `bhae_path` for dado; quando nenhuma fonte fornece CN (sem BHAE,
    sub-bacia sem cobertura ou GEE indisponivel) aplica-se `cn_default` (fonte
    plugavel — no Cloud entra o GEE).
    """
    from chuva_vazao.tempo_concentracao import tempo_concentracao_completo  # noqa: PLC0415

    if disc.work_dir is None:
        raise ValueError("DiscretizacaoResult sem work_dir — rode discretizar_bacia primeiro.")

    cn_por_sub: dict[int, float | None] = {}
    if cn_por_sub_externo is not None:  # CN do GEE pre-calculado por sub-bacia
        cn_por_sub = dict(cn_por_sub_externo)
    elif cn_manual is not None:  # calibracao: CN unico forcado em todas as sub-bacias
        cn_por_sub = {s.id: float(cn_manual) for s in disc.subbacias}
    elif bhae_path is not None:
        cn_por_sub = _cn_por_interseccao(disc, Path(bhae_path), campo_cn)
    wd = Path(disc.work_dir)
    dem_utm = wd / "dem_utm.tif"

    # Vetoriza a rede de canais para medir L por sub-bacia.
    wbt = basin._new_whitebox_tools()
    wbt.set_verbose_mode(False)
    wbt.set_working_dir(str(wd))
    wbt.raster_streams_to_vector(streams="streams.tif", d8_pntr="d8_ptr.tif", output="streams_vec.shp")
    try:
        streams = gpd.read_file(wd / "streams_vec.shp")
        if streams.crs is None:
            streams.set_crs(disc.crs_utm, inplace=True)
        streams = streams.to_crs(disc.crs_utm)
        # NAO usar buffer(0) aqui: streams sao LineStrings e buffer(0) de linha
        # retorna geometria vazia (linha tem area zero). So poligonos precisam.
    except Exception:
        streams = None

    params: dict[int, ParametrosSubBacia] = {}
    for s in disc.subbacias:
        geom = s.geometry_utm.buffer(0)
        zmm = _z_min_max(dem_utm, geom)
        delta_h = max((zmm[1] - zmm[0]) if zmm else 1.0, 1.0)

        L_canal_m = 0.0
        if streams is not None:
            try:
                sel = streams[streams.intersects(geom)]
                L_canal_m = float(sel.geometry.intersection(geom).length.sum())
            except Exception:
                L_canal_m = 0.0
        # Hack L[m] = 1400 * A_km2^0.6 estima o caminho hidraulico total (overland
        # + canal). Usa o MAIOR entre o canal medido e o Hack: em cabeceiras o
        # canal mapeado e curto demais e a declividade explodiria — o Hack corrige.
        L_hack_m = 1400.0 * (s.area_km2 ** 0.6)
        if L_canal_m >= max(L_hack_m, L_canal_min_m):
            L_m, metodo = L_canal_m, "canal"
        else:
            L_m, metodo = L_hack_m, "hack"
        L_km = L_m / 1000.0

        tc = max(tempo_concentracao_completo(L_km, delta_h).media_min, tc_min_floor)
        decl_pct = 100.0 * delta_h / L_m if L_m > 0 else 0.0
        cn_val = cn_por_sub.get(s.id)
        if cn_val is None:
            # nenhuma fonte deu CN (sem BHAE, ou sub-bacia sem cobertura da ANA,
            # ou GEE falhou nessa sub-bacia) -> aplica o default
            cn_val = cn_default
        params[s.id] = ParametrosSubBacia(
            id=s.id, area_km2=s.area_km2, L_km=L_km, delta_h_m=delta_h,
            declividade_pct=decl_pct, tc_min=tc, metodo_L=metodo, cn=cn_val,
        )
    return params


# ---------------------------------------------------------------------------
# Fase 3 — chuva de projeto (hietograma da IDF + ARF de Leclerc-Schaake)
# ---------------------------------------------------------------------------

def arf_leclerc_schaake(area_km2: float, duracao_h: float) -> float:
    """
    Fator de reducao de area / abatimento espacial (Leclerc & Schaake, 1972 —
    MIT Report 142):

        ARF = 1 - exp(-1.1 D^0.25) + exp(-1.1 D^0.25 - 0.01 A)

    D = duracao (h), A = area (km2). Converte a chuva PONTUAL da IDF (de um posto)
    na chuva MEDIA sobre a area da bacia — a celula de tempestade nao cobre toda a
    bacia com a mesma intensidade. Resultado limitado a [0, 1].
    """
    if area_km2 <= 0 or duracao_h <= 0:
        return 1.0
    d025 = duracao_h ** 0.25
    arf = 1.0 - exp(-1.1 * d025) + exp(-1.1 * d025 - 0.01 * area_km2)
    return float(min(max(arf, 0.0), 1.0))


@dataclass(frozen=True)
class ChuvaProjeto:
    """Hietograma de projeto areal — o MESMO em todas as sub-bacias (uniforme)."""
    hietograma: "object"        # pd.Series tempo_min -> altura_mm (ja reduzida)
    arf: float
    altura_pontual_mm: float     # altura total da IDF (antes do ARF)
    altura_areal_mm: float       # altura total apos ARF
    duracao_min: float
    TR_anos: float
    metodo: str


def chuva_projeto_distribuida(
    area_total_km2: float,
    idf_params: "object",
    TR: float,
    duracao_total_min: float,
    dt_min: float,
    *,
    metodo: str = "blocos",
    quartil: int = 2,
    arf_manual: float | None = None,
) -> ChuvaProjeto:
    """
    Hietograma de projeto da IDF reduzido pelo ARF — o mesmo em todas as
    sub-bacias (chuva uniforme). No MVP o ganho do distribuido vem do roteamento
    (tempo de viagem) + ARF; variar a chuva no espaco (satelite) fica para a v2.

    metodo : 'blocos' (alternados/Chicago) ou 'huff'. arf_manual sobrepoe a
    formula de Leclerc-Schaake quando o usuario quer um abatimento proprio.
    """
    from chuva_vazao import hietograma as hmod  # noqa: PLC0415

    if metodo == "huff":
        h_pontual = hmod.huff(idf_params, TR, duracao_total_min, dt_min, quartil)
    else:
        h_pontual = hmod.blocos_alternados(idf_params, TR, duracao_total_min, dt_min)

    arf = (
        float(arf_manual) if arf_manual is not None
        else arf_leclerc_schaake(area_total_km2, duracao_total_min / 60.0)
    )
    h_areal = h_pontual * arf
    return ChuvaProjeto(
        hietograma=h_areal, arf=arf,
        altura_pontual_mm=float(h_pontual.sum()),
        altura_areal_mm=float(h_areal.sum()),
        duracao_min=duracao_total_min, TR_anos=TR, metodo=metodo,
    )


# ---------------------------------------------------------------------------
# Fase 4 — hidrograma SCS (CN + UH triangular) por sub-bacia
# ---------------------------------------------------------------------------

def hidrogramas_locais(
    params_por_sub: dict[int, ParametrosSubBacia],
    chuva: ChuvaProjeto,
    *,
    prf: float = 484.0,
) -> dict[int, "object"]:
    """
    Hidrograma de cada sub-bacia a partir do hietograma AREAL uniforme: SCS-CN
    (com o CN da sub-bacia) -> chuva excedente; convolucao com a UH triangular
    SCS (com o tc da sub-bacia) -> Q(t). Reusa `hidrograma.hidrograma_projeto`.

    Retorna {id: DataFrame com index tempo_min e coluna Q_m3s}. Cada hidrograma
    esta no tempo LOCAL (geração na propria sub-bacia, sem viagem); a Fase 5
    (Muskingum-Cunge) propaga pela rede e soma nas confluencias ate o exutorio.
    """
    from chuva_vazao.hidrograma import SCSParams, hidrograma_projeto  # noqa: PLC0415

    out: dict[int, object] = {}
    for sid, p in params_por_sub.items():
        if p.cn is None:
            raise ValueError(
                f"Sub-bacia {sid} sem CN — rode calcular_parametros com bhae_path."
            )
        scs = SCSParams(
            area_km2=p.area_km2,
            tempo_concentracao_h=max(p.tc_min / 60.0, 0.05),
            CN=float(p.cn),
        )
        out[sid] = hidrograma_projeto(chuva.hietograma, scs, prf=prf)
    return out


# ---------------------------------------------------------------------------
# Fase 5 — roteamento na rede (Muskingum-Cunge)
# ---------------------------------------------------------------------------

def _muskingum_kx(L_km: float, celeridade_ms: float, X: float) -> tuple[float, float]:
    """
    K (h) e X de Muskingum para um trecho.

    K = tempo de viagem da onda no canal = L / c. A celeridade `c` (m/s) e o
    PARAMETRO de calibracao do roteamento: rios naturais ~1-1.5 m/s; rios com
    planicie de inundacao / armazenamento sao mais lentos (espalham mais o
    hidrograma -> pico menor). X = fator de ponderacao (0.2-0.3 tipico de rio
    natural; 0.5 = translacao pura sem atenuacao).

    Nota: a versao anterior usava c = (5/3)·V com V = L/tc, que dava ~3 m/s —
    rapido demais, subestimava o espalhamento e superestimava muito o pico.
    """
    K_h = (L_km * 1000.0) / (max(celeridade_ms, 0.05) * 3600.0)
    return K_h, min(max(X, 0.0), 0.5)


def _rotear_muskingum(inflow: np.ndarray, K_h: float, X: float, dt_h: float) -> np.ndarray:
    """Roteia um hidrograma de entrada por um trecho (Muskingum classico)."""
    den = K_h - K_h * X + 0.5 * dt_h
    if den <= 0 or K_h <= 0:
        return np.asarray(inflow, dtype=float).copy()
    C0 = (-K_h * X + 0.5 * dt_h) / den
    C1 = (K_h * X + 0.5 * dt_h) / den
    C2 = (K_h - K_h * X - 0.5 * dt_h) / den
    inflow = np.asarray(inflow, dtype=float)
    out = np.zeros_like(inflow)
    out[0] = inflow[0]
    for t in range(1, len(inflow)):
        out[t] = max(C0 * inflow[t] + C1 * inflow[t - 1] + C2 * out[t - 1], 0.0)
    return out


@dataclass
class HidrogramaDistribuido:
    """Resultado do roteamento: hidrograma no exutorio + por sub-bacia."""
    exutorio: "object"            # pd.DataFrame index tempo_min, coluna Q_m3s
    outflow_por_sub: dict          # {id: np.ndarray no eixo de tempo comum}
    tempo_min: "object"            # np.ndarray do eixo
    Qpico_m3s: float
    t_pico_min: float
    volume_hm3: float


def rotear_rede(
    disc: DiscretizacaoResult,
    params_por_sub: dict[int, ParametrosSubBacia],
    hidro_locais: dict[int, "object"],
    *,
    X_musk: float = 0.25,
    celeridade_ms: float = 1.2,
) -> HidrogramaDistribuido:
    """
    Propaga os hidrogramas locais pela rede (Muskingum-Cunge) e soma nas
    confluencias ate o exutorio, na ordem montante->jusante.

    Em cada sub-bacia: o inflow que vem de montante e roteado pelo canal da
    sub-bacia (atraso + atenuacao) e somado ao hidrograma lateral gerado nela; o
    resultado segue para a sub-bacia de jusante. O outflow do exutorio e o
    hidrograma final da bacia.
    """
    # Eixo de tempo comum (passo do hidrograma local), estendido p/ o atraso.
    ref = next(iter(hidro_locais.values()))
    idx = ref.index.to_numpy(dtype=float)
    dt_min = float(idx[1] - idx[0]) if len(idx) > 1 else 30.0
    tmax = max(float(df.index.max()) for df in hidro_locais.values())
    # Dimensiona o eixo pelo atraso do roteamento (somatorio dos K = L/c) para
    # acomodar celeridades baixas; a cauda e truncada no fim p/ a visualizacao.
    soma_K_min = sum(
        (p.L_km * 1000.0) / (max(celeridade_ms, 0.05) * 3600.0) * 60.0
        for p in params_por_sub.values()
    )
    eixo = np.arange(0.0, tmax * 2.0 + soma_K_min + dt_min, dt_min)
    dt_h = dt_min / 60.0

    grafo = disc.grafo()
    local = {
        sid: hidro_locais[sid]["Q_m3s"].reindex(eixo, fill_value=0.0).to_numpy(dtype=float)
        for sid in hidro_locais
    }
    inflow_mont = {sid: np.zeros(len(eixo)) for sid in grafo}
    outflow: dict[int, np.ndarray] = {}

    for sid in disc.ordem_roteamento:
        lateral = local.get(sid, np.zeros(len(eixo)))
        entrada = inflow_mont[sid]
        p = params_por_sub[sid]
        K_h, X = _muskingum_kx(p.L_km, celeridade_ms, X_musk)
        rotado = _rotear_muskingum(entrada, K_h, X, dt_h)
        out = rotado + lateral
        outflow[sid] = out
        d = grafo.get(sid)
        if d is not None and d in inflow_mont:
            inflow_mont[d] = inflow_mont[d] + out

    import pandas as pd  # noqa: PLC0415

    q_full = outflow[disc.exutorio_id]
    ipico = int(np.argmax(q_full))
    volume_m3 = float(np.trapezoid(q_full, eixo * 60.0))  # volume no hidrograma inteiro
    # Trunca a cauda (onde Q ja voltou a ~0) p/ o grafico mostrar subida+pico+recessao.
    qmax = float(q_full.max())
    fim = len(eixo)
    if qmax > 0:
        sig = np.where(q_full > 0.005 * qmax)[0]
        if len(sig):
            fim = min(len(eixo), int(sig[-1]) + 6)
    eixo_v, q_exu = eixo[:fim], q_full[:fim]
    df = pd.DataFrame({"tempo_min": eixo_v, "Q_m3s": q_exu}).set_index("tempo_min")
    return HidrogramaDistribuido(
        exutorio=df, outflow_por_sub=outflow, tempo_min=eixo_v,
        Qpico_m3s=float(q_full[ipico]), t_pico_min=float(eixo[ipico]),
        volume_hm3=volume_m3 / 1e6,
    )

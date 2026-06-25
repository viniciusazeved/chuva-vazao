"""
Testes do modulo distribuido (Fase 1: discretizacao + topologia de roteamento).

Roda como script:
    .venv/Scripts/python.exe tests/test_distribuido.py
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from chuva_vazao.distribuido import _D8_OFFSETS, _ordem_topologica


def test_ordem_topologica():
    """Ordem montante->jusante: exutorio por ultimo; montante antes do jusante."""
    g = {1: 3, 2: 3, 3: 5, 4: 5, 5: None}  # 1,2 -> 3 ; 3,4 -> 5 ; 5 = exutorio
    ordem = _ordem_topologica(g)
    assert set(ordem) == set(g), "ordem deve cobrir todas as sub-bacias"
    assert ordem[-1] == 5, f"exutorio deveria ser o ultimo; ordem={ordem}"
    assert ordem.index(1) < ordem.index(3) < ordem.index(5), "montante antes do jusante"
    assert ordem.index(4) < ordem.index(5)
    print("[OK] ordem topologica montante->jusante")


def test_convencao_d8():
    """
    REGRESSAO (bug grave): _D8_OFFSETS tem que bater com o D8Pointer do
    WhiteboxTools. A convencao do WBT e horaria a partir de NE (E=2, S=8...),
    nao a 'E=1' que aparece em varios lugares — usar a errada inverte o grafo.
    """
    import os  # noqa: PLC0415
    import tempfile  # noqa: PLC0415

    import numpy as np  # noqa: PLC0415
    import rasterio  # noqa: PLC0415
    from rasterio.transform import from_origin  # noqa: PLC0415

    from chuva_vazao import basin  # noqa: PLC0415

    N = 30
    rampas = {
        "E": (np.tile(np.linspace(100, 70, N), (N, 1)).astype("float32"), (0, 1)),
        "S": (np.tile(np.linspace(100, 70, N).reshape(-1, 1), (1, N)).astype("float32"), (1, 0)),
    }
    wbt = basin._new_whitebox_tools()
    wbt.set_verbose_mode(False)
    for nome, (z, off_esperado) in rampas.items():
        wd = tempfile.mkdtemp()
        wbt.set_working_dir(wd)
        with rasterio.open(os.path.join(wd, "dem.tif"), "w", driver="GTiff",
                           height=N, width=N, count=1, dtype="float32",
                           crs="EPSG:32723", transform=from_origin(5e5, 75e5, 30, 30),
                           nodata=-9999.0) as d:
            d.write(z, 1)
        wbt.breach_depressions(dem="dem.tif", output="b.tif")
        wbt.d8_pointer(dem="b.tif", output="d8.tif")
        with rasterio.open(os.path.join(wd, "d8.tif")) as d:
            code = int(d.read(1)[15, 15])
        assert _D8_OFFSETS.get(code) == off_esperado, (
            f"rampa p/ {nome}: D8 code {code} -> {_D8_OFFSETS.get(code)}, "
            f"esperado {off_esperado}"
        )
        print(f"  rampa {nome}: code {code} -> offset {_D8_OFFSETS[code]} OK")
    print("[OK] convencao D8 bate com WhiteboxTools")


def test_cn_interseccao_ponderada():
    """CN por sub-bacia = media area-ponderada das ottobacias (BHAE sintetico)."""
    import os  # noqa: PLC0415
    import tempfile  # noqa: PLC0415

    import geopandas as gpd  # noqa: PLC0415
    from shapely.geometry import box  # noqa: PLC0415

    from chuva_vazao.distribuido import (  # noqa: PLC0415
        DiscretizacaoResult, SubBacia, _cn_por_interseccao,
    )

    crs_utm = "EPSG:32723"
    # Duas ottobacias lado a lado: oeste CN=60, leste CN=80.
    oeste = box(500000, 7500000, 502000, 7501000)
    leste = box(502000, 7500000, 504000, 7501000)
    otto = gpd.GeoDataFrame(
        {"CN2.ANA_med": [60.0, 80.0]}, geometry=[oeste, leste], crs=crs_utm,
    ).to_crs(4326)
    fp = os.path.join(tempfile.mkdtemp(), "otto.gpkg")
    otto.to_file(fp, driver="GPKG")

    # Sub-bacia cobrindo 1000 m de cada ottobacia -> areas iguais -> CN=70.
    sb_utm = box(501000, 7500000, 503000, 7501000)
    sb_4326 = gpd.GeoSeries([sb_utm], crs=crs_utm).to_crs(4326).iloc[0]
    sub = SubBacia(id=1, geometry_4326=sb_4326, geometry_utm=sb_utm,
                   area_km2=2.0, downstream_id=None)
    disc = DiscretizacaoResult(
        subbacias=[sub], ordem_roteamento=[1], exutorio_id=1, crs_utm=crs_utm,
        n_subbacias=1, area_total_km2=2.0,
    )
    cn = _cn_por_interseccao(disc, Path(fp), "CN2.ANA_med")
    assert abs(cn[1] - 70.0) < 0.5, f"CN ponderado deveria ser ~70, deu {cn[1]}"
    print(f"[OK] CN interseccao area-ponderada: {cn[1]:.1f} (esperado 70)")


def test_arf_leclerc_schaake():
    """ARF de Leclerc-Schaake (1972): valor analitico + monotonicidade + limites."""
    import math  # noqa: PLC0415

    from chuva_vazao.distribuido import arf_leclerc_schaake  # noqa: PLC0415

    d025 = 6 ** 0.25
    esperado = 1 - math.exp(-1.1 * d025) + math.exp(-1.1 * d025 - 0.01 * 397)
    assert abs(arf_leclerc_schaake(397, 6) - esperado) < 1e-9, "formula divergiu"
    # area maior -> ARF menor; duracao maior -> ARF maior
    assert arf_leclerc_schaake(20, 6) > arf_leclerc_schaake(397, 6)
    assert arf_leclerc_schaake(397, 12) > arf_leclerc_schaake(397, 3)
    # limites e guarda
    assert 0.0 <= arf_leclerc_schaake(397, 6) <= 1.0
    assert arf_leclerc_schaake(0, 6) == 1.0  # area invalida -> sem reducao
    print(f"[OK] ARF Leclerc-Schaake: {arf_leclerc_schaake(397, 6):.3f} (397 km2, 6h)")


def test_muskingum_conserva_e_atenua():
    """Roteamento Muskingum: conserva massa e atenua/atrasa o pico de um pulso."""
    import numpy as np  # noqa: PLC0415

    from chuva_vazao.distribuido import _muskingum_kx, _rotear_muskingum  # noqa: PLC0415

    inflow = np.array(
        [0, 10, 20, 30, 40, 50, 40, 30, 20, 10, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
        dtype=float,
    )
    K_h, X = _muskingum_kx(L_km=4.0, celeridade_ms=1.2, X=0.25)  # K = L/c ~ 0.93 h
    out = _rotear_muskingum(inflow, K_h, X, dt_h=0.5)
    assert abs(out.sum() - inflow.sum()) / inflow.sum() < 0.02, "nao conservou massa"
    assert out.max() <= inflow.max() + 1e-6, "pico de saida excedeu o de entrada"
    assert int(np.argmax(out)) >= int(np.argmax(inflow)), "pico nao atrasou"
    assert out.max() < inflow.max(), "deveria haver atenuacao (X<0.5)"
    print(f"[OK] Muskingum: massa {inflow.sum():.0f}->{out.sum():.0f}, "
          f"pico {inflow.max():.0f}->{out.max():.0f} (atenua+atrasa)")


if __name__ == "__main__":
    falhas = 0
    for fn in [test_ordem_topologica, test_convencao_d8, test_cn_interseccao_ponderada,
               test_arf_leclerc_schaake, test_muskingum_conserva_e_atenua]:
        try:
            fn()
        except AssertionError as e:
            falhas += 1
            print(f"[FALHOU] {fn.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            falhas += 1
            print(f"[ERRO] {fn.__name__}: {type(e).__name__}: {e}")
    print()
    print("=" * 50)
    print("TODOS OS TESTES PASSARAM" if falhas == 0 else f"{falhas} TESTE(S) FALHARAM")
    sys.exit(1 if falhas else 0)

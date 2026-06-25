"""
Testes do solver standard-step (inundacao_1d) contra solucoes de referencia de
canal prismatico retangular, e do I/O do MDT por janela.

Roda como script (sem depender de pytest):
    .venv/Scripts/python.exe tests/test_inundacao_1d.py
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from chuva_vazao.inundacao_1d import (
    SecaoRio,
    estado_hidraulico,
    ler_mdt_janela,
    perfil_linha_dagua,
)
from chuva_vazao.secao_natural import (
    G,
    PontoSecao,
    lamina_critica,
    lamina_normal,
    secao_from_pontos,
)


# Parametros do canal de referencia
B = 20.0          # largura do fundo (m)
Q = 30.0          # vazao (m3/s)
N = 0.035         # Manning
S0 = 0.001        # declividade do leito
H_PAREDE = 3.0    # altura das paredes (m)
DX = 100.0        # espacamento entre secoes (m)


def secao_retangular_b(z_fundo: float, b: float = B, h_parede: float = H_PAREDE):
    """SecaoTransversal retangular de largura b, fundo em z_fundo, paredes h_parede."""
    pts = [
        PontoSecao(N=0.0, E=0.0, Z=z_fundo + h_parede),
        PontoSecao(N=0.0, E=0.001, Z=z_fundo),
        PontoSecao(N=0.0, E=b, Z=z_fundo),
        PontoSecao(N=0.0, E=b + 0.001, Z=z_fundo + h_parede),
    ]
    return secao_from_pontos(pts)


def secao_retangular(z_fundo: float):
    """SecaoTransversal retangular de largura B (atalho)."""
    return secao_retangular_b(z_fundo)


def y_critica_retangular() -> float:
    # Fr=1 num retangulo: A=B*y, espelho=B -> y_c = (Q^2 / (g B^2))^(1/3)
    return (Q * Q / (G * B * B)) ** (1.0 / 3.0)


def y_normal_retangular_exato() -> float:
    """y_n EXATO de canal retangular (R = A/P real, nao a aproximacao largo)."""
    from scipy.optimize import brentq  # noqa: PLC0415

    def f(y: float) -> float:
        A = B * y
        P = B + 2.0 * y
        R = A / P
        Q_calc = (1.0 / N) * A * R ** (2.0 / 3.0) * math.sqrt(S0)
        return Q_calc - Q

    return brentq(f, 1e-4, H_PAREDE - 1e-3)


def test_referencia_normal_critica():
    """Lamina normal/critica do modulo batem com a solucao retangular EXATA."""
    sec = secao_retangular(0.0)
    y_n_teorico = y_normal_retangular_exato()
    y_c_teorico = y_critica_retangular()

    y_n = lamina_normal(sec, Q, N, S0).y_lamina_m
    y_c = lamina_critica(sec, Q).y_lamina_m

    # Paredes de 1 mm introduzem erro <0.5% vs retangulo ideal.
    assert abs(y_n - y_n_teorico) / y_n_teorico < 0.01, (
        f"y_normal: modulo={y_n:.4f}, exato={y_n_teorico:.4f}"
    )
    assert abs(y_c - y_c_teorico) / y_c_teorico < 0.01, (
        f"y_critica: modulo={y_c:.4f}, exato={y_c_teorico:.4f}"
    )
    print(f"[OK] normal/critica: y_n={y_n:.4f} (exato={y_n_teorico:.4f}), "
          f"y_c={y_c:.4f} (exato={y_c_teorico:.4f})")


def _montar_canal(n_secoes: int) -> list[SecaoRio]:
    """Canal prismatico: leito sobe S0*DX por secao indo p/ montante."""
    secoes = []
    for i in range(n_secoes):
        z_fundo = i * S0 * DX
        secoes.append(SecaoRio(
            secao=secao_retangular(z_fundo),
            estaca_rio_m=i * DX,
            rotulo=f"S{i}",
        ))
    return secoes


def test_escoamento_uniforme():
    """
    TESTE DE OURO: comecando na profundidade normal, o perfil deve PERMANECER
    na normal ao longo de todo o canal prismatico (escoamento uniforme). A WSE
    desce paralela ao leito; a lamina sobre o thalweg fica constante.
    """
    secoes = _montar_canal(6)
    y_n = y_normal_retangular_exato()

    perfil = perfil_linha_dagua(secoes, Q, N, cc_jusante="normal")

    laminas = [p.y_lamina_m for p in perfil.pontos]
    print(f"[uniforme] y_n teorico={y_n:.4f} | laminas={[round(l,4) for l in laminas]}")

    # Todas as laminas devem ser ~constantes e ~y_n
    for i, lam in enumerate(laminas):
        assert abs(lam - laminas[0]) < 0.01, (
            f"Lamina nao-uniforme na secao {i}: {lam:.4f} vs jusante {laminas[0]:.4f}"
        )
    # WSE deve subir monotonicamente p/ montante (leito sobe), paralela ao leito
    wses = [p.wse_m for p in perfil.pontos]
    for i in range(1, len(wses)):
        assert wses[i] > wses[i - 1], "WSE deveria subir p/ montante"
    assert not perfil.warnings, f"Warnings inesperados: {perfil.warnings}"
    print(f"[OK] escoamento uniforme: lamina constante = {laminas[0]:.4f} m")


def test_remanso_M1():
    """
    Perfil M1: condicao de jusante acima da normal (barramento/afogamento).
    A lamina deve DECRESCER de jusante p/ montante, tendendo assintoticamente
    a y_n — sempre acima da normal (perfil M1 fica acima da linha normal).
    """
    secoes = _montar_canal(15)
    y_n = y_normal_retangular_exato()
    cota_jus = secoes[0].secao.z_thalweg + 2.0  # lamina 2.0 m > y_n

    perfil = perfil_linha_dagua(
        secoes, Q, N, cc_jusante="cota", cota_jusante_m=cota_jus,
    )
    laminas = [p.y_lamina_m for p in perfil.pontos]
    print(f"[M1] y_n={y_n:.4f} | laminas={[round(l,3) for l in laminas]}")

    # Decrescente p/ montante
    for i in range(1, len(laminas)):
        assert laminas[i] <= laminas[i - 1] + 1e-6, (
            f"M1 deveria decrescer p/ montante; subiu na secao {i}"
        )
    # Sempre acima (ou igual) da normal
    for i, lam in enumerate(laminas):
        assert lam >= y_n - 0.02, f"M1 abaixo da normal na secao {i}: {lam:.4f} < {y_n:.4f}"
    # Tende a y_n: a ultima secao deve estar bem mais perto da normal que a primeira
    assert (laminas[-1] - y_n) < (laminas[0] - y_n) * 0.6, (
        "M1 nao convergiu o suficiente para a normal ao longo do canal"
    )
    print(f"[OK] remanso M1: jusante={laminas[0]:.3f} -> montante={laminas[-1]:.3f} "
          f"(y_n={y_n:.3f})")


def test_conservacao_energia_sem_atrito():
    """Canal horizontal, n->0: energia total H deve se conservar entre secoes."""
    secoes = [
        SecaoRio(secao=secao_retangular(0.0), estaca_rio_m=0.0),
        SecaoRio(secao=secao_retangular(0.0), estaca_rio_m=DX),
    ]
    n_min = 1e-4
    perfil = perfil_linha_dagua(
        secoes, Q, n_min, cc_jusante="cota",
        cota_jusante_m=1.5, coef_contracao=0.0, coef_expansao=0.0,
    )
    est_j = estado_hidraulico(secoes[0].secao, Q, n_min, perfil.pontos[0].wse_m)
    est_m = estado_hidraulico(secoes[1].secao, Q, n_min, perfil.pontos[1].wse_m)
    print(f"[energia] H_jus={est_j.H_m:.5f}  H_mon={est_m.H_m:.5f}")
    assert abs(est_m.H_m - est_j.H_m) < 0.01, (
        f"Energia nao conservada: H_jus={est_j.H_m:.5f}, H_mon={est_m.H_m:.5f}"
    )
    print(f"[OK] conservacao de energia: dH = {abs(est_m.H_m - est_j.H_m):.2e} m")


def test_perda_localizada_contracao_expansao():
    """
    REGRESSAO (bug de sinal h_e): numa CONTRACAO no sentido do fluxo (montante
    largo -> jusante estreito, velocidade cresce p/ jusante), o coeficiente de
    CONTRACAO deve ser o que altera o resultado; o de expansao, nao.
    """
    sec_jus = secao_retangular_b(z_fundo=0.0, b=10.0)   # estreito (jusante)
    sec_mon = secao_retangular_b(z_fundo=0.0, b=40.0)   # largo (montante)
    secoes = [
        SecaoRio(secao=sec_jus, estaca_rio_m=0.0, rotulo="jus_estreito"),
        SecaoRio(secao=sec_mon, estaca_rio_m=100.0, rotulo="mon_largo"),
    ]
    Q_loc = 40.0
    n_min = 1e-4  # isola h_e (h_f -> 0)
    kw = dict(cc_jusante="cota", cota_jusante_m=2.0)
    wse_base = perfil_linha_dagua(secoes, Q_loc, n_min, coef_contracao=0.0, coef_expansao=0.0, **kw).pontos[1].wse_m
    wse_contr = perfil_linha_dagua(secoes, Q_loc, n_min, coef_contracao=1.0, coef_expansao=0.0, **kw).pontos[1].wse_m
    wse_exp = perfil_linha_dagua(secoes, Q_loc, n_min, coef_contracao=0.0, coef_expansao=1.0, **kw).pontos[1].wse_m
    print(f"[h_e] base={wse_base:.4f} | so_contracao={wse_contr:.4f} | so_expansao={wse_exp:.4f}")
    assert abs(wse_contr - wse_base) > 1e-3, "coef de CONTRACAO nao foi aplicado num caso de contracao"
    assert abs(wse_exp - wse_base) < 1e-4, "coef de EXPANSAO foi aplicado num caso de contracao (sinal trocado)"
    print("[OK] perda localizada: contracao aplicada na contracao, expansao inerte")


def test_invariancia_datum():
    """
    REGRESSAO (tolerancia do brentq na cota absoluta): o mesmo canal uniforme em
    datum alto (thalweg ~390 m, ~39 km) deve manter a lamina tao constante
    quanto no datum 0 — senao a precisao escala com a elevacao.
    """
    for base in [0.0, 390.0, 39000.0]:
        secoes = [
            SecaoRio(secao=secao_retangular(base + i * S0 * DX), estaca_rio_m=i * DX)
            for i in range(8)
        ]
        perfil = perfil_linha_dagua(secoes, Q, N, cc_jusante="normal")
        laminas = [p.y_lamina_m for p in perfil.pontos]
        drift = max(laminas) - min(laminas)
        print(f"[datum={base:>8.0f}] deriva da lamina = {drift:.2e} m")
        assert drift < 1e-4, f"datum={base}: deriva {drift:.2e} m (esperado < 1e-4)"
    print("[OK] invariancia de datum: lamina estavel ate ~39 km de cota")


def test_extravasamento_nao_cascateia():
    """
    REGRESSAO (cascata de energia fantasma): uma secao estreita que extravasa no
    meio do trecho NAO pode marcar a secao larga a montante como extravasada por
    contagio da carga cinetica fantasma.
    """
    sec_jus = secao_retangular_b(z_fundo=0.2, b=30.0, h_parede=4.0)
    sec_meio = secao_retangular_b(z_fundo=0.1, b=2.5, h_parede=0.5)   # rasa: extravasa
    sec_mon = secao_retangular_b(z_fundo=0.0, b=30.0, h_parede=4.0)   # larga e folgada
    secoes = [
        SecaoRio(secao=sec_jus, estaca_rio_m=0.0, rotulo="jus"),
        SecaoRio(secao=sec_meio, estaca_rio_m=100.0, rotulo="meio_estreito"),
        SecaoRio(secao=sec_mon, estaca_rio_m=200.0, rotulo="mon_largo"),
    ]
    Q_loc = 40.0
    perfil = perfil_linha_dagua(
        secoes, Q_loc, 0.035,
        cc_jusante="cota", cota_jusante_m=sec_jus.z_thalweg + 2.0,
    )
    p_meio, p_mon = perfil.pontos[1], perfil.pontos[2]
    print(f"[cascata] meio.extrav={p_meio.extravasou} | mon.extrav={p_mon.extravasou}, "
          f"mon.y={p_mon.y_lamina_m:.3f}")
    assert p_meio.extravasou, "secao do meio (rasa) deveria extravasar"
    assert not p_mon.extravasou, "extravasamento cascateou ESPURIAMENTE p/ a secao larga a montante"
    assert p_mon.y_lamina_m < 4.0, "WSE da montante travada na margem (sintoma de cascata)"
    print("[OK] extravasamento contido: secao larga a montante nao foi contaminada")


def test_cc_extravasa_nao_crasha():
    """
    REGRESSAO (CC sem try/except): Q que excede a capacidade critica da secao de
    jusante deve degradar com warning, nao derrubar o solver — em 'critica' e 'normal'.
    """
    sec0 = secao_retangular_b(z_fundo=0.0, b=2.0, h_parede=0.5)   # rasa
    sec1 = secao_retangular_b(z_fundo=0.5, b=2.0, h_parede=0.5)
    secoes = [
        SecaoRio(secao=sec0, estaca_rio_m=0.0),
        SecaoRio(secao=sec1, estaca_rio_m=100.0),
    ]
    Q_grande = 100.0
    for cc in ["critica", "normal"]:
        perfil = perfil_linha_dagua(secoes, Q_grande, 0.035, cc_jusante=cc)  # nao deve crashar
        assert perfil.pontos[0].extravasou, f"cc={cc}: jusante deveria estar extravasada"
        assert perfil.warnings, f"cc={cc}: deveria emitir warning de extravasamento"
        print(f"[cc={cc}] degradou com {len(perfil.warnings)} warning(s), sem crash")
    print("[OK] condicao de contorno degrada graciosamente quando Q extravasa")


def test_ler_janela_decima_sem_overview():
    """REGRESSAO: overview_level deve decimar a leitura MESMO sem pirâmides internas."""
    import os  # noqa: PLC0415
    import tempfile  # noqa: PLC0415

    import numpy as np  # noqa: PLC0415
    import rasterio  # noqa: PLC0415
    from rasterio.transform import from_origin  # noqa: PLC0415

    from chuva_vazao.inundacao_1d import ler_mdt_janela  # noqa: PLC0415

    R, C = np.mgrid[0:400, 0:400]
    z = (400.0 + 0.01 * C).astype("float32")
    p = os.path.join(tempfile.gettempdir(), "no_overview.tif")
    with rasterio.open(p, "w", driver="GTiff", height=400, width=400, count=1,
                       dtype="float32", crs="EPSG:31983",
                       transform=from_origin(580000, 7506000, 0.5, 0.5),
                       nodata=-9999.0) as dst:
        dst.write(z, 1)
    with rasterio.open(p) as src:
        assert src.overviews(1) == [], "raster de teste nao deveria ter overviews"

    bounds = (580000.0, 7505900.0, 580200.0, 7506000.0)
    nat = ler_mdt_janela(p, bounds)
    ov2 = ler_mdt_janela(p, bounds, overview_level=2)
    print(f"[overview] nativo={nat.array.shape} res={nat.res_m:.2f} | "
          f"ov2={ov2.array.shape} res={ov2.res_m:.2f}")
    assert ov2.array.size < nat.array.size / 3, "overview nao decimou (gate de pirâmide?)"
    assert ov2.res_m > nat.res_m * 1.5, "res do overview nao aumentou"
    print("[OK] overview decima sem pirâmides internas")


def test_ler_janela_alinhamento():
    """REGRESSAO: array e transform alinhados mesmo com janela de offset fracionário."""
    import os  # noqa: PLC0415
    import tempfile  # noqa: PLC0415

    import numpy as np  # noqa: PLC0415
    import rasterio  # noqa: PLC0415
    from rasterio.transform import from_origin  # noqa: PLC0415

    from chuva_vazao.inundacao_1d import ler_mdt_janela  # noqa: PLC0415

    R, C = np.mgrid[0:300, 0:300]
    z = (C - R).astype("float32")   # gradiente conhecido (vizinhos diferem por 1)
    p = os.path.join(tempfile.gettempdir(), "align.tif")
    with rasterio.open(p, "w", driver="GTiff", height=300, width=300, count=1,
                       dtype="float32", crs="EPSG:31983",
                       transform=from_origin(580000, 7506000, 0.5, 0.5),
                       nodata=-9999.0) as dst:
        dst.write(z, 1)

    # bounds com offset FRACIONARIO (nao multiplo de 0,5 m)
    bounds = (580005.3, 7505980.7, 580030.3, 7506000.7)
    jan = ler_mdt_janela(p, bounds)
    t, arr = jan.transform, jan.array

    # Para alguns pixels do recorte, o valor do array deve bater com o valor do
    # raster nativo amostrado no centro daquele pixel (via transform). Se o
    # transform estivesse desalinhado, o sample cairia no pixel vizinho (~1 off).
    alvos = [(0, 0), (arr.shape[0] - 1, arr.shape[1] - 1), (arr.shape[0] // 2, arr.shape[1] // 2)]
    with rasterio.open(p) as src:
        pts = [
            (t.c + t.a * (c + 0.5) + t.b * (r + 0.5),
             t.f + t.d * (c + 0.5) + t.e * (r + 0.5))
            for r, c in alvos
        ]
        verdade = [float(v[0]) for v in src.sample(pts)]
    for (r, c), vt in zip(alvos, verdade):
        print(f"[align] arr[{r},{c}]={arr[r, c]:.1f} vs nativo={vt:.1f}")
        assert abs(float(arr[r, c]) - vt) < 0.6, (
            f"misregistro array x transform em ({r},{c}): {arr[r, c]} vs {vt}"
        )
    print("[OK] array e transform alinhados com janela fracionaria")


def test_mancha_cutlines():
    """
    REGRESSAO (cut-lines): a mancha usa o corredor poligonal entre as transversais
    (sem cair no fallback de raio), e curva fechada (hairpin) nao quebra.
    """
    import os  # noqa: PLC0415
    import tempfile  # noqa: PLC0415

    import numpy as np  # noqa: PLC0415
    import rasterio  # noqa: PLC0415
    from rasterio.transform import from_origin  # noqa: PLC0415
    from rasterio.warp import transform as warp_transform  # noqa: PLC0415

    from chuva_vazao.inundacao_1d import (  # noqa: PLC0415
        gerar_secoes_do_eixo, perfil_linha_dagua, projetar_mancha,
    )

    x0, y0, Wd, Hd, pxs = 580000.0, 7506000.0, 600, 600, 1.0
    ccol, rrow = np.meshgrid(np.arange(Wd), np.arange(Hd))
    zz = (400.0 + 0.002 * ccol + 0.05 * np.abs(rrow - 300)).astype("float32")
    p = os.path.join(tempfile.gettempdir(), "cutlines.tif")
    with rasterio.open(p, "w", driver="GTiff", height=Hd, width=Wd, count=1,
                       dtype="float32", crs="EPSG:31983",
                       transform=from_origin(x0, y0, pxs, pxs), nodata=-9999.0) as dst:
        dst.write(zz, 1)

    yt = y0 - 300 * pxs
    lon, lat = warp_transform("EPSG:31983", "EPSG:4326",
                              [580050, 580180, 580310, 580440, 580550], [yt] * 5)
    ger = gerar_secoes_do_eixo(list(zip(lon, lat)), p, espacamento_m=50.0,
                               largura_m=100.0, n_amostras=51)
    perfil = perfil_linha_dagua(ger.secoes, Q=15.0, n=0.035, cc_jusante="normal")
    mancha = projetar_mancha(ger.secoes, perfil, p, crs_secoes=ger.crs_trabalho)
    print(f"[cutlines] area={mancha.area_alagada_m2:.0f} m2, avisos={mancha.avisos}")
    assert mancha.area_alagada_m2 > 0
    assert not any("raio do thalweg" in a for a in mancha.avisos), (
        f"caiu no fallback de raio em vez do corredor: {mancha.avisos}"
    )

    # hairpin (eixo em U fechado) — robustez: nao deve quebrar
    th = np.linspace(0, np.pi, 12)
    xh = 580300 + 60 * np.cos(th)
    yh = (y0 - 300) + 60 * np.sin(th)
    lonh, lath = warp_transform("EPSG:31983", "EPSG:4326", list(xh), list(yh))
    gh = gerar_secoes_do_eixo(list(zip(lonh, lath)), p, espacamento_m=20.0,
                              largura_m=60.0, n_amostras=41)
    ph = perfil_linha_dagua(gh.secoes, Q=10.0, n=0.035, cc_jusante="normal")
    mh = projetar_mancha(gh.secoes, ph, p, crs_secoes=gh.crs_trabalho)
    assert mh.area_alagada_m2 >= 0
    print(f"[cutlines] hairpin OK: area={mh.area_alagada_m2:.0f} m2")
    print("[OK] cut-lines: corredor poligonal usado; hairpin nao quebra")


def test_io_cog_janela():
    """Le uma janela pequena do MDE_TRIBUTARIOS_V6 local (sem carregar inteiro)."""
    mdt = Path(
        r"C:/Users/vinic/OneDrive/PMBM/Regularização Fundiaria/MDE_TRIBUTARIOS_V6.tif"
    )
    if not mdt.exists():
        print(f"[SKIP] MDT local nao encontrado: {mdt}")
        return
    # Janela de ~200x200 m no centro da extensao
    bounds = (582400.0, 7506900.0, 582600.0, 7507100.0)
    jan = ler_mdt_janela(mdt, bounds)
    import numpy as np
    finite = np.isfinite(jan.array)
    print(f"[io] shape={jan.array.shape}, res={jan.res_m:.2f} m, "
          f"validos={finite.mean()*100:.0f}%, "
          f"z=[{np.nanmin(jan.array):.1f}, {np.nanmax(jan.array):.1f}]")
    assert jan.array.shape[0] > 300 and jan.array.shape[1] > 300, (
        f"Janela 200m a 0.5m deveria dar ~400x400 px; deu {jan.array.shape}"
    )
    assert abs(jan.res_m - 0.5) < 0.05, f"Resolucao esperada ~0.5 m, deu {jan.res_m}"
    # MDE_TRIBUTARIOS e esparso (so corredores dos tributarios) — basta ter dado
    # valido e altitudes plausiveis para Barra Mansa (~360-530 m).
    assert finite.mean() > 0.05, "Janela sem dado valido — bounds/CRS errados?"
    z_validos = jan.array[finite]
    assert 300.0 < float(np.nanmin(z_validos)) < 600.0, "Altitudes fora do esperado"
    print("[OK] I/O COG por janela (raster esparso de tributarios)")


if __name__ == "__main__":
    falhas = 0
    for fn in [
        test_referencia_normal_critica,
        test_escoamento_uniforme,
        test_remanso_M1,
        test_conservacao_energia_sem_atrito,
        test_perda_localizada_contracao_expansao,
        test_invariancia_datum,
        test_extravasamento_nao_cascateia,
        test_cc_extravasa_nao_crasha,
        test_ler_janela_decima_sem_overview,
        test_ler_janela_alinhamento,
        test_mancha_cutlines,
        test_io_cog_janela,
    ]:
        try:
            fn()
        except AssertionError as e:
            falhas += 1
            print(f"[FALHOU] {fn.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            falhas += 1
            print(f"[ERRO] {fn.__name__}: {type(e).__name__}: {e}")
    print()
    print(f"{'='*50}")
    print("TODOS OS TESTES PASSARAM" if falhas == 0 else f"{falhas} TESTE(S) FALHARAM")
    sys.exit(1 if falhas else 0)

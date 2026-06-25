"""
Modelagem de inundacao fluvial 1D por standard-step (escoamento gradualmente
variado permanente) + projecao da linha d'agua sobre o MDT.

Pipeline conceitual (a UI monta isso por cima):
    1. Usuario define o eixo do rio e o app gera secoes transversais
       perpendiculares, amostradas do MDT (reaproveita `secao_mdt`).
    2. Vazao de projeto Q vem do hidrograma SCS (pico) da pagina 3.
    3. `perfil_linha_dagua` resolve a cota d'agua (WSE) secao a secao por
       standard-step subcritico, marchando de jusante para montante a partir de
       uma condicao de contorno (lamina normal, critica ou cota dada).
    4. `projetar_mancha` interpola a WSE no corredor e subtrai o MDT pra gerar a
       mancha (restrita ao corredor poligonal entre as transversais — cut-lines).

Este modulo cobre as etapas 3 (nucleo hidraulico, puro — testavel sem raster) e
o I/O do MDT por janela/AOI (etapa de leitura do COG via /vsicurl).

Formulacao do standard-step (Chow 1959, cap. 10; HEC-RAS Reference Manual):
    Energia total na secao:  H = WSE + alpha * V^2 / (2 g),  V = Q / A
    Entre secao de jusante (j) e a de montante (m), separadas por dx no rio:
        H_m = H_j + h_f + h_e
        h_f = dx * (Sf_j + Sf_m) / 2          (average friction slope)
        Sf  = (n Q)^2 / (A^2 R^(4/3))         (Manning invertido)
        h_e = C * |V_m^2/2g - V_j^2/2g|       (perda de contracao/expansao)
    Resolve-se H_m(WSE_m) = H_j + h_f + h_e para WSE_m na faixa subcritica
    (acima da profundidade critica). Sem raiz subcritica -> controle hidraulico
    (regime critico) ou extravasamento, ambos sinalizados em `warnings`.

Convencoes:
    - Secoes em CRS metrico (UTM/SIRGAS). Cotas absolutas no datum do MDT.
    - `estaca_rio_m` (river station) cresce de jusante para montante.
    - alpha (coef. de Coriolis) = 1.0 por padrao.

Referencias:
    - Chow, V. T. (1959). Open-Channel Hydraulics, cap. 10 (standard step method).
    - USACE (2016). HEC-RAS Hydraulic Reference Manual, Energy equation.
    - Chaudhry, M. H. (2008). Open-Channel Flow, 2a ed.
"""
from __future__ import annotations

import importlib.util
import json
import math
import os
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Literal, Sequence

# Antes de importar rasterio, apontar PROJ_DATA/GDAL_DATA para os bundles do
# proprio rasterio. Evita conflito com PROJ do PostgreSQL/PostGIS quando ambos
# estao no mesmo host Windows. Mesmo workaround do basin.py/secao_mdt.py.
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

from scipy.optimize import brentq

from chuva_vazao.secao_natural import (
    G,
    SecaoTransversal,
    geometria_molhada,
    lamina_critica,
    lamina_normal,
)


# Coeficiente de Coriolis (distribuicao de velocidade). 1.0 = uniforme.
ALPHA = 1.0

# Coeficientes de perda localizada (HEC-RAS default para transicoes graduais).
COEF_CONTRACAO = 0.1   # escoamento acelera no sentido do fluxo
COEF_EXPANSAO = 0.3    # escoamento desacelera no sentido do fluxo


# ---------------------------------------------------------------------------
# Estruturas
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SecaoRio:
    """Secao transversal posicionada ao longo do eixo do rio."""
    secao: SecaoTransversal
    estaca_rio_m: float          # river station; cresce de jusante p/ montante
    rotulo: str = ""


@dataclass(frozen=True)
class EstadoHidraulico:
    """Estado do escoamento numa secao para uma cota d'agua WSE."""
    wse_m: float                 # cota d'agua absoluta
    y_lamina_m: float            # lamina sobre o thalweg
    A_m2: float
    R_m: float
    B_m: float
    V_m_s: float
    Sf_m_per_m: float            # declividade da linha de energia (atrito)
    carga_cinetica_m: float      # alpha V^2 / 2g
    H_m: float                   # energia total = WSE + carga cinetica
    Fr: float
    regime: str                  # "subcritico" | "critico" | "supercritico"


@dataclass(frozen=True)
class PontoPerfil:
    """Resultado do perfil de linha d'agua numa secao."""
    estaca_rio_m: float
    z_thalweg_m: float
    wse_m: float
    y_lamina_m: float
    A_m2: float
    R_m: float
    V_m_s: float
    Fr: float
    Sf_m_per_m: float
    regime: str
    controle_critico: bool       # True se a secao controlou em regime critico
    extravasou: bool             # True se a WSE atingiu a margem mais baixa


@dataclass
class PerfilLinhaAgua:
    """Perfil de linha d'agua ao longo do trecho (de jusante p/ montante)."""
    pontos: list[PontoPerfil]
    Q_m3_s: float
    n_manning: float
    cc_jusante: str
    warnings: list[str] = field(default_factory=list)

    @property
    def wse_por_estaca(self) -> list[tuple[float, float]]:
        """Pares (estaca_rio_m, wse_m) ordenados de jusante p/ montante."""
        return [(p.estaca_rio_m, p.wse_m) for p in self.pontos]

    def to_dataframe(self):
        import pandas as pd  # noqa: PLC0415
        return pd.DataFrame([
            {
                "estaca_rio_m": p.estaca_rio_m,
                "z_thalweg_m": p.z_thalweg_m,
                "WSE_m": p.wse_m,
                "y_m": p.y_lamina_m,
                "A_m2": p.A_m2,
                "V_m_s": p.V_m_s,
                "Fr": p.Fr,
                "Sf": p.Sf_m_per_m,
                "regime": p.regime,
                "controle": p.controle_critico,
                "extravasou": p.extravasou,
            }
            for p in self.pontos
        ])


# ---------------------------------------------------------------------------
# Hidraulica de uma secao a uma cota d'agua
# ---------------------------------------------------------------------------

def estado_hidraulico(
    secao: SecaoTransversal, Q: float, n: float, wse: float,
) -> EstadoHidraulico | None:
    """
    Estado do escoamento numa secao para a cota d'agua absoluta `wse`.

    Retorna None se a secao esta seca nessa cota (A <= 0). Reaproveita
    `geometria_molhada` (trata secao irregular, multiplas regioes molhadas).
    """
    gm = geometria_molhada(secao, wse)
    if gm.A_m2 <= 1e-9 or gm.P_m <= 0:
        return None
    A = gm.A_m2
    R = gm.R_m
    B = gm.B_m
    V = Q / A
    # Sf da equacao de Manning: Q = (1/n) A R^(2/3) Sf^(1/2)  ->  Sf = (nQ/(A R^(2/3)))^2
    Sf = (n * Q) ** 2 / (A ** 2 * R ** (4.0 / 3.0))
    carga = ALPHA * V * V / (2.0 * G)
    H = wse + carga
    D_h = A / B if B > 0 else 0.0
    Fr = V / math.sqrt(G * D_h) if D_h > 0 else 0.0
    if Fr < 0.95:
        regime = "subcritico"
    elif Fr <= 1.05:
        regime = "critico"
    else:
        regime = "supercritico"
    return EstadoHidraulico(
        wse_m=wse, y_lamina_m=wse - secao.z_thalweg, A_m2=A, R_m=R, B_m=B,
        V_m_s=V, Sf_m_per_m=Sf, carga_cinetica_m=carga, H_m=H, Fr=Fr,
        regime=regime,
    )


# ---------------------------------------------------------------------------
# Passo do standard-step: dada a secao de jusante resolvida, acha a montante
# ---------------------------------------------------------------------------

def _resolver_wse_montante(
    sec_mon: SecaoTransversal,
    Q: float,
    n: float,
    dx: float,
    est_jus: EstadoHidraulico,
    *,
    coef_contracao: float = COEF_CONTRACAO,
    coef_expansao: float = COEF_EXPANSAO,
) -> tuple[float, bool, bool]:
    """
    Resolve a cota d'agua na secao de montante por balanco de energia.

    Marcha subcritica: busca a raiz de H_m(wse) = H_j + h_f + h_e na faixa
    [profundidade critica, margem mais baixa]. Retorna (wse_m, controle_critico,
    extravasou):
        - controle_critico=True: nao ha solucao subcritica (energia de jusante
          insuficiente mesmo na critica) -> a secao impoe controle critico.
        - extravasou=True: nem na cota da margem mais baixa o balanco fecha
          (a secao nao comporta o escoamento) -> WSE travada no topo.
    """
    # Piso da faixa subcritica = profundidade critica.
    try:
        y_c = lamina_critica(sec_mon, Q).y_w_m
    except ValueError:
        y_c = sec_mon.z_thalweg + 1e-3
    y_lo = max(y_c, sec_mon.z_thalweg) + 1e-4
    y_hi = sec_mon.z_max - 1e-4

    H_j = est_jus.H_m

    def balanco(wse: float) -> float:
        est = estado_hidraulico(sec_mon, Q, n, wse)
        if est is None:
            return 1e9  # secao seca: forca sinal positivo (energia "infinita")
        h_f = dx * (est_jus.Sf_m_per_m + est.Sf_m_per_m) / 2.0
        # Convencao HEC-RAS: coef. de CONTRACAO se aplica quando a carga cinetica
        # CRESCE no sentido do fluxo (jusante) — i.e. e maior na secao de jusante
        # que na de montante, logo d_carga = carga_mon - carga_jus < 0. Caso
        # contrario (escoamento desacelera p/ jusante), expansao.
        d_carga = est.carga_cinetica_m - est_jus.carga_cinetica_m
        coef = coef_contracao if d_carga < 0 else coef_expansao
        h_e = coef * abs(d_carga)
        return est.H_m - (H_j + h_f + h_e)

    if y_hi <= y_lo:
        # Secao rasa demais: nao ha faixa subcritica util. Extravasa.
        return y_hi, False, True

    b_lo = balanco(y_lo)
    b_hi = balanco(y_hi)

    if b_lo == 0.0:
        return y_lo, False, False
    if b_lo * b_hi < 0.0:
        # Resolve na PROFUNDIDADE sobre o thalweg (ordem O(m)), nao na cota
        # absoluta: senao a tolerancia relativa do brentq escala com o datum do
        # MDT (~390 m) e a lamina perde precisao. Reconstroi a cota ao final.
        z_th = sec_mon.z_thalweg
        d_root = brentq(
            lambda d: balanco(z_th + d), y_lo - z_th, y_hi - z_th, xtol=1e-7,
        )
        return z_th + d_root, False, False

    # Sem mudanca de sinal na faixa subcritica.
    if b_lo > 0.0:
        # Mesmo na critica a energia da secao ja excede a de jusante: controle
        # critico (a secao "segura" o escoamento). Adota a critica.
        return y_lo, True, False
    # b_hi < 0: nem no topo da margem o balanco fecha -> extravasamento.
    return y_hi, False, True


# ---------------------------------------------------------------------------
# Condicao de contorno de jusante
# ---------------------------------------------------------------------------

def _declividade_leito(sec_jus: SecaoRio, sec_mon: SecaoRio) -> tuple[float, str | None]:
    """
    Declividade do leito entre os thalwegs, no sentido jusante -> montante.

    Retorna (S, aviso). S sempre > 0 (piso 1e-5 pra Manning nao explodir). O
    `aviso` (!= None) sinaliza leito plano ou invertido — thalweg de montante
    nao mais alto que o de jusante — caso em que usar a profundidade normal como
    contorno perde sentido fisico. Diverge de propostito do abs() silencioso
    anterior, alinhando com a validacao explicita de secao_natural.declividade_trecho.
    """
    dx = abs(sec_mon.estaca_rio_m - sec_jus.estaca_rio_m)
    if dx <= 0:
        raise ValueError("Estacas de rio coincidentes — dx=0 entre secoes.")
    dz = sec_mon.secao.z_thalweg - sec_jus.secao.z_thalweg  # >0 se leito sobe p/ montante
    aviso = None
    if dz <= 1e-6:
        aviso = (
            f"Leito plano ou invertido perto da jusante (dz={dz:.3f} m): a "
            "condicao de contorno por profundidade normal fica imprecisa."
        )
    return max(dz / dx, 1e-5), aviso


def _wse_jusante(
    secoes: Sequence[SecaoRio],
    Q: float,
    n: float,
    cc: Literal["normal", "critica", "cota"],
    cota_jusante_m: float | None,
) -> tuple[float, list[str], bool]:
    """
    Cota d'agua na secao de jusante (indice 0) conforme a condicao de contorno.

    Retorna (wse, warnings, extravasou). NENHUM caminho deixa ValueError de
    lamina_critica/lamina_normal vazar: quando Q nao comporta lamina critica na
    secao de jusante, o contorno degrada para a cota de extravasamento (margem
    mais baixa) com warning, no mesmo padrao do marchamento interior.
    """
    sec0 = secoes[0].secao
    warns: list[str] = []
    eps = 1e-4
    z_max_safe = sec0.z_max - eps

    def _critica_segura() -> tuple[float, bool]:
        try:
            return lamina_critica(sec0, Q).y_w_m, False
        except ValueError:
            warns.append(
                f"Secao de jusante nao comporta lamina critica para "
                f"Q={Q:.1f} m3/s — contorno fixado na margem (extravasamento)."
            )
            return z_max_safe, True

    if cc == "cota":
        if cota_jusante_m is None:
            raise ValueError("cc='cota' exige cota_jusante_m.")
        if cota_jusante_m <= sec0.z_thalweg:
            raise ValueError(
                f"Cota de jusante ({cota_jusante_m:.3f}) <= thalweg "
                f"({sec0.z_thalweg:.3f}) da secao de jusante."
            )
        return cota_jusante_m, warns, cota_jusante_m >= sec0.z_max

    if cc == "critica":
        wse, extrav = _critica_segura()
        return wse, warns, extrav

    # normal: precisa da declividade local do leito perto da jusante
    if len(secoes) < 2:
        raise ValueError("cc='normal' precisa de >= 2 secoes para a declividade.")
    S, aviso = _declividade_leito(secoes[0], secoes[1])
    if aviso:
        warns.append(aviso)
    try:
        return lamina_normal(sec0, Q, n, S).y_w_m, warns, False
    except ValueError as exc:
        # Q extravasa a secao de jusante na normal: cai pra critica (protegida).
        warns.append(
            f"Condicao de jusante 'normal' falhou ({exc}). Tentando profundidade "
            "critica como contorno."
        )
        wse, extrav = _critica_segura()
        return wse, warns, extrav


# ---------------------------------------------------------------------------
# Solver principal
# ---------------------------------------------------------------------------

def perfil_linha_dagua(
    secoes: Sequence[SecaoRio],
    Q: float,
    n: float,
    *,
    cc_jusante: Literal["normal", "critica", "cota"] = "normal",
    cota_jusante_m: float | None = None,
    coef_contracao: float = COEF_CONTRACAO,
    coef_expansao: float = COEF_EXPANSAO,
) -> PerfilLinhaAgua:
    """
    Resolve o perfil de linha d'agua por standard-step subcritico.

    Parameters
    ----------
    secoes : secoes ao longo do rio, ordenadas de JUSANTE (indice 0) para
             MONTANTE. Precisam de `estaca_rio_m` crescente nessa ordem.
    Q : vazao de projeto (m3/s) — tipicamente o pico do hidrograma SCS.
    n : coeficiente de Manning do trecho.
    cc_jusante : condicao de contorno de jusante.
        "normal"  -> profundidade normal (Manning) com S do leito local.
        "critica" -> profundidade critica.
        "cota"    -> cota d'agua absoluta fixa (exige cota_jusante_m).
    cota_jusante_m : cota d'agua imposta quando cc_jusante='cota'.

    Returns
    -------
    PerfilLinhaAgua com um PontoPerfil por secao (de jusante p/ montante) e
    eventuais warnings (controle critico, extravasamento, falha da CC).
    """
    if Q <= 0:
        raise ValueError(f"Q precisa ser > 0, recebi {Q}.")
    if n <= 0:
        raise ValueError(f"n de Manning precisa ser > 0, recebi {n}.")
    if len(secoes) < 2:
        raise ValueError(f"Precisa de >= 2 secoes; recebi {len(secoes)}.")

    secoes = sorted(secoes, key=lambda s: s.estaca_rio_m)  # jusante -> montante

    warns: list[str] = []

    # Coerencia: com estaca crescente = montante, o thalweg deve TENDER a subir.
    # Thalweg de montante mais baixo que o de jusante sugere convencao de estaca
    # invertida (o perfil sairia ao contrario) — avisa em vez de inverter calado.
    if secoes[-1].secao.z_thalweg < secoes[0].secao.z_thalweg - 1e-6:
        warns.append(
            "Thalweg da maior estaca esta mais baixo que o da menor: confira a "
            "convencao de estaca (deve crescer de jusante p/ montante) — o perfil "
            "pode estar invertido."
        )

    wse0, w_cc, extrav0 = _wse_jusante(secoes, Q, n, cc_jusante, cota_jusante_m)
    warns.extend(w_cc)

    est0 = estado_hidraulico(secoes[0].secao, Q, n, wse0)
    if est0 is None:
        raise ValueError(
            "Secao de jusante seca na cota de contorno — verifique Q e a geometria."
        )

    def _ponto(sec_rio: SecaoRio, est: EstadoHidraulico,
               controle: bool, extrav: bool) -> PontoPerfil:
        # Em extravasamento a cinematica in-bank (V, Fr) e nao-fisica — a agua
        # espraia na planicie. Expoe indefinido em vez de V/Fr absurdos.
        return PontoPerfil(
            estaca_rio_m=sec_rio.estaca_rio_m,
            z_thalweg_m=sec_rio.secao.z_thalweg,
            wse_m=est.wse_m, y_lamina_m=est.y_lamina_m, A_m2=est.A_m2,
            R_m=est.R_m,
            V_m_s=float("nan") if extrav else est.V_m_s,
            Fr=float("nan") if extrav else est.Fr,
            Sf_m_per_m=est.Sf_m_per_m,
            regime="extravasado" if extrav else est.regime,
            controle_critico=controle, extravasou=extrav,
        )

    def _propagar(est_mon: EstadoHidraulico, est_jus_ant: EstadoHidraulico,
                  extrav: bool) -> EstadoHidraulico:
        # Estado a propagar p/ o proximo passo. Numa secao extravasada, a
        # hipotese 1D in-bank quebra: propagar a carga cinetica fantasma (Q
        # forcado pela area in-bank, V/Fr absurdos) inflaria H e cascatearia
        # extravasamento espurio p/ montante. Propaga so energia potencial (WSE)
        # e reusa o Sf fisico do passo anterior em vez do Sf inflado desta secao.
        if not extrav:
            return est_mon
        return replace(
            est_mon, carga_cinetica_m=0.0, H_m=est_mon.wse_m,
            Sf_m_per_m=est_jus_ant.Sf_m_per_m,
            V_m_s=float("nan"), Fr=float("nan"), regime="extravasado",
        )

    if extrav0:
        warns.append(
            f"Secao de jusante extravasada (Q={Q:.1f} m3/s acima da capacidade "
            "ate a margem) — resultados a montante sao aproximados."
        )
    pontos: list[PontoPerfil] = [_ponto(secoes[0], est0, False, extrav0)]
    est_jus = _propagar(est0, est0, extrav0)
    extrav_sinalizado = extrav0

    for i in range(1, len(secoes)):
        sec_mon = secoes[i].secao
        dx = abs(secoes[i].estaca_rio_m - secoes[i - 1].estaca_rio_m)
        if dx <= 0:
            raise ValueError(
                f"Secoes {i-1} e {i} com mesma estaca de rio (dx=0)."
            )
        wse_m, controle, extrav = _resolver_wse_montante(
            sec_mon, Q, n, dx, est_jus,
            coef_contracao=coef_contracao, coef_expansao=coef_expansao,
        )
        est_mon = estado_hidraulico(sec_mon, Q, n, wse_m)
        if est_mon is None:
            # Fallback defensivo: trava na critica (protegida) ou na margem.
            try:
                wse_m = lamina_critica(sec_mon, Q).y_w_m
            except ValueError:
                wse_m = sec_mon.z_max - 1e-4
                extrav = True
            est_mon = estado_hidraulico(sec_mon, Q, n, wse_m)
            controle = True
        rotulo = secoes[i].rotulo or f"secao {i}"
        if controle:
            warns.append(
                f"Controle critico na {rotulo} (estaca {secoes[i].estaca_rio_m:.0f} m): "
                "regime passa por critico — fora da hipotese subcritica pura."
            )
        if extrav and not extrav_sinalizado:
            warns.append(
                f"Extravasamento a partir da {rotulo} (estaca "
                f"{secoes[i].estaca_rio_m:.0f} m): Q={Q:.1f} m3/s nao cabe ate a "
                f"margem (z_max={sec_mon.z_max:.2f} m). Resultados a montante sao "
                "aproximados (escoamento sai do regime 1D in-bank)."
            )
            extrav_sinalizado = True
        pontos.append(_ponto(secoes[i], est_mon, controle, extrav))
        est_jus = _propagar(est_mon, est_jus, extrav)

    return PerfilLinhaAgua(
        pontos=pontos, Q_m3_s=Q, n_manning=n, cc_jusante=cc_jusante,
        warnings=warns,
    )


# ---------------------------------------------------------------------------
# I/O do MDT por janela (AOI) — COG local ou remoto via /vsicurl
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class JanelaMDT:
    """Recorte (AOI) de um MDT lido por janela."""
    array: "object"          # np.ndarray float64, nodata -> np.nan
    transform: "object"      # affine.Affine do recorte
    crs: str                 # CRS do raster (string)
    nodata: float | None
    res_m: float             # tamanho medio do pixel (m), se CRS metrico


def ler_mdt_janela(
    fonte: str | Path,
    bounds: tuple[float, float, float, float],
    *,
    bounds_crs: str | None = None,
    overview_level: int | None = None,
) -> JanelaMDT:
    """
    Le um recorte (AOI) de um MDT por janela — NUNCA carrega o raster inteiro.

    Aceita `fonte` local (path) ou remota COG via GDAL VSI
    (ex: "/vsicurl/https://bucket/mdt.tif" ou "/vsis3/...", "/vsigs/..."). Como
    os MDS do projeto sao tiled + overviews, a leitura por janela puxa so os
    tiles da AOI.

    Parameters
    ----------
    fonte : path local ou string VSI do COG.
    bounds : (xmin, ymin, xmax, ymax) da AOI.
    bounds_crs : CRS dos bounds. Se diferente do CRS do raster, e reprojetado.
                 Default: assume mesmo CRS do raster.
    overview_level : se dado, le a pirâmide nesse nivel (0 = nativo). Util pra
                     previa rapida em AOI grande. Default: resolucao nativa.

    Returns
    -------
    JanelaMDT com array float64 (nodata convertido p/ np.nan).
    """
    import numpy as np  # noqa: PLC0415
    import rasterio  # noqa: PLC0415
    from rasterio.warp import transform_bounds  # noqa: PLC0415
    from rasterio.windows import from_bounds as window_from_bounds  # noqa: PLC0415

    with rasterio.open(str(fonte)) as src:
        b = bounds
        if bounds_crs is not None and src.crs is not None and \
                str(src.crs) != str(bounds_crs):
            b = transform_bounds(bounds_crs, src.crs, *bounds)

        window = window_from_bounds(*b, transform=src.transform)
        # Clip da janela aos limites do raster (evita ler fora -> erro).
        window = window.intersection(
            rasterio.windows.Window(0, 0, src.width, src.height)
        )
        # Arredonda offsets/dimensoes p/ inteiros ANTES de ler E de calcular o
        # transform: src.read reamostra os dados p/ um shape inteiro, e usar a
        # janela fracionaria no window_transform desalinha array x transform em
        # ate ~1 px (misregistro sistematico que a mancha inteira herdaria).
        window = window.round_offsets().round_lengths()
        if window.width < 1 or window.height < 1:
            raise ValueError(
                "AOI nao intersecta o MDT. Confira os bounds e o CRS."
            )

        base_transform = src.window_transform(window)
        if overview_level is not None and overview_level > 0:
            # Decima na leitura via out_shape — funciona on-the-fly mesmo SEM
            # piramides internas (GDAL reamostra lendo os tiles). Protege a RAM
            # em AOI grande, independente de o GeoTIFF ter overviews.
            out_h = max(1, int(window.height) >> overview_level)
            out_w = max(1, int(window.width) >> overview_level)
            arr = src.read(
                1, window=window, out_shape=(1, out_h, out_w),
                resampling=rasterio.enums.Resampling.bilinear,
            )
            transform = base_transform * base_transform.scale(
                window.width / arr.shape[-1], window.height / arr.shape[-2],
            )
        else:
            arr = src.read(1, window=window)
            transform = base_transform

        nodata = src.nodata
        crs = src.crs.to_string() if src.crs else ""
        res_m = (abs(transform.a) + abs(transform.e)) / 2.0

    arr = arr.astype(np.float64)
    if nodata is not None:
        arr = np.where(arr == nodata, np.nan, arr)

    return JanelaMDT(
        array=arr, transform=transform, crs=crs, nodata=nodata, res_m=res_m,
    )


# ---------------------------------------------------------------------------
# Geracao de secoes transversais a partir do eixo do rio
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SecoesGeradas:
    """Resultado da geracao de transversais a partir de um eixo desenhado."""
    secoes: list[SecaoRio]           # ja em ordem jusante -> montante
    crs_trabalho: str                # UTM metrico usado p/ as perpendiculares
    comprimento_eixo_m: float
    sentido_invertido: bool          # True se o eixo foi desenhado montante->jusante
    avisos: list[str] = field(default_factory=list)


def _utm_epsg_lonlat(lat: float, lon: float) -> str:
    """EPSG do UTM para o ponto (sul: 327xx; norte: 326xx)."""
    zone = int((lon + 180) / 6) + 1
    return f"EPSG:{32700 + zone if lat < 0 else 32600 + zone}"


def gerar_secoes_do_eixo(
    eixo_lonlat: Sequence[tuple[float, float]],
    mdt_fonte: str | Path,
    *,
    espacamento_m: float = 50.0,
    largura_m: float = 200.0,
    n_amostras: int = 40,
    max_secoes: int = 200,
) -> SecoesGeradas:
    """
    Gera secoes transversais perpendiculares ao eixo do rio, amostradas do MDT.

    Para cada estacao equiespacada ao longo do eixo (lon/lat), constroi uma
    transversal perpendicular de comprimento `largura_m` centrada no eixo e
    amostra o perfil do MDT (reusa secao_mdt.amostrar_perfil — leitura por
    janela; serve MDT local ou COG /vsicurl).

    O sentido jusante->montante e auto-detectado pelos thalwegs (a ponta de
    thalweg mais baixo vira jusante, estaca 0), entao tanto faz desenhar o eixo
    de jusante p/ montante quanto o contrario.
    """
    import math as _math  # noqa: PLC0415

    from rasterio.warp import transform as warp_transform  # noqa: PLC0415
    from shapely.geometry import LineString  # noqa: PLC0415

    from chuva_vazao import secao_mdt  # noqa: PLC0415
    from chuva_vazao.secao_natural import secao_from_pontos  # noqa: PLC0415

    coords = [(float(lon), float(lat)) for lon, lat in eixo_lonlat]
    if len(coords) < 2:
        raise ValueError(f"Eixo precisa de >= 2 vertices; recebi {len(coords)}.")

    lons = [c[0] for c in coords]
    lats = [c[1] for c in coords]
    crs_work = _utm_epsg_lonlat(sum(lats) / len(lats), sum(lons) / len(lons))

    xs, ys = warp_transform("EPSG:4326", crs_work, lons, lats)
    eixo = LineString(list(zip(xs, ys)))
    L = float(eixo.length)
    if L <= 0:
        raise ValueError("Eixo degenerado (comprimento zero).")

    avisos: list[str] = []
    n = max(2, int(L // espacamento_m) + 1)
    if n > max_secoes:
        avisos.append(
            f"Eixo de {L:.0f} m a cada {espacamento_m:.0f} m daria {n} secoes; "
            f"resolucao reduzida p/ caber em {max_secoes} (espacamento efetivo "
            f"~{L / (max_secoes - 1):.0f} m). O eixo continua coberto inteiro."
        )
        n = max_secoes

    estacas = [L * k / (n - 1) for k in range(n)]
    ds = max(0.5, espacamento_m * 0.25)  # base p/ estimar a tangente local

    secoes_rio: list[SecaoRio] = []
    n_falhas = 0
    for s in estacas:
        p = eixo.interpolate(s)
        p0 = eixo.interpolate(max(0.0, s - ds))
        p1 = eixo.interpolate(min(L, s + ds))
        dx = p1.x - p0.x
        dy = p1.y - p0.y
        norm = _math.hypot(dx, dy)
        if norm == 0:
            continue
        # Perpendicular unitaria (tangente rotacionada 90 graus).
        ux, uy = -dy / norm, dx / norm
        h = largura_m / 2.0
        a = (p.x - h * ux, p.y - h * uy)
        b = (p.x + h * ux, p.y + h * uy)
        ax_ll, ay_ll = warp_transform(
            crs_work, "EPSG:4326", [a[0], b[0]], [a[1], b[1]],
        )
        linha_ll = [(ax_ll[0], ay_ll[0]), (ax_ll[1], ay_ll[1])]
        try:
            # Fixa o MESMO UTM (do centro do eixo) p/ TODAS as transversais.
            # Sem crs_trabalho, amostrar_perfil auto-escolhe o UTM por transversal
            # e, perto de uma borda de fuso, secoes adjacentes sairiam em zonas
            # diferentes — bbox/KDTree/projecao da mancha virariam incoerentes.
            perfil = secao_mdt.amostrar_perfil(
                linha_ll, mdt_fonte, n_pontos=n_amostras, crs_trabalho=crs_work,
            )
            sec = secao_from_pontos(perfil.pontos)
        except ValueError:
            n_falhas += 1
            continue
        secoes_rio.append(SecaoRio(secao=sec, estaca_rio_m=s, rotulo=f"E{s:.0f}m"))

    if n_falhas:
        avisos.append(
            f"{n_falhas} transversal(is) cairam fora do MDT e foram puladas."
        )
    if len(secoes_rio) < 2:
        raise ValueError(
            "Menos de 2 secoes validas — o eixo cai fora do MDT ou a largura "
            "extrapola a cobertura. Confira a area do raster."
        )

    # Auto-deteccao de sentido: jusante = extremidade de thalweg mais baixo.
    # Usa a MEDIANA dos K thalwegs de cada ponta (nao o min), p/ nao depender de
    # uma unica amostra espuria (pit/artefato de interpolacao, comuns em MDT).
    import statistics as _stats  # noqa: PLC0415

    k = min(3, len(secoes_rio) // 2) or 1
    ths = [sr.secao.z_thalweg for sr in secoes_rio]
    z_ini = _stats.median(ths[:k])
    z_fim = _stats.median(ths[-k:])
    invertido = z_ini > z_fim
    if invertido:
        L_eff = secoes_rio[-1].estaca_rio_m
        secoes_rio = [
            replace(sr, estaca_rio_m=L_eff - sr.estaca_rio_m) for sr in secoes_rio
        ]
        secoes_rio.sort(key=lambda sr: sr.estaca_rio_m)

    return SecoesGeradas(
        secoes=secoes_rio, crs_trabalho=crs_work, comprimento_eixo_m=L,
        sentido_invertido=invertido, avisos=avisos,
    )


# ---------------------------------------------------------------------------
# Projecao da linha d'agua no MDT -> mancha de inundacao
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ManchaInundacao:
    """Mancha de inundacao projetada (profundidade = WSE - terreno)."""
    mancha_geojson: str          # FeatureCollection (EPSG:4326) do poligono alagado
    profundidade: "object"       # np.ndarray do recorte: profundidade (m), nan fora da mancha
    mdt_z: "object"              # np.ndarray do recorte: cota do terreno (nan = nodata)
    transform: "object"          # affine.Affine do recorte (CRS do MDT)
    crs: str                     # CRS do MDT
    bounds_lonlat: tuple         # (w, s, e, n) em EPSG:4326 (p/ ImageOverlay)
    area_alagada_m2: float
    prof_max_m: float
    prof_media_m: float
    res_m: float                 # resolucao do recorte usado (pode ter overview)
    avisos: list[str] = field(default_factory=list)


def _reproject_geom(geom: dict, src_crs: str, dst_crs: str) -> dict:
    """Reprojeta um GeoJSON Polygon/MultiPolygon entre CRSs (anel a anel)."""
    from rasterio.warp import transform as warp_transform  # noqa: PLC0415

    def _ring(ring):
        xs = [c[0] for c in ring]
        ys = [c[1] for c in ring]
        nx, ny = warp_transform(src_crs, dst_crs, xs, ys)
        return list(zip(nx, ny))

    t = geom["type"]
    if t == "Polygon":
        return {"type": "Polygon", "coordinates": [_ring(r) for r in geom["coordinates"]]}
    if t == "MultiPolygon":
        return {
            "type": "MultiPolygon",
            "coordinates": [[_ring(r) for r in poly] for poly in geom["coordinates"]],
        }
    return geom


def _mascara_corredor(secoes_ord, crs_secoes, mdt_crs, transform, shape):
    """
    Rasteriza o corredor poligonal entre transversais consecutivas (cut-lines).

    Cada par de secoes vizinhas forma um quadrilatero [esq_i, dir_i, dir_{i+1},
    esq_{i+1}] (margens das transversais). A uniao dos quadrilateros e o dominio
    valido da mancha — evita os "caps" semicirculares alem das secoes extremas e
    o vazamento lateral por raio em meandros. Retorna mascara booleana, ou None
    se a geometria falhar (caller cai no raio do thalweg).
    """
    try:
        import numpy as np  # noqa: PLC0415
        from rasterio.features import rasterize  # noqa: PLC0415
        from rasterio.warp import transform as warp_transform  # noqa: PLC0415
        from shapely.geometry import Polygon  # noqa: PLC0415
        from shapely.ops import unary_union  # noqa: PLC0415

        ex = [s.secao.pontos_3d[0].E for s in secoes_ord]
        ey = [s.secao.pontos_3d[0].N for s in secoes_ord]
        dx = [s.secao.pontos_3d[-1].E for s in secoes_ord]
        dy = [s.secao.pontos_3d[-1].N for s in secoes_ord]
        if str(mdt_crs) != str(crs_secoes):
            ex, ey = warp_transform(crs_secoes, mdt_crs, ex, ey)
            dx, dy = warp_transform(crs_secoes, mdt_crs, dx, dy)
        esq = list(zip(ex, ey))
        dirr = list(zip(dx, dy))

        quads = []
        for i in range(len(secoes_ord) - 1):
            # buffer(0) limpa auto-interseccao (bowtie) em curvas fechadas
            poly = Polygon([esq[i], dirr[i], dirr[i + 1], esq[i + 1]]).buffer(0)
            if (not poly.is_empty) and poly.area > 0:
                quads.append(poly)
        if not quads:
            return None
        corredor = unary_union(quads)
        if corredor.is_empty:
            return None
        mask = rasterize(
            [(corredor, 1)], out_shape=shape, transform=transform,
            fill=0, dtype="uint8",
        )
        return mask.astype(bool)
    except Exception:  # noqa: BLE001
        return None


def projetar_mancha(
    secoes: Sequence[SecaoRio],
    perfil: PerfilLinhaAgua,
    mdt_fonte: str | Path,
    *,
    crs_secoes: str,
    margem_m: float = 20.0,
    max_dist_corredor_m: float | None = None,
    n_densifica: int = 800,
    teto_pixels: int = 8_000_000,
) -> ManchaInundacao:
    """
    Projeta a linha d'agua (WSE por secao) no MDT e gera a mancha de inundacao.

    Estrategia:
        1. Recorta a AOI do MDT = bbox do corredor das secoes + margem (janela).
        2. Densifica a linha de thalweg (com WSE interpolada) e monta uma KDTree.
        3. Para cada pixel do recorte, pega a WSE do ponto de thalweg mais proximo
           (e a distancia ao eixo). profundidade = WSE - z_terreno.
        4. Submerso = profundidade > 0 dentro do corredor (dist <= max_dist).
        5. Connected-component a partir do thalweg — mantem so o alagamento ligado
           ao canal (descarta depressoes isoladas com mesma cota).
        6. Poligonaliza a mascara -> GeoJSON 4326; devolve tambem o raster de
           profundidade p/ overlay.

    Para AOI grande a 0,5 m, le num nivel de overview p/ manter <= `teto_pixels`
    (protege a RAM no Streamlit Cloud).
    """
    import numpy as np  # noqa: PLC0415
    import rasterio  # noqa: PLC0415
    from rasterio.features import shapes  # noqa: PLC0415
    from rasterio.warp import transform as warp_transform  # noqa: PLC0415
    from rasterio.warp import transform_bounds  # noqa: PLC0415
    from scipy.ndimage import label  # noqa: PLC0415
    from scipy.spatial import cKDTree  # noqa: PLC0415

    # Casa secao <-> WSE por INDICE: ambas as listas, ordenadas por estaca, vem
    # do mesmo conjunto de secoes (o perfil foi resolvido sobre elas). Evita a
    # fragilidade de chave float. Fallback por estaca se os tamanhos divergirem.
    secoes_ord = sorted(secoes, key=lambda s: s.estaca_rio_m)
    pontos_ord = sorted(perfil.pontos, key=lambda p: p.estaca_rio_m)
    if len(secoes_ord) == len(pontos_ord):
        pares = list(zip(secoes_ord, [p.wse_m for p in pontos_ord]))
    else:
        wse_por_estaca = {round(p.estaca_rio_m, 3): p.wse_m for p in pontos_ord}
        pares = [(sr, wse_por_estaca.get(round(sr.estaca_rio_m, 3))) for sr in secoes_ord]

    xs_th: list[float] = []
    ys_th: list[float] = []
    wse_th: list[float] = []
    for sr, w in pares:
        if w is None:
            continue
        xs_th.append(sr.secao.E_thalweg)
        ys_th.append(sr.secao.N_thalweg)
        wse_th.append(w)
    if len(xs_th) < 2:
        raise ValueError("Nao consegui casar os thalwegs das secoes com a WSE do perfil.")

    # bbox do corredor (todos os pontos das transversais) em crs_secoes
    all_e = [p.E for sr in secoes_ord for p in sr.secao.pontos_3d]
    all_n = [p.N for sr in secoes_ord for p in sr.secao.pontos_3d]
    xmin, xmax = min(all_e) - margem_m, max(all_e) + margem_m
    ymin, ymax = min(all_n) - margem_m, max(all_n) + margem_m

    avisos: list[str] = []

    # Estima o tamanho do recorte na resolucao nativa e escolhe overview se preciso
    with rasterio.open(str(mdt_fonte)) as _src:
        res_nat = (abs(_src.transform.a) + abs(_src.transform.e)) / 2.0
    res_nat = max(res_nat, 1e-6)
    npix_est = ((xmax - xmin) / res_nat) * ((ymax - ymin) / res_nat)
    overview = None
    if npix_est > teto_pixels:
        overview = int(math.ceil(math.log2((npix_est / teto_pixels) ** 0.5)))
        avisos.append(
            f"AOI grande (~{npix_est/1e6:.0f} Mpx a {res_nat:.2f} m): lida em "
            f"overview nivel {overview} p/ caber na RAM. Reduza a largura/"
            "comprimento p/ resolucao total."
        )

    jan = ler_mdt_janela(
        mdt_fonte, (xmin, ymin, xmax, ymax),
        bounds_crs=crs_secoes, overview_level=overview,
    )
    z = jan.array
    mdt_crs = jan.crs
    transform = jan.transform
    nrow, ncol = z.shape

    # thalwegs: crs_secoes -> CRS do MDT
    if str(mdt_crs) != str(crs_secoes):
        tx_l, ty_l = warp_transform(crs_secoes, mdt_crs, xs_th, ys_th)
        tx = np.asarray(tx_l)
        ty = np.asarray(ty_l)
    else:
        tx = np.asarray(xs_th)
        ty = np.asarray(ys_th)
    wse_arr = np.asarray(wse_th)

    # densifica a linha de thalweg (com WSE) no CRS do MDT
    seg = np.hypot(np.diff(tx), np.diff(ty))
    d_acum = np.concatenate([[0.0], np.cumsum(seg)])
    Ld = float(d_acum[-1])
    if Ld <= 0:
        raise ValueError("Linha de thalweg degenerada (comprimento zero).")
    dd = np.linspace(0.0, Ld, n_densifica)
    dx_dense = np.interp(dd, d_acum, tx)
    dy_dense = np.interp(dd, d_acum, ty)
    wse_dense = np.interp(dd, d_acum, wse_arr)

    tree = cKDTree(np.column_stack([dx_dense, dy_dense]))

    # coords UTM do centro de cada pixel
    cols, rows = np.meshgrid(np.arange(ncol), np.arange(nrow))
    xpix = transform.c + transform.a * (cols + 0.5) + transform.b * (rows + 0.5)
    ypix = transform.f + transform.d * (cols + 0.5) + transform.e * (rows + 0.5)
    dist, idx = tree.query(np.column_stack([xpix.ravel(), ypix.ravel()]), k=1)
    wse_pix = wse_dense[idx].reshape(z.shape)
    dist = dist.reshape(z.shape)

    prof = wse_pix - z
    # Dominio da mancha = corredor poligonal entre as transversais (cut-lines):
    # evita os "caps" alem das secoes extremas e o vazamento por raio em meandros.
    # Fallback pro raio do thalweg se a geometria do corredor falhar.
    corredor = _mascara_corredor(secoes_ord, crs_secoes, mdt_crs, transform, z.shape)
    if corredor is not None and corredor.any():
        submerso = np.isfinite(z) & (prof > 0.0) & corredor
    else:
        if max_dist_corredor_m is None:
            max_dist_corredor_m = max(sr.secao.largura_topo for sr in secoes_ord) / 2.0
        submerso = np.isfinite(z) & (prof > 0.0) & (dist <= max_dist_corredor_m)
        avisos.append(
            "Corredor poligonal indisponivel — usei o raio do thalweg "
            "(pode gerar 'caps' nas pontas)."
        )

    # connected-component 4-conexo; mantem componentes que tocam algum thalweg.
    # Semeia na linha de thalweg DENSIFICADA (mais densa que os thalwegs de
    # secao) p/ reduzir a chance de perder todas as ancoras por desalinhamento.
    lbl, _ = label(submerso, structure=np.array([[0, 1, 0], [1, 1, 1], [0, 1, 0]]))
    inv = ~transform
    seed_labels: set[int] = set()
    for xe, ye in zip(dx_dense, dy_dense):
        c = int(inv.a * xe + inv.b * ye + inv.c)
        r = int(inv.d * xe + inv.e * ye + inv.f)
        if 0 <= r < nrow and 0 <= c < ncol and lbl[r, c] > 0:
            seed_labels.add(int(lbl[r, c]))
    if seed_labels:
        mancha = np.isin(lbl, list(seed_labels))
    elif submerso.any():
        mancha = submerso
        avisos.append(
            "Nenhum pixel de thalweg ficou submerso — mancha sem ancora no canal "
            "(usando toda a area submersa do corredor)."
        )
    else:
        mancha = submerso  # tudo False
        avisos.append("Nenhuma celula submersa no corredor — mancha vazia (Q baixo?).")

    prof_masked = np.where(mancha, prof, np.nan)
    area_pixel = abs(transform.a * transform.e - transform.b * transform.d)
    area_alagada = float(mancha.sum() * area_pixel)

    # poligonaliza a mascara -> 4326
    mask_u8 = mancha.astype(np.uint8)
    feats = []
    for geom, val in shapes(mask_u8, mask=mask_u8 == 1, transform=transform):
        feats.append({
            "type": "Feature",
            "geometry": _reproject_geom(geom, mdt_crs, "EPSG:4326"),
            "properties": {"v": int(val)},
        })
    mancha_geojson = json.dumps({"type": "FeatureCollection", "features": feats})

    # bounds do recorte em lonlat (p/ ImageOverlay)
    left = transform.c
    top = transform.f
    right = transform.c + transform.a * ncol
    bottom = transform.f + transform.e * nrow
    w, s, e, nb = transform_bounds(mdt_crs, "EPSG:4326", left, bottom, right, top)

    prof_valid = prof_masked[np.isfinite(prof_masked)]
    return ManchaInundacao(
        mancha_geojson=mancha_geojson, profundidade=prof_masked, mdt_z=z,
        transform=transform, crs=str(mdt_crs), bounds_lonlat=(w, s, e, nb),
        area_alagada_m2=area_alagada,
        prof_max_m=float(prof_valid.max()) if prof_valid.size else 0.0,
        prof_media_m=float(prof_valid.mean()) if prof_valid.size else 0.0,
        res_m=jan.res_m, avisos=avisos,
    )

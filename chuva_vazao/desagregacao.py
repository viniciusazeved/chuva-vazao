"""
Desagregacao de chuva diaria para duracoes curtas.

Dois modos:
- Pfafstetter regional: usa os betas do posto (do HidroFlu) para faixas de duracao
  ~5min, ~15min, ~30min, 1h-6dias.
- Fallback DNAEE: tabela nacional de coeficientes de desagregacao, quando nao
  ha betas regionais para o posto.

Fonte dos coeficientes DNAEE: adaptado de D:/Projetos/IDF/disaggregation.py.
Fator 1.14 (dia fixo -> dia movel): Weiss (1964).
"""
from __future__ import annotations

from typing import Literal

from chuva_vazao.db import PfafstetterCoef


# ---------------------------------------------------------------------------
# Tabela DNAEE (fallback)
# ---------------------------------------------------------------------------

# (duracao_origem_min, duracao_destino_min) -> coeficiente
DNAEE_COEFFICIENTS: dict[tuple[int, int], float] = {
    # Dia fixo -> dia movel (Weiss 1964)
    (1440, 1440): 1.14,
    # 24h (dia movel) -> duracoes intermediarias
    (1440, 720): 0.85,
    (1440, 600): 0.82,
    (1440, 480): 0.78,
    (1440, 360): 0.72,
    (1440, 60): 0.51,
    # 1h -> 2h e 30min
    (60, 120): 1.27,
    (60, 30): 0.74,
    # 30min -> subdivisoes
    (30, 25): 0.91,
    (30, 20): 0.81,
    (30, 15): 0.70,
    (30, 10): 0.54,
    (30, 5): 0.34,
}

DURATIONS_MIN: list[int] = [5, 10, 15, 20, 25, 30, 60, 120, 360, 480, 600, 720, 1440]


def desagregar_dnaee(precipitacao_diaria_mm: float) -> dict[int, float]:
    """
    Desagrega P_1dia em alturas (mm) para todas as duracoes da tabela DNAEE.

    Cadeia:
        P_1dia -*1.14-> P_1440
        P_1440 -> 720, 600, 480, 360, 60 (coefs diretos)
        P_60 -> 120, 30
        P_30 -> 25, 20, 15, 10, 5

    Returns
    -------
    dict[int, float]
        {duracao_minutos: altura_mm}
    """
    depths: dict[int, float] = {}
    p_1440 = precipitacao_diaria_mm * DNAEE_COEFFICIENTS[(1440, 1440)]
    depths[1440] = p_1440

    for (src, dst), coef in DNAEE_COEFFICIENTS.items():
        if src == 1440 and dst != 1440:
            depths[dst] = p_1440 * coef

    p_60 = depths[60]
    depths[120] = p_60 * DNAEE_COEFFICIENTS[(60, 120)]
    depths[30] = p_60 * DNAEE_COEFFICIENTS[(60, 30)]

    p_30 = depths[30]
    for (src, dst), coef in DNAEE_COEFFICIENTS.items():
        if src == 30:
            depths[dst] = p_30 * coef

    return depths


# ---------------------------------------------------------------------------
# Betas regionais (Pfafstetter)
# ---------------------------------------------------------------------------

BetaBand = Literal["5min", "15min", "30min", "1h_6dias"]


def _beta_band_for_duration(duracao_min: float) -> BetaBand:
    """Mapeia duracao para a faixa de beta regional mais proxima."""
    if duracao_min <= 10:
        return "5min"
    if duracao_min <= 22:
        return "15min"
    if duracao_min <= 45:
        return "30min"
    return "1h_6dias"


def desagregar_pfafstetter(
    precipitacao_diaria_mm: float,
    coef: PfafstetterCoef,
    duracoes_min: list[int] | None = None,
) -> dict[int, float]:
    """
    Desagrega P_1dia aplicando os betas regionais do posto.

    A interpretacao adotada: cada beta representa a razao entre a altura de chuva
    da faixa de duracao correspondente e a altura da chuva de 24h. Faixas de
    duracao maiores (1h_6dias) recebem o beta regional; valores intermediarios
    fazem interpolacao linear em escala log(t).

    Se a duracao esta fora da cobertura (>1440min), usa fator DNAEE como aproximacao.

    Parameters
    ----------
    precipitacao_diaria_mm : float
        Precipitacao maxima diaria (mm).
    coef : PfafstetterCoef
        Coeficientes do posto incluindo os 4 betas regionais.
    duracoes_min : list[int], opcional
        Duracoes-alvo. Default: DURATIONS_MIN.

    Returns
    -------
    dict[int, float]
        {duracao_min: altura_mm}
    """
    if duracoes_min is None:
        duracoes_min = DURATIONS_MIN

    # Correcao dia fixo -> dia movel (Weiss 1964)
    p_24h = precipitacao_diaria_mm * 1.14

    depths: dict[int, float] = {}
    for t in duracoes_min:
        if t >= 1440:
            depths[t] = p_24h
            continue
        band = _beta_band_for_duration(t)
        beta = getattr(coef, f"beta{band}" if band != "1h_6dias" else "beta1h_6dias")
        depths[t] = p_24h * beta

    return depths


# ---------------------------------------------------------------------------
# Interface unificada
# ---------------------------------------------------------------------------

def desagregar(
    precipitacao_diaria_mm: float,
    coef_pfafstetter: PfafstetterCoef | None = None,
    duracoes_min: list[int] | None = None,
) -> tuple[dict[int, float], str]:
    """
    Desagrega usando betas regionais quando disponiveis, fallback DNAEE caso contrario.

    Returns
    -------
    (depths, metodo_usado)
        depths: {duracao_min: altura_mm}
        metodo_usado: "pfafstetter" ou "dnaee"
    """
    if coef_pfafstetter is not None:
        return desagregar_pfafstetter(precipitacao_diaria_mm, coef_pfafstetter, duracoes_min), "pfafstetter"
    return desagregar_dnaee(precipitacao_diaria_mm), "dnaee"


def altura_para_intensidade(depths: dict[int, float]) -> dict[int, float]:
    """Converte {duracao_min: h_mm} em {duracao_min: i_mm_por_h}."""
    return {dur: (h / dur) * 60.0 for dur, h in depths.items()}

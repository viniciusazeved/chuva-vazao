"""
Desagregacao de chuva diaria para duracoes curtas pela tabela DNAEE.

Tabela nacional de coeficientes de desagregacao (CETESB 1980, Tucci 2009),
encadeada via fator 1.14 (dia fixo -> dia movel, Weiss 1964) e dos
coeficientes intermediarios DAEE.
"""
from __future__ import annotations


# ---------------------------------------------------------------------------
# Tabela DNAEE
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


def altura_para_intensidade(depths: dict[int, float]) -> dict[int, float]:
    """Converte {duracao_min: h_mm} em {duracao_min: i_mm_por_h}."""
    return {dur: (h / dur) * 60.0 for dur, h in depths.items()}
